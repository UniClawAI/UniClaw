"""LLM 路由器 — 根据配置自动选择协议,对外暴露统一 API。

所有函数接受 system_prompt + session,路由器按协议分别构建参数。
底层 provider 内部会调用 resolve_params() 从 model_name 解析 key/url/proxy。
"""

from __future__ import annotations

from uniclaw.provider.common import get_protocol
from uniclaw.provider.types import Protocol

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uniclaw.tools.session.session import AIMessage, Session, StreamChunk


def stream(
    system_prompt: str,
    session: Session,
    *,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    response_format: dict | None = None,
    config=None,
) -> Iterator[StreamChunk]:
    """流式调用 LLM,每次 yield StreamChunk (delta)。自动选择提供商。"""
    provider = get_protocol(config, model_name=model_name)

    if provider == Protocol.ANTHROPIC:
        from uniclaw.provider import anthropic_provider

        yield from anthropic_provider.stream(
            system_prompt=system_prompt,
            messages=session.to_anthropic_messages(),
            model_name=model_name,
            multimodal_model_name=multimodal_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            tools=tools,
            enable_thinking=enable_thinking,
            thinking=thinking,
            config=config,
        )
    else:
        from uniclaw.provider import openai_provider

        messages = [
            {"role": "system", "content": system_prompt}
        ] + session.to_openai_messages()
        yield from openai_provider.stream(
            messages,
            model_name=model_name,
            multimodal_model_name=multimodal_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            tools=tools,
            enable_thinking=enable_thinking,
            thinking=thinking,
            response_format=response_format,
            config=config,
        )


async def astream(
    system_prompt: str,
    session: Session,
    *,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    response_format: dict | None = None,
    config=None,
) -> AsyncIterator[StreamChunk]:
    """异步流式调用 LLM,每次 yield StreamChunk (delta)。自动选择提供商。"""
    provider = get_protocol(config, model_name=model_name)

    if provider == Protocol.ANTHROPIC:
        from uniclaw.provider import anthropic_provider

        async for chunk in anthropic_provider.astream(
            system_prompt=system_prompt,
            messages=session.to_anthropic_messages(),
            model_name=model_name,
            multimodal_model_name=multimodal_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            tools=tools,
            enable_thinking=enable_thinking,
            thinking=thinking,
            config=config,
        ):
            yield chunk
    else:
        from uniclaw.provider import openai_provider

        messages = [
            {"role": "system", "content": system_prompt}
        ] + session.to_openai_messages()
        async for chunk in openai_provider.astream(
            messages,
            model_name=model_name,
            multimodal_model_name=multimodal_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            tools=tools,
            enable_thinking=enable_thinking,
            thinking=thinking,
            response_format=response_format,
            config=config,
        ):
            yield chunk


def chat(
    system_prompt: str,
    session: Session,
    *,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    response_format: dict | None = None,
    config=None,
) -> AIMessage:
    """同步调用 LLM,返回 AIMessage。自动选择提供商。"""
    provider = get_protocol(config, model_name=model_name)

    if provider == Protocol.ANTHROPIC:
        from uniclaw.provider import anthropic_provider

        return anthropic_provider.chat(
            system_prompt=system_prompt,
            messages=session.to_anthropic_messages(),
            model_name=model_name,
            multimodal_model_name=multimodal_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            tools=tools,
            enable_thinking=enable_thinking,
            thinking=thinking,
            config=config,
        )
    else:
        from uniclaw.provider import openai_provider

        messages = [
            {"role": "system", "content": system_prompt}
        ] + session.to_openai_messages()
        return openai_provider.chat(
            messages,
            model_name=model_name,
            multimodal_model_name=multimodal_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            tools=tools,
            enable_thinking=enable_thinking,
            thinking=thinking,
            response_format=response_format,
            config=config,
        )


async def achat(
    system_prompt: str,
    session: Session,
    *,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    response_format: dict | None = None,
    config=None,
) -> AIMessage:
    """异步调用 LLM,返回 AIMessage。自动选择提供商。"""
    provider = get_protocol(config, model_name=model_name)

    if provider == Protocol.ANTHROPIC:
        from uniclaw.provider import anthropic_provider

        return await anthropic_provider.achat(
            system_prompt=system_prompt,
            messages=session.to_anthropic_messages(),
            model_name=model_name,
            multimodal_model_name=multimodal_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            tools=tools,
            enable_thinking=enable_thinking,
            thinking=thinking,
            config=config,
        )
    else:
        from uniclaw.provider import openai_provider

        messages = [
            {"role": "system", "content": system_prompt}
        ] + session.to_openai_messages()
        return await openai_provider.achat(
            messages,
            model_name=model_name,
            multimodal_model_name=multimodal_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            tools=tools,
            enable_thinking=enable_thinking,
            thinking=thinking,
            response_format=response_format,
            config=config,
        )
