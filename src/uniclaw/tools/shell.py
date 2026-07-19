import asyncio
import fnmatch
import os
import re
import sys
import time
from pathlib import Path
from uniclaw.tools.base import tool
from uniclaw.tools.stream import tool_stream
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.format import sanitize_progress_line
from uniclaw.config import AppConfig

# 标准错误输出标记前缀,用于标识错误信息
STDERR_MARKER = "[stderr]"

# 匹配 bash 中误用 Windows nul 设备名的重定向: >nul, 2>nul, >>nul, <nul 等
# 在 bash 中 nul 只是普通文件名,需要替换为 /dev/null
_RE_BASH_NUL = re.compile(r"(\d?>+|<)\s*nul\b", re.IGNORECASE)


def _fix_bash_nul_redirect(cmd: str) -> str:
    """将 bash 命令中误用的 >nul / 2>nul 替换为 >/dev/null。"""
    return _RE_BASH_NUL.sub(r"\1/dev/null", cmd)


def smart_decode(data: bytes) -> str:
    """尝试多种编码方式解码"""
    if not data:
        return ""

    # 首先尝试 UTF-8
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # 在 Windows 上尝试 GBK
    if sys.platform == "win32":
        try:
            return data.decode("gbk")
        except UnicodeDecodeError:
            pass

        # 尝试 GB18030
        try:
            return data.decode("gb18030")
        except UnicodeDecodeError:
            pass

    # 最后使用 replace 模式
    return data.decode("utf-8", errors="replace")


