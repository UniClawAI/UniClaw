"""LLM 调用回退支持 — 当主模型失败时自动尝试备用模型。

提供 chat/achat 的包装版本,支持 model_name 为 list 时按顺序回退。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uniclaw.provider import router
from uniclaw.utils.logger import get_logger
from uniclaw.console.ui import warn

if TYPE_CHECKING:
    from uniclaw.tools.session.session import AIMessage


def chat(
    system_prompt: str,
    session,
    *,
    model_name: str | list[str] = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    config=None,
) -> AIMessage:
    """同步调用 LLM,支持模型列表回退。

    - model_name 为 str 时转为 [model_name] 统一处理
    - model_name 为 list 时按顺序尝试,出错自动回退下一个模型,成功返回 AIMessage
    """
    if isinstance(model_name, str):
        model_name = [model_name]

    last_error = None
    for model in model_name:
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
                config=config,
            )
        except Exception as e:
            get_logger(
                "provider.fallback", config.root_dir if config else None
            ).warning("模型 %s 调用失败: %s, 回退下一个模型", model, e)
            last_error = e

    raise last_error or RuntimeError("所有模型调用失败")


async def achat(
    system_prompt: str,
    session,
    *,
    model_name: str | list[str] = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    config=None,
) -> AIMessage:
    """异步调用 LLM,支持模型列表回退。

    - model_name 为 str 时转为 [model_name] 统一处理
    - model_name 为 list 时按顺序尝试,出错自动回退下一个模型,成功返回 AIMessage
    """
    if isinstance(model_name, str):
        model_name = [model_name]

    last_error = None
    for model in model_name:
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
                config=config,
            )
        except Exception as e:
            await warn(f"模型 {model} 调用失败: {e}, 回退下一个模型", config)
            last_error = e

    raise last_error or RuntimeError("所有模型调用失败")
