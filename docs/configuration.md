# Configuration

## User-level installer

The repository installer creates or updates a managed checkout under
`~/.local/share/fluxion`, links commands into `~/.local/bin`, and preserves an
existing `.env` and `data/` directory:

```bash
curl -fsSL https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/scripts/install.sh | bash
```

Run the same command again to update. Common overrides:

```bash
FLUXION_WORKSPACE=/path/to/first/project \
FLUXION_INSTALL_DIR=~/.local/share/fluxion \
FLUXION_BIN_DIR=~/.local/bin \
bash scripts/install.sh
```

Use `bash scripts/install.sh --help` for all options. Uninstall with
`~/.local/share/fluxion/scripts/uninstall.sh`; it backs up `.env`, `data/`, and
the macOS desktop config unless `--purge` is passed.

## First-launch detection

The desktop app runs a best-effort availability check before starting its
companion services:

- Executor availability checks whether the `codex`, `claude`, or `agy` command
  can be executed.
- Usage availability runs the matching quota probe and is independent from CLI
  availability.

When the corresponding keys are absent, the first check initializes
`FLUXION_DEFAULT_EXECUTOR` from an available CLI and
`FLUXION_USAGE_PROVIDERS` from successful usage probes. Existing values,
including an intentionally empty provider list, are never overwritten.

Run `.venv/bin/python -m fluxion.cli.detect` to refresh
`data/availability.json`, or add `--initialize` to initialize missing settings.

## Environment templates

Use `fluxion init` for normal setup. It writes a small `.env` with the checkout
path, data directory, default executor, and first allowed workspace.

For manual setup, copy [`.env.example`](../.env.example) and replace the
absolute path placeholders. It intentionally contains only the minimum keys
needed to start Fluxion.

Use [`.env.advanced.example`](../.env.advanced.example) as the full reference
when enabling optional executors, workspace authorization policies, messaging
channels, quota panels, scheduler rules, or desktop companion settings.

## Executor configuration

Set `FLUXION_DEFAULT_EXECUTOR` in `.env` to `codex`, `claude`, or
`antigravity`, then configure the matching section below.

### Codex

Default executor. Relevant env keys:

- `FLUXION_CODEX_SKIP_GIT_REPO_CHECK` — add `--skip-git-repo-check`
- `FLUXION_CODEX_SANDBOX_MODE` — `read-only` / `workspace-write` / `danger-full-access`
- `FLUXION_CODEX_BYPASS_SANDBOX` — pass `--dangerously-bypass-approvals-and-sandbox` (highest risk)

### Claude

Pick one of three provider/auth combinations:

**Claude.ai subscription / stored login:**
```env
FLUXION_DEFAULT_EXECUTOR=claude
FLUXION_CLAUDE_PROVIDER=official
FLUXION_CLAUDE_AUTH_MODE=login
```
Ensure `claude` is already authenticated locally via `/login`.

**Anthropic API key:**
```env
FLUXION_DEFAULT_EXECUTOR=claude
FLUXION_CLAUDE_PROVIDER=official
FLUXION_CLAUDE_AUTH_MODE=api_key
FLUXION_CLAUDE_API_KEY=...
```

**Third-party / local gateway:**
```env
FLUXION_DEFAULT_EXECUTOR=claude
FLUXION_CLAUDE_PROVIDER=third_party
FLUXION_CLAUDE_AUTH_MODE=auth_token   # or api_key
FLUXION_CLAUDE_BASE_URL=...
FLUXION_CLAUDE_AUTH_TOKEN=...         # auth_token mode
FLUXION_CLAUDE_API_KEY=...            # api_key mode
FLUXION_CLAUDE_MODEL=<gateway model name>
```

Validation rules enforced at startup:
- `third_party` requires `FLUXION_CLAUDE_BASE_URL`
- `auth_mode=api_key` requires `FLUXION_CLAUDE_API_KEY`
- `auth_mode=auth_token` requires `FLUXION_CLAUDE_AUTH_TOKEN`
- `auth_mode=login` is only valid with `provider=official`

