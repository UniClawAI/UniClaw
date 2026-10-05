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


class TestCallExceptionFallback:
    """__call__ 异常兜底: 不外抛,返回 TOOL_ERROR; execute 仍外抛。"""

    def _make_raising_tool(self):
        @tool
        async def boom(value: str, tool_runtime: ToolRuntime = None) -> str:
            """Raising tool.

            Args:
                value: 输入值。
            """
            raise ValueError(f"炸了:{value}")

        return boom

    @pytest.mark.asyncio
    async def test_call_does_not_raise(self):
        """__call__ 吞掉异常并返回 TOOL_ERROR 文本。"""
        from uniclaw.utils.constants import TOOL_ERROR

        boom = self._make_raising_tool()
        result = await boom("x")  # 不应抛出
        assert isinstance(result, str)
        assert TOOL_ERROR in result
        assert "炸了:x" in result

    @pytest.mark.asyncio
    async def test_execute_still_raises(self):
        """execute 保留原始异常,便于需要精确断言的调用方使用。"""
        boom = self._make_raising_tool()
        with pytest.raises(ValueError, match="炸了:x"):
            await boom.execute("x")

    @pytest.mark.asyncio
    async def test_call_reports_via_ui_err(self, monkeypatch):
        """异常经 console.ui.err 上报,带上调用入参与异常对象以便落堆栈。"""
        from uniclaw.console import ui

        captured = {}

        async def fake_err(msg, config=None, e=None):
            captured["msg"] = msg
            captured["e"] = e

        monkeypatch.setattr(ui, "err", fake_err)
        boom = self._make_raising_tool()
        result = await boom(value="y", tool_runtime=ToolRuntime(tool_call_id="c1"))
        assert "炸了:y" in result
        assert isinstance(captured["e"], ValueError)
        # 入参进日志,且过滤掉注入参数 tool_runtime
        assert "value" in captured["msg"]
        assert "y" in captured["msg"]
        assert "tool_runtime" not in captured["msg"]

    @pytest.mark.asyncio
    async def test_call_logs_positional_args(self, monkeypatch):
        """位置参数调用时入参进日志, 且映射成真实参数名(不是 arg0)。"""
        from uniclaw.console import ui

        captured = {}

        async def fake_err(msg, config=None, e=None):
            captured["msg"] = msg

        monkeypatch.setattr(ui, "err", fake_err)
        boom = self._make_raising_tool()
        await boom("pos")
        assert "'value': 'pos'" in captured["msg"]

    @pytest.mark.asyncio
    async def test_positional_error_still_fallback(self):
        """位置参数触发的 TypeError 同样被兜底。"""
        from uniclaw.utils.constants import TOOL_ERROR

        sample, _ = TestToolCallPassThrough()._make_async_tool()
        result = await sample("a", "b", "c")  # 参数过多
        assert TOOL_ERROR in result


class TestParamErrorFriendly:
    """模型传参错误: 返回友好提示并给出正确签名, 且不写错误日志。"""

    def _tool(self):
        @tool
        def sample(value: str, limit: int = 10, tool_runtime: ToolRuntime = None) -> str:
            """Sample tool.

            Args:
                value: 输入值。
                limit: 上限。
            """
            return f"{value}:{limit}"

        return sample

    @pytest.mark.asyncio
    async def test_missing_required_param(self, monkeypatch):
        """漏传必填参数 → 友好提示 + 正确签名, 不写日志。"""
        from uniclaw.console import ui

        sample = self._tool()
        logged = []
        monkeypatch.setattr(
            ui, "err", AsyncMock(side_effect=lambda *a, **k: logged.append(a))
        )

        result = await sample()

        assert "参数有误" in result
        assert "sample(" in result
        assert "value" in result
        assert "limit=" in result  # 含默认值展示
        assert logged == []  # 关键: 模型传参错误不写错误日志

    @pytest.mark.asyncio
    async def test_unknown_param_name(self, monkeypatch):
        """参数名拼错 → 友好提示, 不写日志。"""
        from uniclaw.console import ui

        sample = self._tool()
        logged = []
        monkeypatch.setattr(
            ui, "err", AsyncMock(side_effect=lambda *a, **k: logged.append(a))
        )

        result = await sample(valu="x")

        assert "参数有误" in result
        # 要点出拼错的名字, 不能只报 "missing required argument: 'value'"
        assert "unexpected keyword" in result
        assert "valu" in result
        assert logged == []

    @pytest.mark.asyncio
    async def test_too_many_positional(self, monkeypatch):
        """位置参数过多 → 友好提示, 不写日志。"""
        from uniclaw.console import ui

        sample = self._tool()
        logged = []
        monkeypatch.setattr(
            ui, "err", AsyncMock(side_effect=lambda *a, **k: logged.append(a))
        )

        result = await sample("a", 1, 2, 3)  # sample 只有 3 个形参

        assert "参数有误" in result
        assert logged == []

    @pytest.mark.asyncio
    async def test_signature_hides_injected_params(self):
        """正确签名里不出现 tool_runtime(schema 里也没有)。"""
        sample = self._tool()
        result = await sample()
        assert "tool_runtime" not in result

    @pytest.mark.asyncio
    async def test_body_typeerror_still_logged_as_bug(self, monkeypatch):
        """绑定通过后函数体内的 TypeError 是 bug, 照常记日志。"""
        from uniclaw.console import ui

        @tool
        def buggy(value: str, tool_runtime: ToolRuntime = None) -> str:
            """Buggy tool.

            Args:
                value: 输入值。
            """
            return value + 1  # TypeError: 绑定通过, 是真 bug

        logged = []
        monkeypatch.setattr(
            ui, "err", AsyncMock(side_effect=lambda *a, **k: logged.append(a))
        )

        result = await buggy("x")

        assert "参数有误" not in result
        assert len(logged) == 1  # bug 要留痕


class TestSchemaExclusion:
    """schema 生成排除注入参数。"""

    def test_schema_excludes_tool_runtime(self):
        """schema 不含 tool_runtime。"""
        sample, _ = TestToolCallPassThrough()._make_async_tool()
        schema = sample.to_openai_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "tool_runtime" not in props
        assert "value" in props
