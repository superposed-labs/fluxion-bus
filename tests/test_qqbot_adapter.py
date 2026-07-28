from __future__ import annotations

import os
import threading
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from fluxion.channels.base import SettingsHotReloader
from fluxion.channels.qqbot.adapter import (
    PASSIVE_REPLY_MAX_AGE_SEC_C2C,
    PASSIVE_REPLY_MAX_AGE_SEC_GROUP,
    QQBotChannelAdapter,
    markdown_for_qq,
)
from fluxion.channels.qqbot.qqbot_client import QQBotClient
from fluxion.channels.qqbot.signing import callback_signature, verify_signature
from fluxion.channels.qqbot.token_manager import QQBotTokenManager
from fluxion.core.models.result import ExecutionResult
from fluxion.renderers.markdown_renderer import MarkdownRenderer

# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def test_signing_verify_roundtrip_and_tamper():
    secret = "unit_test_secret_value"
    ts = "1700000000"
    body = b'{"op":0,"t":"C2C_MESSAGE_CREATE"}'

    # Sign exactly the way QQ would: over (timestamp + body) with the seed it
    # derives from the same secret, then ensure we accept it and reject tampering.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from fluxion.channels.qqbot.signing import _seed_from_secret

    key = Ed25519PrivateKey.from_private_bytes(_seed_from_secret(secret))
    sig = key.sign(ts.encode() + body).hex()

    assert verify_signature(secret, ts, body, sig) is True
    assert verify_signature(secret, ts, body + b"x", sig) is False
    assert verify_signature(secret, ts, body, "not-hex") is False
    assert verify_signature(secret, "", body, sig) is False


def test_callback_signature_is_hex_and_verifiable():
    secret = "another_secret"
    sig = callback_signature(secret, "1700", "plain_tok")
    assert len(sig) == 128  # 64-byte Ed25519 signature in hex
    # The callback signs (event_ts + plain_token); verify with the public key.
    assert verify_signature(secret, "1700", b"plain_tok", sig) is True


# ---------------------------------------------------------------------------
# Token manager
# ---------------------------------------------------------------------------


def test_token_manager_caches_and_refreshes(monkeypatch):
    calls = {"n": 0}

    def fake_refresh(self):  # noqa: ANN001
        calls["n"] += 1
        self._token = f"tok-{calls['n']}"  # noqa: SLF001
        self._expires_at = time.monotonic() + 7200  # noqa: SLF001

    monkeypatch.setattr(QQBotTokenManager, "_refresh", fake_refresh)
    mgr = QQBotTokenManager("app", "secret")

    assert mgr.get_access_token() == "tok-1"
    assert mgr.get_access_token() == "tok-1"  # cached, no second refresh
    assert calls["n"] == 1

    mgr.invalidate()
    assert mgr.get_access_token() == "tok-2"
    assert calls["n"] == 2


def test_token_manager_refreshes_near_expiry(monkeypatch):
    calls = {"n": 0}

    def fake_refresh(self):  # noqa: ANN001
        calls["n"] += 1
        self._token = f"tok-{calls['n']}"  # noqa: SLF001
        # Already inside the 90s refresh margin, so the next call must refresh.
        self._expires_at = time.monotonic() + 30  # noqa: SLF001

    monkeypatch.setattr(QQBotTokenManager, "_refresh", fake_refresh)
    mgr = QQBotTokenManager("app", "secret")

    assert mgr.get_access_token() == "tok-1"
    assert mgr.get_access_token() == "tok-2"
    assert calls["n"] == 2


def test_token_manager_requires_credentials():
    with pytest.raises(ValueError):
        QQBotTokenManager("", "secret")


# ---------------------------------------------------------------------------
# Client request body
# ---------------------------------------------------------------------------


class _FakeTokens:
    def get_access_token(self) -> str:
        return "TKN"

    def invalidate(self) -> None:  # pragma: no cover - not exercised here
        pass


def test_client_builds_passive_reply_body():
    client = QQBotClient(_FakeTokens())
    captured: dict[str, object] = {}

    def fake_call(path, payload):  # noqa: ANN001
        captured["path"] = path
        captured["payload"] = payload
        return {}

    client._call = fake_call  # type: ignore[method-assign]  # noqa: SLF001

    client.send_c2c_text("user-openid", "hi", msg_id="m1", msg_seq=3)
    assert captured["path"] == "/v2/users/user-openid/messages"
    assert captured["payload"] == {
        "content": "hi",
        "msg_type": 0,
        "msg_id": "m1",
        "msg_seq": 3,
    }

    client.send_group_text("grp-openid", "yo")
    assert captured["path"] == "/v2/groups/grp-openid/messages"
    # No msg_id supplied -> active message, no msg_id/msg_seq keys.
    assert captured["payload"] == {"content": "yo", "msg_type": 0}


def test_client_builds_markdown_body():
    client = QQBotClient(_FakeTokens())
    captured: dict[str, object] = {}

    def fake_call(path, payload):  # noqa: ANN001
        captured["path"] = path
        captured["payload"] = payload
        return {}

    client._call = fake_call  # type: ignore[method-assign]  # noqa: SLF001

    client.send_c2c_markdown("user-openid", "# Hi", msg_id="m1", msg_seq=2)
    assert captured["path"] == "/v2/users/user-openid/messages"
    # Native markdown: msg_type 2, content rides inside the markdown object.
    assert captured["payload"] == {
        "markdown": {"content": "# Hi"},
        "msg_type": 2,
        "msg_id": "m1",
        "msg_seq": 2,
    }


def test_client_sandbox_base_url():
    prod = QQBotClient(_FakeTokens())
    sand = QQBotClient(_FakeTokens(), sandbox=True)
    assert "sandbox" not in prod._api_base  # noqa: SLF001
    assert "sandbox" in sand._api_base  # noqa: SLF001


# ---------------------------------------------------------------------------
# Adapter event routing + send sequencing
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self) -> None:
        # (target, openid, content, msg_id, msg_seq) — text and markdown sends both
        # land here so msg_seq sequencing tests see every send.
        self.calls: list[tuple[str, str, str, str | None, int]] = []
        # Records the kind ("text"/"markdown") of the last send per target.
        self.kinds: list[str] = []

    def send_c2c_text(self, openid, content, *, msg_id=None, msg_seq=1):  # noqa: ANN001
        self.calls.append(("c2c", openid, content, msg_id, msg_seq))
        self.kinds.append("text")

    def send_group_text(self, openid, content, *, msg_id=None, msg_seq=1):  # noqa: ANN001
        self.calls.append(("group", openid, content, msg_id, msg_seq))
        self.kinds.append("text")

    def send_c2c_markdown(self, openid, content, *, msg_id=None, msg_seq=1):  # noqa: ANN001
        self.calls.append(("c2c", openid, content, msg_id, msg_seq))
        self.kinds.append("markdown")

    def send_group_markdown(self, openid, content, *, msg_id=None, msg_seq=1):  # noqa: ANN001
        self.calls.append(("group", openid, content, msg_id, msg_seq))
        self.kinds.append("markdown")


class _FakeSessions:
    def get_executor_override(self, *, conversation_key, channel, user_id):  # noqa: ANN001
        return None


class _FakeGateway:
    def __init__(self) -> None:
        self._sessions = _FakeSessions()
        self.submitted: list[tuple[object, dict]] = []

    def handle_control_command(self, *, text, user_id, convo_key, channel):  # noqa: ANN001
        return None

    def submit_task(self, *, task, channel_adapter, channel_context):  # noqa: ANN001
        self.submitted.append((task, channel_context))
        return True, "ok"


def _make_adapter(
    *,
    allowed=("user-1",),
    allow_group=True,
    locale_mode="fixed",
    ui_locale="en",
) -> QQBotChannelAdapter:
    adapter = object.__new__(QQBotChannelAdapter)
    adapter._settings = SimpleNamespace(  # noqa: SLF001
        qqbot_allowed_users=set(allowed),
        qqbot_allow_group_chat=allow_group,
        qqbot_default_workspace="",
        qqbot_client_secret="sekret",
        data_dir=Path("/tmp"),
        default_executor="codex",
        status_updates={"DONE", "FAILED"},
        locale_mode=locale_mode,
        ui_locale=ui_locale,
        resolve_workspace=lambda raw: Path("/tmp/ws"),
        inbox_ttl_hours=0.0,
    )
    adapter._renderer = MarkdownRenderer()  # noqa: SLF001
    adapter._gateway = _FakeGateway()  # noqa: SLF001
    adapter._client = _FakeClient()  # noqa: SLF001
    adapter._pending_store = SimpleNamespace(
        record=lambda *_args, **_kw: None, remove=lambda *_args: None
    )  # noqa: SLF001
    adapter._settings_reloader = SimpleNamespace(reload_if_changed=lambda settings: settings)  # noqa: SLF001
    adapter._lock = threading.Lock()  # noqa: SLF001
    adapter._reply_msg_ids = {}  # noqa: SLF001
    adapter._reply_msg_seen_at = {}  # noqa: SLF001
    adapter._msg_seq = {}  # noqa: SLF001
    adapter._stream_buffers = {}  # noqa: SLF001
    return adapter


