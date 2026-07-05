# Scheduler

`fluxion-scheduler` is a standalone daemon that fires sub-agent runs on a cron
schedule or when a provider quota window refreshes. It is the fourth
independent Fluxion surface: it shares the same `data/` directory as the other
surfaces but runs on its own.

The scheduler is not a macOS-only feature. It is the service that performs
automatic quota-reset pings and sends Slack, Telegram, WeChat, LINE, QQ, or Feishu reset
notifications on both macOS and Linux. The native macOS menu bar app provides
a convenient UI for configuring these options and starting the scheduler, but
it is not required for them to work. Windows support has not been verified.

It hosts its **own** executor (an internal `SubagentRunner`, the same path used
by `fluxion-sub` and the MCP server), so it executes tasks without the messaging
gateway or the web UI running. Results are written to `data/tasks.jsonl`, so
the web console shows scheduled runs alongside everything else.

## Why

AI providers meter usage in rolling windows (5-hour and weekly). Two patterns
this solves:

- **Reserve a window the moment it opens.** Some providers (notably Codex)
  refresh the weekly quota irregularly, and the weekly window only starts
  counting from the first call after a refresh. A `quota_refresh` trigger
  detects the reset and fires immediately — even just a `ping` ("say hello") —
  to open the window on your terms.
- **Run work on a fixed cadence.** A `cron` trigger runs a task at a set time
  (e.g. the start of a known 5-hour window) to spread usage across the period.

Reading quota does **not** consume model quota, so the daemon can poll tightly
(it caps at 60s / floors at 15s by default).

## Running

```bash
source .venv/bin/activate
fluxion-scheduler                 # run the daemon (Ctrl-C / SIGTERM to stop)
fluxion-scheduler --once          # evaluate one tick and exit (for testing)
fluxion-scheduler --tick-sec 30   # override the poll interval
fluxion-scheduler --log-level debug
```

For always-on operation use the launchd/systemd templates in
[`deploy/`](../deploy/README.md). The scheduler is the only service that needs
to run continuously; the UI is optional.

## Automatic quota pings and reset notifications

Auto Ping is stored as managed schedule rules. Configure it from the Web
**Schedules** panel, the macOS app, or the scheduler CLI:

```bash
fluxion-scheduler --set-autoping codex both
fluxion-scheduler --get-autoping
```

The per-provider mode (`off`, `5h`, `7d`, `both`) is the **monitor scope** —
which windows to watch for a reset. The actions on a detected reset are global:
a notification (sent to whichever `*_NOTIFY_REFRESH` channels are enabled) and,
when `FLUXION_AUTOPING_ENABLED` is on (default off), an Auto Ping. So a monitored
reset always notifies and only pings if you opted in — a monitor never spends
quota uninvited.

Codex and Antigravity 5h windows only anchor after several pings on a single
session, so Auto Ping runs a short burst — resuming one session — until the
window anchors or `FLUXION_AUTOPING_MAX_ATTEMPTS` (default 12) is reached. It
gives up with a single notification only at that cap, so a run that anchors a
ping or two over the typical count stays silent. Antigravity's Gemini and
External Models pools are anchored separately, each pinged with its own model.

Reset notification destinations remain deployment settings:

```env
# Notify configured Slack, Telegram, QQ, WeChat, LINE, or Feishu destinations after quota refresh.
FLUXION_MENU_SLACK_NOTIFY_REFRESH=false
FLUXION_MENU_TELEGRAM_NOTIFY_REFRESH=true
FLUXION_MENU_QQBOT_NOTIFY_REFRESH=false
FLUXION_MENU_WECHAT_NOTIFY_REFRESH=false
FLUXION_MENU_LINE_NOTIFY_REFRESH=false
FLUXION_MENU_FEISHU_NOTIFY_REFRESH=false
```

Reset notifications are sent when a `quota_refresh` rule actually fires.
Enabling Auto Ping creates matching managed quota-refresh rules in
`data/schedules.jsonl`. They are visible in the schedule list but must be
changed through the Auto Ping controls.

