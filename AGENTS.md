# Fluxion Agent Guide

## Project Goal

Fluxion is a local-first agent gateway. The primary agent keeps ownership of
the task, while Fluxion can delegate scoped subtasks to local executors such as
Antigravity, Codex, or Claude.

## Fluxion Sub-Agent MCP

Use the Fluxion MCP server when a subtask is self-contained and delegating it
can reduce the primary agent's context load.

Available MCP flow:

1. If the task is for a known repo, call `mcp__fluxion__list_projects` and use
   the matching `project` key instead of passing a raw absolute path.
2. Call `mcp__fluxion__run_subagent`. Use `wait_for_result=true` for short
   tasks that are expected to finish quickly; use `wait_for_result=false` for
   longer investigation or implementation work.
3. Poll `mcp__fluxion__get_task_status` with the returned `run_id`.
4. If the active run should be stopped, call
   `mcp__fluxion__cancel_subagent_run` with the same `run_id`.
5. When status is `RETURNED`, `FAILED`, or `CANCELED`, call
   `mcp__fluxion__get_task_result`.
6. Review the result before relying on it or continuing work.
7. If a workspace-writing run produced unwanted changes, call
   `mcp__fluxion__revert_subagent_run` only after reviewing the recorded
   `change_set_file` and conflict risk.

Polling and failure handling:

- Poll at a modest interval, such as every 2-5 seconds for short checks and
  less frequently for longer implementation tasks.
- For likely 10-30 second subtasks, prefer `wait_for_result=true` to avoid
  extra tool-call turns. For long-running tasks, keep async mode and avoid
  tight polling loops.
- `wait_for_result` waits on the whole lifecycle, including queue time. If it
  returns `timed_out=true`, check the returned `status` (`QUEUED` vs `RUNNING`)
  and continue with `get_task_status`; the task is not canceled by the wait
  timeout.
- If a run does not finish in a reasonable task-specific time, stop polling and
  report the current `run_id`, status, and blocker instead of looping forever.
- Use `progress_signal`, `log_updated_at`, and `recent_output_tail` from
  `get_task_status` to distinguish a long-running task that is still producing
  output from one that appears stuck. `recent_output_tail` is intentionally a
  short tail, not a full log.
- `get_task_status` returns a compact polling payload by default. Pass
  `detail=true` only when repeated metadata such as timestamps, subagent
  metadata, changed_files, diff_summary, artifacts, or change_set_file is
  needed before `get_task_result`.
- Use `cancel_subagent_run` when the sub-agent is no longer needed or appears
  stuck. Cancellation only applies to active runs owned by the current MCP
  server process.
- Treat `FAILED` and `CANCELED` as terminal states. Inspect `summary` and
  `log_file` before retrying.
- Reverts are hash-checked. If `revert_subagent_run` reports conflicts, inspect
  manually instead of forcing over later user or agent changes.
- Avoid launching multiple workspace-writing sub-agents in the same workspace
  at the same time unless the files are clearly disjoint.

Useful arguments:

- `project`: prefer a configured project key from `list_projects` for
  multi-repo work.
- `agent`: use `auto` by default so Fluxion can choose the project default
  executor. Set `antigravity`, `codex`, or `claude` only when the subtask
  needs a specific executor.
- `model`: optional per-run executor model override. Leave it empty to use the
  executor's configured/default model. If you need to choose explicitly, call
  `list_agent_models(agent=...)` first and pass one of the returned `models[].id`
  values to `run_subagent(model=...)`. Do not pass `price_references[].id`;
  those entries are pricing context only and may not be valid executor model
  arguments. `model` is not a provider selector; provider, base URL, and auth
  remain settings-level configuration.
- `profile`: choose `inspect`, `implement`, `verify`, or `summarize`.
- `mode`: use `read-only` for investigation and `workspace-write` only when
  edits are intended.
- For implementation, edit, fix, or refactor tasks, explicitly set
  `profile=implement` and `mode=workspace-write`.
- `session_policy`: use `continue` for repeated calls in the same task thread;
  use `new` for isolated one-off subtasks.
- `include_stdout`: keep `false` unless debugging a failed run that requires
  raw executor output.

Good delegation targets:

- Read-only investigation of a narrow module or file set.
- First-pass UI or copy implementation for the primary agent to review.
- Repetitive file inspection or summarization.
- Focused verification, smoke checks, or regression notes.

Do not delegate:

- Broad architecture or product decisions.
- Destructive operations.
- Secret handling or credential inspection.
- Tasks requiring direct user approval.
- Final review responsibility.

The primary agent remains responsible for planning, diff review, final
verification, and the user-facing answer.

Expected sub-agent result handling:

- Prefer concise summaries over raw logs in the primary response.
- Preserve `run_id`, `status`, and `summary` while polling. After terminal
  status, preserve `changed_files`, `artifacts`, and `executor_session_id` from
  `get_task_result` when reporting or chaining sub-agent work.
- Preserve `change_set_file`, `recoverable_changed_files`, and
  `unrecoverable_changed_files` when a run changed files.
- Use `get_task_status` helper fields (`is_terminal`, `can_cancel`,
  `elapsed_sec`, `next_action`) to decide whether to poll, cancel, or fetch
  the result.
- Do not treat `changed_files=[]` on a `RUNNING` task as proof that nothing was
  changed. File changes are finalized in `get_task_result` after a terminal
  status; canceled tasks may still report partial changes.
- Use the default compact `get_task_result` response for normal work. Request
  `raw=true` or `include_output=true` only when debugging.
- Inspect changed files before continuing if the sub-agent used
  `mode=workspace-write`.
- Do not assume a successful sub-agent result is final approval.

## Local Development

- Prefer the project virtualenv when running Python:
  `.venv/bin/python`.
- Keep generated caches, build output, and temporary artifacts out of commits
  unless they are explicitly part of the task.
- Avoid personal absolute paths in docs and source files.

## Agent Verification

Before finishing code changes, run only the checks that match the touched
area:

- Python changed: run `ruff check --fix src tests`, then
  `ruff format src tests`, then `pytest -q`.
- `web/` changed: run `npm --prefix web run build`.
- Swift or macOS app sources changed: run `desktop/build.sh`.
- macOS packaging, release, bundle metadata, signing, app icon, or installer
  files changed: run `scripts/package-macos-app.sh`.
- If a required command cannot be run, report the reason clearly.
