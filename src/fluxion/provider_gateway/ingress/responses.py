"""Codex ingress: the OpenAI Responses API as spoken by Codex Core.

Field names and precedence here are taken from the Codex source at commit
`c8957bbf0f` (`codex-rs/core/src/responses_metadata.rs`), which states that the
turn-metadata blob in `client_metadata` is canonical and that flat keys and HTTP
headers are "generated compatibility projections of this snapshot, not separate
sources of truth". The extractor follows that ordering exactly.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fluxion.provider_gateway.identity import (
    KIND_COMPACTION,
    KIND_MEMORY,
    KIND_PREWARM,
    KIND_TURN,
    ROUTE_HINT_AUTO,
    IdentityConfidence,
    RequestIdentity,
    derive_route_key,
)
from fluxion.provider_gateway.request import NormalizedRequest, RawRequest

INGRESS_NAME = "codex"

# Canonical blob key, present in both client_metadata and the compat header.
TURN_METADATA_KEY = "x-codex-turn-metadata"

# Flat client_metadata keys. Note the inconsistent prefixing: session/thread/turn
# are bare names while the rest carry `x-codex-`. This mirrors Codex, not our
# preference — renaming them here would break the mapping.
INSTALLATION_ID_KEY = "x-codex-installation-id"
SESSION_ID_KEY = "session_id"
THREAD_ID_KEY = "thread_id"
TURN_ID_KEY = "turn_id"
WINDOW_ID_KEY = "x-codex-window-id"
PARENT_THREAD_ID_KEY = "x-codex-parent-thread-id"
SUBAGENT_KEY = "x-openai-subagent"

# The route role header carried by each Fluxion provider entry in config.toml.
ROUTE_HEADER = "x-fluxion-route"

# Codex emits exactly these four request kinds.
_REQUEST_KINDS = frozenset({KIND_TURN, KIND_PREWARM, KIND_COMPACTION, KIND_MEMORY})


@dataclass(frozen=True)
class CodexIdentity:
    """Ingress-private parse result, before normalization."""

    installation_id: str | None = None
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    window_id: str | None = None
    parent_thread_id: str | None = None
    subagent_kind: str | None = None
    request_kind: str | None = None
    route_hint: str = ROUTE_HINT_AUTO
    # Keys are git repo roots (absolute paths). Populated by Codex only when the
    # session's cwd is inside a git repo with non-empty git metadata, so this is
    # frequently empty — see core/src/turn_metadata.rs::memory_workspaces.
    workspaces: tuple[str, ...] = ()


class CodexIdentityExtractor:
    """Derives conversation identity from a Codex Responses request."""

    ingress = INGRESS_NAME

    def extract(self, request: NormalizedRequest) -> RequestIdentity:
        parsed = self.parse(request)
        route_key, confidence = self._route_key(parsed)
        return RequestIdentity(
            ingress=self.ingress,
            route_key=route_key,
            confidence=confidence,
            route_hint=parsed.route_hint,
            request_kind=parsed.request_kind or KIND_TURN,
            installation_id=parsed.installation_id,
            session_id=parsed.session_id,
            thread_id=parsed.thread_id,
            parent_thread_id=parsed.parent_thread_id,
            turn_id=parsed.turn_id,
            raw={
                "window_id": parsed.window_id,
                "subagent_kind": parsed.subagent_kind,
                "workspaces": parsed.workspaces,
            },
        )

    def parse(self, request: NormalizedRequest) -> CodexIdentity:
        """Merge every identity source in Codex's documented precedence order."""
        client_metadata = request.client_metadata

        # 1. The canonical blob. It is a JSON *string* inside a string-valued map,
        #    so it needs a second parse.
        turn_metadata = _parse_turn_metadata(client_metadata.get(TURN_METADATA_KEY))
        # 3. Same blob as a compatibility header, for transports that dropped it.
        if not turn_metadata:
            turn_metadata = _parse_turn_metadata(request.header(TURN_METADATA_KEY))

        # 2/4. Flat projections. Consulted only where the blob left a gap.
        def pick(blob_key: str, flat_key: str, header: str | None = None) -> str | None:
            return (
                _clean(turn_metadata.get(blob_key))
                or _clean(client_metadata.get(flat_key))
                or (_clean(request.header(header)) if header else None)
            )

        return CodexIdentity(
            installation_id=pick("installation_id", INSTALLATION_ID_KEY),
            session_id=pick("session_id", SESSION_ID_KEY),
            thread_id=pick("thread_id", THREAD_ID_KEY),
            turn_id=pick("turn_id", TURN_ID_KEY),
            window_id=pick("window_id", WINDOW_ID_KEY, WINDOW_ID_KEY),
            parent_thread_id=pick("parent_thread_id", PARENT_THREAD_ID_KEY, PARENT_THREAD_ID_KEY),
            # `x-openai-subagent` is a request *class* (typically "collab_spawn"),
            # not a per-agent id, so it never contributes to the route key.
            subagent_kind=_clean(turn_metadata.get("subagent_kind"))
            or _clean(client_metadata.get(SUBAGENT_KEY))
            or _clean(request.header(SUBAGENT_KEY)),
            request_kind=_request_kind(turn_metadata.get("request_kind")),
            route_hint=_clean(request.header(ROUTE_HEADER)) or ROUTE_HINT_AUTO,
            workspaces=_workspaces(turn_metadata.get("workspaces")),
        )

    def _route_key(self, parsed: CodexIdentity) -> tuple[str, IdentityConfidence]:
        """Apply the degradation chain from section 7.4.

        Memory requests are excluded from the explicit tier even when they carry
        ids: Codex marks them as having no stable turn identity, so pinning a
        route on one would attach a model choice to a conversation that does not
        really exist.
        """
        if parsed.request_kind == KIND_MEMORY:
            return _ephemeral_key(parsed), IdentityConfidence.EPHEMERAL

        if parsed.thread_id:
            return (
                derive_route_key(
                    INGRESS_NAME,
                    [parsed.installation_id, parsed.parent_thread_id, parsed.thread_id],
                ),
                IdentityConfidence.EXPLICIT,
            )
        if parsed.session_id:
            return (
                derive_route_key(INGRESS_NAME, [parsed.installation_id, parsed.session_id]),
                IdentityConfidence.EXPLICIT,
            )
        if parsed.parent_thread_id and parsed.window_id:
            return (
                derive_route_key(
                    INGRESS_NAME,
                    [parsed.installation_id, parsed.parent_thread_id, parsed.window_id],
                ),
                IdentityConfidence.EXPLICIT,
            )
        return _ephemeral_key(parsed), IdentityConfidence.EPHEMERAL