Other Claude keys:
- `FLUXION_CLAUDE_COMMAND` — optional CLI path override
- `FLUXION_CLAUDE_PERMISSION_MODE` — default `acceptEdits`
- `FLUXION_CLAUDE_ALLOWED_TOOLS` — comma-separated allowed tools
- `FLUXION_CLAUDE_MAX_TURNS` — max turns per invocation (0 = CLI default)
- `FLUXION_CLAUDE_USAGE_KEYCHAIN` — opt-in Keychain read for the quota panel
- `FLUXION_CLAUDE_USAGE_AUTO_REFRESH` — opt-in refresh/write-back for quota
  OAuth credentials when the access token expires

### Antigravity

```env
FLUXION_DEFAULT_EXECUTOR=antigravity
FLUXION_ANTIGRAVITY_COMMAND=/path/to/agy   # auto-discovers agy if omitted
```

Fluxion runs `agy --add-dir <workspace>` for each task and resumes sessions
with `--conversation <id>` when a prior session exists. Authenticate `agy`
locally first.

Other Antigravity keys:
- `FLUXION_ANTIGRAVITY_SANDBOX` — pass `--sandbox`. Restricts terminal access; it is *not* a read-only mode, and the agent can still edit files
- `FLUXION_ANTIGRAVITY_DANGEROUSLY_SKIP_PERMISSIONS` — highest risk; auto-approves every tool in every run and every workspace. Usually unnecessary, see below
- `FLUXION_ANTIGRAVITY_PRINT_TIMEOUT_SEC` — timeout passed to `agy --print-timeout`

### How Antigravity gets permission to edit

`agy` has one permission control and it is all-or-nothing: without
`--dangerously-skip-permissions` it auto-approves its read-only tools (Search,
ReadFile, ListDir) and soft-denies `Bash` and `Edit` — every way it could change
code. A soft-denied run exits 0 and prints no answer, so Fluxion detects it and
reports an explicit failure rather than a silent success.

Fluxion passes the flag automatically for a run that was **already authorized to
write**, which requires two explicit decisions to have been made first:

1. the caller asked for `mode="workspace-write"`, and
2. the workspace is a registered project or an allow-listed path — otherwise the
   engine refuses the run before `agy` ever starts.

So sub-agent edit tasks work without setting anything extra. Set the env key
only to auto-approve runs that made neither decision — messaging-channel tasks,
which declare no mode.

`read-only` runs do not receive the flag, but **that is not a sandbox**. agy has
no read-only mode: whether it then declines to edit is its own decision, and
once it has trusted a workspace it stops asking. A read-only run in a
previously used workspace was measured editing a file with no soft-deny at all.
For agy, `mode="read-only"` is a prompt-level instruction — use an executor that
enforces it (Codex, Claude Code) when the guarantee matters.

Change detection and revert capture:
- `FLUXION_CHANGE_DETECTION` — `off` by default; `snapshot` and `force` opt into filesystem snapshots, `fsevents` and `auto` are reserved
- `FLUXION_REVERT_CAPTURE` — `structured` by default; `off` disables structured revert capture, `full` opts into content snapshots

## Messaging channel configuration

`fluxion-gateway` hosts every enabled messaging adapter. Start it once to serve
Slack, Telegram, LINE, QQ, Feishu, and WeChat:

```env
FLUXION_MENU_AUTOSTART_GATEWAY=true
```

This flag controls the complete messaging gateway (all channels), not only Slack.

For manual runs, start the same gateway directly after configuring one or more
channels:

```bash
fluxion-gateway
```

The macOS app exposes the same settings under **Preferences → Messaging**. Set
`FLUXION_SLACK_ENABLED=false` when you want to run only Telegram, WeChat, or
LINE; Slack is enabled by default for backward compatibility.

### Slack

Initialize Slack in this order:

1. Create and install a Slack app with Socket Mode enabled.
2. Add the required bot token scopes and events below.
3. Copy the bot token, app token, and signing secret into `.env`.
4. Optionally restrict access with `FLUXION_SLACK_ALLOWED_USERS`.
5. Start `fluxion-gateway`, or enable `FLUXION_MENU_AUTOSTART_GATEWAY=true` for
   the macOS menu bar app.

