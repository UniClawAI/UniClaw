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


class TestListVariablesFilters:
    """ipython_vars 不应把内建/IPython 注入变量列为用户变量。"""

    def _make_fake_client(self, output: str):
        """构造假 client: execute 吐固定 stdout + idle。"""
        state = {"queue": []}

        def execute(code):
            msg_id = "msg-0"
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "execute_input"},
                    "content": {"code": code, "execution_count": 1},
                }
            )
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "stream"},
                    "content": {"name": "stdout", "text": output},
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
    async def test_list_variables_skips_builtins_and_ipython_injected(self):
        """内建(open/len 等)和 IPython 注入(In/Out/exit 等)不出现在结果中。"""
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        # 模拟内核返回: 过滤后的用户变量列表(过滤逻辑在内核侧代码中)
        output = '[{"name": "x", "type": "int", "repr": "42"}]'
        client = self._make_fake_client(output)
        info = KernelInfo(
            kernel_id="vars-filter-test", kernel_manager=MagicMock(), client=client
        )
        mgr.kernels["vars-filter-test"] = info
        try:
            vars_list = await mgr.list_variables("vars-filter-test")
            assert len(vars_list) == 1
            assert vars_list[0].name == "x"
        finally:
            mgr.kernels.pop("vars-filter-test", None)

    @pytest.mark.asyncio
    async def test_list_variables_code_filters_builtins_and_injected(self):
        """探查代码本身包含内建和 IPython 注入变量的过滤逻辑。"""
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        captured = {}
        client = self._make_fake_client("[]")

        original_execute = client.execute.side_effect

        def capture_execute(code):
            captured["code"] = code
            return original_execute(code)

        client.execute.side_effect = capture_execute
        info = KernelInfo(
            kernel_id="vars-code-test", kernel_manager=MagicMock(), client=client
        )
        mgr.kernels["vars-code-test"] = info
        try:
            await mgr.list_variables("vars-code-test")
            code = captured["code"]
            assert "user_ns_hidden" in code, "应使用 user_ns_hidden 过滤 IPython 注入变量"
            assert "builtins" in code, "应使用 builtins 过滤内建函数"
        finally:
            mgr.kernels.pop("vars-code-test", None)


class TestProbeCodeNotInHistory:
    """工具自身的探查代码不应混入 ipython_history。"""

    def _make_fake_client(self):
        state = {"queue": []}

        def execute(code):
            msg_id = f"msg-{len(state['queue'])}"
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "execute_input"},
                    "content": {"code": code, "execution_count": 1},
                }
            )
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "stream"},
                    "content": {"name": "stdout", "text": "[]"},
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
    async def test_execute_record_history_false_skips_history(self):
        """record_history=False 时不追加到 history。"""
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        client = self._make_fake_client()
        info = KernelInfo(
            kernel_id="hist-test", kernel_manager=MagicMock(), client=client
        )
        mgr.kernels["hist-test"] = info
        try:
            await mgr.execute("user_code = 1", "hist-test", record_history=True)
            await mgr.execute("probe_code = 2", "hist-test", record_history=False)
            assert info.history == ["user_code = 1"]
        finally:
            mgr.kernels.pop("hist-test", None)

    @pytest.mark.asyncio
    async def test_list_variables_and_inspect_not_in_history(self):
        """list_variables / inspect_variable 的探查代码不进 history。"""
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        client = self._make_fake_client()
        info = KernelInfo(
            kernel_id="hist-test2", kernel_manager=MagicMock(), client=client
        )
        mgr.kernels["hist-test2"] = info
        try:
            await mgr.execute("user_var = 99", "hist-test2")
            await mgr.list_variables("hist-test2")
            assert len(info.history) == 1
            assert info.history[0] == "user_var = 99"
        finally:
            mgr.kernels.pop("hist-test2", None)