class CodexResponsesIngress:
    """The Codex-facing side of the gateway."""

    name = INGRESS_NAME

    def __init__(self) -> None:
        self.identity = CodexIdentityExtractor()

    def normalize(self, raw: RawRequest) -> NormalizedRequest:
        return NormalizedRequest.from_raw(raw)

    def extract_identity(self, request: NormalizedRequest) -> RequestIdentity:
        return self.identity.extract(request)


def is_compaction_request(request: NormalizedRequest, identity: RequestIdentity) -> bool:
    """Whether this is a compaction turn.

    Since `remote_compaction_v2` became the default, compaction no longer has its
    own endpoint — it arrives on `/v1/responses` like any other turn, marked by
    `request_kind` and by a trailing `compaction_trigger` input item. Missing it
    means routing a compaction to whatever model the role prompt selected, which
    is both wrong and expensive.

    Both signals are checked because the metadata blob is only sent when Codex
    populates `request_kind`; the input marker survives even when it is not.
    """
    if identity.request_kind == KIND_COMPACTION:
        return True
    return _has_compaction_trigger(request.raw.body.get("input"))


def _has_compaction_trigger(items: Any) -> bool:
    if not isinstance(items, list):
        return False
    # Codex appends the trigger last; scanning from the end keeps this cheap on
    # the long histories compaction requests carry by definition.
    for item in reversed(items):
        if isinstance(item, Mapping) and item.get("type") == "compaction_trigger":
            return True
    return False


def _workspaces(raw: Any) -> tuple[str, ...]:
    """Repo roots from the turn metadata, in a stable order."""
    return tuple(sorted(str(key) for key in raw)) if isinstance(raw, Mapping) else ()


def _parse_turn_metadata(blob: str | None) -> Mapping[str, Any]:
    """Decode the canonical metadata blob, tolerating anything malformed.

    A metadata parse failure must cost us routing precision, never the request:
    the model call itself is still perfectly valid without it.
    """
    if not blob:
        return {}
    try:
        decoded = json.loads(blob)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, Mapping) else {}


def _request_kind(value: Any) -> str | None:
    """Accept only kinds Codex actually emits.

    An unrecognized kind is treated as absent rather than passed through, so a
    future Codex value cannot silently become a routing key nobody configured.
    """
    cleaned = _clean(value)
    return cleaned if cleaned in _REQUEST_KINDS else None


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _ephemeral_key(parsed: CodexIdentity) -> str:
    """A key good enough to trace one request and nothing more."""
    return derive_route_key(
        INGRESS_NAME,
        [
            "ephemeral",
            parsed.installation_id,
            parsed.window_id,
            parsed.turn_id,
            parsed.request_kind,
        ]
        # `turn_id` alone can be absent; fall back to object identity so the key
        # is always derivable rather than raising inside a request path.
        if any([parsed.installation_id, parsed.window_id, parsed.turn_id, parsed.request_kind])
        else ["ephemeral", str(id(parsed))],
    )
