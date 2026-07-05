# Fluxion Bus

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

https://github.com/user-attachments/assets/7ff8be14-f4e6-4bd9-9ceb-bbf425fafba3

_Illustrative demo with staged data, not a live recording._

Fluxion Bus is the open-source project behind the Fluxion macOS app and local
agent gateway.

**Fluxion** lets your primary AI agent delegate scoped tasks across **Codex**,
**Claude Code**, and **Antigravity** through one local MCP server.

Stay inside your current agent while Fluxion routes work to another provider,
preserves sessions, reports progress and results, and records file changes for
review or recovery.

Fluxion also reads provider-reported quota windows, detects and notifies you
when the provider resets them, and can automatically make a minimal Agent call
after a reset to start the next rolling window immediately.

Quota and usage data are not limited to tasks delegated through Fluxion.
Fluxion reads them directly from provider APIs, local agent services, or local
agent histories; it does not calculate provider quota from Fluxion task
records.

Local-first, single-tenant, and self-hosted. No Fluxion account or SaaS
dependency; default exposure is `127.0.0.1`.

## Why Fluxion

### Cross-provider delegation without leaving your primary agent

```text
┌────────────────────────────────────────────┐
│ Primary agent                              │
│ Codex / Claude Code / Antigravity          │
└─────────────────────┬──────────────────────┘
                      │ MCP: delegate scoped subtask
                      ▼
              ┌──────────────────┐
              │ Fluxion MCP      │
              │ route + supervise│
              └────────┬─────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       Codex      Claude Code   Antigravity
          │            │            │
          └────────────┼────────────┘
                       ▼
       status / result / changed files / revert
                       │
                       ▼
                 Primary agent
```

- Route each subtask to a different provider.
- Continue executor-native sessions across repeated calls.
- Choose read-only investigation or explicitly authorized edits.
- Inspect async status, logs, artifacts, and changed files.
- Revert recoverable text-file changes after review.

### Turn quota resets into usable windows

```text
 Claude quota     Codex quota     Antigravity quota
      └───────────────┬──────────────────┘
                      ▼
             ┌─────────────────┐
             │ Fluxion quota   │
             │ monitor + sched │
             └────────┬────────┘
                      │
         ┌────────────┼──────────────┐
         ▼            ▼              ▼
      Web UI      macOS app     Reset detected
                                      │
                               Auto Ping + notify
                          Slack/Telegram/WeChat/LINE/QQ/Feishu
```

- See remaining quota and reset countdowns across providers.
- Monitor provider/account quota, including usage made outside Fluxion.
- Use the browser-based console on macOS or Linux.
- Use the native macOS app for menu bar quota, service controls, and setup.
- Detect provider-side quota resets.
- Automatically make a minimal Agent call after a detected reset to start the
  next rolling window.
- Send quota-reset notifications through Slack, Telegram, WeChat, LINE, QQ, or
  Feishu.

### Control local agents remotely

Send tasks from Slack, Telegram, WeChat, LINE, QQ, or Feishu while away from
your computer. Fluxion routes the message to a local Codex, Claude Code, or
Antigravity executor, then returns progress updates and the final result in the
same conversation.

```text
Phone / remote device
Slack/Telegram/WeChat/LINE/QQ/Feishu
          │
          ▼
 Fluxion messaging gateway
          │
          ▼
Codex / Claude / Antigravity
          │
          ▼
 progress updates + final result
```

Remote conversations preserve their executor session, so follow-up messages can
continue the same task context. Users can also inspect recent tasks, check
gateway status, reset a conversation, or cancel queued/running tasks through
channel control commands.

WeChat uses iLink QR-code login. Bind the account once, enable the channel, and
start the same messaging gateway used by the other messaging channels.

## Platform Support

| Capability | macOS | Linux | Windows |
| --- | --- | --- | --- |
| MCP cross-provider delegation | Supported | Expected, not verified | Not verified |
| Web quota console | Supported | Expected, not verified | Not verified |
| Scheduler auto-ping and notifications | Supported | Expected, not verified | Not verified |
| Native macOS app | macOS 12+ | Not available | Not available |

Linux support is expected for non-native features based on the implementation,
but has not yet been manually verified.

The menu bar app runs on macOS 12 or newer; its Launch at Login toggle
requires macOS 13+ and is disabled on macOS 12.

