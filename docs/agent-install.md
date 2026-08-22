# Agent-Assisted Installation

This document is written for an AI agent (Claude Code, Codex, Antigravity, or
similar) that is installing Fluxion's backend CLI and registering the MCP
server on its user's machine. If you are a human reading this, the manual path
in [Install and Verify](../README.md#install-and-verify) covers the same
backend result.

For normal macOS desktop use on Apple Silicon (M-series chips), the easiest path is Homebrew — `brew install --cask superposed-labs/tap/fluxion` — or the prebuilt `Fluxion.dmg` from the [latest GitHub Release](https://github.com/superposed-labs/fluxion-bus/releases/latest) (Intel Mac users should build from source or use the CLI installation instead). The app installs or repairs its backend from a bundled source snapshot and dependency wheels, so it needs no git, Node, Xcode Command Line Tools, or network access on the user's machine — only Python 3.12+. Use this agent-assisted document when the user wants MCP registration, CLI verification, or a non-desktop install.

The current prebuilt DMG is unsigned and not notarized. The Homebrew cask
strips the quarantine flag automatically, so the steps below apply only to a
direct DMG download. If macOS blocks the app on first launch, tell the user to
confirm they downloaded it from the official
[GitHub Releases](https://github.com/superposed-labs/fluxion-bus/releases) and verified `SHA256SUMS`, then either:

- Open **System Settings -> Privacy & Security**, find the Fluxion warning near
  the bottom, click **Open Anyway**, and launch Fluxion again.
- Or, for users comfortable with Terminal:

  ```bash
  xattr -dr com.apple.quarantine /Applications/Fluxion.app
  ```

## Ground rules

- Follow the steps in order. Every step is idempotent and safe to re-run.
- When a step fails, stop, report the failing step and the exact error output
  to the user, and wait for their decision. Do not switch to an alternative
  installation strategy on your own.
- Never delete or overwrite an existing `~/.local/share/fluxion/.env` or
  `~/.local/share/fluxion/data/`. The installer preserves both on update.
- Do not start long-running services (`fluxion-gateway`, `fluxion-scheduler`,
  `fluxion-web`) unless the user asks for them. The desktop app starts them
  when needed.
- Use absolute paths in every configuration you write. Some MCP clients do not
  expand `~`.

## Step 1 — Collect facts

Determine and remember:

1. **Operating system.** `uname -s`. Fluxion supports macOS; Linux is expected
   to work but is not fully verified. On Windows without WSL, stop and tell
   the user that the installer requires a POSIX shell.
2. **Workspace.** The absolute path of the project directory the user wants
   Fluxion to operate on. Default to the current working directory unless the
   user named another path.
3. **Primary agent client.** The MCP client to register Fluxion with. Default
   to the client you are running inside (Claude Code, Codex, or Antigravity).
   If the user wants additional clients, repeat Step 5 for each.

## Step 2 — Check prerequisites

```bash
python3 --version          # needs 3.12+, 3.13 recommended
git --version
command -v codex claude agy
```

Hard requirements — stop and report if unmet:

- `git` must be present; this agent-assisted path distributes Fluxion as a git
  checkout and cannot fetch or update it otherwise. (The Release DMG does not
  need git — it installs from a bundled source snapshot.) If missing on macOS
  and Homebrew is available, `brew install git` is the preferred remedy because
  it avoids requiring Xcode Command Line Tools for normal desktop users. If
  Homebrew is not available, report the missing prerequisite and wait for the
  user's decision.
- At least one executor CLI (`codex`, `claude`, or `agy`) must be present.
  If none is found, stop and report; Fluxion cannot run tasks without one.
  On macOS, the ChatGPT desktop app counts: it bundles a `codex` CLI at
  `/Applications/ChatGPT.app/Contents/Resources/codex` that Fluxion detects even
  when a GUI-clean `PATH` hides it. The legacy `Codex.app` bundle path is also
  supported, so a standalone `codex` install is optional.
- If `python3` is older than 3.12, look for a newer interpreter (for example
  `python3.13`). If one exists, pass it to the installer via
  `FLUXION_PYTHON=<path>`. If not, stop and report.

Node, `swiftc`, and `codesign` are not prerequisites for this path. The macOS
app and its bundled Web console static assets are built in release automation
for desktop users. Developers who want to build those components locally should
follow `desktop/README.md`.

## Step 3 — Run the installer

```bash
curl -fsSL https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/scripts/install.sh \
  | FLUXION_WORKSPACE=<absolute-workspace-path> bash -s -- --no-desktop
```

The installer clones or updates the managed checkout at
`~/.local/share/fluxion`, creates its virtualenv, links commands into
`~/.local/bin`, and on a fresh install writes
`~/.local/share/fluxion/.env` with the given workspace as the first
authorized workspace. This agent-assisted path passes `--no-desktop`; desktop users on Apple Silicon should install the prebuilt Release DMG instead of building the macOS app on their own machine (Intel Mac users should build the macOS app locally).

Re-running the same command later updates Fluxion in place.

## Step 4 — Ensure PATH

Check that `~/.local/bin` is on `PATH`:

```bash
command -v fluxion || echo "not on PATH"
```

If missing, append the export line to the user's shell profile
(`~/.zshrc` for zsh, `~/.bashrc` for bash) and tell the user you did so:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Step 5 — Register the MCP server

Register `fluxion-mcp` with the user's primary agent. For the managed install,
`<fluxion-repo>` is `$HOME/.local/share/fluxion`. The canonical reference for
all clients is [docs/mcp.md](mcp.md#client-setup).

**Claude Code:**

```bash
claude mcp add -s user \
  -e FLUXION_ENV_FILE="$HOME/.local/share/fluxion/.env" \
  -e FLUXION_WORKSPACE_ROOT="$HOME/.local/share/fluxion" \
  -e FLUXION_DATA_DIR="$HOME/.local/share/fluxion/data" \
  fluxion -- "$HOME/.local/share/fluxion/.venv/bin/fluxion-mcp"
```

Confirm with `claude mcp list`.

**Codex** — merge into `~/.codex/config.toml`, replacing `/Users/<user>` with
the real home directory (do not remove existing entries):

```toml
[mcp_servers.fluxion]
command = "/Users/<user>/.local/share/fluxion/.venv/bin/fluxion-mcp"
args = []
startup_timeout_sec = 120

[mcp_servers.fluxion.env]
FLUXION_ENV_FILE = "/Users/<user>/.local/share/fluxion/.env"
FLUXION_WORKSPACE_ROOT = "/Users/<user>/.local/share/fluxion"
FLUXION_DATA_DIR = "/Users/<user>/.local/share/fluxion/data"
```

**Antigravity** — merge into `~/.gemini/antigravity/mcp_config.json`
(or `~/.gemini/config/mcp_config.json`, whichever exists), using the same
absolute paths:

```json
{
  "mcpServers": {
    "fluxion": {
      "command": "/Users/<user>/.local/share/fluxion/.venv/bin/fluxion-mcp",
      "args": [],
      "env": {
        "FLUXION_ENV_FILE": "/Users/<user>/.local/share/fluxion/.env",
        "FLUXION_WORKSPACE_ROOT": "/Users/<user>/.local/share/fluxion",
        "FLUXION_DATA_DIR": "/Users/<user>/.local/share/fluxion/data"
      }
    }
  }
}
```

When editing an existing config file, add the `fluxion` entry without
disturbing other servers.

This step registers the Fluxion MCP server only. It does not install Fluxion's
native Codex roles — and on Codex 0.149 and later it does not need to, because
that integration no longer works. Codex stopped letting an agent role choose its
own `model_provider`, so `spawn_agent` with `fluxion_worker` inherits the parent
session's provider and never reaches the Provider Gateway, silently. Fluxion
refuses to install it on those versions; see
[Client: Codex](provider-gateway.md#client-codex) for the detail and the
evidence. On Codex 0.148 and earlier the separate
[Codex Integration installation](provider-gateway.md#client-codex) is still
available — in the macOS app, **Preferences → Provider Routing → Codex
Integration → Install / Repair**.

The MCP registration you just made is the supported way to run Fluxion agents
from Codex on any version: `mcp__fluxion__run_subagent` launches a local agent
directly and does not depend on Codex's provider resolution.

MCP tools load when the client starts a session, so the registration cannot be
exercised from the current session. Verification of the registration itself
happens in Step 7.

## Step 6 — Verify the installation

```bash
FLUXION_ENV_FILE="$HOME/.local/share/fluxion/.env" fluxion doctor
```

Every check should pass. Then confirm the execution path with a minimal
read-only task (this calls the detected executor once):

```bash
FLUXION_ENV_FILE="$HOME/.local/share/fluxion/.env" \
  fluxion run --workspace <absolute-workspace-path> "Reply with the single word OK."
```

If `doctor` reports a missing or unauthenticated executor, report it to the
user with the provider's login command (`codex login`, `claude` /
`claude setup-token`, or `agy` sign-in) rather than attempting to
authenticate on their behalf.

Then record the Web console static asset status. This is a status check for the
Step 7 report, not a pass/fail gate. Missing static assets affect only the
browser console; CLI and MCP can still work:

```bash
ls "$HOME/.local/share/fluxion/src/fluxion/web/static/index.html"   # Web console build
```

## Step 7 — Report to the user

Report using exactly this template — one status line per component, then a
single next step. Do not add sections, option lists, or questions:

```text
✅ Fluxion installed at ~/.local/share/fluxion
   Authorized workspace: <absolute-workspace-path>
✅ CLI — `fluxion doctor` and the test run passed
✅ MCP — registered with <client>
<✅|⚠️> Web console — <static assets present | static assets missing: use the macOS DMG or build the web frontend>
ℹ️ macOS desktop app — `brew install --cask superposed-labs/tap/fluxion` or the prebuilt Fluxion.dmg (M-series), or build from source (Intel)

Next step: start a new <client> session so the fluxion MCP tools load, then
ask for `list_projects` via Fluxion to confirm.
```

Rules for filling it in:

- Every ⚠️ line carries exactly one remedy, phrased as: fix the reported error
  or use the named install path, then re-run the same install command from
  Step 3 when applicable. No extra alternatives or explanations.
- If `fluxion doctor` or the test run failed, mark that line ⚠️ and include
  the failing output verbatim below the template.
- End with the single next step shown above. Mention other services
  (`fluxion-gateway`, `fluxion-scheduler`, `fluxion-web`) only if the user
  asks; never start them.

## Troubleshooting

- `managed checkout has local tracked changes` — the user modified
  `~/.local/share/fluxion` directly. Report it; do not discard their changes.
- `No .env file found` from `doctor` — the command was run without
  `FLUXION_ENV_FILE` and outside the checkout. Re-run with the variable set
  as shown in Step 6.
- Executor `not found` in `doctor` — the executor CLI is not on the `PATH`
  seen by Fluxion. Check `command -v` output and the user's shell profile.
- Anything else — collect the failing command, its full output, and
  `fluxion doctor` output, and present them to the user.
