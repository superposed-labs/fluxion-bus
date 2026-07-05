from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fluxion.availability import Availability
from fluxion.cli.main import main


def test_init_writes_minimal_real_paths(tmp_path: Path, capsys) -> None:
    env_path = tmp_path / ".env"
    workspace = tmp_path / "project"
    workspace.mkdir()

    with patch(
        "fluxion.cli.main.detect_executor",
        side_effect=lambda provider: Availability(
            status="available" if provider == "claude" else "unavailable",
            detail="test",
        ),
    ):
        result = main(
            [
                "init",
                "--env-file",
                str(env_path),
                "--workspace",
                str(workspace),
            ]
        )

    assert result == 0
    text = env_path.read_text(encoding="utf-8")
    assert f"FLUXION_WORKSPACE_ROOT={tmp_path}" in text
    assert f"FLUXION_ALLOWED_WORKSPACES={workspace}" in text
    assert "FLUXION_DEFAULT_EXECUTOR=claude" in text
    assert "FLUXION_SCHEDULER_ENABLED=true" in text
    assert "FLUXION_MENU_AUTOSTART_SCHEDULER=true" in text
    assert "/absolute/path" not in text
    output = capsys.readouterr().out
    assert f"fluxion doctor --workspace {workspace}" in output
    assert f"fluxion run --workspace {workspace}" in output


def test_init_does_not_replace_existing_config_without_force(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("KEEP=true\n", encoding="utf-8")

    result = main(["init", "--env-file", str(env_path)])

    assert result == 2
    assert env_path.read_text(encoding="utf-8") == "KEEP=true\n"


def test_run_defaults_to_read_only_current_workspace(tmp_path: Path) -> None:
    with patch("fluxion.cli.main.subagent_main", return_value=0) as subagent_main:
        result = main(["run", "--workspace", str(tmp_path), "Inspect", "README"])

    assert result == 0
    forwarded = subagent_main.call_args.args[0]
    assert forwarded[-2:] == ["Inspect", "README"]
    assert forwarded[forwarded.index("--profile") + 1] == "inspect"
    assert forwarded[forwarded.index("--mode") + 1] == "read-only"
    assert forwarded[forwarded.index("--workspace") + 1] == str(tmp_path.resolve())


def test_run_write_uses_implementation_profile(tmp_path: Path) -> None:
    with patch("fluxion.cli.main.subagent_main", return_value=0) as subagent_main:
        result = main(["run", "--write", "--workspace", str(tmp_path), "Fix", "tests"])

    assert result == 0
    forwarded = subagent_main.call_args.args[0]
    assert forwarded[forwarded.index("--profile") + 1] == "implement"
    assert forwarded[forwarded.index("--mode") + 1] == "workspace-write"


def test_project_run_keeps_default_workspace_relative() -> None:
    with patch("fluxion.cli.main.subagent_main", return_value=0) as subagent_main:
        result = main(["run", "--project", "web", "Inspect"])

    assert result == 0
    forwarded = subagent_main.call_args.args[0]
    assert forwarded[forwarded.index("--workspace") + 1] == "."
