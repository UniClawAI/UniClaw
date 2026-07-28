"""WebUI 模式旋转器 — 通过 WebSocket 推送 spinner 状态到前端。"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Callable, Awaitable

from uniclaw.spinner import BaseSpinner

logger = logging.getLogger(__name__)


class WebSpinner(BaseSpinner):
    """WebUI 模式旋转器 — 通过 WebSocket 推送 spinner 状态到前端。

    每个会话独立持有 WebSpinner 实例,通过 config.spinner 传递给 agent 和子 agent。
    set_send_callback() 在 WebSocket 连接建立时调用,绑定到该会话的连接。
    """

    def __init__(self):
        self._stack: list[tuple[str, float, str]] = []
        self._lock = threading.Lock()
        self._send_callback: Callable[[dict], Awaitable[None]] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session_id: str | None = None

    def set_session_id(self, session_id: str):
        """设置会话 ID,用于在事件中标记来源。"""
        self._session_id = session_id

    def set_send_callback(self, callback: Callable[[dict], Awaitable[None]]):
        """设置 WebSocket 发送回调。callback 是一个 async 函数,接收 JSON 消息。

        同时捕获当前 event loop 引用,确保从 agent 线程调度时使用正确的 loop。
        """
        self._send_callback = callback
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def start(self, text: str = "waiting...", wait_id: str | None = None) -> str:
        if wait_id is None:
            wait_id = f"WebSpinner_{uuid.uuid4().hex[:8]}"
        with self._lock:
            # 检查是否已有相同 wait_id(更新 text)
            for i, (t, ts, wid) in enumerate(self._stack):
                if wid == wait_id:
                    if t != text:
                        self._stack[i] = (text, time.time(), wait_id)
                        self._schedule_send(
                            self._make_event(
                                "spinner_update", text=text, wait_id=wait_id
                            )
                        )
                    return wait_id
            # 新增
            self._stack.append((text, time.time(), wait_id))
            self._schedule_send(
                self._make_event("spinner_start", text=text, wait_id=wait_id)
            )
        return wait_id

    def stop(self, wait_id: str) -> None:
        with self._lock:
            self._stack = [(t, ts, wid) for t, ts, wid in self._stack if wid != wait_id]
            self._schedule_send(self._make_event("spinner_stop", wait_id=wait_id))

    def _make_event(self, event_type: str, **kwargs) -> dict:
        """构建事件消息,包含 session_id。"""
        msg = {"event": event_type, **kwargs}
        if self._session_id:
            msg["session_id"] = self._session_id
        return msg

    def is_active(self) -> bool:
        return len(self._stack) > 0

    def get_display(self) -> str:
        with self._lock:
            if self._stack:
                return self._stack[-1][0]  # 堆栈顶部的 text
            return ""

    def get_all_displays(self) -> list[str]:
        """获取堆栈中所有 spinner 的显示文本(用于前端堆栈渲染)。"""
        with self._lock:
            return [t for t, _, _ in self._stack]

    def _schedule_send(self, msg: dict):
        """异步发送消息到 WebSocket(非阻塞,从 agent 线程安全调用)。"""
        if self._send_callback and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._send_callback(msg), self._loop)
            except Exception as e:
                logger.debug("WebSocket spinner 消息发送失败: %s", e)
