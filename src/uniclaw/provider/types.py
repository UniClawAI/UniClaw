"""LLM 共享数据类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Protocol(StrEnum):
    """LLM API 协议类型。"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Effort(StrEnum):
    """推理努力级别 (OpenRouter)。"""

    XHIGH = "xhigh"
    HIGH = "high"
    MEDIUM = "medium"
    MINIMAL = "minimal"
    LOW = "low"
    NONE = "none"


@dataclass
class Usage:
    """Token 用量。

    cached_tokens / cache_write_tokens / cache_discount 来自 OpenRouter 缓存
    观测(prompt_tokens_details + 响应体顶层 cache_discount),非 OpenRouter 时保持 0。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0  # 命中缓存读取的 token 数
    cache_write_tokens: int = 0  # 写入缓存的 token 数
    cache_discount: float = 0.0  # 缓存折扣(读为正,写为负)

    def __post_init__(self):
        if self.total_tokens == 0:
            self.total_tokens = self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int | float]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_discount": self.cache_discount,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Usage":
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            cached_tokens=data.get("cached_tokens", 0),
            cache_write_tokens=data.get("cache_write_tokens", 0),
            cache_discount=data.get("cache_discount", 0.0),
        )
