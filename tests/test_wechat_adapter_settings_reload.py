from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from fluxion.channels.wechat.adapter import WeChatChannelAdapter
from fluxion.channels.wechat.models import Credentials


def _settings(data_dir: Path, allowed_users: set[str]):
    return SimpleNamespace(
        data_dir=data_dir,
        wechat_allowed_users=allowed_users,
        allowed_workspaces=[],
        status_updates={"RUNNING", "FAILED", "CANCELED"},
    )


class _FakeThread:
    started = False
    target = None

    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon
        _FakeThread.target = target

    def start(self):
        _FakeThread.started = True


def test_wechat_adapter_reloads_settings_when_env_file_changes(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"FLUXION_WORKSPACE_ROOT={tmp_path}",
                f"FLUXION_DATA_DIR={tmp_path / 'data'}",
                "FLUXION_WECHAT_ALLOWED_USERS=old-user",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_file))

    adapter = WeChatChannelAdapter(_settings(tmp_path / "data", {"old-user"}))  # type: ignore[arg-type]
    adapter._last_env_mtime = 0.0

    env_file.write_text(
        "\n".join(
            [
                f"FLUXION_WORKSPACE_ROOT={tmp_path}",
                f"FLUXION_DATA_DIR={tmp_path / 'data'}",
                "FLUXION_WECHAT_ALLOWED_USERS=new-user",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(env_file, None)

    adapter._check_and_reload_settings()

    assert adapter._settings.wechat_allowed_users == {"new-user"}


def test_wechat_allowlist_rejection_mentions_user_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fluxion.channels.allowlist_messages.platform.system", lambda: "Darwin")
    adapter = WeChatChannelAdapter(_settings(tmp_path, set()))  # type: ignore[arg-type]

    text = adapter._format_rejection("user is not in allowlist.", "u1@im.wechat")

    assert "FLUXION_WECHAT_ALLOWED_USERS" in text
    assert "u1@im.wechat" in text
    assert "Messaging -> WeChat -> Pending Users" in text
    assert "Preferences" in text
    assert "automatically" in text


def test_wechat_allowlist_rejection_omits_macos_hint_off_macos(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fluxion.channels.allowlist_messages.platform.system", lambda: "Linux")
    adapter = WeChatChannelAdapter(_settings(tmp_path, set()))  # type: ignore[arg-type]

    text = adapter._format_rejection("user is not in allowlist.", "u1@im.wechat")

    assert "macOS app" not in text
    assert "FLUXION_WECHAT_ALLOWED_USERS" in text
    assert "automatically" in text


def test_wechat_settings_reload_updates_pending_store_data_dir(tmp_path: Path, monkeypatch) -> None:
    old_data = tmp_path / "old-data"
    new_data = tmp_path / "new-data"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"FLUXION_WORKSPACE_ROOT={tmp_path}",
                f"FLUXION_DATA_DIR={new_data}",
                "FLUXION_WECHAT_ALLOWED_USERS=user-a",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_file))

    adapter = WeChatChannelAdapter(_settings(old_data, set()))  # type: ignore[arg-type]
    adapter._last_env_mtime = 0.0

    adapter._check_and_reload_settings()

    assert adapter._pending_store.path.parent == new_data


def test_wechat_start_waits_when_credentials_are_missing(tmp_path: Path, monkeypatch) -> None:
    _FakeThread.started = False
    _FakeThread.target = None
    monkeypatch.setattr("fluxion.channels.wechat.adapter.threading.Thread", _FakeThread)

    adapter = WeChatChannelAdapter(_settings(tmp_path, set()))  # type: ignore[arg-type]

    adapter.start(gateway=object())  # type: ignore[arg-type]

    assert _FakeThread.started is True
    assert _FakeThread.target == adapter._poll_loop
    assert adapter._credentials_ready is False


def test_wechat_session_timeout_waits_for_new_credentials(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    adapter = WeChatChannelAdapter(_settings(data_dir, {"u1"}))  # type: ignore[arg-type]
    adapter._cred_store.save(
        Credentials(
            bot_token="old-token",
            ilink_bot_id="bot-id",
            baseurl="https://ilink.example",
        )
    )
    adapter._check_and_reload_credentials()
    old_mtime = adapter._last_cred_mtime

    adapter._mark_credentials_expired("getupdates failed with errcode=-14, errmsg=session timeout")
    adapter._check_and_reload_credentials()

    assert adapter._credentials_ready is False
    assert adapter._last_cred_mtime == old_mtime

    adapter._cred_store.save(
        Credentials(
            bot_token="new-token",
            ilink_bot_id="bot-id",
            baseurl="https://ilink.example",
        )
    )
    os.utime(adapter._cred_store.path, (old_mtime + 10, old_mtime + 10))

    adapter._check_and_reload_credentials()

    assert adapter._credentials_ready is True
    assert adapter._last_cred_mtime > old_mtime


def test_wechat_default_workspace_prefers_first_allowed_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    settings = _settings(tmp_path / "data", {"u1"})
    settings.allowed_workspaces = [workspace]
    adapter = WeChatChannelAdapter(settings)  # type: ignore[arg-type]

    assert adapter._default_workspace() == str(workspace)
