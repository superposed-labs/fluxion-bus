# MCP Reference

`fluxion-mcp` exposes the Fluxion sub-agent runner as MCP tools over stdio.
MCP clients (Claude Code, Codex, etc.) start it on demand and call tools
without shelling out to `fluxion-sub`.

## Client setup

Replace `<fluxion-repo>` with the absolute path to your local Fluxion checkout.

**Claude Code:**

```bash
claude mcp add -s user \
  -e FLUXION_ENV_FILE=<fluxion-repo>/.env \
  -e FLUXION_WORKSPACE_ROOT=<fluxion-repo> \
  -e FLUXION_DATA_DIR=<fluxion-repo>/data \
  fluxion -- <fluxion-repo>/.venv/bin/fluxion-mcp
```

**Codex `config.toml`:**

```toml
[mcp_servers.fluxion]
command = "<fluxion-repo>/.venv/bin/fluxion-mcp"
args = []
startup_timeout_sec = 120

[mcp_servers.fluxion.env]
FLUXION_ENV_FILE = "<fluxion-repo>/.env"
FLUXION_WORKSPACE_ROOT = "<fluxion-repo>"
FLUXION_DATA_DIR = "<fluxion-repo>/data"
```

**AntiGravity (`mcp_config.json`):**

File path: `~/.gemini/antigravity/mcp_config.json` (or `~/.gemini/config/mcp_config.json` depending on installation)

```json
{
  "mcpServers": {
    "fluxion": {
      "command": "<fluxion-repo>/.venv/bin/fluxion-mcp",
      "args": [],
      "env": {
        "FLUXION_ENV_FILE": "<fluxion-repo>/.env",
        "FLUXION_WORKSPACE_ROOT": "<fluxion-repo>",
        "FLUXION_DATA_DIR": "<fluxion-repo>/data"
      }
    }
  }
}
```

After registration, restart the client or start a new session to reload MCP
tools. Tool names are exposed as `mcp__fluxion__run_subagent`,
`mcp__fluxion__list_projects`, `mcp__fluxion__get_project`,
`mcp__fluxion__get_task_status`, `mcp__fluxion__cancel_subagent_run`,
`mcp__fluxion__revert_subagent_run`, `mcp__fluxion__get_task_result`, and
`mcp__fluxion__list_subagent_runs`.

**Runtime notes:**

- `fluxion-mcp` is a stdio server started by the MCP client, so its working
  directory may not be the Fluxion checkout.
- Set `FLUXION_WORKSPACE_ROOT` to the absolute Fluxion checkout path.
- `Settings.load()` reads `FLUXION_ENV_FILE` first when set, then
  `FLUXION_WORKSPACE_ROOT/.env`, then the current directory's `.env`.

## Project registry

For multi-project MCP usage, register project keys so primary agents can call
Fluxion with `project="web"` instead of passing raw absolute paths every
time. Registered project workspaces are added to the allowed workspace set.

Recommended JSON file:

```json
{
  "web": {
    "workspace": "/Users/you/code/web",
    "default_executor": "antigravity",
    "description": "UI and local smoke-check tasks"
  },
  "api": {
    "workspace": "/Users/you/code/api",
    "default_executor": "codex"
  }
}
```

Then set:

```env
FLUXION_PROJECTS_FILE=<fluxion-repo>/projects.json
```

Inline alternative:

```env
FLUXION_PROJECTS=web=/Users/you/code/web|executor=antigravity,api=/Users/you/code/api|executor=codex
```

Use `list_projects` to discover configured projects and `get_project` to
inspect one project. `run_subagent(project=...)` runs in that project's root
by default. If `workspace` is also provided, relative values are resolved
inside the project root; absolute values must still be inside the project
root. Without `project`, `workspace` keeps its existing behavior and must be
inside `FLUXION_ALLOWED_WORKSPACES`.

For path-based calls without a registered project, configure trusted roots:

```env
FLUXION_TRUSTED_WORKSPACE_ROOTS=/Users/you/code
FLUXION_WORKSPACE_DISCOVERY=true
FLUXION_WRITE_ALLOWED_WORKSPACES=
FLUXION_DENIED_WORKSPACES=
```

With this policy, `read-only` runs may target Git repository roots under the
trusted roots. `workspace-write` remains stricter: it requires a registered
project, `FLUXION_ALLOWED_WORKSPACES`, or `FLUXION_WRITE_ALLOWED_WORKSPACES`.
Denied workspaces always win.

