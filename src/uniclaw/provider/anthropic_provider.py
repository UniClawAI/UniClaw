"""Anthropic 提供商 — 使用 Anthropic SDK,支持流式/同步/异步调用。"""

from __future__ import annotations

import asyncio
import json

import anthropic

from uniclaw.provider.common import (
    OPENROUTER_SESSION_PREFIX_CHARS,
    REQUEST_TIMEOUT_SECONDS,
    create_async_http_client,
    create_http_client,
    is_anthropic_api,
    is_openrouter_base_url,
    make_session_id,
    record_usage_async,
    resolve_params,
    safe_parse_args,
    usage_field,
    usage_number,
)
from collections.abc import AsyncIterator, Iterator
from uniclaw.provider.types import Usage
from uniclaw.tools.session.session import AIMessage, StreamChunk

# ── 客户端构建 ─────────────────────────────────────────────────


def _build_anthropic_client(
    base_url: str, api_key: str, proxy_url: str = ""
) -> anthropic.Anthropic:
    """创建 Anthropic 客户端。"""
    http_client = create_http_client(base_url, proxy_url)
    kwargs = dict(
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=3,
    )
    if base_url and base_url != "https://api.anthropic.com":
        kwargs["base_url"] = base_url
    if http_client:
        kwargs["http_client"] = http_client
    return anthropic.Anthropic(**kwargs)


def _build_async_anthropic_client(
    base_url: str, api_key: str, proxy_url: str = ""
) -> anthropic.AsyncAnthropic:
    """创建异步 Anthropic 客户端。"""
    http_client = create_async_http_client(base_url, proxy_url)
    kwargs = dict(
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=3,
    )
    if base_url and base_url != "https://api.anthropic.com":
        kwargs["base_url"] = base_url
    if http_client:
        kwargs["http_client"] = http_client
    return anthropic.AsyncAnthropic(**kwargs)


def _openrouter_session_extra(base_url: str, system_prompt: str) -> dict | None:
    """OpenRouter 端点时构建粘性路由的 session_id extra_body,否则返回 None。

    与 OpenAI 路径同一算法: 取系统提示词前 OPENROUTER_SESSION_PREFIX_CHARS
    字符做 sha256 哈希。相同系统提示词的不同会话路由到同一上游,共享前缀缓存。
    Anthropic 兼容端点(Messages API)同样接受顶层 session_id 字段。

    Args:
        base_url: Anthropic API base_url。
        system_prompt: 系统提示词。

    Returns:
        {"session_id": <hash>} 或 None(非 OpenRouter 端点/前缀为空时)。
    """
    if not is_openrouter_base_url(base_url):
        return None
    prefix = (system_prompt or "")[:OPENROUTER_SESSION_PREFIX_CHARS]
    if not prefix:
        return None
    return {"session_id": make_session_id(prefix)}


def _usage_from_anthropic(usage, extra_discount=0.0) -> Usage:
    """从 Anthropic/OpenRouter/DeepSeek usage 对象解析 Usage,含缓存观测字段。

    OpenRouter 的 Anthropic 兼容端点以 Anthropic 原生命名返回缓存用量:
    cache_read_input_tokens(命中)与 cache_creation_input_tokens(写入);
    响应体顶层 cache_discount 为缓存折扣(读正写负)。
    DeepSeek 以顶层 prompt_cache_hit_tokens(命中)与
    prompt_cache_miss_tokens(未命中)命名,同样映射到 cached_tokens / cache_write_tokens。

    Args:
        usage: Anthropic SDK 的 usage 对象。
        extra_discount: 响应体顶层的 cache_discount。

    Returns:
        填充了缓存字段的 Usage 实例。
    """
    cached = usage_field(usage, "cache_read_input_tokens") or 0
    write = usage_field(usage, "cache_creation_input_tokens") or 0
    # DeepSeek: 顶层返回命中/未命中(OpenRouter Anthropic 端点无这两个字段)
    if not cached:
        cached = usage_field(usage, "prompt_cache_hit_tokens") or 0
    if not write:
        write = usage_field(usage, "prompt_cache_miss_tokens") or 0
    return Usage(
        input_tokens=usage_field(usage, "input_tokens") or 0,
        output_tokens=usage_field(usage, "output_tokens") or 0,
        total_tokens=usage_field(usage, "total_tokens") or 0,
        cached_tokens=usage_number(cached),
        cache_write_tokens=usage_number(write),
        cache_discount=usage_number(extra_discount, 0.0, cast=float),
    )


