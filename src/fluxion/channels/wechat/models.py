"""iLink Backend API protocol data models.

Mirrors the public protocol published by Tencent/openclaw-weixin without
importing any OpenClaw dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@dataclass
class Credentials:
    """Persisted after a successful QR-code login."""

    bot_token: str
    ilink_bot_id: str
    baseurl: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Message primitives
# ---------------------------------------------------------------------------


@dataclass
class TextItem:
    text: str


@dataclass
class MediaItem:
    """Placeholder for image/voice/video/file CDN media."""

    encrypt_query_param: str = ""
    aes_key: str = ""
    name: str = ""
    title: str = ""


@dataclass
class MessageItem:
    """A single item in a WeixinMessage.item_list.

    type mapping:
        1 → text   (text_item)
        2 → image  (image_item)
        3 → voice  (voice_item)
        4 → file   (file_item)
        5 → video  (video_item)
    """

    type: int
    text_item: TextItem | None = None
    image_item: MediaItem | None = None
    voice_item: MediaItem | None = None
    file_item: MediaItem | None = None
    video_item: MediaItem | None = None

    @classmethod
    def text(cls, content: str) -> MessageItem:
        return cls(type=1, text_item=TextItem(text=content))

    @classmethod
    def image(
        cls, encrypt_query_param: str, aes_key: str, name: str = "", title: str = ""
    ) -> MessageItem:
        return cls(
            type=2,
            image_item=MediaItem(
                encrypt_query_param=encrypt_query_param, aes_key=aes_key, name=name, title=title
            ),
        )

    @classmethod
    def file(
        cls, encrypt_query_param: str, aes_key: str, name: str = "", title: str = ""
    ) -> MessageItem:
        return cls(
            type=4,
            file_item=MediaItem(
                encrypt_query_param=encrypt_query_param, aes_key=aes_key, name=name, title=title
            ),
        )

    def to_dict(self) -> dict:
        d: dict = {"type": self.type}
        if self.text_item is not None:
            d["text_item"] = {"text": self.text_item.text}
        if self.image_item is not None:
            d["image_item"] = {
                "name": self.image_item.name,
                "title": self.image_item.title or self.image_item.name,
                "cdn_media": {
                    "encrypt_query_param": self.image_item.encrypt_query_param,
                    "aes_key": self.image_item.aes_key,
                },
            }
        if self.voice_item is not None:
            d["voice_item"] = {
                "name": self.voice_item.name,
                "title": self.voice_item.title or self.voice_item.name,
                "cdn_media": {
                    "encrypt_query_param": self.voice_item.encrypt_query_param,
                    "aes_key": self.voice_item.aes_key,
                },
            }
        if self.file_item is not None:
            d["file_item"] = {
                "name": self.file_item.name,
                "title": self.file_item.title or self.file_item.name,
                "cdn_media": {
                    "encrypt_query_param": self.file_item.encrypt_query_param,
                    "aes_key": self.file_item.aes_key,
                },
            }
        if self.video_item is not None:
            d["video_item"] = {
                "name": self.video_item.name,
                "title": self.video_item.title or self.video_item.name,
                "cdn_media": {
                    "encrypt_query_param": self.video_item.encrypt_query_param,
                    "aes_key": self.video_item.aes_key,
                },
            }
        return d


# ---------------------------------------------------------------------------
# Inbound message
# ---------------------------------------------------------------------------


@dataclass
class WeixinMessage:
    """Single message returned by getUpdates."""

    from_user_id: str = ""
    to_user_id: str = ""
    message_id: int = 0
    message_type: int = 0
    create_time_ms: int = 0
    session_id: str = ""
    group_id: str = ""
    context_token: str = ""
    item_list: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> WeixinMessage:
        return cls(
            from_user_id=str(data.get("from_user_id") or ""),
            to_user_id=str(data.get("to_user_id") or ""),
            message_id=int(data.get("message_id") or 0),
            message_type=int(data.get("message_type") or 0),
            create_time_ms=int(data.get("create_time_ms") or 0),
            session_id=str(data.get("session_id") or ""),
            group_id=str(data.get("group_id") or ""),
            context_token=str(data.get("context_token") or ""),
            item_list=list(data.get("item_list") or []),
        )

    def extract_text(self) -> str:
        """Extract plain text from all text items."""
        parts: list[str] = []
        for item in self.item_list:
            if isinstance(item, dict) and item.get("type") == 1:
                text_item = item.get("text_item")
                if isinstance(text_item, dict):
                    parts.append(str(text_item.get("text") or ""))
        return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# getUpdates response
# ---------------------------------------------------------------------------


@dataclass
class GetUpdatesResp:
    ret: int = 0
    errcode: int = 0
    errmsg: str = ""
    msgs: list[WeixinMessage] = field(default_factory=list)
    get_updates_buf: str = ""
    longpolling_timeout_ms: int = 30000

    @classmethod
    def from_dict(cls, data: dict) -> GetUpdatesResp:
        msgs: list[WeixinMessage] = []
        for raw_msg in data.get("msgs") or []:
            if isinstance(raw_msg, dict):
                msgs.append(WeixinMessage.from_dict(raw_msg))
        return cls(
            ret=int(data.get("ret") or 0),
            errcode=int(data.get("errcode") or 0),
            errmsg=str(data.get("errmsg") or ""),
            msgs=msgs,
            get_updates_buf=str(data.get("get_updates_buf") or ""),
            longpolling_timeout_ms=int(data.get("longpolling_timeout_ms") or 30000),
        )
