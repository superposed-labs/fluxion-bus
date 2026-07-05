from pathlib import Path

from fluxion.workspace.change_set import take_content_snapshot


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_content_snapshot_captures_normal_files(tmp_path):
    _write(tmp_path / "a.txt", "hello")
    _write(tmp_path / "src" / "b.py", "print(1)")
    snap = take_content_snapshot(tmp_path)
    assert set(snap.files.keys()) == {"a.txt", "src/b.py"}
    assert snap.files["a.txt"].content == "hello"
    assert snap.files["a.txt"].text is True


def test_content_snapshot_excludes_and_does_not_descend_ignored_dirs(tmp_path):
    _write(tmp_path / "keep.txt", "x")
    _write(tmp_path / ".git" / "config", "x")
    _write(tmp_path / ".venv" / "bin" / "python", "x")
    for i in range(40):
        _write(tmp_path / "node_modules" / f"f{i}.js", "x")
    snap = take_content_snapshot(tmp_path)
    assert set(snap.files.keys()) == {"keep.txt"}


def test_content_snapshot_excludes_ds_store(tmp_path):
    _write(tmp_path / "keep.txt", "x")
    _write(tmp_path / ".DS_Store", "x")
    snap = take_content_snapshot(tmp_path)
    assert set(snap.files.keys()) == {"keep.txt"}


def test_content_snapshot_respects_total_cap_in_sorted_order(tmp_path):
    # Sorted iteration order must be preserved so the byte cap truncates the
    # later files, not arbitrary ones.
    _write(tmp_path / "a.txt", "A" * 100)
    _write(tmp_path / "b.txt", "B" * 100)
    snap = take_content_snapshot(tmp_path, max_file_bytes=1000, max_total_bytes=150)
    assert snap.truncated is True
    # a.txt sorts first -> captured; b.txt pushes over the cap -> content dropped.
    assert snap.files["a.txt"].content == "A" * 100
    assert snap.files["b.txt"].content is None
    assert "max_total_bytes" in snap.files["b.txt"].skipped_reason
