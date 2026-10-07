"""Shell 工具测试 — 覆盖 Bash、Grep、Everything 搜索和辅助函数。"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from uniclaw.tools import shell as shell_mod
from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.shell import (
    Bash,
    Grep,
    GrepOutputMode,
    _check_es,
    _count_binary_files,
    _find_git_bash,
    _is_binary_file,
    _prepare_heredoc_script,
    _python_grep,
    contains_heredoc,
    fix_bash_nul_redirect,
    get_all_tools,
    get_tools,
    search_files_with_everything,
    smart_decode,
)
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.process import RunResult


def _make_rt(cancel_event=None):
    """构造带 mock config 的 ToolRuntime,供 Bash/Grep 测试使用。"""
    config = MagicMock()
    config.root_dir = "/tmp/work"
    agent = MagicMock()
    agent.cancel_event = cancel_event
    config.current_agent = agent
    return ToolRuntime(config=config)


class TestFixBashNul:
    """fix_bash_nul_redirect 测试。"""

    def test_output_redirect(self):
        """>nul 替换。"""
        assert fix_bash_nul_redirect("echo hi >nul") == "echo hi >/dev/null"

    def test_stderr_redirect(self):
        """2>nul 替换。"""
        assert fix_bash_nul_redirect("cmd 2>nul") == "cmd 2>/dev/null"

    def test_append_redirect(self):
        """>>nul 替换。"""
        assert fix_bash_nul_redirect("echo >>nul") == "echo >>/dev/null"

    def test_input_redirect(self):
        """<nul 替换。"""
        assert fix_bash_nul_redirect("cmd <nul") == "cmd </dev/null"

    def test_uppercase(self):
        """>NUL 大小写不敏感。"""
        assert fix_bash_nul_redirect("echo >NUL") == "echo >/dev/null"

    def test_no_nul(self):
        """无 nul 不变。"""
        assert fix_bash_nul_redirect("echo hello") == "echo hello"

    def test_multiple(self):
        """多处替换。"""
        assert (
            fix_bash_nul_redirect("cmd 2>nul 1>nul")
            == "cmd 2>/dev/null 1>/dev/null"
        )


class TestContainsHeredoc:
    """contains_heredoc / _prepare_heredoc_script 测试(任务 1: heredoc 误入 REPL 保护)。"""

    def test_detects_heredoc(self):
        """识别 << / <<- heredoc。"""
        assert contains_heredoc("python - <<'EOF'\nprint(1)\nEOF")
        assert contains_heredoc("cat <<-EOF\nhi\nEOF")
        assert contains_heredoc('bash -c "python - <<EOF\nprint(1)\nEOF"')

    def test_ignores_bit_shift(self):
        """位移运算 $((1<<2)) / x<<3 不误判为 heredoc。"""
        assert not contains_heredoc("echo $((1<<2))")
        assert not contains_heredoc("python -c 'print(1<<3)'")
        assert not contains_heredoc("echo hello")

    def test_ignores_append_redirect(self):
        """>> 重定向不算 heredoc。"""
        assert not contains_heredoc("echo hi >> out.txt")

    @pytest.mark.asyncio
    async def test_windows_heredoc_rewrites_to_script(self, tmp_path):
        """Windows 下 heredoc 改写为 bash 执行临时脚本,不把原命令直接丢给 cmd.exe。"""
        from uniclaw.tools.shell import Bash

        cmd = "python - <<'EOF'\nprint(123)\nEOF"
        proc = AsyncMock()
        proc.pid = 123
        proc.returncode = 0
        proc.stdin = None
        out_stream = AsyncMock()
        out_stream.read = AsyncMock(side_effect=[b"123\n", b""])
        proc.stdout = out_stream
        proc.stderr = None

        with patch(
            "uniclaw.tools.shell.sys.platform", "win32"
        ), patch(
            "uniclaw.tools.shell._find_bash", return_value=r"C:\Git\bin\bash.exe"
        ), patch(
            "uniclaw.tools.shell.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=proc,
        ) as mock_exec:
            result = await Bash(cmd, tool_runtime=self._rt() if hasattr(self, "_rt") else _make_rt())

        # 实际执行的命令应是 bash <临时脚本>,而不是含 heredoc 的原始命令
        spawned = mock_exec.await_args.args[0]
        assert "bash.exe" in spawned
        assert ".sh" in spawned
        assert "<<" not in spawned
        assert result == "123"

    @pytest.mark.asyncio
    async def test_windows_heredoc_without_bash_rejected(self):
        """Windows 下含 heredoc 但找不到 bash 时拒绝执行,提示改用文件脚本。"""
        from uniclaw.tools.shell import Bash

        with patch("uniclaw.tools.shell.sys.platform", "win32"), patch(
            "uniclaw.tools.shell._find_bash", return_value=None
        ), patch(
            "uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock
        ) as mock_exec:
            result = await Bash(
                "python - <<'EOF'\nprint(1)\nEOF", tool_runtime=_make_rt()
            )
        assert TOOL_ERROR in result
        assert "heredoc" in result
        assert "REPL" in result
        assert "文件脚本" in result or ".sh" in result
        mock_exec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_windows_heredoc_not_rewritten(self):
        """非 Windows 平台 heredoc 原样透传( /bin/sh 天然支持)。"""
        from uniclaw.tools.shell import Bash

        proc = AsyncMock()
        proc.pid = 123
        proc.returncode = 0
        proc.stdin = None
        out_stream = AsyncMock()
        out_stream.read = AsyncMock(side_effect=[b"ok\n", b""])
        proc.stdout = out_stream
        proc.stderr = None

        with patch("uniclaw.tools.shell.sys.platform", "linux"), patch(
            "uniclaw.tools.shell.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=proc,
        ) as mock_exec:
            result = await Bash(
                "cat <<'EOF'\nhi\nEOF", tool_runtime=_make_rt()
            )
        spawned = mock_exec.await_args.args[0]
        assert "<<" in spawned
        assert result == "ok"

    def test_prepare_heredoc_script_non_windows_passthrough(self):
        """非 Windows 直接返回原命令。"""
        with patch("uniclaw.tools.shell.sys.platform", "linux"):
            cmd, tmp = _prepare_heredoc_script("cat <<EOF\nhi\nEOF")
        assert cmd == "cat <<EOF\nhi\nEOF"
        assert tmp is None

    def test_prepare_heredoc_script_no_heredoc_passthrough(self):
        """无 heredoc 不改写。"""
        with patch("uniclaw.tools.shell.sys.platform", "win32"):
            cmd, tmp = _prepare_heredoc_script("echo hello")
        assert cmd == "echo hello"
        assert tmp is None

    def test_prepare_heredoc_script_writes_script(self):
        """Windows + heredoc 写出临时脚本,内容与原命令一致。"""
        import os

        src = "python - <<'EOF'\nprint(1)\nEOF"
        with patch("uniclaw.tools.shell.sys.platform", "win32"), patch(
            "uniclaw.tools.shell._find_bash", return_value="bash"
        ):
            cmd, tmp = _prepare_heredoc_script(src)
        assert tmp is not None
        assert os.path.isfile(tmp)
        try:
            body = open(tmp, encoding="utf-8").read()
            assert "print(1)" in body
            assert "EOF" in body
            assert cmd.startswith('"bash"')
        finally:
            os.unlink(tmp)


class TestSmartDecode:
    """smart_decode 测试。"""

    def test_empty(self):
        """空数据。"""
        assert smart_decode(b"") == ""

    def test_utf8(self):
        """UTF-8 解码。"""
        assert smart_decode("你好".encode("utf-8")) == "你好"

    def test_gbk(self):
        """GBK 解码。"""
        assert smart_decode("你好".encode("gbk")) == "你好"

    def test_gb18030(self):
        """GB18030 解码。"""
        assert smart_decode("你好".encode("gb18030")) == "你好"

    def test_fallback_replace(self):
        """无法解码使用 replace。"""
        data = b"\xff\xfe\x00\x01\x02"
        result = smart_decode(data)
        assert isinstance(result, str)


class TestGrepOutputMode:
    """GrepOutputMode 测试。"""

    def test_values(self):
        """枚举值。"""
        assert GrepOutputMode.content == "content"
        assert GrepOutputMode.files_with_matches == "files_with_matches"
        assert GrepOutputMode.count == "count"


class TestPythonGrep:
    """_python_grep 回退实现测试。"""

    def _make_file(self, tmp_path, name="a.txt", content="hello world\nfoo bar\nhello again\n"):
        f = tmp_path / name
        f.write_text(content, encoding="utf-8")
        return f

    def test_content_mode(self, tmp_path):
        """content 模式带行号。"""
        f = self._make_file(tmp_path)
        out = _python_grep("hello", str(tmp_path))
        lines = out.splitlines()
        assert any(l.startswith(f"{f}:1:") for l in lines)
        assert any(l.startswith(f"{f}:3:") for l in lines)
        assert len(lines) == 2

    def test_files_with_matches(self, tmp_path):
        """files_with_matches 模式。"""
        f = self._make_file(tmp_path)
        out = _python_grep("hello", str(tmp_path), output_mode="files_with_matches")
        assert out == str(f)

    def test_count_mode(self, tmp_path):
        """count 模式。"""
        f = self._make_file(tmp_path)
        out = _python_grep("hello", str(tmp_path), output_mode="count")
        assert out == f"{f}:2"

    def test_no_match(self, tmp_path):
        """无匹配。"""
        self._make_file(tmp_path)
        assert _python_grep("zzz", str(tmp_path)) == "No matches found"

    def test_case_insensitive(self, tmp_path):
        """忽略大小写。"""
        f = self._make_file(tmp_path)
        out = _python_grep("HELLO", str(tmp_path), case_insensitive=True)
        assert str(f) in out

    def test_case_sensitive(self, tmp_path):
        """区分大小写。"""
        self._make_file(tmp_path)
        assert _python_grep("HELLO", str(tmp_path)) == "No matches found"

    def test_context(self, tmp_path):
        """上下文行。"""
        f = self._make_file(tmp_path)
        out = _python_grep("foo", str(tmp_path), context=1)
        # 行 1,2,3 都显示,行 2 是匹配行
        assert f"{f}:1-" in out
        assert f"{f}:2:" in out
        assert f"{f}:3-" in out

    def test_invalid_regex(self, tmp_path):
        """无效正则。"""
        self._make_file(tmp_path)
        out = _python_grep("(", str(tmp_path))
        assert "无效的正则表达式" in out

    def test_nonexistent_path(self, tmp_path):
        """路径不存在。"""
        out = _python_grep("x", str(tmp_path / "missing"))
        assert "路径不存在" in out

    def test_file_target(self, tmp_path):
        """直接搜文件。"""
        f = self._make_file(tmp_path)
        out = _python_grep("hello", str(f))
        assert str(f) in out

    def test_directory_empty(self, tmp_path):
        """空目录。"""
        assert _python_grep("x", str(tmp_path)) == "No matches found"

    def test_glob_filter(self, tmp_path):
        """glob 过滤。"""
        self._make_file(tmp_path, name="a.py", content="hello py\n")
        self._make_file(tmp_path, name="b.txt", content="hello txt\n")
        out = _python_grep("hello", str(tmp_path), glob="*.txt")
        assert "b.txt" in out
        assert "a.py" not in out

    def test_unknown_mode(self, tmp_path):
        """未知模式。"""
        self._make_file(tmp_path)
        out = _python_grep("x", str(tmp_path), output_mode="bogus")
        assert "未知的 output_mode" in out


class TestBash:
    """Bash 工具测试。"""

    def _config(self, cancel_event=None):
        config = MagicMock()
        config.root_dir = "/tmp/work"
        agent = MagicMock()
        agent.cancel_event = cancel_event
        config.current_agent = agent
        return config

    def _rt(self, cancel_event=None):
        return ToolRuntime(config=self._config(cancel_event))

    def _proc(self, returncode=0, pid=123, stdout=None, stderr=None):
        proc = AsyncMock()
        proc.pid = pid
        proc.returncode = returncode
        proc.stdin = None
        proc.stdout = stdout
        proc.stderr = stderr
        return proc

    @pytest.mark.asyncio
    async def test_timeout_must_be_positive(self):
        """超时必须大于 0。"""
        from uniclaw.tools.shell import Bash

        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell") as mock_exec:
            result = await Bash("echo hi", timeout=0, tool_runtime=self._rt())
        assert "大于 0" in result
        mock_exec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_negative_rejected(self):
        """负数超时被拒绝。"""
        from uniclaw.tools.shell import Bash

        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell") as mock_exec:
            result = await Bash("echo hi", timeout=-1, tool_runtime=self._rt())
        assert "大于 0" in result
        mock_exec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_max_limit(self):
        """超过上限的超时被拒绝,引导使用 monitor_start。"""
        from uniclaw.tools.shell import BASH_MAX_TIMEOUT, Bash

        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell") as mock_exec:
            result = await Bash(
                "echo hi", timeout=BASH_MAX_TIMEOUT + 1, tool_runtime=self._rt()
            )
        assert str(BASH_MAX_TIMEOUT) in result
        assert "monitor_start" in result
        mock_exec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_at_max_allowed(self):
        """上限值本身允许通过。"""
        from uniclaw.tools.shell import BASH_MAX_TIMEOUT, Bash

        proc = self._proc()
        with patch(
            "uniclaw.tools.shell.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=proc,
        ):
            result = await Bash(
                "true", timeout=BASH_MAX_TIMEOUT, tool_runtime=self._rt()
            )
        assert result == "(没有输出)"

    @pytest.mark.asyncio
    async def test_no_output(self):
        """无输出返回占位。"""
        from uniclaw.tools.shell import Bash

        proc = self._proc()
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc):
            result = await Bash("true", tool_runtime=self._rt())
        assert result == "(没有输出)"

    @pytest.mark.asyncio
    async def test_with_output(self):
        """有输出返回内容。"""
        from uniclaw.tools.shell import Bash

        out_stream = AsyncMock()
        out_stream.read = AsyncMock(side_effect=[b"hello\n", b""])
        proc = self._proc(stdout=out_stream)
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc):
            result = await Bash("echo hello", tool_runtime=self._rt())
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_stderr_appended(self):
        """stderr 追加到输出。"""
        from uniclaw.tools.shell import Bash

        out_stream = AsyncMock()
        out_stream.read = AsyncMock(side_effect=[b"out\n", b""])
        err_stream = AsyncMock()
        err_stream.read = AsyncMock(side_effect=[b"err\n", b""])
        proc = self._proc(stdout=out_stream, stderr=err_stream)
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc):
            result = await Bash("echo out; echo err >&2", tool_runtime=self._rt())
        # stdout 的末尾换行保留,再拼接 [stderr]
        assert result == "out\n\n[stderr]err"

    @pytest.mark.asyncio
    async def test_cancelled(self):
        """用户取消返回中断消息。"""
        from uniclaw.tools.shell import Bash

        cancel_event = MagicMock()
        cancel_event.is_set.return_value = True
        cancel_event.wait = AsyncMock()
        proc = self._proc(returncode=None)
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc), patch(
            "uniclaw.tools.shell._kill_proc_tree", new_callable=AsyncMock
        ):
            result = await Bash("sleep 100", tool_runtime=self._rt(cancel_event))
        assert "用户中断" in result

    @pytest.mark.asyncio
    async def test_external_cancel_kills_process(self):
        """外部取消(ESC / Tool 层 task.cancel)必须杀掉进程树,不留孤儿。

        竞态背景:Tool.__call__ 的 cancel_wait 会先于 Bash 内层轮询醒来并
        task.cancel(),CancelledError 直接打进 Bash,旧代码只清理 read tasks
        不杀进程,子进程会脱管继续运行。
        """
        from uniclaw.tools.shell import Bash

        async def never():
            await asyncio.Event().wait()

        proc = self._proc(returncode=None)
        proc.wait = never
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc), patch(
            "uniclaw.tools.shell._kill_proc_tree", new_callable=AsyncMock
        ) as mock_kill:
            task = asyncio.create_task(Bash("sleep 100", tool_runtime=self._rt()))
            await asyncio.sleep(0.05)  # 让 Bash 进入等待循环
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        mock_kill.assert_awaited()

    @pytest.mark.asyncio
    async def test_timeout_transfers_without_kill(self):
        """超时转交 monitor 时不杀进程。"""
        from uniclaw.tools.shell import Bash

        async def never():
            await asyncio.Event().wait()

        proc = self._proc(returncode=None)
        proc.wait = never
        mock_monitor = AsyncMock()
        mock_monitor.register_existing_process = AsyncMock(return_value=("abc12345", None))
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc), patch(
            "uniclaw.tools.shell._kill_proc_tree", new_callable=AsyncMock
        ) as mock_kill, patch(
            "uniclaw.tools.monitor.manager.MonitorManager.get_instance", return_value=mock_monitor
        ):
            result = await Bash("sleep 100", timeout=0.2, tool_runtime=self._rt())
        assert "abc12345" in result
        mock_kill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout(self):
        """超时后进程转入监控。"""
        from uniclaw.tools.shell import Bash

        async def never():
            await asyncio.Event().wait()

        proc = self._proc(returncode=None)
        proc.wait = never
        mock_monitor = AsyncMock()
        mock_monitor.register_existing_process = AsyncMock(return_value=("abc12345", None))
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc), patch(
            "uniclaw.tools.monitor.manager.MonitorManager.get_instance", return_value=mock_monitor
        ):
            result = await Bash("sleep 100", timeout=0.2, tool_runtime=self._rt())
        assert "超时" in result
        assert "abc12345" in result
        assert "监控" in result

    @pytest.mark.asyncio
    async def test_exception(self):
        """创建进程异常直接抛出(create_subprocess_shell 在 try 外)。

        走 execute() 而非 __call__: 异常外抛是 execute 的契约,
        __call__ 只做兜底并返回 TOOL_ERROR 文本。
        """
        from uniclaw.tools.shell import Bash

        with patch(
            "uniclaw.tools.shell.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            side_effect=OSError("boom"),
        ):
            with pytest.raises(OSError):
                await Bash.execute("echo hi", tool_runtime=self._rt())

    @pytest.mark.asyncio
    async def test_output_overflow_killed(self, monkeypatch):
        """无界输出超过阈值时终止进程树并明确标注(任务 1 回归)。

        刷屏场景: 30s 超时转后台后仍持续输出,实测 140 万行才手动停。
        必须在行数/字节数超阈值时主动终止,不能让输出把上下文冲垮。
        """
        from uniclaw.tools.shell import Bash

        monkeypatch.setattr(shell_mod, "BASH_MAX_OUTPUT_LINES", 100)
        monkeypatch.setattr(shell_mod, "BASH_MAX_OUTPUT_BYTES", 10**9)

        call_count = {"n": 0}

        async def endless_read(_n):
            call_count["n"] += 1
            if call_count["n"] > 20:
                await asyncio.Event().wait()  # 阻塞住,模拟持续刷屏
            return b"spam line\n" * 20  # 每块 20 行,很快超过 100 行阈值

        out_stream = AsyncMock()
        out_stream.read = AsyncMock(side_effect=endless_read)

        async def never():
            await asyncio.Event().wait()

        proc = self._proc(returncode=None, stdout=out_stream)
        proc.wait = never

        with patch(
            "uniclaw.tools.shell.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=proc,
        ), patch(
            "uniclaw.tools.shell._kill_proc_tree", new_callable=AsyncMock
        ) as mock_kill:
            result = await Bash("spam", timeout=5, tool_runtime=self._rt())

        assert "输出超出上限已终止" in result
        assert "行" in result and "字节" in result
        # 阈值必须出现在返回文本里,便于调用方调整
        assert "100" in result
        mock_kill.assert_awaited()

    @pytest.mark.asyncio
    async def test_output_overflow_by_bytes(self, monkeypatch):
        """字节数超阈值同样终止。"""
        from uniclaw.tools.shell import Bash

        monkeypatch.setattr(shell_mod, "BASH_MAX_OUTPUT_LINES", 10**9)
        monkeypatch.setattr(shell_mod, "BASH_MAX_OUTPUT_BYTES", 1024)

        call_count = {"n": 0}

        async def endless_read(_n):
            call_count["n"] += 1
            if call_count["n"] > 20:
                await asyncio.Event().wait()
            return b"x" * 512

        out_stream = AsyncMock()
        out_stream.read = AsyncMock(side_effect=endless_read)

        async def never():
            await asyncio.Event().wait()

        proc = self._proc(returncode=None, stdout=out_stream)
        proc.wait = never

        with patch(
            "uniclaw.tools.shell.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=proc,
        ), patch(
            "uniclaw.tools.shell._kill_proc_tree", new_callable=AsyncMock
        ) as mock_kill:
            result = await Bash("spam", timeout=5, tool_runtime=self._rt())

        assert "输出超出上限已终止" in result
        mock_kill.assert_awaited()

    @pytest.mark.asyncio
    async def test_output_under_limit_not_killed(self):
        """输出未超阈值时正常返回,不误杀。"""
        from uniclaw.tools.shell import Bash

        out_stream = AsyncMock()
        out_stream.read = AsyncMock(side_effect=[b"hello\n", b""])
        proc = self._proc(stdout=out_stream)
        with patch(
            "uniclaw.tools.shell.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=proc,
        ), patch(
            "uniclaw.tools.shell._kill_proc_tree", new_callable=AsyncMock
        ) as mock_kill:
            result = await Bash("echo hello", tool_runtime=self._rt())
        assert result == "hello"
        mock_kill.assert_not_awaited()


class TestGrepTool:
    """Grep 工具测试。"""

    @pytest.mark.asyncio
    async def test_python_fallback(self, tmp_path):
        """无原生 grep 时用 Python 回退。"""
        f = tmp_path / "a.txt"
        f.write_text("hello\n", encoding="utf-8")
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=False):
            result = await Grep("hello", str(tmp_path))
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_uses_rg(self, tmp_path):
        """使用 ripgrep。"""
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch(
            "uniclaw.tools.shell.run_command",
            new_callable=AsyncMock,
            return_value=RunResult(0, "a.txt:1:hello\n"),
        ) as m:
            result = await Grep("hello", str(tmp_path))
        assert "hello" in result
        assert m.await_args.args[0][0] == "rg"

    @pytest.mark.asyncio
    async def test_uses_grep(self, tmp_path):
        """使用原生 grep。"""
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=False
        ), patch(
            "uniclaw.tools.shell.run_command",
            new_callable=AsyncMock,
            return_value=RunResult(0, "a.txt:1:hello\n"),
        ) as m:
            result = await Grep("hello", str(tmp_path))
        assert "hello" in result
        assert m.await_args.args[0][0] == "grep"

    @pytest.mark.asyncio
    async def test_no_match(self, tmp_path):
        """无匹配返回提示(rg/grep 退出码 1 表示无匹配)。"""
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch(
            "uniclaw.tools.shell.run_command", new_callable=AsyncMock, return_value=RunResult(1, "")
        ):
            result = await Grep("hello", str(tmp_path))
        assert result == "No matches found"

    @pytest.mark.asyncio
    async def test_error_not_reported_as_no_match(self, tmp_path):
        """出错(退出码 >=2)必须回报错误,不能伪装成 "No matches found"。

        这是静默假结果 bug 的回归测试: 非法正则 / 路径不存在时 rg 退出码为 2,
        stdout 为空,旧实现会返回 "No matches found",误导 LLM 认为符号不存在。
        """
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch(
            "uniclaw.tools.shell.run_command",
            new_callable=AsyncMock,
            return_value=RunResult(2, "", "regex parse error: unclosed group\n"),
        ):
            result = await Grep("hello(", str(tmp_path))
        assert "No matches found" not in result
        assert "退出码 2" in result
        assert "regex parse error" in result
        assert "hello(" in result

    @pytest.mark.asyncio
    async def test_zero_exit_empty_stdout_is_no_match(self, tmp_path):
        """退出码 0 但 stdout 为空仍视为无匹配(不误报错误)。"""
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch(
            "uniclaw.tools.shell.run_command", new_callable=AsyncMock, return_value=RunResult(0, "")
        ):
            result = await Grep("hello", str(tmp_path))
        assert result == "No matches found"

    @pytest.mark.asyncio
    async def test_timeout(self, tmp_path):
        """搜索超时。

        run_command 把超时收成 RunResult(timed_out=True),不再抛 TimeoutError。
        """
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch(
            "uniclaw.tools.shell.run_command",
            new_callable=AsyncMock,
            return_value=RunResult(-1, timed_out=True),
        ):
            result = await Grep("hello", str(tmp_path))
        assert "搜索超时" in result

    @pytest.mark.asyncio
    async def test_python_fallback_searches_hidden_dir(self, tmp_path):
        """显式给出的搜索根是隐藏目录(如 .UniClaw/temp)时必须可搜。

        回归测试: 兜底规则 `.*` 按绝对路径分量匹配,导致 .UniClaw/xxx 下的
        所有文件被清空、静默返回 "No matches found" —— 而 rg 的语义是
        显式路径强制搜索。Windows 无 rg 时走的正是 _python_grep 这条路。
        """
        root = tmp_path / ".UniClaw" / "temp" / "round6"
        root.mkdir(parents=True)
        (root / "rep1.md").write_text("COMMON_MARKER_XYZ alpha\n", encoding="utf-8")
        (root / "rep2.txt").write_text("COMMON_MARKER_XYZ hello\n", encoding="ascii")

        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=False):
            result = await Grep("COMMON_MARKER_XYZ", str(root))

        assert "No matches found" not in result
        assert "rep1.md" in result
        assert "rep2.txt" in result

    def test_python_fallback_still_filters_nested_ignored(self, tmp_path):
        """根不被忽略时, 内部的隐藏项(如 .git)照常过滤。"""
        root = tmp_path / "proj"
        root.mkdir()
        (root / "keep.txt").write_text("HIT_XYZ\n", encoding="utf-8")
        nested = root / ".git"
        nested.mkdir()
        (nested / "secret.txt").write_text("HIT_XYZ\n", encoding="utf-8")

        out = _python_grep("HIT_XYZ", str(root))
        assert "keep.txt" in out
        assert "secret.txt" not in out

    @pytest.mark.asyncio
    async def test_grep_fallback_recurses_into_dirs(self, tmp_path):
        """rg 缺失回退到 grep 时必须带 -r,否则目录搜索整体失效。

        回归测试: 无 -r 时 `grep -n pattern 目录` 报 "Is a directory"(exit 2),
        旧代码忽略退出码会伪装成 "No matches found",误导 LLM 认为符号不存在。
        """
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=False
        ), patch(
            "uniclaw.tools.shell.run_command",
            new_callable=AsyncMock,
            return_value=RunResult(0, "a.txt:1:hello\n"),
        ) as m:
            result = await Grep("hello", str(tmp_path))
        cmd = m.await_args.args[0]
        assert cmd[0] == "grep"
        assert "-r" in cmd
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_rg_fallback_no_recursive_flag(self, tmp_path):
        """rg 本身就递归,不需要也不该加 -r(rg 的 -r 是 --replace)。"""
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch(
            "uniclaw.tools.shell.run_command",
            new_callable=AsyncMock,
            return_value=RunResult(0, "a.txt:1:hello\n"),
        ) as m:
            await Grep("hello", str(tmp_path))
        cmd = m.await_args.args[0]
        assert cmd[0] == "rg"
        assert "-r" not in cmd

    @pytest.mark.asyncio
    async def test_unknown_mode(self, tmp_path):
        """未知模式。"""
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ):
            result = await Grep("hello", str(tmp_path), output_mode="bogus")
        assert "未知的 output_mode" in result

    @pytest.mark.asyncio
    async def test_case_insensitive_flag(self, tmp_path):
        """大小写不敏感标志传给 rg。"""
        proc = AsyncMock()
        proc.communicate.return_value = (b"x", b"")
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc) as m:
            await Grep("HELLO", str(tmp_path), case_insensitive=True)
        assert "-i" in m.await_args.args


class TestEverything:
    """Everything 搜索测试。"""

    @staticmethod
    def _make_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
        proc.returncode = returncode
        return proc

    @pytest.mark.asyncio
    @patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_basic(self, mock_exec):
        """基本搜索。"""
        mock_exec.return_value = self._make_proc(stdout=b"output")
        result = await search_files_with_everything("readme")
        assert result == "output"
        assert mock_exec.await_args.args == ("es", "readme")

    @pytest.mark.asyncio
    @patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_path_filter(self, mock_exec):
        """路径过滤。"""
        mock_exec.return_value = self._make_proc()
        await search_files_with_everything("config", path_filter="D:/Projects")
        assert mock_exec.await_args.args == ("es", "-path", "D:/Projects", "config")

    @pytest.mark.asyncio
    @patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_max_results(self, mock_exec):
        """限制结果数。"""
        mock_exec.return_value = self._make_proc()
        await search_files_with_everything("*.py", max_results=10)
        assert mock_exec.await_args.args == ("es", "-n", "10", "*.py")

    @pytest.mark.asyncio
    @patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_metachars_not_shell_interpreted(self, mock_exec):
        """query/path_filter 含引号与 shell 元字符时按单参数透传,不会拼进命令行。"""
        mock_exec.return_value = self._make_proc()
        evil = 'x" & del C:\\Windows\\a.txt & echo "'
        await search_files_with_everything(evil, path_filter=evil)
        assert mock_exec.await_args.args == ("es", "-path", evil, evil)

    @pytest.mark.asyncio
    @patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", side_effect=FileNotFoundError)
    async def test_check_es_not_found(self, mock_exec):
        """es 未安装。"""
        err = await _check_es()
        assert "es.exe" in err

    @pytest.mark.asyncio
    async def test_check_es_success(self):
        """es 可用。"""
        proc = AsyncMock()
        proc.communicate.return_value = (b"", b"")
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            assert await _check_es() is None

    @pytest.mark.asyncio
    async def test_check_es_stderr(self):
        """es 返回错误。"""
        proc = AsyncMock()
        proc.communicate.return_value = (b"", b"permission denied")
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            err = await _check_es()
        assert err == "permission denied"


class TestBinarySkipNote:
    """Grep 二进制跳过提示测试(任务 3)。"""

    def test_is_binary_file_detects_png(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x01target\x00\x02" + b"\x00" * 16)
        assert _is_binary_file(f) is True

    def test_is_binary_file_allows_text(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello\n", encoding="utf-8")
        assert _is_binary_file(f) is False

    def test_is_binary_file_allows_utf16(self, tmp_path):
        f = tmp_path / "u16.txt"
        f.write_text("你好\n", encoding="utf-16")
        assert _is_binary_file(f) is False

    def test_count_binary_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
        (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        (tmp_path / "b.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00target\x00" + b"\x00" * 8)
        binary, total = _count_binary_files(str(tmp_path))
        assert binary == 2
        assert total == 3

    def test_count_binary_files_missing_path(self, tmp_path):
        assert _count_binary_files(str(tmp_path / "nope")) == (0, 0)

    def test_python_grep_notes_skipped_binary(self, tmp_path):
        """二进制被跳过时必须注明,不能与"确实无匹配"混淆。"""
        (tmp_path / "a.txt").write_text("no match here\n", encoding="utf-8")
        (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00target\x00" + b"\x00" * 8)
        out = _python_grep("target", str(tmp_path))
        assert "No matches found" in out
        assert "已跳过 1 个二进制文件" in out
        assert "全部是二进制" not in out

    def test_python_grep_all_binary_special_note(self, tmp_path):
        """全部是二进制时单独说明。"""
        (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00target\x00" + b"\x00" * 8)
        (tmp_path / "b.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00target\x00" + b"\x00" * 8)
        out = _python_grep("target", str(tmp_path))
        assert "全部是二进制文件" in out
        assert "已跳过 2 个二进制文件" in out

    def test_python_grep_text_match_with_binary_note(self, tmp_path):
        """文本命中时也追加二进制跳过说明。"""
        (tmp_path / "a.txt").write_text("hello target\n", encoding="utf-8")
        (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00target\x00" + b"\x00" * 8)
        out = _python_grep("target", str(tmp_path))
        assert "a.txt" in out
        assert "已跳过 1 个二进制文件" in out

    def test_python_grep_no_binary_no_note(self, tmp_path):
        """没有二进制时不加多余说明。"""
        (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
        out = _python_grep("zzz", str(tmp_path))
        assert out == "No matches found"

    @pytest.mark.asyncio
    async def test_native_grep_notes_skipped_binary(self, tmp_path):
        """rg/grep 路径同样要注明二进制跳过。"""
        (tmp_path / "a.txt").write_text("hello target\n", encoding="utf-8")
        (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00target\x00" + b"\x00" * 8)
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch(
            "uniclaw.tools.shell.run_command",
            new_callable=AsyncMock,
            return_value=RunResult(0, "a.txt:1:hello target\n"),
        ):
            result = await Grep("target", str(tmp_path))
        assert "hello target" in result
        assert "已跳过 1 个二进制文件" in result

    @pytest.mark.asyncio
    async def test_native_grep_no_match_with_binary(self, tmp_path):
        """无文本匹配但有二进制被跳过时,不能只说 No matches found。"""
        (tmp_path / "a.txt").write_text("no match here\n", encoding="utf-8")
        (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00target\x00" + b"\x00" * 8)
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch(
            "uniclaw.tools.shell.run_command", new_callable=AsyncMock, return_value=RunResult(1, "")
        ):
            result = await Grep("target", str(tmp_path))
        assert "No matches found" in result
        assert "已跳过 1 个二进制文件" in result

    @pytest.mark.asyncio
    async def test_native_grep_binary_hit_lines_folded(self, tmp_path):
        """grep 的 "Binary file X matches" 归并进说明,不当作文本命中。"""
        (tmp_path / "a.txt").write_text("hello target\n", encoding="utf-8")
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=False
        ), patch(
            "uniclaw.tools.shell.run_command",
            new_callable=AsyncMock,
            return_value=RunResult(
                0,
                "a.txt:1:hello target\nBinary file ./img.png matches\n",
            ),
        ):
            result = await Grep("target", str(tmp_path))
        assert "hello target" in result
        assert "Binary file ./img.png matches" not in result
        assert "img.png" in result  # 归并到二进制命中说明里


class TestGetTools:
    """get_tools / get_all_tools 测试。"""

    def _reset_cache(self):
        shell_mod._tools_cache["result"] = None
        shell_mod._tools_cache["time"] = 0

    @pytest.mark.asyncio
    async def test_get_tools_with_es(self):
        """es 可用时包含搜索工具。"""
        self._reset_cache()
        with patch("uniclaw.tools.shell._check_es", new_callable=AsyncMock, return_value=None):
            tools = await get_tools()
        assert len(tools) == 3
        assert any(t.name == "search_files_with_everything" for t in tools)

    @pytest.mark.asyncio
    async def test_get_tools_without_es(self):
        """es 不可用时禁用搜索工具并记录原因。"""
        self._reset_cache()
        mock_config = MagicMock()
        with patch("uniclaw.tools.shell._check_es", new_callable=AsyncMock, return_value="not found"):
            tools = await get_tools(config=mock_config)
        assert len(tools) == 2
        mock_config.record_unavailable_tools.assert_called_once()
        args = mock_config.record_unavailable_tools.call_args.args
        assert args[0] == [search_files_with_everything.name]
        assert "not found" in args[1]

    @pytest.mark.asyncio
    async def test_get_tools_with_es_clears_unavailable(self):
        """es 恢复可用时清除不可用记录。"""
        self._reset_cache()
        mock_config = MagicMock()
        with patch("uniclaw.tools.shell._check_es", new_callable=AsyncMock, return_value=None):
            tools = await get_tools(config=mock_config)
        assert len(tools) == 3
        mock_config.clear_unavailable_tools.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_tools_cache(self):
        """缓存生效。"""
        cached = [MagicMock()]
        shell_mod._tools_cache["result"] = cached
        shell_mod._tools_cache["time"] = asyncio.get_event_loop().time() if False else 0
        import time as _t

        shell_mod._tools_cache["time"] = _t.monotonic()
        tools = await get_tools()
        assert tools is cached
        self._reset_cache()

    def test_get_all_tools(self):
        """无条件返回全部。"""
        tools = get_all_tools()
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert names == {"Bash", "Grep", "search_files_with_everything"}


class TestFindGitBash:
    """_find_git_bash 测试。"""

    @patch("uniclaw.tools.shell.sys.platform", "linux")
    def test_non_windows(self):
        """非 Windows 返回 None。"""
        assert _find_git_bash() is None
