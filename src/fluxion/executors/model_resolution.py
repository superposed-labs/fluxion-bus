from __future__ import annotations

import re
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