class TestTracebackTempFilename:
    """语法错误 traceback 中的临时文件名应替换为可读标识。"""

    def _make_error_client(self, evalue: str, traceback_lines: list[str]):
        state = {"queue": []}

        def execute(code):
            msg_id = "msg-0"
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "execute_input"},
                    "content": {"code": code, "execution_count": 1},
                }
            )
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "error"},
                    "content": {
                        "ename": "SyntaxError",
                        "evalue": evalue,
                        "traceback": traceback_lines,
                    },
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
    async def test_temp_filename_replaced_with_input(self):
        """evalue 中的 3524816064.py 等临时文件名替换为 <input>。"""
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        client = self._make_error_client(
            "incomplete input (3524816064.py, line 1)",
            ["  Cell In[1], line 1", "    def broken(", "               ^"],
        )
        info = KernelInfo(
            kernel_id="tb-test", kernel_manager=MagicMock(), client=client
        )
        mgr.kernels["tb-test"] = info
        try:
            r = await mgr.execute("def broken(", "tb-test")
            assert "3524816064.py" not in (r.error or "")
            assert "<input>" in (r.error or "")
        finally:
            mgr.kernels.pop("tb-test", None)

    @pytest.mark.asyncio
    async def test_temp_filename_replaced_in_traceback(self):
        """traceback 行中的临时文件名也替换为 <input>。"""
        import re

        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        client = self._make_error_client(
            "invalid syntax (1240104730.py, line 1)",
            ["  File \"1240104730.py\", line 1", "    x === 1", "        ^"],
        )
        info = KernelInfo(
            kernel_id="tb-test2", kernel_manager=MagicMock(), client=client
        )
        mgr.kernels["tb-test2"] = info
        try:
            r = await mgr.execute("x === 1", "tb-test2")
            assert not re.search(r"\b\d+\.py\b", r.error or "")
            assert "<input>" in (r.error or "")
        finally:
            mgr.kernels.pop("tb-test2", None)


