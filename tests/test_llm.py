"""
LLM 调用层的单元测试
"""

import hashlib
from types import SimpleNamespace

import pytest
from unittest.mock import patch, MagicMock
from uniclaw.provider import (
    stream,
    chat,
    achat,
    Usage,
    compare_urls,
    ThoughtParser,
    Effort,
)
from uniclaw.tools.session.session import AIMessage, StreamChunk
from pathlib import Path
from uniclaw.tools.session.session import Session
from uniclaw.provider.common import (
    build_extra_body,
    create_http_client,
    create_async_http_client,
    resolve_params,
    is_multimodal_error,
    safe_parse_args,
)
from uniclaw.provider.openai_provider import (
    _extract_media_url,
    _session_prefix,
    _usage_from_prompt_details,
)
from uniclaw.provider.anthropic_provider import (
    _openrouter_session_extra,
    _usage_from_anthropic,
    _with_cache_control,
)
from uniclaw.config import ProviderProfile


def _make_config(
    api_key="test-key", base_url="https://api.openai.com/v1/", protocol="openai"
):
    """创建带 provider 的测试配置。"""
    config = MagicMock()
    config.providers = {
        "test": ProviderProfile(
            name="test", protocol=protocol, api_key=api_key, base_url=base_url
        )
    }
    config.model_name = ["test/gpt-4"]
    config.mini_model_name = []
    config.multimodal_model_name = []
    config.proxy_url = ""
    config.temperature = 0.7
    config.max_tokens = None
    config.top_p = None
    return config


# ── 辅助函数测试 ──────────────────────────────────────────────


class TestBuildExtraBody:
    """extra_body 构建测试"""

    def test_google_api_returns_none(self):
        result = build_extra_body(
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            enable_thinking=True,
            thinking=True,
        )
        assert result is None

    def test_thinking_enabled(self):
        result = build_extra_body(
            "https://api.openai.com/v1/",
            enable_thinking=True,
            thinking=True,
        )
        assert result == {
            "enable_thinking": True,
            "thinking": {"type": "enabled"},
        }

    def test_thinking_disabled(self):
        result = build_extra_body(
            "https://api.openai.com/v1/",
            enable_thinking=True,
            thinking=False,
        )
        assert result == {
            "enable_thinking": True,
            "thinking": {"type": "disabled"},
        }

    def test_openrouter_thinking_disabled_adds_reasoning(self):
        result = build_extra_body(
            "https://openrouter.ai/api/v1/",
            enable_thinking=False,
            thinking=False,
        )
        assert result == {
            "enable_thinking": False,
            "thinking": {"type": "disabled"},
            "reasoning": {"effort": "none"},
        }

    def test_openrouter_adds_session_id_hash(self):
        prefix = "你是 UniClaw,一个 AI 助手"
        result = build_extra_body(
            "https://openrouter.ai/api/v1/",
            enable_thinking=True,
            thinking=True,
            session_prefix=prefix,
        )
        assert result["session_id"] == hashlib.sha256(prefix.encode("utf-8")).hexdigest()[
            :32
        ]
        assert len(result["session_id"]) == 32

    def test_same_prefix_same_session_id(self):
        base = "https://openrouter.ai/api/v1/"
        id1 = build_extra_body(base, True, True, "system-prefix-abc")["session_id"]
        id2 = build_extra_body(base, True, True, "system-prefix-abc")["session_id"]
        assert id1 == id2

    def test_different_prefix_different_session_id(self):
        base = "https://openrouter.ai/api/v1/"
        id1 = build_extra_body(base, True, True, "system-prefix-abc")["session_id"]
        id2 = build_extra_body(base, True, True, "system-prefix-abd")["session_id"]
        assert id1 != id2

    def test_session_id_ignored_for_non_openrouter(self):
        result = build_extra_body(
            "https://api.openai.com/v1/",
            enable_thinking=True,
            thinking=True,
            session_prefix="some-prefix",
        )
        assert "session_id" not in result

    def test_empty_prefix_no_session_id(self):
        result = build_extra_body(
            "https://openrouter.ai/api/v1/",
            enable_thinking=True,
            thinking=True,
            session_prefix="",
        )
        assert "session_id" not in result

    def test_session_prefix_extracts_first_500_chars(self):
        messages = [{"role": "system", "content": "S" * 600}]
        assert _session_prefix(messages) == "S" * 500

    def test_session_prefix_handles_missing_system_message(self):
        assert _session_prefix([]) == ""
        assert _session_prefix([{"role": "user", "content": "hi"}]) == "hi"


