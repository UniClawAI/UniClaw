"""旧进程侧的重启执行:服务关闭后拉起新进程并硬退出自身。

serve() 返回后由 webui/launcher.py 调用 relaunch_if_pending():
读取重启标志,以相同启动参数构造命令行,通过 Win32 CreateProcessW
创建完全脱离的新进程,随后 os._exit 立即退出。
"""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

from uniclaw.utils.logger import get_logger

# 进程级基础设施,无 session/config 上下文:日志落用户级 logs/
logger = get_logger("restart", None)


def _clean_relaunch_env() -> dict:
    """构造重启新进程使用的干净环境变量。

    调试器(如 VS Code debugpy)会向当前进程注入 pydevd 相关环境变量,
    子进程继承后会尝试回连调试器,调试器不在时直接崩溃。
    重启出的新进程应脱离调试器干净运行,这里剥离相关条目。
    """
    env = dict(os.environ)
    for key in list(env):
        if key.upper().startswith(("PYDEVD_", "DEBUGPY_")):
            del env[key]
    pp = env.get("PYTHONPATH")
    if pp:
        kept = [
            p
            for p in pp.split(os.pathsep)
            if p and "pydevd" not in p.lower() and "debugpy" not in p.lower()
        ]
        if kept:
            env["PYTHONPATH"] = os.pathsep.join(kept)
        else:
            del env["PYTHONPATH"]
    return env


