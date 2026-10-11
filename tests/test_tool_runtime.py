"""ToolRuntime 测试 — 覆盖透传、schema 排除、stream() 与并行安全。"""

import asyncio
from types import SimpleNamespace
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
    async def test_wrong_type_reported_without_log(self, monkeypatch):
        """类型传错(该传 int 却传 str)→ 友好提示, 不写错误日志。"""
        from uniclaw.console import ui

        @tool
        def typed(count: int, names: list = None, tool_runtime: ToolRuntime = None) -> str:
            """Typed tool.

            Args:
                count: 数量。
                names: 名称列表。
            """
            return f"{count}"

        logged = []
        monkeypatch.setattr(
            ui, "err", AsyncMock(side_effect=lambda *a, **k: logged.append(a))
        )

        r1 = await typed(count="abc")
        assert "参数有误" in r1 and "count" in r1 and "int" in r1 and "str" in r1

        r2 = await typed(count=1, names="browser")
        assert "参数有误" in r2 and "names" in r2 and "list" in r2

        assert logged == []

    @pytest.mark.asyncio
    async def test_numeric_types_interchangeable(self):
        """数值类型互相放宽, 不能误杀(timeout=0.2 给 int 参数是常见用法)。"""
        @tool
        def f(x: float = 0.5, n: int = 30, tool_runtime: ToolRuntime = None) -> str:
            """Numeric tool.

            Args:
                x: 浮点数。
                n: 整数。
            """
            return f"{x}|{n}"

        assert await f(x=3) == "3|30"  # int → float
        assert await f(n=0.2) == "0.5|0.2"  # float → int (如 Bash 的 timeout=0.2)

    @pytest.mark.asyncio
    async def test_bool_not_accepted_as_number(self):
        """bool 不算数字(True 给 count 基本都是错的)。"""
        @tool
        def f(n: int = 1, tool_runtime: ToolRuntime = None) -> str:
            """N tool.

            Args:
                n: 数量。
            """
            return str(n)

        assert "参数有误" in await f(n=True)

    @pytest.mark.asyncio
    async def test_optional_and_unknown_annotation_pass(self):
        """Optional[...] 与不认识的注解(Enum 等)不做检查, 不误杀。"""
        from enum import StrEnum

        class Mode(StrEnum):
            a = "a"

        @tool
        def g(x: int | None = None, mode: Mode = Mode.a, tool_runtime: ToolRuntime = None) -> str:
            """G tool.

            Args:
                x: 可选数值。
                mode: 模式。
            """
            return f"{x}:{mode}"

        assert await g(x=None) == "None:a"
        assert await g(mode="a") == "None:a"  # Enum 注解不拦


class TestUserErrorNoLog:
    """ToolUserError: 预期失败, 透出消息且不写错误日志。"""

    @pytest.mark.asyncio
    async def test_user_error_not_logged(self, monkeypatch):
        from uniclaw.console import ui
        from uniclaw.tools.base import ToolUserError

        logged = []
        monkeypatch.setattr(
            ui, "err", AsyncMock(side_effect=lambda *a, **k: logged.append(a))
        )

        @tool
        def lookup(item_id: int, tool_runtime: ToolRuntime = None) -> str:
            """Lookup tool.

            Args:
                item_id: 条目 ID。
            """
            raise ToolUserError(f"条目 {item_id} 不存在(可用: [1, 2])")

        result = await lookup(item_id=999)

        assert "条目 999 不存在" in result
        assert "可用" in result  # 上下文要透出来
        assert logged == []  # 预期失败不写错误日志

    @pytest.mark.asyncio
    async def test_other_exception_still_logged(self, monkeypatch):
        """非 ToolUserError 仍视为 bug, 照常记日志。"""
        from uniclaw.console import ui

        logged = []
        monkeypatch.setattr(
            ui, "err", AsyncMock(side_effect=lambda *a, **k: logged.append(a))
        )

        @tool
        def broken(tool_runtime: ToolRuntime = None) -> str:
            """Broken tool."""
            raise ValueError("boom")

        result = await broken()
        assert "boom" in result
        assert len(logged) == 1

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


class TestFileNotFoundWarnOnly:
    """FileNotFoundError: 非 bug(外部路径问题), 只警告不写错误日志。"""

    @pytest.mark.asyncio
    async def test_warns_not_logs(self, monkeypatch, tmp_path):
        """文件不存在 → 报警告并返回 TOOL_ERROR, 不落错误日志。"""
        from uniclaw.console import ui

        err_log, warn_log = [], []
        monkeypatch.setattr(
            ui, "err", AsyncMock(side_effect=lambda *a, **k: err_log.append(a))
        )
        monkeypatch.setattr(
            ui, "warn", AsyncMock(side_effect=lambda *a, **k: warn_log.append(a))
        )

        @tool
        def read_file(path: str, tool_runtime: ToolRuntime = None) -> str:
            """Read file.

            Args:
                path: 文件路径。
            """
            return open(path, encoding="utf-8").read()

        # 父目录缺失, 等价于保存/读取不存在路径的场景(如 TTS 存到未建目录)
        missing = tmp_path / "no_such_dir" / "seg7_test.wav"
        result = await read_file(path=str(missing))

        assert "文件不存在" in result
        assert err_log == []  # 非 bug 不写错误日志
        assert len(warn_log) == 1  # 报个警告就行
        assert "read_file" in warn_log[0][0]


