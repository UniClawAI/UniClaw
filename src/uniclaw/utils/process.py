"""进程执行工具 — os.system 的异步/同步统一版本。

统一入口:
- ``run_command()`` — 异步版,供 async 代码 await
- ``run_command_sync()`` — 同步版,供无法 await 的同步代码调用

两者按参数类型自动选择执行方式,语义一致:
- ``list`` → 直接执行(create_subprocess_exec,不经 shell,安全)
- ``str`` → 经系统 shell(create_subprocess_shell,与 os.system 同语义,
  支持管道/重定向/环境变量展开,仅执行可信来源)
统一返回 RunResult(退出码 + stdout + stderr + 超时标记)。

超时均按进程树回收(Windows taskkill /T,POSIX killpg),避免孙进程残留。
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
import sys
from typing import Callable, Optional


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


def smart_decode(data: bytes | None) -> str:
    """按 UTF-8 → GBK → GB18030 顺序回退解码,最后退化为 UTF-8 + replace。

    Windows 上子进程输出常用系统 ANSI 码页(中文环境即 GBK),固定用 UTF-8
    解码会把中文变成 U+FFFD。需要这种容错时把本函数作为 ``decode`` 传给
    run_command / run_command_sync。

    Args:
        data: 子进程的原始字节输出。

    Returns:
        str: 解码后的文本;入参为空时返回空串。
    """
    if not data:
        return ""

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    if sys.platform == "win32":
        for enc in ("gbk", "gb18030"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue

    return data.decode("utf-8", errors="replace")


def _decode(
    data: bytes | None,
    encoding: str,
    errors: str,
    decode: Optional[Callable[[bytes | None], str]] = None,
) -> str:
    """按调用方约定解码子进程输出。

    ``decode`` 给定时优先使用(可传 smart_decode 做多编码回退),
    否则按 encoding/errors 解码。
    """
    if not data:
        return ""
    if decode is not None:
        return decode(data)
    return data.decode(encoding, errors)


def _build_env(
    env: dict[str, str] | None,
    env_replace: dict[str, str] | None,
) -> dict[str, str] | None:
    """构建子进程环境变量。

    ``env`` 叠加到当前环境;``env_replace`` 整份替换当前环境(两者互斥)。
    需要过滤敏感变量等自定义环境时用 ``env_replace`` —— 叠加语义会把
    已过滤的变量从当前环境重新带回。
    """
    if env_replace is not None:
        if env:
            raise ValueError("env 与 env_replace 互斥")
        return dict(env_replace)
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
        return full_env
    return None


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """终止整个进程树,避免孙进程残留(超时回收用)。

    Windows: taskkill /T 递归杀;POSIX: killpg 进程组(子进程需以独立
    进程组启动,见 run_command 的 start_new_session)。
    """
    if os.name == "nt":
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


def _kill_tree_sync(proc: subprocess.Popen) -> None:
    """同步版 _kill_tree —— 终止整个进程树。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            _safe_kill_sync(proc)
    else:
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            _safe_kill_sync(proc)


def _safe_kill(proc: asyncio.subprocess.Process) -> None:
    """kill 已退出进程会抛 ProcessLookupError,统一吞掉。"""
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


def _safe_kill_sync(proc: subprocess.Popen) -> None:
    """同步版 _safe_kill。"""
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


async def run_command(
    cmd: str | list[str],
    *,
    cwd: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
    env_replace: dict[str, str] | None = None,
    timeout: float | None = None,
    input: str | bytes | None = None,
    encoding: str = "utf-8",
    errors: str = "replace",
    decode: Optional[Callable[[bytes | None], str]] = None,
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
        env_replace: 完整环境变量,整份替换当前环境(与 env 互斥)。
            需要过滤敏感变量等自定义环境时使用。
        timeout: 超时秒数;超时后终止进程树,返回 timed_out=True。
        input: 写入子进程 stdin 的内容(字符串或字节)。
        encoding / errors: stdout/stderr 解码参数(被 decode 覆盖时忽略)。
        decode: 自定义解码函数,给定时优先于 encoding/errors。
            需要 UTF-8/GBK/GB18030 回退容错时传 smart_decode。

    Returns:
        RunResult。

    Raises:
        ValueError: cmd 为空,或 env 与 env_replace 同时给定。
    """
    if not cmd or (isinstance(cmd, (list, tuple)) and not cmd):
        raise ValueError("命令不能为空")
    # input 编码与环境构建前置:避免启动子进程后才失败导致进程泄漏
    data = input.encode(encoding) if isinstance(input, str) else input
    full_env = _build_env(env, env_replace)
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
        _decode(out_b, encoding, errors, decode),
        _decode(err_b, encoding, errors, decode),
    )


def run_command_sync(
    cmd: str | list[str],
    *,
    cwd: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
    env_replace: dict[str, str] | None = None,
    timeout: float | None = None,
    input: str | bytes | None = None,
    encoding: str = "utf-8",
    errors: str = "replace",
    decode: Optional[Callable[[bytes | None], str]] = None,
) -> RunResult:
    """run_command 的同步版 —— 供无法 await 的同步代码调用。

    参数与返回值同 run_command;超时同样按进程树回收。
    仅当调用方确实不能 await 时才用它,否则请用 run_command
    (同步版会阻塞当前线程)。

    Args:
        cmd: 同 run_command;list 直接执行,str 经系统 shell。
        cwd: 工作目录。
        env: 附加环境变量(合并到当前环境)。
        env_replace: 完整环境变量,整份替换当前环境(与 env 互斥)。
        timeout: 超时秒数;超时后终止进程树,返回 timed_out=True。
        input: 写入子进程 stdin 的内容(字符串或字节)。
        encoding / errors: stdout/stderr 解码参数(被 decode 覆盖时忽略)。
        decode: 自定义解码函数,给定时优先于 encoding/errors。

    Returns:
        RunResult。

    Raises:
        ValueError: cmd 为空,或 env 与 env_replace 同时给定。
    """
    if not cmd or (isinstance(cmd, (list, tuple)) and not cmd):
        raise ValueError("命令不能为空")
    data = input.encode(encoding) if isinstance(input, str) else input
    full_env = _build_env(env, env_replace)
    proc = subprocess.Popen(
        cmd,
        shell=isinstance(cmd, str),
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=full_env,
        # POSIX:独立进程组,超时 killpg 不会误杀父进程
        **({"start_new_session": True} if os.name != "nt" else {}),
    )
    try:
        out_b, err_b = proc.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree_sync(proc)
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        return RunResult(-1, timed_out=True)
    return RunResult(
        proc.returncode if proc.returncode is not None else -1,
        _decode(out_b, encoding, errors, decode),
        _decode(err_b, encoding, errors, decode),
    )
