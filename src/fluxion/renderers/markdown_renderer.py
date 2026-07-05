"""Channel-agnostic markdown renderer.

Renders task status and execution results while preserving the executor's
markdown (bold/lists/code), clipping to a per-channel soft limit. Shared by every
channel that displays markdown natively — Slack, Telegram, WeChat, Feishu, and
QQ. LINE is the exception: it flattens markdown to plain text, so it keeps its
own renderer (:mod:`fluxion.renderers.line_renderer`).

Status/result strings come from the locale-aware ``chat.*`` i18n keys (shared
with the LINE renderer via :class:`SummaryRenderer`).
"""

from __future__ import annotations

from fluxion.core.models.result import ExecutionResult
from fluxion.i18n import t
from fluxion.renderers.summary_renderer import SummaryRenderer

# Generous default; well under every supported channel's hard cap. Channels with
# a tighter limit pass their own value to MarkdownRenderer / clip_text.
DEFAULT_TEXT_SOFT_LIMIT = 4000
TRUNCATION_SUFFIX = "\n...(truncated)"


def clip_text(text: str, limit: int = DEFAULT_TEXT_SOFT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX


class MarkdownRenderer:
    """Renders status/results as markdown, clipped to ``soft_limit`` chars."""

    def __init__(self, soft_limit: int = DEFAULT_TEXT_SOFT_LIMIT) -> None:
        self._summary = SummaryRenderer()
        self._limit = soft_limit

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

    def render_result(self, task_id: str, result: ExecutionResult, *, locale: str = "en") -> str:
        text = self._summary.render(result, locale=locale)
        if result.success:
            return clip_text(text, self._limit)
        content = t(locale, "chat.result.finished", task_id=task_id) + "\n" + text
        return clip_text(content, self._limit)