# Anthropic 缓存最小长度要求:block 至少 1024 tokens 才允许 cache_control
# (实际以模型为准,保守按 1024 判断,避免对过短 block 打标记导致 400)
_ANTHROPIC_CACHE_MIN_TOKENS = 1024


def _with_cache_control(
    system_prompt: str, base_url: str
) -> str | list[dict]:
    """将 system prompt 包装为带 cache_control 的 Anthropic blocks。

    仅当目标是 Anthropic 官方或 OpenRouter 的 Anthropic 兼容端点、且
    system prompt 足够长(≥1024 tokens)时,才转为 blocks 列表并打上
    ephemeral 缓存标记,让缓存 TTL 显式提升至 5 分钟。其他情况(非 Anthropic
    端点 / 提示词过短)返回原字符串,避免 API 400 或误加标记。

    Args:
        system_prompt: 系统提示词。
        base_url: Anthropic API 的 base_url。

    Returns:
        Anthropic SDK 接受的 system 参数(字符串或 blocks 列表)。
    """
    if not system_prompt:
        return system_prompt
    if not (is_anthropic_api(base_url) or is_openrouter_base_url(base_url)):
        return system_prompt
    # 粗略估算 token 数:按中英文混合 1 token ≈ 1.5~2 字符,取保守下限
    if len(system_prompt) < _ANTHROPIC_CACHE_MIN_TOKENS * 2:
        return system_prompt
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


# ── 多模态降级 ─────────────────────────────────────────────────

_ANTHROPIC_MEDIA_TYPES = {"image"}


def _extract_anthropic_media_url(block: dict) -> tuple[str, str]:
    """从 Anthropic 多模态 block 中提取 URL 和媒体类型。"""
    if block.get("type") == "image":
        source = block.get("source", {})
        if source.get("type") == "url":
            return source.get("url", ""), "image"
        if source.get("type") == "base64":
            return (
                f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}",
                "image",
            )
    return "", ""


async def _describe_multimodal(messages, mm_model: str | None = None, config=None):
    """将 Anthropic 消息中的多模态内容块替换为描述文本。"""
    cleaned = []
    for m in messages:
        content = m.get("content", "") if isinstance(m, dict) else ""
        if not isinstance(content, list):
            cleaned.append(m)
            continue
        new_blocks = []
        for b in content:
            if isinstance(b, dict) and b.get("type") in _ANTHROPIC_MEDIA_TYPES:
                if mm_model:
                    media_url, media_type = _extract_anthropic_media_url(b)
                    if media_url:
                        from uniclaw.utils.media_describer import describe_media

                        desc = await describe_media(
                            media_url, media_type, mm_model, config=config
                        )
                        new_blocks.append({"type": "text", "text": desc})
                        continue
                new_blocks.append({"type": "text", "text": f"[{b['type']}]"})
            else:
                new_blocks.append(b)
        cleaned.append({**m, "content": new_blocks})
    return cleaned


def _is_multimodal_error(e: Exception) -> bool:
    """判断是否为多模态内容不支持的错误。"""
    status = getattr(e, "status_code", None) or getattr(e, "status", None)
    if status not in (400, 404):
        return False
    msg = str(e).lower()
    return any(kw in msg for kw in ("image", "video", "multimodal", "media"))


