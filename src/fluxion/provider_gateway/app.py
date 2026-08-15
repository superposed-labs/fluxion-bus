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

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from fluxion.core.models.attachment import Attachment, ImageAttachment
from fluxion.executors.base import accepts_native_image
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
from fluxion.provider_gateway.image_inputs import (
    ImageInputError,
    anthropic_remote_image_urls,
    materialize_anthropic_images,
    materialize_responses_images,
    prepare_image_prompt,
)
from fluxion.provider_gateway.ingress.messages import (
    AnthropicMessagesIngress,
    extract_messages_prompt,
)
from fluxion.provider_gateway.ingress.responses import (
    CodexResponsesIngress,
    is_compaction_request,
)
from fluxion.provider_gateway.messages_stream import (
    FLUXION_RESULT,
    encode_messages_sse,
    fresh_message_id,
    non_streaming_message,
)
from fluxion.provider_gateway.model_catalog import ExecutorCatalog, load_catalog
from fluxion.provider_gateway.model_health import CatalogHealth
from fluxion.provider_gateway.request import RawRequest
from fluxion.provider_gateway.routing import (
    NoRouteAvailableError,
    RouteDecision,
    Router,
    split_candidate,
)
from fluxion.provider_gateway.sticky import StickyRoute, StickyStore
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
    # Feeds `router.health_check`. None means no runtime catalog checking, which
    # is the pre-existing behaviour: every configured model is assumed to exist.
    model_health: CatalogHealth | None = None
    ingress: CodexResponsesIngress = field(default_factory=CodexResponsesIngress)
    messages_ingress: AnthropicMessagesIngress = field(default_factory=AnthropicMessagesIngress)
    # Where FLUXION_PROVIDER_LOG_BODIES writes captured requests. Off unless set:
    # a request body carries the full delegated task and the parent's context.
    body_log_dir: Path | None = None
    inbox_ttl_hours: float = 24.0
    max_request_bytes: int = 48 * 1024 * 1024
    max_concurrency: int = 12

    def local_agent_for(self, decision: RouteDecision) -> LocalAgentUpstream | None:
        return self.local_agents.get(decision.provider_id)

    def workspace_for(
        self, decision: RouteDecision, identity: RequestIdentity, sticky: StickyRoute | None = None
    ) -> Path:
        """Where a local agent should run.

        Codex reports the session's git repo root in its turn metadata, which is
        the workspace the parent agent is operating in and therefore where the
        sub-agent's work belongs. Three sources in order, and the middle one is
        not an optimization:

        1. What this request reports, which is current by definition.
        2. What the conversation used last time. Codex sends `workspaces` when it
           *spawns* a sub-agent and omits it on every follow-up turn to that
           sub-agent — measured, not assumed — so without this a second message
           to a live sub-agent has nowhere to run and the whole turn 503s.
        3. The provider's configured `default_workspace`.

        Otherwise refuse. Guessing would point an agent at the wrong repository
        and it would start editing before anyone noticed.
        """
        reported = identity.raw.get("workspaces") or ()
        for candidate in reported:
            path = Path(candidate)
            if path.is_dir():
                return path
        if sticky is not None and sticky.workspace:
            remembered = Path(sticky.workspace)
            if remembered.is_dir():
                return remembered
        configured = self.workspaces.get(decision.provider_id)
        if configured is not None:
            return configured
        raise NoRouteAvailableError(
            f"provider {decision.provider_id!r} runs a local agent but this request carries no "
            "workspace, none was remembered for this conversation, and the provider has no "
            "'default_workspace' configured",
            {},
        )


def _sticky_candidate(sticky: StickyRoute | None) -> str | None:
    """The remembered route, but only once a turn has completed on it.

    A row also gets written before its turn runs, to capture the workspace while
    the request still reports one. The provider/model on such a row record what
    was attempted, not what worked, and reusing them would pin a conversation to
    a backend that may have just failed it — the thing `remember`'s
    completed-only rule exists to prevent.
    """
    if sticky is None or not sticky.route_confirmed:
        return None
    return sticky.candidate_id


