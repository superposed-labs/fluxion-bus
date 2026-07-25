"""Capability matching: what a request needs vs what a model can do.

Hard filter, applied before any scoring. A model that is cheaper but cannot
serve the request is not a cheaper option — it is a failure that surfaces later
as a confusing upstream error, after we have already committed the stream.

Capabilities are declared per model in config, never inferred from the model
name. Name-based guessing breaks silently the first time a vendor ships a model
whose name does not match its predecessors' conventions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# Boolean capability flags a model may declare.
RESPONSES_NATIVE = "responses_native"
STREAMING = "streaming"
TOOL_CALLING = "tool_calling"
PARALLEL_TOOL_CALLING = "parallel_tool_calling"
REASONING = "reasoning"
REASONING_SUMMARY = "reasoning_summary"
IMAGE_INPUT = "image_input"
COMPACT = "compact"

BOOLEAN_CAPABILITIES = frozenset(
    {
        RESPONSES_NATIVE,
        STREAMING,
        TOOL_CALLING,
        PARALLEL_TOOL_CALLING,
        REASONING,
        REASONING_SUMMARY,
        IMAGE_INPUT,
        COMPACT,
    }
)


@dataclass(frozen=True)
class ModelCapabilities:
    """What one upstream model supports.

    Every flag defaults to False. An undeclared capability is treated as absent
    rather than assumed present, so forgetting to declare one costs a routing
    option instead of producing a broken request.
    """

    flags: frozenset[str] = frozenset()
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> ModelCapabilities:
        flags = {name for name in BOOLEAN_CAPABILITIES if bool(config.get(name))}
        return cls(
            flags=frozenset(flags),
            max_context_tokens=_positive_int(config.get("max_context_tokens")),
            max_output_tokens=_positive_int(config.get("max_output_tokens")),
        )

    def supports(self, capability: str) -> bool:
        return capability in self.flags


@dataclass(frozen=True)
class RequestRequirements:
    """What a request needs from whichever model serves it."""

    required: frozenset[str] = frozenset()
    min_context_tokens: int | None = None

    def unmet_by(self, capabilities: ModelCapabilities) -> tuple[str, ...]:
        """Reasons this model cannot serve the request, empty when it can.

        Returns reasons rather than a bool so the routing trace can explain why
        a candidate was dropped. "No model available" with no explanation is the
        hardest possible failure to debug in production.
        """
        reasons = [
            f"missing:{name}" for name in sorted(self.required) if not capabilities.supports(name)
        ]
        if self.min_context_tokens is not None:
            available = capabilities.max_context_tokens
            if available is not None and available < self.min_context_tokens:
                reasons.append(f"context:{available}<{self.min_context_tokens}")
        return tuple(reasons)


def derive_requirements(
    body: Mapping[str, Any],
    *,
    is_compaction: bool = False,
    min_context_tokens: int | None = None,
) -> RequestRequirements:
    """Read a Responses request and decide what the serving model must support.

    Deliberately conservative: when a field is ambiguous we require the
    capability rather than skipping it. Over-requiring costs a candidate;
    under-requiring produces a request the model cannot answer.
    """
    required: set[str] = set()

    if bool(body.get("stream", False)):
        required.add(STREAMING)

    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        required.add(TOOL_CALLING)
        # Codex sends `parallel_tool_calls` per turn based on model info. Only an
        # explicit True is a requirement — absent means "no opinion", and
        # requiring it then would drop otherwise-fine candidates.
        if body.get("parallel_tool_calls") is True:
            required.add(PARALLEL_TOOL_CALLING)

    reasoning = body.get("reasoning")
    if isinstance(reasoning, Mapping) and reasoning:
        required.add(REASONING)
        # Codex renders reasoning summaries in the UI; a model that reasons but
        # cannot summarize would leave that pane empty.
        if reasoning.get("summary"):
            required.add(REASONING_SUMMARY)

    if _has_image_input(body.get("input")):
        required.add(IMAGE_INPUT)

    if is_compaction:
        # Compaction output must satisfy Codex's structural expectations, so only
        # models verified against the compaction contract may serve it.
        required.add(COMPACT)

    return RequestRequirements(
        required=frozenset(required),
        min_context_tokens=min_context_tokens,
    )


def _has_image_input(items: Any) -> bool:
    """Detect image parts anywhere in the input tree.

    Images can appear nested inside message content, so a shallow scan of the
    top-level items would miss them and route to a text-only model.
    """
    if isinstance(items, Mapping):
        item_type = items.get("type")
        if isinstance(item_type, str) and "image" in item_type:
            return True
        return any(_has_image_input(value) for value in items.values())
    if isinstance(items, list):
        return any(_has_image_input(item) for item in items)
    return False


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def filter_candidates(
    candidates: Iterable[tuple[str, ModelCapabilities]],
    requirements: RequestRequirements,
) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    """Split candidates into those that can serve the request and those that cannot.

    Both halves are returned: the rejected map is what makes a routing decision
    explainable after the fact.
    """
    eligible: list[str] = []
    rejected: dict[str, tuple[str, ...]] = {}
    for name, capabilities in candidates:
        reasons = requirements.unmet_by(capabilities)
        if reasons:
            rejected[name] = reasons
        else:
            eligible.append(name)
    return eligible, rejected