#### Slack app setup

To create the Slack app:

1. Open Slack API's app management page and create a new app for the workspace.
2. Enable **Socket Mode**.
3. Create an app-level token with the Socket Mode connection scope, then copy it
   as `SLACK_APP_TOKEN`; the token should start with `xapp-`.
4. Add the bot token scopes listed below under OAuth permissions.
5. Subscribe to the bot events listed below.
6. Install or reinstall the app to the workspace, then copy the bot token as
   `SLACK_BOT_TOKEN`; the token should start with `xoxb-`.
7. Copy the app's signing secret as `SLACK_SIGNING_SECRET`.

Minimum required:

- Enable Socket Mode
- Bot token scopes: `chat:write`, `im:history`, `files:read`, `files:write`
- Bot events: `message.im`
- Install app to workspace

For channel mode, also add:
- Bot scopes: `channels:history`, `groups:history`
- Bot events: `message.channels`, `message.groups`

Required `.env` keys:

```env
FLUXION_SLACK_ENABLED=true
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
FLUXION_SLACK_ALLOWED_USERS=U01234567,U08999999
FLUXION_SCHEDULER_SLACK_CHANNEL=          # optional quota-reset notification target
FLUXION_MENU_SLACK_NOTIFY_REFRESH=false
```

### Telegram

Initialize Telegram in this order:

1. Create a bot with Telegram's `@BotFather`.
2. Copy the bot token into `TELEGRAM_BOT_TOKEN`.
3. Optionally restrict access with numeric Telegram user IDs.
4. Start `fluxion-gateway`, or enable `FLUXION_MENU_AUTOSTART_GATEWAY=true` for
   the macOS menu bar app.

```env
FLUXION_TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=...
FLUXION_TELEGRAM_ALLOWED_USERS=        # comma-separated numeric user IDs
FLUXION_TELEGRAM_DEFAULT_WORKSPACE=    # empty uses FLUXION_WORKSPACE_ROOT
FLUXION_MENU_TELEGRAM_NOTIFY_REFRESH=false
```

Telegram user IDs are numeric. Message a user-info bot, or inspect the gateway
logs after a first inbound message, to find the ID for allowlisting.

#### File and image attachments

Slack, Telegram, LINE, QQ, Feishu, and WeChat downloads are represented as
structured task attachments; their private inbox paths are not part of the
user's prompt or final reply.
JPEG, PNG, GIF, and WebP files are validated and delivered through the selected
executor's native image interface when it has one. On macOS, HEIC and HEIF
uploads are converted with ImageIO into a validated PNG before execution, so a
headless agent does not need shell permission merely to decode an iPhone photo.
The original upload remains in the transient `.fluxion_inbox` directory until
the normal inbox TTL removes it.

Telegram's **photo** mode is normalized by Telegram itself and normally reaches
the bot as JPEG. Sending the same item as a **document** preserves its original
name and format; Fluxion then applies the normalization described above.
Unsupported or non-image files remain generic attachments and are left to the
executor's own file capabilities and permission policy.

Each inbound message may contain up to 8 files, with a 20 MiB limit per file
and 48 MiB combined. Downloads are streamed to disk under those bounds instead
of being buffered without a ceiling. Corrupt images, oversized files, and
excess attachment counts are rejected with a channel reply before an executor
starts.

### LINE

LINE uses the Messaging API to interact with users through a public webhook.
Initialize LINE in this order:

1. Create a LINE Messaging API channel in the LINE Developers Console.
2. Copy the channel secret and long-lived access token into `.env`.
3. Expose the local webhook server to the public internet.
4. Register `https://<your-tunnel-domain>/line/webhook` as the webhook URL.
5. Start `fluxion-gateway`, or enable `FLUXION_MENU_AUTOSTART_GATEWAY=true` for
   the macOS menu bar app.

To create the LINE channel:

1. Open the LINE Developers Console and create or select a provider.
2. Create a **Messaging API** channel under that provider.
3. In the channel's basic settings, copy the **Channel secret**.
4. In the Messaging API settings, issue a long-lived **Channel access token**.
5. Enable webhook usage, then set the webhook URL to
   `https://<your-tunnel-domain>/line/webhook`.
