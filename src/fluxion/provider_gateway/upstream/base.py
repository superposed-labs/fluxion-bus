"""The upstream adapter contract.

Frozen alongside the ingress contract so protocol work can proceed in parallel.

Every adapter is judged against the same Codex contract suite regardless of the
vendor behind it — that is what keeps "supported" from meaning something
different per provider.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from fluxion.provider_gateway.capabilities import ModelCapabilities, RequestRequirements


@dataclass(frozen=True)
class UpstreamRequest:
    """A request rendered for one specific vendor endpoint."""

    url: str
    headers: Mapping[str, str]
    body: Mapping[str, Any]
    # Kept for the trace so a failed call can be tied back to the decision that
    # produced it without correlating across logs.
    provider_id: str = ""
    upstream_model: str = ""


class UnsupportedRequestError(RuntimeError):
    """Raised when an adapter cannot serve a request faithfully.

    Raised *before* the call goes out. Silently degrading — dropping images,
    flattening parallel tool calls — produces wrong answers that look like model
    failures, which is far harder to diagnose than an upfront refusal.
    """

    def __init__(self, provider_id: str, reasons: Iterable[str]):
        self.reasons = tuple(reasons)
        super().__init__(f"{provider_id} cannot serve this request: {', '.join(self.reasons)}")


class UpstreamAdapter(Protocol):
    """One vendor protocol, in the outbound direction."""

    provider_id: str

    def capabilities(self, model: str) -> ModelCapabilities:
        """Declared capabilities for `model`, from config — never guessed."""
        ...

    def build_request(self, body: Mapping[str, Any], model: str) -> UpstreamRequest:
        """Render a gateway request for this vendor, or refuse it."""
        ...

    def translate(self, event: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
        """Convert one vendor event into zero or more Responses events."""
        ...


@dataclass
class ProviderConfig:
    """Static configuration for one upstream provider."""

    provider_id: str
    base_url: str
    protocol: str = "responses"
    api_key: str | None = None
    enabled: bool = True
    models: Mapping[str, ModelCapabilities] = field(default_factory=dict)

    def capabilities(self, model: str) -> ModelCapabilities:
        # An unconfigured model has no declared capabilities, so it fails every
        # hard filter rather than being optimistically tried.
        return self.models.get(model, ModelCapabilities())

    def check_supported(self, model: str, requirements: RequestRequirements) -> None:
        reasons = requirements.unmet_by(self.capabilities(model))
        if reasons:
            raise UnsupportedRequestError(self.provider_id, reasons)
