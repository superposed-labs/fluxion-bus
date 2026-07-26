from __future__ import annotations

import re
from dataclasses import dataclass

_GEMINI_DISPLAY_RE = re.compile(
    r"^gemini\s+(?P<version>\d+(?:\.\d+)*)\s+"
    r"(?P<tier>flash(?:[-\s]lite)?|pro)"
    r"(?:\s*\((?P<effort>[^()]*)\))?$",
    re.IGNORECASE,
)
_GEMINI_SLUG_RE = re.compile(
    r"^(?P<model>gemini-\d+(?:\.\d+)*-(?:flash(?:-lite)?|pro))"
    r"(?:-(?P<effort>low|medium|high|xhigh|max))?$",
    re.IGNORECASE,
)
_TRAILING_EFFORT_RE = re.compile(
    r"(?:[-\s]|\()(?P<effort>low|medium|high|xhigh|max)\)?$",
    re.IGNORECASE,
)
_DOTTED_VERSION_RE = re.compile(r"(?:^|[-\s])(?P<version>\d+(?:\.\d+)+)(?:[-\s]|$)")
_KNOWN_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


@dataclass(frozen=True)
class ModelIdentity:
    billing_id: str
    effort: str
    quota_pool: str
    version: tuple[int, ...]


def identify_model(provider: str, model: str) -> ModelIdentity:
    """Normalize a live/display model name into shared provider metadata."""
    low = model.strip().lower()
    display = _GEMINI_DISPLAY_RE.fullmatch(low)
    slug = _GEMINI_SLUG_RE.fullmatch(low)

    if display is not None:
        tier = re.sub(r"\s+", "-", display.group("tier"))
        billing_id = f"gemini-{display.group('version')}-{tier}"
        effort = (display.group("effort") or "").strip().lower()
        if effort not in _KNOWN_EFFORTS:
            effort = ""
    elif slug is not None:
        billing_id = slug.group("model")
        effort = slug.group("effort") or ""
    else:
        billing_id = low
        effort_match = _TRAILING_EFFORT_RE.search(low)
        effort = effort_match.group("effort") if effort_match is not None else ""

    quota_pool = ""
    if provider.strip().lower() == "antigravity":
        quota_pool = "gemini" if low.startswith(("gemini ", "gemini-")) else "external"

    return ModelIdentity(
        billing_id=billing_id,
        effort=effort.lower(),
        quota_pool=quota_pool,
        version=_model_version(billing_id),
    )


def billing_model_id(provider: str, model: str) -> str:
    return identify_model(provider, model).billing_id


def _model_version(model: str) -> tuple[int, ...]:
    match = _DOTTED_VERSION_RE.search(model)
    if match is None:
        return ()
    return tuple(int(part) for part in match.group("version").split("."))