class TestAnthropicOpenRouterSession:
    """Anthropic 路径的 OpenRouter 粘性路由 session_id 测试"""

    def test_openrouter_base_adds_session_id(self):
        prompt = "你是 UniClaw,一个 AI 助手"
        extra = _openrouter_session_extra(
            "https://openrouter.ai/api/v1/anthropic", prompt
        )
        assert extra == {
            "session_id": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:32]
        }

    def test_openrouter_api_base_adds_session_id(self):
        # Claude Code 风格的 base: https://openrouter.ai/api (无 /v1/anthropic 后缀)
        extra = _openrouter_session_extra("https://openrouter.ai/api", "prompt")
        assert extra is not None and len(extra["session_id"]) == 32

    def test_anthropic_official_no_session_id(self):
        assert _openrouter_session_extra("https://api.anthropic.com", "prompt") is None

    def test_empty_prompt_no_session_id(self):
        assert (
            _openrouter_session_extra("https://openrouter.ai/api/v1/anthropic", "")
            is None
        )

    def test_same_prefix_same_session_id(self):
        a = _openrouter_session_extra(
            "https://openrouter.ai/api/v1/anthropic", "S" * 600
        )
        b = _openrouter_session_extra(
            "https://openrouter.ai/api/v1/anthropic", "S" * 700
        )
        # 前 500 字符相同,提示词后段差异不影响 id
        assert a == b