class TestInspectTruncatesLargeContainers:
    """ipython_inspect 的大容器必须在内核侧截断,不能把全量 repr 穿过 IPC。"""

    def _make_fake_client(self, output: str):
        state = {"queue": []}

        def execute(code):
            msg_id = "msg-0"
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "execute_input"},
                    "content": {"code": code, "execution_count": 1},
                }
            )
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "stream"},
                    "content": {"name": "stdout", "text": output},
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

    async def _capture_inspect_code(self, name: str) -> str:
        """跑一遍 inspect_variable 拿到注入内核的探查代码。"""
        import json

        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        captured = {}
        stub = json.dumps(
            {
                "name": name,
                "type": "list",
                "summary": None,
                "repr": "[]",
                "str": "[]",
                "doc": None,
                "len": 0,
                "shape": None,
                "dtype": None,
            }
        )
        client = self._make_fake_client(stub)
        original_execute = client.execute.side_effect

        def capture_execute(code):
            captured["code"] = code
            return original_execute(code)

        client.execute.side_effect = capture_execute
        info = KernelInfo(
            kernel_id="inspect-cap-test", kernel_manager=MagicMock(), client=client
        )
        mgr.kernels["inspect-cap-test"] = info
        try:
            await mgr.inspect_variable(name, "inspect-cap-test")
        finally:
            mgr.kernels.pop("inspect-cap-test", None)
        return captured["code"]

    def _run_probe(self, code: str, namespace: dict) -> dict:
        """在本地执行探查代码,解析它 print 出的 JSON(等价于内核侧行为)。"""
        import contextlib
        import io
        import json

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(code, namespace)  # noqa: S102 - 测试里执行自产探查代码
        return json.loads(buf.getvalue())

    @pytest.mark.asyncio
    async def test_probe_code_has_kernel_side_truncation(self):
        """探查代码内含截断与摘要逻辑(截断发生在内核侧,不是 Python 侧)。"""
        code = await self._capture_inspect_code("big")
        assert "_trunc" in code
        assert "_summary" in code
        assert "截断" in code

    @pytest.mark.asyncio
    async def test_large_list_not_fully_serialized(self):
        """20 万元素 list: 返回带长度摘要,repr 被截断且不含全量元素。"""
        from uniclaw.tools.ipython.kernel import INSPECT_TEXT_LIMIT

        code = await self._capture_inspect_code("big")
        data = self._run_probe(code, {"big": list(range(200000))})

        # 摘要格式: list, 长度 200000
        assert data["summary"] == "list, 长度 200000"
        # repr 被截断到上限附近(上限 + 截断标注)
        assert "截断" in data["repr"]
        assert len(data["repr"]) < INSPECT_TEXT_LIMIT + 100
        assert len(data["str"]) < INSPECT_TEXT_LIMIT + 100
        # 不含全量 20 万元素(全量 repr 约百万字符)
        assert "199999" not in data["repr"]
        assert len(data["repr"]) < 5000

    @pytest.mark.asyncio
    async def test_large_dict_and_str_summaries(self):
        """5000 项 dict / 20000 字符 str 同样内核侧截断并给摘要。"""
        from uniclaw.tools.ipython.kernel import INSPECT_TEXT_LIMIT

        code = await self._capture_inspect_code("obj")
        big_dict = {f"k{i}": i for i in range(5000)}
        data_d = self._run_probe(code, {"obj": big_dict})
        assert data_d["summary"] == "dict, 5000 项"
        assert "截断" in data_d["repr"]
        assert len(data_d["repr"]) < INSPECT_TEXT_LIMIT + 100

        big_str = "x" * 20000
        data_s = self._run_probe(code, {"obj": big_str})
        assert data_s["summary"] == "str, 20000 字符"
        assert "截断" in data_s["repr"]
        assert len(data_s["repr"]) < INSPECT_TEXT_LIMIT + 100
        assert len(data_s["str"]) < INSPECT_TEXT_LIMIT + 100

    @pytest.mark.asyncio
    async def test_small_values_not_truncated(self):
        """正常尺寸的 inspect 不受影响(现有测试覆盖的 None/小对象场景)。"""
        code = await self._capture_inspect_code("obj")
        data = self._run_probe(code, {"obj": [1, 2, 3]})
        assert data["repr"] == "[1, 2, 3]"
        assert "截断" not in data["repr"]
        assert data["summary"] == "list, 长度 3"

        data2 = self._run_probe(code, {"obj": 42})
        assert data2["repr"] == "42"
        assert data2["summary"] is None
        assert "截断" not in data2["str"]


