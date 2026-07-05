"""QQ official bot channel adapter for Fluxion.

Bridges the QQ open-platform bot to the Fluxion gateway over a webhook, mirroring
the LINE adapter's shape (a background uvicorn server feeding inbound messages to
``submit_task``, with status/result pushed back out).

First-version scope, deliberately small:

* Single chat (``C2C_MESSAGE_CREATE``) and group @-mentions
  (``GROUP_AT_MESSAGE_CREATE``).
* Text in; answers go out as native (custom) markdown so the executor's
  formatting (bold/lists/code) survives — status/error replies stay plain text.
  Inbound images are downloaded into the task workspace inbox so the agent can
  read them.
* Passive replies (echo the inbound ``msg_id``) so we stay inside QQ's free
  messaging quota.
* Token auto-refresh via :class:`QQBotTokenManager`.

Not yet handled (intentionally): *outbound* rich media, markdown/buttons,
channels/guilds, voice, and the WebSocket transport. Outbound media is omitted on
purpose: QQ *can* send images/files, but its rich-media send API requires a
publicly reachable ``url`` for the media (``url`` is required; raw ``file_data``
is not the supported path), and Fluxion is local-first with no public host for
its result artifacts. Tencent's own hosted runtimes (OpenClaw/Hermes) get this
for free because they already run behind a public URL. The transport is isolated
behind the webhook server so a WebSocket option can be added later.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response

from fluxion.channels.allowlist_messages import format_allowlist_rejection
from fluxion.channels.base import SettingsHotReloader, authorize_inbound
from fluxion.channels.control_formatter import format_control_response
from fluxion.channels.inbox import make_inbox_dir
from fluxion.channels.qqbot.pending_store import PendingUserStore
from fluxion.channels.qqbot.qqbot_client import QQBotAPIError, QQBotClient
from fluxion.channels.qqbot.signing import callback_signature, verify_signature
from fluxion.channels.qqbot.token_manager import QQBotTokenManager
from fluxion.config.settings import Settings
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.i18n import resolve_locale

# QQ supports native (custom) markdown out, so — like Slack and Feishu — we keep
# the executor's markdown intact (bold/lists/code) via the shared MarkdownRenderer
# instead of flattening it. We still borrow LINE's clip_text for QQ's length cap.
from fluxion.renderers.line_renderer import clip_text
from fluxion.renderers.markdown_renderer import MarkdownRenderer
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

# QQ event types we act on.
EVENT_C2C_MESSAGE = "C2C_MESSAGE_CREATE"
EVENT_GROUP_AT_MESSAGE = "GROUP_AT_MESSAGE_CREATE"

# Webhook opcodes.
OP_DISPATCH = 0
OP_CALLBACK_VALIDATION = 13

# QQ passive replies are time-limited. Keep a buffer for queueing, network
# latency, and clock differences before falling back to active sends instead of
# letting the passive reply fail at the edge.
PASSIVE_REPLY_MAX_AGE_SEC_C2C = 55 * 60
PASSIVE_REPLY_MAX_AGE_SEC_GROUP = 4 * 60


def markdown_for_qq(text: str) -> str:
    """Normalize markdown so QQ's renderer keeps the line breaks the user expects.

    QQ's native markdown follows standard markdown: a lone ``\\n`` is a *soft*
    break that the renderer collapses into a space, so multi-line text would mash
    together. We force a hard break by appending two trailing spaces to each
    non-blank line — except inside fenced code blocks (which preserve their own
    newlines) and blank lines (which already separate paragraphs).
    """
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence or not line.strip():
            out.append(line)
            continue
        out.append(line if line.endswith("  ") else line.rstrip() + "  ")
    return "\n".join(out)


class QQBotChannelAdapter:
    """Fluxion channel adapter bridging the QQ official bot via webhooks."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._renderer = MarkdownRenderer()
        self._gateway = None
        self._tokens = QQBotTokenManager(
            app_id=settings.qqbot_app_id,
            client_secret=settings.qqbot_client_secret,
        )
        self._client = QQBotClient(self._tokens, sandbox=settings.qqbot_sandbox)
        self._pending_store = PendingUserStore(settings.data_dir)
        self._settings_reloader = SettingsHotReloader(channel="QQ")

        self._lock = threading.Lock()
        # Per-task inbound msg_id so terminal sends are free passive replies, and a
        # per-task msg_seq counter (must be unique per msg_id).
        self._reply_msg_ids: dict[str, str] = {}
        self._reply_msg_seen_at: dict[str, float] = {}
        self._msg_seq: dict[str, int] = {}
        # Streaming output accumulated until the result is sent.
        self._stream_buffers: dict[str, str] = {}

    # ------------------------------------------------------------------
    # ChannelAdapter protocol
    # ------------------------------------------------------------------

    def start(self, gateway: GatewayCore) -> None:
        self._gateway = gateway
        if self._settings.qqbot_transport == "websocket":
            target, name = self._run_websocket, "fluxion-qqbot-websocket"
        else:
            target, name = self._run_server, "fluxion-qqbot-webhook"
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        logger.info("QQ bot channel adapter started (transport=%s)", self._settings.qqbot_transport)

    def _run_websocket(self) -> None:
        from fluxion.channels.qqbot.websocket_transport import QQBotWebSocketTransport

        transport = QQBotWebSocketTransport(
            self._tokens,
            self._handle_event,
            sandbox=self._settings.qqbot_sandbox,
        )
        transport.run_forever()

    def send_status(
        self, task_id: str, status: str, context: dict, detail: str | None = None
    ) -> None:
        if status not in self._settings.status_updates:
            return
        if status == "RUNNING":
            # QQ has no message-edit or typing affordance, so a stream of RUNNING
            # bubbles would just be noise. Skip it, like the LINE adapter does.
            return
        locale = self._locale_for_context(context)
        text = self._renderer.render_status(task_id, status, detail=detail, locale=locale)
        self._send(task_id, context, text, terminal=False)

    def send_result(self, task_id: str, result: ExecutionResult, context: dict) -> None:
        buffered = self._pop_stream_buffer(task_id)
        locale = self._locale_for_context(context)

        if buffered and result.success:
            combined = buffered
        else:
            rendered = self._renderer.render_result(task_id, result, locale=locale)
            combined = buffered + "\n\n" + rendered if buffered else rendered

        # Keep markdown intact — QQ renders it natively.
        self._send(task_id, context, combined, terminal=True, markdown=True)

    def send_typing(self, context: dict) -> None:
        # QQ exposes no typing indicator; nothing to do.
        return

    def send_output_delta(self, task_id: str, text: str, context: dict) -> None:
        chunk = text or ""
        if not chunk:
            return
        with self._lock:
            self._stream_buffers[task_id] = self._stream_buffers.get(task_id, "") + chunk

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def _send(
        self, task_id: str, context: dict, text: str, *, terminal: bool, markdown: bool = False
    ) -> None:
        target_type = context.get("qqbot_target_type")
        openid = context.get("qqbot_openid")
        if not target_type or not openid:
            logger.error("QQ bot send skipped: missing target info in context")
            return

        # Strip trailing whitespace so executor output ending in a newline does
        # not render as a dangling blank line in the QQ message bubble.
        content = clip_text(text.strip())
        # QQ collapses soft (single-\n) line breaks, so force hard breaks for the
        # native-markdown path (status/error replies stay plain text).
        if markdown:
            content = markdown_for_qq(content)
        with self._lock:
            msg_id = self._reply_msg_ids.get(task_id)
            seen_at = self._reply_msg_seen_at.get(task_id)
            seq = self._msg_seq.get(task_id, 0) + 1
            self._msg_seq[task_id] = seq
            if msg_id and seen_at is not None:
                max_age_sec = (
                    PASSIVE_REPLY_MAX_AGE_SEC_GROUP
                    if target_type == "group"
                    else PASSIVE_REPLY_MAX_AGE_SEC_C2C
                )
                age_sec = time.monotonic() - seen_at
                if age_sec > max_age_sec:
                    logger.warning(
                        "QQ passive reply for task %s is %.0fs old; falling back to active send.",
                        task_id,
                        age_sec,
                    )
                    msg_id = None
            if terminal:
                self._reply_msg_ids.pop(task_id, None)
                self._reply_msg_seen_at.pop(task_id, None)
                self._msg_seq.pop(task_id, None)

        try:
            if target_type == "group":
                send = (
                    self._client.send_group_markdown if markdown else self._client.send_group_text
                )
            else:
                send = self._client.send_c2c_markdown if markdown else self._client.send_c2c_text
            send(openid, content, msg_id=msg_id, msg_seq=seq)
        except QQBotAPIError:
            logger.exception("Failed to send QQ bot message for task %s", task_id)

    # ------------------------------------------------------------------
    # Webhook server
    # ------------------------------------------------------------------

    def _run_server(self) -> None:
        app = FastAPI()

        @app.get("/health")
        def health() -> Response:
            return Response(content="ok", media_type="text/plain")

        @app.post("/qqbot/webhook")
        async def qqbot_webhook(
            request: Request,
            x_signature_ed25519: str = Header(None),
            x_signature_timestamp: str = Header(None),
        ) -> Any:
            body = await request.body()
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                raise HTTPException(status_code=400, detail="invalid json") from None

            op = payload.get("op")

            # Callback URL validation: QQ proves it can reach us and we prove we
            # hold the secret by signing event_ts + plain_token.
            if op == OP_CALLBACK_VALIDATION:
                d = payload.get("d") or {}
                plain_token = str(d.get("plain_token") or "")
                event_ts = str(d.get("event_ts") or "")
                signature = callback_signature(
                    self._settings.qqbot_client_secret, event_ts, plain_token
                )
                return {"plain_token": plain_token, "signature": signature}

            # Every pushed event is signed; reject anything we can't verify.
            if not verify_signature(
                self._settings.qqbot_client_secret,
                x_signature_timestamp or "",
                body,
                x_signature_ed25519 or "",
            ):
                logger.warning("Invalid QQ bot signature received")
                raise HTTPException(status_code=401, detail="invalid signature")

            if op == OP_DISPATCH:
                self._handle_event(payload)

            return Response(content="ok", media_type="text/plain")

        port = int(os.environ.get("FLUXION_QQBOT_PORT", 8767))
        logger.info("Starting background FastAPI server for QQ bot on port %s", port)
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

    def _handle_event(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("t")
        d = payload.get("d") or {}

        if event_type == EVENT_C2C_MESSAGE:
            author = d.get("author") or {}
            sender_id = author.get("user_openid")
            target_type = "c2c"
            openid = sender_id
            channel_type = "qqbot_c2c"
        elif event_type == EVENT_GROUP_AT_MESSAGE:
            if not self._settings.qqbot_allow_group_chat:
                return
            author = d.get("author") or {}
            sender_id = author.get("member_openid")
            target_type = "group"
            openid = d.get("group_openid")
            channel_type = "qqbot_group"
        else:
            # Ignore other event types in this first version.
            return

        msg_id = d.get("id")
        text = (d.get("content") or "").strip()
        attachments = self._extract_image_attachments(d)
        if not sender_id or not openid or not msg_id:
            return

        # Fail-closed whitelist check (empty allowlist denies everyone).
        reloader = getattr(self, "_settings_reloader", None)
        if reloader is not None:
            self._settings = reloader.reload_if_changed(self._settings)
        allowed, _reason = authorize_inbound(
            channel="qqbot",
            user_id=sender_id,
            allowed_users=self._settings.qqbot_allowed_users,
        )
        if not allowed:
            self._pending_store.record(
                sender_id,
                text or "[attachment]",
                notify_locale=self._locale_for_context({"source_text": ""}),
            )
            locale = self._locale_for_context({"source_text": text})
            self._reply_now(
                target_type,
                openid,
                msg_id,
                self._format_rejection(_reason, sender_id, locale=locale),
            )
            return
        self._pending_store.remove(sender_id)

        logger.info("QQ bot inbound %s from %s: %s", event_type, sender_id, text or "(empty)")

        convo_key = f"qqbot:{target_type}:{openid}"

        # Control commands are text-only; a message carrying an image is a task.
        if (
            text
            and not attachments
            and self._handle_command(text, sender_id, convo_key, target_type, openid, msg_id)
        ):
            return

        try:
            raw_ws = self._settings.qqbot_default_workspace or self._default_workspace()
            workspace = self._settings.resolve_workspace(raw_ws)
        except ValueError as exc:
            self._reply_now(target_type, openid, msg_id, f"Rejected: {exc}")
            return

        task_text = text
        if attachments:
            saved = self._download_attachments(
                attachments=attachments,
                workspace=workspace,
                target_type=target_type,
                openid=openid,
                msg_id=msg_id,
            )
            if saved:
                rel = [str(p.relative_to(workspace)) for p in saved]
                note = "Attached image(s) saved in the workspace:\n" + "\n".join(
                    f"- {r}" for r in rel
                )
                task_text = (
                    f"{text}\n\n{note}".strip()
                    if text
                    else (
                        "The user sent the following image(s) without a text instruction. "
                        "Inspect them and respond appropriately.\n\n" + note
                    )
                )

        override = self._gateway._sessions.get_executor_override(
            conversation_key=convo_key,
            channel="qqbot",
            user_id=sender_id,
        )
        executor_to_use = override if override else self._settings.default_executor

        task = Task.create(
            channel="qqbot",
            user_id=sender_id,
            text=task_text,
            workspace=workspace,
            metadata={
                "executor": executor_to_use,
                "conversation_key": convo_key,
            },
        )

        with self._lock:
            self._reply_msg_ids[task.id] = msg_id
            self._reply_msg_seen_at[task.id] = time.monotonic()
            self._msg_seq[task.id] = 0

        channel_context = {
            "user": sender_id,
            "channel_type": channel_type,
            "source_text": text,
            "qqbot_target_type": target_type,
            "qqbot_openid": openid,
        }

        if self._gateway is None:
            self._discard_task(task.id)
            self._reply_now(target_type, openid, msg_id, "Error: Gateway not ready.")
            return

        ok, info = self._gateway.submit_task(
            task=task,
            channel_adapter=self,
            channel_context=channel_context,
        )
        if not ok:
            self._discard_task(task.id)
            self._reply_now(target_type, openid, msg_id, f"Rejected: {info}")

    def _handle_command(
        self,
        text: str,
        user_id: str,
        convo_key: str,
        target_type: str,
        openid: str,
        msg_id: str,
    ) -> bool:
        if self._gateway is None:
            return False
        response = self._gateway.handle_control_command(
            text=text,
            user_id=user_id,
            convo_key=convo_key,
            channel="qqbot",
        )
        if response is not None:
            self._reply_now(
                target_type,
                openid,
                msg_id,
                format_control_response(response, channel="qqbot"),
            )
            return True
        return False

    def _format_rejection(self, reason: str, user_id: str, *, locale: str | None = None) -> str:
        return format_allowlist_rejection(
            reason=reason,
            user_id=user_id,
            id_label="QQ openid",
            env_key="FLUXION_QQBOT_ALLOWED_USERS",
            locale=locale or self._locale_for_context({"source_text": ""}),
            macos_pending_location="Messaging -> QQ -> Pending Users",
        )

    def _default_workspace(self) -> str | None:
        resolver = getattr(self._settings, "default_channel_workspace", None)
        if callable(resolver):
            return resolver()
        allowed = getattr(self._settings, "allowed_workspaces", [])
        if allowed:
            return str(allowed[0])
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _reply_now(self, target_type: str, openid: str, msg_id: str, text: str) -> None:
        """Send a one-off passive reply outside of a task (errors, denials, commands)."""
        content = clip_text(text.strip())
        try:
            if target_type == "group":
                self._client.send_group_text(openid, content, msg_id=msg_id, msg_seq=1)
            else:
                self._client.send_c2c_text(openid, content, msg_id=msg_id, msg_seq=1)
        except QQBotAPIError:
            logger.exception("Failed to send QQ bot reply")

    @staticmethod
    def _extract_image_attachments(d: dict[str, Any]) -> list[dict[str, Any]]:
        """Pull downloadable image attachments from an inbound QQ message.

        QQ carries media in ``d.attachments`` (each with ``content_type``,
        ``filename``, ``url``). We keep images only; other media types are ignored
        in this version.
        """
        raw = d.get("attachments")
        if not isinstance(raw, list):
            return []
        images: list[dict[str, Any]] = []
        for att in raw:
            if not isinstance(att, dict):
                continue
            url = str(att.get("url") or "").strip()
            if not url:
                continue
            content_type = str(att.get("content_type") or "").lower()
            filename = str(att.get("filename") or "")
            is_image = content_type.startswith("image") or filename.lower().endswith(
                (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
            )
            if not is_image:
                continue
            # QQ sometimes returns scheme-less CDN URLs; default to https.
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            images.append({"url": url, "name": filename or None})
        return images

    def _download_attachments(
        self,
        *,
        attachments: list[dict[str, Any]],
        workspace: Path,
        target_type: str,
        openid: str,
        msg_id: str,
    ) -> list[Path]:
        attach_dir = make_inbox_dir(workspace, ttl_hours=self._settings.inbox_ttl_hours)
        saved: list[Path] = []
        for idx, att in enumerate(attachments):
            url = str(att.get("url") or "")
            if not url:
                continue
            name = att.get("name") or Path(url.split("?", 1)[0]).name or f"image_{idx}.png"
            dest = attach_dir / self._safe_filename(str(name))
            try:
                self._download_url(url, dest)
                saved.append(dest)
            except Exception:
                logger.exception("Failed to download QQ attachment %s", url)
                self._reply_now(target_type, openid, msg_id, "Failed to download the sent image.")
        return saved

    @staticmethod
    def _download_url(url: str, dest: Path) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "Fluxion"})
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(req, timeout=30) as resp, dest.open("wb") as out:
            out.write(resp.read())

    @staticmethod
    def _safe_filename(name: str) -> str:
        # Keep only the basename and strip anything path- or shell-unfriendly.
        base = Path(name).name.strip() or "file"
        cleaned = re.sub(r"[^A-Za-z0-9._\-]+", "_", base).strip("._")
        return cleaned or "file"

    def _discard_task(self, task_id: str) -> None:
        with self._lock:
            self._reply_msg_ids.pop(task_id, None)
            self._reply_msg_seen_at.pop(task_id, None)
            self._msg_seq.pop(task_id, None)

    def _pop_stream_buffer(self, task_id: str) -> str:
        with self._lock:
            return self._stream_buffers.pop(task_id, "")

    def _locale_for_context(self, context: dict[str, Any]) -> str:
        return resolve_locale(
            mode=getattr(self._settings, "locale_mode", "auto"),
            fixed_locale=getattr(self._settings, "ui_locale", "en"),
            context=context,
        )


# Avoid circular import at runtime while keeping type hints.
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from fluxion.core.engine import GatewayCore
