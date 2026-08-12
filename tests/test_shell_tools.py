"""Shell 工具测试 — 覆盖 Bash、Grep、Everything 搜索和辅助函数。"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from uniclaw.tools import shell as shell_mod
from uniclaw.tools.shell import (
    Grep,
    GrepOutputMode,
    _check_es,
    _find_git_bash,
    _python_grep,
    fix_bash_nul_redirect,
    get_all_tools,
    get_tools,
    search_files_with_everything,
    smart_decode,
)
from uniclaw.utils.constants import TOOL_ERROR


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
            result = await Bash("echo hi", timeout=0, config=self._config())
        assert "大于 0" in result
        mock_exec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_negative_rejected(self):
        """负数超时被拒绝。"""
        from uniclaw.tools.shell import Bash

        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell") as mock_exec:
            result = await Bash("echo hi", timeout=-1, config=self._config())
        assert "大于 0" in result
        mock_exec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_output(self):
        """无输出返回占位。"""
        from uniclaw.tools.shell import Bash

        proc = self._proc()
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc):
            result = await Bash("true", config=self._config())
        assert result == "(没有输出)"

    @pytest.mark.asyncio
    async def test_with_output(self):
        """有输出返回内容。"""
        from uniclaw.tools.shell import Bash

        out_stream = AsyncMock()
        out_stream.read = AsyncMock(side_effect=[b"hello\n", b""])
        proc = self._proc(stdout=out_stream)
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc), patch(
            "uniclaw.tools.shell.tool_stream", new_callable=AsyncMock
        ):
            result = await Bash("echo hello", config=self._config())
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
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc), patch(
            "uniclaw.tools.shell.tool_stream", new_callable=AsyncMock
        ):
            result = await Bash("echo out; echo err >&2", config=self._config())
        # stdout 的末尾换行保留,再拼接 [stderr]
        assert result == "out\n\n[stderr]err"

    @pytest.mark.asyncio
    async def test_cancelled(self):
        """用户取消返回中断消息。"""
        from uniclaw.tools.shell import Bash

        cancel_event = MagicMock()
        cancel_event.is_set.return_value = True
        proc = self._proc(returncode=None)
        with patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=proc), patch(
            "uniclaw.tools.shell._kill_proc_tree", new_callable=AsyncMock
        ):
            result = await Bash("sleep 100", config=self._config(cancel_event))
        assert "用户中断" in result

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
            result = await Bash("sleep 100", timeout=0.2, config=self._config())
        assert "超时" in result
        assert "abc12345" in result
        assert "监控" in result

    @pytest.mark.asyncio
    async def test_exception(self):
        """创建进程异常直接抛出(create_subprocess_shell 在 try 外)。"""
        from uniclaw.tools.shell import Bash

        with patch(
            "uniclaw.tools.shell.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            side_effect=OSError("boom"),
        ):
            with pytest.raises(OSError):
                await Bash("echo hi", config=self._config())


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
        proc = AsyncMock()
        proc.communicate.return_value = (b"a.txt:1:hello\n", b"")
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc) as m:
            result = await Grep("hello", str(tmp_path))
        assert "hello" in result
        assert m.await_args.args[0] == "rg"

    @pytest.mark.asyncio
    async def test_uses_grep(self, tmp_path):
        """使用原生 grep。"""
        proc = AsyncMock()
        proc.communicate.return_value = (b"a.txt:1:hello\n", b"")
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=False
        ), patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc) as m:
            result = await Grep("hello", str(tmp_path))
        assert "hello" in result
        assert m.await_args.args[0] == "grep"

    @pytest.mark.asyncio
    async def test_no_match(self, tmp_path):
        """无匹配返回提示。"""
        proc = AsyncMock()
        proc.communicate.return_value = (b"", b"")
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            result = await Grep("hello", str(tmp_path))
        assert result == "No matches found"

    @pytest.mark.asyncio
    async def test_timeout(self, tmp_path):
        """搜索超时。"""
        proc = AsyncMock()
        # wait_for 被 mock 抛异常前,Python 已先执行 proc.communicate() 创建
        # coroutine,该 coroutine 永远不会被 await,产生 "never awaited" 警告。
        # 用同步 MagicMock 返回普通元组,避免创建未 await 的 coroutine。
        proc.communicate = MagicMock(return_value=(b"", b""))
        with patch("uniclaw.tools.shell._has_native_grep", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.tools.shell._has_rg", new_callable=AsyncMock, return_value=True
        ), patch("uniclaw.tools.shell.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc), patch(
            "uniclaw.tools.shell.asyncio.wait_for", side_effect=asyncio.TimeoutError
        ):
            result = await Grep("hello", str(tmp_path))
        assert "搜索超时" in result

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
    @patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock)
    async def test_basic(self, mock_shell):
        """基本搜索。"""
        mock_shell.return_value = self._make_proc(stdout=b"output")
        result = await search_files_with_everything("readme")
        assert result == "output"
        assert mock_shell.await_args.args[0] == 'es "readme"'

    @pytest.mark.asyncio
    @patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock)
    async def test_path_filter(self, mock_shell):
        """路径过滤。"""
        mock_shell.return_value = self._make_proc()
        await search_files_with_everything("config", path_filter="D:/Projects")
        assert mock_shell.await_args.args[0] == 'es -p "D:/Projects" "config"'

    @pytest.mark.asyncio
    @patch("uniclaw.tools.shell.asyncio.create_subprocess_shell", new_callable=AsyncMock)
    async def test_max_results(self, mock_shell):
        """限制结果数。"""
        mock_shell.return_value = self._make_proc()
        await search_files_with_everything("*.py", max_results=10)
        assert mock_shell.await_args.args[0] == 'es -n 10 "*.py"'

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
    @patch("uniclaw.console.ui.warn", new_callable=AsyncMock)
    async def test_get_tools_without_es(self, mock_warn):
        """es 不可用时禁用搜索工具。"""
        self._reset_cache()
        with patch("uniclaw.tools.shell._check_es", new_callable=AsyncMock, return_value="not found"):
            tools = await get_tools()
        assert len(tools) == 2
        mock_warn.assert_awaited()

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
