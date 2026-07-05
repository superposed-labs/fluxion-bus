from pathlib import Path

from fluxion.channels.artifacts import channel_artifact_paths, upload_channel_artifacts
from fluxion.core.models.result import ExecutionResult
from fluxion.workspace.artifact_collector import collect_artifacts
from fluxion.workspace.snapshot import WorkspaceDelta, take_snapshot


def test_scratch_files_are_excluded_from_snapshot_and_artifacts(tmp_path: Path) -> None:
    scratch_file = tmp_path / "scratch" / "crop_clock.png"
    scratch_file.parent.mkdir()
    scratch_file.write_bytes(b"temporary crop")

    assert take_snapshot(tmp_path) == {}
    assert (
        collect_artifacts(
            workspace=tmp_path,
            delta=WorkspaceDelta(added=["scratch/crop_clock.png"], modified=[], deleted=[]),
            max_files=5,
        )
        == []
    )


def test_channel_artifacts_fall_back_to_uploadable_changed_files(tmp_path: Path) -> None:
    output = tmp_path / "temp_test.json"
    output.write_text('{"ok": true}', encoding="utf-8")
    source = tmp_path / "main.py"
    source.write_text("print('skip source')", encoding="utf-8")

    result = ExecutionResult(
        success=True,
        summary="done",
        stdout="",
        stderr="",
        exit_code=0,
        changed_files=["temp_test.json", "main.py"],
    )

    assert channel_artifact_paths(
        result=result,
        context={"workspace": str(tmp_path)},
        max_files=5,
    ) == [output.resolve()]


def test_channel_artifacts_prefer_explicit_uploads(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.txt"
    explicit.write_text("explicit", encoding="utf-8")
    changed = tmp_path / "changed.json"
    changed.write_text("{}", encoding="utf-8")

    result = ExecutionResult(
        success=True,
        summary="done",
        stdout="",
        stderr="",
        exit_code=0,
        artifacts=[str(explicit)],
        changed_files=["changed.json"],
    )

    assert channel_artifact_paths(
        result=result,
        context={"workspace": str(tmp_path)},
        max_files=5,
    ) == [explicit.resolve()]


def test_upload_channel_artifacts_handles_fallback_dedupe_and_failure_log(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.json"
    output.write_text("{}", encoding="utf-8")
    log_file = tmp_path / "task.log"
    log_file.write_text("failed", encoding="utf-8")
    uploaded: list[Path] = []

    result = ExecutionResult(
        success=False,
        summary="failed",
        stdout="",
        stderr="",
        exit_code=1,
        changed_files=["result.json", "result.json"],
        log_file=str(log_file),
    )

    upload_channel_artifacts(
        result=result,
        context={"workspace": str(tmp_path)},
        max_files=5,
        upload_log_on_success=False,
        upload_one=uploaded.append,
    )

    assert uploaded == [output.resolve(), log_file]
