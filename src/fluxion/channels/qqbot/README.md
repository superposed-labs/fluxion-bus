# QQ Bot Adapter

This directory contains the QQ official bot `ChannelAdapter` implementation
(single chat + group @-mentions, text in / text out). Two transports are
supported; the send path is identical for both:

- **WebSocket** (recommended, default for local use) — Fluxion connects *out* to
  QQ's gateway. No public address, no tunnel, no callback configuration.
- **Webhook** — QQ POSTs events to a public HTTPS callback. Needs a tunnel and a
  registered callback URL.

## Files

- `token_manager.py` — caches the app access token and refreshes it ~90s before
  expiry (`getAppAccessToken`).
- `signing.py` — Ed25519 callback validation (`op:13`) and inbound signature
  verification, using `cryptography` (no PyNaCl dependency). Webhook only.
- `qqbot_client.py` — `urllib`-based client for the C2C and group send endpoints.
- `websocket_transport.py` — resilient WebSocket gateway client (identify,
  heartbeat, reconnect) that feeds events into the adapter.
- `adapter.py` — turns QQ events (from either transport) into Fluxion tasks and
  pushes status/results back as passive replies.

## Setup (WebSocket — recommended)

1. Create a bot in the QQ open platform console and note its **AppID** and
   **AppSecret**. The AppSecret is shown only once when generated — copy it then
   (the **AppSecret** row only has a *Generate* button, no copy; do not confuse
   it with the **Token** row).
2. Set the env vars:
   - `FLUXION_QQBOT_ENABLED=true`
   - `FLUXION_QQBOT_TRANSPORT=websocket`
   - `QQBOT_APP_ID=<your app id>`
   - `QQBOT_CLIENT_SECRET=<your app secret>`
   - `FLUXION_QQBOT_ALLOWED_USERS=<comma-separated user openids>` (fail-closed:
     an empty allowlist denies everyone; the first denied message replies with
     the sender's openid so you can add it)
   - Optional: `FLUXION_QQBOT_SANDBOX=true` while testing,
     `FLUXION_QQBOT_ALLOW_GROUP_CHAT=false` to disable group @-replies,
     `FLUXION_QQBOT_DEFAULT_WORKSPACE=<path>`.
3. Start `fluxion-gateway`. The log shows `QQ bot WebSocket ready` once connected.

## Setup (Webhook — alternative)

Use this only if you run Fluxion behind a stable public HTTPS endpoint.

1. Same credentials as above, plus `FLUXION_QQBOT_TRANSPORT=webhook`.
2. Expose the local webhook port (default `8767`, override with
   `FLUXION_QQBOT_PORT`) over a public HTTPS tunnel — QQ only allows callback
   ports `80, 443, 8080, 8443`, so map the tunnel's public port accordingly.
3. Register `https://<your-tunnel-domain>/qqbot/webhook` as the callback URL.
   On save, QQ sends an `op:13` validation request that the adapter answers
   automatically with an Ed25519 signature.
4. Start `fluxion-gateway`.

> Note: after a callback URL is successfully saved, QQ disables the WebSocket
> transport for that bot. Also, the webhook signature is keyed on the **AppSecret**
> — if you reset the AppSecret, QQ's callback-validation service can lag behind its
> token service for a while, so validation may fail even with a correct secret.
> The WebSocket transport avoids this entirely.

## Notes

- Task replies are **passive** whenever possible — the adapter echoes the
  inbound `msg_id` and auto-increments `msg_seq` per task. This is the right
  path for normal "user sends a task, Fluxion answers" traffic.
- Passive replies are still time-limited by QQ. C2C single-chat passive replies
  are valid for 60 minutes and up to 5 replies per inbound message; group
  passive replies use a much shorter window. Fluxion uses conservative local
  cutoffs before falling back to an active send.
- Active sends are used when there is no inbound `msg_id`, such as quota-reset
  and credit notifications. QQ's active C2C quota is much tighter (the official
  docs list 4 active single-chat messages per user per month), so do not use QQ
  as a high-volume notification channel.
- For current limits, check QQ's official send-message docs:
  https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/send-receive/send.html
- Inbound images are downloaded into the task workspace inbox (`.fluxion_inbox/`)
  so the agent can read them.
- Answers are sent as **native (custom) markdown** (`msg_type: 2`), so the
  executor's bold/lists/code survive instead of being flattened to plain text.
  This needs no template registration (QQ opened custom markdown to all bots for
  C2C + group on 2026-04-23). Status and error replies stay plain text. QQ's
  markdown follows standard markdown — a lone `\n` is a soft break the renderer
  collapses — so `markdown_for_qq` appends two trailing spaces to force hard
  breaks (code fences and blank lines are left untouched).
- Not yet implemented (intentionally): **outbound** rich media (images/files),
  markdown *templates*, buttons, channels/guilds, and voice. QQ *can* send
  images/files (`file_type` 1–4), but
  its rich-media send API requires a publicly reachable `url` for the media (`url`
  is required; raw `file_data` is not the supported path). Fluxion is local-first
  and has no public host for its result artifacts, so there is nothing to point QQ
  at. (Tencent's own hosted runtimes like OpenClaw/Hermes get multimedia "for
  free" because they already run behind a public URL.) Adding outbound media would
  mean exposing local artifacts at a public URL — e.g. reusing the cloudflared
  tunnel — shared with the other URL-based channels.
