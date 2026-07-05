from pathlib import Path

from fluxion.workspace.snapshot import diff_snapshot, take_snapshot


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_take_snapshot_includes_normal_files(tmp_path):
    _write(tmp_path / "a.txt")
    _write(tmp_path / "src" / "b.py")
    snap = take_snapshot(tmp_path)
    assert set(snap.keys()) == {"a.txt", "src/b.py"}


def test_take_snapshot_excludes_ignored_dirs(tmp_path):
    _write(tmp_path / "keep.txt")
    _write(tmp_path / "node_modules" / "pkg" / "index.js")
    _write(tmp_path / ".git" / "config")
    _write(tmp_path / ".venv" / "bin" / "python")
    _write(tmp_path / "src" / "__pycache__" / "m.pyc")
    snap = take_snapshot(tmp_path)
    assert set(snap.keys()) == {"keep.txt"}


def test_take_snapshot_excludes_ds_store(tmp_path):
    _write(tmp_path / "keep.txt")
    _write(tmp_path / ".DS_Store")
    _write(tmp_path / "sub" / ".DS_Store")
    snap = take_snapshot(tmp_path)
    assert set(snap.keys()) == {"keep.txt"}


def test_take_snapshot_does_not_descend_into_ignored_dirs(tmp_path):
    # Many files under an ignored dir must not appear (and must not be walked).
    _write(tmp_path / "real.txt")
    for i in range(50):
        _write(tmp_path / "node_modules" / f"f{i}.js")
    snap = take_snapshot(tmp_path)
    assert set(snap.keys()) == {"real.txt"}


def test_take_snapshot_missing_root_returns_empty(tmp_path):
    assert take_snapshot(tmp_path / "does-not-exist") == {}


def test_diff_snapshot_detects_add_modify_delete(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    _write(a, "one")
    _write(b, "two")
    before = take_snapshot(tmp_path)

    a.write_text("changed-and-longer", encoding="utf-8")  # modify (size differs)
    b.unlink()  # delete
    _write(tmp_path / "c.txt")  # add

    after = take_snapshot(tmp_path)
    delta = diff_snapshot(before, after)
    assert delta.added == ["c.txt"]
    assert delta.deleted == ["b.txt"]
    assert delta.modified == ["a.txt"]
    assert set(delta.changed) == {"a.txt", "c.txt"}