class TestDeadKernelDetection:
    """死内核必须给出明确报错和重启指引,不能静默返回空结果。"""

    def _make_dead_info(self, kernel_id: str = "dead-test") -> KernelInfo:
        km = MagicMock()
        km.is_alive.return_value = False
        client = MagicMock()
        return KernelInfo(kernel_id=kernel_id, kernel_manager=km, client=client)

    @pytest.mark.asyncio
    async def test_execute_raises_on_dead_kernel(self):
        from uniclaw.tools.ipython.kernel import IPythonKernelManager

        mgr = IPythonKernelManager.get_instance()
        mgr.kernels["dead-test"] = self._make_dead_info()
        try:
            with pytest.raises(RuntimeError, match="已终止") as exc:
                await mgr.execute("print(1)", "dead-test", timeout=5)
            assert "ipython_start" in str(exc.value)
        finally:
            mgr.kernels.pop("dead-test", None)

    @pytest.mark.asyncio
    async def test_ipython_execute_tool_error_on_dead_kernel(self):
        from uniclaw.tools.ipython import tools as ipy_tools
        from uniclaw.tools.ipython.kernel import IPythonKernelManager
        from uniclaw.utils.constants import TOOL_ERROR

        mgr = IPythonKernelManager.get_instance()
        mgr.kernels["dead-tool-test"] = self._make_dead_info("dead-tool-test")
        try:
            result = await ipy_tools.ipython_execute("print(1)", "dead-tool-test")
            assert TOOL_ERROR in result
            assert "已终止" in result
            assert "ipython_start" in result
        finally:
            mgr.kernels.pop("dead-tool-test", None)

    @pytest.mark.asyncio
    async def test_death_during_execution_detected(self):
        """执行中内核挂掉(os._exit): 收不到 idle 且 is_alive=False → 报已终止。"""
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        km = MagicMock()
        # 执行前存活,执行后死亡
        km.is_alive.side_effect = [True, False]
        state = {"queue": []}

        def execute(code):
            msg_id = "msg-0"
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "execute_input"},
                    "content": {"code": code, "execution_count": 1},
                }
            )
            return msg_id

        def get_iopub_msg(timeout=None):
            if state["queue"]:
                return state["queue"].pop(0)
            raise Exception("timeout")  # 内核已死,收不到 idle

        client = MagicMock()
        client.execute.side_effect = execute
        client.get_iopub_msg.side_effect = get_iopub_msg
        info = KernelInfo(kernel_id="death-during", kernel_manager=km, client=client)
        mgr.kernels["death-during"] = info
        try:
            with pytest.raises(RuntimeError, match="已终止"):
                await mgr.execute("import os; os._exit(0)", "death-during", timeout=1)
        finally:
            mgr.kernels.pop("death-during", None)

    @pytest.mark.asyncio
    async def test_slow_execution_not_misclassified_as_dead(self):
        """慢执行(超时但 is_alive=True)不能报"已终止"。"""
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        km = MagicMock()
        km.is_alive.return_value = True
        state = {"queue": []}

        def execute(code):
            msg_id = "msg-0"
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "execute_input"},
                    "content": {"code": code, "execution_count": 1},
                }
            )
            return msg_id

        def get_iopub_msg(timeout=None):
            if state["queue"]:
                return state["queue"].pop(0)
            raise Exception("timeout")

        client = MagicMock()
        client.execute.side_effect = execute
        client.get_iopub_msg.side_effect = get_iopub_msg
        info = KernelInfo(
            kernel_id="slow-alive", kernel_manager=km, client=client
        )
        mgr.kernels["slow-alive"] = info
        try:
            r = await mgr.execute("import time; time.sleep(99)", "slow-alive", timeout=1)
            # 慢执行: 不能抛"已终止"
            assert r.success is True or (r.error and "已终止" not in r.error)
        finally:
            mgr.kernels.pop("slow-alive", None)

    @pytest.mark.asyncio
    async def test_vars_on_dead_kernel_has_clear_reason(self):
        """死内核的 ipython_vars 不能出现"解析变量列表失败:"(冒号后为空)。"""
        from uniclaw.tools.ipython import tools as ipy_tools
        from uniclaw.tools.ipython.kernel import IPythonKernelManager
        from uniclaw.utils.constants import TOOL_ERROR

        mgr = IPythonKernelManager.get_instance()
        mgr.kernels["dead-vars"] = self._make_dead_info("dead-vars")
        try:
            result = await ipy_tools.ipython_vars("dead-vars")
            assert TOOL_ERROR in result
            assert "已终止" in result
            assert "解析变量列表失败:" not in result or result.strip() != "解析变量列表失败:"
            # 不能是空原因
            assert not result.rstrip().endswith("失败:")
        finally:
            mgr.kernels.pop("dead-vars", None)

    @pytest.mark.asyncio
    async def test_inspect_on_dead_kernel_has_clear_reason(self):
        from uniclaw.tools.ipython import tools as ipy_tools
        from uniclaw.tools.ipython.kernel import IPythonKernelManager
        from uniclaw.utils.constants import TOOL_ERROR

        mgr = IPythonKernelManager.get_instance()
        mgr.kernels["dead-inspect"] = self._make_dead_info("dead-inspect")
        try:
            result = await ipy_tools.ipython_inspect("x", "dead-inspect")
            assert TOOL_ERROR in result
            assert "已终止" in result
            assert "ipython_start" in result
        finally:
            mgr.kernels.pop("dead-inspect", None)

    @pytest.mark.asyncio
    async def test_start_kernel_replaces_dead_instead_of_already_exists(self):
        """死内核不应报'已存在',应清掉旧条目后允许 ipython_start 重启。"""
        from unittest.mock import MagicMock, patch

        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        mgr = IPythonKernelManager.get_instance()
        dead = self._make_dead_info("restart-dead")
        mgr.kernels["restart-dead"] = dead
        try:
            with patch("jupyter_client.manager.KernelManager") as mock_cls:
                km = MagicMock()
                mock_cls.return_value = km
                kc = MagicMock()
                km.client.return_value = kc
                info = await mgr.start_kernel("restart-dead")
            assert info.kernel_id == "restart-dead"
            assert mgr.kernels["restart-dead"] is info
            # 旧死内核的通道被关闭
            dead.client.stop_channels.assert_called()
        finally:
            mgr.kernels.pop("restart-dead", None)