class _ProviderLimitsMiddleware:
    """Bound inference request memory and in-flight local-agent turns.

    This is pure ASGI middleware rather than ``BaseHTTPMiddleware`` so a
    concurrency slot remains occupied until the final streaming response byte
    has been sent. Releasing when the endpoint merely returns a
    ``StreamingResponse`` would make the limit ineffective for the gateway's
    normal long-lived SSE requests.
    """

    _LIMITED_PATHS = frozenset({"/v1/responses", "/v1/responses/compact", "/v1/messages"})

    def __init__(self, app: Any, *, max_request_bytes: int, max_concurrency: int) -> None:
        self._app = app
        self._max_request_bytes = max_request_bytes
        self._max_concurrency = max_concurrency
        self._active = 0
        self._lock = asyncio.Lock()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") not in self._LIMITED_PATHS:
            await self._app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", ())}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = -1
            if declared_size > self._max_request_bytes:
                await self._send_error(
                    send,
                    413,
                    "request_too_large",
                    f"request body exceeds {self._max_request_bytes} bytes",
                )
                return

        async with self._lock:
            if self._active >= self._max_concurrency:
                await self._send_error(
                    send,
                    429,
                    "concurrency_limit_exceeded",
                    f"provider gateway already has {self._max_concurrency} active request(s)",
                    retry_after=True,
                )
                return
            self._active += 1

        received = 0

        async def bounded_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_request_bytes:
                    raise _RequestTooLarge
            return message

        try:
            await self._app(scope, bounded_receive, send)
        except _RequestTooLarge:
            await self._send_error(
                send,
                413,
                "request_too_large",
                f"request body exceeds {self._max_request_bytes} bytes",
            )
        finally:
            async with self._lock:
                self._active -= 1

    @staticmethod
    async def _send_error(
        send: Any,
        status: int,
        kind: str,
        message: str,
        *,
        retry_after: bool = False,
    ) -> None:
        body = json.dumps({"error": {"type": kind, "message": message}}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if retry_after:
            headers.append((b"retry-after", b"1"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


class _RequestTooLarge(Exception):
    """The ASGI receive stream crossed the configured byte ceiling."""


def _native_images(executor: object, attachments: list[Attachment]) -> tuple[ImageAttachment, ...]:
    return tuple(
        attachment
        for attachment in attachments
        if isinstance(attachment, ImageAttachment)
        and accepts_native_image(executor, attachment.media_type)
    )


def create_app(context: GatewayContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Owned by the app rather than by `main()` so tests exercise the same
        # startup and shutdown path the service uses.
        if context.model_health is not None:
            context.model_health.start()
        try:
            yield
        finally:
            if context.model_health is not None:
                context.model_health.stop()
            context.sticky.close()
            if context.attribution is not None:
                context.attribution.close()

    app = FastAPI(
        title="Fluxion Provider Gateway", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.add_middleware(
        _ProviderLimitsMiddleware,
        max_request_bytes=context.max_request_bytes,
        max_concurrency=context.max_concurrency,
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

    @app.post("/v1/messages")
    async def messages(request: Request):
        return await _handle_messages(context, request)

    return app


async def _handle_messages(context: GatewayContext, request: Request):
    """Serve an Anthropic Messages turn from a local agent.

    Separate from `_handle` rather than parameterised over the ingress: the two
    protocols differ in framing, in how the prompt is assembled, and in where
    the turn's bookkeeping travels. Folding them together would put three
    branches inside one function and make each protocol harder to read than
    either is alone.
    """
    # Anthropic clients send the key bare in `x-api-key`; Claude Code sends a
    # Bearer `authorization`. Accept either, normalising to the Bearer form the
    # verifier expects rather than teaching it a second scheme.
    header = request.headers.get("authorization")
    if not header and request.headers.get("x-api-key"):
        header = f"Bearer {request.headers['x-api-key']}"
    try:
        context.authenticator.verify_header(header)
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
    normalized = context.messages_ingress.normalize(raw)
    identity = context.messages_ingress.extract_identity(normalized)
    # Reuse the Responses capability detector over the Messages content tree:
    # it recursively recognizes Anthropic `type: image` blocks as image input.
    requirements = derive_requirements({"input": body.get("messages", [])}, is_compaction=False)

    sticky = context.sticky.lookup(identity.route_key)
    session_id = sticky.executor_session_id if sticky else ""
    # Whether the agent still remembers this conversation decides how much of it
    # to resend — see `extract_messages_prompt`.
    try:
        decision = context.router.select(
            identity, requirements, sticky_candidate=_sticky_candidate(sticky)
        )
        upstream = context.local_agent_for(decision)
        if upstream is None:
            raise NoRouteAvailableError(f"no local agent for {decision.provider_id!r}", {})
        workspace = context.workspace_for(decision, identity, sticky)
    except NoRouteAvailableError as err:
        return _error_response(503, "no_route_available", str(err))

    # Before the turn, not after it: this request may be the only one that ever
    # reports the workspace, and it is no less true if the turn then fails.
    context.sticky.remember_workspace(
        identity,
        str(workspace),
        attempted_provider_id=decision.provider_id,
        attempted_upstream_model=decision.upstream_model,
        attempted_policy_id=decision.policy_id,
    )

    try:
        images = materialize_anthropic_images(
            body,
            workspace=workspace,
            resuming=bool(session_id),
            ttl_hours=context.inbox_ttl_hours,
            storage_key=identity.route_key,
        )
        remote_urls = anthropic_remote_image_urls(body, resuming=bool(session_id))
    except ImageInputError as err:
        return _error_response(err.status_code, err.kind, str(err))
    prompt = extract_messages_prompt(body, resuming=bool(session_id))
    native_images = _native_images(upstream.executor, images)
    if images or remote_urls:
        log.info(
            "provider image input route=%s files=%d urls=%d bytes=%d pixels=%d "
            "formats=%s native=%d file_bridge=%d",
            identity.route_key[:12],
            len(images),
            len(remote_urls),
            sum(image.byte_size for image in images),
            sum(image.pixel_count for image in images if isinstance(image, ImageAttachment)),
            ",".join(sorted({image.media_type for image in images})),
            len(native_images),
            len(images) - len(native_images),
        )
        prompt = prepare_image_prompt(
            prompt,
            images,
            workspace=workspace,
            native_attachments=native_images,
            remote_urls=remote_urls,
        )

    turn = upstream.stream_messages(
        body,
        decision.upstream_model,
        prompt=prompt,
        workspace=workspace,
        session_id=session_id,
        image_attachments=images,
        reasoning_effort=decision.reasoning_effort,
    )

    # `stream` defaults to false in the Messages API, and a caller that wants
    # one answer rather than a live feed is exactly who this ingress serves —
    # a script, a CI step, another agent's delegate. Returning SSE to one of
    # them hands its SDK a body it cannot parse.
    if not body.get("stream"):
        return await _collect_message(
            context, identity, decision, turn, workspace, decision.upstream_model
        )

    async def events() -> AsyncIterator[bytes]:
        async for event in turn:
            if event.get("type") == FLUXION_RESULT:
                # Bookkeeping, not protocol: recorded here and never written out.
                _finish_messages(context, identity, decision, event, workspace)
                continue
            yield encode_messages_sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )


async def _collect_message(
    context: GatewayContext,
    identity: RequestIdentity,
    decision: RouteDecision,
    turn: AsyncIterator[Mapping[str, Any]],
    workspace: Path,
    model: str,
) -> JSONResponse:
    """Fold the turn's events into the single object a non-streaming caller expects.

    The same event sequence is consumed either way; only the rendering differs.
    Token counts and the message id are read back out of the events rather than
    recomputed, so the two shapes cannot disagree about the same turn.

    An in-stream error becomes an HTTP status here. On the streaming path that
    is impossible — the 200 and its headers are already on the wire by the time
    anything fails — but a caller waiting for one object has nothing to read an
    error event out of, so it gets a real status code instead.
    """
    message_id = fresh_message_id()
    parts: list[str] = []
    input_tokens = 0
    output_tokens = 0

    async for event in turn:
        kind = str(event.get("type", ""))
        if kind == FLUXION_RESULT:
            _finish_messages(context, identity, decision, event, workspace)
        elif kind == "message_start":
            message = event.get("message")
            message = message if isinstance(message, Mapping) else {}
            message_id = str(message.get("id") or message_id)
            usage = message.get("usage")
            input_tokens = int((usage or {}).get("input_tokens") or 0)
        elif kind == "content_block_delta":
            delta = event.get("delta")
            parts.append(str((delta or {}).get("text") or "") if isinstance(delta, Mapping) else "")
        elif kind == "message_delta":
            usage = event.get("usage")
            output_tokens = int((usage or {}).get("output_tokens") or 0)
        elif kind == "error":
            detail = event.get("error")
            detail = detail if isinstance(detail, Mapping) else {}
            error_kind = str(detail.get("type") or "api_error")
            return _error_response(
                400 if error_kind == "invalid_request_error" else 500,
                error_kind,
                str(detail.get("message") or "local agent run failed"),
            )

    return JSONResponse(
        non_streaming_message(message_id, model, "".join(parts), input_tokens, output_tokens)
    )


def _finish_messages(
    context: GatewayContext,
    identity: RequestIdentity,
    decision: RouteDecision,
    event: Mapping[str, Any],
    workspace: Path,
) -> None:
    """Remember the route, the agent session, and the workspace behind this
    conversation."""
    if not event.get("success"):
        # A failed turn is not evidence the route works, and pinning it would
        # send the next turn to a backend that just refused this one.
        return
    context.sticky.remember(
        identity,
        decision.provider_id,
        decision.upstream_model,
        decision.policy_id,
        routing_reason=decision.routing_reason,
        executor_session_id=str(event.get("executor_session_id") or ""),
        workspace=str(workspace),
    )


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
    except ImageInputError as err:
        return _error_response(err.status_code, err.kind, str(err))
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
        sticky_candidate=_sticky_candidate(sticky),
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
    workspace = context.workspace_for(decision, identity, sticky)
    context.sticky.remember_workspace(
        identity,
        str(workspace),
        attempted_provider_id=decision.provider_id,
        attempted_upstream_model=decision.upstream_model,
        attempted_policy_id=decision.policy_id,
    )
    # Resume the agent session this sub-thread used last time, so a follow-up
    # turn continues rather than starting cold with no memory of its own work.
    session_id = sticky.executor_session_id if sticky else ""
    images = materialize_responses_images(
        raw.body,
        workspace=workspace,
        resuming=bool(session_id),
        ttl_hours=context.inbox_ttl_hours,
        storage_key=identity.route_key,
    )
    if images:
        native_images = _native_images(upstream.executor, images)
        log.info(
            "provider image input route=%s files=%d bytes=%d pixels=%d formats=%s "
            "native=%d file_bridge=%d",
            identity.route_key[:12],
            len(images),
            sum(image.byte_size for image in images),
            sum(image.pixel_count for image in images if isinstance(image, ImageAttachment)),
            ",".join(sorted({image.media_type for image in images})),
            len(native_images),
            len(images) - len(native_images),
        )

    async for event in upstream.stream(
        raw.body,
        decision.upstream_model,
        workspace=workspace,
        session_id=session_id,
        image_attachments=images,
        # The role file's `sandbox_mode` binds Codex's sub-thread, which runs no
        # tools here, so enforcing it is ours to do — see codex_config.
        read_only=is_read_only_role(identity.route_hint),
        reasoning_effort=decision.reasoning_effort,
    ):
        event_type = str(event.get("type", ""))
        yield encode_sse(event)
        if is_terminal_event(event_type):
            _finish(context, identity, decision, event, started, workspace)
            return


def _finish(
    context: GatewayContext,
    identity: RequestIdentity,
    decision: RouteDecision,
    event: Mapping[str, Any],
    started: float,
    workspace: Path,
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
        # Codex omits `workspaces` on every turn after the spawn, so this is the
        # only record of where the sub-thread runs.
        workspace=str(workspace),
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

    # Built before the router and the health source: ejecting a model has to
    # drop the sticky routes pointing at it, or those conversations spend every
    # subsequent turn looking up a row that selection will only reject again.
    sticky = StickyStore(
        settings.token_file.parent / "sticky.db", ttl_seconds=settings.sticky_ttl_seconds
    )
    model_health = _build_model_health(settings, routing, sticky)
    router = Router(
        policies=routing.policies,
        routes=routing.routes,
        capabilities=routing.capability_index(),
        default_policy_id=settings.default_policy,
    )
    if model_health is not None:
        # Assigned rather than passed at construction so that a gateway with the
        # check switched off keeps the router's own default instead of this
        # function restating what "no health source" means.
        router.health_check = model_health.health_check

    return GatewayContext(
        router=router,
        sticky=sticky,
        model_health=model_health,
        attribution=AttributionStore(settings.token_file.parent / "attribution.db"),
        authenticator=TokenAuthenticator(load_or_create_token(settings.token_file)),
        local_agents=local_agents,
        workspaces=workspaces,
        body_log_dir=(settings.token_file.parent / "logs" / "provider-requests")
        if settings.log_bodies
        else None,
        inbox_ttl_hours=settings.inbox_ttl_hours,
        max_request_bytes=settings.max_request_bytes,
        max_concurrency=settings.max_concurrency,
    )


def _build_model_health(
    settings: GatewaySettings,
    routing: RoutingConfig,
    sticky: StickyStore,
    *,
    load: Callable[[str], ExecutorCatalog] = load_catalog,
) -> CatalogHealth | None:
    """Wire the runtime catalog check, unless the operator turned it off.

    The eject hook drains that model's sticky routes. It deliberately does not
    touch pinned ones: a pin is a standing operator decision, and this is the
    one path here that acts without anybody asking.
    """
    if settings.model_health_refresh_seconds <= 0:
        log.info("runtime model health checking is off; configured models are used as declared")
        return None

    def drop_sticky_routes(candidate: str) -> None:
        provider_id, model = split_candidate(candidate)
        dropped = sticky.drain_model(provider_id, model)
        if dropped:
            log.warning(
                "dropped %d sticky route(s) bound to the retired model %s; "
                "those conversations will be re-routed on their next turn",
                dropped,
                candidate,
            )

    return CatalogHealth(
        routing,
        load=load,
        on_eject=drop_sticky_routes,
        interval=settings.model_health_refresh_seconds,
    )


def _warn_about_retired_models(routing) -> None:
    """Log configured models their own CLI no longer lists.

    A warning, never a refusal to start. The check runs a subprocess per CLI, so
    making it fatal would let a slow, upgrading, or missing CLI keep the gateway
    down — a worse outcome than one bad candidate in the routing table, which
    fails only the turns that actually select it. `fluxion-provider check-models`
    is the form of this check that exits non-zero.
    """
    try:
        from fluxion.provider_gateway.model_catalog import (
            describe_missing,
            verify_configured_models,
        )

        verification = verify_configured_models(routing)
    except Exception as err:  # noqa: BLE001 - a diagnostic must not break startup
        log.debug("model catalog check skipped: %s", err)
        return
    for candidate in verification.missing:
        log.warning(
            "%s — every turn routed there will fail; `fluxion-provider check-models` "
            "lists the full picture",
            describe_missing(routing, candidate),
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

    _warn_about_retired_models(routing)

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
