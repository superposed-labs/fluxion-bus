"""The Provider Gateway ASGI application.

Deliberately a separate FastAPI app from `fluxion-web`, not a router mounted on
it. Three reasons that all bite in production: the auth semantics differ (this
token reaches every upstream credential), the SPA fallback would swallow
`/v1/...` paths, and Responses SSE is a long-lived stream that web middleware
tends to buffer.

Request path:
    auth -> normalize -> identity -> sticky lookup -> requirements
         -> route -> local agent -> stream -> sticky remember
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from fluxion.provider_gateway.attribution import (
    BILLING_API,
    BILLING_SUBSCRIPTION,
    AttributionStore,
)
from fluxion.provider_gateway.auth import (
    AuthError,
    InsecureBindError,
    TokenAuthenticator,
    check_bind,
    load_or_create_token,
)
from fluxion.provider_gateway.capabilities import derive_requirements
from fluxion.provider_gateway.codex_config import is_read_only_role
from fluxion.provider_gateway.config import (
    PROTOCOL_LOCAL_AGENT,
    ConfigError,
    GatewaySettings,
    RoutingConfig,
)
from fluxion.provider_gateway.identity import RequestIdentity
from fluxion.provider_gateway.ingress.responses import (
    CodexResponsesIngress,
    is_compaction_request,
)
from fluxion.provider_gateway.request import RawRequest
from fluxion.provider_gateway.routing import NoRouteAvailableError, RouteDecision, Router
from fluxion.provider_gateway.sticky import StickyStore
from fluxion.provider_gateway.stream import (
    EV_COMPLETED,
    encode_sse,
    is_terminal_event,
)
from fluxion.provider_gateway.upstream.local_agent import LocalAgentUpstream

log = logging.getLogger(__name__)


@dataclass
class GatewayContext:
    """Everything one gateway instance needs, wired at startup."""

    router: Router
    sticky: StickyStore
    authenticator: TokenAuthenticator
    local_agents: Mapping[str, LocalAgentUpstream] = field(default_factory=dict)
    workspaces: Mapping[str, Path] = field(default_factory=dict)
    attribution: AttributionStore | None = None
    ingress: CodexResponsesIngress = field(default_factory=CodexResponsesIngress)
    # Where FLUXION_PROVIDER_LOG_BODIES writes captured requests. Off unless set:
    # a request body carries the full delegated task and the parent's context.
    body_log_dir: Path | None = None

    def local_agent_for(self, decision: RouteDecision) -> LocalAgentUpstream | None:
        return self.local_agents.get(decision.provider_id)

    def workspace_for(self, decision: RouteDecision, identity: RequestIdentity) -> Path:
        """Where a local agent should run.

        Codex reports the session's git repo root in its turn metadata, which is
        the workspace the parent agent is operating in and therefore where the
        sub-agent's work belongs. When it is absent — a non-git cwd, or metadata
        Codex chose not to send — we fall back to the provider's configured
        default and otherwise refuse. Guessing would point an agent at the wrong
        repository and it would start editing before anyone noticed.
        """
        reported = identity.raw.get("workspaces") or ()
        for candidate in reported:
            path = Path(candidate)
            if path.is_dir():
                return path
        configured = self.workspaces.get(decision.provider_id)
        if configured is not None:
            return configured
        raise NoRouteAvailableError(
            f"provider {decision.provider_id!r} runs a local agent but this request carries no "
            "workspace and the provider has no 'default_workspace' configured",
            {},
        )


def create_app(context: GatewayContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Owned by the app rather than by `main()` so tests exercise the same
        # startup and shutdown path the service uses.
        try:
            yield
        finally:
            context.sticky.close()
            if context.attribution is not None:
                context.attribution.close()

    app = FastAPI(
        title="Fluxion Provider Gateway", docs_url=None, redoc_url=None, lifespan=lifespan
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness only. Deliberately does not start an agent: a wedged CLI
        must not make the supervisor restart a perfectly healthy process."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        configured = sorted(context.local_agents)
        ready = bool(configured)
        return JSONResponse(
            {"status": "ready" if ready else "unready", "providers": configured},
            status_code=200 if ready else 503,
        )

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        """The logical models Codex may name. Shaped like the OpenAI list
        endpoint because Codex parses it with the same client."""
        data = [
            {"id": model, "object": "model", "owned_by": provider_id}
            for provider_id, agent in sorted(context.local_agents.items())
            for model in sorted(agent.models)
        ]
        return {"object": "list", "data": data}

    @app.post("/v1/responses")
    async def responses(request: Request):
        return await _handle(context, request, force_compaction=False)

    @app.post("/v1/responses/compact")
    async def compact(request: Request):
        # Legacy endpoint: only reached when the user has disabled
        # `remote_compaction_v2`. V2 compaction arrives on /v1/responses and is
        # detected from request metadata instead.
        return await _handle(context, request, force_compaction=True)

    return app


async def _handle(context: GatewayContext, request: Request, *, force_compaction: bool):
    try:
        context.authenticator.verify_header(request.headers.get("authorization"))
    except AuthError as err:
        return _error_response(401, "authentication_error", str(err))

    try:
        body = json.loads(await request.body())
    except (TypeError, ValueError) as err:
        return _error_response(400, "invalid_request_error", f"malformed JSON body: {err}")
    if not isinstance(body, Mapping):
        return _error_response(400, "invalid_request_error", "request body must be a JSON object")

    _log_body(context, body)
    raw = RawRequest.create(body, request.headers)
    normalized = context.ingress.normalize(raw)
    identity = context.ingress.extract_identity(normalized)
    compaction = force_compaction or is_compaction_request(normalized, identity)
    requirements = derive_requirements(raw.body, is_compaction=compaction)

    try:
        stream = _run(context, raw, identity, requirements)
        # Pull the first chunk before returning a 200: a routing failure that
        # happens now can still be reported as a proper error status, which
        # stops being possible once headers are on the wire.
        first = await anext(stream)
    except NoRouteAvailableError as err:
        return _error_response(503, "no_route_available", str(err))
    except StopAsyncIteration:
        return _error_response(502, "upstream_error", "the local agent produced no events")

    return StreamingResponse(
        _prepend(first, stream),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )


async def _prepend(first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    yield first
    async for chunk in rest:
        yield chunk


async def _run(
    context: GatewayContext,
    raw: RawRequest,
    identity: RequestIdentity,
    requirements,
) -> AsyncIterator[bytes]:
    """Route the request and stream the chosen local agent's output.

    One attempt, no failover. A local agent has side effects in a real
    workspace from its first tool call, so retrying it against another candidate
    would run those effects twice — the retry loop that belongs in front of a
    stateless HTTP upstream is exactly wrong here.
    """
    sticky = context.sticky.lookup(identity.route_key)
    decision = context.router.select(
        identity,
        requirements,
        sticky_candidate=sticky.candidate_id if sticky else None,
    )
    async for chunk in _run_local_agent(
        context,
        context.local_agent_for(decision),
        decision,
        raw,
        identity,
        sticky,
        time.monotonic(),
    ):
        yield chunk


async def _run_local_agent(
    context: GatewayContext,
    upstream: LocalAgentUpstream,
    decision: RouteDecision,
    raw: RawRequest,
    identity: RequestIdentity,
    sticky,
    started: float,
) -> AsyncIterator[bytes]:
    """Serve the turn from a local agent CLI.

    There is no failover here by design. A local agent run has side effects in a
    real workspace from its first tool call; retrying it elsewhere would run
    those effects twice.

    If multi-upstream retry is ever reintroduced, two constraints have to come
    back with it, and both are protocol-level rather than preferences:

    1. Once any event with content has been written, the stream is committed —
       a different backend cannot be asked to regenerate what the caller has
       already seen, and a started tool call must never be replayed.
    2. Provider-bound reasoning payloads pin the conversation permanently.
       OpenAI reasoning depends on server-side state keyed by
       `previous_response_id`, Anthropic thinking blocks carry signatures that
       must be returned verbatim, and Gemini thought signatures behave the same
       way. Once one appears in the history, switching vendors produces an
       invalid request — not a worse answer.

    The tracker that enforced both was removed with the API-upstream path.
    """
    workspace = context.workspace_for(decision, identity)
    # Resume the agent session this sub-thread used last time, so a follow-up
    # turn continues rather than starting cold with no memory of its own work.
    session_id = sticky.executor_session_id if sticky else ""

    async for event in upstream.stream(
        raw.body,
        decision.upstream_model,
        workspace=workspace,
        session_id=session_id,
        # The role file's `sandbox_mode` binds Codex's sub-thread, which runs no
        # tools here, so enforcing it is ours to do — see codex_config.
        read_only=is_read_only_role(identity.route_hint),
    ):
        event_type = str(event.get("type", ""))
        yield encode_sse(event)
        if is_terminal_event(event_type):
            _finish(context, identity, decision, event, started)
            return


def _finish(
    context: GatewayContext,
    identity: RequestIdentity,
    decision: RouteDecision,
    event: Mapping[str, Any],
    started: float,
) -> None:
    """Persist the route and record telemetry after a terminal event.

    Success and usage are read from the event itself rather than from the
    adapter that produced it. Both are part of the Responses protocol, so
    reading them here keeps this path identical whether the turn was served by
    a model API or a local agent — and stops every new backend from having to
    reimplement the same two accessors.
    """
    if event.get("type") != EV_COMPLETED:
        # A failed turn is not evidence this route works; remembering it would
        # pin the conversation to a backend that just refused it.
        return

    response = event.get("response")
    response = response if isinstance(response, Mapping) else {}
    usage = response.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    fluxion_block = response.get("fluxion")
    fluxion_block = fluxion_block if isinstance(fluxion_block, Mapping) else {}

    executor_session_id = str(fluxion_block.get("executor_session_id", "") or "")
    context.sticky.remember(
        identity,
        decision.provider_id,
        decision.upstream_model,
        decision.policy_id,
        routing_reason=decision.routing_reason,
        executor_session_id=executor_session_id,
    )
    duration = time.monotonic() - started
    if context.attribution is not None:
        # Records the link, not the tokens. Fluxion's usage layer already counts
        # this run from the agent's own transcript; a second entry here would
        # bill one turn twice.
        context.attribution.record(
            identity,
            provider_id=decision.provider_id,
            upstream_model=decision.upstream_model,
            billing_source=BILLING_SUBSCRIPTION if executor_session_id else BILLING_API,
            executor_session_id=executor_session_id,
            duration_sec=duration,
        )
    log.info(
        "route=%s policy=%s kind=%s latency_ms=%d ctx_in=%s ctx_out=%s agent_usage=%s reason=%s",
        decision.candidate_id,
        decision.policy_id,
        identity.request_kind,
        int(duration * 1000),
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        # Present only for local-agent turns; this is the figure that cost
        # subscription quota, as opposed to the estimated sub-thread context.
        fluxion_block.get("agent_token_usage") or None,
        ",".join(decision.routing_reason),
    )


def _log_body(context: GatewayContext, body: Mapping[str, Any]) -> None:
    """Dump a request verbatim when FLUXION_PROVIDER_LOG_BODIES is set.

    The prompt Fluxion builds is a lossy view of the request — it keeps the
    developer items and the delegated task and drops the rest — so when a
    sub-agent behaves as though it never received its task, the built prompt
    cannot tell you whether the task was absent, encrypted, or simply somewhere
    this reader does not look. Off by default: bodies carry the full task text.
    """
    if context.body_log_dir is None:
        return
    try:
        context.body_log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        path = context.body_log_dir / f"request-{stamp}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        log.warning("could not write request body log", exc_info=True)


def _error_response(status: int, kind: str, message: str) -> JSONResponse:
    """Errors in the Responses API's own shape, so Codex parses them normally."""
    return JSONResponse(
        {"error": {"type": kind, "message": message}},
        status_code=status,
    )


