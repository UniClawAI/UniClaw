"""Linux UI 自动化后端测试 — 覆盖 automation_linux.py 的 xdotool 命令封装。

说明:
- automation_linux 通过 utils.process.run_command 执行 xdotool,
  测试 patch 该函数隔离真实 X11。
- 重点覆盖"动作类命令失败必须回报"这一约定: 点击/输入/聚焦失败时
  绝不能向 LLM 报"已点击""已输入"的假成功。
- 后端整体为异步: 同步 subprocess 会阻塞事件循环最长 10 秒。
"""

from unittest.mock import AsyncMock, patch

import pytest

from uniclaw.tools.computer_use import automation_linux as al
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.process import RunResult


def _ok(stdout: str = "") -> RunResult:
    """模拟命令成功(退出码 0)。"""
    return RunResult(0, stdout, "")


def _fail(stderr: str = "xdotool: badwindow", code: int = 1) -> RunResult:
    """模拟命令失败(非零退出码)。"""
    return RunResult(code, "", stderr)


def _which_ok(cmd: str, **kwargs) -> RunResult:
    """which xdotool 探测通过,其余命令失败。"""
    if cmd.startswith("which"):
        return _ok("/usr/bin/xdotool")
    return _fail()


def _patch_run(side_effect):
    """patch 掉 run_command,返回 AsyncMock 以便断言调用。"""
    return patch.object(
        al, "run_command", new_callable=AsyncMock, side_effect=side_effect
    )


class TestRunAction:
    """_run_action — 动作类命令必须校验退出码。"""

    @pytest.mark.asyncio
    async def test_success_returns_stdout(self):
        """成功时返回 stdout。"""
        with _patch_run(lambda cmd, **kw: _ok("ok\n")):
            assert await al._run_action("xdotool click 1") == "ok"

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises(self):
        """非零退出必须抛 _CmdError,不能返回空串假装成功。"""
        with _patch_run(lambda cmd, **kw: _fail("boom")):
            with pytest.raises(al._CmdError, match="退出码"):
                await al._run_action("xdotool click 1")

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        """超时必须抛 _CmdError(run_command 把超时收成 timed_out)。"""
        with _patch_run(lambda cmd, **kw: RunResult(-1, timed_out=True)):
            with pytest.raises(al._CmdError, match="超时"):
                await al._run_action("xdotool click 1")

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        """子进程无法启动时包成 _CmdError。"""
        with _patch_run(OSError("no xdotool")):
            with pytest.raises(al._CmdError, match="执行异常"):
                await al._run_action("xdotool click 1")

    @pytest.mark.asyncio
    async def test_error_carries_command_context(self):
        """报错必须带上原始命令,便于定位是哪一步失败。"""
        with _patch_run(lambda cmd, **kw: _fail("badwindow")):
            with pytest.raises(al._CmdError, match="xdotool mousemove"):
                await al._run_action("xdotool mousemove 10 20 click 1")


class TestRunProbe:
    """_run — 探测类命令失败返回空串,调用方自行判断。"""

    @pytest.mark.asyncio
    async def test_failure_returns_empty(self):
        """探测命令失败返回空串(如 which xdotool 未找到)。"""
        with _patch_run(lambda cmd, **kw: _fail()):
            assert await al._run("which xdotool") == ""

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        """探测命令异常也返回空串,不抛出。"""
        with _patch_run(OSError("nope")):
            assert await al._run("which xdotool") == ""

    @pytest.mark.asyncio
    async def test_success_returns_stdout(self):
        """成功时返回 stdout。"""
        with _patch_run(lambda cmd, **kw: _ok("12345")):
            assert await al._run("xdotool getactivewindow") == "12345"


class TestInteractNoFakeSuccess:
    """interact — 动作失败必须回报 TOOL_ERROR,不能报假成功。

    静默假结果 bug 的回归测试: 旧实现执行 xdotool 后无条件返回
    "已点击: (x, y)",命令实际失败时 LLM 会以为点击生效了。
    """

    @pytest.mark.asyncio
    async def test_click_failure_not_reported_as_success(self):
        """点击失败时返回 TOOL_ERROR,而不是"已点击"。"""
        def fake(cmd, **kwargs):
            if cmd.startswith("which"):
                return _ok("/usr/bin/xdotool")
            return _fail("X connection to :0 broken")

        with _patch_run(fake):
            result = await al.interact(action="click", x=10, y=20)

        assert "已点击" not in result
        assert TOOL_ERROR in result
        assert "click" in result
        assert "X connection to :0 broken" in result

    @pytest.mark.asyncio
    async def test_click_success_reported(self):
        """点击成功时照常回报。"""
        def fake(cmd, **kwargs):
            return _ok("/usr/bin/xdotool") if cmd.startswith("which") else _ok("")

        with _patch_run(fake):
            result = await al.interact(action="click", x=10, y=20)
        assert "已点击: (10, 20)" in result

    @pytest.mark.asyncio
    async def test_type_failure_not_reported_as_success(self):
        """输入失败时返回 TOOL_ERROR,而不是"已输入文本"。"""
        def fake(cmd, **kwargs):
            return _ok("/usr/bin/xdotool") if cmd.startswith("which") else _fail("xdotool: type failed")

        with _patch_run(fake):
            result = await al.interact(action="type", type_text="hello")

        assert "已输入文本" not in result
        assert TOOL_ERROR in result
        assert "type" in result

    @pytest.mark.asyncio
    async def test_focus_failure_not_reported_as_success(self):
        """聚焦失败时返回 TOOL_ERROR,而不是"已聚焦窗口"。"""
        def fake(cmd, **kwargs):
            if cmd.startswith("which"):
                return _ok("/usr/bin/xdotool")
            if "search" in cmd:
                return _ok("12345\n")
            return _fail("windowactivate failed")

        with _patch_run(fake):
            result = await al.interact(name="终端", action="focus")

        assert "已聚焦窗口" not in result
        assert TOOL_ERROR in result

    @pytest.mark.asyncio
    async def test_missing_xdotool_reported(self):
        """xdotool 未安装时给出安装指引。"""
        with _patch_run(lambda cmd, **kw: _ok("")):
            result = await al.interact(action="click", x=1, y=2)
        assert "需要安装 xdotool" in result