6. Add the bot as a friend from the channel's QR code before sending tasks.

```env
FLUXION_LINE_ENABLED=true
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
FLUXION_LINE_ALLOWED_USERS=            # comma-separated LINE user IDs (e.g., U1234567890abcdef1234567890abcdef)
FLUXION_LINE_DEFAULT_WORKSPACE=        # empty uses FLUXION_WORKSPACE_ROOT
FLUXION_MENU_LINE_NOTIFY_REFRESH=false
```

The LINE adapter starts a local FastAPI webhook server on port `8766` by
default. Use a tunnel such as `cloudflared` or `ngrok`, then register the tunnel
URL in the LINE Developers Console. `FLUXION_LINE_ALLOWED_USERS` uses LINE user
IDs that start with `U`; leave it empty to allow all inbound LINE users.

### QQ

The QQ official bot connects via the QQ open platform and supports single chat
and group @-mentions, text in and out. It offers two transports — **WebSocket**
(recommended) and **webhook** — selected with `FLUXION_QQBOT_TRANSPORT`. The
account must complete 主体认证 (entity verification) before the full bot
developer settings are available.

With WebSocket, Fluxion connects *out* to QQ's gateway, so no public address,
tunnel, or callback configuration is needed — the right fit for a local-first
setup. Initialize QQ in this order:

1. Create a bot in the QQ open platform console (`bot.q.qq.com`).
2. Copy the **AppID** and generate/copy the **AppSecret** into `.env`. The
   AppSecret is shown only once on generation; the **AppSecret** row only has a
   *Generate* button (no copy) — do not confuse it with the **Token** row.
3. Add yourself to the bot's sandbox test list while `FLUXION_QQBOT_SANDBOX=true`.
4. Start `fluxion-gateway`, or enable `FLUXION_MENU_AUTOSTART_GATEWAY=true` for the
   macOS menu bar app. The log shows `QQ bot WebSocket ready` once connected.

```env
FLUXION_QQBOT_ENABLED=true
FLUXION_QQBOT_TRANSPORT=websocket     # or "webhook"
QQBOT_APP_ID=...
QQBOT_CLIENT_SECRET=...                # the AppSecret, not the Token
FLUXION_QQBOT_ALLOWED_USERS=           # comma-separated QQ user openids
FLUXION_QQBOT_SANDBOX=false            # true while testing with whitelisted accounts
FLUXION_QQBOT_ALLOW_GROUP_CHAT=true    # false to ignore group @-mentions
FLUXION_QQBOT_DEFAULT_WORKSPACE=       # empty uses FLUXION_WORKSPACE_ROOT
```

The app access token is fetched and refreshed automatically; replies are sent as
passive replies that stay inside QQ's free messaging quota.
`FLUXION_QQBOT_ALLOWED_USERS` is fail-closed — leave it empty and the bot denies
everyone, surfacing each sender's openid so you can add it.

QQ has separate limits for passive replies and active pushes. Fluxion task
replies use the inbound `msg_id` whenever possible, so normal "user sends a
task, Fluxion answers" traffic uses QQ's passive-reply path. Passive replies are
time-limited by QQ (C2C single chat: 60 minutes and up to 5 replies per inbound
message; group replies are much shorter), so Fluxion falls back to an active
send after a conservative local window. Active sends are used for monitor
notifications such as quota resets and credit alerts because there is no
inbound `msg_id` to echo; QQ's active C2C quota is much tighter (for example,
the official docs list 4 active single-chat messages per user per month). Treat
QQ notifications as low-volume, and prefer Slack/Telegram/WeChat/LINE for
frequent alerts. The one-time "approved" notice sent when you allow a pending
QQ user also draws from this active-send quota. See QQ's official send-message limits:
<https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/send-receive/send.html>.

