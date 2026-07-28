"""Outbound protocol adapters: Fluxion -> model vendor.

An upstream adapter converts the gateway's view of a request into one vendor's
API call, and that vendor's response stream back into Responses events.

Do not put inbound adapters here. `upstream/anthropic.py` speaks *to* Anthropic;
a future `ingress/messages.py` is spoken *to* by an Anthropic-shaped client. The
conversions run in opposite directions and must not share a package.
"""

from __future__ import annotations

from fluxion.provider_gateway.upstream.base import UpstreamAdapter, UpstreamRequest

__all__ = ["UpstreamAdapter", "UpstreamRequest"]