class TestUsageCacheFields:
    """Usage 缓存字段的序列化与解析测试"""

    def test_to_dict_roundtrip(self):
        u = Usage(
            input_tokens=100,
            output_tokens=50,
            cached_tokens=90,
            cache_write_tokens=10,
            cache_discount=0.5,
        )
        restored = Usage.from_dict(u.to_dict())
        assert restored == u

    def test_from_dict_missing_cache_fields(self):
        u = Usage.from_dict({"input_tokens": 1, "output_tokens": 2})
        assert u.cached_tokens == 0
        assert u.cache_write_tokens == 0
        assert u.cache_discount == 0.0

    def test_usage_from_prompt_details(self):
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            prompt_tokens_details=SimpleNamespace(
                cached_tokens=95, cache_write_tokens=0
            ),
        )
        u = _usage_from_prompt_details(usage, extra_discount=0.25)
        assert u.input_tokens == 100
        assert u.cached_tokens == 95
        assert u.cache_write_tokens == 0
        assert u.cache_discount == 0.25
        assert u.total_tokens == 120

    def test_usage_from_prompt_details_no_details(self):
        usage = SimpleNamespace(
            prompt_tokens=1, completion_tokens=1, total_tokens=2
        )
        u = _usage_from_prompt_details(usage)
        assert u.cached_tokens == 0
        assert u.cache_discount == 0.0

    def test_usage_from_prompt_details_mock_safe(self):
        # MagicMock 对未设置的属性会自动创建占位对象,不应污染 Usage 的数值字段
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 20
        # total_tokens 未设置 → MagicMock,模拟 SDK 不返回 total 的场景
        u = _usage_from_prompt_details(usage, extra_discount=MagicMock())
        assert u.input_tokens == 100
        assert u.output_tokens == 20
        assert u.cached_tokens == 0
        assert u.cache_write_tokens == 0
        assert u.cache_discount == 0.0

    def test_usage_from_prompt_details_deepseek_attr(self):
        # DeepSeek: 顶层 prompt_cache_hit_tokens / prompt_cache_miss_tokens
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            prompt_cache_hit_tokens=85,
            prompt_cache_miss_tokens=15,
        )
        u = _usage_from_prompt_details(usage)
        assert u.cached_tokens == 85
        assert u.cache_write_tokens == 15

    def test_usage_from_prompt_details_deepseek_model_extra(self):
        # OpenAI SDK 把未知字段放入 model_extra,getattr 取不到
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            model_extra={
                "prompt_cache_hit_tokens": 90,
                "prompt_cache_miss_tokens": 10,
            },
        )
        u = _usage_from_prompt_details(usage)
        assert u.cached_tokens == 90
        assert u.cache_write_tokens == 10

    def test_usage_from_anthropic(self):
        usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            cache_read_input_tokens=90,
            cache_creation_input_tokens=10,
        )
        u = _usage_from_anthropic(usage, extra_discount=0.3)
        assert u.cached_tokens == 90
        assert u.cache_write_tokens == 10
        assert u.cache_discount == 0.3
        assert u.total_tokens == 120

    def test_usage_from_anthropic_no_cache(self):
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2)
        u = _usage_from_anthropic(usage)
        assert u.cached_tokens == 0
        assert u.cache_write_tokens == 0

    def test_usage_from_anthropic_mock_safe(self):
        # MagicMock 未设置的缓存属性自动创建占位对象,应收敛为 0
        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 20
        u = _usage_from_anthropic(usage, extra_discount=MagicMock())
        assert u.input_tokens == 100
        assert u.output_tokens == 20
        assert u.cached_tokens == 0
        assert u.cache_write_tokens == 0
        assert u.cache_discount == 0.0

    def test_usage_from_anthropic_deepseek_attr(self):
        # DeepSeek 经 Anthropic 兼容端点:顶层 prompt_cache_hit_tokens/miss
        usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            prompt_cache_hit_tokens=85,
            prompt_cache_miss_tokens=15,
        )
        u = _usage_from_anthropic(usage)
        assert u.cached_tokens == 85
        assert u.cache_write_tokens == 15

    def test_usage_from_anthropic_deepseek_model_extra(self):
        # OpenAI/Anthropic SDK 把未知字段放入 model_extra,getattr 取不到
        usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            model_extra={
                "prompt_cache_hit_tokens": 90,
                "prompt_cache_miss_tokens": 10,
            },
        )
        u = _usage_from_anthropic(usage)
        assert u.cached_tokens == 90
        assert u.cache_write_tokens == 10


class TestAnthropicCacheControl:
    """system prompt 的 cache_control 包装测试"""

    def test_short_prompt_untouched(self):
        # 过短提示词不加 cache_control,保持字符串
        r = _with_cache_control("short prompt", "https://api.anthropic.com")
        assert r == "short prompt"

    def test_non_anthropic_url_untouched(self):
        # 非 Anthropic 官方/OpenRouter 端点不加 cache_control
        r = _with_cache_control("S" * 5000, "https://example.com/api")
        assert r == "S" * 5000

    def test_anthropic_long_prompt_gets_cache_control(self):
        r = _with_cache_control("S" * 5000, "https://api.anthropic.com")
        assert isinstance(r, list)
        assert r[0]["type"] == "text"
        assert r[0]["cache_control"] == {"type": "ephemeral"}
        assert len(r[0]["text"]) == 5000

    def test_openrouter_anthropic_endpoint_gets_cache_control(self):
        r = _with_cache_control(
            "S" * 5000, "https://openrouter.ai/api/v1/anthropic"
        )
        assert isinstance(r, list)
        assert r[0]["cache_control"] == {"type": "ephemeral"}


# ── 多轮对话带工具测试 ────────────────────────────────────────


