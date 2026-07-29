"""Token 估算工具 — 模型编码器映射与 token 计数。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 模型到 tiktoken 编码器的映射
MODEL_ENCODINGS: dict[str, str] = {
    # GPT-4o / GPT-4.1 系列使用 o200k_base
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-4.1": "o200k_base",
    "gpt-4.1-mini": "o200k_base",
    "gpt-4.1-nano": "o200k_base",
    # 其他 GPT 系列使用 cl100k_base
    "gpt-3.5-turbo": "cl100k_base",
    "gpt-4": "cl100k_base",
    "gpt-4-turbo": "cl100k_base",
}

_encoder_cache: dict[str, Any] = {}


def get_encoder(model: str | None = None):
    """获取 tiktoken 编码器(带缓存)。tiktoken 未安装时返回 None。"""
    try:
        import tiktoken
    except ImportError:
        return None
    if not model:
        return tiktoken.get_encoding("cl100k_base")
    short_name = model.split("/")[-1] if "/" in model else model
    encoding_name = "cl100k_base"
    for key, enc in MODEL_ENCODINGS.items():
        if short_name.startswith(key):
            encoding_name = enc
            break
    if encoding_name not in _encoder_cache:
        try:
            _encoder_cache[encoding_name] = tiktoken.get_encoding(encoding_name)
        except Exception as e:
            logger.debug("加载 tiktoken 编码器 %s 失败: %s", encoding_name, e)
            return None
    return _encoder_cache[encoding_name]


def count_tokens(text: str, model: str | None = None) -> int:
    """估算文本的 token 数量。tiktoken 未安装时按字符数近似。"""
    encoder = get_encoder(model)
    if encoder is None:
        return int(len(text) / 2.8)
    try:
        return len(encoder.encode(text))
    except Exception as e:
        logger.debug("token 编码失败,按字符近似: %s", e)
        return int(len(text) / 2.8)


def slice_by_tokens(text: str, max_tokens: int, from_end: bool = False) -> str:
    """按 token 数精确截取文本。

    通过 tiktoken 编码 → 切片 token → 解码,避免字符/token 换算误差。

    Args:
        text: 原始文本
        max_tokens: 要保留的 token 数
        from_end: False 取前 N 个 token,True 取后 N 个 token

    Returns:
        截取后的文本。tiktoken 不可用时按字符近似截取。
    """
    encoder = get_encoder()
    if encoder is None:
        # fallback: 按字符近似
        approx_chars = int(max_tokens * 2.8)
        return text[-approx_chars:] if from_end else text[:approx_chars]
    try:
        tokens = encoder.encode(text)
        if len(tokens) <= max_tokens:
            return text
        sliced = tokens[-max_tokens:] if from_end else tokens[:max_tokens]
        return encoder.decode(sliced)
    except Exception as e:
        logger.debug("token 精确截取失败,按字符近似: %s", e)
        approx_chars = int(max_tokens * 2.8)
        return text[-approx_chars:] if from_end else text[:approx_chars]
