"""LLM 调用回退支持 — 当主模型失败时自动尝试备用模型。

提供 chat/achat 的包装版本,支持 model_name 为 list 时按顺序回退。
每个模型内按错误分类(ErrorCategory)执行差异化重试与退避。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from uniclaw.provider import router
from uniclaw.provider.error_classifier import (
    ErrorCategory,
    classify_error,
    get_backoff_delay,
    get_max_retries,
)
from uniclaw.console.ui import warn

if TYPE_CHECKING:
    from uniclaw.tools.session.session import AIMessage, Session
    from uniclaw.config import AppConfig


def _run_async(coro):
    """在当前线程安全地运行一个协程。

    若当前已有运行中的事件循环(如被流式循环内调用),返回 None 跳过,
    避免 asyncio.run 在嵌套 loop 中抛 RuntimeError。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return None


def chat(
    system_prompt: str,
    session: Session,
    *,
    model_name: str | list[str] = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    response_format: dict | None = None,
    timeout: float | None = None,
    config: AppConfig | None = None,
) -> AIMessage:
    """同步调用 LLM,支持模型列表回退与分类重试。

    - model_name 为 str 时转为 [model_name] 统一处理
    - model_name 为 list 时按顺序尝试,每个模型内按错误分类重试,
      重试耗尽才回退下一个模型,成功返回 AIMessage
    - AUTH/UNKNOWN 类错误不重试,立即回退下一个模型
    - CONTEXT_OVERFLOW 时先尝试压缩会话再重试

    Args:
        system_prompt: 系统提示词。
        session: 会话对象。
        model_name: 模型名称或模型列表。
        multimodal_model_name: 多模态降级模型名称。
        temperature: 采样温度。
        max_tokens: 最大生成 token 数。
        top_p: 核采样参数。
        tools: 工具列表。
        enable_thinking: 是否启用思考。
        thinking: 是否启用思考模式。
        response_format: 响应格式。
        timeout: 单次请求超时秒数,覆盖客户端默认值。为 None 时使用默认超时。
        config: 应用配置。

    Returns:
        AIMessage: 转换后的 AI 回复消息。
    """
    if isinstance(model_name, str):
        model_name = [model_name]

    last_error = None
    for model in model_name:
        attempt = 0
        while True:
            try:
                return router.chat(
                    system_prompt=system_prompt,
                    session=session,
                    model_name=model,
                    multimodal_model_name=multimodal_model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                    tools=tools,
                    enable_thinking=enable_thinking,
                    thinking=thinking,
                    response_format=response_format,
                    timeout=timeout,
                    config=config,
                )
            except Exception as e:
                cat = classify_error(e)
                _run_async(warn(f"模型 {model} 第 {attempt + 1} 次调用失败: {e} ({cat})", config))
                max_retries = get_max_retries(cat)
                if attempt >= max_retries:
                    _run_async(warn(f"模型 {model} 重试耗尽({cat}),回退下一个模型", config))
                    last_error = e
                    break
                if cat == ErrorCategory.CONTEXT_OVERFLOW:
                    _run_async(session.maybe_compact(config))
                delay = get_backoff_delay(cat, attempt + 1)
                if delay > 0:
                    time.sleep(delay)
                attempt += 1

    raise last_error or RuntimeError("所有模型调用失败")


async def achat(
    system_prompt: str,
    session: Session,
    *,
    model_name: str | list[str] = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    response_format: dict | None = None,
    timeout: float | None = None,
    config: AppConfig | None = None,
) -> AIMessage:
    """异步调用 LLM,支持模型列表回退与分类重试。

    - model_name 为 str 时转为 [model_name] 统一处理
    - model_name 为 list 时按顺序尝试,每个模型内按错误分类重试,
      重试耗尽才回退下一个模型,成功返回 AIMessage
    - AUTH/UNKNOWN 类错误不重试,立即回退下一个模型
    - CONTEXT_OVERFLOW 时先尝试压缩会话再重试

    Args:
        system_prompt: 系统提示词。
        session: 会话对象。
        model_name: 模型名称或模型列表。
        multimodal_model_name: 多模态降级模型名称。
        temperature: 采样温度。
        max_tokens: 最大生成 token 数。
        top_p: 核采样参数。
        tools: 工具列表。
        enable_thinking: 是否启用思考。
        thinking: 是否启用思考模式。
        response_format: 响应格式。
        timeout: 单次请求超时秒数,覆盖客户端默认值。为 None 时使用默认超时。
        config: 应用配置。

    Returns:
        AIMessage: 转换后的 AI 回复消息。
    """
    if isinstance(model_name, str):
        model_name = [model_name]

    last_error = None
    for model in model_name:
        attempt = 0
        while True:
            try:
                return await router.achat(
                    system_prompt=system_prompt,
                    session=session,
                    model_name=model,
                    multimodal_model_name=multimodal_model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                    tools=tools,
                    enable_thinking=enable_thinking,
                    thinking=thinking,
                    response_format=response_format,
                    timeout=timeout,
                    config=config,
                )
            except Exception as e:
                cat = classify_error(e)
                await warn(f"模型 {model} 第 {attempt + 1} 次调用失败: {e} ({cat})", config)
                max_retries = get_max_retries(cat)
                if attempt >= max_retries:
                    await warn(f"模型 {model} 重试耗尽({cat}),回退下一个模型", config)
                    last_error = e
                    break
                if cat == ErrorCategory.CONTEXT_OVERFLOW:
                    await session.maybe_compact(config)
                delay = get_backoff_delay(cat, attempt + 1)
                if delay > 0:
                    await asyncio.sleep(delay)
                attempt += 1

    raise last_error or RuntimeError("所有模型调用失败")
