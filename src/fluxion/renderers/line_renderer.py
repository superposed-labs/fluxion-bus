"""LINE message renderer.

Formats task status and execution results as plain text suitable for LINE
messages.
"""

from __future__ import annotations

import re

from fluxion.core.models.result import ExecutionResult
from fluxion.i18n import t
from fluxion.renderers.summary_renderer import SummaryRenderer

LINE_TEXT_SOFT_LIMIT = 4000
TRUNCATION_SUFFIX = "\n...(truncated)"


def clip_text(text: str, limit: int = LINE_TEXT_SOFT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX


def markdown_to_plain_text(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    fence_lang = ""

    for line in lines:
        fence = re.match(r"^\s*```(\w*)\s*$", line)
        if fence:
            if not in_fence:
                in_fence = True
                fence_lang = fence.group(1).strip()
                out.append(f"--- CODE ({fence_lang if fence_lang else 'text'}) ---")
            else:
                out.append("--------------------")
                in_fence = False
            continue

        if in_fence:
            out.append(line)
            continue

        # Headers: # -> ■, ## -> ◆, ### -> ●, #### -> ○
        heading = re.match(r"^\s*(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            bullet = "■" if level == 1 else "◆" if level == 2 else "●" if level == 3 else "○"
            out.append(f"{bullet} {heading.group(2)}")
            continue

        # Bullet lists
        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if bullet:
            out.append(bullet.group(1) + "• " + bullet.group(2))
            continue

        out.append(line)

    result = "\n".join(out)

    # Inline code: `code` -> code
    result = re.sub(r"`([^`\n]+)`", r"\1", result)

    # Bold / Italic / Strike
    result = re.sub(r"\*\*([^*]+)\*\*", r"\1", result)
    result = re.sub(r"__([^_]+)__", r"\1", result)
    result = re.sub(r"\*([^*]+)\*", r"\1", result)
    result = re.sub(r"_([^_]+)_", r"\1", result)
    result = re.sub(r"~~([^~]+)~~", r"\1", result)

    # Links: [label](url) -> label (url)
    result = re.sub(r"\[([^\]\n]+)\]\(([^)\s\n]+)\)", r"\1 (\2)", result)

    return result


class LineRenderer:
    def __init__(self) -> None:
        self._summary = SummaryRenderer()

    def render_status(
        self,
        task_id: str,
        status: str,
        detail: str | None = None,
        *,
        locale: str = "en",
    ) -> str:
        if status == "RUNNING":
            if detail and detail.startswith("elapsed="):
                value = detail.split("=", 1)[1]
                return t(locale, "chat.status.running_elapsed", elapsed=value)
            return t(locale, "chat.status.running")
        if status == "QUEUED":
            return t(locale, "chat.status.queued")
        if status == "RECEIVED":
            return t(locale, "chat.status.received")
        if status == "RETRYING":
            return t(locale, "chat.status.retrying")
        text = t(locale, "chat.status.task", task_id=task_id, status=status)
        if detail:
            text += f" ({detail})"
        return text

    def render_result(
        self,
        task_id: str,
        result: ExecutionResult,
        *,
        locale: str = "en",
    ) -> str:
        text = self._summary.render(result, locale=locale)
        text_plain = markdown_to_plain_text(text)
        if result.success:
            return clip_text(text_plain, LINE_TEXT_SOFT_LIMIT)
        content = t(locale, "chat.result.finished", task_id=task_id) + "\n" + text_plain
        return clip_text(content, LINE_TEXT_SOFT_LIMIT)