WeChat notifications require a saved QR-login credential and a context token
from a prior inbound message. `FLUXION_WECHAT_ALLOWED_USERS` limits recipients;
when it is empty, all users with a saved context token are notified. That
context token expires after WeChat's observed delivery window, so recipients
may need to message the bot again before proactive notifications can reach
them.

LINE notifications use `FLUXION_LINE_ALLOWED_USERS` as the recipient list and
require `LINE_CHANNEL_ACCESS_TOKEN`.

## Triggers and actions

A rule pairs one **trigger** with one **action**, guarded by a **policy**.

**Triggers**

| Type | Fires when | Key fields |
| --- | --- | --- |
| `cron` | A 5-field cron expression matches (in `timezone`) | `cron`, `timezone` |
| `quota_refresh` | A provider window resets — `resets_at` jumps to a new window, or `used_percent` drops off a cliff | `provider`, `window_key` |

Cron supports `*`, lists (`a,b`), ranges (`a-b`), and steps (`*/n`). Day-of-week
is `0`–`7` with `0`/`7` = Sunday. `window_key` is one of `7d`, `5h`, `prompt`,
`flow` (matching the Model quota panel).

**Actions**

| Type | Does |
| --- | --- |
| `subagent` | Runs a real sub-agent task with your prompt |
| `ping` | Fires a minimal read-only "say hello" — enough to open a quota window |

For a `quota_refresh` trigger with `agent: auto`, the action targets the
provider whose quota just reset.

## Safety rails (policy)

Because a quota-edge action consumes quota itself, three rails prevent runaway
firing:

- **Edge detection.** A refresh fires exactly once per reset — the daemon
  re-baselines the observed window every tick, so a sustained low level does
  not re-trigger.
- **`cooldown_sec`.** Minimum gap between two fires of the same rule. Managed
  Auto Ping rules use 10 minutes; different provider windows remain independent.
- **`max_runs_per_day`.** Hard daily cap per rule.
- **`catch_up`** (cron only): `skip` ignores an occurrence missed while the
  daemon was down; `run_once` runs it once on the next tick.

## Managing rules

**Web UI (recommended):** open `fluxion-web`, click the **⏱ Schedules** button
(bottom-left), and create/edit/enable/delete rules. The form switches fields by
trigger type, and a "Recent fires" list shows history.

**By hand:** edit `data/schedules.jsonl` directly (one rule per line). The
daemon watches the file's mtime and reloads on change — no restart needed.

## Data files

| File | Owner | Contents |
| --- | --- | --- |
| `data/schedules.jsonl` | web UI / you | Rule definitions (one JSON object per line) |
| `data/scheduler_state.json` | daemon | Per-rule runtime state (last fire, daily count, quota baseline) |
| `data/schedule_runs.jsonl` | daemon | Fire history; `task_id` links into `data/tasks.jsonl` |

Definition and state are written atomically (temp + rename), so the daemon
never reads a half-written file.

## Rule schema

```jsonc
{
  "id": "sch_a1b2c3d4",          // assigned on create
  "name": "Codex weekly kickoff",
  "enabled": true,
  "trigger": {
    "type": "quota_refresh",     // "cron" | "quota_refresh"
    "cron": "",                  // cron only, e.g. "0 9 * * 1"
    "timezone": "UTC",           // cron only
    "provider": "codex",         // quota_refresh only
    "window_key": "7d"           // quota_refresh only
  },
  "action": {
    "type": "ping",              // "subagent" | "ping"
    "agent": "auto",             // auto | codex | claude | antigravity
    "prompt": "",
    "project": null,
    "workspace": ".",
    "profile": "inspect",
    "mode": "read-only",         // read-only | workspace-write
    "thread": "scheduler",
    "task_name": null
  },
  "policy": {
    "cooldown_sec": 7200,
    "catch_up": "skip",          // "skip" | "run_once"
    "max_runs_per_day": 24,
    "jitter_sec": 0
  }
}
```

## Caveat: cross-process workspace writes

Each surface that executes tasks (messaging gateway, scheduler, `fluxion-sub`)
builds its own in-process workspace write lock. That lock does **not** span
processes. If you run the scheduler and the messaging gateway concurrently and
both write to the same workspace, their writes are not serialized. Keep scheduled
actions read-only/`ping`, or give write-heavy schedules a dedicated workspace.
