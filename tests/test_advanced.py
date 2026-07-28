"""
高级测试方法演示

包含：
1. 属性测试 (Property-based Testing)
2. 参数化测试
3. Fixture 复用
4. 异步测试
5. Mock 边界测试
"""

import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from hypothesis import given, strategies as st, settings, example

from uniclaw.provider import Usage, compare_urls
from uniclaw.tools.session.session import StreamChunk
from uniclaw.provider.common import safe_parse_args
from uniclaw.utils.format import format_args_for_display
from uniclaw.config import Permissions, AppConfig

# ═══════════════════════════════════════════════════════════════
# 1. 属性测试 (Property-based Testing)
# ═══════════════════════════════════════════════════════════════


class TestPropertyBased:
    """属性测试：验证代码满足的通用属性"""

    @given(st.integers(min_value=0, max_value=1000000))
    def test_usage_meta_total_always_sum(self, tokens):
        """属性：total_tokens 应该等于 input + output"""
        usage = Usage(input_tokens=tokens, output_tokens=tokens)
        assert usage.total_tokens == tokens * 2

    @given(st.text(min_size=1), st.text(min_size=1))
    def test_stream_chunk_iadd_concatenates(self, s1, s2):
        """属性：+= 应该连接内容"""
        chunk1 = StreamChunk(content=s1)
        chunk2 = StreamChunk(content=s2)
        chunk1 += chunk2
        assert chunk1.content == s1 + s2

    @given(st.dictionaries(st.text(min_size=1), st.text()))
    def test_format_args_returns_string(self, args):
        """属性：format_args_for_display 总是返回字符串"""
        result = format_args_for_display(args)
        assert isinstance(result, str)

    @given(st.dictionaries(st.text(min_size=1), st.text()))
    def test_format_args_contains_all_keys(self, args):
        """属性：结果包含所有键"""
        result = format_args_for_display(args)
        for key in args:
            assert key in result

    @given(st.text())
    def testsafe_parse_args_never_raises(self, text):
        """属性：safe_parse_args 永远不抛异常"""
        result = safe_parse_args(text)
        assert isinstance(result, dict)

    @given(st.text(min_size=1), st.text(min_size=1))
    def test_compare_urls_reflexive(self, path1, path2):
        """属性：URL 比较是自反的"""
        url = f"https://example.com/{path1}"
        assert compare_urls(url, url) is True

    @given(st.integers(min_value=0), st.integers(min_value=0))
    def test_usage_meta_non_negative(self, inp, out):
        """属性：token 数量非负"""
        usage = Usage(input_tokens=inp, output_tokens=out)
        assert usage.input_tokens >= 0
        assert usage.output_tokens >= 0
        assert usage.total_tokens >= 0

    @given(st.lists(st.text()))
    def test_stream_chunk_tool_calls_accumulate(self, names):
        """属性：工具调用应该累积"""
        chunk = StreamChunk()
        for name in names:
            chunk += StreamChunk(tool_calls=[{"name": name}])
        assert len(chunk.tool_calls) == len(names)


# ═══════════════════════════════════════════════════════════════
# 2. 参数化测试
# ═══════════════════════════════════════════════════════════════


