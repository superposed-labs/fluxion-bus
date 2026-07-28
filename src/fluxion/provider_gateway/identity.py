"""Request identity: "which conversation does this request belong to?"

Identity extraction is ingress-private. Codex sends explicit thread/parent ids
in its turn metadata; other runtimes may send nothing comparable and force us to
infer identity from prompt content. The routing core therefore depends on the
normalized `RequestIdentity` below, never on any one runtime's fields.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from fluxion.provider_gateway.request import NormalizedRequest

# Request kinds. Routing treats these differently: a compaction request must not
# inherit a normal turn's role prompt, and a prewarm should not create a sticky
# route on its own.
KIND_TURN = "turn"
KIND_PREWARM = "prewarm"
KIND_COMPACTION = "compaction"
KIND_MEMORY = "memory"

# The route hint used when the ingress cannot express an explicit role.
ROUTE_HINT_AUTO = "auto"


class IdentityConfidence(Enum):
    """How much the router may lean on this identity.

    This is not decorative metadata. A route pinned to an identity we merely
    guessed at can bleed one conversation's model choice into another, so the
    sticky store refuses to persist anything below `INFERRED`, and the Web UI
    must mark inferred identities rather than showing them like explicit ones.
    """

    # Protocol-level stable id (Codex `thread_id`).
    EXPLICIT = "explicit"
    # Derived from prompt fingerprint, metadata, or similar heuristics.
    INFERRED = "inferred"
    # No stable handle at all; usable for tracing one request, nothing more.
    EPHEMERAL = "ephemeral"

    @property
    def is_persistable(self) -> bool:
        """Whether a route keyed on this identity may be written to the store."""
        return self is not IdentityConfidence.EPHEMERAL


@dataclass(frozen=True)
class RequestIdentity:
    """Normalized identity shared by every ingress.

    Fields a given runtime cannot supply stay `None` — notably `parent_thread_id`
    is always `None` for runtimes with no parent/child thread concept. Consumers
    must treat absence as normal, not as an error.
    """

    ingress: str
    route_key: str
    confidence: IdentityConfidence
    route_hint: str = ROUTE_HINT_AUTO
    request_kind: str = KIND_TURN
    installation_id: str | None = None
    session_id: str | None = None
    thread_id: str | None = None
    parent_thread_id: str | None = None
    turn_id: str | None = None
    # Ingress-private fields kept for trace/debug only. Nothing in the routing
    # core may read from here — that would silently recouple it to one runtime.
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ingress:
            raise ValueError("RequestIdentity.ingress must be a non-empty string")
        if not self.route_key:
            raise ValueError("RequestIdentity.route_key must be a non-empty string")

    @property
    def is_persistable(self) -> bool:
        return self.confidence.is_persistable

    def trace_fields(self) -> dict[str, Any]:
        """Fields safe to attach to logs and route traces.

        `installation_id` is hashed rather than dropped so it stays useful for
        correlating a user's requests without being displayed as an identity.
        """
        return {
            "ingress": self.ingress,
            "route_key": self.route_key,
            "identity_confidence": self.confidence.value,
            "route_hint": self.route_hint,
            "request_kind": self.request_kind,
            "installation_hash": _hash_optional(self.installation_id),
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "parent_thread_id": self.parent_thread_id,
            "turn_id": self.turn_id,
        }


class IdentityExtractor(Protocol):
    """Per-ingress identity derivation.

    Implementations own their own degradation chain: which fields they try, in
    what order, and what confidence each step yields.
    """

    ingress: str

    def extract(self, request: NormalizedRequest) -> RequestIdentity: ...


def derive_route_key(ingress: str, parts: Sequence[str | None]) -> str:
    """Hash identity `parts` into a stable, collision-resistant route key.

    The ingress name is part of the digest so two runtimes cannot collide even
    if they happen to mint the same id string. Missing parts are encoded rather
    than skipped: ("a", None) and (None, "a") must not produce the same key.
    """
    if not ingress:
        raise ValueError("derive_route_key() requires a non-empty ingress name")
    if not any(part for part in parts):
        raise ValueError("derive_route_key() requires at least one non-empty part")

    # "\x1f" (unit separator) cannot appear in the ids we hash, so it is a safe
    # delimiter — using ":" would let a value containing ":" forge another key.
    payload = "\x1f".join([ingress, *(part or "" for part in parts)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_optional(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
