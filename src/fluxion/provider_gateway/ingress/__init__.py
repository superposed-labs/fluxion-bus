"""Inbound protocol adapters: agent runtime -> Fluxion.

An ingress owns one wire protocol as spoken *to* the gateway: it parses the
request, derives identity, and renders the response stream back in that
protocol's own event shape.

Do not put outbound vendor adapters here. `upstream/anthropic.py` converts the
gateway's view into an Anthropic call; a future `ingress/messages.py` converts an
Anthropic-shaped call into the gateway's view. Sharing a package would make it
easy to reuse one for the other and get the direction backwards.
"""

from __future__ import annotations

from fluxion.provider_gateway.ingress.base import Ingress

__all__ = ["Ingress"]
