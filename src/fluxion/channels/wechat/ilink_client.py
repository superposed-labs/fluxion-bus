"""HTTP client for the iLink Backend API.

Implements the five public endpoints (getupdates, sendmessage, getconfig,
sendtyping, getuploadurl) plus the QR-code login flow, using only the
standard library ``urllib`` — no third-party HTTP dependency required.
"""

from __future__ import annotations

import base64
import json
import random
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fluxion.channels.attachments import (
    MAX_ATTACHMENT_BYTES,
    AttachmentNormalizationError,
    read_stream_limited,
)
from fluxion.channels.wechat.models import (
    Credentials,
    GetUpdatesResp,
    MessageItem,
)
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
_LOGIN_POLL_INTERVAL_SEC = 2.0
_LOGIN_TIMEOUT_SEC = 180
_LONGPOLL_TIMEOUT_SEC = 45  # allow slightly more than the server's ~30s timeout
_REQUEST_TIMEOUT_SEC = 30
_BOT_AGENT = "Fluxion/1.0.0"


def _random_wechat_uin() -> str:
    """Generate a base64-encoded random uint32 for X-WECHAT-UIN."""
    value = random.randint(0, 0xFFFFFFFF)
    return base64.b64encode(struct.pack("<I", value)).decode("ascii")


