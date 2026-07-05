from __future__ import annotations

from typing import Any

from fluxion.config.settings import Settings
from fluxion.mcp_server.model_catalog import list_agent_models_view
from fluxion.mcp_server.payloads import _error_payload, _timed_out_still_running_payload
from fluxion.mcp_server.threads import _resolve_thread
from fluxion.mcp_server.views import (
    _TERMINAL_STATUSES,
    _filtered_tasks,
    _find_task,
    _result_view,
    _slim_task,
    _status_poll_view,
    _status_view,
)
from fluxion.subagent import SubagentRunner, SubagentRunRequest, visible_changed_files
from fluxion.web.services.aggregator import reset_cache, wait_for_terminal
from fluxion.web.services.log_parser import load_task_logs
from fluxion.workspace.change_set import revert_change_set


def run_subagent_tool(
    *,
    runner: SubagentRunner,
    settings: Settings,
    prompt: str,
    agent: str = "auto",
    project: str = "",
    workspace: str = ".",
    thread: str = "",
    task_name: str = "",
    parent_path: str = "/root",
    profile: str = "inspect",
    mode: str = "read-only",
    session_policy: str = "auto",
    conversation_key: str = "",
    include_stdout: bool = False,
    include_subagent_preamble: bool | None = None,
    model: str = "",
    wait_for_result: bool = False,
    timeout_sec: int = 300,
) -> dict[str, Any]:
    resolved_thread = _resolve_thread(thread)
    request = SubagentRunRequest(
        agent=agent,
        prompt=[prompt],
        project=project or None,
        workspace=workspace,
        thread=resolved_thread,
        task_name=task_name or None,
        parent_path=parent_path,
        profile=profile,
        mode=mode,
        session_policy=session_policy,
        conversation_key=conversation_key or None,
        include_subagent_preamble=include_subagent_preamble,
        model=model or None,
    )
    try:
        handle = runner.submit(request)
        reset_cache()
        if not wait_for_result:
            payload = handle.to_payload()
            payload["timed_out"] = False
            payload["wait_for_result"] = False
            payload["next_tools"] = [
                "get_task_status",
                "cancel_subagent_run",
                "get_task_result",
            ]
            return payload

        timeout_sec = max(1, min(int(timeout_sec), settings.task_timeout_sec + 5))
        result = handle.adapter.wait(timeout_sec=timeout_sec)
        reset_cache()
        if result is None:
            # The wait elapsed but the task is not a failure. Leave it running
            # in the background (still bounded by task_timeout_sec) and hand the
            # run_id back so the caller can collect the result later, instead of
            # destroying in-progress work just because the blocking wait was too
            # short. Callers who actually want to stop it use cancel_subagent_run.
            return _timed_out_still_running_payload(
                handle,
                timeout_sec=timeout_sec,
                prompt=prompt,
                profile=profile,
                mode=mode,
            )

        payload = handle.to_payload()
        payload.update(
            {
                "success": result.success,
                "status": "RETURNED" if result.success else "FAILED",
                "timed_out": False,
                "summary": result.summary,
                "exit_code": result.exit_code,
                "executor_session_id": result.executor_session_id,
                "changed_files": visible_changed_files(
                    workspace=settings.resolve_run_workspace(
                        raw_workspace=workspace,
                        project_key=project,
                        mode=mode,
                    ),
                    result=result,
                ),
                "risk_flags": result.risk_flags,
                "change_set_file": result.change_set_file,
                "diff_summary": result.diff_summary,
                "artifacts": result.artifacts,
                "log_file": result.log_file,
                "wait_for_result": True,
            }
        )
        if include_stdout:
            payload["result"] = result.__dict__.copy()
        return payload
    except Exception as exc:
        return _error_payload(
            error=exc,
            agent=agent,
            project=project,
            workspace=workspace,
            thread=thread,
            task_name=task_name,
            parent_path=parent_path,
            profile=profile,
            mode=mode,
            settings=settings,
        )


