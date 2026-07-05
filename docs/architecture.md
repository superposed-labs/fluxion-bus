# Architecture

Fluxion is a local-first agent gateway with several independent entrypoints.
They share one gateway/router and persist state to the same local `data/`
directory.

## Full system

```text
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────┐
│ Messaging users      │   │ MCP client           │   │ Shell/agent  │
│ Slack/TG/WX/LINE/QQ/FS│  │ Claude/Codex/Agy/... │   │              │
└──────────┬───────────┘   └──────────┬───────────┘   └──────┬───────┘
           │                          │                      │
           ▼                          ▼                      ▼
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────┐
│ Channel adapters     │   │ MCP server           │   │ Sub-agent CLI│
│ fluxion-gateway      │   │ fluxion-mcp          │   │ fluxion-sub  │
└──────────┬───────────┘   └──────────┬───────────┘   └──────┬───────┘
           │                          │                      │
           └─────────────────┬────────┴──────────────────────┘
                             ▼
                    ┌────────────────┐
                    │ Gateway/Router │
                    │ core/          │
                    └───────┬────────┘
                            │
           ┌────────────────┼────────────────┐
           ▼                ▼                ▼
        Codex       Claude Code CLI     Antigravity
           │                │                │
           └────────────────┼────────────────┘
                            ▼
                    ┌────────────────┐
                    │ data/          │
                    │ tasks.jsonl    │
                    │ sessions.jsonl │
                    │ logs/          │
                    └───────┬────────┘
                            │ reads
                            ▼
                    ┌────────────────┐
                    │ Web UI         │
                    │ fluxion-web    │
                    └────────────────┘
```

## Surfaces

| Surface | Entrypoint | Responsibility |
| --- | --- | --- |
| MCP server | `fluxion-mcp` | Exposes sub-agent delegation tools over stdio. |
| Sub-agent CLI | `fluxion-sub` | Runs one executor task from a shell or another agent. |
| Messaging gateway | `fluxion-gateway` | Hosts Slack, Telegram, WeChat, LINE, QQ, and Feishu channel adapters. |
| Web observation deck | `fluxion-web` | Reads task history, sessions, logs, usage, and manages scheduler rules. |
| Scheduler | `fluxion-scheduler` | Fires cron/quota-reset tasks, automatic pings, and notifications. |
| macOS menu bar app | `desktop/Fluxion.app` | Displays quota and controls companion services. |

The Web UI does not directly dispatch ordinary tasks. It reads shared state and
writes scheduler rule definitions; `fluxion-scheduler` acts on those rules.

## Callers and executors

Fluxion separates callers from executors. A caller submits and supervises a
task; an executor performs it.

- Callers: MCP clients, messaging channels, shell scripts, or another agent.
- Executors: Codex, Claude Code CLI, and Antigravity.

The same product can play both roles. For example, Claude Code can remain the
primary caller while Fluxion delegates a subtask to Codex.

## Shared state

All surfaces use the same configured data directory:

| Path | Contents |
| --- | --- |
| `data/tasks.jsonl` | Task lifecycle and result metadata |
| `data/sessions.jsonl` | Executor-native session mappings |
| `data/logs/` | Executor and service logs |
| `data/change_sets/` | Recoverable text-file changes from workspace-writing runs |
| `data/schedules.jsonl` | Scheduler rule definitions |
| `data/usage_cache.json` | Shared provider-quota snapshot |

## Project layout

```text
src/fluxion/
  gateway.py              # Messaging gateway entrypoint
  cli/                    # Console entrypoints: main (fluxion),
                          #   sub (fluxion-sub), detect, usage
  mcp_server.py           # fluxion-mcp entrypoint
  web/                    # FastAPI UI server
  scheduler/              # Cron / quota-refresh daemon
  executors/              # Codex / Claude / Antigravity adapters
  channels/slack/         # Slack adapter
  channels/telegram/      # Telegram adapter
  channels/wechat/        # WeChat iLink adapter
  channels/line/          # LINE Messaging API adapter
  channels/qqbot/         # QQ Bot adapter
  channels/feishu/        # Feishu adapter
  core/                   # Gateway engine, router, session manager, storage
  config/                 # Settings and environment (.env) loading
web/                      # React / Vite frontend source
desktop/                  # Native macOS menu bar app
deploy/                   # launchd / systemd service templates
data/                     # Local runtime state
```

## Concurrency caveat

Each executing surface builds its own in-process workspace-write lock. That
lock does not span processes. If the scheduler and messaging gateway write to
the same workspace concurrently, their writes are not serialized. Keep
scheduled actions read-only or give write-heavy schedules a dedicated
workspace.
