from __future__ import annotations

TRUNCATION_SUFFIX = "\n...(truncated)"
SLACK_TEXT_SOFT_LIMIT = 3900
SLACK_TEXT_RECOMMENDED_LIMIT = 4000
SLACK_TEXT_HARD_TRUNCATION_LIMIT = 40000


def clip_text(text: str, limit: int = SLACK_TEXT_SOFT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX
