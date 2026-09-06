from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_AGY_SELECTED_MODEL_RE = re.compile(
    r'Propagating selected model override to backend:\s*label=\\"([^"]+)\\"'
    r'|Propagating selected model override to backend:\s*label="([^"]+)"'
)


def extract_antigravity_resolved_model(*sources: str | Path) -> str:
    """Return the selectable agy model id reported by its runtime log."""
    selected = ""
    for source in sources:
        text = _read_source(source)
        for match in _AGY_SELECTED_MODEL_RE.finditer(text):
            label = next((group for group in match.groups() if group), "")
            if label:
                selected = antigravity_label_to_model_id(label)
    return selected


def antigravity_label_to_model_id(label: str) -> str:
    """Convert agy's display label to the id accepted by ``agy --model``."""
    value = label.strip().lower()
    value = re.sub(r"[()]", " ", value)
    value = re.sub(r"[^a-z0-9.]+", "-", value).strip("-")
    # Gemini keeps dotted version ids (gemini-3.7-*); Claude's live agy ids use
    # a hyphenated version (claude-opus-4-6-*).
    if value.startswith("claude-"):
        value = value.replace(".", "-")
    return value


def _read_source(source: str | Path) -> str:
    if isinstance(source, Path):
        try:
            return source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    return source


# --- Model + effort resolution ----------------------------------------------
#
# Callers name a model and, optionally, how hard it should think. Codex and
# Claude take effort as a runtime flag; Antigravity publishes it fused into the
# model id (`gemini-3.1-pro-high`) and only ships some combinations —
# `gemini-3.1-pro` has high and low but no medium. Resolution is therefore
# always a LOOKUP in the published catalog, never string construction: an id
# this code invents would be rejected by the CLI at run time, long after the
# caller could do anything about it.


@dataclass(frozen=True)
class ModelOption:
    """One product row of an agent's catalog."""

    family: str
    label: str
    efforts: tuple[str, ...] = ()
    default_effort: str = ""
    # effort -> the exact id the CLI accepts. Empty for agents that take effort
    # as a flag, where `family` is itself the id.
    variants: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelCatalog:
    """A snapshot of what an agent can currently be asked for.

    `encodes_effort_in_id` is what makes the two agent shapes one code path: it
    decides whether a resolved effort leaves as part of the model id or as a
    separate flag.
    """

    agent: str
    options: tuple[ModelOption, ...] = ()
    encodes_effort_in_id: bool = False
    status: str = "fresh"  # fresh | stale | unavailable
    # Why the status is not fresh, in the words of whatever failed to load.
    error: str = ""


@dataclass(frozen=True)
class ModelTarget:
    """What to actually launch with."""

    model_id: str
    reasoning_effort: str
    source: str  # executor_runtime | requested_override | family+effort


class ModelResolutionError(ValueError):
    """A model/effort pair the catalog does not publish.

    Carries the machine-readable fields so a tool surface can hand the caller
    the available options instead of a bare string. `reason` narrows the single
    `error_code` the tool surface reports; the payload deliberately has no
    top-level `error` key, so there is only one field to branch on.
    """

    def __init__(self, payload: dict[str, object]):
        super().__init__(str(payload.get("next_action") or payload.get("reason") or "unresolved"))
        self.payload = payload


