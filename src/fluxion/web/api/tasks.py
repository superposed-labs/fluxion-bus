from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from fluxion.config.settings import Settings
from fluxion.mcp_server.server import run_subagent_tool
from fluxion.subagent import SubagentRunner
from fluxion.web.deps import get_data_dir
from fluxion.web.services.aggregator import aggregate_tasks_cached
from fluxion.web.services.diff_hunks import load_diff_hunks, summarize_diff_hunks
from fluxion.web.services.log_parser import load_task_logs

router = APIRouter()


class TaskRunInput(BaseModel):
    prompt: str = Field(min_length=1)
    agent: str = "auto"
    project: str = ""
    workspace: str = "."
    thread: str = ""
    task_name: str = ""
    parent_path: str = "/root"
    profile: str = "inspect"
    mode: str = "read-only"
    session_policy: str = "auto"
    conversation_key: str = ""
    model: str = ""
    client_id: str = "web"
    authorization_request_id: str = ""


def executor_settings_fingerprint(settings: Settings) -> tuple[Any, ...]:
    return (
        settings.default_executor,
        tuple(settings.enabled_executors),
        settings.claude_command,
        settings.claude_provider,
        settings.claude_auth_mode,
        settings.claude_model,
        settings.claude_base_url,
        settings.claude_api_key,
        settings.claude_auth_token,
        settings.codex_sandbox_mode,
        settings.codex_bypass_sandbox,
        settings.antigravity_command,
        settings.antigravity_sandbox,
        settings.antigravity_dangerously_skip_permissions,
    )


def _current_runner(request: Request) -> tuple[SubagentRunner | None, Settings | None]:
    runner = getattr(request.app.state, "subagent_runner", None)
    settings = getattr(request.app.state, "subagent_settings", None)
    if runner is None or settings is None:
        return None, None

    next_settings = Settings.reload()
    next_fingerprint = executor_settings_fingerprint(next_settings)
    current_fingerprint = getattr(request.app.state, "subagent_settings_fingerprint", None)
    if current_fingerprint != next_fingerprint:
        settings = next_settings
        runner = SubagentRunner(settings)
        request.app.state.subagent_settings = settings
        request.app.state.subagent_settings_fingerprint = next_fingerprint
        request.app.state.subagent_runner = runner
    else:
        # Permission and other non-executor settings can refresh without
        # rebuilding GatewayCore. The runner's WorkspaceAccessService takes
        # its own fresh authorization snapshot for every submission.
        settings = next_settings
        request.app.state.subagent_settings = settings
    return runner, settings


def _hydrate_logs(task_in: dict[str, Any], *, include_diff_hunks: bool = False) -> dict[str, Any]:
    # Shallow-copy so we don't mutate the aggregator's cached dict —
    # repeated requests would otherwise see _fallback_* keys already
    # popped and stdout/stderr already filled from a stale read.
    task = {**task_in}
    data_dir = get_data_dir()
    log_path = data_dir / "logs" / f"task-{task['task_id']}.log"
    fallback_stdout = task.pop("_fallback_stdout", "")
    fallback_stderr = task.pop("_fallback_stderr", "")
    task["stdout"], task["stderr"] = load_task_logs(
        log_path,
        fallback_stdout=fallback_stdout,
        fallback_stderr=fallback_stderr,
    )
    if include_diff_hunks:
        hunks = load_diff_hunks(str(task.get("change_set_file") or ""))
        task["diff_hunks"] = hunks
        _merge_changed_file_stats(task, summarize_diff_hunks(hunks))
    return task


def _merge_changed_file_stats(
    task: dict[str, Any],
    stats: dict[str, dict[str, int | str]],
) -> None:
    if not stats:
        return
    changed = task.get("changed_files")
    if not isinstance(changed, list):
        changed = []
    by_path: dict[str, dict[str, Any]] = {}
    for item in changed:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        by_path[item["path"]] = {**item}
    for path, stat in stats.items():
        current = by_path.get(path, {"op": stat["op"], "path": path})
        if not current.get("op") or current.get("op") == "M":
            current["op"] = stat["op"]
        current["additions"] = stat["additions"]
        current["deletions"] = stat["deletions"]
        by_path[path] = current
    task["changed_files"] = list(by_path.values())
    task["diff_summary"] = {
        "files": len(stats),
        "additions": sum(int(stat["additions"]) for stat in stats.values()),
        "deletions": sum(int(stat["deletions"]) for stat in stats.values()),
    }


@router.get("/tasks")
def list_tasks() -> dict[str, list[dict[str, Any]]]:
    tasks = [_hydrate_logs(t) for t in aggregate_tasks_cached(get_data_dir())]
    return {"tasks": tasks}


@router.post("/tasks")
def run_task(payload: TaskRunInput, request: Request) -> dict[str, Any]:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
    runner, settings = _current_runner(request)
    if runner is None or settings is None:
        raise HTTPException(status_code=503, detail="Subagent runner is not ready")
    result = run_subagent_tool(
        runner=runner,
        settings=settings,
        prompt=prompt,
        agent=payload.agent,
        project=payload.project,
        workspace=payload.workspace,
        thread=payload.thread,
        task_name=payload.task_name,
        parent_path=payload.parent_path,
        profile=payload.profile,
        mode=payload.mode,
        session_policy=payload.session_policy,
        conversation_key=payload.conversation_key,
        model=payload.model,
        client_id=payload.client_id or "web",
        authorization_request_id=payload.authorization_request_id,
        wait_for_result=False,
        # No inline wait for a workspace approval here: this handler runs on a
        # threadpool worker, so holding it open for a human click would spend a
        # server worker per waiting client. HTTP callers poll
        # GET /api/workspaces/requests/{id} instead.
        authorization_wait_ms=0,
    )
    if result.get("success") is False:
        status_code = 403 if result.get("error_code") == "WORKSPACE_NOT_AUTHORIZED" else 400
        # Keep the structured authorization_request_id/pending fields in the
        # HTTP response.  The caller is responsible for retrying after App
        # approval; the server never replays a rejected task automatically.
        raise HTTPException(status_code=status_code, detail=result)
    return result


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    for task in aggregate_tasks_cached(get_data_dir()):
        if task["task_id"] == task_id:
            return _hydrate_logs(task, include_diff_hunks=True)
    raise HTTPException(status_code=404, detail="Task not found")