def stream(
    system_prompt: str,
    messages,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    config=None,
) -> Iterator[StreamChunk]:
    """流式调用 Anthropic LLM,每次 yield StreamChunk (delta)。"""
    p = resolve_params(
        config,
        model_name=model_name,
        multimodal_model_name=multimodal_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    # Anthropic 使用自己的 key 和 base_url
    api_key = p.get("anthropic_api_key") or p["openai_api_key"]
    base_url = (
        p.get("anthropic_base_url") or p.get("base_url") or "https://api.anthropic.com"
    )

    client = _build_anthropic_client(base_url, api_key, p["proxy_url"])
    anthropic_messages = messages
    from uniclaw.tools.base import should_explain

    anthropic_tools = (
        [
            t.to_anthropic_schema(
                explain=should_explain(t.name, config.explain_mode, config.is_sub),
                model_name=p["model_name"],
            )
            for t in tools
        ]
        if tools
        else None
    )

    resolved_max_tokens = p["max_tokens"]
    kwargs = dict(
        model=p["model_name"],
        messages=anthropic_messages,
        max_tokens=resolved_max_tokens,
        temperature=p["temperature"],
        top_p=p["top_p"],
    )
    if system_prompt:
        kwargs["system"] = _with_cache_control(system_prompt, base_url)
    if anthropic_tools:
        kwargs["tools"] = anthropic_tools
    if enable_thinking and thinking:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": max((resolved_max_tokens or 8192) // 2, 1024),
        }

    # OpenRouter 粘性路由: 相同系统提示词 → 相同 session_id → 同一上游共享缓存
    session_extra = _openrouter_session_extra(base_url, system_prompt)
    if session_extra:
        kwargs["extra_body"] = session_extra

    try:
        yield from _stream_inner(client, kwargs)
    except Exception as e:
        if _is_multimodal_error(e) and p["multimodal_model_name"]:
            try:
                kwargs["messages"] = asyncio.run(
                    _describe_multimodal(
                        messages, p["multimodal_model_name"], config=config
                    )
                )
                yield from _stream_inner(client, kwargs)
            except RuntimeError:
                raise e
        else:
            raise


def _stream_inner(client: anthropic.Anthropic, kwargs: dict):
    """内部流式调用,处理 Anthropic SSE 事件流。"""
    tc_accum: dict[str, dict] = {}  # tool_use_id → tool_call dict
    current_block_type = None
    current_block_index = -1
    thinking_text = ""

    with client.messages.stream(**kwargs) as stream:
        for event in stream:
            sc = StreamChunk()

            if event.type == "content_block_start":
                current_block_index = event.index
                block = event.content_block
                if block.type == "text":
                    current_block_type = "text"
                elif block.type == "thinking":
                    current_block_type = "thinking"
                    thinking_text = ""
                elif block.type == "tool_use":
                    current_block_type = "tool_use"
                    tc_accum[block.id] = {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": "",
                        },
                    }
                    sc.new_tool_call_name = block.name
                    sc.new_tool_call_args = {}

            elif event.type == "content_block_delta":
                delta = event.delta
                if delta.type == "text_delta":
                    sc.content = delta.text
                elif delta.type == "thinking_delta":
                    thinking_text += delta.thinking
                    sc.reasoning_content = delta.thinking
                elif delta.type == "input_json_delta":
                    # 累积 tool arguments
                    for tc in tc_accum.values():
                        if not tc["function"]["arguments"]:
                            tc["function"]["arguments"] = delta.partial_json
                            break
                        # 找到最后一个正在累积的
                    # 更精确: 通过当前 block 关联
                    if tc_accum:
                        last_id = list(tc_accum.keys())[-1]
                        tc_accum[last_id]["function"]["arguments"] += delta.partial_json
                        sc.new_tool_call_args = safe_parse_args(
                            tc_accum[last_id]["function"]["arguments"]
                        )

            elif event.type == "content_block_stop":
                current_block_type = None

            elif event.type == "message_delta":
                # usage 信息
                usage = getattr(event, "usage", None)
                if usage:
                    sc.usage = _usage_from_anthropic(usage)

            elif event.type == "message_start":
                message = event.message
                if hasattr(message, "model") and message.model:
                    sc.model_name = (
                        message.message if hasattr(message, "message") else ""
                    )

            # 只在有内容时 yield
            if sc.content or sc.reasoning_content or sc.new_tool_call_name or sc.usage:
                yield sc

    # 流结束 — yield 累积的 tool_calls
    if tc_accum:
        final = StreamChunk()
        final.tool_calls = list(tc_accum.values())
        yield final


