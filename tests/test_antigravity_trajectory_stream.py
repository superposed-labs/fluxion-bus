"""Live working notes read from agy's trajectory mid-run.

Payload shapes are copied from a real `agy` conversation DB: protobuf noise
wrapped around a JSON object, with the tool's proposal row and its result row
carrying byte-identical JSON.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fluxion.executors.antigravity.trajectory_stream import (
    TrajectoryNarrator,
    describe_step,
    read_max_step_idx,
)

VIEW_A = (
    b'\x08\x0f \x03*\x02\n{"AbsolutePath":"/repo/a.txt","toolAction":"Reading a.txt",'
    b'"toolSummary":"View file"}'
)
VIEW_B = b'\x08\x0f{"AbsolutePath":"/repo/b.txt","toolAction":"Reading b.txt"}'
RUN_WC = (
    b'\x08\x15{"CommandLine":"wc -l *.txt","Cwd":"/repo","toolAction":"Run wc command",'
    b'"toolSummary":"Count lines"}'
)
# Model text steps share a step_type with tool proposals and carry no tool keys.
PROSE = b"\x08\x0f\n\x0bb.txt has the most lines."


def write_db(path: Path, payloads: list[bytes], *, first_idx: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE steps (
                idx INTEGER PRIMARY KEY,
                step_type INTEGER NOT NULL DEFAULT 0,
                step_payload BLOB
            )
            """
        )
        for offset, payload in enumerate(payloads):
            conn.execute(
                "INSERT INTO steps (idx, step_payload) VALUES (?, ?)",
                (first_idx + offset, payload),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def append_steps(path: Path, payloads: list[bytes]) -> None:
    conn = sqlite3.connect(path)
    try:
        start = conn.execute("SELECT MAX(idx) FROM steps").fetchone()[0]
        for offset, payload in enumerate(payloads, start=1):
            conn.execute(
                "INSERT INTO steps (idx, step_payload) VALUES (?, ?)",
                (start + offset, payload),
            )
        conn.commit()
    finally:
        conn.close()


# ── rendering ────────────────────────────────────────────────────────
def test_tool_action_is_used_verbatim():
    """agy already phrases these for display; rewriting them adds nothing."""
    assert describe_step({"toolAction": "Reading a.txt"}) == "Reading a.txt"


def test_command_beats_its_summary():
    """`Run wc command` hides the thing worth seeing."""
    rendered = describe_step({"CommandLine": "wc -l *.txt", "toolAction": "Run wc command"})
    assert rendered == "$ wc -l *.txt"


def test_steps_without_tool_keys_render_nothing():
    assert describe_step({"someOtherKey": 1}) == ""
    assert describe_step({}) == ""


def test_long_commands_are_clipped():
    rendered = describe_step({"CommandLine": "x" * 200})
    assert len(rendered) < 100
    assert rendered.endswith("...")


def test_multiline_commands_stay_on_one_line():
    assert describe_step({"CommandLine": "a\nb"}) == "$ a b"


# ── polling ──────────────────────────────────────────────────────────
def test_new_steps_are_reported_once(tmp_path):
    db = write_db(tmp_path / "s.db", [VIEW_A])
    narrator = TrajectoryNarrator()

    assert narrator.poll(db) == "Reading a.txt"
    assert narrator.poll(db) == "", "a second poll must not replay the same row"


def test_only_the_new_rows_are_sent(tmp_path):
    db = write_db(tmp_path / "s.db", [VIEW_A])
    narrator = TrajectoryNarrator()
    narrator.poll(db)

    append_steps(db, [RUN_WC])
    assert narrator.poll(db) == "\n\n$ wc -l *.txt"


def test_the_proposal_and_its_result_are_one_note(tmp_path):
    """Every tool lands twice, carrying the same JSON both times."""
    db = write_db(tmp_path / "s.db", [VIEW_A, VIEW_A])
    assert TrajectoryNarrator().poll(db) == "Reading a.txt"


def test_interleaved_parallel_tools_are_still_deduped(tmp_path):
    """Proposal A, proposal B, result A, result B — the pairs are not adjacent."""
    db = write_db(tmp_path / "s.db", [VIEW_A, VIEW_B, VIEW_A, VIEW_B])
    assert TrajectoryNarrator().poll(db) == "Reading a.txt\n\nReading b.txt"


def test_a_genuine_repeat_much_later_stays_visible(tmp_path):
    """Re-reading a file after other work is real work, not a duplicate row.

    The suppression window holds distinct notes, so "much later" means past
    enough other activity — not past enough rows.
    """
    db = write_db(tmp_path / "s.db", [VIEW_A])
    narrator = TrajectoryNarrator()
    narrator.poll(db)

    append_steps(db, [b'{"CommandLine":"step %d"}' % n for n in range(10)])
    narrator.poll(db)
    append_steps(db, [VIEW_A])
    assert "Reading a.txt" in narrator.poll(db)


def test_prose_steps_are_not_narrated(tmp_path):
    """The answer travels on the other channel; repeating it here doubles it."""
    db = write_db(tmp_path / "s.db", [PROSE])
    assert TrajectoryNarrator().poll(db) == ""


def test_a_resumed_conversation_starts_after_the_prior_turn(tmp_path):
    """Without a floor, the whole accumulated DB replays as this turn's working."""
    db = write_db(tmp_path / "s.db", [VIEW_A, RUN_WC])
    narrator = TrajectoryNarrator(since_idx=read_max_step_idx(db))

    assert narrator.poll(db) == ""
    append_steps(db, [VIEW_B])
    assert narrator.poll(db) == "Reading b.txt"


def test_a_missing_database_is_not_an_error(tmp_path):
    assert TrajectoryNarrator().poll(tmp_path / "absent.db") == ""
    assert read_max_step_idx(tmp_path / "absent.db") == -1


def test_a_database_without_the_table_is_not_an_error(tmp_path):
    """agy creates the file before the schema; polls land in that window."""
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    assert TrajectoryNarrator().poll(path) == ""
    assert read_max_step_idx(path) == -1


def test_an_empty_table_reads_as_no_floor(tmp_path):
    db = write_db(tmp_path / "s.db", [])
    assert read_max_step_idx(db) == -1


def test_the_high_water_mark_advances_past_unrenderable_rows(tmp_path):
    """Otherwise every poll rescans the 40KB prompt blob agy writes as row 0."""
    db = write_db(tmp_path / "s.db", [PROSE, PROSE])
    narrator = TrajectoryNarrator()
    narrator.poll(db)
    assert narrator.since_idx == 1
