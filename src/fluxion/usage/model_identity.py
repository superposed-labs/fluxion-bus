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
_DOTTED_VERSION_RE = re.compile(r"(?:^|[-\s])(?P<version>\d+(?:\.\d+)+)(?:[-\s]|$)")


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
    elif slug is not None:
        billing_id = slug.group("model")
    else:
        # Deliberately NOT the effort-free family: `billing_id` selects a price
        # row, and the price table keys some non-Gemini models by their full
        # published id. Product identity is `parse_model_name().family`.
        billing_id = low

    effort = _effort_suffix(low)

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


# --- Product identity -------------------------------------------------------
#
# A model name carries two independent facts: which product it is, and how hard
# that product was told to think. Antigravity fuses them into one string
# (`gemini-3.1-pro-high`) while Codex and Claude keep effort as a runtime flag,
# so every consumer wanting one without the other used to re-derive the split
# from its own regexes — three copies that disagreed on `gpt-oss-120b-medium`.
# `parse_model_name` is now the only place that knows what an effort suffix
# looks like: the model catalog, the usage grouping and the executors all read
# the split from here, so a picker row and a usage row cannot drift apart.

_EFFORTS_PATTERN = "low|medium|high|xhigh|max|ultra"

_AGY_PAREN_SUFFIX_RE = re.compile(rf"\s*\((?:{_EFFORTS_PATTERN}|thinking)\)", re.IGNORECASE)
_AGY_GEMINI_DISPLAY_RE = re.compile(
    r"^Gemini\s+(?P<ver>\d+(?:\.\d+)*)\s+(?P<tier>Flash(?:-Lite)?|Pro)$", re.IGNORECASE
)
_AGY_CLAUDE_DISPLAY_RE = re.compile(
    r"^Claude\s+(?P<fam>Opus|Sonnet|Haiku)\s+(?P<ver>\d+(?:\.\d+)*)$", re.IGNORECASE
)
_AGY_GPT_OSS_DISPLAY_RE = re.compile(r"^GPT-OSS\s+(?P<size>\d+B)$", re.IGNORECASE)
_AGY_GEMINI_SLUG_RE = re.compile(
    r"^gemini-(?P<ver>\d+(?:\.\d+)*)-(?P<tier>flash(?:-lite)?|pro)"
    rf"(?:-(?:exp(?:-[a-z0-9]+)?|tiered|{_EFFORTS_PATTERN}|a|b))?$",
    re.IGNORECASE,
)
_AGY_CLAUDE_SLUG_RE = re.compile(
    rf"^claude-(?P<fam>opus|sonnet|haiku)-(?P<ver>\d+(?:-\d+)*)(?:-(?:thinking|{_EFFORTS_PATTERN}))?$",
    re.IGNORECASE,
)
_AGY_GPT_OSS_SLUG_RE = re.compile(
    rf"^gpt-oss-(?P<size>\d+b)(?:-(?:{_EFFORTS_PATTERN}))?$", re.IGNORECASE
)
# Effort as a trailing token of a slug (`-high`) or a display suffix (`(High)`).
_EFFORT_SUFFIX_RE = re.compile(rf"(?:[-\s]|\()(?P<effort>{_EFFORTS_PATTERN})\)?$", re.IGNORECASE)


@dataclass(frozen=True)
class ModelName:
    """One model name split into the product and the effort it was asked for.

    `family` is the id with any effort suffix removed; it is NOT necessarily a
    string the CLI accepts — Antigravity only publishes the fused variants, so
    the catalog keeps those in `variants` and resolves through them.
    """

    family: str
    label: str
    effort: str


def parse_model_name(provider: str, model: str) -> ModelName:
    """Split a live model name into its product identity and reasoning effort."""
    raw = (model or "").strip()
    if not raw or raw == "unknown":
        return ModelName(family=raw or "unknown", label=raw or "unknown", effort="")

    effort = _effort_suffix(raw)
    if provider.strip().lower() != "antigravity":
        # Codex and Claude publish effort as a separate axis, so the id is
        # already the product. Nothing to strip.
        return ModelName(family=raw.lower(), label=raw, effort=effort)

    family, label = _antigravity_product(raw)
    return ModelName(family=family, label=label, effort=effort)


def _effort_suffix(model: str) -> str:
    match = _EFFORT_SUFFIX_RE.search(model.strip())
    return match.group("effort").lower() if match is not None else ""


def _antigravity_product(model: str) -> tuple[str, str]:
    """Map an agy display label or backend slug to (family id, product label).

    Unifies effort/thinking variants and internal routing slugs onto one product:
      - 'gemini-3.7-flash-exp-a', 'Gemini 3.7 Flash (High)' -> 'Gemini 3.7 Flash'
      - 'claude-opus-4-6-thinking', 'Claude Opus 4.6 (Thinking)' -> 'Claude Opus 4.6'
      - 'gpt-oss-120b-medium', 'GPT-OSS 120B (Medium)' -> 'GPT-OSS 120B'
    """
    s = _AGY_PAREN_SUFFIX_RE.sub("", model.strip()).strip()

    gemini_display = _AGY_GEMINI_DISPLAY_RE.match(s)
    if gemini_display:
        return _gemini_product(
            gemini_display.group("ver"),
            gemini_display.group("tier"),
            # Display names always carry the real version; only backend slugs
            # abbreviate 3.5 to 3, so the remap must not reach this branch.
            remap_bare_three=False,
        )

    claude_display = _AGY_CLAUDE_DISPLAY_RE.match(s)
    if claude_display:
        return _claude_product(claude_display.group("fam"), claude_display.group("ver"))

    gpt_oss_display = _AGY_GPT_OSS_DISPLAY_RE.match(s)
    if gpt_oss_display:
        return _gpt_oss_product(gpt_oss_display.group("size"))

    gemini_slug = _AGY_GEMINI_SLUG_RE.match(s)
    if gemini_slug:
        return _gemini_product(gemini_slug.group("ver"), gemini_slug.group("tier"))

    claude_slug = _AGY_CLAUDE_SLUG_RE.match(s)
    if claude_slug:
        return _claude_product(claude_slug.group("fam"), claude_slug.group("ver"))

    gpt_oss_slug = _AGY_GPT_OSS_SLUG_RE.match(s)
    if gpt_oss_slug:
        return _gpt_oss_product(gpt_oss_slug.group("size"))

    if s == "gemini-default":
        return _gemini_product("3.5", "flash")

    return s, s


def _gemini_product(version: str, tier: str, *, remap_bare_three: bool = True) -> tuple[str, str]:
    # Antigravity's backend slugs report plain "3" for what its catalog publishes
    # as 3.5; keep both spellings on one product row.
    ver = "3.5" if (remap_bare_three and version == "3") else version
    tier_low = tier.lower()
    label_tier = "Flash-Lite" if tier_low == "flash-lite" else tier_low.capitalize()
    return f"gemini-{ver}-{tier_low}", f"Gemini {ver} {label_tier}"


def _claude_product(family: str, version: str) -> tuple[str, str]:
    # agy ids hyphenate the version (claude-opus-4-6); its labels dot it.
    slug_ver = version.replace(".", "-")
    label_ver = version.replace("-", ".")
    fam = family.lower()
    return f"claude-{fam}-{slug_ver}", f"Claude {fam.capitalize()} {label_ver}"


def _gpt_oss_product(size: str) -> tuple[str, str]:
    return f"gpt-oss-{size.lower()}", f"GPT-OSS {size.upper()}"