class ILinkClient:
    """Stateless HTTP client for the iLink Backend API."""

    def __init__(
        self,
        *,
        bot_token: str = "",
        ilink_bot_id: str = "",
        baseurl: str = "",
    ) -> None:
        self._bot_token = bot_token
        self._ilink_bot_id = ilink_bot_id
        self._baseurl = (baseurl or _DEFAULT_BASE_URL).rstrip("/")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self._bot_token}",
            "X-WECHAT-UIN": _random_wechat_uin(),
        }

    def _post(
        self,
        path: str,
        body: dict | None = None,
        *,
        timeout: float = _REQUEST_TIMEOUT_SEC,
    ) -> dict:
        url = f"{self._baseurl}{path}"
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers=self._auth_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path: str, *, timeout: float = _REQUEST_TIMEOUT_SEC) -> dict:
        url = f"{self._baseurl}{path}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ------------------------------------------------------------------
    # QR-code login flow
    # ------------------------------------------------------------------

    def get_bot_qrcode(self) -> dict:
        """GET /ilink/bot/get_bot_qrcode?bot_type=3

        Returns ``{qrcode, qrcode_img_content}`` on success.
        """
        return self._get("/ilink/bot/get_bot_qrcode?bot_type=3")

    def poll_qrcode_status(self, qrcode_id: str) -> dict:
        """GET /ilink/bot/get_qrcode_status?qrcode=<id>

        Returns ``{status: wait|scaned|confirmed, bot_token?, ...}``.
        """
        return self._get(f"/ilink/bot/get_qrcode_status?qrcode={qrcode_id}")

    def login_interactive(self, *, json_mode: bool = False) -> Credentials:
        """Run the full QR-code login flow.

        1. Fetch QR code URL / content
        2. Print it to the terminal (or output JSON)
        3. Poll until ``confirmed``
        4. Return credentials

        Raises ``RuntimeError`` on timeout or protocol error.
        """
        qr = self.get_bot_qrcode()
        qrcode_id = str(qr.get("qrcode") or "").strip()
        qrcode_url = str(qr.get("qrcode_img_content") or "").strip()
        if not qrcode_id:
            raise RuntimeError("get_bot_qrcode returned no qrcode id")

        # Print QR code: try generating ASCII art, fall back to URL
        if json_mode:
            print(
                json.dumps(
                    {
                        "event": "qrcode",
                        "qrcode_id": qrcode_id,
                        "qrcode_url": qrcode_url or qrcode_id,
                    }
                ),
                flush=True,
            )
        else:
            self._print_qrcode(qrcode_url or qrcode_id)

        deadline = time.monotonic() + _LOGIN_TIMEOUT_SEC
        last_status = ""
        while time.monotonic() < deadline:
            time.sleep(_LOGIN_POLL_INTERVAL_SEC)
            status_resp = self.poll_qrcode_status(qrcode_id)
            status = str(status_resp.get("status") or "").strip().lower()
            if status == "wait":
                continue
            if status == "scaned":
                if status != last_status:
                    if json_mode:
                        print(json.dumps({"event": "scanned"}), flush=True)
                    else:
                        print("[Fluxion] QR code scanned — please confirm in WeChat app...")
                    last_status = status
                continue
            if status == "confirmed":
                token = str(status_resp.get("bot_token") or "").strip()
                bot_id = str(status_resp.get("ilink_bot_id") or "").strip()
                baseurl = str(status_resp.get("baseurl") or "").strip()
                if not token or not bot_id:
                    raise RuntimeError("confirmed but no bot_token/ilink_bot_id")
                if json_mode:
                    print(json.dumps({"event": "confirmed", "bot_id": bot_id}), flush=True)
                return Credentials(
                    bot_token=token,
                    ilink_bot_id=bot_id,
                    baseurl=baseurl or self._baseurl,
                )
            if status in {"expired", "error"}:
                raise RuntimeError(f"QR code login failed with status: {status}")
        raise RuntimeError("QR code login timed out")

    @staticmethod
    def _print_qrcode(content: str) -> None:
        """Try to render a terminal QR code; fall back to printing the URL."""
        try:
            import qrcode as qr_lib  # type: ignore[import-untyped]

            qr = qr_lib.QRCode(border=1)
            qr.add_data(content)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except ImportError:
            print(f"[Fluxion] Open this URL in a browser or scan with WeChat:\n  {content}")

    # ------------------------------------------------------------------
    # Backend API — getupdates
    # ------------------------------------------------------------------

    def get_updates(self, get_updates_buf: str = "") -> GetUpdatesResp:
        """POST /ilink/bot/getupdates — long-poll for new messages.

        The server holds the connection open for ~30 s when there are no
        messages, so we set a generous read timeout.
        """
        body: dict[str, Any] = {"get_updates_buf": get_updates_buf}
        data = self._post(
            "/ilink/bot/getupdates",
            body,
            timeout=_LONGPOLL_TIMEOUT_SEC,
        )
        resp = GetUpdatesResp.from_dict(data)
        if resp.ret != 0 or resp.errcode != 0:
            raise RuntimeError(
                f"getupdates failed with ret={resp.ret}, errcode={resp.errcode}, errmsg={resp.errmsg}"
            )
        return resp

    # ------------------------------------------------------------------
    # Backend API — sendmessage
    # ------------------------------------------------------------------

    def send_message(
        self,
        *,
        to_user_id: str,
        context_token: str,
        items: list[MessageItem],
    ) -> None:
        """POST /ilink/bot/sendmessage — send a message."""
        import uuid

        client_id = str(uuid.uuid4())
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "context_token": context_token,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [item.to_dict() for item in items],
            }
        }
        resp = self._post("/ilink/bot/sendmessage", body)
        ret = int(resp.get("ret") or 0)
        errcode = int(resp.get("errcode") or 0)
        if ret != 0 or errcode != 0:
            raise RuntimeError(
                f"sendmessage failed with ret={ret}, errcode={errcode}, errmsg={resp.get('errmsg')}"
            )

    # ------------------------------------------------------------------
    # Backend API — getconfig
    # ------------------------------------------------------------------

    def get_config(self, *, user_id: str, context_token: str) -> dict:
        """POST /ilink/bot/getconfig — fetch typing_ticket etc."""
        body = {
            "ilink_user_id": user_id,
            "context_token": context_token,
        }
        return self._post("/ilink/bot/getconfig", body)

    # ------------------------------------------------------------------
    # Backend API — sendtyping
    # ------------------------------------------------------------------

    def send_typing(
        self,
        *,
        user_id: str,
        typing_ticket: str,
        status: int = 1,
    ) -> None:
        """POST /ilink/bot/sendtyping — show/cancel typing indicator.

        status: 1 = typing, 2 = cancel
        """
        body = {
            "ilink_user_id": user_id,
            "typing_ticket": typing_ticket,
            "status": status,
        }
        try:
            self._post("/ilink/bot/sendtyping", body)
        except Exception:
            logger.debug("sendtyping failed", exc_info=True)

    # ------------------------------------------------------------------
    # Backend API — getuploadurl (stub for media uploads)
    # ------------------------------------------------------------------

    def get_upload_url(
        self,
        *,
        file_key: str,
        media_type: int,
        to_user_id: str,
        raw_size: int,
        raw_file_md5: str,
        filesize: int,
        aes_key_hex: str,
    ) -> dict:
        """POST /ilink/bot/getuploadurl — get pre-signed CDN upload URL.

        Provides media upload support.
        """
        body = {
            "filekey": file_key,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": raw_size,
            "rawfilemd5": raw_file_md5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aes_key_hex,
        }
        return self._post("/ilink/bot/getuploadurl", body)

    def upload_file_to_cdn(
        self,
        *,
        upload_url: str,
        file_path: Path,
        aes_key: str,
        key_is_hex: bool,
    ) -> str:
        """Encrypt file contents and upload to WeChat CDN using POST.

        Returns the x-encrypted-param header from the response.
        """
        from fluxion.channels.wechat.crypto import decode_aes_key, encrypt_aes_ecb

        raw_bytes = file_path.read_bytes()
        key_bytes = decode_aes_key(aes_key, key_is_hex)
        ciphertext = encrypt_aes_ecb(raw_bytes, key_bytes)

        req = urllib.request.Request(
            upload_url,
            data=ciphertext,
            method="POST",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(ciphertext)),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                enc_param = resp.headers.get("x-encrypted-param")
                if not enc_param:
                    logger.warning(
                        "x-encrypted-param header missing in CDN response headers: %s",
                        dict(resp.headers),
                    )
                    try:
                        body_str = resp.read().decode("utf-8")
                        if body_str:
                            body_data = json.loads(body_str)
                            enc_param = body_data.get("x-encrypted-param") or body_data.get(
                                "encrypt_query_param"
                            )
                    except Exception:
                        pass
                if not enc_param:
                    raise RuntimeError("Failed to retrieve x-encrypted-param from CDN response")
                return enc_param
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(2048).decode("utf-8", errors="replace").strip()
            except Exception:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"WeChat CDN upload returned HTTP {exc.code}{suffix}") from exc

    def download_file_from_cdn(
        self,
        *,
        encrypt_query_param: str,
        dest_path: Path,
        aes_key: str,
        key_is_hex: bool,
        full_url: str | None = None,
    ) -> None:
        """Download encrypted file from CDN and decrypt to dest_path."""
        from fluxion.channels.wechat.crypto import decode_aes_key, decrypt_aes_ecb

        if full_url:
            url = full_url
        else:
            url = f"{self._baseurl}/download?encrypted_query_param={encrypt_query_param}"

        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=120) as resp:
            ciphertext = read_stream_limited(resp, max_bytes=MAX_ATTACHMENT_BYTES + 32)

        key_bytes = decode_aes_key(aes_key, key_is_hex)
        decrypted = decrypt_aes_ecb(ciphertext, key_bytes)
        if len(decrypted) > MAX_ATTACHMENT_BYTES:
            raise AttachmentNormalizationError(
                f"attachment exceeds {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB"
            )

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(decrypted)

    # ------------------------------------------------------------------
    # Credential application
    # ------------------------------------------------------------------

    def apply_credentials(self, creds: Credentials) -> None:
        """Update the client with stored credentials."""
        self._bot_token = creds.bot_token
        self._ilink_bot_id = creds.ilink_bot_id
        if creds.baseurl:
            self._baseurl = creds.baseurl.rstrip("/")

    @property
    def ilink_bot_id(self) -> str:
        return self._ilink_bot_id

    @property
    def is_authenticated(self) -> bool:
        return bool(self._bot_token and self._ilink_bot_id)