def resolve_model_target(
    *,
    catalog: ModelCatalog,
    model: str = "",
    reasoning_effort: str = "",
) -> ModelTarget:
    """Resolve a requested (model, effort) into a concrete launch target."""
    requested_model = (model or "").strip()
    effort = (reasoning_effort or "").strip().lower()

    if not requested_model and not effort:
        return ModelTarget(model_id="", reasoning_effort="", source="executor_runtime")

    option, carried_effort = _match(catalog, requested_model)

    if not effort:
        if (
            option is not None
            and option.efforts
            and catalog.encodes_effort_in_id
            and carried_effort is None
        ):
            # A product row, not a launchable id: the caller named the family and
            # left the effort to us, so take the model's own default. A model
            # with no effort axis has no variants and is launchable as named.
            return _variant_target(
                catalog=catalog,
                option=option,
                requested_model=requested_model,
                effort=option.default_effort,
                source="family+default_effort",
            )
        # A bare model id is passed through verbatim, known to the catalog or
        # not: a saved route or a stale catalog must not block a run whose id
        # the CLI would have accepted.
        return ModelTarget(
            model_id=requested_model,
            reasoning_effort="",
            source="requested_override",
        )

    if not requested_model:
        raise ModelResolutionError(
            {
                "reason": "model-required",
                "agent": catalog.agent,
                "requested_reasoning_effort": effort,
                "next_action": (
                    "reasoning_effort needs a model to apply to. Name one from "
                    "list_agent_models, or drop reasoning_effort to use the agent default."
                ),
                "next_tools": ["list_agent_models"],
            }
        )

    if option is None:
        if catalog.encodes_effort_in_id:
            # The effort has to become part of the id, and only the catalog
            # knows which fused ids exist. Guessing one is how you get a run
            # that dies inside the CLI.
            raise ModelResolutionError(
                {
                    "reason": "model-unknown",
                    "agent": catalog.agent,
                    "requested_model": requested_model,
                    "requested_reasoning_effort": effort,
                    "catalog_status": catalog.status,
                    "available_models": [item.family for item in catalog.options],
                    "next_action": (
                        f"{catalog.agent} encodes reasoning effort in the model id, so "
                        f"'{requested_model}' has to be a published model. Use one of "
                        "available_models, or pass an exact model id without reasoning_effort."
                    ),
                    "next_tools": ["list_agent_models"],
                }
            )
        # Effort is a separate flag here, so it is orthogonal to the id: let an
        # unlisted model through rather than block on catalog freshness.
        return ModelTarget(
            model_id=requested_model,
            reasoning_effort=effort,
            source="family+effort",
        )

    if catalog.encodes_effort_in_id and not option.efforts:
        raise ModelResolutionError(
            {
                "reason": "no-effort-axis",
                "agent": catalog.agent,
                "requested_model": requested_model,
                "requested_reasoning_effort": effort,
                "available_reasoning_efforts": [],
                "next_action": (
                    f"{option.family} is published as a single model with no reasoning "
                    "effort to choose. Drop reasoning_effort, or pick a model whose "
                    "supported_reasoning_efforts is non-empty."
                ),
                "next_tools": ["list_agent_models"],
            }
        )

    if option.efforts and effort not in option.efforts:
        raise ModelResolutionError(
            {
                "reason": "effort-unavailable",
                "agent": catalog.agent,
                "requested_model": requested_model,
                "requested_reasoning_effort": effort,
                "available_reasoning_efforts": list(option.efforts),
                "next_action": (
                    f"{option.family} is only published at "
                    f"{', '.join(option.efforts)}. Retry with one of those, or pass an "
                    "exact model id without reasoning_effort."
                ),
                "next_tools": ["list_agent_models"],
            }
        )

    if not catalog.encodes_effort_in_id:
        return ModelTarget(model_id=option.family, reasoning_effort=effort, source="family+effort")

    return _variant_target(
        catalog=catalog,
        option=option,
        requested_model=requested_model,
        effort=effort,
        source="family+effort",
    )


def _variant_target(
    *,
    catalog: ModelCatalog,
    option: ModelOption,
    requested_model: str,
    effort: str,
    source: str,
) -> ModelTarget:
    """Pick the published id for one (product, effort), or explain what exists.

    Never falls back to a neighbouring effort: quietly running `low` where the
    caller asked for `high` is the failure mode nobody can see in the output.
    """
    variant = option.variants.get(effort)
    if not variant:
        published = [item for item in option.variants if item]
        raise ModelResolutionError(
            {
                "reason": "effort-unavailable",
                "agent": catalog.agent,
                "requested_model": requested_model,
                "requested_reasoning_effort": effort,
                "available_reasoning_efforts": published,
                "next_action": (
                    f"{option.family} publishes no id for effort '{effort}'. Retry with "
                    f"one of {', '.join(published) or 'the listed efforts'}."
                ),
                "next_tools": ["list_agent_models"],
            }
        )
    return ModelTarget(model_id=variant, reasoning_effort="", source=source)


def _match(catalog: ModelCatalog, requested: str) -> tuple[ModelOption | None, str | None]:
    """Find the option a requested string names, by family or by exact variant id.

    The second element is the effort that string already carried, or None when
    it named a bare family — that is what tells an explicit id apart from a
    family the caller asked us to combine with an effort.
    """
    if not requested:
        return None, None
    low = requested.strip().lower()
    for option in catalog.options:
        if option.family.lower() == low:
            return option, None
    for option in catalog.options:
        for effort, model_id in option.variants.items():
            if model_id.lower() == low:
                return option, effort
    return None, None


def model_catalog_from_view(view: dict[str, object]) -> ModelCatalog:
    """Adapt a `list_agent_models` payload into the resolver's snapshot.

    Kept as a plain dict-in/dataclass-out step so resolution can be tested
    against recorded `codex debug models` / `agy models` output without either
    CLI being installed.
    """
    raw_models = view.get("models")
    options: list[ModelOption] = []
    for item in raw_models if isinstance(raw_models, list) else []:
        if not isinstance(item, dict):
            continue
        family = str(item.get("id") or "").strip()
        if not family:
            continue
        efforts = item.get("supported_reasoning_efforts")
        variants = item.get("variants")
        options.append(
            ModelOption(
                family=family,
                label=str(item.get("label") or family),
                efforts=tuple(
                    str(effort) for effort in (efforts if isinstance(efforts, list) else [])
                ),
                default_effort=str(item.get("default_reasoning_effort") or ""),
                variants={
                    str(key): str(value)
                    for key, value in (variants if isinstance(variants, dict) else {}).items()
                },
            )
        )
    return ModelCatalog(
        agent=str(view.get("agent") or ""),
        options=tuple(options),
        encodes_effort_in_id=str(view.get("effort_encoding") or "") == "model_id_suffix",
        status=str(view.get("catalog_status") or "fresh"),
    )
