"""Convert the GitHub/CommonMark-flavored Markdown that executor agents emit
into the small HTML subset Telegram's ``parse_mode=HTML`` understands.

Telegram has no headings/lists/tables, so this maps the common constructs:
  - ``**bold**`` / ``__bold__``      -> ``<b>``
  - ``*italic*`` / ``_italic_``      -> ``<i>``
  - ``~~strike~~``                   -> ``<s>``
  - `` `code` ``                     -> ``<code>``
  - ```` ```lang fenced``` ````      -> ``<pre><code class="language-lang">``
  - ``[text](url)``                  -> ``<a href="url">text</a>``
  - ``# heading``                    -> ``<b>`` (Telegram has no headings)
  - ``- bullet``                     -> ``• bullet``
  - ``> quote``                      -> ``<blockquote>``

Everything else is HTML-escaped so it renders literally and never breaks the
parser. Delimiters are matched in balanced pairs, so the emitted HTML is always
well-formed; anything unmatched is left as escaped text.
"""

from __future__ import annotations

import re

# Private-use sentinels that wrap protected fragments (code/links) so later
# inline passes never touch their contents. They survive HTML-escaping.
_OPEN = ""
_CLOSE = ""


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(url: str) -> str:
    return _esc(url).replace('"', "&quot;")


def markdown_to_telegram_html(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    fence_lang = ""
    fence_buf: list[str] = []
    quote_buf: list[str] = []

    def flush_quote() -> None:
        if quote_buf:
            out.append("<blockquote>" + "\n".join(quote_buf) + "</blockquote>")
            quote_buf.clear()

    for line in lines:
        fence = re.match(r"^\s*```(\w*)\s*$", line)
        if fence:
            if not in_fence:
                flush_quote()
                in_fence = True
                fence_lang = fence.group(1)
                fence_buf = []
            else:
                code = _esc("\n".join(fence_buf))
                if fence_lang:
                    out.append(f'<pre><code class="language-{fence_lang}">{code}</code></pre>')
                else:
                    out.append(f"<pre>{code}</pre>")
                in_fence = False
                fence_lang = ""
                fence_buf = []
            continue
        if in_fence:
            fence_buf.append(line)
            continue

        quote = re.match(r"^\s*>\s?(.*)$", line)
        if quote:
            quote_buf.append(_inline(quote.group(1)))
            continue
        flush_quote()

        heading = re.match(r"^\s*(#{1,6})\s+(.*)$", line)
        if heading:
            out.append("<b>" + _inline(heading.group(2)) + "</b>")
            continue
        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if bullet:
            out.append(bullet.group(1) + "• " + _inline(bullet.group(2)))
            continue
        out.append(_inline(line))

    flush_quote()
    if in_fence and fence_buf:  # unterminated fence — emit what we have
        out.append("<pre>" + _esc("\n".join(fence_buf)) + "</pre>")
    return "\n".join(out)


def _inline(text: str) -> str:
    stash: list[str] = []

    def _put(fragment: str) -> str:
        stash.append(fragment)
        return f"{_OPEN}{len(stash) - 1}{_CLOSE}"

    # Protect inline code first; its content must not be reformatted.
    text = re.sub(r"`([^`]+)`", lambda m: _put("<code>" + _esc(m.group(1)) + "</code>"), text)
    # Links: [label](url)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: _put(f'<a href="{_esc_attr(m.group(2))}">{_esc(m.group(1))}</a>'),
        text,
    )
    # Escape everything else, then apply paired inline styles.
    text = _esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"(?<![*\w])\*(?!\s)([^*\n]+?)\*(?![*\w])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![_\w])_(?!\s)([^_\n]+?)_(?![_\w])", r"<i>\1</i>", text)
    # Restore protected fragments.
    text = re.sub(rf"{_OPEN}(\d+){_CLOSE}", lambda m: stash[int(m.group(1))], text)
    return text
