from __future__ import annotations

from fluxion.core.models.result import ExecutionResult
from fluxion.i18n import t


class SummaryRenderer:
    def render(self, result: ExecutionResult, *, locale: str = "en") -> str:
        if result.success:
            answer = (result.summary or "").strip()
            if answer:
                return answer
            return t(locale, "summary.completed")

        lines = [
            t(locale, "summary.failed"),
            f"{t(locale, 'summary.reason')}: {result.summary}",
            f"{t(locale, 'summary.exit_code')}: {result.exit_code}",
        ]
        if result.diff_summary:
            lines.append(f"Diff: {result.diff_summary}")
        if result.log_file:
            lines.append(f"Log file: {result.log_file}")
        if result.stderr:
            lines.append(t(locale, "summary.error_excerpt") + ":\n" + _clip(result.stderr, 500))
        return "\n".join(lines)


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 16] + "\n...(truncated)"