class TestParameterized:
    """参数化测试：覆盖多种输入场景"""

    @pytest.mark.parametrize(
        "input,expected",
        [
            ("", ""),
            ("hello", "hello"),
            ("a" * 100, "a" * 100),
            ("a" * 101, "a" * 100 + "...(省略1字符)"),
            ("line1\nline2", "line1...(省略6字符)"),
            (None, ""),
        ],
    )
    def test_format_args_edge_cases(self, input, expected):
        """测试 format_args 的边界情况"""
        if input is None:
            result = format_args_for_display(None)
        else:
            result = format_args_for_display({"key": input}, max_length=100)
        assert expected in result or result == expected

    @pytest.mark.parametrize(
        "perm,expected",
        [
            (Permissions.AUTO, "auto"),
            (Permissions.MANUAL, "manual"),
            (Permissions.ACCEPT_ALL, "accept-all"),
            (Permissions.PLAN, "plan"),
        ],
    )
    def test_permissions_values(self, perm, expected):
        """测试所有权限模式"""
        assert perm == expected

    @pytest.mark.parametrize(
        "url1,url2,expected",
        [
            ("https://api.openai.com/v1/", "https://api.openai.com/v1/", True),
            ("https://api.openai.com/v1", "https://api.openai.com/v1/", True),
            ("https://API.OPENAI.COM/v1/", "https://api.openai.com/v1/", True),
            ("https://api.openai.com/v1/", "https://api.anthropic.com/v1/", False),
        ],
    )
    def test_compare_urls(self, url1, url2, expected):
        """测试 URL 比较"""
        assert compare_urls(url1, url2) == expected

    @pytest.mark.parametrize(
        "json_str,expected",
        [
            ("", {}),
            ("null", {}),
            ("[]", {}),
            ('{"key": "value"}', {"key": "value"}),
            ("invalid json", {}),
            ('{"a": 1, "b": 2}', {"a": 1, "b": 2}),
        ],
    )
    def testsafe_parse_args(self, json_str, expected):
        """测试参数解析"""
        assert safe_parse_args(json_str) == expected


# ═══════════════════════════════════════════════════════════════
# 3. Fixture 复用
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def sample_config():
    """创建测试用配置"""
    from uniclaw.config import ProviderProfile

    return AppConfig(
        providers={
            "default": ProviderProfile(
                name="default",
                protocol="openai",
                api_key="test-key-123",
                base_url="https://api.openai.com/v1",
            )
        },
        model_name=["gpt-4"],
        temperature=0.7,
    )


@pytest.fixture
def sample_stream_chunk():
    """创建测试用 StreamChunk"""
    return StreamChunk(
        content="Hello",
        reasoning_content="thinking",
        model_name="gpt-4",
    )


@pytest.fixture
def mock_openai_client():
    """创建 Mock OpenAI 客户端"""
    with patch("openai.OpenAI") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


class TestWithFixtures:
    """使用 Fixture 的测试"""

    def test_config_defaults(self, sample_config):
        """测试配置默认值"""
        assert sample_config.providers["default"].api_key == "test-key-123"
        assert sample_config.model_name == ["gpt-4"]

    def test_config_isolation(self, sample_config):
        """测试配置隔离（每个测试独立）"""
        sample_config.model_name = ["gpt-4o"]
        assert sample_config.model_name == ["gpt-4o"]

    def test_stream_chunk_initial(self, sample_stream_chunk):
        """测试 StreamChunk 初始状态"""
        assert sample_stream_chunk.content == "Hello"
        assert sample_stream_chunk.reasoning_content == "thinking"

    def test_stream_chunk_modify(self, sample_stream_chunk):
        """测试 StreamChunk 修改"""
        sample_stream_chunk += StreamChunk(content=" World")
        assert sample_stream_chunk.content == "Hello World"

    def test_mock_client(self, mock_openai_client):
        """测试 Mock 客户端"""
        mock_openai_client.chat.completions.create.return_value = "response"
        result = mock_openai_client.chat.completions.create()
        assert result == "response"


# ═══════════════════════════════════════════════════════════════
# 4. 异步测试
# ═══════════════════════════════════════════════════════════════


