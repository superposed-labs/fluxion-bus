"""Bounds on the text an executor hands back, independent of any channel.

An executor's answer reaches consumers with very different limits. An IM channel
caps a message at a few thousand characters; the provider gateway (where the
answer *is* a Codex sub-agent's report to its parent), the MCP server, and the
web UI have no such cap. Clipping to a channel's limit here truncated all of
them alike: a sub-agent's report arrived at its parent cut mid-sentence, and
often mid-path in a list of changed files, which is worse than short — the
parent cannot tell that a file is missing from a list that simply stops.

Every channel already clips at its own boundary (`MarkdownRenderer`'s
`soft_limit`, `line_renderer.clip_text`), which is the layer that knows the
limit it is clipping for. What belongs here is only a defensive bound, far above
any real answer, so that a raw-mode run that dumps its whole stdout cannot flood
the database and the event stream.
"""

from __future__ import annotations

TRUNCATION_SUFFIX = "\n...(truncated)"

# Not a transport limit — no channel's cap is anywhere near this. It exists so
# that "the answer is the whole stdout" (raw mode) has a ceiling at all.
EXECUTOR_TEXT_HARD_LIMIT = 40_000


def clip_text(text: str, limit: int = EXECUTOR_TEXT_HARD_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX
