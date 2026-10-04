"""进程执行工具 — os.system 的 asyncio 异步版本。

统一入口 ``run_command()``:按参数类型自动选择执行方式——
- ``list`` → 直接执行(create_subprocess_exec,不经 shell,安全)
- ``str`` → 经系统 shell(create_subprocess_shell,与 os.system 同语义,
  支持管道/重定向/环境变量展开,仅执行可信来源)
统一返回 RunResult(退出码 + stdout + stderr + 超时标记)。
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from typing import Optional


@dataclasses.dataclass
class RunResult:
    """命令执行结果。

    Attributes:
        code: 退出码(超时终止时为 -1)。
        stdout: 标准输出(已按 encoding 解码)。
        stderr: 标准错误(已按 encoding 解码)。
        timed_out: 是否因超时被终止。
    """

    code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """是否成功(未超时且退出码为 0)。"""
        return not self.timed_out and self.code == 0

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        parts = [f"exit={self.code}"]
        if self.timed_out:
            parts.append("timed_out")
        if self.stdout:
            parts.append(f"stdout={self.stdout[:100]!r}")
        if self.stderr:
            parts.append(f"stderr={self.stderr[:100]!r}")
        return f"RunResult({', '.join(parts)})"


def _decode(data: bytes | None, encoding: str, errors: str) -> str:
    if not data:
        return ""
    return data.decode(encoding, errors)


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """终止整个进程树,避免孙进程残留(超时回收用)。

    Windows: taskkill /T 递归杀;POSIX: killpg 进程组(子进程需以独立
    进程组启动,见 run_command 的 start_new_session)。
    """
    if os.name == "nt":
        import subprocess

        try:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            _safe_kill(proc)
    else:
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            _safe_kill(proc)


def _safe_kill(proc: asyncio.subprocess.Process) -> None:
    """kill 已退出进程会抛 ProcessLookupError,统一吞掉。"""
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


async def run_command(
    cmd: str | list[str],
    *,
    cwd: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    input: str | bytes | None = None,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> RunResult:
    """执行命令(os.system 的异步版),按 cmd 类型自动选择执行方式。

    Args:
        cmd:
            - list(如 ["pyright-langserver", "--stdio"]):直接执行,
              不经 shell,无命令注入风险;但不能用管道/重定向/通配符。
              Windows 上 .cmd/.bat 包装器需给出可被 CreateProcess 定位的完整路径。
            - str(如 "echo hi && ls"):经系统 shell 执行(Windows→cmd.exe,
              POSIX→/bin/sh),支持管道/重定向/环境变量展开;仅执行可信来源。
        cwd: 工作目录。
        env: 附加环境变量(合并到当前环境)。
        timeout: 超时秒数;超时后终止进程树,返回 timed_out=True。
        input: 写入子进程 stdin 的内容(字符串或字节)。
        encoding / errors: stdout/stderr 解码参数。

    Returns:
        RunResult。

    Raises:
        ValueError: cmd 为空。
    """
    if not cmd or (isinstance(cmd, (list, tuple)) and not cmd):
        raise ValueError("命令不能为空")
    # input 编码前置:避免启动子进程后才编码失败导致进程泄漏
    data = input.encode(encoding) if isinstance(input, str) else input
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    stdin = asyncio.subprocess.PIPE if input is not None else None
    if isinstance(cmd, (list, tuple)):
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=full_env,
            # POSIX:独立进程组,超时 killpg 不会误杀父进程
            **({"start_new_session": True} if os.name != "nt" else {}),
        )
    else:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=full_env,
            **({"start_new_session": True} if os.name != "nt" else {}),
        )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(data), timeout)
    except asyncio.TimeoutError:
        # 超时:终止进程树(含孙进程);不做二次 communicate
        # (管道可能已挂起,二次通信反而阻塞)
        await _kill_tree(proc)
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except Exception:
            pass
        return RunResult(-1, timed_out=True)
    return RunResult(
        proc.returncode if proc.returncode is not None else -1,
        _decode(out_b, encoding, errors),
        _decode(err_b, encoding, errors),
    )