class TestStreamWithTools:
    """流式调用带工具的测试"""

    def _make_mock_stream(self, chunks):
        """创建模拟的流式响应"""
        for chunk in chunks:
            yield chunk

    def test_multi_turn_with_tool_calls(self):
        """测试多轮对话：用户提问 -> AI 调用工具 -> 工具返回结果 -> AI 回答"""
        # 第一轮：AI 返回 tool_call
        tool_call_chunk = MagicMock()
        tool_call_chunk.choices = [MagicMock()]
        tool_call_chunk.choices[0].delta.content = ""
        tool_call_chunk.choices[0].delta.tool_calls = [MagicMock()]
        tool_call_chunk.choices[0].delta.tool_calls[0].index = 0
        tool_call_chunk.choices[0].delta.tool_calls[0].id = "call_123"
        tool_call_chunk.choices[0].delta.tool_calls[0].function = MagicMock()
        tool_call_chunk.choices[0].delta.tool_calls[0].function.name = "get_weather"
        tool_call_chunk.choices[0].delta.tool_calls[
            0
        ].function.arguments = '{"city": "Beijing"}'

        usage_chunk = MagicMock()
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock()
        usage_chunk.usage.prompt_tokens = 100
        usage_chunk.usage.completion_tokens = 20
        usage_chunk.model = "gpt-4"

        with patch("uniclaw.provider.openai_provider.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = self._make_mock_stream(
                [
                    tool_call_chunk,
                    usage_chunk,
                ]
            )

            _s = Session(root_dir=Path.cwd())
            _s.add_user_message(content="北京天气怎么样？")
            tools = [
                MagicMock(name="get_weather", description="获取天气", parameters={})
            ]
            config = _make_config()

            chunks = list(
                stream(
                    "",
                    _s,
                    tools=tools,
                    model_name="test/gpt-4",
                    config=config,
                )
            )

            # 验证返回了 tool_calls
            assert len(chunks) > 0
            tool_chunk = chunks[-1]
            assert len(tool_chunk.tool_calls) == 1
            assert tool_chunk.tool_calls[0]["function"]["name"] == "get_weather"
            assert tool_chunk.tool_calls[0]["id"] == "call_123"

    def test_extra_body_passed_to_api(self):
        """测试 extra_body 正确传递到 API"""
        content_chunk = MagicMock()
        content_chunk.choices = [MagicMock()]
        content_chunk.choices[0].delta.content = "你好"
        content_chunk.choices[0].delta.tool_calls = None

        usage_chunk = MagicMock()
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock()
        usage_chunk.usage.prompt_tokens = 50
        usage_chunk.usage.completion_tokens = 10
        usage_chunk.model = "gpt-4"

        with patch("uniclaw.provider.openai_provider.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = self._make_mock_stream(
                [
                    content_chunk,
                    usage_chunk,
                ]
            )

            _s = Session(root_dir=Path.cwd())
            _s.add_user_message(content="你好")
            config = _make_config()

            list(
                stream(
                    "",
                    _s,
                    model_name="test/gpt-4",
                    config=config,
                    enable_thinking=True,
                    thinking=True,
                )
            )

            # 验证 kwargs 中包含 extra_body
            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert "extra_body" in call_kwargs
            assert call_kwargs["extra_body"]["enable_thinking"] is True
            assert call_kwargs["extra_body"]["thinking"]["type"] == "enabled"

    def test_google_api_no_extra_body(self):
        """测试 Google API 不传递 extra_body"""
        content_chunk = MagicMock()
        content_chunk.choices = [MagicMock()]
        content_chunk.choices[0].delta.content = "你好"
        content_chunk.choices[0].delta.tool_calls = None

        usage_chunk = MagicMock()
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock()
        usage_chunk.usage.prompt_tokens = 50
        usage_chunk.usage.completion_tokens = 10
        usage_chunk.model = "gemini-pro"

        with patch("uniclaw.provider.openai_provider.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = self._make_mock_stream(
                [
                    content_chunk,
                    usage_chunk,
                ]
            )

            _s = Session(root_dir=Path.cwd())
            _s.add_user_message(content="你好")
            config = _make_config(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
            )

            list(
                stream(
                    "",
                    _s,
                    model_name="test/gemini-pro",
                    config=config,
                )
            )

            # 验证 kwargs 中不包含 extra_body
            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert "extra_body" not in call_kwargs


