"""IPython 内核测试 — 覆盖数据结构和工具注册。"""

import pytest
import time
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
