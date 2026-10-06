"""IPython 内核测试 — 覆盖数据结构和工具注册。"""

import pytest
import time
from unittest.mock import MagicMock
from uniclaw.tools.ipython.kernel import (
    KernelInfo,
    ExecutionResult,
    VariableInfo,
    IPythonKernelManager,
)


class TestKernelInfo:
    """KernelInfo 数据结构测试。"""

    def test_creation(self):
        """创建 KernelInfo。"""
        info = KernelInfo(
            kernel_id="test",
            kernel_manager=None,
            client=None,
        )
        assert info.kernel_id == "test"
        assert info.execution_count == 0
        assert info.history == []
        assert info.created_at > 0

    def test_execution_count_default(self):
        """默认执行次数为 0。"""
        info = KernelInfo(kernel_id="k", kernel_manager=None, client=None)
        assert info.execution_count == 0

    def test_history_default_empty(self):
        """默认历史为空列表。"""
        info = KernelInfo(kernel_id="k", kernel_manager=None, client=None)
        assert info.history == []


class TestExecutionResult:
    """ExecutionResult 数据结构测试。"""

    def test_success_result(self):
        """成功结果格式化。"""
        result = ExecutionResult(
            success=True,
            output="hello",
            execution_count=1,
        )
        formatted = result.format()
        assert "[In 1]" in formatted
        assert "hello" in formatted
        assert result.error is None

    def test_error_result(self):
        """错误结果格式化。"""
        result = ExecutionResult(
            success=False,
            output="",
            error="NameError: x is not defined",
            execution_count=2,
        )
        formatted = result.format()
        assert "[In 2]" in formatted
        assert "错误" in formatted
        assert "NameError" in formatted

    def test_rich_output_image(self):
        """图像富文本输出。"""
        result = ExecutionResult(
            success=True,
            output="",
            rich_outputs=[{"type": "image/png", "data": "base64data..." * 10}],
            execution_count=3,
        )
        formatted = result.format()
        assert "图像数据" in formatted

    def test_rich_output_html(self):
        """HTML 富文本输出。"""
        result = ExecutionResult(
            success=True,
            output="",
            rich_outputs=[{"type": "text/html", "data": "<table>...</table>"}],
            execution_count=4,
        )
        formatted = result.format()
        assert "HTML" in formatted

    def test_rich_output_latex(self):
        """LaTeX 富文本输出。"""
        result = ExecutionResult(
            success=True,
            output="",
            rich_outputs=[{"type": "text/latex", "data": "E=mc^2"}],
            execution_count=5,
        )
        formatted = result.format()
        assert "LaTeX" in formatted
        assert "E=mc^2" in formatted

    def test_empty_result(self):
        """空结果。"""
        result = ExecutionResult(
            success=True, output="", execution_count=0
        )
        formatted = result.format()
        assert "[In 0]" in formatted


class TestVariableInfo:
    """VariableInfo 数据结构测试。"""

    def test_creation(self):
        """创建 VariableInfo。"""
        info = VariableInfo(
            name="x",
            type_name="int",
            repr_str="42",
            str_val="42",
        )
        assert info.name == "x"
        assert info.type_name == "int"
        assert info.repr_str == "42"
        assert info.doc is None
        assert info.length is None
        assert info.shape is None
        assert info.dtype is None

    def test_with_optional_fields(self):
        """带可选字段。"""
        info = VariableInfo(
            name="arr",
            type_name="ndarray",
            repr_str="[1 2 3]",
            str_val="[1 2 3]",
            length=3,
            shape="(3,)",
            dtype="int64",
        )
        assert info.length == 3
        assert info.shape == "(3,)"
        assert info.dtype == "int64"


class TestIPythonKernelManager:
    """IPythonKernelManager 单例测试。"""

    def test_singleton(self):
        """单例模式。"""
        m1 = IPythonKernelManager.get_instance()
        m2 = IPythonKernelManager.get_instance()
        assert m1 is m2

    def test_kernels_dict_initially_empty(self):
        """初始内核字典为空。"""
        mgr = IPythonKernelManager.get_instance()
        assert isinstance(mgr.kernels, dict)


class TestIPythonToolsRegistration:
    """IPython 工具注册测试。"""

    def test_get_tools_returns_list(self):
        """get_tools 返回列表。"""
        from uniclaw.tools.ipython.tools import get_tools

        result = get_tools()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_get_all_tools_returns_list(self):
        """get_all_tools 返回列表。"""
        from uniclaw.tools.ipython.tools import get_all_tools

        result = get_all_tools()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_tools_have_descriptions(self):
        """所有工具都有描述。"""
        from uniclaw.tools.ipython.tools import get_all_tools

        for t in get_all_tools():
            assert t.description, f"{t.name} 缺少描述"


