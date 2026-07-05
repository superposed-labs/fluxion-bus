from __future__ import annotations

import json

from fluxion.channels.feishu.content import build_text_content, parse_inbound_text


def test_parse_inbound_text_extracts_text():
    assert parse_inbound_text(json.dumps({"text": "hello world"})) == "hello world"


def test_parse_inbound_text_strips_mention_tokens():
    raw = json.dumps({"text": "@_user_1 please check @_all this"})
    assert parse_inbound_text(raw) == "please check this"


def test_parse_inbound_text_handles_non_text_and_garbage():
    assert parse_inbound_text("") == ""
    assert parse_inbound_text("not json") == ""
    assert parse_inbound_text(json.dumps({"image_key": "x"})) == ""
    assert parse_inbound_text(json.dumps(["a", "b"])) == ""


def test_build_text_content_roundtrips():
    content = build_text_content("你好 world")
    assert json.loads(content) == {"text": "你好 world"}
