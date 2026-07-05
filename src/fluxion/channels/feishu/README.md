# Feishu (Lark) Adapter

This directory contains the Feishu/Lark `ChannelAdapter` implementation
(single chat + group @-mentions, text in / text out). It uses Feishu's
**long connection** (WebSocket) via the official `lark-oapi` SDK: Fluxion
connects *out* to Feishu, so there is no public address, tunnel, or callback
configuration. The SDK manages the tenant access token, event dispatch, and
decryption internally.

## Files

- `feishu_client.py` — thin wrapper over `lark.Client` for the two send
  requests (send-to-chat and reply-to-message); normalizes failures into
  `FeishuAPIError`.
- `content.py` — converts between Feishu's JSON `content` (`{"text": "..."}`)
  and plain text, and strips the `@_user_N` / `@_all` mention placeholders that
  Feishu leaves in group text.
- `pending_store.py` — records rejected open_ids so an admin can approve them.
- `adapter.py` — turns Feishu `im.message.receive_v1` events into Fluxion tasks
  and pushes status/results back into the originating chat.

## Setup

1. Create a **self-built app** in the Feishu/Lark developer console and note its
   **App ID** and **App Secret**.
2. Enable the **long connection** (事件订阅 → 使用长连接接收事件) and subscribe to
   the **`im.message.receive_v1`** event.
3. Grant the app the IM permissions to read and send messages
   (e.g. `im:message`, `im:message:send_as_bot`). Reading **inbound images/files**
   additionally needs a message-read scope — any one of `im:message` /
   `im:message:readonly` / `im:message.history:readonly` (otherwise the download
   fails with `99991672 Access denied`). Sending image/file artifacts back may
   also require the upload permission `im:resource`. Re-publish the app after
   changing scopes.
4. Publish/enable the app so the bot can be added to chats.
5. Set the env vars:
   - `FLUXION_FEISHU_ENABLED=true`
   - `FEISHU_APP_ID=<your app id>`
   - `FEISHU_APP_SECRET=<your app secret>`
   - `FLUXION_FEISHU_ALLOWED_USERS=<comma-separated open_ids>` (fail-closed: an
     empty allowlist denies everyone; the first denied message replies with the
     sender's open_id so you can add it)
   - Optional: `FLUXION_FEISHU_ALLOW_GROUP_CHAT=false` to disable group
     @-replies, `FLUXION_FEISHU_DEFAULT_WORKSPACE=<path>`.
6. Start `fluxion-gateway`. The log shows `Feishu channel adapter started`.

## Notes

- The allowlist key is the user's **open_id** (app-scoped). DM the bot once; the
  rejection reply prints your open_id so you can add it to the allowlist.
- Task replies are sent actively to the originating `chat_id`, which works for
  both single chats and group chats, so there is no passive-reply time window to
  manage (unlike QQ).
- The answer streams into an updatable markdown **card**: a "thinking…" card is
  posted when work starts, patched in place as output arrives (throttled to
  respect Feishu's `message.patch` rate limit), and finalized with the result —
  one bubble, with the executor's markdown rendered. This mirrors the official
  `lark-samples` AI-bot pattern (`im.v1.message.patch` on a card with
  `config.update_multi = true`).
- Quota-reset and credit notifications send to each allowed user's private chat
  via `open_id` as plain text.
- Images and files in/out: inbound image/file messages are downloaded into the
  task workspace inbox (`.fluxion_inbox/`) so the agent can read them; result
  artifacts are sent back as native image messages (image extensions) or file
  messages (everything else).
- Not yet implemented (intentionally): reply threading and a webhook transport.
