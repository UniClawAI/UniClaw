"""进程执行工具测试 — 覆盖 run_command / run_command_sync 的共同语义。

两者参数与返回值一致,测试按同一组行为断言,只有调用方式(async / sync)不同。
"""

import os
import sys

import pytest

from uniclaw.utils.process import RunResult, run_command, run_command_sync, smart_decode


def _echo(stdout: str = "", stderr: str = "", code: int = 0) -> list[str]:
    """构造跨平台可用的命令:写 stdout/stderr 并以指定码退出。"""
    script = (
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"raise SystemExit({code})\n"
    )
    return [sys.executable, "-c", script]


class TestRunCommandAsync:
    """run_command 异步版。"""

    async def test_list_cmd_runs_directly(self):
        """list 参数直接执行,返回退出码与输出。"""
        r = await run_command(_echo("hi", "warn", 3))
        assert isinstance(r, RunResult)
        assert r.code == 3
        assert r.stdout == "hi"
        assert r.stderr == "warn"
        assert r.ok is False

    async def test_str_cmd_via_shell(self):
        """str 参数经系统 shell 执行。"""
        r = await run_command(f'"{sys.executable}" -c "import sys; sys.stdout.write(\'sh\')"')
        assert r.code == 0
        assert "sh" in r.stdout

    async def test_ok_property(self):
        """退出码 0 时 ok 为真。"""
        r = await run_command(_echo("x"))
        assert r.ok is True
        assert bool(r) is True

    async def test_timeout_returns_timed_out(self):
        """超时返回 timed_out=True 且退出码为 -1,不抛异常。"""
        r = await run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.3
        )
        assert r.timed_out is True
        assert r.code == -1
        assert r.ok is False

    async def test_decode_callback_overrides_encoding(self):
        """decode 回调给定时优先于 encoding/errors。"""
        # 输出 GBK 编码的中文,用 smart_decode 应能还原
        script = (
            "import sys\n"
            "sys.stdout.buffer.write('中文'.encode('gbk'))\n"
        )
        r = await run_command([sys.executable, "-c", script], decode=smart_decode)
        assert r.stdout == "中文"

    async def test_default_utf8_replace(self):
        """未给 decode 时按 encoding/errors 解码(非法字节变替换符)。"""
        script = "import sys; sys.stdout.buffer.write(b'\\xff\\xfe')"
        r = await run_command([sys.executable, "-c", script])
        assert "�" in r.stdout

    async def test_empty_cmd_raises(self):
        """空命令抛 ValueError。"""
        with pytest.raises(ValueError):
            await run_command([])

    async def test_input_written_to_stdin(self):
        """input 写入子进程 stdin 并被原样回显。"""
        r = await run_command(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            input="echoed",
        )
        assert r.stdout == "echoed"

    async def test_env_replace_replaces_whole_env(self, monkeypatch):
        """env_replace 整份替换: 过滤掉的变量不会从当前环境泄漏给子进程。"""
        monkeypatch.setenv("UNICLAW_TEST_LEAK", "secret")
        base = {k: v for k, v in os.environ.items() if k != "UNICLAW_TEST_LEAK"}
        base["UNICLAW_TEST_KEEP"] = "ok"
        script = (
            "import os, sys; "
            "sys.stdout.write(os.environ.get('UNICLAW_TEST_LEAK', 'absent')); "
            "sys.stdout.write(' '); "
            "sys.stdout.write(os.environ.get('UNICLAW_TEST_KEEP', 'absent'))"
        )
        r = await run_command([sys.executable, "-c", script], env_replace=base)
        assert r.stdout == "absent ok"

    async def test_env_and_env_replace_mutex(self):
        """env 与 env_replace 同时给定抛 ValueError。"""
        with pytest.raises(ValueError):
            await run_command(
                [sys.executable, "-c", ""], env={"A": "1"}, env_replace={"B": "2"}
            )


class TestRunCommandSync:
    """run_command_sync 同步版 —— 行为应与异步版一致。"""

    def test_list_cmd_runs_directly(self):
        """list 参数直接执行,返回退出码与输出。"""
        r = run_command_sync(_echo("hi", "warn", 3))
        assert isinstance(r, RunResult)
        assert r.code == 3
        assert r.stdout == "hi"
        assert r.stderr == "warn"
        assert r.ok is False

    def test_str_cmd_via_shell(self):
        """str 参数经系统 shell 执行。"""
        r = run_command_sync(f'"{sys.executable}" -c "import sys; sys.stdout.write(\'sh\')"')
        assert r.code == 0
        assert "sh" in r.stdout

    def test_ok_property(self):
        """退出码 0 时 ok 为真。"""
        r = run_command_sync(_echo("x"))
        assert r.ok is True

    def test_timeout_returns_timed_out(self):
        """超时返回 timed_out=True 且退出码为 -1,不抛异常。"""
        r = run_command_sync([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.3)
        assert r.timed_out is True
        assert r.code == -1
        assert r.ok is False

    def test_decode_callback_overrides_encoding(self):
        """decode 回调给定时优先于 encoding/errors。"""
        script = (
            "import sys\n"
            "sys.stdout.buffer.write('中文'.encode('gbk'))\n"
        )
        r = run_command_sync([sys.executable, "-c", script], decode=smart_decode)
        assert r.stdout == "中文"

    def test_empty_cmd_raises(self):
        """空命令抛 ValueError。"""
        with pytest.raises(ValueError):
            run_command_sync("")

    def test_input_written_to_stdin(self):
        """input 写入子进程 stdin 并被原样回显。"""
        r = run_command_sync(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            input="echoed",
        )
        assert r.stdout == "echoed"

    def test_env_replace_replaces_whole_env(self, monkeypatch):
        """env_replace 整份替换(同步版语义与异步版一致)。"""
        monkeypatch.setenv("UNICLAW_TEST_LEAK", "secret")
        base = {k: v for k, v in os.environ.items() if k != "UNICLAW_TEST_LEAK"}
        base["UNICLAW_TEST_KEEP"] = "ok"
        script = (
            "import os, sys; "
            "sys.stdout.write(os.environ.get('UNICLAW_TEST_LEAK', 'absent')); "
            "sys.stdout.write(' '); "
            "sys.stdout.write(os.environ.get('UNICLAW_TEST_KEEP', 'absent'))"
        )
        r = run_command_sync([sys.executable, "-c", script], env_replace=base)
        assert r.stdout == "absent ok"


class TestSmartDecode:
    """smart_decode —— UTF-8 → GBK → GB18030 回退。"""

    def test_empty(self):
        """空输入返回空串。"""
        assert smart_decode(b"") == ""
        assert smart_decode(None) == ""

    def test_utf8(self):
        """UTF-8 优先。"""
        assert smart_decode("你好".encode("utf-8")) == "你好"

    def test_gbk_fallback_on_windows(self):
        """Windows 上非 UTF-8 时回退 GBK。"""
        if sys.platform != "win32":
            pytest.skip("GBK 回退仅在 Windows 启用")
        assert smart_decode("你好".encode("gbk")) == "你好"

    def test_gb18030_fallback_on_windows(self):
        """Windows 上再回退 GB18030。"""
        if sys.platform != "win32":
            pytest.skip("GB18030 回退仅在 Windows 启用")
        assert smart_decode("你好".encode("gb18030")) == "你好"

    def test_undecodable_uses_replace(self):
        """全编码失败时用 replace,不抛异常。"""
        result = smart_decode(b"\xff\xfe\xff")
        assert "�" in result
