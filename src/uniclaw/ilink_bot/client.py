from __future__ import annotations

import base64
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

import requests
from requests.exceptions import Timeout

from .crypto import (
    encode_aes_key,
    encrypt_aes_ecb,
    encrypted_size,
    generate_aes_key,
    md5_hex,
)
from .exceptions import ApiError, AuthError, MediaError, NoContextError
from .models import IncomingMessage, MessageItemType
from .storage import JsonStateStore

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_API_PREFIX = "ilink/bot"

MessageHandler = Callable[[IncomingMessage], Any]


class IlinkBotClient:
    def __init__(
        self,
        *,
        name: str = "",
        base_url: str = DEFAULT_BASE_URL,
        cdn_base_url: str = DEFAULT_CDN_BASE_URL,
        api_prefix: str = DEFAULT_API_PREFIX,
        cred_path: str | Path = "~/.ilink-bot/credentials.json",
        session: requests.Session | None = None,
        poll_timeout: float = 40.0,
    ):
        self.name = name or Path(cred_path).stem
        self.base_url = base_url.rstrip("/")
        self.cdn_base_url = cdn_base_url.rstrip("/")
        self.api_prefix = api_prefix.strip("/")
        self.store = JsonStateStore(cred_path)
        self.http = session or requests.Session()
        self.poll_timeout = poll_timeout
        self._handlers: list[MessageHandler] = []
        self._stop_event = threading.Event()
        self._current_user_id: str | None = self._restore_user_id()

        if self.store.base_url:
            self.base_url = self.store.base_url.rstrip("/")

    @property
    def is_logged_in(self) -> bool:
        return bool(self.store.bot_token)

    @property
    def credential_path(self) -> Path:
        return self.store.path

    def session_info(self) -> dict[str, Any]:
        return self.store.session_info()

    def on_message(self, handler: MessageHandler) -> MessageHandler:
        self._handlers.append(handler)
        return handler

    async def login(
        self,
        *,
        force: bool = False,
        poll_interval: float = 2.0,
        on_status: Callable[[str, Any], None] | None = None,
    ) -> None:
        """异步登录(获取 QR 码 + 轮询状态)。

        Args:
            force: 强制重新登录
            poll_interval: 轮询间隔(秒)
            on_status: 状态变化回调 (status_name, data) -> None
                - ("qrcode", qr_url): 获取到 QR 码
                - ("reused", None): 复用已有 token
                - ("scanned", status): 已扫码
                - ("confirmed", status): 已确认
                - ("success", status): 登录成功
                - ("failed", name): 登录失败
        """
        if self.store.bot_token and not force:
            if on_status:
                import inspect

                r = on_status("reused", None)
                if inspect.isawaitable(r):
                    await r
            return

        qr = self._get_qrcode()
        qrcode = _pick(qr, "qrcode")
        qr_url = _pick(qr, "qrcode_img_content") or qrcode
        if not qrcode:
            raise AuthError("QR login response does not include qrcode", payload=qr)

        if on_status:
            import inspect

            result = on_status("qrcode", qr_url)
            if inspect.isawaitable(result):
                await result
        else:
            self.print_qrcode(qr_url)

        await self.poll_login(qrcode, poll_interval=poll_interval, on_status=on_status)

    def get_updates(self) -> list[IncomingMessage]:
        self._require_login()
        payload = {"get_updates_buf": self.store.sync_buf}
        data = self._post("getupdates", payload, timeout=self.poll_timeout)
        self._check_api(data)
        self.store.sync_buf = data.get("get_updates_buf") or self.store.sync_buf

        messages = [
            IncomingMessage.from_raw(item)
            for item in data.get("msgs", [])
            if item.get("message_type") == 1
        ]
        for msg in messages:
            if msg.user_id and msg.context_token:
                self.store.set_context(msg.user_id, msg.context_token)
                self._current_user_id = msg.user_id
        self.store.save()
        return messages

    def run_forever(self, *, interval: float = 0.2) -> None:
        import asyncio

        self._require_login()
        self._stop_event.clear()
        while not self._stop_event.is_set():
            for msg in self.get_updates():
                asyncio.run(self._dispatch(msg))
            if interval:
                # 使用 wait 替代 sleep,可以被立即中断
                self._stop_event.wait(interval)

    def stop(self) -> None:
        self._stop_event.set()

    def logout(self) -> None:
        self.store.clear_session()

    def reply_text(self, text: str) -> dict[str, Any]:
        return self.send_text(text)

    def send_text(self, text: str) -> dict[str, Any]:
        user_id = self._require_user()
        token = self._context_for(user_id, None)
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": str(uuid.uuid4()),
                "context_token": token,
                "message_type": 2,
                "message_state": 2,
                "item_list": [
                    {"type": int(MessageItemType.TEXT), "text_item": {"text": text}}
                ],
            }
        }
        data = self._post("sendmessage", payload)
        self._check_api(data)
        return data

    def reply_image(
        self, image_path: str | Path, *, caption: str | None = None
    ) -> dict[str, Any]:
        return self.send_image(image_path, caption=caption)

    def send_image(
        self, image_path: str | Path, *, caption: str | None = None
    ) -> dict[str, Any]:
        user_id = self._require_user()
        token = self._context_for(user_id, None)
        media = self._upload_media(user_id, image_path, media_type=1)
        item_list: list[dict[str, Any]] = [
            {
                "type": int(MessageItemType.IMAGE),
                "image_item": {
                    "media": {
                        "encrypt_query_param": media["encrypt_query_param"],
                        "aes_key": media["aes_key"],
                        "encrypt_type": 1,
                    },
                    "mid_size": media["encrypted_file_size"],
                },
            }
        ]
        if caption:
            item_list.append(
                {"type": int(MessageItemType.TEXT), "text_item": {"text": caption}}
            )
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": str(uuid.uuid4()),
                "context_token": token,
                "message_type": 2,
                "message_state": 2,
                "item_list": item_list,
            }
        }
        data = self._post("sendmessage", payload)
        self._check_api(data)
        return data

    def reply_file(
        self, file_path: str | Path, *, file_name: str | None = None
    ) -> dict[str, Any]:
        return self.send_file(file_path, file_name=file_name)

    def send_file(
        self, file_path: str | Path, *, file_name: str | None = None
    ) -> dict[str, Any]:
        user_id = self._require_user()
        token = self._context_for(user_id, None)
        media = self._upload_media(user_id, file_path, media_type=3)
        file_path = Path(file_path)
        file_item: dict[str, Any] = {
            "media": {
                "encrypt_query_param": media["encrypt_query_param"],
                "aes_key": media["aes_key"],
                "encrypt_type": 1,
            },
            "file_name": file_name or file_path.name,
            "len": str(file_path.stat().st_size),
        }
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": str(uuid.uuid4()),
                "context_token": token,
                "message_type": 2,
                "message_state": 2,
                "item_list": [
                    {
                        "type": int(MessageItemType.FILE),
                        "file_item": file_item,
                    }
                ],
            }
        }
        data = self._post("sendmessage", payload)
        self._check_api(data)
        return data

    def reply_voice(
        self, voice_path: str | Path, *, duration_ms: int = 0
    ) -> dict[str, Any]:
        return self.send_voice(voice_path, duration_ms=duration_ms)

    def send_voice(
        self, voice_path: str | Path, *, duration_ms: int = 0
    ) -> dict[str, Any]:
        user_id = self._require_user()
        token = self._context_for(user_id, None)
        media = self._upload_media(user_id, voice_path, media_type=4)
        voice_item: dict[str, Any] = {
            "media": {
                "encrypt_query_param": media["encrypt_query_param"],
                "aes_key": media["aes_key"],
                "encrypt_type": 1,
            },
            "encode_type": 6,
            "bits_per_sample": 16,
            "sample_rate": 24000,
            "playtime": duration_ms,
        }
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": str(uuid.uuid4()),
                "context_token": token,
                "message_type": 2,
                "message_state": 2,
                "item_list": [
                    {
                        "type": int(MessageItemType.VOICE),
                        "voice_item": voice_item,
                    }
                ],
            }
        }
        data = self._post("sendmessage", payload)
        self._check_api(data)
        return data

    def send_typing(self) -> dict[str, Any]:
        return self._send_typing_status(1)

    def stop_typing(self) -> dict[str, Any]:
        return self._send_typing_status(2)

    def _send_typing_status(self, status: int) -> dict[str, Any]:
        user_id = self._require_user()
        token = self._context_for(user_id, None)
        config = self._post(
            "getconfig", {"ilink_user_id": user_id, "context_token": token}
        )
        self._check_api(config)
        ticket = config.get("typing_ticket") or config.get("typingTicket")
        if not ticket:
            raise ApiError("getconfig did not return typing_ticket", payload=config)
        data = self._post(
            "sendtyping",
            {"ilink_user_id": user_id, "typing_ticket": ticket, "status": status},
        )
        self._check_api(data)
        return data

    def _upload_media(
        self, user_id: str, path: str | Path, *, media_type: int
    ) -> dict[str, Any]:
        file_path = Path(path)
        if not file_path.exists():
            raise MediaError(f"Media file does not exist: {file_path}")
        raw = file_path.read_bytes()
        key = generate_aes_key()
        encrypted = encrypt_aes_ecb(raw, key)
        filekey = uuid.uuid4().hex
        upload_req = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": user_id,
            "rawsize": len(raw),
            "rawfilemd5": md5_hex(raw),
            "filesize": encrypted_size(len(raw)),
            "no_need_thumb": True,
            "aeskey": key.hex(),
        }
        upload_cfg = self._post("getuploadurl", upload_req)
        self._check_api(upload_cfg)
        upload_param = upload_cfg.get("upload_param") or upload_cfg.get("uploadParam")
        if not upload_param:
            raise MediaError(
                f"getuploadurl response missing upload_param: {upload_cfg}"
            )

        # 优先使用 upload_full_url,否则构建 URL
        upload_full_url = upload_cfg.get("upload_full_url")
        if upload_full_url:
            upload_url = upload_full_url
        else:
            upload_url = (
                f"{self.cdn_base_url}/upload"
                f"?encrypted_query_param={requests.utils.quote(upload_param)}"
                f"&filekey={requests.utils.quote(filekey)}"
            )

        cdn_resp = self.http.post(
            upload_url,
            data=encrypted,
            headers={"Content-Type": "application/octet-stream"},
            timeout=60,
        )
        if not cdn_resp.ok:
            err_code = cdn_resp.headers.get("x-error-code", "")
            err_msg = cdn_resp.headers.get("x-error-message", "")
            raise MediaError(
                f"CDN upload failed: {cdn_resp.status_code} [{err_code}] {err_msg}"
            )

        encrypt_query_param = cdn_resp.headers.get("x-encrypted-param")
        if not encrypt_query_param:
            try:
                cdn_data = cdn_resp.json()
                encrypt_query_param = cdn_data.get(
                    "encrypt_query_param"
                ) or cdn_data.get("encryptQueryParam")
            except ValueError:
                pass
        if not encrypt_query_param:
            encrypt_query_param = upload_cfg.get("encrypt_query_param") or upload_param

        return {
            **upload_req,
            "file_name": file_path.name,
            "aes_key": encode_aes_key(key),
            "encrypt_query_param": encrypt_query_param,
            "encrypted_file_size": len(encrypted),
            "cdn_headers": dict(cdn_resp.headers),
        }

    async def _dispatch(self, msg: IncomingMessage) -> None:
        import inspect

        for handler in self._handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(msg)
                else:
                    handler(msg)
            except Exception as e:
                print(f"[ilink_bot] Handler error: {e}")

    def _get_qrcode(self) -> dict[str, Any]:
        resp = self.http.get(
            self._url("get_bot_qrcode"), params={"bot_type": 3}, timeout=60
        )
        resp.raise_for_status()
        return resp.json()

    async def poll_login(
        self,
        qrcode: str,
        *,
        poll_interval: float = 2.0,
        on_status: Callable[[str, Any], None] | None = None,
    ) -> None:
        """异步轮询已有的 QR 码直到登录成功(跳过 _get_qrcode 步骤)。

        Args:
            qrcode: QR 会话标识
            poll_interval: 轮询间隔(秒)
            on_status: 状态变化回调 (status_name, data) -> None
        """
        import asyncio

        retry_count = 0
        max_retries = 3
        last_status = ""
        while True:
            try:
                status = await self._get_qrcode_status(qrcode)
                retry_count = 0
            except Timeout as e:
                retry_count += 1
                if retry_count >= max_retries:
                    raise AuthError(
                        f"QR status check timed out after {max_retries} retries",
                        payload={"error": str(e)},
                    )
                await asyncio.sleep(poll_interval)
                continue

            name = str(_pick(status, "status")).lower()
            # 状态变化时触发回调
            if name != last_status and on_status:
                last_status = name
                try:
                    import inspect

                    result = on_status(name, status)
                    if inspect.isawaitable(result):
                        await result
                except Exception as e:
                    logger.debug("on_status 回调执行失败: %s", e)
            if name in {"confirmed", "confirm", "success", "ok"} or _pick(
                status, "bot_token", "token"
            ):
                token = _pick(status, "bot_token")
                if not token:
                    if on_status:
                        r = on_status("error", "bot_token missing")
                        if inspect.isawaitable(r):
                            await r
                    raise AuthError(
                        "QR confirmed but bot_token is missing", payload=status
                    )
                returned_base_url = _pick(status, "baseurl")
                if returned_base_url:
                    self.base_url = str(returned_base_url).rstrip("/")
                self.store.save_session(
                    bot_token=token, base_url=self.base_url, login_response=status
                )
                if on_status:
                    r = on_status("success", status)
                    if inspect.isawaitable(r):
                        await r
                return
            if name in {"expired", "timeout", "cancel", "canceled", "cancelled"}:
                if on_status:
                    r = on_status("failed", name)
                    if inspect.isawaitable(r):
                        await r
                raise AuthError(f"QR login ended with status: {name}", payload=status)
            await asyncio.sleep(poll_interval)

    async def _get_qrcode_status(self, qrcode: str) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self._url("get_qrcode_status"),
                params={"qrcode": qrcode},
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()

    def _post(
        self, path: str, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self._require_login()
        body = {"base_info": {"channel_version": "0.1.0"}, **payload}
        resp = self.http.post(
            self._url(path),
            json=body,
            headers=self._headers(),
            timeout=timeout or 20,
        )
        resp.raise_for_status()
        return resp.json()

    def _url(self, path: str) -> str:
        suffix = path.strip("/")
        if self.api_prefix:
            suffix = f"{self.api_prefix}/{suffix}"
        return f"{self.base_url}/{suffix}"

    def _headers(self) -> dict[str, str]:
        token = self.store.bot_token or ""
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {token}",
            "X-WECHAT-UIN": _random_uin(),
            "iLink-App-Id": "bot",
            "iLink-App-ClientVersion": "65536",
        }

    def _context_for(self, user_id: str, context_token: str | None) -> str:
        token = context_token or self.store.get_context(user_id)
        if not token:
            raise NoContextError(f"No context_token cached for user: {user_id}")
        self.store.set_context(user_id, token)
        self.store.save()
        return token

    def _check_api(self, data: dict[str, Any]) -> None:
        code = data.get("ret", data.get("errcode"))
        if code in (None, 0):
            return
        if code == -14:
            self.store.clear_session()
            raise AuthError(
                "iLink session expired; run login again", code=code, payload=data
            )
        raise ApiError(
            data.get("errmsg") or data.get("message") or f"iLink API error: {code}",
            code=code,
            payload=data,
        )

    def _require_login(self) -> None:
        if not self.store.bot_token:
            raise AuthError("Not logged in. Call login() first.")

    def _restore_user_id(self) -> str | None:
        """从持久化 store 中恢复上次的 _current_user_id。"""
        contexts = self.store.data.get("contexts", {})
        if contexts:
            # 取最后一个有 context_token 的用户
            return next(reversed(contexts))
        return None

    def _require_user(self) -> str:
        if not self._current_user_id:
            raise NoContextError("No current user. Wait for an incoming message first.")
        return self._current_user_id

    def print_qrcode(self, qr_url: str) -> None:
        """在终端打印 ASCII 二维码。"""
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(qr_url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)


def _random_uin() -> str:
    value = int.from_bytes(os.urandom(4), "big")
    return base64.b64encode(str(value).encode("ascii")).decode("ascii")


def _pick(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    nested = data.get("data")
    if isinstance(nested, dict):
        return _pick(nested, *keys, default=default)
    return default
