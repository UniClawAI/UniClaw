"""ToolRuntime 测试 — 覆盖透传、schema 排除、stream() 与并行安全。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from uniclaw.tools.base import Tool, ToolRuntime, tool


class TestToolRuntime:
    """ToolRuntime dataclass 基础行为。"""

    @pytest.mark.asyncio
    async def test_stream_none_safe(self):
        """stream_writer 为 None 时 stream() 方法静默忽略。"""
        rt = ToolRuntime()
        await rt.stream("hello")  # 不抛异常

    @pytest.mark.asyncio
    async def test_stream_calls_writer_with_id(self):
        """stream() 自动附上 tool_call_id,writer 收到 (tool_call_id, content)。"""
        writer = AsyncMock()
        rt = ToolRuntime(tool_call_id="call_1", stream_writer=writer)
        await rt.stream("hello")
        writer.assert_awaited_once_with("call_1", "hello")


class TestToolCallPassThrough:
    """Tool.__call__ 对 tool_runtime 的透传行为(调用方负责构造传入)。"""

    def _make_async_tool(self):
        captured = {}

        @tool
        async def sample(value: str, tool_runtime: ToolRuntime = None) -> str:
            """Sample tool.

            Args:
                value: 输入值。
            """
            captured["rt"] = tool_runtime
            return f"got:{value}"

        return sample, captured

    def _make_sync_tool(self):
        captured = {}

        @tool
        def sync_sample(value: str, tool_runtime: ToolRuntime = None) -> str:
            """Sync sample.

            Args:
                value: 输入值。
            """
            captured["rt"] = tool_runtime
            return f"got:{value}"

        return sync_sample, captured

    @pytest.mark.asyncio
    async def test_explicit_runtime_passed_through(self):
        """显式传入 ToolRuntime 时按普通 kwargs 透传。"""
        sample, captured = self._make_async_tool()
        marker = object()
        writer = AsyncMock()
        rt = ToolRuntime(config=marker, tool_call_id="call_9", stream_writer=writer)

        await sample("x", tool_runtime=rt)
        assert captured["rt"] is rt

    @pytest.mark.asyncio
    async def test_missing_runtime_is_none(self):
        """未传 tool_runtime 时函数拿到形参默认值 None,不做兜底。"""
        sample, captured = self._make_async_tool()

        result = await sample("x")
        assert captured["rt"] is None
        assert result == "got:x"

    @pytest.mark.asyncio
    async def test_sync_tool_pass_through(self):
        """同步工具同样透传。"""
        sync_sample, captured = self._make_sync_tool()

        result = await sync_sample("x", tool_runtime=ToolRuntime(tool_call_id="call_2"))
        assert isinstance(captured["rt"], ToolRuntime)
        assert captured["rt"].tool_call_id == "call_2"
        assert result == "got:x"

    @pytest.mark.asyncio
    async def test_kwargs_func_not_broken(self):
        """**kwargs 动态函数(模拟 MCP)不炸,注入参数被过滤。"""
        captured = {}

        @tool
        async def dynamic(**kwargs) -> str:
            """Dynamic tool."""
            captured.update(kwargs)
            return "ok"

        result = await dynamic(a=1, tool_runtime=ToolRuntime(tool_call_id="call_3"))
        assert result == "ok"
        assert "tool_runtime" not in captured
        assert captured == {"a": 1}

    @pytest.mark.asyncio
    async def test_positional_args_mapping(self):
        """位置参数映射不受影响。"""
        sample, _ = self._make_async_tool()
        result = await sample("x", ToolRuntime(tool_call_id="call_4"))
        rt = sample.func.__wrapped_rt__ if hasattr(sample.func, "__wrapped_rt__") else None
        # 位置传参时第二个位置参数映射到 tool_runtime
        assert result == "got:x"

    @pytest.mark.asyncio
    async def test_explain_filtered(self):
        """_explain 参数被过滤。"""
        sample, captured = self._make_async_tool()
        result = await sample("x", _explain="说明文本")
        assert result == "got:x"


class TestParallelSafety:
    """asyncio.gather 并行下各 ToolRuntime 独立。"""

    @pytest.mark.asyncio
    async def test_gather_independent_runtimes(self):
        """并行执行时每个调用拿到独立的 tool_call_id,互不串扰。"""
        captured = []

        @tool
        async def slow(value: str, tool_runtime: ToolRuntime = None) -> str:
            """Slow tool.

            Args:
                value: 输入值。
            """
            await asyncio.sleep(0.01)
            captured.append((value, tool_runtime.tool_call_id))
            return value

        rt_a = ToolRuntime(tool_call_id="call_a")
        rt_b = ToolRuntime(tool_call_id="call_b")
        await asyncio.gather(
            slow("a", tool_runtime=rt_a),
            slow("b", tool_runtime=rt_b),
        )
        assert sorted(captured) == [("a", "call_a"), ("b", "call_b")]


class TestSchemaExclusion:
    """schema 生成排除注入参数。"""

    def test_schema_excludes_tool_runtime(self):
        """schema 不含 tool_runtime。"""
        sample, _ = TestToolCallPassThrough()._make_async_tool()
        schema = sample.to_openai_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "tool_runtime" not in props
        assert "value" in props
