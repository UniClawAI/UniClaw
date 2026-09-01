"""OpenAI 提供商 — 使用 OpenAI SDK,支持流式/同步/异步调用。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from openai import AsyncOpenAI, OpenAI

from uniclaw.provider.common import (
    OPENROUTER_SESSION_PREFIX_CHARS,
    REQUEST_TIMEOUT_SECONDS,
    build_extra_body,
    create_async_http_client,
    create_http_client,
    is_multimodal_error,
    is_openrouter_api,
    record_usage_async,
    resolve_params,
    safe_parse_args,
    usage_field,
    usage_number,
)
from collections.abc import AsyncIterator, Iterator
from uniclaw.provider.thought_parser import ThoughtParser
from uniclaw.provider.types import Usage
from uniclaw.tools.session.session import AIMessage, StreamChunk

if TYPE_CHECKING:
    from uniclaw.config import AppConfig


def _sanitize_surrogates(obj):
    """递归清理对象中的孤立代理码点(surrogates),避免 JSON 序列化失败。"""
    if isinstance(obj, str):
        return obj.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
    if isinstance(obj, list):
        return [_sanitize_surrogates(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_surrogates(v) for k, v in obj.items()}
    return obj


def _session_prefix(messages) -> str:
    """提取首条系统消息的文本前缀,用于 OpenRouter 粘性路由 session_id 哈希。

    router 层约定 messages[0] 为 {"role": "system", "content": system_prompt},
    这里兼容 dict 与消息对象两种形态,多模态 content 列表只拼纯文本块。

    Args:
        messages: OpenAI 格式消息列表。

    Returns:
        系统提示词前 OPENROUTER_SESSION_PREFIX_CHARS 个字符;取不到则返回空串。
    """
    if not messages:
        return ""
    first = messages[0]
    content = (
        first.get("content", "") if isinstance(first, dict) else getattr(first, "content", "")
    )
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    if not isinstance(content, str):
        return ""
    return content[:OPENROUTER_SESSION_PREFIX_CHARS]


def _usage_from_prompt_details(usage, extra_discount=0.0) -> Usage:
    """从 OpenAI/OpenRouter/DeepSeek usage 对象解析 Usage,含缓存观测字段。

    OpenRouter 在 usage.prompt_tokens_details 返回 cached_tokens(命中)与
    cache_write_tokens(写入),响应体顶层返回 cache_discount(读正写负)。
    DeepSeek 在 usage 顶层返回 prompt_cache_hit_tokens(命中)与
    prompt_cache_miss_tokens(未命中),分别映射到 cached_tokens / cache_write_tokens。

    Args:
        usage: OpenAI SDK 的 usage 对象(含 prompt_tokens_details 或顶层缓存字段)。
        extra_discount: 响应体顶层的 cache_discount,流式/非流式均可为 0。

    Returns:
        填充了缓存字段的 Usage 实例。
    """
    details = usage_field(usage, "prompt_tokens_details", None)
    if isinstance(details, dict):
        cached = details.get("cached_tokens", 0)
        write = details.get("cache_write_tokens", 0)
    else:
        cached = getattr(details, "cached_tokens", 0) if details else 0
        write = getattr(details, "cache_write_tokens", 0) if details else 0
    # DeepSeek: usage 顶层返回命中/未命中,OpenRouter 则无这两个字段
    if not cached:
        cached = usage_field(usage, "prompt_cache_hit_tokens")
    if not write:
        write = usage_field(usage, "prompt_cache_miss_tokens")
    return Usage(
        input_tokens=usage.prompt_tokens or 0,
        output_tokens=usage.completion_tokens or 0,
        total_tokens=usage.total_tokens or 0,
        cached_tokens=usage_number(cached),
        cache_write_tokens=usage_number(write),
        cache_discount=usage_number(extra_discount, 0.0, cast=float),
    )


# ── 多模态降级 ─────────────────────────────────────────────────

_MULTIMODAL_TYPES = {"image_url", "input_audio", "video_url"}


def _extract_media_url(block: dict) -> tuple[str, str]:
    """从多模态 content block 中提取 URL 和媒体类型。"""
    btype = block.get("type")
    if btype == "image_url":
        return block["image_url"]["url"], "image"
    if btype == "input_audio":
        return block["input_audio"]["data"], "audio"
    if btype == "video_url":
        return block["video_url"]["url"], "video"
    return "", ""


async def _describe_multimodal(messages, mm_model: str | None = None, config: AppConfig | None = None):
    """将消息中的多模态内容块替换为描述文本。"""
    cleaned = []
    for m in messages:
        content = (
            m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        )
        if not isinstance(content, list):
            cleaned.append(m)
            continue
        new_blocks = []
        for b in content:
            if isinstance(b, dict) and b.get("type") in _MULTIMODAL_TYPES:
                if mm_model:
                    media_url, media_type = _extract_media_url(b)
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
        if isinstance(m, dict):
            cleaned.append({**m, "content": new_blocks})
        else:
            m.content = new_blocks
            cleaned.append(m)
    return cleaned


# ── 客户端构建 ─────────────────────────────────────────────────


def _build_openai_client(
    openai_api_base: str, openai_api_key: str, proxy_url: str = ""
) -> OpenAI:
    """创建 OpenAI 客户端。"""
    http_client = create_http_client(openai_api_base, proxy_url)
    return OpenAI(
        api_key=openai_api_key,
        base_url=openai_api_base,
        http_client=http_client,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=2,
    )


def _build_async_openai_client(
    openai_api_base: str, openai_api_key: str, proxy_url: str = ""
) -> AsyncOpenAI:
    """创建异步 OpenAI 客户端。"""
    http_client = create_async_http_client(openai_api_base, proxy_url)
    return AsyncOpenAI(
        api_key=openai_api_key,
        base_url=openai_api_base,
        http_client=http_client,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=2,
    )


# ── 核心调用函数 ───────────────────────────────────────────────


def stream(
    messages,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    audio: dict | None = None,
    asr_options: dict | None = None,
    response_format: dict | None = None,
    config: AppConfig | None = None,
) -> Iterator[StreamChunk]:
    """流式调用 LLM,每次 yield StreamChunk (delta)。"""
    p = resolve_params(
        config,
        model_name=model_name,
        multimodal_model_name=multimodal_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    client = _build_openai_client(
        p["openai_api_base"], p["openai_api_key"], p["proxy_url"]
    )
    extra_body = build_extra_body(
        p["openai_api_base"], enable_thinking, thinking, _session_prefix(messages)
    )
    if asr_options:
        extra_body["asr_options"] = asr_options
    from uniclaw.tools.base import should_explain

    openai_tools = (
        [
            t.to_openai_schema(
                explain=should_explain(t.name, config.explain_mode, config.is_sub),
                model_name=p["model_name"],
            )
            for t in tools
        ]
        if tools
        else None
    )

    # 清理消息中的孤立代理码点,避免 OpenAI SDK JSON 序列化失败
    messages = _sanitize_surrogates(messages)

    kwargs = dict(
        model=p["model_name"],
        messages=messages,
        temperature=p["temperature"],
        max_tokens=p["max_tokens"],
        top_p=p["top_p"],
        stream=True,
    )
    # OpenRouter 流式默认不返回 usage,显式请求以观测缓存命中(cached_tokens)
    if is_openrouter_api(p["openai_api_base"]):
        kwargs["stream_options"] = {"include_usage": True}
    if openai_tools:
        kwargs["tools"] = openai_tools
    if extra_body:
        kwargs["extra_body"] = extra_body
    if audio:
        audio.setdefault("format", "pcm16")
        kwargs["modalities"] = ["text", "audio"]
        kwargs["audio"] = audio
    if response_format:
        kwargs["response_format"] = response_format

    try:
        yield from _stream_inner(client, kwargs)
    except Exception as e:
        if is_multimodal_error(e) and p["multimodal_model_name"]:
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


def _stream_inner(client: OpenAI, kwargs: dict):
    """内部流式调用,处理 delta 累积。"""
    parser = ThoughtParser()
    # tool_calls 按 index 累积
    tc_accum: dict[int, dict] = {}

    response = client.chat.completions.create(**kwargs)
    for chunk in response:
        sc = StreamChunk()

        # content + reasoning + tool_calls
        if chunk.choices:
            delta = chunk.choices[0].delta

            raw_content = delta.content or ""
            if raw_content:
                reasoning, content = parser.process(raw_content)
                sc.content = content
                sc.reasoning_content = reasoning

            # reasoning_content (部分 API 直接返回)
            rc = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
            )
            if rc:
                sc.reasoning_content += rc

            # audio
            if hasattr(delta, "audio") and delta.audio and delta.audio["data"]:
                sc.audio = delta.audio["data"]

            # tool_calls — 累积,首次获得 name 时标记通知
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    is_new = idx not in tc_accum
                    if is_new:
                        tc_accum[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    tc = tc_accum[idx]
                    if tc_delta.id:
                        tc["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc["function"]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc["function"]["arguments"] += tc_delta.function.arguments
                    if tc["function"]["name"] and (
                        is_new or (tc_delta.function and tc_delta.function.arguments)
                    ):
                        sc.new_tool_call_name = tc["function"]["name"]
                        sc.new_tool_call_args = safe_parse_args(
                            tc["function"]["arguments"]
                        )

        # usage
        if chunk.usage:
            sc.usage = _usage_from_prompt_details(
                chunk.usage, getattr(chunk, "cache_discount", 0)
            )

        if hasattr(chunk, "model") and chunk.model:
            sc.model_name = chunk.model

        yield sc

    # 流结束 — yield 累积的 tool_calls
    if tc_accum:
        final = StreamChunk()
        final.tool_calls = [tc_accum[i] for i in sorted(tc_accum)]
        yield final


async def astream(
    messages,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    audio: dict | None = None,
    asr_options: dict | None = None,
    response_format: dict | None = None,
    config: AppConfig | None = None,
) -> AsyncIterator[StreamChunk]:
    """异步流式调用 LLM,每次 yield StreamChunk (delta)。"""
    p = resolve_params(
        config,
        model_name=model_name,
        multimodal_model_name=multimodal_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    client = _build_async_openai_client(
        p["openai_api_base"], p["openai_api_key"], p["proxy_url"]
    )
    extra_body = build_extra_body(
        p["openai_api_base"], enable_thinking, thinking, _session_prefix(messages)
    )
    if asr_options:
        extra_body["asr_options"] = asr_options
    from uniclaw.tools.base import should_explain

    openai_tools = (
        [
            t.to_openai_schema(
                explain=should_explain(t.name, config.explain_mode, config.is_sub),
                model_name=p["model_name"],
            )
            for t in tools
        ]
        if tools
        else None
    )

    # 清理消息中的孤立代理码点,避免 OpenAI SDK JSON 序列化失败
    messages = _sanitize_surrogates(messages)

    kwargs = dict(
        model=p["model_name"],
        messages=messages,
        temperature=p["temperature"],
        max_tokens=p["max_tokens"],
        top_p=p["top_p"],
        stream=True,
    )
    # OpenRouter 流式默认不返回 usage,显式请求以观测缓存命中(cached_tokens)
    if is_openrouter_api(p["openai_api_base"]):
        kwargs["stream_options"] = {"include_usage": True}
    if openai_tools:
        kwargs["tools"] = openai_tools
    if extra_body:
        kwargs["extra_body"] = extra_body
    if audio:
        audio.setdefault("format", "pcm16")
        kwargs["modalities"] = ["text", "audio"]
        kwargs["audio"] = audio
    if response_format:
        kwargs["response_format"] = response_format

    try:
        async for chunk in _astream_inner(client, kwargs):
            yield chunk
    except Exception as e:
        if is_multimodal_error(e) and p["multimodal_model_name"]:
            kwargs["messages"] = await _describe_multimodal(
                messages, p["multimodal_model_name"], config=config
            )
            async for chunk in _astream_inner(client, kwargs):
                yield chunk
        else:
            raise


async def _astream_inner(client: AsyncOpenAI, kwargs: dict):
    """内部异步流式调用,处理 delta 累积。"""
    parser = ThoughtParser()
    tc_accum: dict[int, dict] = {}

    response = await client.chat.completions.create(**kwargs)
    async for chunk in response:
        sc = StreamChunk()
        if chunk is None:
            continue

        if chunk.choices:
            delta = chunk.choices[0].delta

            raw_content = delta.content or ""
            if raw_content:
                reasoning, content = parser.process(raw_content)
                sc.content = content
                sc.reasoning_content = reasoning

            rc = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
            )
            if rc:
                sc.reasoning_content += rc

            # audio
            if hasattr(delta, "audio") and delta.audio and delta.audio["data"]:
                sc.audio = delta.audio["data"]

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    is_new = idx not in tc_accum
                    if is_new:
                        tc_accum[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    tc = tc_accum[idx]
                    if tc_delta.id:
                        tc["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc["function"]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc["function"]["arguments"] += tc_delta.function.arguments
                    if tc["function"]["name"] and (
                        is_new or (tc_delta.function and tc_delta.function.arguments)
                    ):
                        sc.new_tool_call_name = tc["function"]["name"]
                        sc.new_tool_call_args = safe_parse_args(
                            tc["function"]["arguments"]
                        )

        if chunk.usage:
            sc.usage = _usage_from_prompt_details(
                chunk.usage, getattr(chunk, "cache_discount", 0)
            )

        if hasattr(chunk, "model") and chunk.model:
            sc.model_name = chunk.model

        yield sc

    if tc_accum:
        final = StreamChunk()
        final.tool_calls = [tc_accum[i] for i in sorted(tc_accum)]
        yield final


def chat(
    messages,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    audio: dict | None = None,
    asr_options: dict | None = None,
    response_format: dict | None = None,
    timeout: float | None = None,
    config: AppConfig | None = None,
) -> AIMessage:
    """同步调用 LLM,返回 AIMessage。

    Args:
        messages: OpenAI 格式消息列表。
        model_name: 模型名称(含提供商前缀),为空时使用配置中的模型。
        multimodal_model_name: 多模态降级模型名称。
        temperature: 采样温度。默认为 None(使用配置值)。
        max_tokens: 最大生成 token 数。默认为 None(使用配置值)。
        top_p: 核采样参数。默认为 None(使用配置值)。
        tools: 工具列表。
        enable_thinking: 是否启用思考(受 thinking 参数约束)。
        thinking: 是否启用思考模式。
        audio: 音频生成参数。
        asr_options: ASR 选项。
        response_format: 响应格式。
        timeout: 单次请求超时秒数,覆盖客户端默认值(REQUEST_TIMEOUT_SECONDS)。
            为 None 时使用客户端默认超时。
        config: 应用配置。

    Returns:
        AIMessage: 转换后的 AI 回复消息。
    """
    p = resolve_params(
        config,
        model_name=model_name,
        multimodal_model_name=multimodal_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    client = _build_openai_client(
        p["openai_api_base"], p["openai_api_key"], p["proxy_url"]
    )
    extra_body = build_extra_body(
        p["openai_api_base"], enable_thinking, thinking, _session_prefix(messages)
    )
    if asr_options:
        extra_body["asr_options"] = asr_options
    openai_tools = [t.to_openai_schema(model_name=p["model_name"]) for t in tools] if tools else None

    # 清理消息中的孤立代理码点,避免 OpenAI SDK JSON 序列化失败
    messages = _sanitize_surrogates(messages)

    kwargs = dict(
        model=p["model_name"],
        messages=messages,
        temperature=p["temperature"],
        max_tokens=p["max_tokens"],
        top_p=p["top_p"],
    )
    if openai_tools:
        kwargs["tools"] = openai_tools
    if extra_body:
        kwargs["extra_body"] = extra_body
    if audio:
        audio.setdefault("format", "wav")
        kwargs["modalities"] = ["text", "audio"]
        kwargs["audio"] = audio
    if response_format:
        kwargs["response_format"] = response_format
    if timeout is not None:
        kwargs["timeout"] = timeout

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as e:
        if is_multimodal_error(e) and p["multimodal_model_name"]:
            try:
                kwargs["messages"] = asyncio.run(
                    _describe_multimodal(
                        messages, p["multimodal_model_name"], config=config
                    )
                )
                response = client.chat.completions.create(**kwargs)
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
    messages,
    model_name: str = "",
    multimodal_model_name: str | None = None,
    temperature=None,
    max_tokens=None,
    top_p=None,
    tools: list | None = None,
    enable_thinking=True,
    thinking=True,
    audio: dict | None = None,
    asr_options: dict | None = None,
    response_format: dict | None = None,
    timeout: float | None = None,
    config: AppConfig | None = None,
) -> AIMessage:
    """异步调用 LLM,返回 AIMessage。

    Args:
        messages: OpenAI 格式消息列表。
        model_name: 模型名称(含提供商前缀),为空时使用配置中的模型。
        multimodal_model_name: 多模态降级模型名称。
        temperature: 采样温度。默认为 None(使用配置值)。
        max_tokens: 最大生成 token 数。默认为 None(使用配置值)。
        top_p: 核采样参数。默认为 None(使用配置值)。
        tools: 工具列表。
        enable_thinking: 是否启用思考(受 thinking 参数约束)。
        thinking: 是否启用思考模式。
        audio: 音频生成参数。
        asr_options: ASR 选项。
        response_format: 响应格式。
        timeout: 单次请求超时秒数,覆盖客户端默认值(REQUEST_TIMEOUT_SECONDS)。
            为 None 时使用客户端默认超时。
        config: 应用配置。

    Returns:
        AIMessage: 转换后的 AI 回复消息。
    """
    p = resolve_params(
        config,
        model_name=model_name,
        multimodal_model_name=multimodal_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    client = _build_async_openai_client(
        p["openai_api_base"], p["openai_api_key"], p["proxy_url"]
    )
    extra_body = build_extra_body(
        p["openai_api_base"], enable_thinking, thinking, _session_prefix(messages)
    )
    if asr_options:
        extra_body["asr_options"] = asr_options
    openai_tools = [t.to_openai_schema(model_name=p["model_name"]) for t in tools] if tools else None

    # 清理消息中的孤立代理码点,避免 OpenAI SDK JSON 序列化失败
    messages = _sanitize_surrogates(messages)

    kwargs = dict(
        model=p["model_name"],
        messages=messages,
        temperature=p["temperature"],
        max_tokens=p["max_tokens"],
        top_p=p["top_p"],
    )
    if openai_tools:
        kwargs["tools"] = openai_tools
    if extra_body:
        kwargs["extra_body"] = extra_body
    if audio:
        audio.setdefault("format", "wav")
        kwargs["modalities"] = ["text", "audio"]
        kwargs["audio"] = audio
    if response_format:
        kwargs["response_format"] = response_format
    if timeout is not None:
        kwargs["timeout"] = timeout

    try:
        response = await client.chat.completions.create(**kwargs)
    except Exception as e:
        if is_multimodal_error(e) and p["multimodal_model_name"]:
            kwargs["messages"] = await _describe_multimodal(
                messages, p["multimodal_model_name"], config=config
            )
            response = await client.chat.completions.create(**kwargs)
        else:
            raise

    return await _response_to_ai_message_async(response)


# ── 响应转换 ───────────────────────────────────────────────────


def _response_to_ai_message(response) -> AIMessage:
    """将 OpenAI 响应转换为 AIMessage。"""
    choice = response.choices[0]
    msg = choice.message

    # tool_calls — 直接保留 OpenAI 格式
    tool_calls = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            tool_calls.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "",
                    },
                }
            )

    # reasoning_content
    reasoning = (
        getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None) or ""
    )

    # usage
    usage = None
    if response.usage:
        usage = _usage_from_prompt_details(
            response.usage, getattr(response, "cache_discount", 0)
        )

    # post-process: parse <thought> tags from content
    content = msg.content or ""
    if content:
        parser = ThoughtParser()
        thinking_text, content = parser.process(content)
        reasoning = thinking_text + reasoning

    ai_msg = AIMessage(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
        model_name=response.model or "",
        usage=usage,
        audio=msg.audio.data if msg.audio else None,
    )
    return ai_msg


async def _response_to_ai_message_async(response) -> AIMessage:
    """异步版本:转换响应并记录用量。"""
    ai_msg = _response_to_ai_message(response)
    await record_usage_async(ai_msg.model_name, ai_msg.usage)
    return ai_msg


# ── 图片生成 ───────────────────────────────────────────────────


def generate_image(
    prompt: str,
    model_name: str,
    size: str = "1024x768",
    config: AppConfig | None = None,
) -> list[str]:
    """同步图片生成,返回图片 URL 或 base64 列表。

    Args:
        prompt: 图片描述提示词
        model_name: 模型名称(含提供商前缀),为空时使用配置中的模型
        size: 图片尺寸,如 "2K", "1024x1024"
        config: 应用配置

    Returns:
        图片 URL 或 base64 编码字符串列表
    """
    p = resolve_params(config, model_name=model_name)
    client = _build_openai_client(
        p["openai_api_base"], p["openai_api_key"], p["proxy_url"]
    )

    kwargs = dict(
        model=p["model_name"],
        prompt=prompt,
        size=size,
    )

    response = client.images.generate(**kwargs)
    return [item.url or item.b64_json for item in response.data]


async def agenerate_image(
    prompt: str,
    model_name: str,
    size: str = "1024x768",
    config: AppConfig | None = None,
) -> list[str]:
    """异步图片生成,返回图片 URL 或 base64 列表。

    Args:
        prompt: 图片描述提示词
        model_name: 模型名称(含提供商前缀),为空时使用配置中的模型
        size: 图片尺寸,如 "2K", "1024x1024"
        config: 应用配置

    Returns:
        图片 URL 或 base64 编码字符串列表
    """
    p = resolve_params(config, model_name=model_name)
    client = _build_async_openai_client(
        p["openai_api_base"], p["openai_api_key"], p["proxy_url"]
    )

    kwargs = dict(
        model=p["model_name"],
        prompt=prompt,
        size=size,
    )

    response = await client.images.generate(**kwargs)
    return [item.url or item.b64_json for item in response.data]
