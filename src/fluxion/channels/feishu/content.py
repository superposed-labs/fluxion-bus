"""Helpers for Feishu (Lark) message content.

Feishu carries text in a JSON-encoded ``content`` string (e.g.
``{"text": "hello"}``) rather than a bare string, and a group message that
@-mentions the bot embeds placeholder tokens (``@_user_1``, ``@_all``) in the
text that map to the event's ``mentions`` array. These helpers convert between
that wire shape and the plain text the gateway works with.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Mention placeholders Feishu injects into group text, keyed to the event's
# ``mentions`` array. We strip them so the executor sees a clean prompt.
_MENTION_TOKEN_RE = re.compile(r"@_(?:user_\d+|all)\b")


def parse_inbound_text(content_json: str, mentions: Any = None) -> str:
    """Extract the plain text from a Feishu text message's ``content``.

    ``mentions`` is accepted for symmetry and future use (e.g. resolving a
    mention to a display name); the current implementation just strips the
    ``@_user_N`` / ``@_all`` placeholder tokens that Feishu leaves in the text.
    Returns an empty string for non-text payloads or malformed JSON.
    """
    if not content_json:
        return ""
    try:
        payload = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    text = payload.get("text")
    if not isinstance(text, str):
        return ""
    cleaned = _MENTION_TOKEN_RE.sub("", text)
    # Collapse the runs of whitespace left behind by removed mention tokens.
    return " ".join(cleaned.split())


def parse_inbound_resource(content_json: str, message_type: str) -> dict | None:
    """Extract the resource key + name from an ``image`` or ``file`` message.

    Image messages carry ``{"image_key": "..."}``; file messages carry
    ``{"file_key": "...", "file_name": "..."}``. Returns a dict with ``file_key``
    (the key Feishu's resource fetch uses for either type), ``resource_type``
    (``"image"`` / ``"file"``), and ``file_name``, or ``None`` if the payload is
    not a supported resource message.
    """
    if not content_json:
        return None
    try:
        payload = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if message_type == "image":
        key = payload.get("image_key")
        if isinstance(key, str) and key:
            return {"file_key": key, "resource_type": "image", "file_name": f"{key}.png"}
        return None
    if message_type == "file":
        key = payload.get("file_key")
        if isinstance(key, str) and key:
            name = payload.get("file_name")
            return {
                "file_key": key,
                "resource_type": "file",
                "file_name": name if isinstance(name, str) and name else key,
            }
        return None
    return None


def build_text_content(text: str) -> str:
    """Encode plain text into the JSON ``content`` Feishu expects for ``text``."""
    return json.dumps({"text": text}, ensure_ascii=False)


def build_markdown_card(text: str) -> dict:
    """Build a minimal updatable card holding ``text`` as a markdown element.

    ``config.update_multi`` must be true for the card to be patchable in place —
    that is what lets the adapter stream the answer into one bubble (post a
    placeholder, then patch it as output arrives). Mirrors the official
    ``lark-samples`` AI-bot card shape.
    """
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "streaming_mode": False},
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": [
                {
                    "tag": "markdown",
                    "content": text,
                    "text_align": "left",
                    "text_size": "normal",
                }
            ],
        },
    }