To use the **webhook** transport instead, set `FLUXION_QQBOT_TRANSPORT=webhook`,
expose the local webhook server (port `8767` by default, override with
`FLUXION_QQBOT_PORT`) over a public HTTPS tunnel, and register
`https://<your-tunnel-domain>/qqbot/webhook` as the callback URL. QQ only allows
callback ports `80, 443, 8080, 8443`, so map your tunnel's public port to one of
those. On save, QQ sends an `op:13` validation request the adapter answers
automatically. Note: once an https callback is saved, QQ disables WebSocket for
that bot; and because the webhook signature is keyed on the AppSecret, QQ's
callback-validation service can briefly lag after an AppSecret reset — WebSocket
avoids both issues.

### Feishu (Lark)

The Feishu/Lark bot connects via a **long connection** (WebSocket) using the
official `lark-oapi` SDK, so Fluxion dials *out* to Feishu — no public address,
tunnel, or callback configuration is needed. It supports single (p2p) chat and
group @-mentions, text in and out. Initialize Feishu in this order:

1. Create a **self-built app** in the Feishu/Lark developer console and copy its
   **App ID** and **App Secret** into `.env`.
2. Under 事件订阅 (Event Subscription), choose **使用长连接接收事件** (receive
   events over the long connection) and subscribe to the
   `im.message.receive_v1` event.
3. Grant the app IM permissions to read and send messages (e.g. `im:message`,
   `im:message:send_as_bot`), then publish/enable the app so it can be added to
   chats.
4. Start `fluxion-gateway`, or enable `FLUXION_MENU_AUTOSTART_GATEWAY=true` for the
   macOS menu bar app. The log shows `Feishu channel adapter started` once up.

```env
FLUXION_FEISHU_ENABLED=true
FEISHU_APP_ID=cli_...
FEISHU_APP_SECRET=...
FLUXION_FEISHU_ALLOWED_USERS=          # comma-separated Feishu open_ids
FLUXION_FEISHU_ALLOW_GROUP_CHAT=true   # false to ignore group @-mentions
FLUXION_FEISHU_DEFAULT_WORKSPACE=      # empty uses FLUXION_WORKSPACE_ROOT
```

The tenant access token is fetched and refreshed automatically by the SDK. Task
replies are sent to the originating chat, which works for both single chats and
groups. `FLUXION_FEISHU_ALLOWED_USERS` is fail-closed — leave it empty and the
bot denies everyone, surfacing each sender's open_id so you can add it. Monitor
notifications (quota resets, credit alerts) are delivered to each allowed user's
private chat by open_id.

### WeChat

Initialize WeChat in this order:

1. Enable the channel in `.env`.
2. Bind the WeChat account with QR login.
3. Start `fluxion-gateway`, or enable `FLUXION_MENU_AUTOSTART_GATEWAY=true` for
   the macOS menu bar app.

Bind a WeChat account using iLink QR-code login:

```bash
.venv/bin/python -m fluxion.channels.wechat.wechat_login
```

The macOS app exposes the same flow under **Preferences → WeChat Integration →
Open QR Login**. Credentials are saved locally under the configured data
directory.

If WeChat is enabled before QR login has created
`data/wechat_credentials.json`, the gateway stays running and the WeChat adapter
waits for credentials instead of exiting. A successful QR login or later
credential refresh is hot-loaded. If the iLink session expires, the adapter
waits for a fresh QR login.

```env
FLUXION_WECHAT_ENABLED=true
FLUXION_WECHAT_ALLOWED_USERS=          # comma-separated iLink user IDs; required for inbound tasks
FLUXION_WECHAT_DEFAULT_WORKSPACE=      # empty uses FLUXION_WORKSPACE_ROOT
FLUXION_WECHAT_MESSAGE_MAX_CHARS=4096
FLUXION_WECHAT_TYPING_HEARTBEAT_SEC=8  # 0 disables typing indicators
FLUXION_MENU_WECHAT_NOTIFY_REFRESH=false
```