async def _find_git_bash() -> str | None:
    """在 Windows 上查找 Git 自带的 bash.exe,返回路径或 None"""
    if sys.platform != "win32":
        return None

    # 常见安装路径
    candidates = [
        os.path.join(
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            "Git",
            "bin",
            "bash.exe",
        ),
        os.path.join(
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            "Git",
            "bin",
            "bash.exe",
        ),
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "bin", "bash.exe"
        ),
    ]

    # 从 PATH 中的 git 推断
    proc = await asyncio.create_subprocess_exec(
        "where", "git",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    if proc.returncode == 0:
        for line in stdout.decode("utf-8", errors="replace").strip().splitlines():
            git_exe = line.strip()
            if git_exe:
                git_dir = os.path.dirname(os.path.dirname(git_exe))
                bash_candidate = os.path.join(git_dir, "bin", "bash.exe")
                candidates.insert(0, bash_candidate)

    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


# 懒加载:Bash 工具首次调用时才检测
_GIT_BASH_PATH: str | None = None
_GIT_BASH_DETECTED = False


async def _kill_proc_tree(pid: int) -> None:
    """Kill a process and all its children."""
    if sys.platform == "win32":
        proc = await asyncio.create_subprocess_exec(
            "taskkill", "/F", "/T", "/PID", str(pid),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
    else:
        import signal

        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


@tool
async def Bash(command: str, timeout: int = 30, config: AppConfig = None) -> str:
    """
    执行 shell 命令并返回输出结果。

    该函数通过 asyncio.subprocess 执行指定的 shell 命令,不阻塞事件循环。
    在 Windows 上优先使用 Git bash,未找到时回退到 cmd.exe。
    在 Unix/Linux/macOS 上使用 /bin/sh。
    如果命令执行超时,会自动终止进程及其子进程树。

    注意:某些命令可能触发分页器(如 git log、man),导致阻塞等待用户交互。建议添加禁用分页参数:
    - git:`git --no-pager <subcommand>`(--no-pager 必须在 git 和子命令之间)
    - man:`MANPAGER=cat man <command>` 或 `man <command> | cat`

    重要提示:
    - 超时上限为 180 秒。如果命令执行时间可能超过 180 秒,请使用 monitor_start 工具而非本函数。
    - 如果需要启动长期运行的后台服务(如 Web 服务器、数据库等),请使用 monitor_start 工具,否则总是超时。
    - 如果需要下载大文件,请使用 monitor_start 工具(如 `monitor_start("curl -O <url>")` 或 `monitor_start("wget <url>")`),可以后台下载并监控进度。
    monitor_start 提供了更好的进程管理功能,包括进程监控、日志捕获和生命周期管理。

    Args:
        command (str): 要执行的 shell 命令字符串。
        timeout (int): 命令执行的超时时间(秒),默认为 30 秒,最大 180 秒。
                       小于等于 0 时进入异步模式,命令在后台运行,立即返回进程 ID。

    Returns:
        str: 同步模式:命令的标准输出内容。如果存在标准错误输出,会追加在标准输出之后。
             如果超时,返回超时错误信息。如果发生异常,返回[stderr]开头的标准错误。
             如果没有输出内容,返回 "(没有输出)"。
             异步模式(timeout<=0):返回 "[async] 进程已启动,PID: {pid}" 格式的消息。
    """
    root_dir = config.root_dir
    cancel_event = config.current_agent.cancel_event

    # 超时上限校验:超过 180 秒直接拒绝,引导使用 monitor_start
    if timeout > 180:
        return f"{TOOL_ERROR}: 超时上限 180 秒,请改用 monitor_start 工具。"

    # Windows 上 asyncio.subprocess.DEVNULL 可能无法打开 nul 设备(Python 3.14+)
    if sys.platform == "win32":
        stdout_flag = asyncio.subprocess.PIPE
        stderr_flag = asyncio.subprocess.PIPE
    else:
        stdout_flag = asyncio.subprocess.PIPE if timeout > 0 else asyncio.subprocess.DEVNULL
        stderr_flag = asyncio.subprocess.PIPE if timeout > 0 else asyncio.subprocess.DEVNULL

    # 懒加载:首次调用时检测 git bash
    global _GIT_BASH_PATH, _GIT_BASH_DETECTED
    if not _GIT_BASH_DETECTED:
        _GIT_BASH_PATH = await _find_git_bash()
        _GIT_BASH_DETECTED = True

    # Windows 上 asyncio.subprocess.DEVNULL 可能无法正确打开 nul 设备
    # 在 Windows 上始终使用 PIPE 作为 stdin 来避免这个问题
    stdin_flag = asyncio.subprocess.PIPE if sys.platform == "win32" else asyncio.subprocess.DEVNULL

    # 根据平台准备命令参数
    if _GIT_BASH_PATH:
        # bash 中 nul 不是设备名,自动替换为 /dev/null 防止创建误名文件
        command = _fix_bash_nul_redirect(command)
        proc = await asyncio.create_subprocess_exec(
            _GIT_BASH_PATH, "-c", command.strip(),
            stdin=stdin_flag,
            stdout=stdout_flag, stderr=stderr_flag,
            cwd=root_dir,
        )
    elif sys.platform != "win32":
        proc = await asyncio.create_subprocess_shell(
            command.strip(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stdout_flag, stderr=stderr_flag,
            cwd=root_dir, start_new_session=True,
        )
    else:
        proc = await asyncio.create_subprocess_shell(
            command.strip(),
            stdin=stdin_flag,
            stdout=stdout_flag, stderr=stderr_flag,
            cwd=root_dir,
        )

    # 关闭 stdin pipe 以允许子进程正常退出
    if sys.platform == "win32" and proc.stdin:
        proc.stdin.close()

    # 异步模式:立即返回进程信息
    if timeout <= 0:
        return f"[async] 进程已启动,PID: {proc.pid}"

    start_time = time.monotonic()

    # 用于流式输出和最终结果收集
    _collected = {"stdout": [], "stderr": []}

    async def _read_stream(stream, key):
        """批量读取流,读多少发多少,直接收集原始数据。

        流式输出和最终结果都直接存原始解码数据,
        sanitize_progress_line 在最终拼接时统一处理 \r 进度条。
        """
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                text = smart_decode(chunk)
                _collected[key].append(text)
                await tool_stream(text)
        except (asyncio.CancelledError, Exception):
            pass

    async def _wait_with_cancel():
        """等待进程完成,同时逐行推送输出。"""
        read_tasks = []
        if proc.stdout:
            read_tasks.append(
                asyncio.create_task(_read_stream(proc.stdout, "stdout"))
            )
        if proc.stderr:
            read_tasks.append(
                asyncio.create_task(_read_stream(proc.stderr, "stderr"))
            )

        try:
            while proc.returncode is None:
                if cancel_event is not None and cancel_event.is_set():
                    await _kill_proc_tree(proc.pid)
                    for t in read_tasks:
                        t.cancel()
                    await asyncio.gather(*read_tasks, return_exceptions=True)
                    return "cancelled"
                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass

            # 进程已结束,等待流读取完成(可能还有缓冲中的数据)
            await asyncio.gather(*read_tasks, return_exceptions=True)
            return "done"
        except (asyncio.CancelledError, Exception):
            for t in read_tasks:
                t.cancel()
            await asyncio.gather(*read_tasks, return_exceptions=True)
            raise

    try:
        status = await asyncio.wait_for(_wait_with_cancel(), timeout=timeout)

        stdout = sanitize_progress_line("".join(_collected["stdout"]))
        stderr = sanitize_progress_line("".join(_collected["stderr"]))
        out = stdout
        if stderr:
            out += ("\n" if out else "") + f"{STDERR_MARKER}" + stderr

        if status == "cancelled":
            elapsed_time = time.monotonic() - start_time
            cancel_msg = f"{TOOL_ERROR}: 用户中断(进程已终止,用时 {elapsed_time:.1f} 秒)"
            return (out.strip() + "\n" + cancel_msg).strip()

        return out.strip() or "(没有输出)"

    except asyncio.TimeoutError:
        await _kill_proc_tree(proc.pid)
        # 等待一小段时间让读取任务收集最后的输出
        await asyncio.sleep(0.1)

        stdout = sanitize_progress_line("".join(_collected["stdout"]))
        stderr = sanitize_progress_line("".join(_collected["stderr"]))
        out = stdout
        if stderr:
            out += ("\n" if out else "") + f"{STDERR_MARKER}" + stderr
        timeout_msg = f"{TOOL_ERROR}: 在 {timeout} 秒后超时(进程已终止)"
        return (out.strip() + "\n" + timeout_msg).strip()

    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


# ── Grep ──────────────────────────────────────────────────────────────────


async def _has_rg() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "rg", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


async def _has_native_grep() -> bool:
    """检查 rg 或 grep 是否可用"""
    if await _has_rg():
        return True
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


def _python_grep(
    pattern: str,
    path: str,
    glob: str = None,
    output_mode: str = "content",
    case_insensitive: bool = False,
    context: int = 0,
) -> str:
    """纯 Python 实现的 grep,作为 rg/grep 不可用时的回退方案"""
    import re

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"{TOOL_ERROR}: 无效的正则表达式: {e}"

    target = Path(path)
    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = sorted(target.rglob(glob or "*"))
        files = [f for f in files if f.is_file()]
    else:
        return f"{TOOL_ERROR}: 路径不存在: {path}"

    results = []
    matched_files = 0

    for filepath in files:
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError):
            continue

        lines = text.splitlines()
        if not lines:
            continue

        if output_mode == "files_with_matches":
            for line in lines:
                if regex.search(line):
                    results.append(str(filepath))
                    matched_files += 1
                    break

        elif output_mode == "count":
            count = sum(1 for line in lines if regex.search(line))
            if count:
                results.append(f"{filepath}:{count}")
                matched_files += 1

        elif output_mode == "content":
            match_indices = set()
            for i, line in enumerate(lines):
                if regex.search(line):
                    match_indices.add(i)

            if not match_indices:
                continue
            matched_files += 1

            include = set()
            for idx in match_indices:
                for j in range(
                    max(0, idx - context), min(len(lines), idx + context + 1)
                ):
                    include.add(j)

            sorted_indices = sorted(include)
            prev = -2
            for idx in sorted_indices:
                if context and prev >= 0 and idx - prev > 1:
                    results.append("--")
                prefix = ":" if idx in match_indices else "-"
                results.append(f"{filepath}:{idx + 1}{prefix}{lines[idx]}")
                prev = idx
        else:
            return f"{TOOL_ERROR}: 未知的 output_mode: {output_mode}"

    if not results:
        return "No matches found"
    return "\n".join(results)


@tool
async def Grep(
    pattern: str,
    path: str,
    glob: str = None,
    output_mode: str = "content",
    case_insensitive: bool = False,
    context: int = 0,
) -> str:
    """
    在文件中搜索匹配的模式,支持使用 rg (ripgrep) 或 grep 工具。

    Args:
        pattern: 要搜索的正则表达式模式
        path: 搜索的文件或目录路径
        glob: 文件匹配模式,用于过滤要搜索的文件类型(如 "*.py")
        output_mode: 输出模式,可选值:
            - "content": 返回带行号的匹配内容(默认)
            - "files_with_matches": 仅返回匹配的文件列表
            - "count": 返回每个文件的匹配行数
        case_insensitive: 是否忽略大小写进行搜索,默认为 False
        context: 上下文行数,显示匹配行前后指定行数的内容,默认为 0

    Returns:
        str: 搜索结果字符串。如果找到匹配项,返回结果(最多20000字符);
             如果没有匹配项,返回 "No matches found";
             如果发生错误,返回 "Error: {错误信息}"
    """

    if not await _has_native_grep():
        out = _python_grep(
            pattern=pattern,
            path=path,
            glob=glob,
            output_mode=output_mode,
            case_insensitive=case_insensitive,
            context=context,
        )
        return out

    use_rg = await _has_rg()
    cmd = ["rg" if use_rg else "grep", "--no-heading"]

    if case_insensitive:
        cmd.append("-i")
    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    elif output_mode == "content":
        cmd.append("-n")
        if context:
            cmd += ["-C", str(context)]
    else:
        return f"{TOOL_ERROR}: 未知的 output_mode: {output_mode}"

    if glob:
        cmd += ["--glob", glob] if use_rg else ["--include", glob]
    cmd.append(pattern)
    cmd.append(path)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=30,
        )
        stdout = smart_decode(stdout_bytes)
        out = stdout.strip()
        return out[:20000] if out else "No matches found"
    except asyncio.TimeoutError:
        return f"{TOOL_ERROR}: 搜索超时"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


async def _check_es() -> str | None:
    """检查 Everything (es.exe) 是否可用,返回错误信息或 None"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "es", "test_sandbox_check",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=5)
        stderr = smart_decode(stderr_bytes).strip()
        if stderr:
            return stderr
    except FileNotFoundError:
        return "未找到 es.exe 命令,请安装 Everything 并确保 es.exe 在 PATH 中"
    except asyncio.TimeoutError:
        return "es.exe 响应超时"
    except Exception as e:
        return str(e)
    return None


@tool
async def search_files_with_everything(
    query: str,
    max_results: int = 0,
    path_filter: str = None,
) -> str:
    """
    使用 Everything 搜索引擎的文件名搜索工具(es 命令行)

    该函数通过调用 es.exe 命令行工具来执行快速文件名搜索。Everything 是一款高效的 Windows 文件搜索工具,
    能够实时索引文件系统并提供毫秒级的搜索响应。

    Args:
        query (str): 搜索关键词或查询字符串
            - 支持通配符:*(任意字符)、?(单个字符)
            - 支持逻辑运算符:AND、OR、NOT
            - 示例:"report"、"*.pdf"、"document AND 2024"

        max_results (int): 最大返回结果数量限制
            - 默认值 0 表示不限制返回数量
            - 设置为正整数可限制返回结果条数,提高响应速度
            - 建议对于大型搜索结果设置合理的限制值(如 50-100)

        path_filter (str): 路径过滤器,用于限定搜索范围到特定目录
            - 可以是绝对路径或相对路径
            - 示例:"C:/Users/Documents"、"./src"
            - 使用正斜杠 / 以避免转义问题

    Returns:
        str: 搜索结果字符串
            - 成功时:返回匹配的文件列表,每行一个文件路径
              例如:"C:/Users/file1.txt\nC:/Users/file2.txt"
            - 失败时:返回以 "[stderr]" 开头的错误信息字符串
              例如:"[stderr]es command not found" 或 "[stderr]Permission denied"
            - 常见错误场景:
              * es 命令未安装或未配置到系统 PATH
              * 指定的路径不存在或无访问权限
              * 搜索查询语法错误

    Note:
        - 使用前需确保系统已安装 Everything 并正确配置 es.exe 命令行工具
        - 如果检测到 es 命令不可用,函数会自动返回 "[stderr]" 开头的错误提示
        - 路径参数建议使用正斜杠格式以避免 Unicode 转义问题
        - 返回值判断:检查字符串是否以 "[stderr]" 开头来判断是否执行成功

    Example:
        >>> # 基本搜索
        >>> result = search_files_with_everything("readme")
        >>> if not result.startswith("[stderr]"):
        ...     print(result)

        >>> # 限制结果数量
        >>> result = search_files_with_everything("*.py", max_results=10)

        >>> # 在指定路径下搜索
        >>> result = search_files_with_everything("config", path_filter="D:/Projects")

        >>> # 组合使用
        >>> result = search_files_with_everything("test_*.py", max_results=20, path_filter="./tests")
    """

    # 构建带参数的完整搜索命令
    search_cmd = "es"

    if path_filter:
        search_cmd += f' -p "{path_filter}"'
    if max_results:
        search_cmd += f" -n {max_results}"
    search_cmd += f' "{query}"'

    result = await Bash.func(search_cmd)
    return result


# 工具检测结果缓存(3分钟过期)
_tools_cache: dict = {"result": None, "time": 0}
_tools_cache_ttl = 60 * 30


async def get_tools(config=None) -> list:
    """获取Shell工具列表(带缓存,避免重复检测依赖)"""
    now = time.monotonic()
    if _tools_cache["result"] is not None and now - _tools_cache["time"] < _tools_cache_ttl:
        return _tools_cache["result"]

    from uniclaw.console.ui import warn

    tools = [Bash, Grep]

    _es_err = await _check_es()
    if _es_err:
        await warn(
            f"[shell] Everything 不可用: {_es_err},search_files_with_everything 工具已禁用。",
            config,
        )
    else:
        tools.append(search_files_with_everything)

    _tools_cache["result"] = tools
    _tools_cache["time"] = now
    return tools


def get_all_tools() -> list:
    """获取所有Shell工具(无条件返回)"""
    return [Bash, Grep, search_files_with_everything]