def _c2c_event(content="hello", openid="user-1", msg_id="m-1", attachments=None):
    d = {"id": msg_id, "content": content, "author": {"user_openid": openid}}
    if attachments is not None:
        d["attachments"] = attachments
    return {"op": 0, "t": "C2C_MESSAGE_CREATE", "d": d}


def _group_event(content="hi bot", member="user-1", group="grp-9", msg_id="m-2"):
    return {
        "op": 0,
        "t": "GROUP_AT_MESSAGE_CREATE",
        "d": {
            "id": msg_id,
            "content": content,
            "author": {"member_openid": member},
            "group_openid": group,
        },
    }


def test_c2c_event_submits_task_with_context():
    adapter = _make_adapter()
    adapter._handle_event(_c2c_event())

    submitted = adapter._gateway.submitted  # noqa: SLF001
    assert len(submitted) == 1
    task, ctx = submitted[0]
    assert task.channel == "qqbot"
    assert task.user_id == "user-1"
    assert ctx["qqbot_target_type"] == "c2c"
    assert ctx["qqbot_openid"] == "user-1"
    # source_text lets auto-mode locale detection follow the question's language.
    assert ctx["source_text"] == "hello"
    # The inbound msg_id is held for free passive replies.
    assert adapter._reply_msg_ids[task.id] == "m-1"  # noqa: SLF001
    assert task.id in adapter._reply_msg_seen_at  # noqa: SLF001


def test_group_event_submits_with_group_target():
    adapter = _make_adapter()
    adapter._handle_event(_group_event())

    _task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001
    assert ctx["qqbot_target_type"] == "group"
    assert ctx["qqbot_openid"] == "grp-9"


def test_group_event_ignored_when_disabled():
    adapter = _make_adapter(allow_group=False)
    adapter._handle_event(_group_event())
    assert adapter._gateway.submitted == []  # noqa: SLF001


def test_unauthorized_user_is_denied_not_submitted(monkeypatch):
    monkeypatch.setattr("fluxion.channels.allowlist_messages.platform.system", lambda: "Darwin")
    adapter = _make_adapter(allowed=("someone-else",))
    recorded = []
    adapter._pending_store = SimpleNamespace(  # noqa: SLF001
        record=lambda user_id, preview, **_kw: recorded.append((user_id, preview)),
        remove=lambda *_args: None,
    )
    adapter._handle_event(_c2c_event(openid="intruder"))

    assert adapter._gateway.submitted == []  # noqa: SLF001
    assert recorded == [("intruder", "hello")]
    calls = adapter._client.calls  # noqa: SLF001
    assert len(calls) == 1
    assert calls[0][0] == "c2c"
    assert "Rejected: user is not in allowlist" in calls[0][2]
    assert "FLUXION_QQBOT_ALLOWED_USERS" in calls[0][2]
    assert "Preferences" in calls[0][2]
    assert "Messaging -> QQ -> Pending Users" in calls[0][2]
    assert "intruder" in calls[0][2]


def test_unauthorized_user_hint_omits_macos_copy_off_macos(monkeypatch):
    monkeypatch.setattr("fluxion.channels.allowlist_messages.platform.system", lambda: "Linux")
    adapter = _make_adapter(allowed=("someone-else",))
    adapter._handle_event(_c2c_event(openid="intruder"))

    text = adapter._client.calls[0][2]  # noqa: SLF001
    assert "macOS app" not in text
    assert "FLUXION_QQBOT_ALLOWED_USERS" in text
    assert "After saving .env" in text


def test_unauthorized_user_rejection_uses_detected_locale(monkeypatch):
    monkeypatch.setattr("fluxion.channels.allowlist_messages.platform.system", lambda: "Linux")
    adapter = _make_adapter(allowed=("someone-else",), locale_mode="auto", ui_locale="en")

    adapter._handle_event(_c2c_event(content="你好", openid="intruder"))

    text = adapter._client.calls[0][2]  # noqa: SLF001
    assert "已拒绝" in text
    assert "请管理员" in text
    assert "保存 .env 后" in text


