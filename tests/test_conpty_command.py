"""ConPTY 命令拆分策略测试 — 覆盖 visible=True 可视模式的引号/路径兼容性。

对应 UNICLAW-BUG-20260830-03: visible=True 下 bash -c "echo quoted-test"
报 command not found, 根因是 pywinpty PtyProcess.spawn 对字符串入参内部
shlex.split(posix=False) 保留引号字面字符。_split_conpty_command 按 shell
家族分流: POSIX shell 用 posix=True 剥离引号, Windows shell 保留字符串以
保护反斜杠路径。
"""

import pytest

from uniclaw.tools.monitor.pty_session import _split_conpty_command


class TestPosixShellSplit:
    """POSIX shell 家族: 用 posix=True 拆分并剥离引号标记。"""

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            # 含引号的复合命令 (bug 原始复现场景)
            ('bash -c "echo quoted-test"', ["bash", "-c", "echo quoted-test"]),
            ('sh -c "echo quoted-test"', ["sh", "-c", "echo quoted-test"]),
            ('zsh -c "echo quoted-test"', ["zsh", "-c", "echo quoted-test"]),
            # 含空格的参数
            ('bash -c "echo a b c"', ["bash", "-c", "echo a b c"]),
            # 带 .exe 后缀 / 完整路径的 shell
            (
                '"C:\\Program Files\\Git\\bin\\bash.exe" -c "echo hi"',
                ["C:\\Program Files\\Git\\bin\\bash.exe", "-c", "echo hi"],
            ),
            ('bash.exe -c "echo hi"', ["bash.exe", "-c", "echo hi"]),
            # wsl 直接透传命令
            ("wsl echo hi", ["wsl", "echo", "hi"]),
        ],
    )
    def test_posix_split_strips_quotes(self, command, expected):
        assert _split_conpty_command(command) == expected


class TestWindowsShellPassthrough:
    """Windows shell 家族: 保留原字符串, 避免 posix=True 破坏反斜杠路径。"""

    @pytest.mark.parametrize(
        "command",
        [
            # cmd 读 Windows 路径: \W / \w 不能被当转义序列吃掉
            r'cmd /c type C:\Windows\win.ini',
            r'cmd /c type C:\Windows\System32\win.ini',
            "cmd /c echo hello",
            'cmd /c "echo hello"',
            # powershell 引号命令
            'powershell -NoProfile -Command "Write-Host hi"',
            'pwsh -NoProfile -Command "Write-Host hi"',
            # 非 shell 的普通命令保持字符串原样
            "echo plain",
            "ls -la /tmp",
        ],
    )
    def test_windows_shell_keeps_raw_string(self, command):
        assert _split_conpty_command(command) == command


class TestPipelineConsistency:
    """可视模式(ConPTY)与管道模式使用同一套命令字符串解析规则。

    管道模式走 create_subprocess_shell (cmd.exe 解析); 可视模式拆分后,
    bash 家族得到 argv 应等价于 cmd.exe 解析出的参数, 保证两模式行为一致。
    """

    def test_bash_quoted_argv_matches_pipe_mode(self):
        # cmd.exe 解析 bash -c "echo quoted-test" 后传给 bash 的 argv
        argv = _split_conpty_command('bash -c "echo quoted-test"')
        assert argv == ["bash", "-c", "echo quoted-test"]
        # 引号被正确剥离, 不再保留字面引号 (旧行为: 保留 "echo quoted-test")
        assert '"' not in argv[2]

    def test_bash_spaced_arg_survives(self):
        argv = _split_conpty_command('bash -c "echo a b c"')
        assert argv == ["bash", "-c", "echo a b c"]

    def test_empty_and_whitespace(self):
        # 空命令/纯空白: 不应崩溃, 返回原字符串
        assert _split_conpty_command("") == ""
        assert _split_conpty_command("   ") == "   "
