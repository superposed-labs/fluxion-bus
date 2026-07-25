"""The ingress contract.

Frozen before any ingress or upstream work starts: the gateway core, the
protocol adapters, and the router are built in parallel against this interface,
so changing it later invalidates work in several places at once.
"""

from __future__ import annotations

from typing import Protocol

from fluxion.provider_gateway.identity import IdentityExtractor, RequestIdentity
from fluxion.provider_gateway.request import NormalizedRequest, RawRequest


class Ingress(Protocol):
    """One inbound wire protocol.

    Implementations stay stateless per request: everything about a request lives
    in the objects passed through, so concurrent sub-agents cannot bleed into
    each other via adapter state.
    """

    # Stable ingress name. Appears in route keys, sticky rows, and traces, so it
    # must not change once routes have been persisted under it.
    name: str

    identity: IdentityExtractor

    def normalize(self, raw: RawRequest) -> NormalizedRequest:
        """Build the router-facing view of an inbound request."""
        ...

    def extract_identity(self, request: NormalizedRequest) -> RequestIdentity:
        """Derive conversation identity, including its confidence level."""
        ...
