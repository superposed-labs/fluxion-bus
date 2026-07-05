from __future__ import annotations

from pathlib import Path

from fluxion.config.settings import Settings


def test_default_channel_workspace_prefers_first_allowed_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"FLUXION_WORKSPACE_ROOT={tmp_path / 'install'}",
                f"FLUXION_ALLOWED_WORKSPACES={workspace}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_path))
    monkeypatch.setenv("FLUXION_WORKSPACE_ROOT", str(tmp_path / "install"))
    monkeypatch.setenv("FLUXION_ALLOWED_WORKSPACES", str(workspace))

    settings = Settings.load()

    assert settings.default_channel_workspace() == str(workspace)


def test_default_channel_workspace_uses_workspace_root_default(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"FLUXION_WORKSPACE_ROOT={tmp_path}\nFLUXION_ALLOWED_WORKSPACES=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_path))
    monkeypatch.setenv("FLUXION_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLUXION_ALLOWED_WORKSPACES", "")

    settings = Settings.load()

    assert settings.default_channel_workspace() == str(tmp_path)


def test_slack_event_workspace_defaults_to_first_allowed_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    install = tmp_path / "install"
    workspace = tmp_path / "workspace"
    install.mkdir()
    workspace.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"FLUXION_WORKSPACE_ROOT={install}",
                f"FLUXION_ALLOWED_WORKSPACES={workspace}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_path))
    monkeypatch.setenv("FLUXION_WORKSPACE_ROOT", str(install))
    monkeypatch.setenv("FLUXION_ALLOWED_WORKSPACES", str(workspace))

    settings = Settings.load()

    assert (
        settings.resolve_workspace_for_event(
            channel_id="D123",
            channel_type="im",
            raw_workspace=None,
        )
        == workspace
    )


def test_autoping_workspace_is_always_authorized(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"FLUXION_WORKSPACE_ROOT={tmp_path / 'install'}",
                f"FLUXION_ALLOWED_WORKSPACES={tmp_path / 'workspace'}",
                f"FLUXION_DATA_DIR={tmp_path / 'data'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_path))
    monkeypatch.setenv("FLUXION_WORKSPACE_ROOT", str(tmp_path / "install"))
    monkeypatch.setenv("FLUXION_ALLOWED_WORKSPACES", str(tmp_path / "workspace"))
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path / "data"))

    settings = Settings.load()

    autoping_dir = tmp_path / "data" / "autoping_workspace"
    autoping_dir.mkdir(parents=True, exist_ok=True)

    auth = settings.authorize_run_workspace(raw_workspace=str(autoping_dir))
    assert auth.allowed is True
    assert auth.policy == "autoping"
