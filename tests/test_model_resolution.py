from pathlib import Path

from fluxion.executors.model_resolution import (
    antigravity_label_to_model_id,
    extract_antigravity_resolved_model,
)
from fluxion.web.services.aggregator import _apply_event, _initial_task


def test_antigravity_display_labels_map_to_cli_model_ids() -> None:
    assert antigravity_label_to_model_id("Gemini 3.7 Flash (Low)") == "gemini-3.7-flash-low"
    assert antigravity_label_to_model_id("Claude Opus 4.6 (Thinking)") == "claude-opus-4-6-thinking"


def test_runtime_model_extraction_supports_raw_and_json_escaped_logs(tmp_path: Path) -> None:
    log_file = tmp_path / "task.agy.log"
    log_file.write_text(
        'Propagating selected model override to backend: label="Gemini 3.7 Flash (Low)"\n',
        encoding="utf-8",
    )
    bundled_tail = (
        r'{"body":"Propagating selected model override to backend: '
        r'label=\"Gemini 3.7 Flash (High)\""}'
    )

    assert extract_antigravity_resolved_model(log_file, bundled_tail) == "gemini-3.7-flash-high"


def test_runtime_model_extraction_returns_empty_when_cli_has_not_reported_it() -> None:
    assert extract_antigravity_resolved_model("ordinary output") == ""


def test_aggregated_task_preserves_prelaunch_and_runtime_model_fields() -> None:
    received = {
        "task_id": "t1",
        "status": "RECEIVED",
        "task": {
            "channel": "local",
            "user_id": "local",
            "workspace": "/repo",
            "metadata": {
                "executor": "antigravity",
                "requested_model": "",
                "effective_model": "",
                "model_resolution_source": "executor_runtime",
            },
        },
    }
    task = _initial_task("t1", received)
    _apply_event(
        task,
        {
            "status": "RETURNED",
            "result": {
                "success": True,
                "effective_model": "",
                "resolved_model": "gemini-3.7-flash-high",
                "model_resolution_source": "executor_runtime",
            },
        },
    )

    assert task["effective_model"] == ""
    assert task["resolved_model"] == "gemini-3.7-flash-high"
    assert task["model_resolution_source"] == "executor_runtime"