class TestAsync:
    """异步测试"""

    @pytest.mark.asyncio
    async def test_async_function(self):
        """测试异步函数"""

        async def async_add(a, b):
            await asyncio.sleep(0.01)
            return a + b

        result = await async_add(1, 2)
        assert result == 3

    @pytest.mark.asyncio
    async def test_async_with_mock(self):
        """测试异步 Mock"""
        mock_func = AsyncMock(return_value="async result")
        result = await mock_func()
        assert result == "async result"

    @pytest.mark.asyncio
    async def test_async_exception(self):
        """测试异步异常"""

        async def failing_func():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            await failing_func()

    @pytest.mark.asyncio
    async def test_async_timeout(self):
        """测试异步超时"""

        async def slow_func():
            await asyncio.sleep(10)
            return "done"

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_func(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_async_gather(self):
        """测试并发执行"""

        async def fetch(id):
            await asyncio.sleep(0.01)
            return f"result-{id}"

        results = await asyncio.gather(*[fetch(i) for i in range(5)])
        assert len(results) == 5
        assert all(r.startswith("result-") for r in results)


# ═══════════════════════════════════════════════════════════════
# 5. Mock 边界测试
# ═══════════════════════════════════════════════════════════════


class TestMockBoundaries:
    """Mock 边界测试"""

    def test_mock_context_manager(self):
        """测试 Mock 上下文管理器"""
        with patch("openai.OpenAI") as mock_class:
            mock_instance = MagicMock()
            mock_class.return_value = mock_instance

            # 模拟链式调用
            mock_instance.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="response"))]
            )

            from openai import OpenAI

            client = OpenAI(api_key="test")
            response = client.chat.completions.create()

            assert response.choices[0].message.content == "response"

    def test_mock_side_effect(self):
        """测试 Mock 副作用"""
        mock_func = MagicMock()
        mock_func.side_effect = [1, 2, 3]

        assert mock_func() == 1
        assert mock_func() == 2
        assert mock_func() == 3

    def test_mock_exception(self):
        """测试 Mock 抛异常"""
        mock_func = MagicMock()
        mock_func.side_effect = ValueError("mock error")

        with pytest.raises(ValueError, match="mock error"):
            mock_func()

    def test_mock_assert_called(self):
        """测试 Mock 调用断言"""
        mock_func = MagicMock()
        mock_func("arg1", "arg2")

        mock_func.assert_called_once_with("arg1", "arg2")

    def test_patch_dict(self):
        """测试 patch.dict"""
        import os

        with patch.dict(os.environ, {"TEST_KEY": "test_value"}):
            assert os.environ.get("TEST_KEY") == "test_value"
        assert os.environ.get("TEST_KEY") is None


# ═══════════════════════════════════════════════════════════════
# 6. 测试组织和标记
# ═══════════════════════════════════════════════════════════════


class TestOrganization:
    """测试组织示例"""

    @pytest.mark.slow
    def test_slow_operation(self):
        """标记为慢测试"""
        import time

        time.sleep(0.1)
        assert True

    @pytest.mark.skip(reason="功能未实现")
    def test_future_feature(self):
        """跳过未实现的功能"""
        pass

    @pytest.mark.skipif(
        not hasattr(pytest, "importorskip"), reason="需要 pytest 特定版本"
    )
    def test_conditional_skip(self):
        """条件跳过"""
        pass

    @pytest.mark.xfail(reason="已知问题")
    def test_known_issue(self):
        """预期失败"""
        assert False


# ═══════════════════════════════════════════════════════════════
# 7. 自定义 Fixture 作用域
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def expensive_setup():
    """模块级 Fixture（整个模块只执行一次）"""
    print("\n[模块级 Setup]")
    data = {"initialized": True}
    yield data
    print("\n[模块级 Teardown]")


@pytest.fixture(scope="function")
def per_function_setup():
    """函数级 Fixture（每个测试执行一次）"""
    print("\n[函数级 Setup]")
    yield {"count": 0}
    print("\n[函数级 Teardown]")


class TestFixtureScopes:
    """Fixture 作用域测试"""

    def test_with_module_scope(self, expensive_setup):
        """使用模块级 Fixture"""
        assert expensive_setup["initialized"] is True

    def test_with_function_scope(self, per_function_setup):
        """使用函数级 Fixture"""
        per_function_setup["count"] += 1
        assert per_function_setup["count"] == 1

    def test_function_scope_isolation(self, per_function_setup):
        """验证函数级 Fixture 隔离"""
        assert per_function_setup["count"] == 0  # 每次都是新的