async def astream(
    system_prompt: str,
    messages,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    config=None,
) -> AsyncIterator[StreamChunk]:
    """异步流式调用 Anthropic LLM,每次 yield StreamChunk (delta)。"""
    p = resolve_params(
        config,
        model_name=model_name,
        multimodal_model_name=multimodal_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    api_key = p.get("anthropic_api_key") or p["openai_api_key"]
    base_url = (
        p.get("anthropic_base_url") or p.get("base_url") or "https://api.anthropic.com"
    )

    client = _build_async_anthropic_client(base_url, api_key, p["proxy_url"])
    anthropic_messages = messages
    from uniclaw.tools.base import should_explain

    anthropic_tools = (
        [
            t.to_anthropic_schema(
                explain=should_explain(t.name, config.explain_mode, config.is_sub),
                model_name=p["model_name"],
            )
            for t in tools
        ]
        if tools
        else None
    )

    resolved_max_tokens = p["max_tokens"]
    kwargs = dict(
        model=p["model_name"],
        messages=anthropic_messages,
        max_tokens=resolved_max_tokens,
        temperature=p["temperature"],
        top_p=p["top_p"],
    )
    if system_prompt:
        kwargs["system"] = _with_cache_control(system_prompt, base_url)
    if anthropic_tools:
        kwargs["tools"] = anthropic_tools
    if enable_thinking and thinking:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": max((resolved_max_tokens or 8192) // 2, 1024),
        }

    # OpenRouter 粘性路由: 相同系统提示词 → 相同 session_id → 同一上游共享缓存
    session_extra = _openrouter_session_extra(base_url, system_prompt)
    if session_extra:
        kwargs["extra_body"] = session_extra

    try:
        async for chunk in _astream_inner(client, kwargs):
            yield chunk
    except Exception as e:
        if _is_multimodal_error(e) and p["multimodal_model_name"]:
            kwargs["messages"] = await _describe_multimodal(
                messages, p["multimodal_model_name"], config=config
            )
            async for chunk in _astream_inner(client, kwargs):
                yield chunk
        else:
            raise


async def _astream_inner(client: anthropic.AsyncAnthropic, kwargs: dict):
    """内部异步流式调用,处理 Anthropic SSE 事件流。"""
    tc_accum: dict[str, dict] = {}

    async with client.messages.stream(**kwargs) as stream:
        async for event in stream:
            sc = StreamChunk()

            if event.type == "content_block_start":
                block = event.content_block
                if block.type == "tool_use":
                    tc_accum[block.id] = {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": "",
                        },
                    }
                    sc.new_tool_call_name = block.name
                    sc.new_tool_call_args = {}

            elif event.type == "content_block_delta":
                delta = event.delta
                if delta.type == "text_delta":
                    sc.content = delta.text
                elif delta.type == "thinking_delta":
                    sc.reasoning_content = delta.thinking
                elif delta.type == "input_json_delta":
                    if tc_accum:
                        last_id = list(tc_accum.keys())[-1]
                        tc_accum[last_id]["function"]["arguments"] += delta.partial_json
                        sc.new_tool_call_args = safe_parse_args(
                            tc_accum[last_id]["function"]["arguments"]
                        )

            elif event.type == "message_delta":
                usage = getattr(event, "usage", None)
                if usage:
                    sc.usage = _usage_from_anthropic(usage)

            elif event.type == "message_start":
                message = event.message
                if hasattr(message, "model") and message.model:
                    sc.model_name = message.model

            if sc.content or sc.reasoning_content or sc.new_tool_call_name or sc.usage:
                yield sc

    if tc_accum:
        final = StreamChunk()
        final.tool_calls = list(tc_accum.values())
        yield final


