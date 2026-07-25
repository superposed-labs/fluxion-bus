"""Inbound request handling: understand known fields, preserve unknown ones.

Both Codex and the Responses API keep evolving. A strict schema that drops
unrecognized keys would silently degrade requests the moment either side adds a
field, and the failure would surface as a model behaving oddly rather than as a
parse error. So we keep the original JSON verbatim and build a read-only
normalized view beside it for the router to consult.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

# Fields the gateway understands well enough to route on. Anything outside this
# set is carried through untouched — the list is for normalization, not for
# validation, and must never be used to reject a request.
KNOWN_FIELDS = frozenset(
    {
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "stream",
        "previous_response_id",
        "include",
        "metadata",
        "client_metadata",
        "text",
        "truncation",
    }
)


@dataclass(frozen=True)
class RawRequest:
    """The request exactly as received, plus the headers that came with it.

    Held separately from `NormalizedRequest` so the upstream adapter can forward
    fields the gateway never modelled. Header names are lowercased on the way in
    because HTTP header casing is not significant and callers vary.
    """

    body: Mapping[str, Any]
    headers: Mapping[str, str]

    @classmethod
    def create(
        cls, body: Mapping[str, Any], headers: Mapping[str, str] | None = None
    ) -> RawRequest:
        normalized_headers = {
            str(name).lower(): str(value) for name, value in (headers or {}).items()
        }
        return cls(
            body=MappingProxyType(dict(body)),
            headers=MappingProxyType(normalized_headers),
        )

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def unknown_fields(self) -> frozenset[str]:
        """Body keys the gateway does not model.

        Useful as a canary: a new key appearing here in production usually means
        the upstream protocol moved and the contract fixtures are stale.
        """
        return frozenset(self.body) - KNOWN_FIELDS


@dataclass(frozen=True)
class NormalizedRequest:
    """Read-only view over a request, for identity extraction and routing.

    Deliberately narrow. Anything the router does not need stays in `raw` so
    that adding a routing input is a visible change here rather than an
    accidental dependency on some nested key.
    """

    raw: RawRequest
    model: str | None = None
    stream: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
    client_metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: RawRequest) -> NormalizedRequest:
        model = raw.body.get("model")
        return cls(
            raw=raw,
            model=str(model) if isinstance(model, str) and model else None,
            # Absent `stream` means non-streaming per the Responses API default.
            stream=bool(raw.body.get("stream", False)),
            metadata=_as_mapping(raw.body.get("metadata")),
            client_metadata=_as_mapping(raw.body.get("client_metadata")),
        )

    def header(self, name: str) -> str | None:
        return self.raw.header(name)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Coerce a body field to a read-only mapping.

    Non-mapping values are flattened to empty rather than raising: a malformed
    `metadata` should cost us a routing hint, not the whole request.
    """
    if isinstance(value, Mapping):
        return MappingProxyType(dict(value))
    return MappingProxyType({})
