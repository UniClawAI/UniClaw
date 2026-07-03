from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Literal


class MessageItemType(IntEnum):
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


MessageType = Literal["text", "image", "voice", "file", "video", "mixed", "unknown"]


@dataclass
class MediaContent:
    type: MessageType
    url: str | None = None
    file_name: str | None = None
    size: int | None = None
    md5: str | None = None
    aes_key: str | None = None
    encrypt_query_param: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class IncomingMessage:
    user_id: str
    text: str
    type: MessageType
    timestamp: datetime
    context_token: str
    raw: dict[str, Any]
    images: list[MediaContent] = field(default_factory=list)
    voices: list[MediaContent] = field(default_factory=list)
    files: list[MediaContent] = field(default_factory=list)
    videos: list[MediaContent] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "IncomingMessage":
        user_id = (
            raw.get("from_user_id")
            or raw.get("user_id")
            or raw.get("ilink_user_id")
            or raw.get("sender")
            or ""
        )
        context_token = raw.get("context_token") or raw.get("contextToken") or ""
        timestamp = _timestamp(
            raw.get("create_time_ms") or raw.get("createTimeMs") or raw.get("time")
        )
        items = raw.get("item_list") or raw.get("itemList") or raw.get("items") or []

        texts: list[str] = []
        images: list[MediaContent] = []
        voices: list[MediaContent] = []
        files: list[MediaContent] = []
        videos: list[MediaContent] = []

        for item in items:
            item_type = item.get("type")
            if item_type == MessageItemType.TEXT:
                text_item = item.get("text_item") or item.get("textItem") or {}
                if text_item.get("text"):
                    texts.append(str(text_item["text"]))
            elif item_type == MessageItemType.IMAGE:
                images.append(
                    _media(
                        "image", item.get("image_item") or item.get("imageItem") or {}
                    )
                )
            elif item_type == MessageItemType.VOICE:
                voices.append(
                    _media(
                        "voice", item.get("voice_item") or item.get("voiceItem") or {}
                    )
                )
            elif item_type == MessageItemType.FILE:
                files.append(
                    _media("file", item.get("file_item") or item.get("fileItem") or {})
                )
            elif item_type == MessageItemType.VIDEO:
                videos.append(
                    _media(
                        "video", item.get("video_item") or item.get("videoItem") or {}
                    )
                )

        msg_type: MessageType = "unknown"
        present = [bool(texts), bool(images), bool(voices), bool(files), bool(videos)]
        if sum(present) > 1:
            msg_type = "mixed"
        elif texts:
            msg_type = "text"
        elif images:
            msg_type = "image"
        elif voices:
            msg_type = "voice"
        elif files:
            msg_type = "file"
        elif videos:
            msg_type = "video"

        return cls(
            user_id=user_id,
            text="\n".join(texts),
            type=msg_type,
            timestamp=timestamp,
            context_token=context_token,
            raw=raw,
            images=images,
            voices=voices,
            files=files,
            videos=videos,
        )


def _timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, timezone.utc).astimezone()
    return datetime.now(timezone.utc).astimezone()


def _media(kind: MessageType, raw: dict[str, Any]) -> MediaContent:
    cdn = raw.get("cdn_media") or raw.get("cdnMedia") or raw
    media = raw.get("media") or {}
    return MediaContent(
        type=kind,
        url=raw.get("url")
        or cdn.get("url")
        or media.get("full_url")
        or media.get("url"),
        file_name=raw.get("file_name") or raw.get("fileName") or raw.get("name"),
        size=raw.get("size") or raw.get("filesize") or raw.get("file_size"),
        md5=raw.get("md5") or raw.get("rawfilemd5"),
        aes_key=raw.get("aeskey")
        or cdn.get("aes_key")
        or cdn.get("aesKey")
        or media.get("aes_key")
        or media.get("aesKey"),
        encrypt_query_param=cdn.get("encrypt_query_param")
        or cdn.get("encryptQueryParam")
        or media.get("encrypt_query_param")
        or media.get("encryptQueryParam"),
        raw=raw,
    )