class TestNetworkErrorWarnOnly:
    """网络/模型厂商 API 错误(限流 429 等): 非 bug, 只警告不写错误日志。"""

    @pytest.mark.asyncio
    async def test_rate_limit_warns_not_logs(self, monkeypatch):
        """429 限流 → 报警告并返回 TOOL_ERROR, 不落错误堆栈。"""
        from uniclaw.console import ui

        err_log, warn_log = [], []
        monkeypatch.setattr(
            ui, "err", AsyncMock(side_effect=lambda *a, **k: err_log.append(a))
        )
        monkeypatch.setattr(
            ui, "warn", AsyncMock(side_effect=lambda *a, **k: warn_log.append(a))
        )

        class RateLimitError(Exception):
            """仿 openai.RateLimitError(429), 无需 SDK。"""

            status_code = 429

        @tool
        def call_model(tool_runtime: ToolRuntime = None) -> str:
            """Call model."""
            raise RateLimitError("Error code: 429 - Too Many Requests")

        result = await call_model()

        assert "模型/网络服务异常" in result
        assert err_log == []  # 非 bug 不写错误日志
        assert len(warn_log) == 1  # 报个警告就行
        assert "call_model" in warn_log[0][0]

    @pytest.mark.asyncio
    async def test_unknown_exception_still_logs(self, monkeypatch):
        """与网络无关的异常仍是 bug, 照常记日志(降噪判定不能误伤)。"""
        from uniclaw.console import ui

        err_log, warn_log = [], []
        monkeypatch.setattr(
            ui, "err", AsyncMock(side_effect=lambda *a, **k: err_log.append(a))
        )
        monkeypatch.setattr(
            ui, "warn", AsyncMock(side_effect=lambda *a, **k: warn_log.append(a))
        )

        @tool
        def broken(tool_runtime: ToolRuntime = None) -> str:
            """Broken tool."""
            raise ValueError("boom")

        result = await broken()
        assert "boom" in result
        assert len(err_log) == 1
        assert warn_log == []


class TestSchemaExclusion:
    """schema 生成排除注入参数。"""

    def test_schema_excludes_tool_runtime(self):
        """schema 不含 tool_runtime。"""
        sample, _ = TestToolCallPassThrough()._make_async_tool()
        schema = sample.to_openai_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "tool_runtime" not in props
        assert "value" in props


class TestExecuteBugs:
    """__call__/execute 的边界行为回归。"""

    @pytest.mark.asyncio
    async def test_async_tool_without_runtime_still_cancellable(self):
        """不收 tool_runtime 的 async 工具也必须能被 cancel_event 中断。

        回归测试: _bind_call 会按签名过滤掉 tool_runtime,execute 再去
        `kwargs.get("tool_runtime")` 就拿到 None —— 取消竞争对 89 个这类
        工具(Grep/browser_*/Docker_* 等)完全失效,ESC 打不断。
        """
        cancel_event = asyncio.Event()

        @tool
        async def slow(value: str) -> str:
            """Slow tool.

            Args:
                value: 输入值。
            """
            await asyncio.sleep(30)
            return "done"

        rt = ToolRuntime(
            config=SimpleNamespace(current_agent=SimpleNamespace(cancel_event=cancel_event))
        )
        asyncio.get_event_loop().call_later(0.05, cancel_event.set)
        result = await slow("x", tool_runtime=rt)  # 目标函数不收 tool_runtime
        assert "被用户中断" in result

    @pytest.mark.asyncio
    async def test_multiple_values_for_argument(self):
        """同一参数不能既位置又关键字传入(与 Python 语义一致)。"""

        @tool
        def f(a: str, b: int = 1, tool_runtime: ToolRuntime = None) -> str:
            """F tool.

            Args:
                a: 参数 a。
                b: 参数 b。
            """
            return f"{a}{b}"

        result = await f("x", a="y")
        assert "参数有误" in result
        assert "multiple values" in result

    @pytest.mark.asyncio
    async def test_param_error_truncates_large_values(self):
        """报错里的入参要截断, 大值不能撑爆报错文本。"""

        @tool
        def g(value: str = "", tool_runtime: ToolRuntime = None) -> str:
            """G tool.

            Args:
                value: 输入值。
            """
            return value

        result = await g(valu="x" * 5000)  # 拼错参数名 + 大值
        assert "参数有误" in result
        assert len(result) < 1000