class TestConcurrentExecutionCount:
    """并发 execute 的 [In N] 标签不能碰撞 — 每次拿到本次执行实际分配的编号。"""

    def _make_fake_client(self, counts: list[int]):
        """构造假 client: 每次 execute 吐一条 execute_input(带 execution_count)。"""
        import itertools

        counter = itertools.count(0)
        state = {"queue": []}

        def execute(code):
            idx = next(counter)
            n = counts[idx] if idx < len(counts) else idx + 1
            msg_id = f"msg-{idx}"
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "execute_input"},
                    "content": {"code": code, "execution_count": n},
                }
            )
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "stream"},
                    "content": {"name": "stdout", "text": f"out{idx}\n"},
                }
            )
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "status"},
                    "content": {"execution_state": "idle"},
                }
            )
            return msg_id

        def get_iopub_msg(timeout=None):
            if state["queue"]:
                return state["queue"].pop(0)
            raise Exception("no message")

        client = MagicMock()
        client.execute.side_effect = execute
        client.get_iopub_msg.side_effect = get_iopub_msg
        return client

    @pytest.mark.asyncio
    async def test_parallel_execute_gets_distinct_counts(self):
        """两次并发 execute 各自拿到自己的 execution_count, 不共享旧值。"""
        import asyncio
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        client = self._make_fake_client([2, 3])
        info = KernelInfo(
            kernel_id="race-test",
            kernel_manager=MagicMock(),
            client=client,
        )
        mgr.kernels["race-test"] = info
        try:
            r1, r2 = await asyncio.gather(
                mgr.execute("print(1)", "race-test"),
                mgr.execute("print(2)", "race-test"),
            )
            counts = sorted([r1.execution_count, r2.execution_count])
            assert counts == [2, 3], f"[In N] 标签碰撞: 都拿到 {counts}"
            # history 按执行顺序追加, 条数与执行次数一致
            assert len(info.history) == 2
        finally:
            mgr.kernels.pop("race-test", None)

    @pytest.mark.asyncio
    async def test_serial_execute_increments(self):
        """串行 execute 的 [In N] 依次递增。"""
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        client = self._make_fake_client([1, 2, 3])
        info = KernelInfo(
            kernel_id="serial-test", kernel_manager=MagicMock(), client=client
        )
        mgr.kernels["serial-test"] = info
        try:
            r1 = await mgr.execute("a=1", "serial-test")
            r2 = await mgr.execute("b=2", "serial-test")
            assert r1.execution_count == 1
            assert r2.execution_count == 2
            assert "[In 1]" in r1.format()
            assert "[In 2]" in r2.format()
        finally:
            mgr.kernels.pop("serial-test", None)


class TestEnsureKernelRace:
    """并行首次 execute 的自动启动不能起多个内核(TOCTOU)。

    回归测试: "先查 self.kernels 再 start_kernel" 在并发下两个调用都看到内核
    缺失,各起一个 —— 后者覆盖 self.kernels[kernel_id],前者成孤儿。表现为
    并行两次都拿到 [In 1](各自计数从 0 开始),且一个内核的 history 永久丢失。
    """

    @pytest.mark.asyncio
    async def test_parallel_autostart_creates_one_kernel(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from uniclaw.tools.ipython import tools as ipy_tools
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        mgr.kernels.clear()
        started = []

        async def fake_start(kernel_id="default", kernel_name="python3"):
            started.append(kernel_id)
            await asyncio.sleep(0.02)  # 放大竞态窗口
            info = KernelInfo(
                kernel_id=kernel_id,
                kernel_manager=MagicMock(),
                client=MagicMock(),
            )
            mgr.kernels[kernel_id] = info
            return info

        class _R:
            @staticmethod
            def format():
                return "ok"

        with patch.object(mgr, "start_kernel", side_effect=fake_start), patch.object(
            mgr, "execute", new=AsyncMock(return_value=_R())
        ):
            r1, r2 = await asyncio.gather(
                ipy_tools.ipython_execute("a"),
                ipy_tools.ipython_execute("b"),
            )

        try:
            assert started == ["default"]  # 关键: 只启动一次
            assert r1 == "ok" and r2 == "ok"
        finally:
            mgr.kernels.clear()

    @pytest.mark.asyncio
    async def test_ensure_kernel_returns_existing(self):
        """内核已存在时 ensure_kernel 直接复用, 不重复启动。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        mgr.kernels.clear()
        existing = KernelInfo(
            kernel_id="reuse-test", kernel_manager=MagicMock(), client=MagicMock()
        )
        mgr.kernels["reuse-test"] = existing
        try:
            with patch.object(mgr, "start_kernel", new=AsyncMock()) as fake:
                got = await mgr.ensure_kernel("reuse-test")
            assert got is existing
            fake.assert_not_awaited()
        finally:
            mgr.kernels.pop("reuse-test", None)