An empty WeChat allowlist rejects all inbound tasks. If a user is rejected,
Fluxion replies with the iLink user ID to add to
`FLUXION_WECHAT_ALLOWED_USERS`. The gateway hot-reloads this value before the
next WeChat message, so a restart is not required for allowlist edits.
Rejected WeChat users are also recorded in
`data/wechat_pending_users.json`; the macOS Preferences window shows them under
**Messaging → WeChat → Pending Users** with **Allow** and **Remove** actions.
Every channel has the same pending-user flow (`data/<channel>_pending_users.json`
and a matching Preferences section), and each rejection raises a macOS
notification whose **Allow** action approves the sender directly. Approving a
pending user on any channel also sends them a best-effort "approved" notice so
they know they can retry (`python -m fluxion.channels.approval_notify`).

WeChat sends, including task replies and quota-reset notifications, use the
latest iLink `context_token` from that user's inbound message. Public WeChat
iLink documentation does not currently publish a precise token lifetime, but in
practice tokens expire after a limited delivery window (roughly 24 hours in
Fluxion testing, with successful sends observed after more than 19 hours). A
very long-running task or an
overnight proactive notification can therefore fail after the window closes.
Quota-reset notifications require each recipient to message the bot at least
once, and only reach users who messaged recently. To restore delivery, send any
message to the bot; for time-critical alerts, prefer Slack or Telegram. See
[WeChat delivery window](quota-monitoring.md#automatic-pings-and-notifications).

### Per-task workspace override

Override the workspace for a single task (within `FLUXION_ALLOWED_WORKSPACES`):

```
workspace=/absolute/or/relative/path your task text
/workspace /absolute/or/relative/path your task text
```

## Project registry

For MCP and local CLI sub-agent calls, prefer registering projects instead of
asking the primary model to pass absolute paths on every tool call.

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

Point Fluxion at the file:

```env
FLUXION_PROJECTS_FILE=/Users/you/code/fluxion/projects.json
```

Registered project workspaces are treated as allowed roots. MCP clients can
discover them with `list_projects` and call:

```json
{
  "project": "web",
  "prompt": "Inspect the current UI task"
}
```

`workspace` remains available for temporary paths. When used together with
`project`, `workspace="."` means the project root, relative paths resolve
inside the project, and absolute paths must also be inside the project.

## Workspace authorization policy

MCP/CLI sub-agent runs use a stricter policy than the Slack workspace override.
This keeps common read-only multi-project use convenient without giving models
unbounded write access.

Recommended local setup:

```env
FLUXION_TRUSTED_WORKSPACE_ROOTS=/Users/you/code
FLUXION_WORKSPACE_DISCOVERY=true
FLUXION_DENIED_WORKSPACES=
FLUXION_WRITE_ALLOWED_WORKSPACES=
```

Policy order:

1. `FLUXION_DENIED_WORKSPACES` always blocks access.
2. Registered projects allow both `read-only` and `workspace-write` within the
   project root.
3. `FLUXION_ALLOWED_WORKSPACES` keeps the legacy behavior and allows matching
   workspaces.
4. `workspace-write` outside registered projects requires
   `FLUXION_WRITE_ALLOWED_WORKSPACES`.
5. `read-only` outside registered projects is allowed only when the workspace
   is a Git repository root under `FLUXION_TRUSTED_WORKSPACE_ROOTS` and
   `FLUXION_WORKSPACE_DISCOVERY=true`.

### Channel-to-workspace mode

Bind specific Slack channels to specific project directories.

```env
FLUXION_SLACK_ALLOW_CHANNELS=true
FLUXION_SLACK_REQUIRE_MENTION_IN_CHANNELS=true   # set false for bot-only channels
FLUXION_SLACK_CHANNEL_WORKSPACES=C12345678:/abs/path/projectA,C23456789:/abs/path/projectB
```

In mapped channels, Fluxion only accepts messages starting with `@Fluxion`
(unless `FLUXION_SLACK_REQUIRE_MENTION_IN_CHANNELS=false`). Per-message
workspace override is ignored in mapped channels.

### DM control commands

| Command | Description |
| --- | --- |
| `help` / `/help` | Show command help |
| `ping` / `/ping` | Health check |
| `status` / `/status` | Gateway queue and worker runtime status |
| `tasks` / `/tasks` | Recent tasks |
| `history` / `/history` | Recent task status history |
| `task <task_id>` | Show one task detail |
| `cancel <task_id>` | Cancel a queued or running task |
| `reset` | Clear conversation memory, executor session IDs, and thread overrides |
| `executors` / `/executors` | List supported executors and the active executor override |
| `models` / `/models` | List selectable model IDs for the active executor |
| `models <executor>` / `/models <executor>` | List selectable model IDs for a specific executor |
| `use executor <executor>` / `/use executor <executor>` | Switch executor for the current conversation or thread |
| `use executor default` / `/use executor default` | Clear the executor override |
| `use model <model-id>` / `/use model <model-id>` | Switch model for the active executor in the current conversation or thread |
| `use model default` / `/use model default` | Clear the model override for the active executor |

## Web UI

By default the UI binds to `127.0.0.1:8765` with no token required.

| Scope | Recipe |
| --- | --- |
| Localhost only (default) | `fluxion-web` |
| Same-LAN devices | Set `FLUXION_UI_TOKEN`, then `fluxion-web --host 0.0.0.0` |
| Public internet | Same as LAN, plus HTTPS in front (Caddy / Cloudflare Tunnel / Tailscale Funnel) |

```bash
export FLUXION_UI_TOKEN="$(openssl rand -hex 16)"
echo "$FLUXION_UI_TOKEN"   # write this down — the browser needs it
fluxion-web --host 0.0.0.0 --port 8765
```

When `FLUXION_UI_TOKEN` is set, every `/api/*` request must carry
`Authorization: Bearer <token>` or `?token=<token>`. The frontend reads the
token from `localStorage.fluxion.uiToken`. Inject it once via the browser
devtools console:

```js
localStorage.setItem('fluxion.uiToken', 'paste-the-token-here');
location.reload();
```

### API endpoints

All under `/api`. The task/session/log/usage routes are read-only; the schedule
routes write rule definitions to `data/schedules.jsonl` — they never dispatch
tasks (the scheduler daemon does).

| Endpoint | Returns / does |
| --- | --- |
| `GET /api/tasks` | Aggregated task list |
| `GET /api/tasks/{id}` | Single task detail |
| `GET /api/sessions` | Latest session events keyed by conversation |
| `GET /api/logs/{id}` | Raw task log file contents |
| `GET /api/usage` | Live provider quota snapshot |
| `GET /api/schedules` | List scheduler rules |
| `GET /api/autoping` | Read managed Auto Ping modes |
| `PUT /api/autoping` | Update one managed Auto Ping mode |
| `POST /api/schedules` | Create a rule |
| `PUT /api/schedules/{id}` | Update a rule |
| `POST /api/schedules/{id}/enable` | Enable / disable a rule |
| `DELETE /api/schedules/{id}` | Delete a rule |
| `GET /api/schedule_runs` | Recent fire history |

## Scheduler

`fluxion-scheduler` fires sub-agent runs on a cron schedule or when a provider
quota window refreshes. It runs as its own daemon — the only service that needs
to stay up for scheduled tasks — and hosts its own executor, so it works
without the messaging gateway or UI. Manage rules from the web UI's **⏱ Schedules**
panel or by editing `data/schedules.jsonl`. See [Scheduler](scheduler.md) for
trigger types, the rule schema, safety rails, and launchd/systemd templates.

## Session reuse

Fluxion reuses executor-native sessions per Slack conversation key (DM/thread):

- Each finished task stores the returned executor session ID
- The next task in the same conversation resumes the stored session when the
  executor supports it
- Session state is persisted under `data/sessions.jsonl` and restored on restart
- `reset` clears both conversation memory and bound executor session IDs

To verify session reuse from logs (`data/logs/task-*.log`):

- Codex: `command=codex exec resume <session_id> --full-auto ...`
- Claude: `command=claude --resume <session_id> -p --output-format json ...`

## Environment variables

See [`.env.advanced.example`](../.env.advanced.example) for the full reference
with inline comments. [`.env.example`](../.env.example) is intentionally
minimal. The table below covers the most commonly tuned keys.

### Gateway

| Key | Default | Description |
| --- | --- | --- |
| `FLUXION_WORKSPACE_ROOT` | | Absolute path to the Fluxion checkout |
| `FLUXION_ALLOWED_WORKSPACES` | | Comma-separated allowed workspace paths |
| `FLUXION_TRUSTED_WORKSPACE_ROOTS` | | Roots where discovered Git repos may be used for read-only MCP/CLI runs |
| `FLUXION_WORKSPACE_DISCOVERY` | `false` | Allow read-only runs for Git repo roots under trusted workspace roots |
| `FLUXION_DENIED_WORKSPACES` | | Comma-separated paths that are always denied |
| `FLUXION_WRITE_ALLOWED_WORKSPACES` | | Comma-separated paths allowed for `workspace-write` outside registered projects |
| `FLUXION_PROJECTS_FILE` | | JSON project registry used by MCP/CLI sub-agent callers |
| `FLUXION_PROJECTS` | | Inline project registry, e.g. `app=/path/app\|executor=codex` |
| `FLUXION_DATA_DIR` | `data` | State directory (relative to `FLUXION_WORKSPACE_ROOT`) |
| `FLUXION_DEFAULT_EXECUTOR` | `codex` | `codex` / `claude` / `antigravity` |
| `FLUXION_WORKER_COUNT` | `3` | Concurrent runs per Fluxion process. Same-workspace writes stay serialized by the workspace lock regardless |
| `FLUXION_MAX_PENDING_PER_USER` | `5` | Max in-flight tasks per user (queued + running). Keep above `FLUXION_WORKER_COUNT` |
| `FLUXION_TASK_TIMEOUT_SEC` | `1800` | Per-task timeout |
| `FLUXION_WORKSPACE_LOCK_TIMEOUT_SEC` | `FLUXION_TASK_TIMEOUT_SEC` | How long a `workspace-write` run waits for another run to release the same workspace before failing |
| `FLUXION_MAX_RETRIES` | `1` | Max retries for transient failures |
| `FLUXION_RETRY_BACKOFF_SEC` | `2` | Retry backoff base seconds |
| `FLUXION_CHANGE_SET_MAX_FILE_BYTES` | `1000000` | Max per-file UTF-8 text content captured for reversible ChangeSets; `0` disables capture |
| `FLUXION_CHANGE_SET_MAX_TOTAL_BYTES` | `20000000` | Max total captured text bytes per run; `0` disables capture |
| `FLUXION_STATUS_UPDATES` | `RUNNING,FAILED,CANCELED` | Status notifications to send |
| `FLUXION_UPLOAD_LOG_ON_SUCCESS` | `false` | Upload executor logs on success |
| `FLUXION_LOCALE_MODE` | `auto` | `fixed` or `auto` (infers from message text) |
| `FLUXION_UI_LOCALE` | `en` | Default locale (`zh` / `en` / `ja`) for replies and push notifications (quota resets etc.). The macOS app keeps this in sync when you change its language in Preferences |
| `FLUXION_SLACK_TYPING_HEARTBEAT_SEC` | `6` | Typing heartbeat during long runs |
| `FLUXION_SLACK_RUNNING_UPDATE_SEC` | `30` | Periodic in-progress status interval |

### Web UI

| Key | Description |
| --- | --- |
| `FLUXION_UI_TOKEN` | When set, all `/api/*` requests must present this token |

### Scheduler

| Key | Default | Description |
| --- | --- | --- |
| `FLUXION_SCHEDULER_ENABLED` | `true` | Canonical on/off switch for the scheduler daemon; when `false`, schedules, quota monitors, and Auto Ping do not run even if the process is started |
| `FLUXION_SCHEDULER_TICK_SEC` | `0` | Poll interval seconds; `0` = auto (derived from `FLUXION_USAGE_REFRESH_SEC`, capped to 15–60s) |
| `FLUXION_AUTOPING_ENABLED` | `false` | Global Auto Ping switch — when on, a monitored reset triggers a keep-alive ping (off by default so a monitor never spends quota uninvited) |
| `FLUXION_AUTOPING_MAX_ATTEMPTS` | `12` | Max Auto Ping pings per quota-reset event; the burst resumes one session until the 5h window anchors, then notifies once and gives up |