Fluxion serializes `workspace-write` sub-agent runs per workspace. Read-only
runs may still execute concurrently.

## Tools

### `list_projects`

List configured Fluxion projects available for sub-agent runs.

### `get_project`

Return one configured Fluxion project. Required parameter: `project`.

### `run_subagent`

Submit a sub-agent task. Returns `run_id` immediately by default.

| Parameter | Default | Notes |
| --- | --- | --- |
| `prompt` | (required) | Task prompt sent to the sub-agent. |
| `agent` | `auto` | Executor: `auto`, `antigravity`, `codex`, or `claude`. `auto` uses the project default executor, then `FLUXION_DEFAULT_EXECUTOR`. Common aliases such as `agy`, `antigravity-cli`, and `antigratity` are normalized. |
| `project` | `""` | Optional registered project key from `list_projects`. Prefer this over raw absolute `workspace` paths. |
| `profile` | `inspect` | `inspect` / `implement` / `verify` / `summarize`. |
| `mode` | `read-only` | `read-only` for investigation; `workspace-write` only when edits are intended. Prompt-level boundary, not executor sandboxing. |
| `session_policy` | `auto` | `auto` / `continue` reuse the workspace+thread session. `new` starts a fresh thread key. |
| `wait_for_result` | `false` | `true` blocks until the sub-agent completes. Use only for short smoke checks. |
| `include_stdout` | `false` | Include raw executor stdout/stderr. Keep `false` unless debugging. |
| `include_subagent_preamble` | `auto` | Prepend the standard Fluxion sub-agent preamble to the prompt. `auto` includes it on new sessions and skips it on resumed sessions to save tokens. Pass `false` to always suppress. |
| `timeout_sec` | `300` | Server-side blocking wait budget when `wait_for_result=true`. On expiry, Fluxion returns `timed_out=true` but does not cancel the run; it remains queued or running, bounded by `settings.task_timeout_sec`. |
| `workspace` | `.` | Workspace directory for the run. With `project`, `.` means the project root and relative paths resolve inside that project. Without `project`, relative paths resolve under `FLUXION_WORKSPACE_ROOT`. |
| `parent_path` | `/root` | Canonical local agent path recorded for UI/MCP routing metadata. |
| `task_name` | `""` | Optional task label stored with the run. |
| `thread` | `default` | Thread key combined with `workspace` to derive the conversation key. |

For implementation, edit, fix, or refactor tasks, explicitly pass
`profile=implement` and `mode=workspace-write`. The defaults are intentionally
read-only. If an edit-looking prompt is submitted as read-only, submit a
corrected call with `profile=implement` and `mode=workspace-write`.

### `list_subagent_runs`

List recent runs from `tasks.jsonl`.

| Parameter | Default | Notes |
| --- | --- | --- |
| `limit` | `20` | Max runs to return. Clamped to `1..100`. |
| `agent` | `""` | Filter by executor name. Empty means all. |
| `project` | `""` | Filter by registered project key. Empty means all. |
| `status` | `""` | Filter by status. Empty means all. |
| `agent_path_prefix` | `""` | Filter by agent path prefix. |

### `get_task_status`

Returns compact polling status for one `run_id`.

| Parameter | Default | Notes |
| --- | --- | --- |
| `run_id` | (required) | Run identifier returned by `run_subagent`. |
| `wait_ms` | `0` | Long-poll up to this many milliseconds, capped by `FLUXION_MCP_STATUS_MAX_WAIT_MS`. |
| `detail` | `false` | Include the full status view with repeated metadata such as timestamps, subagent metadata, changed_files, diff_summary, artifacts, and change_set_file. |

### `cancel_subagent_run`

Request cancellation for an active run. Required parameter: `run_id`.
Cancellation only applies to active runs owned by the current MCP server
process.

### `revert_subagent_run`

Revert recoverable text-file changes recorded for one completed or canceled
run. Required parameter: `run_id`.

Fluxion records a per-run ChangeSet by taking a text-content snapshot before
and after execution. Revert applies the inverse of that ChangeSet only when
the current file hashes still match the run's recorded final hashes. If a file
was edited again after the run, the revert is blocked with conflicts instead
of overwriting later work.

Large files, binary files, and non-UTF-8 files are tracked by hash/size but are
not automatically restored when old content was not captured. Added files can
still be removed if their current hash matches the recorded hash.