def chat(
    system_prompt: str,
    messages,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    config=None,
) -> AIMessage:
    """同步调用 Anthropic LLM,返回 AIMessage。"""
    p = resolve_params(
        config,
        model_name=model_name,
        multimodal_model_name=multimodal_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    api_key = p.get("anthropic_api_key") or p["openai_api_key"]
    base_url = (
        p.get("anthropic_base_url") or p.get("base_url") or "https://api.anthropic.com"
    )

    client = _build_anthropic_client(base_url, api_key, p["proxy_url"])
    anthropic_messages = messages
    anthropic_tools = [t.to_anthropic_schema(model_name=p["model_name"]) for t in tools] if tools else None

    resolved_max_tokens = p["max_tokens"]
    kwargs = dict(
        model=p["model_name"],
        messages=anthropic_messages,
        max_tokens=resolved_max_tokens,
        temperature=p["temperature"],
        top_p=p["top_p"],
    )
    if system_prompt:
        kwargs["system"] = _with_cache_control(system_prompt, base_url)
    if anthropic_tools:
        kwargs["tools"] = anthropic_tools
    if enable_thinking and thinking:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": max((resolved_max_tokens or 8192) // 2, 1024),
        }

    # OpenRouter 粘性路由: 相同系统提示词 → 相同 session_id → 同一上游共享缓存
    session_extra = _openrouter_session_extra(base_url, system_prompt)
    if session_extra:
        kwargs["extra_body"] = session_extra

    try:
        response = client.messages.create(**kwargs)
    except Exception as e:
        if _is_multimodal_error(e) and p["multimodal_model_name"]:
            try:
                kwargs["messages"] = asyncio.run(
                    _describe_multimodal(
                        messages, p["multimodal_model_name"], config=config
                    )
                )
                response = client.messages.create(**kwargs)
            except RuntimeError:
                raise e
        else:
            raise

    ai_msg = _response_to_ai_message(response)
    try:
        asyncio.get_running_loop().create_task(
            record_usage_async(ai_msg.model_name, ai_msg.usage)
        )
    except RuntimeError:
        asyncio.run(record_usage_async(ai_msg.model_name, ai_msg.usage))
    return ai_msg


async def achat(
    system_prompt: str,
    messages,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    config=None,
) -> AIMessage:
    """异步调用 Anthropic LLM,返回 AIMessage。"""
    p = resolve_params(
        config,
        model_name=model_name,
        multimodal_model_name=multimodal_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    api_key = p.get("anthropic_api_key") or p["openai_api_key"]
    base_url = (
        p.get("anthropic_base_url") or p.get("base_url") or "https://api.anthropic.com"
    )

    client = _build_async_anthropic_client(base_url, api_key, p["proxy_url"])
    anthropic_messages = messages
    anthropic_tools = [t.to_anthropic_schema(model_name=p["model_name"]) for t in tools] if tools else None

    resolved_max_tokens = p["max_tokens"]
    kwargs = dict(
        model=p["model_name"],
        messages=anthropic_messages,
        max_tokens=resolved_max_tokens,
        temperature=p["temperature"],
        top_p=p["top_p"],
    )
    if system_prompt:
        kwargs["system"] = _with_cache_control(system_prompt, base_url)
    if anthropic_tools:
        kwargs["tools"] = anthropic_tools
    if enable_thinking and thinking:
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": max((resolved_max_tokens or 8192) // 2, 1024),
        }

    # OpenRouter 粘性路由: 相同系统提示词 → 相同 session_id → 同一上游共享缓存
    session_extra = _openrouter_session_extra(base_url, system_prompt)
    if session_extra:
        kwargs["extra_body"] = session_extra

    try:
        response = await client.messages.create(**kwargs)
    except Exception as e:
        if _is_multimodal_error(e) and p["multimodal_model_name"]:
            kwargs["messages"] = await _describe_multimodal(
                messages, p["multimodal_model_name"], config=config
            )
            response = await client.messages.create(**kwargs)
        else:
            raise

    return await _response_to_ai_message_async(response)


# ── 响应转换 ───────────────────────────────────────────────────


def _response_to_ai_message(response) -> AIMessage:
    """将 Anthropic 响应转换为 AIMessage。"""
    content = ""
    reasoning = ""
    tool_calls = []

    for block in response.content:
        if block.type == "text":
            content += block.text
        elif block.type == "thinking":
            reasoning += block.thinking
        elif block.type == "tool_use":
            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input) if block.input else "{}",
                    },
                }
            )

    usage = None
    if response.usage:
        usage = _usage_from_anthropic(
            response.usage, getattr(response, "cache_discount", 0)
        )

    return AIMessage(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
        model_name=response.model or "",
        usage=usage,
    )


async def _response_to_ai_message_async(response) -> AIMessage:
    """异步版本:转换响应并记录用量。"""
    ai_msg = _response_to_ai_message(response)
    await record_usage_async(ai_msg.model_name, ai_msg.usage)
    return ai_msg
