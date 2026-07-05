from __future__ import annotations

import sqlite3
from pathlib import Path

from fluxion.config.settings import Settings
from fluxion.core.session_manager import SessionManager
from fluxion.workspace.antigravity_trajectory import collect_antigravity_trajectory
from fluxion.workspace.change_set import load_change_set


def _write_trajectory_db(path: Path, payloads: list[bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE steps (
                idx INTEGER PRIMARY KEY,
                step_type INTEGER NOT NULL DEFAULT 0,
                status INTEGER NOT NULL DEFAULT 0,
                has_subtrajectory NUMERIC NOT NULL DEFAULT false,
                metadata BLOB,
                error_details BLOB,
                permissions BLOB,
                task_details BLOB,
                render_info BLOB,
                step_payload BLOB,
                step_format INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        for idx, payload in enumerate(payloads):
            conn.execute(
                "INSERT INTO steps (idx, step_payload) VALUES (?, ?)",
                (idx, payload),
            )
        conn.commit()
    finally:
        conn.close()


def test_collect_antigravity_trajectory_extracts_created_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversations = tmp_path / "conversations"
    db = conversations / "session-1.db"
    _write_trajectory_db(
        db,
        [
            b'noise {"TargetFile":"'
            + str(workspace / "bar.txt").encode("utf-8")
            + b'","CodeContent":"created by agy\\n","Overwrite":true,"toolAction":"Creating bar.txt"}',
        ],
    )

    capture = collect_antigravity_trajectory(
        session_id="session-1",
        workspace=workspace,
        data_dir=tmp_path,
        run_id="run-1",
        status="RETURNED",
        revert_capture="structured",
        conversation_dirs=(conversations,),
    )

    assert capture.changed_files == ["bar.txt"]
    assert capture.risk_flags == []
    assert capture.change_set_file

    change_set = load_change_set(tmp_path, "run-1")
    assert change_set is not None
    assert change_set.changed_files == ["bar.txt"]
    assert change_set.recoverable_files == ["bar.txt"]
    assert change_set.unrecoverable_files == []
    assert change_set.files[0].change_type == "added"


def test_collect_antigravity_trajectory_keeps_generic_write_as_created_file(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversations = tmp_path / "conversations"
    db = conversations / "session-generic-write.db"
    _write_trajectory_db(
        db,
        [
            b'noise {"TargetFile":"'
            + str(workspace / "bar.txt").encode("utf-8")
            + b'","CodeContent":"created by agy\\n","Overwrite":true,'
            + b'"toolAction":"Write file","toolSummary":"Write to file"}',
        ],
    )

    capture = collect_antigravity_trajectory(
        session_id="session-generic-write",
        workspace=workspace,
        data_dir=tmp_path,
        run_id="run-generic-write",
        status="RETURNED",
        revert_capture="structured",
        conversation_dirs=(conversations,),
    )

    assert capture.changed_files == ["bar.txt"]
    assert capture.risk_flags == []

    change_set = load_change_set(tmp_path, "run-generic-write")
    assert change_set is not None
    assert change_set.changed_files == ["bar.txt"]
    assert change_set.recoverable_files == ["bar.txt"]
    assert change_set.unrecoverable_files == []
    assert change_set.files[0].change_type == "added"


def test_collect_antigravity_trajectory_extracts_modified_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversations = tmp_path / "conversations"
    db = conversations / "session-2.db"
    _write_trajectory_db(
        db,
        [
            b'noise {"TargetFile":"'
            + str(workspace / "foo.txt").encode("utf-8")
            + b'","CodeContent":"line1\\nline2\\nhello from agy\\n","toolAction":"Modifying foo.txt","toolSummary":"Updated existing file content"}',
        ],
    )

    capture = collect_antigravity_trajectory(
        session_id="session-2",
        workspace=workspace,
        data_dir=tmp_path,
        run_id="run-2",
        status="RETURNED",
        revert_capture="structured",
        conversation_dirs=(conversations,),
    )

    assert capture.changed_files == ["foo.txt"]
    assert capture.risk_flags == []
    assert capture.change_set_file

    change_set = load_change_set(tmp_path, "run-2")
    assert change_set is not None
    assert change_set.changed_files == ["foo.txt"]
    assert change_set.recoverable_files == []
    assert change_set.unrecoverable_files == ["foo.txt"]


def test_collect_antigravity_trajectory_extracts_deleted_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversations = tmp_path / "conversations"
    db = conversations / "session-3.db"
    _write_trajectory_db(
        db,
        [
            b'noise {"TargetFile":"'
            + str(workspace / "gone.txt").encode("utf-8")
            + b'","TargetContent":"remove me\\n","toolAction":"Delete gone.txt"}',
        ],
    )

    capture = collect_antigravity_trajectory(
        session_id="session-3",
        workspace=workspace,
        data_dir=tmp_path,
        run_id="run-3",
        status="RETURNED",
        revert_capture="structured",
        conversation_dirs=(conversations,),
    )

    assert capture.changed_files == ["gone.txt"]
    assert capture.risk_flags == []
    assert capture.change_set_file

    change_set = load_change_set(tmp_path, "run-3")
    assert change_set is not None
    assert change_set.changed_files == ["gone.txt"]
    assert change_set.recoverable_files == ["gone.txt"]
    assert change_set.unrecoverable_files == []


def test_settings_defaults_disable_snapshot_detection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLUXION_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FLUXION_ENV_FILE", str(tmp_path / ".env"))

    settings = Settings.load()

    assert settings.change_detection == "off"
    assert settings.revert_capture == "structured"


def test_collect_antigravity_trajectory_dedupes_shell_risk_when_structured_write_covers_it(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversations = tmp_path / "conversations"
    db = conversations / "session-4.db"
    _write_trajectory_db(
        db,
        [
            b'noise {"TargetFile":"'
            + str(workspace / "hello.txt").encode("utf-8")
            + b'","CodeContent":"hello from agy","Overwrite":true,"toolAction":"Creating hello.txt"}',
            b'noise {"CommandLine":"printf \'hello from agy\' > hello.txt","Cwd":"'
            + str(workspace).encode("utf-8")
            + b'","toolAction":"Writing hello.txt exactly","toolSummary":"Overwriting file with exact content"}',
        ],
    )

    capture = collect_antigravity_trajectory(
        session_id="session-4",
        workspace=workspace,
        data_dir=tmp_path,
        run_id="run-4",
        status="RETURNED",
        revert_capture="structured",
        conversation_dirs=(conversations,),
    )

    assert capture.changed_files == ["hello.txt"]
    assert capture.risk_flags == []


def test_collect_antigravity_trajectory_uses_cwd_for_shell_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    subdir = workspace / "subdir"
    subdir.mkdir(parents=True)
    conversations = tmp_path / "conversations"
    db = conversations / "session-cwd.db"
    _write_trajectory_db(
        db,
        [
            b'noise {"TargetFile":"'
            + str(subdir / "hello.txt").encode("utf-8")
            + b'","CodeContent":"hello from agy","Overwrite":true,'
            + b'"toolAction":"Creating hello.txt"}',
            b'noise {"CommandLine":"printf \'hello from agy\' > hello.txt","Cwd":"'
            + str(subdir).encode("utf-8")
            + b'","toolAction":"Writing hello.txt exactly",'
            + b'"toolSummary":"Overwriting file with exact content"}',
        ],
    )

    capture = collect_antigravity_trajectory(
        session_id="session-cwd",
        workspace=workspace,
        data_dir=tmp_path,
        run_id="run-cwd",
        status="RETURNED",
        revert_capture="structured",
        conversation_dirs=(conversations,),
    )

    assert capture.changed_files == ["subdir/hello.txt"]
    assert capture.risk_flags == []


# ── P0#2 Lever 2: since_idx scopes a resumed session to the current run ──
def _create_payload(workspace: Path, name: str) -> bytes:
    return (
        b'{"TargetFile":"'
        + str(workspace / name).encode("utf-8")
        + b'","CodeContent":"x","Overwrite":true,"toolAction":"Creating '
        + name.encode("utf-8")
        + b'"}'
    )


def _collect_since(tmp_path: Path, since_idx: int):
    # Two steps at 0-based idx 0 (a.txt) and 1 (b.txt).
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    conversations = tmp_path / "conversations"
    _write_trajectory_db(
        conversations / "sess.db",
        [_create_payload(workspace, "a.txt"), _create_payload(workspace, "b.txt")],
    )
    return collect_antigravity_trajectory(
        session_id="sess",
        workspace=workspace,
        data_dir=tmp_path,
        run_id="r",
        status="RETURNED",
        revert_capture="off",
        since_idx=since_idx,
        conversation_dirs=(conversations,),
    )


def test_since_idx_default_reads_all_steps(tmp_path: Path) -> None:
    cap = _collect_since(tmp_path, -1)
    assert set(cap.changed_files) == {"a.txt", "b.txt"}
    assert cap.max_step_idx == 1


def test_since_idx_scopes_to_newer_steps(tmp_path: Path) -> None:
    cap = _collect_since(tmp_path, 0)  # idx 0 already counted last run
    assert cap.changed_files == ["b.txt"]
    assert cap.max_step_idx == 1


def test_since_idx_at_head_returns_nothing_but_keeps_mark(tmp_path: Path) -> None:
    cap = _collect_since(tmp_path, 1)
    assert cap.changed_files == []
    assert cap.max_step_idx == 1


def test_missing_db_preserves_since_idx(tmp_path: Path) -> None:
    cap = collect_antigravity_trajectory(
        session_id="nope",
        workspace=tmp_path,
        data_dir=tmp_path,
        run_id="r",
        status="RETURNED",
        since_idx=5,
        conversation_dirs=(tmp_path,),
    )
    assert cap.changed_files == []
    assert cap.max_step_idx == 5


def test_session_manager_trajectory_idx_roundtrip() -> None:
    sm = SessionManager()  # storage=None → in-memory, no persistence side effects
    kw = {"conversation_key": "k", "channel": "local", "user_id": "u"}
    assert sm.get_trajectory_idx(session_id="s1", **kw) == -1
    sm.set_trajectory_idx(session_id="s1", idx=4, **kw)
    assert sm.get_trajectory_idx(session_id="s1", **kw) == 4
    assert sm.get_trajectory_idx(session_id="s2", **kw) == -1