def _spawn_detached_win32(cmd: list[str], cwd: str, env: dict) -> None:
    """在 Windows 上以原生 CreateProcessW 启动带独立控制台的新进程。

    新进程获得自己的控制台窗口(CREATE_NEW_CONSOLE),stdout/stderr 直接
    打到该窗口,像手动启动一样可见运行日志;与旧进程的控制台互不相干。

    不经过 subprocess/asyncio 的高层封装:调试器(pydevd)会在这些层打
    猴子补丁强制把新启动的 Python 子进程纳入调试,回连调试器失败时
    新进程直接崩溃。ctypes 直调 C 层 API 不受任何 Python 层补丁影响。

    Args:
        cmd: 命令及参数列表。
        cwd: 新进程工作目录。
        env: 新进程环境变量。
    """
    import ctypes
    from ctypes import wintypes

    # creationflags 常量(subprocess 模块内部值一致)
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NEW_CONSOLE = 0x00000010
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    ERROR_ACCESS_DENIED = 5
    WAIT_OBJECT_0 = 0

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    cmdline = subprocess.list2cmdline(cmd)
    # env block: "k=v\0" 序列双 \0 结尾(UTF-16 小端字节串,保内嵌 \0 不截断)
    env_block = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0"
    env_bytes = (env_block + "\0").encode("utf-16-le")
    env_buf = ctypes.create_string_buffer(env_bytes, len(env_bytes))

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    pi = PROCESS_INFORMATION()

    try:
        flags = (
            CREATE_NEW_PROCESS_GROUP | CREATE_NEW_CONSOLE | CREATE_UNICODE_ENVIRONMENT
        )
        # 优先带 CREATE_BREAKAWAY_FROM_JOB:调试器/IDE(VS Code 等)会把
        # 被调试进程放进 Job Object,新进程默认被拉入同一 Job,旧进程退出
        # 触发 Job 清理时会连带杀死刚创建的新进程;breakaway 使其脱离。
        # Job 不允许 breakaway 时返回 ACCESS_DENIED,降级为不带该标志重试。
        ok = ctypes.windll.kernel32.CreateProcessW(
            None,
            cmdline,
            None,
            None,
            False,
            flags | CREATE_BREAKAWAY_FROM_JOB,
            env_buf,
            cwd,
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if not ok and ctypes.GetLastError() == ERROR_ACCESS_DENIED:
            ok = ctypes.windll.kernel32.CreateProcessW(
                None,
                cmdline,
                None,
                None,
                False,
                flags,
                env_buf,
                cwd,
                ctypes.byref(si),
                ctypes.byref(pi),
            )
        if not ok:
            raise OSError(f"CreateProcessW failed: {ctypes.GetLastError()}")

        # 短暂观察新进程:若立即退出则拿到退出码抛错(调用方保持旧进程存活),
        # 避免"新进程无声消失"这种无从排查的失败
        event = ctypes.windll.kernel32.WaitForSingleObject(pi.hProcess, 2000)
        if event == WAIT_OBJECT_0:
            code = wintypes.DWORD()
            if ctypes.windll.kernel32.GetExitCodeProcess(
                pi.hProcess, ctypes.byref(code)
            ):
                raise ChildProcessError(
                    f"重启新进程(pid={pi.dwProcessId})启动后立即退出,"
                    f"exit_code={code.value:#x}(输出见新控制台窗口,"
                    f"日志见用户级 ~/.UniClaw/logs/)"
                )
            raise ChildProcessError(
                f"重启新进程(pid={pi.dwProcessId})启动后立即退出,退出码获取失败"
            )
    finally:
        ctypes.windll.kernel32.CloseHandle(pi.hProcess)
        ctypes.windll.kernel32.CloseHandle(pi.hThread)


def build_relaunch_command(kwargs: dict) -> list[str]:
    """构造重启新进程的命令行(尽量复用原始入口方式)。

    注意:`uv run uniclaw` 等入口下 sys.argv[0] 是 trampoline 的虚拟路径
    (形如 "<...>\\uviclaw.exe\\__main__.py"),不能直接当作脚本路径使用。
    """
    import importlib.util

    args = [
        "--mode",
        "webui",
        "--host",
        kwargs.get("host", "127.0.0.1"),
        "--port",
        str(kwargs.get("port", 8080)),
    ]
    if not kwargs.get("ssl", False):
        args.append("--no-ssl")
    domain = kwargs.get("domain", "")
    if domain:
        args += ["--domain", domain]

    # 1. 包提供 __main__ 时 python -m 最可靠(不依赖 argv 形态)
    try:
        if importlib.util.find_spec("uniclaw.__main__") is not None:
            return [sys.executable, "-m", "uniclaw", *args]
    except (ImportError, ValueError):
        pass

    argv0 = (sys.argv[0] or "").replace("/", os.sep)

    # 2. argv0 是真实存在的 .py 脚本(如 `python src/uniclaw/main.py`、调试器启动):
    #    直接以绝对路径重跑同一脚本。trampoline 的虚拟路径不存在于磁盘,不会误入此分支
    if argv0.lower().endswith(".py"):
        script = Path(argv0)
        if not script.is_absolute():
            script = Path.cwd() / script
        if script.exists():
            return [sys.executable, str(script), *args]

    # 3. entry point 可执行文件:argv0 可能是 "<...>uniclaw.exe" 或
    #    trampoline 虚拟路径 "<...>uniclaw.exe\\__main__.py",取 exe 前缀
    exe = Path(argv0)
    if os.name == "nt":
        m = re.match(r"^(.+\.exe)[\\/]", argv0, re.IGNORECASE)
        if m:
            exe = Path(m.group(1))
    # is_file() 排除 argv0 为空时 Path(".") 命中 cwd 目录的情况
    if str(exe) and exe.is_file() and exe.suffix.lower() != ".py":
        return [str(exe), *args]

    # 4. 兜底:解释器同级目录下的入口脚本(venv 的 Scripts/ 或 bin/)
    ext = ".exe" if os.name == "nt" else ""
    sibling = Path(sys.executable).with_name(f"uniclaw{ext}")
    if sibling.exists():
        return [str(sibling), *args]

    raise RuntimeError("未找到 uniclaw 入口,无法构造重启命令")


async def relaunch_if_pending(launch_kwargs: dict) -> None:
    """服务关闭后检查重启标志,存在则以相同参数拉起新进程。

    成功拉起后不删除标志 — 由新进程启动时读取并清除(resumer)。
    """
    from uniclaw.utils.restart_flag import clear_pending_restart, read_pending_restart

    pending = read_pending_restart()
    if not pending:
        return
    try:
        cmd = build_relaunch_command(launch_kwargs)
    except Exception as e:
        clear_pending_restart()
        logger.exception(f"[restart] 构造重启命令失败: {e}")
        return
    logger.info(f"[restart] 拉起新进程: {subprocess.list2cmdline(cmd)}")
    try:
        if sys.platform == "win32":
            # Windows:直调 CreateProcessW。调试器(pydevd)会在 subprocess/
            # asyncio 的 Python 层打补丁把新启动的 Python 子进程纳入调试,
            # 回连调试器失败时新进程直接崩溃;C 层调用不受补丁影响。
            # 新进程带独立控制台窗口,输出直接可见
            _spawn_detached_win32(cmd, os.getcwd(), _clean_relaunch_env())
        else:
            # POSIX:无此补丁问题,走标准异步封装(新会话脱离当前进程)
            await asyncio.create_subprocess_exec(
                *cmd,
                cwd=os.getcwd(),
                env=_clean_relaunch_env(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception as e:
        clear_pending_restart()  # 拉起失败清掉标志,避免残留
        logger.exception(f"[restart] 拉起新进程失败: {e}")
        print(f"  [restart] 重启失败: {e}")
        return
    print("\n  [restart] 正在重启 UniClaw WebUI...")
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    # 新进程已拉起,立即硬退出:绕过 asyncio.run 收尾阶段
    # (个别不肯取消的后台任务可能把正常退出无限期挂住)
    os._exit(0)
