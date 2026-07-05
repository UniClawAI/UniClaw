"""LLM 调用层 — 多协议支持 (OpenAI / Anthropic)。"""

from uniclaw.provider.common import compare_urls
from uniclaw.provider.router import achat, astream, chat, stream
from uniclaw.provider.thought_parser import ThoughtParser
from uniclaw.provider.types import Effort, Protocol, Usage

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uniclaw.tools.session.session import AIMessage, StreamChunk

__all__ = [
    # 数据类型
    "Usage",
    "StreamChunk",
    "AIMessage",
    "Effort",
    "Protocol",
    # 核心 API (路由器 — 自动选择协议)
    "stream",
    "astream",
    "chat",
    "achat",
    # 工具
    "ThoughtParser",
    "compare_urls",
]
