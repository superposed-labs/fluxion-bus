# Quota Monitoring

Fluxion monitors live provider quota windows, stores a shared local snapshot,
and exposes it through the Web console and the native macOS menu bar app.

The displayed quota is reported by provider APIs or the corresponding local
agent service. It reflects the provider/account's usage regardless of whether
requests were made through Fluxion. Fluxion does not derive quota from its own
task records.

## Platform support

| Capability | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Web quota console | Supported | Expected, not verified | Not verified |
| Scheduler auto-ping and reset notifications | Supported | Expected, not verified | Not verified |
| Native menu bar quota app | Supported | Not available | Not available |

Linux support is expected for non-native features based on the implementation,
but has not yet been manually verified.

Provider-specific probes still depend on compatible local credentials or
services.

## Provider sources

### Claude

Fluxion queries Claude's OAuth usage endpoint for subscription/login auth.
It surfaces rolling quota windows such as 5-hour and weekly usage. Claude's
`extra_usage`/`spend` fields are billing controls for paid overage usage, not
model quota or API credit balance, so Fluxion does not show them in the quota
panel.

- Token source: `FLUXION_CLAUDE_USAGE_TOKEN` or
  `~/.claude/.credentials.json`.
- macOS Keychain access is opt-in with
  `FLUXION_CLAUDE_USAGE_KEYCHAIN=true`.
- OAuth refresh and credential write-back are opt-in with
  `FLUXION_CLAUDE_USAGE_AUTO_REFRESH=true`.

### Codex

Fluxion queries the live ChatGPT usage endpoint using the token in
`~/.codex/auth.json`. In `auto` mode it falls back to local Codex session logs
when the live endpoint is unavailable.

Configure the source with:

```env
FLUXION_CODEX_USAGE_MODE=auto  # auto | live | logs
```

### Antigravity

Fluxion reads AI credits and quota from Antigravity's cloud API, falling back
to the local `language_server` sidecar when no token is available or the cloud
response is unusable. The sidecar path needs the Antigravity IDE running.
Quota is reported the way Antigravity itself groups it — Gemini and External
Models — rather than per individual model.

## Web console

Build the frontend once:

```bash
cd web
npm install
npm run build
cd ..
```

Then start:

```bash
fluxion-web                 # http://127.0.0.1:8765
fluxion-web --port 8000
```

The console shows remaining quota, reset countdowns, task history, scheduler
rules, and usage statistics. For LAN exposure, configure `FLUXION_UI_TOKEN`
before binding to `0.0.0.0`; see [Configuration](configuration.md#web-ui).

## macOS menu bar app

The native menu bar app is macOS only:

```bash
./desktop/build.sh
open desktop/Fluxion.app
```

It reads `data/usage_cache.json`, shows provider quota and reset countdowns,
and provides controls for auto-ping, notifications, companion services, and
Slack, Telegram, WeChat, and LINE channel settings.
The app may remain in the repository or be copied to `/Applications`; when
needed, it asks the user to select the Fluxion checkout and stores that path in
`~/Library/Application Support/Fluxion/config.json`.

| Switch | Service | Default |
| --- | --- | --- |
| `FLUXION_MENU_AUTOSTART_WEB` | `fluxion-web` | `true` |
| `FLUXION_MENU_AUTOSTART_SCHEDULER` | `fluxion-scheduler` | `true`; the app starts it only when `FLUXION_SCHEDULER_ENABLED=true` |
| `FLUXION_MENU_AUTOSTART_GATEWAY` | `fluxion-gateway` | `false` |

When the repository is under `~/Documents`, services started by the app inherit
its Documents-folder permission. A background launchd agent may otherwise fail
with `PermissionError`.

See the [macOS app guide](../desktop/README.md) for build, installation, and
development override instructions.

## Automatic pings and notifications

The Web Schedules panel and menu bar app configure Auto Ping through the same
managed scheduler rules. `fluxion-scheduler` performs the actual background
work.

After Fluxion detects that a monitored window reset, it sends a notification and,
when Auto Ping is enabled, a short burst of minimal Agent calls (resuming one
session) until the new window anchors. See the [scheduler guide](scheduler.md)
for the monitor scope, the global Auto Ping switch, and the tuning knobs.

```bash
fluxion-scheduler --set-autoping codex both
fluxion-scheduler --get-autoping
```

Reset notifications remain deployment settings:

```env
FLUXION_MENU_SLACK_NOTIFY_REFRESH=false
FLUXION_MENU_TELEGRAM_NOTIFY_REFRESH=true
FLUXION_MENU_QQBOT_NOTIFY_REFRESH=false
FLUXION_MENU_WECHAT_NOTIFY_REFRESH=false
FLUXION_MENU_LINE_NOTIFY_REFRESH=false
FLUXION_MENU_FEISHU_NOTIFY_REFRESH=false
```

WeChat notifications require a prior QR-code login and at least one inbound
message from each recipient so Fluxion can persist the required context token.
When `FLUXION_WECHAT_ALLOWED_USERS` is set, only those users are notified.

**WeChat delivery window.** The iLink bot protocol requires outbound sends to
include a `context_token` from the recipient's most recent inbound message.
Public WeChat iLink documentation does not currently publish a precise token
lifetime, but in Fluxion testing and community reports the token expires after
a limited window (roughly 24 hours, with successful sends observed after more
than 19 hours), and sends can fail silently (`sendmessage` returns `ret=-2`).
This affects both proactive notifications and very long-running task
replies. It is a WeChat platform limitation, not a Fluxion bug: there is no
token-refresh or keep-alive endpoint, and the bot cannot re-open the window on
its own. In practice an **overnight quota reset may be missed** if you haven't
messaged the bot since the previous day. To restore delivery, send any message
to the bot. For time-critical alerts, prefer Slack or Telegram, whose delivery
does not expire.

**QQ and Feishu delivery.** Similar to WeChat, QQ Bot and Feishu adapters require valid subscriber or app IDs, and notifications are sent to the allowed user lists configured in their respective sections of the advanced environment variables.

See [Scheduler](scheduler.md) for triggers, rules, safety rails, and deployment.

## Privacy

Quota monitoring uses locally stored credentials and provider endpoints. The
macOS Keychain is never read unless explicitly enabled. The shared snapshot is
stored locally under the configured Fluxion data directory.
