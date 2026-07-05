from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

SUPPORTED_LOCALES = {"zh", "en", "ja"}
SUPPORTED_LOCALE_MODES = {"fixed", "auto"}
_LOCALES_DIR = Path(__file__).resolve().parent / "locales"


def normalize_locale(value: str | None, default: str = "en") -> str:
    raw = (value or "").strip().lower().replace("_", "-")
    if raw.startswith("zh"):
        return "zh"
    if raw.startswith("en"):
        return "en"
    if raw.startswith("ja"):
        return "ja"
    return default


def normalize_locale_mode(value: str | None, default: str = "auto") -> str:
    raw = (value or "").strip().lower()
    if raw in SUPPORTED_LOCALE_MODES:
        return raw
    return default


def detect_locale_from_text(text: str | None) -> str | None:
    value = (text or "").strip()
    if not value:
        return None
    if re.search(r"[\u3040-\u30ff\u31f0-\u31ff]", value):
        return "ja"
    if re.search(r"[\u4e00-\u9fff]", value):
        return "zh"
    if re.search(r"[A-Za-z]", value):
        return "en"
    return None


def resolve_locale(
    *,
    mode: str,
    fixed_locale: str,
    context: dict[str, Any] | None = None,
) -> str:
    locale = normalize_locale(fixed_locale)
    if mode == "fixed":
        return locale
    hints = context or {}
    for key in ("locale", "user_locale", "team_locale"):
        candidate = normalize_locale(str(hints.get(key) or ""), default="")
        if candidate in SUPPORTED_LOCALES:
            return candidate
    detected = detect_locale_from_text(str(hints.get("source_text") or ""))
    if detected:
        return detected
    return locale


def t(locale: str, key: str, **kwargs: Any) -> str:
    selected_locale = normalize_locale(locale)
    catalog = _load_catalog(selected_locale)
    template = catalog.get(key)
    if template is None and selected_locale != "en":
        template = _load_catalog("en").get(key)
    if template is None:
        return key
    if kwargs:
        return template.format(**kwargs)
    return template


@cache
def _load_catalog(locale: str) -> dict[str, str]:
    path = _LOCALES_DIR / f"{normalize_locale(locale)}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()}
