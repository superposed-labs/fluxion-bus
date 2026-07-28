"""ACTIONS_JSON upload declarations, and keeping runtime logs out of stdout."""

from __future__ import annotations

import json
from pathlib import Path

from fluxion.executors.common.actions import (
    extract_actions_json,
    resolve_uploads_from_text,
    upload_paths,
)
from fluxion.executors.common.log_writer import write_jsonl_log
from fluxion.mcp_server.logs import _output_view
from fluxion.web.services.log_parser import load_task_logs, load_task_streams

_GLOG = (
    "I0613 11:46:53.143622 11683 http_helpers.go:186] "
    "URL: https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent"
)


def _answer(upload: list[str]) -> str:
    return "FINAL_ANSWER:\nDone.\nACTIONS_JSON:\n" + json.dumps({"upload_files": upload}) + "\n"


def test_declared_uploads_are_extracted() -> None:
    assert upload_paths(extract_actions_json(_answer(["shot.png"]))) == ["shot.png"]


def test_a_fenced_actions_block_is_still_parsed() -> None:
    text = 'FINAL_ANSWER:\nok\nACTIONS_JSON:\n```json\n{"upload_files": ["a.png"]}\n```\n'
    assert upload_paths(extract_actions_json(text)) == ["a.png"]


def test_duplicate_and_object_form_paths_collapse() -> None:
    text = (
        "FINAL_ANSWER:\nok\nACTIONS_JSON:\n"
        '{"upload_files": ["a.png", {"path": "b.png"}, "A.PNG", "", {"nope": 1}]}\n'
    )
    assert upload_paths(extract_actions_json(text)) == ["a.png", "b.png"]


def test_no_declaration_yields_nothing(tmp_path) -> None:
    # The prompt asks agents to declare uploads only when files were requested,
    # so an empty list is the normal case, not a failure.
    assert resolve_uploads_from_text(text=_answer([]), workspace=tmp_path, max_files=5) == []
    assert resolve_uploads_from_text(text="no markers here", workspace=tmp_path, max_files=5) == []


def test_antigravity_now_resolves_declared_uploads(tmp_path) -> None:
    from fluxion.executors.antigravity.executor import AntiGravityExecutor

    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    executor = AntiGravityExecutor(
        timeout_sec=10,
        command="agy",
        sandbox=False,
        dangerously_skip_permissions=False,
        print_timeout_sec=10,
        logs_dir=tmp_path / "logs",
        max_structured_uploads=5,
    )

    # Previously dropped on the floor: agy emitted the block, nothing read it.
    resolved = executor._resolve_action_uploads(stdout=_answer(["shot.png"]), workspace=tmp_path)
    assert [Path(p).name for p in resolved] == ["shot.png"]


def _write_agy_log(path: Path) -> None:
    write_jsonl_log(
        path=path,
        task_id="t1",
        command=["agy"],
        stdout="FINAL_ANSWER:\nthe answer\n",
        stderr="",
        extra_streams={"agy": "\n".join([_GLOG] * 3 + ["agy: real runtime note"])},
    )


def test_the_ui_still_gets_the_runtime_log_folded_into_stdout(tmp_path) -> None:
    log = tmp_path / "task-t1.log"
    _write_agy_log(log)

    stdout, _stderr = load_task_logs(log)
    bodies = [row["body"] for row in stdout]
    assert any("the answer" in b for b in bodies)
    # Unchanged for the web UI: one stream, no per-stream tab needed.
    assert any("http_helpers.go" in b for b in bodies)


def test_the_mcp_view_separates_and_denoises_the_runtime_log(tmp_path) -> None:
    log = tmp_path / "task-t1.log"
    _write_agy_log(log)

    view = _output_view(log, fallback_stdout="", fallback_stderr="")

    stdout_bodies = [row["body"] for row in view["stdout"]]
    assert any("the answer" in b for b in stdout_bodies)
    # The agent's own output is no longer buried under transport plumbing.
    assert not any("http_helpers.go" in b for b in stdout_bodies)

    executor_bodies = [row["body"] for row in view["executor_log"]]
    assert executor_bodies == ["agy: real runtime note"]
    assert view["executor_log_noise_filtered"] == 3


def test_streams_are_only_split_when_asked(tmp_path) -> None:
    log = tmp_path / "task-t1.log"
    _write_agy_log(log)

    _out, _err, folded = load_task_streams(log)
    assert folded == {}

    _out, _err, split = load_task_streams(log, fold_extra_streams=False)
    assert list(split) == ["agy"]