def build_context(
    settings: GatewaySettings,
    routing: RoutingConfig,
    *,
    executors: Mapping[str, Any] | None = None,
) -> GatewayContext:
    """Wire a gateway from validated configuration."""
    local_agents: dict[str, LocalAgentUpstream] = {}
    workspaces: dict[str, Path] = {}
    for provider_id, spec in routing.enabled_providers().items():
        if spec.protocol == PROTOCOL_LOCAL_AGENT:
            executor = executors.get(spec.executor) if executors else None
            if executor is None:
                log.warning(
                    "provider %s wants executor %r, which is not registered; skipping",
                    provider_id,
                    spec.executor,
                )
                continue
            local_agents[provider_id] = LocalAgentUpstream(
                provider_id=provider_id,
                executor=executor,
                models=dict(spec.models),
            )
            if spec.default_workspace:
                workspaces[provider_id] = Path(spec.default_workspace).expanduser()
            continue
        log.warning(
            "provider %s declares protocol %r, which has no adapter; skipping",
            provider_id,
            spec.protocol,
        )
    if not local_agents:
        raise ConfigError("no usable providers after filtering; the gateway would serve nothing")

    return GatewayContext(
        router=Router(
            policies=routing.policies,
            routes=routing.routes,
            capabilities=routing.capability_index(),
            default_policy_id=settings.default_policy,
        ),
        sticky=StickyStore(
            settings.token_file.parent / "sticky.db", ttl_seconds=settings.sticky_ttl_seconds
        ),
        attribution=AttributionStore(settings.token_file.parent / "attribution.db"),
        authenticator=TokenAuthenticator(load_or_create_token(settings.token_file)),
        local_agents=local_agents,
        workspaces=workspaces,
        body_log_dir=(settings.token_file.parent / "logs" / "provider-requests")
        if settings.log_bodies
        else None,
    )


def main() -> int:
    """Entry point for `fluxion-provider`."""
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = GatewaySettings.load()
    try:
        # Fail before binding, not on the first request: a bad routing config or
        # an exposed bind is a startup problem, and a running-but-broken gateway
        # is worse than one that refused to start.
        check_bind(settings.host, token=str(settings.token_file))
        routing = RoutingConfig.load(settings.config_file)
        # Imported here rather than at module scope: pulling in the executor
        # stack costs startup time that the API-only path never needs.
        from fluxion.config.settings import Settings
        from fluxion.executors.registry import build_all_executors

        context = build_context(settings, routing, executors=build_all_executors(Settings.load()))
    except (ConfigError, InsecureBindError) as err:
        log.error("provider gateway cannot start: %s", err)
        return 1

    app = create_app(context)
    # Both maps, always. This line is how an operator confirms what actually
    # loaded, and printing only `upstreams` reported an empty gateway to every
    # local-agent user — the configuration most likely to be correct.
    loaded = [
        *(f"{name} (local agent)" for name in sorted(context.local_agents)),
    ]
    log.info(
        "provider gateway listening on %s:%s with providers %s",
        settings.host,
        settings.port,
        loaded or "NONE — check the routing config",
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    return 0
