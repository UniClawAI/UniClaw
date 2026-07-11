"""工具流式输出 — 通用接口,任何工具可通过 tool_stream() 推送实时输出到前端。

流式数据仅用于 UI 展示,不参与 LLM 交互。工具完成后,最终结果覆盖流式显示。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Awaitable, Callable

# 每个协程独立的流式回调(agent 在执行工具前设置)
# 回调签名: async (content: str) -> None
# content 是单次推送的文本片段(通常是一行)
_stream_cb: ContextVar[Callable[[str], Awaitable[None]] | None] = ContextVar(
    "_stream_cb", default=None
)


async def tool_stream(content: str) -> None:
    """推送工具执行过程中的流式输出。

    任何工具在执行过程中均可调用。若当前协程未设置回调(非 WebUI 模式、
    或工具不在 agent 框架内执行),调用会被静默忽略。

    Args:
        content: 要推送的文本片段(通常是一行输出,包含换行符)。
    """
    cb = _stream_cb.get(None)
    if cb is not None:
        await cb(content)


def set_stream_callback(cb: Callable[[str], Awaitable[None]] | None) -> object:
    """设置当前协程的流式回调,返回 token 用于 reset。

    仅供 agent 框架调用,工具不应直接使用此函数。
    """
    return _stream_cb.set(cb)


def reset_stream_callback(token: object) -> None:
    """重置流式回调。仅供 agent 框架调用。"""
    _stream_cb.reset(token)
