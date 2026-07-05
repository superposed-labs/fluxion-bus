from __future__ import annotations

from pathlib import Path

from fluxion.workspace.antigravity_trajectory import _StructuredChange, _trajectory_file_change
from fluxion.workspace.change_set import (
    ChangeSet,
    revert_change_set,
    save_change_set,
)


def _change_set(tmp_path: Path, *changes) -> None:
    cs = ChangeSet(
        run_id="r1",
        workspace=str(tmp_path),
        status="ok",
        created_at="now",
        changed_files=[c.path for c in changes],
        recoverable_files=[c.path for c in changes if c.recoverable],
        unrecoverable_files=[c.path for c in changes if not c.recoverable],
        files=list(changes),
    )
    save_change_set(tmp_path / "data", cs)


def test_trajectory_added_file_populates_sha_and_reverts(tmp_path: Path) -> None:
    target = tmp_path / "created.txt"
    target.write_text("agy revert check", encoding="utf-8")
    change = _StructuredChange(
        path="created.txt",
        change_type="added",
        new_content="agy revert check",
        recoverable=True,
    )

    fc = _trajectory_file_change(change, tmp_path)
    # The bug: these were left empty, so the conflict check always blocked revert.
    assert fc.new_sha256 != ""
    assert fc.new_size == len("agy revert check")

    _change_set(tmp_path, fc)
    result = revert_change_set(tmp_path / "data", "r1")

    assert result.success is True
    assert not target.exists()


def test_trajectory_added_new_sha_tracks_disk_not_trajectory(tmp_path: Path) -> None:
    # When the finalized file differs from the trajectory's captured content,
    # the hash follows the real on-disk bytes so the conflict guard is accurate.
    target = tmp_path / "created.txt"
    target.write_text("on disk bytes", encoding="utf-8")
    change = _StructuredChange(
        path="created.txt",
        change_type="added",
        new_content="stale trajectory content",
        recoverable=True,
    )

    fc = _trajectory_file_change(change, tmp_path)

    import hashlib

    assert fc.new_sha256 == hashlib.sha256(b"on disk bytes").hexdigest()


def test_trajectory_modified_reverts_to_old_content(tmp_path: Path) -> None:
    target = tmp_path / "edited.txt"
    target.write_text("new body", encoding="utf-8")
    change = _StructuredChange(
        path="edited.txt",
        change_type="modified",
        old_content="old body",
        new_content="new body",
        recoverable=True,
    )

    fc = _trajectory_file_change(change, tmp_path)
    assert fc.old_sha256 != "" and fc.new_sha256 != ""

    _change_set(tmp_path, fc)
    result = revert_change_set(tmp_path / "data", "r1")

    assert result.success is True
    assert target.read_text(encoding="utf-8") == "old body"
