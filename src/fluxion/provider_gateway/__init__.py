"""Provider Gateway: Fluxion answering as a model provider.

Codex spawns a native sub-agent, which asks its configured provider for a model
inference. That provider is this gateway, and instead of forwarding the request
to a vendor API it runs a local agent CLI on the user's subscription and renders
that agent's output as a Responses event stream. No API key, no per-token bill.

This package is deliberately independent of `fluxion.executors`. An executor
owns an agent process (lifecycle, workspace, results); the gateway owns a single
model request (protocol, routing, upstream). The only contact point is the
`Executor` protocol and the registry that builds them.

Layout mirrors the direction of conversion:

- `ingress/`  inbound: agent runtime -> Fluxion
- `upstream/` outbound: Fluxion -> whatever serves the turn

Same-named modules on either side perform *opposite* conversions, which is why
they never share a package.

The design notes that used to live in `docs/` are gone; what mattered is in the
module docstrings here, closest to the code each finding constrains. Start with
`upstream/local_agent.py` for what this mode can and cannot do, and
`codex_config.py` before changing anything that Codex has to parse.
"""

from __future__ import annotations

from fluxion.provider_gateway.identity import (
    IdentityConfidence,
    IdentityExtractor,
    RequestIdentity,
    derive_route_key,
)
from fluxion.provider_gateway.request import NormalizedRequest, RawRequest

__all__ = [
    "IdentityConfidence",
    "IdentityExtractor",
    "NormalizedRequest",
    "RawRequest",
    "RequestIdentity",
    "derive_route_key",
]