### `get_task_result`

Return the result for a completed run.

| Parameter | Default | Notes |
| --- | --- | --- |
| `run_id` | (required) | Run identifier returned by `run_subagent`. |
| `include_output` | `false` | Include raw executor stdout/stderr. Keep `false` unless debugging. |
| `raw` | `false` | Return the full persisted task object instead of the compact result view. |

Default compact shape includes: `final_summary`, `changed_files`,
`change_set_file`, `artifacts`, `needs_review`, and `log_file`.

## Usage flows

### Async (default)

For any non-trivial task.

1. Call `run_subagent` with `wait_for_result=false`. Keep the `run_id`.
2. Poll `get_task_status` until `is_terminal` is `true`.
   - Short tasks: poll every 2–5 s.
   - Longer tasks: back off to 10–30 s.
3. Call `get_task_result`.

### Sync short-task

For quick smoke checks expected to finish in seconds.

1. Call `run_subagent` with `wait_for_result=true` and a matching `timeout_sec`.
2. Read the inline summary. No polling needed.
3. If `timed_out=true`, use the `run_id` with `get_task_status` then
   `get_task_result` to inspect the final state.

### Cancel

When an active run should be stopped early.

1. Call `cancel_subagent_run` with the `run_id`.
2. Poll `get_task_status` until the status reaches `CANCELED` or another
   terminal state.
3. Call `get_task_result` to inspect what was produced before cancellation.

### Revert a run

When a sub-agent produced unwanted file changes:

1. Call `get_task_result` and review `change_set_file`.
2. Call `revert_subagent_run` with the `run_id`.
3. If the response includes conflicts, inspect them manually. Do not force a
   revert over files that changed after the run.

## Task status states

| Status | Description | Terminal |
| --- | --- | --- |
| `RECEIVED` | Request accepted by the gateway. | |
| `VALIDATED` | Inputs validated; ready to enqueue. | |
| `QUEUED` | Waiting on a worker slot. | |
| `RUNNING` | Executor is producing output. | |
| `RETRYING` | Transient failure; gateway is retrying. | |
| `RETURNED` | Finished successfully. | ✓ |
| `FAILED` | Finished with an error. | ✓ |
| `CANCELED` | Stopped via `cancel_subagent_run`. | ✓ |

`is_terminal` is `true` for `RETURNED`, `FAILED`, and `CANCELED`. The default
compact status includes helper fields `result_available`,
`changed_files_available`, `can_cancel`, `elapsed_sec`, `next_action`,
`suggested_poll_after_sec`, `progress_signal`, `log_size`, `log_updated_at`,
and `recent_output_tail`. Do not treat
`changed_files=[]` on a `RUNNING` task as proof that no files changed; file
changes are finalized only after a terminal status. Canceled tasks preserve
partial changed files when Fluxion can compute the workspace delta.

Fields such as `timestamp`, `subagent`, `changed_files`, `diff_summary`,
`artifacts`, and `change_set_file` are omitted by default to keep polling
lightweight. Pass `detail=true` when those repeated metadata fields are needed.

`recent_output_tail` is a compact tail, not the full log. The default polling
view caps it to the last 8 lines and roughly 1000 characters so repeated polls
do not flood the primary agent context. Terminal compact status omits the tail;
call `get_task_result` for the final answer and file changes. Pass `detail=true`
for the fuller status tail (up to 20 lines / roughly 4000 characters). Use
`get_task_result(include_output=true)` only for failure debugging.

`next_action` values:

- `poll_later_or_cancel` — wait before polling again, or cancel if no longer needed.
- `get_task_result` — terminal state reached; fetch the result.
- `review_result` — returned by `get_task_result`; review and integrate.
- `inspect_status` — unexpected state; inspect the status payload.

`progress_signal` values:

- `output_recent` — live output was observed from the executor in this MCP process.
- `output_seen` — a task log exists and has output, but no live stream is available.
- `terminal_output` — terminal run has persisted output.
- `no_output_seen` — no live output or log output has been observed yet.
- `terminal` — terminal run without visible output.

## Failure debugging

When a run ends in `FAILED` or `CANCELED`:

1. Call `get_task_result` and read `summary`, `status`, and `log_file`.
2. If the summary is unclear, re-call with `include_output=true`.
3. Use `raw=true` only when you need the full task object.
4. Do not retry by polling — submit a new `run_subagent` request if a retry is
   intended.