class TestChatWithTools:
    """同步调用带工具的测试"""

    def test_chat_with_tool_calls(self):
        """测试同步调用返回 tool_calls"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_response.choices[0].message.tool_calls = [MagicMock()]
        mock_response.choices[0].message.tool_calls[0].id = "call_456"
        mock_response.choices[0].message.tool_calls[0].function.name = "search"
        mock_response.choices[0].message.tool_calls[
            0
        ].function.arguments = '{"query": "test"}'
        mock_response.choices[0].message.reasoning_content = None
        mock_response.choices[0].message.reasoning = None
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 30
        mock_response.model = "gpt-4"

        with patch("uniclaw.provider.openai_provider.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response

            _s = Session(root_dir=Path.cwd())
            _s.add_user_message(content="搜索测试")
            tools = [MagicMock(name="search", description="搜索", parameters={})]
            config = _make_config()

            result = chat(
                "",
                _s,
                tools=tools,
                model_name="test/gpt-4",
                config=config,
            )

            assert isinstance(result, AIMessage)
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0]["function"]["name"] == "search"
            assert result.tool_calls[0]["id"] == "call_456"

    def test_chat_extra_body_passed(self):
        """测试同步调用 extra_body 传递"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "回答"
        mock_response.choices[0].message.tool_calls = None
        mock_response.choices[0].message.reasoning_content = None
        mock_response.choices[0].message.reasoning = None
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 50
        mock_response.usage.completion_tokens = 10
        mock_response.model = "gpt-4"

        with patch("uniclaw.provider.openai_provider.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response

            _s = Session(root_dir=Path.cwd())
            _s.add_user_message(content="你好")
            config = _make_config()
            chat(
                "",
                _s,
                model_name="test/gpt-4",
                config=config,
                enable_thinking=True,
                thinking=False,
            )

            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert "extra_body" in call_kwargs
            assert call_kwargs["extra_body"]["thinking"]["type"] == "disabled"


@pytest.mark.asyncio
class TestAchatWithTools:
    """异步调用带工具的测试"""

    async def test_achat_with_tool_calls(self):
        """测试异步调用返回 tool_calls"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_response.choices[0].message.tool_calls = [MagicMock()]
        mock_response.choices[0].message.tool_calls[0].id = "call_789"
        mock_response.choices[0].message.tool_calls[0].function.name = "calculate"
        mock_response.choices[0].message.tool_calls[
            0
        ].function.arguments = '{"expr": "1+1"}'
        mock_response.choices[0].message.reasoning_content = None
        mock_response.choices[0].message.reasoning = None
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 80
        mock_response.usage.completion_tokens = 20
        mock_response.model = "gpt-4"

        with patch("uniclaw.provider.openai_provider.AsyncOpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client

            # 模拟异步上下文管理器
            async def mock_create(**kwargs):
                return mock_response

            mock_client.chat.completions.create = mock_create

            _s = Session(root_dir=Path.cwd())
            _s.add_user_message(content="计算 1+1")
            tools = [MagicMock(name="calculate", description="计算", parameters={})]
            config = _make_config()

            result = await achat(
                "",
                _s,
                tools=tools,
                model_name="test/gpt-4",
                config=config,
            )

            assert isinstance(result, AIMessage)
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0]["function"]["name"] == "calculate"


# ── 数据类型测试 ──────────────────────────────────────────────


class TestUsage:
    """Usage 数据类的测试"""

    def test_default_values(self):
        """测试默认值"""
        usage = Usage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.total_tokens == 0

    def test_auto_calculate_total(self):
        """测试自动计算 total_tokens"""
        usage = Usage(input_tokens=100, output_tokens=50)
        assert usage.total_tokens == 150

    def test_explicit_total(self):
        """测试显式设置 total_tokens"""
        usage = Usage(input_tokens=100, output_tokens=50, total_tokens=200)
        assert usage.total_tokens == 200


class TestStreamChunk:
    """StreamChunk 数据类的测试"""

    def test_default_values(self):
        """测试默认值"""
        chunk = StreamChunk()
        assert chunk.content == ""
        assert chunk.reasoning_content == ""
        assert chunk.tool_calls == []
        assert chunk.new_tool_call_name == ""
        assert chunk.new_tool_call_args == {}
        assert chunk.model_name == ""
        assert chunk.usage is None

    def test_iadd_content(self):
        """测试内容累积"""
        chunk1 = StreamChunk(content="Hello")
        chunk2 = StreamChunk(content=" World")
        chunk1 += chunk2
        assert chunk1.content == "Hello World"

    def test_iadd_reasoning(self):
        """测试推理内容累积"""
        chunk1 = StreamChunk(reasoning_content="think1")
        chunk2 = StreamChunk(reasoning_content="think2")
        chunk1 += chunk2
        assert chunk1.reasoning_content == "think1think2"

    def test_iadd_tool_calls(self):
        """测试工具调用累积"""
        chunk1 = StreamChunk(tool_calls=[{"id": "1"}])
        chunk2 = StreamChunk(tool_calls=[{"id": "2"}])
        chunk1 += chunk2
        assert len(chunk1.tool_calls) == 2

    def test_iadd_model_name(self):
        """测试模型名称更新"""
        chunk1 = StreamChunk(model_name="gpt-4")
        chunk2 = StreamChunk(model_name="gpt-4o")
        chunk1 += chunk2
        assert chunk1.model_name == "gpt-4o"

    def test_iadd_model_name_empty(self):
        """测试空模型名称不覆盖"""
        chunk1 = StreamChunk(model_name="gpt-4")
        chunk2 = StreamChunk(model_name="")
        chunk1 += chunk2
        assert chunk1.model_name == "gpt-4"

    def test_iadd_usage(self):
        """测试用量更新"""
        chunk1 = StreamChunk()
        chunk2 = StreamChunk(usage=Usage(input_tokens=100, output_tokens=50))
        chunk1 += chunk2
        assert chunk1.usage is not None
        assert chunk1.usage.input_tokens == 100

    def test_iadd_usage_none(self):
        """测试空用量不覆盖"""
        usage = Usage(input_tokens=100, output_tokens=50)
        chunk1 = StreamChunk(usage=usage)
        chunk2 = StreamChunk(usage=None)
        chunk1 += chunk2
        assert chunk1.usage is usage


# ── 辅助函数测试 ──────────────────────────────────────────────


class TestCompareUrls:
    """URL 比较函数测试"""

    def test_same_urls(self):
        """测试相同 URL"""
        assert (
            compare_urls("https://api.openai.com/v1/", "https://api.openai.com/v1/")
            is True
        )

    def test_trailing_slash(self):
        """测试尾部斜杠差异"""
        assert (
            compare_urls("https://api.openai.com/v1", "https://api.openai.com/v1/")
            is True
        )

    def test_case_insensitive_netloc(self):
        """测试域名大小写不敏感"""
        assert (
            compare_urls("https://API.OPENAI.COM/v1/", "https://api.openai.com/v1/")
            is True
        )

    def test_different_urls(self):
        """测试不同 URL"""
        assert (
            compare_urls("https://api.openai.com/v1/", "https://api.anthropic.com/v1/")
            is False
        )


class TestCreateHttpClient:
    """HTTP 客户端创建测试"""

    def test_localhost_returns_none(self):
        """测试本地地址返回 None"""
        result = create_http_client("http://127.0.0.1:8080/v1/")
        assert result is None

    def test_with_proxy(self):
        """测试带代理创建客户端"""
        result = create_http_client("https://api.openai.com/v1/", "http://proxy:8080")
        assert result is not None
        result.close()

    def test_no_proxy(self):
        """测试无代理返回 None"""
        result = create_http_client("https://api.openai.com/v1/", "")
        assert result is None


class TestCreateAsyncHttpClient:
    """异步 HTTP 客户端创建测试"""

    def test_localhost_returns_none(self):
        """测试本地地址返回 None"""
        result = create_async_http_client("http://127.0.0.1:8080/v1/")
        assert result is None

    def test_with_proxy(self):
        """测试带代理创建客户端"""
        result = create_async_http_client(
            "https://api.openai.com/v1/", "http://proxy:8080"
        )
        assert result is not None

    def test_no_proxy(self):
        """测试无代理返回 None"""
        result = create_async_http_client("https://api.openai.com/v1/", "")
        assert result is None


class TestResolveParams:
    """参数解析测试"""

    def test_from_config(self):
        """测试从 config 获取参数(通过 provider profile)"""
        from uniclaw.config import ProviderProfile

        config = MagicMock()
        config.model_name = ["mimo/gpt-4"]
        config.multimodal_model_name = ["mimo/gpt-4o"]
        config.proxy_url = "http://proxy:8080"
        config.providers = {
            "mimo": ProviderProfile(
                name="mimo",
                protocol="openai",
                api_key="test-key",
                base_url="https://api.openai.com/v1/",
            )
        }

        result = resolve_params(config)
        assert result["model_name"] == "gpt-4"
        assert result["openai_api_base"] == "https://api.openai.com/v1/"
        assert result["openai_api_key"] == "test-key"

    def test_global_proxy_not_applied_to_models(self):
        """全局 proxy_url 不应用到模型:provider 无自有代理时直连。"""
        config = MagicMock()
        config.model_name = ["mimo/gpt-4"]
        config.multimodal_model_name = []
        config.proxy_url = "http://global-proxy:8080"
        config.providers = {
            "mimo": ProviderProfile(
                name="mimo",
                protocol="openai",
                api_key="test-key",
                base_url="https://api.openai.com/v1/",
                proxy_url="",
            )
        }

        result = resolve_params(config)
        assert result["proxy_url"] == ""

    def test_provider_proxy_overrides(self):
        """provider 自有代理生效,不被全局代理干扰。"""
        config = MagicMock()
        config.model_name = ["mimo/gpt-4"]
        config.multimodal_model_name = []
        config.proxy_url = "http://global-proxy:8080"
        config.providers = {
            "mimo": ProviderProfile(
                name="mimo",
                protocol="openai",
                api_key="test-key",
                base_url="https://api.openai.com/v1/",
                proxy_url="http://my-proxy:3128",
            )
        }

        result = resolve_params(config)
        assert result["proxy_url"] == "http://my-proxy:3128"

    def test_no_config_proxy_default_empty(self):
        """无 config 时 proxy_url 兜底为空串(直连),不抛 KeyError。"""
        result = resolve_params(None, model_name="gpt-4", openai_api_key="key")
        assert result["proxy_url"] == ""

    def test_kwargs_override_config(self):
        """测试 kwargs 覆盖 config"""
        config = MagicMock()
        config.model_name = ["mimo/gpt-4"]
        config.multimodal_model_name = []
        config.proxy_url = ""
        config.providers = {"mimo": MagicMock()}

        result = resolve_params(config, model_name="gpt-4o")
        assert result["model_name"] == "gpt-4o"

    def test_no_config(self):
        """测试无 config 时使用 kwargs"""
        result = resolve_params(None, model_name="gpt-4", openai_api_key="key")
        assert result["model_name"] == "gpt-4"
        assert result["openai_api_key"] == "key"


class TestEffort:
    """Effort 枚举测试"""

    def test_values(self):
        """测试枚举值"""
        assert Effort.XHIGH == "xhigh"
        assert Effort.HIGH == "high"
        assert Effort.MEDIUM == "medium"
        assert Effort.MINIMAL == "minimal"
        assert Effort.LOW == "low"
        assert Effort.NONE == "none"


# ── ThoughtParser 测试 ──────────────────────────────────────


class TestThoughtParser:
    """ThoughtParser 类的测试"""

    def test_no_thought_tag(self):
        """测试无 thought 标签"""
        parser = ThoughtParser()
        thinking, content = parser.process("Hello World")
        assert thinking == ""
        assert content == "Hello World"

    def test_thought_tag_at_start(self):
        """测试开头的 thought 标签"""
        parser = ThoughtParser()
        thinking, content = parser.process("<thought>thinking</thought>Hello")
        assert thinking == "thinking"
        assert content == "Hello"

    def test_thinking_tag(self):
        """测试 thinking 标签"""
        parser = ThoughtParser()
        thinking, content = parser.process("<think>reasoning</think>Answer")
        assert thinking == "reasoning"
        assert content == "Answer"

    def test_partial_thought_tag(self):
        """测试部分 thought 标签（缓冲）"""
        parser = ThoughtParser()
        # 第一次：只收到部分标签
        thinking, content = parser.process("<thou")
        assert thinking == ""
        assert content == ""
        # 第二次：收到完整标签
        thinking, content = parser.process("ght>thinking</thought>Hello")
        assert thinking == "thinking"
        assert content == "Hello"

    def test_multiple_chunks(self):
        """测试多块处理"""
        parser = ThoughtParser()
        # 第一次处理：收到部分开标签，会被缓冲
        t1, c1 = parser.process("<thought")
        assert t1 == ""
        assert c1 == ""

        # 第二次处理：收到完整的开标签和内容
        t2, c2 = parser.process(">thinking</thought>")
        assert t2 == "thinking"
        assert c2 == ""

        # 第三次处理：thought 后的内容
        t3, c3 = parser.process("Hello")
        assert t3 == ""
        assert c3 == "Hello"

    def test_after_close_tag(self):
        """测试关闭标签后进入 TEXT 阶段"""
        parser = ThoughtParser()
        parser.process("<thought>thinking</thought>Hello")
        # 之后的调用应该直接返回内容
        thinking, content = parser.process(" World")
        assert thinking == ""
        assert content == " World"


# ── 多模态错误检测测试 ──────────────────────────────────────


class TestIsMultimodalError:
    """多模态错误检测测试"""

    def test_image_error(self):
        """测试图片错误"""
        error = Exception("image not supported")
        error.status_code = 400
        assert is_multimodal_error(error) is True

    def test_audio_error(self):
        """测试音频错误"""
        error = Exception("input_audio format error")
        error.status_code = 404
        assert is_multimodal_error(error) is True

    def test_video_error(self):
        """测试视频错误"""
        error = Exception("video_url not allowed")
        error.status_code = 400
        assert is_multimodal_error(error) is True

    def test_multimodal_keyword(self):
        """测试 multimodal 关键词"""
        error = Exception("multimodal content not supported")
        error.status_code = 400
        assert is_multimodal_error(error) is True

    def test_non_multimodal_error(self):
        """测试非多模态错误"""
        error = Exception("rate limit exceeded")
        error.status_code = 429
        assert is_multimodal_error(error) is False

    def test_no_status_code(self):
        """测试无状态码"""
        error = Exception("some error")
        assert is_multimodal_error(error) is False


class TestExtractMediaUrl:
    """媒体 URL 提取测试"""

    def test_image_url(self):
        """测试图片 URL"""
        block = {
            "type": "image_url",
            "image_url": {"url": "https://example.com/img.png"},
        }
        url, media_type = _extract_media_url(block)
        assert url == "https://example.com/img.png"
        assert media_type == "image"

    def test_input_audio(self):
        """测试音频数据"""
        block = {"type": "input_audio", "input_audio": {"data": "base64data"}}
        url, media_type = _extract_media_url(block)
        assert url == "base64data"
        assert media_type == "audio"

    def test_video_url(self):
        """测试视频 URL"""
        block = {
            "type": "video_url",
            "video_url": {"url": "https://example.com/video.mp4"},
        }
        url, media_type = _extract_media_url(block)
        assert url == "https://example.com/video.mp4"
        assert media_type == "video"

    def test_unknown_type(self):
        """测试未知类型"""
        block = {"type": "text", "text": "hello"}
        url, media_type = _extract_media_url(block)
        assert url == ""
        assert media_type == ""


class TestSafeParseArgs:
    """参数解析安全测试"""

    def test_empty_string(self):
        """测试空字符串"""
        assert safe_parse_args("") == {}

    def test_none(self):
        """测试 None"""
        assert safe_parse_args(None) == {}

    def test_valid_json(self):
        """测试有效 JSON"""
        assert safe_parse_args('{"key": "value"}') == {"key": "value"}

    def test_invalid_json(self):
        """测试无效 JSON"""
        assert safe_parse_args("not json") == {}

    def test_non_dict_json(self):
        """测试非字典 JSON"""
        assert safe_parse_args("[1, 2, 3]") == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
