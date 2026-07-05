from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.subagent import (
    MODE_INSTRUCTIONS,
    PROFILE_INSTRUCTIONS,
    SUPPORTED_AGENTS,
    SubagentRunRequest,
    conversation_key_for_run,
    run_subagent,
    task_text,
    visible_changed_files,
)
from fluxion.utils.logger import setup_logging


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    setup_logging()
    if args.wait:
        return _wait_main(args)
    if not args.prompt:
        return _print_error("a prompt is required (or use --wait RUN_ID)", as_json=args.json)
    try:
        run = run_subagent(
            SubagentRunRequest(
                agent=args.agent,
                prompt=args.prompt,
                project=args.project,
                workspace=args.workspace,
                thread=args.thread,
                task_name=args.task_name,
                parent_path=args.parent_path,
                user=args.user,
                profile=args.profile,
                mode=args.mode,
                session_policy=args.session_policy,
                include_subagent_preamble=not args.no_subagent_preamble,
                model=args.model or None,
            )
        )
        if args.json:
            print(
                json.dumps(
                    run.to_payload(include_stdout=args.include_stdout or args.verbose),
                    ensure_ascii=False,
                    default=str,
                )
            )
        else:
            print(run.summary)
        return 0 if run.success else max(1, run.exit_code or 1)
    except Exception as exc:
        return _print_error(str(exc), as_json=args.json)


def _wait_main(args: argparse.Namespace) -> int:
    """Block until an already-submitted run reaches a terminal status, then
    print its result. Meant to be launched as a background task by the caller's
    harness: when this process exits, the harness wakes the caller with the
    result — effectively turning Fluxion's async runs into push-on-completion
    without needing any MCP-side notification support."""
    from fluxion.config.settings import Settings
    from fluxion.web.services.aggregator import TERMINAL_RUN_STATUSES, wait_for_terminal

    settings = Settings.load()
    task = wait_for_terminal(
        settings.data_dir, args.wait, timeout_sec=args.timeout, poll_interval=2.0
    )
    if task is None:
        return _print_error(f"run not found: {args.wait}", as_json=args.json)
    status = str(task.get("status") or "")
    terminal = status in TERMINAL_RUN_STATUSES
    if args.json:
        print(json.dumps(task, ensure_ascii=False, default=str))
    else:
        print(f"[{status}] {task.get('summary', '')}".rstrip())
        for path in task.get("changed_files") or []:
            print(f"  changed: {path}")
    if not terminal:
        return 2  # timed out while still running — caller may re-wait
    return 0 if task.get("success") else max(1, int(task.get("exit_code") or 1))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fluxion-sub",
        description="Run a Fluxion sub-agent task from the local command line.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Subtask prompt to send to the agent. Omit when using --wait.",
    )
    parser.add_argument(
        "--agent",
        default="auto",
        choices=[
            "auto",
            "agy",
            "antigratity",
            "antigravity-cli",
            "claude-code",
            "claude-code-cli",
            *sorted(SUPPORTED_AGENTS),
        ],
        help="Executor to run for this subtask. auto uses the project default, then FLUXION_DEFAULT_EXECUTOR.",
    )
    parser.add_argument(
        "--project",
        default="",
        help="Configured Fluxion project key. When set, --workspace is resolved inside that project.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace path. Defaults to the current directory.",
    )
    parser.add_argument(
        "--thread",
        default="default",
        help="Local thread key used for executor session reuse.",
    )
    parser.add_argument(
        "--task-name",
        default=None,
        help="Optional lowercase task name used to build an agent path.",
    )
    parser.add_argument(
        "--parent-path",
        default="/root",
        help="Parent agent path for task naming. Defaults to /root.",
    )
    parser.add_argument(
        "--user",
        default="local",
        help="Local user key used for session storage.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a slim machine-readable JSON result.",
    )
    parser.add_argument(
        "--profile",
        default="inspect",
        choices=sorted(PROFILE_INSTRUCTIONS),
        help="Sub-agent task profile.",
    )
    parser.add_argument(
        "--mode",
        default="read-only",
        choices=sorted(MODE_INSTRUCTIONS),
        help="Execution intent passed to the sub-agent prompt.",
    )
    parser.add_argument(
        "--session-policy",
        default="auto",
        choices=["auto", "continue", "new"],
        help="Session policy. auto/continue reuse workspace+thread session; new starts a fresh local thread key.",
    )
    parser.add_argument("--model", default="", help="Optional per-run model override.")
    parser.add_argument(
        "--include-stdout",
        action="store_true",
        help="Include full executor stdout/stderr in JSON output.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Alias for --include-stdout in JSON mode.",
    )
    parser.add_argument(
        "--no-subagent-preamble",
        action="store_true",
        help="Send the prompt without Fluxion's sub-agent boundary preamble.",
    )
    parser.add_argument(
        "--wait",
        metavar="RUN_ID",
        default="",
        help="Instead of submitting, block until an existing run finishes and "
        "print its result. Designed to be run as a background task so the "
        "calling harness wakes when it exits. Exit 0 = success, 2 = still "
        "running at timeout.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help="Max seconds to block in --wait mode before giving up (default 900).",
    )
    return parser.parse_args(argv)


def _conversation_key(*, workspace: Path, thread: str, session_policy: str) -> str:
    return conversation_key_for_run(
        workspace=workspace,
        thread=thread,
        session_policy=session_policy,
    )


def _task_text(
    prompt_parts: list[str],
    *,
    subagent: bool,
    profile: str,
    mode: str,
) -> str:
    return task_text(prompt_parts, subagent=subagent, profile=profile, mode=mode)


def _result_payload(
    task: Task,
    agent: str,
    conversation_key: str,
    result: ExecutionResult,
    *,
    include_stdout: bool,
) -> dict[str, Any]:
    changed_files = _visible_changed_files(task=task, result=result)
    payload: dict[str, Any] = {
        "run_id": task.id,
        "task_id": task.id,
        "agent": agent,
        "thread": task.metadata.get("subagent", {}).get("thread", ""),
        "task_name": task.metadata.get("subagent", {}).get("task_name", ""),
        "parent_path": task.metadata.get("subagent", {}).get("parent_path", ""),
        "agent_path": task.metadata.get("subagent", {}).get("agent_path", ""),
        "conversation_key": conversation_key,
        "success": result.success,
        "summary": result.summary,
        "exit_code": result.exit_code,
        "executor_session_id": result.executor_session_id,
        "changed_files": changed_files,
        "risk_flags": result.risk_flags,
        "change_set_file": result.change_set_file,
        "diff_summary": result.diff_summary,
        "artifacts": result.artifacts,
        "log_file": result.log_file,
    }
    if include_stdout:
        raw_result = result.__dict__.copy()
        raw_result["changed_files"] = changed_files
        payload["result"] = raw_result
    return payload


def _visible_changed_files(*, task: Task, result: ExecutionResult) -> list[str]:
    return visible_changed_files(task=task, result=result)


def _print_error(message: str, *, as_json: bool) -> int:
    if as_json:
        print(
            json.dumps(
                {
                    "success": False,
                    "summary": message,
                    "exit_code": 1,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