def create_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The MCP Python SDK is not installed. Install Fluxion with its project "
            "dependencies, or install `mcp[cli]`."
        ) from exc

    settings = Settings.load()
    runner = SubagentRunner(settings)
    mcp = FastMCP("Fluxion")

    @mcp.tool()
    def run_subagent(
        prompt: str,
        agent: str = "auto",
        project: str = "",
        workspace: str = ".",
        thread: str = "",
        task_name: str = "",
        parent_path: str = "/root",
        profile: str = "inspect",
        mode: str = "read-only",
        session_policy: str = "auto",
        include_stdout: bool = False,
        include_subagent_preamble: bool | None = None,
        model: str = "",
        wait_for_result: bool = False,
        timeout_sec: int = 300,
    ) -> dict[str, Any]:
        """Submit a local Fluxion executor as a sub-agent task.

        By default (wait_for_result=false) this returns as soon as the task is queued:
        you get a run_id — poll get_task_status and read get_task_result once terminal.
        Set wait_for_result=true for small smoke checks that should block until done; if
        the wait elapses first the task continues in the background (queued or running,
        bounded by task_timeout_sec) and is collected later via get_task_result with the
        run_id (the run is not canceled). For long or open-ended work prefer the default.
        timeout_sec only limits this blocking wait; it is not the executor runtime
        limit. The actual task execution cap is settings.task_timeout_sec.

        agent: "auto" (default; uses the configured default) or one of "claude",
        "codex", "antigravity".

        workspace: directory the agent runs in. With a configured `project`, "." is the
        project root. WITHOUT a project (the default), pass an ABSOLUTE path to the
        target repo — "." resolves against the server's workspace_root, not the caller's
        cwd, so it is rarely what you want. Use list_projects to see configured projects.

        profile/mode: for edit/fix/implement tasks set profile=implement and
        mode=workspace-write; the defaults profile=inspect / mode=read-only are
        intentionally read-only.

        task_name: optional free-form label for the run. It is shown as-is in the
        UI and also slugified into the agent-path segment (lowercased, non
        [a-z0-9] runs collapsed to _), so spaces, uppercase, and hyphens are all
        accepted — no need to pre-format it.

        model: optional per-run model override for executors that support model
        selection. Leave empty to use the executor's configured/default model.
        To choose explicitly, call list_agent_models first and pass one of the
        returned models[].id values. Do not pass price_references[].id; those
        entries are pricing context only and may not be accepted by the executor.
        model is not a provider selector: provider, base URL, and auth remain
        settings-level configuration. Ping tasks keep their cheapest-model default
        unless model is explicitly set.

        thread / session_policy scope which executor session is reused. Default
        (empty thread) = a FRESH isolated session per call: independent tasks don't
        resume, so they can't inherit stale context or have their workspace edits
        reconciled away by a resumed agent. To CONTINUE a prior run (resume — reuses
        the agent's context, saving tokens), pass the SAME stable `thread` string on
        each call. session_policy is "auto" (default), "continue", or "new" (force a
        brand-new session even for a reused thread). Note: a resumed Antigravity run
        may reconcile the workspace to its remembered state, so avoid editing the
        workspace out-of-band between two same-thread runs.

        Results: both are finalized only at terminal status (changed_files_available
        stays false until then). changed_files is the authoritative, run-scoped list of
        what THIS run touched — act on it. diff_summary's additions/deletions are a rough
        magnitude from `git diff --stat` over the WHOLE working tree vs HEAD, not scoped
        to this run: pre-existing edits and consecutive uncommitted runs accumulate into
        it, so don't attribute its line counts to this run. To confirm exact changes,
        read the changed_files or run `git diff` yourself.
        """
        return run_subagent_tool(
            runner=runner,
            settings=settings,
            prompt=prompt,
            agent=agent,
            project=project,
            workspace=workspace,
            thread=thread,
            task_name=task_name,
            parent_path=parent_path,
            profile=profile,
            mode=mode,
            session_policy=session_policy,
            include_subagent_preamble=include_subagent_preamble,
            model=model,
            wait_for_result=wait_for_result,
            timeout_sec=timeout_sec,
            include_stdout=include_stdout,
        )

    @mcp.tool()
    def list_agent_models(agent: str = "auto", project: str = "") -> dict[str, Any]:
        """List likely selectable models for one executor, sorted high to low.

        Codex uses `codex debug models` when available. Antigravity uses `agy models`
        when available. Claude Code does not expose a stable local model catalog, so
        models[] contains known CLI aliases and the configured model. Every catalog is
        enriched with the local price table when possible.

        Pass a chosen models[].id to run_subagent(model=...). price_references[] is
        pricing context only and may not be valid as a model override. Provider, base
        URL, and auth remain settings-level config.
        """
        return list_agent_models_view(agent=agent, project=project, settings=settings)

    @mcp.tool()
    def list_projects() -> dict[str, Any]:
        """List configured Fluxion projects available for sub-agent runs."""
        projects = [
            project.to_public_dict()
            for project in sorted(settings.projects.values(), key=lambda item: item.key)
        ]
        result: dict[str, Any] = {"projects": projects}
        if not projects:
            result["hint"] = (
                "No projects are configured. You can still run_subagent by passing an "
                "absolute `workspace` path to the target repo (and profile=implement, "
                "mode=workspace-write for edits)."
            )
        return result

    @mcp.tool()
    def get_project(project: str) -> dict[str, Any]:
        """Return one configured Fluxion project by key."""
        try:
            resolved = settings.resolve_project(project)
        except Exception as exc:
            return {"found": False, "project": project, "summary": str(exc)}
        if resolved is None:
            return {"found": False, "project": project, "summary": "project is required."}
        payload = resolved.to_public_dict()
        payload["found"] = True
        return payload

    @mcp.tool()
    def list_subagent_runs(
        limit: int = 20,
        agent: str = "",
        project: str = "",
        status: str = "",
        agent_path_prefix: str = "",
    ) -> dict[str, Any]:
        """List recent Fluxion sub-agent runs from the local task store."""
        rows = _filtered_tasks(
            limit=max(1, min(limit, 100)),
            agent=agent,
            project=project,
            status=status,
            agent_path_prefix=agent_path_prefix,
        )
        return {"runs": [_slim_task(row) for row in rows]}

    @mcp.tool()
    def get_task_status(run_id: str, wait_ms: int = 0, detail: bool = False) -> dict[str, Any]:
        """Get model-friendly status and next action for a Fluxion run.

        The default response is compact for polling. It includes the run status,
        terminal/cancel hints, progress_signal, recent_output_tail, log freshness,
        and next_action. Pass detail=true to include the full status view with
        repeated metadata such as timestamps, subagent metadata, changed_files,
        diff_summary, artifacts, and change_set_file.

        Pass wait_ms > 0 to long-poll: the call blocks until the run reaches a
        terminal status or the wait elapses, then returns. wait_ms is capped at
        FLUXION_MCP_STATUS_MAX_WAIT_MS (default 60000 = 60s) to stay under the MCP
        client's per-call request timeout, so a still-running task just returns
        RUNNING — call again to keep waiting. This collapses a busy-poll loop into
        one blocking call per cap. Raise the env var if your client tolerates
        longer requests (set it to the client's tool timeout minus a margin)."""
        task = _find_task(run_id)
        if task is None:
            return {"found": False, "run_id": run_id}
        if wait_ms and str(task.get("status") or "") not in _TERMINAL_STATUSES:
            capped = min(max(0, int(wait_ms)), settings.mcp_status_max_wait_ms)
            waited = wait_for_terminal(
                settings.data_dir, run_id, timeout_sec=capped / 1000.0, poll_interval=1.0
            )
            task = waited or task
        if detail:
            return _status_view(task, settings=settings, runner=runner)
        return _status_poll_view(task, settings=settings, runner=runner)

    @mcp.tool()
    def cancel_subagent_run(run_id: str) -> dict[str, Any]:
        """Request cancellation for an active sub-agent run in this MCP process."""
        wanted = run_id.strip()
        if not wanted:
            return {
                "success": False,
                "canceled": False,
                "run_id": run_id,
                "summary": "run_id is required.",
            }
        canceled, reason = runner.cancel(wanted)
        reset_cache()
        task = _find_task(wanted)
        status = ""
        if task is not None:
            status = str(task.get("status") or "")
        return {
            "success": canceled,
            "canceled": canceled,
            "run_id": wanted,
            "task_id": wanted,
            "status": status or ("CANCEL_REQUESTED" if canceled else "UNKNOWN"),
            "summary": reason,
            "found": task is not None,
            "terminal": status in {"RETURNED", "FAILED", "CANCELED"},
        }

    @mcp.tool()
    def revert_subagent_run(run_id: str) -> dict[str, Any]:
        """Revert recoverable text-file changes recorded for one Fluxion run."""
        wanted = run_id.strip()
        if not wanted:
            return {
                "success": False,
                "run_id": run_id,
                "summary": "run_id is required.",
            }
        result = revert_change_set(settings.data_dir, wanted)
        reset_cache()
        return {
            "success": result.success,
            "run_id": result.run_id,
            "workspace": result.workspace,
            "reverted_files": result.reverted_files,
            "conflicts": [conflict.__dict__ for conflict in result.conflicts],
            "summary": result.summary,
        }

    @mcp.tool()
    def get_task_result(
        run_id: str,
        include_output: bool = False,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Get a model-friendly result view, optionally including raw output."""
        task = _find_task(run_id)
        if task is None:
            return {"found": False, "run_id": run_id}
        result = {**task, "found": True} if raw else _result_view(task)
        result.pop("_fallback_stdout", None)
        result.pop("_fallback_stderr", None)
        if include_output:
            log_path = settings.data_dir / "logs" / f"task-{task['task_id']}.log"
            stdout, stderr = load_task_logs(
                log_path,
                fallback_stdout=str(task.get("_fallback_stdout", "")),
                fallback_stderr=str(task.get("_fallback_stderr", "")),
            )
            result["stdout"] = stdout
            result["stderr"] = stderr
        else:
            result.pop("stdout", None)
            result.pop("stderr", None)
        return result

    return mcp


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
