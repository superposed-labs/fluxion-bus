# Security Policy

Fluxion is a **local-first** agent gateway. It runs on your own machine, drives
local CLI executors (Codex, Claude, Antigravity), and reads locally-stored
credentials and histories. It is designed to keep secrets and task data on your
host. This document explains how to report a vulnerability and the trust
boundaries you should understand before exposing Fluxion beyond `localhost`.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub Security Advisories:

1. Go to <https://github.com/superposed-labs/fluxion-bus/security/advisories/new>.
2. Describe the issue, the affected component, and a reproduction if possible.

We aim to acknowledge a report within a few days and to keep you updated as we
investigate. Please give us a reasonable window to ship a fix before any public
disclosure. There is currently no paid bounty program.

## Supported versions

Fluxion ships from `main` as a rolling release. Security fixes land on `main`;
there is no separate maintenance branch. Always run the latest commit.

## What Fluxion touches

Understanding these helps you scope a report and configure Fluxion safely:

- **Messaging tokens** — Slack, Telegram, WeChat, and LINE credentials are read
  from your environment / `.env` and used to receive and reply to tasks.
- **Provider quota credentials** — the quota panel may read a Claude Code OAuth
  token (from a file, env, or — opt-in — the macOS Keychain) and a Codex usage
  token to query live 5h/weekly windows.
- **Local agent histories** — usage statistics parse Claude/Codex/Antigravity
  history files on disk. This is read-only and stays local.
- **Workspaces** — executors run against project directories you authorize. In
  `--write` / `workspace-write` mode an executor can modify files in the
  authorized workspace.
- **Web console** — `fluxion-web` serves the browser control surface.

## Trust boundaries and hardening

- **The Web UI has no auth on localhost by design.** Binding to a non-loopback
  address (e.g. `0.0.0.0` for LAN/VPS) requires a token (`FLUXION_UI_TOKEN`):
  `fluxion-web` refuses to start without one. See `.env.example` and
  [docs/configuration.md](docs/configuration.md). Treat the console as
  sensitive — it exposes task summaries, logs, and artifacts.
- **The messaging gateway executes tasks from chat.** The per-channel allow-lists
  (e.g. `FLUXION_TELEGRAM_ALLOWED_USERS`, `FLUXION_LINE_ALLOWED_USERS`) are
  fail-closed: an unset/empty list rejects everyone, so you must name the users
  who may drive the bot. They work alongside the MCP/CLI authorization policy.
- **Keep secrets out of git.** `.env`, `data/`, caches, and `.fluxion_inbox/`
  are gitignored. Never commit real tokens; `.env.example` is the only env file
  that belongs in the repo, and it must contain placeholders only.
- **Executors default to write-enabled, not read-only.** This is a deliberate
  usability choice — the gateway exists to do work — but know what it grants:
  - Codex runs with `FLUXION_CODEX_SANDBOX_MODE=workspace-write` by default,
    so tasks can create and edit files (network stays restricted). Set it to
    `read-only` to require explicit opt-in for writes; `danger-full-access`
    (or `FLUXION_CODEX_BYPASS_SANDBOX=true`) removes the sandbox entirely.
  - Antigravity runs without `--sandbox` by default
    (`FLUXION_ANTIGRAVITY_SANDBOX=false`); set it to `true` to sandbox the run.
    Keep `FLUXION_ANTIGRAVITY_DANGEROUSLY_SKIP_PERMISSIONS=false` (the default).

  The blast radius is bounded on two sides: writes are confined to the run
  workspace, which is itself restricted to your registered project roots (an
  unauthorized path is rejected at run time), and the fail-closed allow-lists
  mean only the users you name can submit tasks in the first place. Tighten the
  defaults above if you run untrusted prompts or share the bot more widely.

## Known non-issues

- Running the Web UI unauthenticated on `localhost` is the intended default for
  single-user local use; it is not a vulnerability on its own.
- Fluxion reading your own local credential/history files on your own machine is
  expected behavior, not data exfiltration.