class TestStringResultUnescape:
    """%lsmagic 等返回字符串的魔术命令,输出应还原真实换行而非字面 \\n。"""

    def _make_result_client(self, text_plain: str):
        state = {"queue": []}

        def execute(code):
            msg_id = "msg-0"
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "execute_input"},
                    "content": {"code": code, "execution_count": 1},
                }
            )
            state["queue"].append(
                {
                    "parent_header": {"msg_id": msg_id},
                    "header": {"msg_type": "execute_result"},
                    "content": {
                        "execution_count": 1,
                        "data": {"text/plain": text_plain},
                    },
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

    def test_unwrap_str_repr_restores_newlines(self):
        from uniclaw.tools.ipython.kernel import _unwrap_str_repr

        raw = "Available line magics:\n%alias %cd\nAutomagic is ON"
        assert _unwrap_str_repr(repr(raw)) == raw

    def test_unwrap_str_repr_keeps_plain_short_repr(self):
        from uniclaw.tools.ipython.kernel import _unwrap_str_repr

        assert _unwrap_str_repr("'hi'") == "'hi'"
        assert _unwrap_str_repr("42") == "42"
        assert _unwrap_str_repr("[1, 2]") == "[1, 2]"

    def test_unwrap_str_repr_invalid_literal_unchanged(self):
        from uniclaw.tools.ipython.kernel import _unwrap_str_repr

        s = "'not a valid \\u literal"
        assert _unwrap_str_repr(s) == s

    @pytest.mark.asyncio
    async def test_lsmagic_like_output_has_real_newlines(self):
        """模拟 %lsmagic 的 execute_result: 输出含真实换行,不含字面 \\n。"""
        from uniclaw.tools.ipython.kernel import IPythonKernelManager, KernelInfo

        raw = "Available line magics:\n%alias %cd %lsmagic\nAvailable cell magics:\n%%bash %%time"
        mgr = IPythonKernelManager.get_instance()
        client = self._make_result_client(repr(raw))
        info = KernelInfo(
            kernel_id="lsmagic-test", kernel_manager=MagicMock(), client=client
        )
        mgr.kernels["lsmagic-test"] = info
        try:
            r = await mgr.execute("%lsmagic", "lsmagic-test")
            assert r.success
            assert "\n" in r.output
            assert "\\n" not in r.output
            assert "Available line magics:" in r.output
            assert r.output == raw
        finally:
            mgr.kernels.pop("lsmagic-test", None)

# ── ipython_vars 截断标注(第 38 轮 #5) ────────────────────


class TestIpythonVarsTruncationLabel:
    def test_kernel_code_marks_truncation(self):
        """ipython_vars 的 repr 截断带长度标注,与 inspect 口径一致。"""
        from pathlib import Path

        from uniclaw.tools.ipython import kernel

        src = Path(kernel.__file__).read_text(encoding="utf-8")
        assert "截断, 共 " in src

    def test_kernel_code_labels_item_count(self):
        """清单截断标注同时给出项数与 repr 字符数,与 inspect 的长度摘要同口径。"""
        from pathlib import Path

        from uniclaw.tools.ipython import kernel

        src = Path(kernel.__file__).read_text(encoding="utf-8")
        assert "项, repr " in src