def test_qqbot_allowlist_reloads_before_authorizing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    workspace = tmp_path / "ws"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    env_file.write_text(
        "\n".join(
            [
                f"FLUXION_WORKSPACE_ROOT={workspace}",
                f"FLUXION_ALLOWED_WORKSPACES={workspace}",
                f"FLUXION_DATA_DIR={data_dir}",
                "FLUXION_QQBOT_ALLOWED_USERS=old-user",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_file))

    adapter = _make_adapter(allowed=("old-user",))
    adapter._settings_reloader = SettingsHotReloader(channel="QQ")  # noqa: SLF001

    env_file.write_text(
        "\n".join(
            [
                f"FLUXION_WORKSPACE_ROOT={workspace}",
                f"FLUXION_ALLOWED_WORKSPACES={workspace}",
                f"FLUXION_DATA_DIR={data_dir}",
                "FLUXION_QQBOT_ALLOWED_USERS=user-1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(env_file, None)

    adapter._handle_event(_c2c_event(openid="user-1"))

    assert len(adapter._gateway.submitted) == 1  # noqa: SLF001
    assert adapter._settings.qqbot_allowed_users == {"user-1"}  # noqa: SLF001


def test_status_then_result_increment_msg_seq_and_clear_state():
    adapter = _make_adapter()
    adapter._handle_event(_c2c_event())
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001

    adapter.send_status(task.id, "DONE", ctx)
    result = ExecutionResult(success=True, summary="all done", stdout="", stderr="", exit_code=0)
    adapter.send_result(task.id, result, ctx)

    sends = adapter._client.calls  # noqa: SLF001
    assert len(sends) == 2
    # Both sends are passive replies to the same inbound msg_id, with increasing seq.
    assert sends[0][3] == "m-1" and sends[0][4] == 1
    assert sends[1][3] == "m-1" and sends[1][4] == 2
    # Terminal send clears per-task tracking.
    assert task.id not in adapter._reply_msg_ids  # noqa: SLF001
    assert task.id not in adapter._reply_msg_seen_at  # noqa: SLF001
    assert task.id not in adapter._msg_seq  # noqa: SLF001


def test_result_falls_back_to_active_send_when_passive_reply_is_too_old(monkeypatch):
    now = {"t": 10_000.0}
    monkeypatch.setattr("fluxion.channels.qqbot.adapter.time.monotonic", lambda: now["t"])

    adapter = _make_adapter()
    adapter._handle_event(_c2c_event())
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001
    now["t"] += PASSIVE_REPLY_MAX_AGE_SEC_C2C + 1

    result = ExecutionResult(success=True, summary="late result", stdout="", stderr="", exit_code=0)
    adapter.send_result(task.id, result, ctx)

    send = adapter._client.calls[-1]  # noqa: SLF001
    assert send[0] == "c2c"
    assert send[3] is None
    assert send[4] == 1
    assert task.id not in adapter._reply_msg_ids  # noqa: SLF001
    assert task.id not in adapter._reply_msg_seen_at  # noqa: SLF001


def test_group_result_uses_shorter_passive_reply_window(monkeypatch):
    now = {"t": 10_000.0}
    monkeypatch.setattr("fluxion.channels.qqbot.adapter.time.monotonic", lambda: now["t"])

    adapter = _make_adapter()
    adapter._handle_event(_group_event())
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001
    now["t"] += PASSIVE_REPLY_MAX_AGE_SEC_GROUP + 1

    result = ExecutionResult(
        success=True, summary="late group result", stdout="", stderr="", exit_code=0
    )
    adapter.send_result(task.id, result, ctx)

    send = adapter._client.calls[-1]  # noqa: SLF001
    assert send[0] == "group"
    assert send[3] is None


# ---------------------------------------------------------------------------
# Inbound images
# ---------------------------------------------------------------------------


def test_image_attachment_downloads_and_submits(tmp_path, monkeypatch):
    adapter = _make_adapter()
    adapter._settings.resolve_workspace = lambda raw: tmp_path  # noqa: SLF001

    downloaded: list[tuple[str, Path]] = []

    def fake_download(url, dest):  # noqa: ANN001
        downloaded.append((url, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = BytesIO()
        Image.new("RGB", (8, 8)).save(payload, format="JPEG")
        dest.write_bytes(payload.getvalue())

    monkeypatch.setattr(adapter, "_download_url", fake_download)

    ev = _c2c_event(
        content="",
        attachments=[
            {"url": "gchat.qpic.cn/pic.jpg", "content_type": "image/jpeg", "filename": "pic.jpg"}
        ],
    )
    adapter._handle_event(ev)

    assert len(downloaded) == 1
    url, _dest = downloaded[0]
    assert url == "https://gchat.qpic.cn/pic.jpg"  # scheme-less URL normalized
    task, _ctx = adapter._gateway.submitted[0]  # noqa: SLF001
    assert task.text == ""
    assert task.attachments == ()
    assert len(task.image_attachments) == 1
    assert task.image_attachments[0].media_type == "image/jpeg"


def test_non_image_attachment_is_preserved_for_the_executor(tmp_path, monkeypatch):
    adapter = _make_adapter()
    adapter._settings.resolve_workspace = lambda raw: tmp_path  # noqa: SLF001

    def fake_download(_url, dest):  # noqa: ANN001
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"audio")

    monkeypatch.setattr(adapter, "_download_url", fake_download)
    ev = _c2c_event(
        content="just text",
        attachments=[{"url": "x/voice.silk", "content_type": "audio/silk", "filename": "v.silk"}],
    )
    adapter._handle_event(ev)

    task, _ctx = adapter._gateway.submitted[0]  # noqa: SLF001
    assert task.text == "just text"
    assert len(task.attachments) == 1
    assert task.attachments[0].media_type == "audio/silk"
    assert ".fluxion_inbox" not in task.text


def test_image_message_skips_command_handling(tmp_path, monkeypatch):
    # A "/reset"-looking caption alongside an image must be treated as a task,
    # not a control command.
    adapter = _make_adapter()
    adapter._settings.resolve_workspace = lambda raw: tmp_path  # noqa: SLF001
    monkeypatch.setattr(adapter, "_download_url", lambda url, dest: dest.write_bytes(b"i"))

    called = {"cmd": False}

    def fake_cmd(*_args, **_kwargs):
        called["cmd"] = True
        return True

    monkeypatch.setattr(adapter._gateway, "handle_control_command", fake_cmd)  # noqa: SLF001
    ev = _c2c_event(
        content="/reset",
        attachments=[{"url": "h/p.png", "content_type": "image/png", "filename": "p.png"}],
    )
    adapter._handle_event(ev)

    assert called["cmd"] is False
    assert len(adapter._gateway.submitted) == 1  # noqa: SLF001


# ---------------------------------------------------------------------------
# Outbound native markdown
# ---------------------------------------------------------------------------


def test_markdown_for_qq_forces_hard_breaks_outside_code_fences():
    src = "line one\nline two\n\n- a\n- b\n```\ncode 1\ncode 2\n```\ntail"
    out = markdown_for_qq(src)
    lines = out.split("\n")
    # Plain text + list lines get two trailing spaces (hard break); blank lines
    # and fenced code lines are left untouched.
    assert lines[0] == "line one  "
    assert lines[1] == "line two  "
    assert lines[2] == ""  # paragraph break preserved
    assert lines[3] == "- a  " and lines[4] == "- b  "
    assert lines[5] == "```"
    assert lines[6] == "code 1" and lines[7] == "code 2"  # code preserved verbatim
    assert lines[8] == "```"
    assert lines[9] == "tail  "


def test_result_goes_out_as_markdown_with_breaks():
    adapter = _make_adapter()
    adapter._handle_event(_c2c_event())
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001

    result = ExecutionResult(
        success=True, summary="**Done**\nsecond line", stdout="", stderr="", exit_code=0
    )
    adapter.send_result(task.id, result, ctx)

    assert adapter._client.kinds[-1] == "markdown"  # noqa: SLF001
    content = adapter._client.calls[-1][2]  # noqa: SLF001
    # Markdown preserved (not flattened) and the soft break is now a hard break.
    assert "**Done**" in content
    assert "**Done**  \nsecond line" in content


def test_status_stays_plain_text():
    adapter = _make_adapter()
    adapter._handle_event(_c2c_event())
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001

    adapter.send_status(task.id, "DONE", ctx)
    assert adapter._client.kinds[-1] == "text"  # noqa: SLF001