Provider quota probes depend on compatible local credentials or services.
Antigravity live quota, for example, requires its local sidecar to be running.
The displayed quota comes from those provider or agent sources, not from a
counter of tasks routed through Fluxion.

## Install and Verify

Requirements:

- At least one installed and authenticated executor CLI: `codex`, `claude`, or
  `agy`
  - Codex: either the standalone CLI, or the Codex desktop app — its bundled
    CLI is detected automatically on macOS, and its login satisfies auth.
- Python 3.12+ (Python 3.13 recommended) for CLI/backend installs. The macOS
  desktop app can install `python@3.13` through Homebrew when needed.
- Node 18+ only when rebuilding the Web console frontend locally.

### macOS desktop app (recommended)

For macOS users on Apple Silicon (M-series chips), install the prebuilt `Fluxion.app` from the [latest GitHub Release](https://github.com/superposed-labs/fluxion-bus/releases/latest) DMG, drag it into `/Applications`, and open it. *(Note: The prebuilt Release DMG is targeted at Apple Silicon. Intel Mac users should build from source or use the CLI installation).*

The current prebuilt DMG is unsigned and not notarized. On first launch, macOS
Gatekeeper may block it because Apple cannot verify the developer. If you
downloaded it from the official [GitHub Releases](https://github.com/superposed-labs/fluxion-bus/releases) and verified `SHA256SUMS`, open
it without Terminal by trying once, then going to **System Settings -> Privacy
& Security**, finding the Fluxion warning near the bottom, and clicking **Open
Anyway** before launching it again. If you're comfortable with the command
line, you can instead remove the quarantine flag directly:

```bash
xattr -dr com.apple.quarantine /Applications/Fluxion.app
```

On first launch, Fluxion uses `~/.local/share/fluxion` as the managed backend path and offers **Install / Repair**. The app then installs the backend from the source snapshot and dependency wheels bundled inside the app, creates `.venv`, initializes `.env`, and starts the local services — no git, network access, Xcode Command Line Tools, or local Node build required.

If Python 3.12+ is not already available and Homebrew is installed, the
installer uses Homebrew to install `python@3.13`; without Homebrew, the app
points you to the python.org installer before setup starts. Executor CLIs such
as `codex`, `claude`, or `agy` still need to be installed and authenticated
separately.

### Let your agent configure CLI/MCP

For CLI-first use, MCP registration, or non-desktop installs, the agent you
already use can run the backend installation end to end: prerequisites,
installer, MCP registration for your client, and verification. Paste this into
Claude Code, Codex, or Antigravity from the project directory you want Fluxion
to work on:

```text
Read https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/docs/agent-install.md
and follow it to install and configure Fluxion on this machine. Use the current
directory as the first authorized workspace, register the MCP server with the
client you are running in, then run the verification steps and report the results.
```

The agent follows [docs/agent-install.md](docs/agent-install.md), which wraps
the same installer used below. It finishes with a per-component status report
covering the backend CLI, MCP registration, and Web console static assets. The
macOS desktop app is distributed separately through the Release DMG.

### Manual install

Install or update Fluxion for the current user:

```bash
curl -fsSL https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/scripts/install.sh \
  | bash -s -- --no-desktop
```

The installer uses `~/.local/share/fluxion`, links commands into
`~/.local/bin`, and installs the CLI, Gateway, and MCP commands. The prebuilt
macOS app is distributed through the Release DMG; use the source installer for
backend/CLI setup and development workflows. Run the same command again to
update while preserving `.env` and `data/`.

By default, the directory where the install command is run becomes the first
authorized workspace. Override it when needed:

```bash
curl -fsSL https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/scripts/install.sh \
  | FLUXION_WORKSPACE=/absolute/path/to/project bash -s -- --no-desktop
```

Uninstall while preserving configuration and data in a timestamped backup:

```bash
~/.local/share/fluxion/scripts/uninstall.sh
```

Use `--purge` only when the configuration and runtime data should also be
deleted.

### Development install

For a source checkout used for Fluxion development:

```bash
git clone git@github.com:superposed-labs/fluxion-bus.git
cd fluxion-bus

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Detect an executor and create a minimal .env with real paths.
fluxion init

# Check configuration, executor availability, and workspace authorization.
fluxion doctor

# Verify the local execution path with a read-only task.
fluxion run "Summarize this project and explain how to run its tests."
```

Allow edits explicitly:

```bash
fluxion run --write "Fix the failing tests."
```

Initialize Fluxion for another workspace:

```bash
fluxion init --workspace /absolute/path/to/project
fluxion doctor --workspace /absolute/path/to/project
fluxion run --workspace /absolute/path/to/project "Inspect this project."
```

`fluxion init` creates a deliberately small `.env`; [`.env.example`](.env.example)
mirrors that minimal shape for manual setup. For advanced manual configuration,
see [`.env.advanced.example`](.env.advanced.example),
[Configuration](docs/configuration.md), and [`scripts/install.sh`](scripts/install.sh).

## MCP Delegation Quick Start

Register `fluxion-mcp` with the primary agent where you already work. Complete
client-specific examples for Claude Code, Codex, and Antigravity are in the
[MCP reference](docs/mcp.md#client-setup).

Example Claude Code registration:

```bash
claude mcp add -s user \
  -e FLUXION_ENV_FILE=<fluxion-repo>/.env \
  -e FLUXION_WORKSPACE_ROOT=<fluxion-repo> \
  -e FLUXION_DATA_DIR=<fluxion-repo>/data \
  fluxion -- <fluxion-repo>/.venv/bin/fluxion-mcp
```

The primary agent can then delegate a focused subtask:

```json
{
  "agent": "claude",
  "project": "web",
  "profile": "inspect",
  "mode": "read-only",
  "prompt": "Investigate why the login form is submitting twice."
}
```

Fluxion returns a `run_id`. The primary agent can inspect status, fetch the
result, cancel the run, review changed files, or revert a reviewed
workspace-writing run through the same MCP server.

For multi-project usage, configure project keys with
`FLUXION_PROJECTS_FILE`; see [Project registry](docs/configuration.md#project-registry).

## Quota Monitoring Quick Start

### Web Console
If you installed the prebuilt app or used the installer, the console is ready. Start it directly:

```bash
fluxion-web                  # http://127.0.0.1:8765
```

*(If running from a Git clone or rebuilding static assets: `cd web && npm install && npm run build && cd ..` before running the command).*

### macOS Menu Bar App
If you downloaded the prebuilt `Fluxion.dmg`, drag it into `/Applications` and open it.

If building the menu bar app from your local source checkout (this compiles natively for your machine's architecture, whether Apple Silicon or Intel):

```bash
./desktop/build.sh
open desktop/Fluxion.app
```

The menu bar app can configure and start quota monitoring, automatic pings,
reset notifications, and companion services. The actual background auto-ping
and notification work is performed by `fluxion-scheduler`, which also runs
without the menu bar app on Linux.

The app can remain inside the repository or be copied to `/Applications`. When
launched outside the repository, it asks the user to select the Fluxion source
checkout and stores that path under `~/Library/Application Support/Fluxion/`.

```bash
fluxion-scheduler
```

See [Quota monitoring](docs/quota-monitoring.md) and
[Scheduler](docs/scheduler.md) for provider sources, configuration, and
always-on deployment.

## Messaging Channels

`fluxion-gateway` accepts remote tasks from Slack, Telegram, WeChat, LINE, QQ,
and Feishu and submits them through the same router used by MCP and the local
CLI. It replies in the same conversation with execution status and the final
result.

```bash
fluxion-gateway
```

See [Configuration](docs/configuration.md#messaging-channel-configuration) for
channel and workspace settings.

## Documentation

- [Architecture](docs/architecture.md) — full system diagram, surfaces, shared
  state, and project layout
- [Agent-assisted installation](docs/agent-install.md) — step-by-step install
  instructions written for an AI agent to execute
- [MCP reference](docs/mcp.md) — client setup, tools, status states, cancel, and
  safe revert flows
- [Quota monitoring](docs/quota-monitoring.md) — provider sources, Web
  console, macOS app, privacy, and notifications
- [macOS app](desktop/README.md) — release packaging, managed backend,
  `/Applications` installation, and development override
- [Usage statistics](docs/usage-statistics.md) — agent-history coverage,
  independence from Fluxion delegation, cost estimates, and Fast-mode
  limitations
- [Scheduler](docs/scheduler.md) — auto-ping, quota-reset triggers, cron rules,
  and deployment
- [Configuration](docs/configuration.md) — executors, authorization, channels,
  Web UI, and environment variables
- [Deployment](deploy/README.md) — launchd and systemd service templates

## License

[Apache License 2.0](LICENSE) — see also [NOTICE](NOTICE).
