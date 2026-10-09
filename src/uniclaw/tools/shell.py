import asyncio
import fnmatch
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from enum import StrEnum
from pathlib import Path

from uniclaw.tools.base import tool, ToolRuntime

logger = logging.getLogger(__name__)
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.format import sanitize_progress_line
from uniclaw.utils.process import run_command, smart_decode
from uniclaw.utils.read_text import read_text_file

# 标准错误输出标记前缀,用于标识错误信息
STDERR_MARKER = "[stderr]"


class GrepOutputMode(StrEnum):
    """Grep 工具的输出模式。"""

    content = "content"
    files_with_matches = "files_with_matches"
    count = "count"


# 匹配 bash 中误用 Windows nul 设备名的重定向: >nul, 2>nul, >>nul, <nul 等
# 在 bash 中 nul 只是普通文件名,需要替换为 /dev/null
_RE_BASH_NUL = re.compile(r"(\d?>+|<)\s*nul\b", re.IGNORECASE)


def fix_bash_nul_redirect(cmd: str) -> str:
    """将 bash 命令中误用的 >nul / 2>nul 替换为 >/dev/null。"""
    return _RE_BASH_NUL.sub(r"\1/dev/null", cmd)


# 匹配 bash heredoc 起始定界符: <<WORD / <<-WORD / <<'WORD' / <<"WORD"
# << 后必须跟可选的 -、引号和标识符 —— 这样能排除位移运算(如 $((1<<2))、x<<3)
_RE_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)")


def contains_heredoc(cmd: str) -> bool:
    """检测命令是否含 bash heredoc 语法(<< 或 <<-)。"""
    return bool(_RE_HEREDOC.search(cmd))




def _find_git_bash() -> str | None:
    """查找 Git Bash,如果找到则将其 bin 目录加入 PATH,返回 bash 路径或 None"""
    if sys.platform != "win32":
        return None

    # 优先查找常见 Git 安装路径
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Git"),
        os.path.join(
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Git"
        ),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git"),
    ]

    for git_dir in candidates:
        bash_path = os.path.join(git_dir, "bin", "bash.exe")
        if os.path.isfile(bash_path):
            # 把 Git 的 bin 加入当前进程的 PATH
            bin_dir = os.path.join(git_dir, "bin")
            current_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bin_dir};{current_path}"
            return bash_path

    # 最后检查 PATH 中是否已有 bash
    import subprocess

    try:
        result = subprocess.run(
            ["where", "bash"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0]
    except Exception as e:
        logger.warning("检测 bash 可用性失败: %s", e)

    return None


GIT_BASH_PATH: str | None = _find_git_bash()


async def _kill_proc_tree(pid: int) -> None:
    """Kill a process and all its children."""
    if sys.platform == "win32":
        proc = await asyncio.create_subprocess_exec(
            "taskkill",
            "/F",
            "/T",
            "/PID",
            str(pid),
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


# Bash 前台等待超时上限(秒)。超过该时长的任务应使用 monitor_start 转入后台,
# 避免 agent 主循环被单个命令长时间阻塞。
BASH_MAX_TIMEOUT = 300

# Bash 无界输出保护: 累计输出超过任一阈值即终止进程树,防止刷屏把上下文冲垮。
# 阈值会在返回文本里明确写出,便于调用方调整命令输出。
BASH_MAX_OUTPUT_LINES = 50_000
BASH_MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10MB


def _find_bash() -> str | None:
    """查找可用的 bash 可执行文件(Git Bash 优先,其次 PATH)。"""
    if GIT_BASH_PATH and os.path.isfile(GIT_BASH_PATH):
        return GIT_BASH_PATH
    found = shutil.which("bash")
    return found


def _prepare_heredoc_script(command: str) -> tuple[str, str | None]:
    """Windows 下将含 heredoc 的命令改写为「bash 执行临时脚本」。

    Windows 的 create_subprocess_shell 走 cmd.exe,拆坏引号/换行后 bash 拿不到
    完整的 heredoc 体,`python - <<'EOF'` 会把命令拖进交互式 REPL 无限刷屏。
    处理方式: 把原始命令文本原样写入临时 .sh,由 bash 解析 heredoc。

    Args:
        command: 原始命令字符串。

    Returns:
        tuple[str, str | None]: (待执行命令, 临时脚本路径)。
            无需改写时返回 (原命令, None)。
            无法安全执行时返回 (以 TOOL_ERROR 开头的提示文本, None),
            调用方应直接 return 该文本。
    """
    if sys.platform != "win32":
        return command, None
    if not contains_heredoc(command):
        return command, None

    bash = _find_bash()
    if not bash:
        return (
            f"{TOOL_ERROR}: 命令含 heredoc 语法(<<),Windows 的 cmd.exe 不支持,"
            f"直接执行会拖入交互式 REPL 无限刷屏;当前又找不到 bash,无法安全执行。"
            f"请改用文件脚本: 先用 Write 工具写入 .sh 文件,再执行 bash <该文件>。"
        ), None

    # 原样写入命令文本(bash 自己解析 heredoc),换行统一为 LF 供 bash 读取
    script_text = command.replace("\r\n", "\n")
    if not script_text.endswith("\n"):
        script_text += "\n"
    fd, tmp_path = tempfile.mkstemp(suffix=".sh", prefix="uniclaw_heredoc_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(script_text)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return (
            f"{TOOL_ERROR}: 写入 heredoc 临时脚本失败({tmp_path}): "
            f"{type(e).__name__}: {e}"
        ), None

    # bash 路径/脚本路径都可能含空格,加引号交给 cmd.exe 透传
    return f'"{bash}" "{tmp_path}"', tmp_path


@tool
async def Bash(
    command: str, timeout: int = 30, tool_runtime: ToolRuntime = None
) -> str:
    """
    执行 shell 命令并返回输出结果。

    该函数通过 asyncio.subprocess 执行指定的 shell 命令,不阻塞事件循环。
    Windows 上默认使用 cmd.exe,可用 `bash -c "命令"` 调用 Git Bash,用 `powershell -c "命令"` 调用 PowerShell。
    Unix/Linux/macOS 上使用 /bin/sh。

    注意:某些命令可能触发分页器(如 git log、man),导致阻塞等待用户交互。建议添加禁用分页参数:
    - git:`git --no-pager <subcommand>`(--no-pager 必须在 git 和子命令之间)
    - man:`MANPAGER=cat man <command>` 或 `man <command> | cat`

    超时行为:
    - 如果命令执行超时,进程会自动转入后台监控系统,返回监控 ID。
    - 使用 monitor_output 查看后续输出,monitor_stop 停止进程,monitor_list 查看所有进程。

    输出上限:
    - 累计输出超过 50000 行或 10MB 时自动终止进程树,返回文本会写明
      「输出超出上限已终止(N 行/M 字节)」及实际阈值,避免刷屏冲垮上下文。

    Windows heredoc:
    - 命令含 heredoc(<< / <<-)时会自动改写为 bash 执行临时脚本,
      避免 cmd.exe 拆坏引号后把命令拖进交互式 REPL;找不到 bash 则拒绝执行。

    注意:
    - 下载 HTTP/HTTPS 文件请使用 http_download 工具(支持断点续传、并发下载、进度显示)。
    - 长期运行的后台服务请使用 monitor_start 工具。

    Args:
        command (str): 要执行的 shell 命令字符串。
        timeout (int): 命令执行的超时时间(秒),默认为 30 秒,取值范围 1-300。
                       超时后进程自动转入后台监控,预期运行更久的命令请直接用 monitor_start 启动。

    Returns:
        str: 命令的标准输出内容。如果存在标准错误输出,会追加在标准输出之后。
             如果超时,进程转入后台监控并返回监控 ID 和已有输出。
             输出超过上限时终止进程并明确标注截断原因。
             如果发生异常,返回[stderr]开头的标准错误。
             如果没有输出内容,返回 "(没有输出)"。
    """
    from .monitor.tools import monitor_start

    config = tool_runtime.config
    root_dir = config.root_dir
    cancel_event = config.current_agent.cancel_event

    # 超时校验
    if timeout <= 0:
        return f"{TOOL_ERROR}: timeout 必须大于 0,后台任务请使用 {monitor_start.name} 工具。"
    if timeout > BASH_MAX_TIMEOUT:
        return (
            f"{TOOL_ERROR}: timeout 不能超过 {BASH_MAX_TIMEOUT} 秒,"
            f"长期任务请使用 {monitor_start.name} 工具。"
        )

    # 如果是 bash 命令,自动修正 nul 重定向
    if command.strip().lower().startswith("bash "):
        command = fix_bash_nul_redirect(command)

    # Windows heredoc 保护: 改写为 bash 执行临时脚本,避免拖入 REPL
    tmp_script: str | None = None
    command, tmp_script = _prepare_heredoc_script(command)
    if tmp_script is None and command.startswith(TOOL_ERROR):
        return command

    def _cleanup_tmp_script() -> None:
        if tmp_script:
            try:
                os.unlink(tmp_script)
            except OSError:
                pass

    try:
        # 根据平台准备命令参数
        # Windows 上 asyncio.subprocess.DEVNULL 可能无法正确打开 nul 设备,stdin 用 PIPE
        if sys.platform != "win32":
            proc = await asyncio.create_subprocess_shell(
                command.strip(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=root_dir,
                start_new_session=True,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                command.strip(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=root_dir,
            )

        # 关闭 stdin pipe 以允许子进程正常退出
        if sys.platform == "win32" and proc.stdin:
            proc.stdin.close()

        start_time = time.monotonic()

        # 用于流式输出和最终结果收集
        _collected = {"stdout": [], "stderr": []}
        # 无界输出保护的累计量与溢出标记
        _stats = {"lines": 0, "bytes": 0}
        overflow = asyncio.Event()

        async def _read_stream(stream, key):
            """批量读取流,读多少发多少,直接收集原始数据。

            流式输出和最终结果都直接存原始解码数据,
            sanitize_progress_line 在最终拼接时统一处理 \r 进度条。

            累计输出超过行数/字节数上限时置位 overflow,由等待循环终止进程树。
            """
            try:
                while True:
                    if overflow.is_set():
                        break
                    chunk = await stream.read(4096)
                    if not chunk:
                        break
                    text = smart_decode(chunk)
                    # 只收集到阈值为止:单个读块可能远大于剩余配额,
                    # 整块塞进去会让"上限 200 行"实际收回 800+ 行
                    line_room = BASH_MAX_OUTPUT_LINES - _stats["lines"]
                    byte_room = BASH_MAX_OUTPUT_BYTES - _stats["bytes"]
                    if line_room <= 0 or byte_room <= 0:
                        overflow.set()
                        break
                    trimmed = text
                    if len(trimmed) > byte_room:
                        trimmed = trimmed[:byte_room]
                    if trimmed.count("\n") > line_room:
                        # 按行数截断到配额边界
                        cut = 0
                        seen = 0
                        for i, ch in enumerate(trimmed):
                            if ch == "\n":
                                seen += 1
                                if seen >= line_room:
                                    cut = i + 1
                                    break
                        trimmed = trimmed[:cut] if cut else trimmed
                    if trimmed:
                        _collected[key].append(trimmed)
                        _stats["bytes"] += len(trimmed.encode("utf-8", errors="replace"))
                        _stats["lines"] += trimmed.count("\n")
                        await tool_runtime.stream(trimmed)
                    if (
                        _stats["lines"] >= BASH_MAX_OUTPUT_LINES
                        or _stats["bytes"] >= BASH_MAX_OUTPUT_BYTES
                    ):
                        # 超限即置位并停止收集 —— 继续收只会把上下文冲垮,
                        # 进程由等待循环负责终止
                        overflow.set()
                        break
            except (asyncio.CancelledError, Exception):
                pass

        # 提前创建 read tasks,超时时可从外部取消
        read_tasks = []
        if proc.stdout:
            read_tasks.append(asyncio.create_task(_read_stream(proc.stdout, "stdout")))
        if proc.stderr:
            read_tasks.append(asyncio.create_task(_read_stream(proc.stderr, "stderr")))

        async def _drain_read_tasks() -> None:
            """等待读任务自然读完(进程已结束,流会 EOF)。"""
            await asyncio.gather(*read_tasks, return_exceptions=True)

        async def _cancel_read_tasks() -> None:
            """取消读任务(进程被杀 / 中断 / 溢出收尾路径用)。"""
            for t in read_tasks:
                t.cancel()
            await asyncio.gather(*read_tasks, return_exceptions=True)

        async def _wait_with_cancel():
            """等待进程完成,同时逐行推送输出。"""
            try:
                while proc.returncode is None:
                    if cancel_event is not None and cancel_event.is_set():
                        await _kill_proc_tree(proc.pid)
                        await _cancel_read_tasks()
                        return "cancelled"
                    if overflow.is_set():
                        await _kill_proc_tree(proc.pid)
                        await _cancel_read_tasks()
                        return "overflow"
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=0.1)
                    except asyncio.TimeoutError:
                        pass

                # 进程已结束,等待流读取完成(可能还有缓冲中的数据)
                await _drain_read_tasks()
                return "overflow" if overflow.is_set() else "done"
            except (asyncio.CancelledError, Exception):
                await _cancel_read_tasks()
                raise

        async def _kill_quietly() -> None:
            """进程仍在运行时强杀进程树,失败不抛出(收尾路径用)。"""
            if proc.returncode is not None:
                return
            try:
                await _kill_proc_tree(proc.pid)
            except asyncio.CancelledError:
                # 清理期间可能被再次取消,吞掉二次取消保证收尾完成
                pass
            except Exception:
                pass

        def _join_output() -> str:
            stdout = sanitize_progress_line("".join(_collected["stdout"]))
            stderr = sanitize_progress_line("".join(_collected["stderr"]))
            out = stdout
            if stderr:
                out += ("\n" if out else "") + f"{STDERR_MARKER}" + stderr
            return out

        def _overflow_msg() -> str:
            return (
                f"{TOOL_ERROR}: 输出超出上限已终止"
                f"({_stats['lines']} 行/{_stats['bytes']} 字节;上限 "
                f"{BASH_MAX_OUTPUT_LINES} 行/{BASH_MAX_OUTPUT_BYTES} 字节)。"
                f"输出已截断,请收窄命令输出(如加 head/管道过滤)后重试"
            )

        try:
            status = await asyncio.wait_for(_wait_with_cancel(), timeout=timeout)

            from uniclaw.tools.monitor.tools import monitor_output, monitor_stop  # 循环导入例外: 取工具规范名
            out = _join_output()

            if status == "cancelled":
                elapsed_time = time.monotonic() - start_time
                cancel_msg = (
                    f"{TOOL_ERROR}: 用户中断(进程已终止,用时 {elapsed_time:.1f} 秒)"
                )
                return (out.strip() + "\n" + cancel_msg).strip()

            if status == "overflow":
                return (out.strip() + "\n" + _overflow_msg()).strip()

            return out.strip() or "(没有输出)"

        except asyncio.TimeoutError:
            # 确保旧的 read tasks 已停止,避免与 monitor 的 reader 竞争
            await _cancel_read_tasks()

            # 输出已超上限时不再转监控 —— 转过去只会继续刷屏
            if overflow.is_set():
                await _kill_quietly()
                return (_join_output().strip() + "\n" + _overflow_msg()).strip()

            # 将仍在运行的进程转移到 monitor 系统
            from uniclaw.tools.monitor.manager import MonitorManager

            manager = MonitorManager.get_instance()
            try:
                monitor_id, _ = await manager.register_existing_process(
                    process=proc,
                    command=command,
                    description=command[:80],
                    cwd=root_dir,
                    config=config,
                )
            except RuntimeError as e:
                # 并发数已满,回退到杀死进程
                await _kill_quietly()
                return f"{TOOL_ERROR}: 超时且无法转入监控 - {e}"

            from uniclaw.tools.monitor.tools import monitor_output, monitor_stop  # 循环导入例外: 取工具规范名
            out = _join_output()
            header = (
                f"进程在 {timeout} 秒后超时,已转入后台监控\n"
                f"  监控 ID: {monitor_id}\n"
                f"  使用 {monitor_output.name} 查看输出,{monitor_stop.name} 停止进程"
            )
            return (header + "\n" + out.strip()).strip()

        except asyncio.CancelledError:
            # 外部取消(ESC / Tool 层 task.cancel)会以 CancelledError 打进来,
            # 内层轮询的 kill 路径几乎必输(事件 set 立即唤醒 cancel_wait),
            # 必须在这里杀进程树,否则子进程成为孤儿继续运行
            await _kill_quietly()
            raise

        except Exception:
            # 意外异常也收尾,避免残留进程;
            # 收尾后原样上抛(bug 级异常交给 Tool.__call__ 兜底),不转成返回值
            await _kill_quietly()
            raise
    finally:
        _cleanup_tmp_script()


# ── Grep ──────────────────────────────────────────────────────────────────


def _is_binary_file(path: Path) -> bool:
    """判断文件是否为二进制(含 NUL 且非 UTF-16/32 BOM 文本)。

    与 read_text_file 的二进制判定保持一致,用于统计"被跳过的二进制文件"。
    """
    try:
        with path.open("rb") as f:
            head = f.read(8192)
    except OSError:
        return False
    if not head:
        return False
    # UTF-16/32 BOM 文本天然含 NUL,豁免
    if head.startswith(
        (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xff\xfe", b"\xfe\xff")
    ):
        return False
    return b"\x00" in head


def _collect_search_files(path: str, glob: str | None = None) -> list[Path] | str:
    """枚举搜索范围内的候选文件列表。

    与 _python_grep 的文件发现逻辑一致: 目录递归 + .gitignore 过滤
    (显式给出的搜索根强制可搜)。单文件直接返回。

    Args:
        path: 搜索的文件或目录路径。
        glob: 可选的文件名通配符过滤(如 "*.py")。

    Returns:
        list[Path]: 候选文件列表(可能为空)。
        str: 路径不存在时返回以 TOOL_ERROR 开头的报错文本。
    """
    target = Path(path)
    if target.is_file():
        return [target]
    if not target.is_dir():
        return f"{TOOL_ERROR}: 路径不存在: {path}"

    files = sorted(target.rglob(glob or "*"))
    files = [f for f in files if f.is_file()]
    # 按 .gitignore 规则过滤(含隐藏文件),但显式给出的搜索根必须可搜
    from uniclaw.utils.gitignore import (
        get_not_ignored_files,
        is_ignored_by_gitignore,
    )

    if not is_ignored_by_gitignore([target]):
        files = get_not_ignored_files(files)
    return files


def _count_binary_files(path: str, glob: str | None = None) -> tuple[int, int]:
    """统计搜索范围内的二进制文件数与总文件数。

    Returns:
        tuple[int, int]: (二进制文件数, 总文件数)。路径不存在或无文件时为 (0, 0)。
    """
    files = _collect_search_files(path, glob)
    if isinstance(files, str) or not files:
        return (0, 0)
    binary = sum(1 for f in files if _is_binary_file(f))
    return (binary, len(files))


def _binary_skip_note(binary: int, total: int) -> str:
    """生成"已跳过二进制文件"说明;无跳过时返回空串。

    全部候选都是二进制时单独说明 —— 否则 "No matches found" 与
    "确实无匹配" 无法区分,会误导调用方认为符号不存在。
    """
    if binary <= 0:
        return ""
    from uniclaw.tools.media import ReadMedia  # 循环导入例外: 取工具规范名
    if total > 0 and binary >= total:
        return (
            f"(搜索目标全部是二进制文件,已跳过 {binary} 个二进制文件;"
            f"文本搜索无法匹配二进制内容,请用 {ReadMedia.name} 或专用工具)"
        )
    return f"(已跳过 {binary} 个二进制文件)"


async def _has_rg() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "rg",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode == 0
    except Exception as e:
        logger.warning("检测 rg 可用性失败: %s", e)
        return False


async def _has_native_grep() -> bool:
    """检查 rg 或 grep 是否可用"""
    if sys.platform == "win32":
        return await _has_rg()
    if await _has_rg():
        return True
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode == 0
    except Exception as e:
        logger.warning("检测 grep 可用性失败: %s", e)
        return False


def _python_grep(
    pattern: str,
    path: str,
    glob: str | None = None,
    output_mode: GrepOutputMode = GrepOutputMode.content,
    case_insensitive: bool = False,
    context: int = 0,
) -> str:
    """纯 Python 实现的 grep,作为 rg/grep 不可用时的回退方案

    默认忽略以 . 开头的隐藏文件/目录。
    用户显式指定的文件(单个文件路径)不受此限制。
    """
    import re

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"{TOOL_ERROR}: 无效的正则表达式: {e}"

    files = _collect_search_files(path, glob)
    if isinstance(files, str):
        return files

    results = []
    matched_files = 0
    skipped_binary = 0

    for filepath in files:
        try:
            text = read_text_file(filepath)
            if text is None:
                # read_text_file 对二进制返回 None,单独统计以免与"无匹配"混淆
                if _is_binary_file(filepath):
                    skipped_binary += 1
                continue
        except (PermissionError, OSError):
            continue

        lines = text.splitlines()
        if not lines:
            continue

        if output_mode == GrepOutputMode.files_with_matches:
            for line in lines:
                if regex.search(line):
                    results.append(str(filepath))
                    matched_files += 1
                    break

        elif output_mode == GrepOutputMode.count:
            count = sum(1 for line in lines if regex.search(line))
            if count:
                results.append(f"{filepath}:{count}")
                matched_files += 1

        elif output_mode == GrepOutputMode.content:
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

    note = _binary_skip_note(skipped_binary, len(files))
    if not results:
        return f"No matches found\n{note}" if note else "No matches found"
    return "\n".join(results) + (f"\n{note}" if note else "")


@tool
async def Grep(
    pattern: str,
    path: str,
    glob: str | None = None,
    output_mode: GrepOutputMode = GrepOutputMode.content,
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
        str: 搜索结果字符串。如果找到匹配项,返回结果;
             如果没有匹配项,返回 "No matches found";
             二进制文件被跳过时追加「已跳过 N 个二进制文件」说明;
             如果发生错误,返回以 TOOL_ERROR 开头的报错文本
    """

    if not await _has_native_grep():
        # 纯 Python 回退实现是同步阻塞的,丢到线程池执行,避免冻结事件循环
        out = await asyncio.to_thread(
            _python_grep,
            pattern=pattern,
            path=path,
            glob=glob,
            output_mode=output_mode,
            case_insensitive=case_insensitive,
            context=context,
        )
        return out

    use_rg = await _has_rg()
    cmd = ["rg" if use_rg else "grep"]

    # grep 必须带 -r: 无它时给目录只会报 "Is a directory"(exit 2),
    # 目录搜索整体失效 —— 而 rg 缺失时正是走这条回退路径。
    # -r 对单文件无副作用,故无条件加上。rg 本身就递归,不需要。
    if not use_rg:
        cmd.append("-r")

    if case_insensitive:
        cmd.append("-i")
    if output_mode == GrepOutputMode.files_with_matches:
        cmd.append("-l")
    elif output_mode == GrepOutputMode.count:
        cmd.append("-c")
    elif output_mode == GrepOutputMode.content:
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
        # decode=smart_decode: Windows 上 rg/grep 命中 GBK 文件时输出非 UTF-8
        r = await run_command(cmd, timeout=30, decode=smart_decode)
    except OSError as e:
        # 命令不可执行(无权限、竞态消失等);超时是 r.timed_out,不走异常
        return (
            f"{TOOL_ERROR}: 搜索异常(pattern={pattern!r}, path={path}): "
            f"{type(e).__name__}: {e}"
        )

    if r.timed_out:
        return f"{TOOL_ERROR}: 搜索超时(pattern={pattern!r}, path={path}, 30 秒)"
    # rg/grep 退出码约定: 0=有匹配, 1=无匹配, 其余=出错
    # (非法正则、路径不存在、无权限等)。出错必须回报,不能伪装成
    # "无匹配",否则 LLM 会误判"该符号不存在"。
    if r.code not in (0, 1):
        return (
            f"{TOOL_ERROR}: 搜索失败({cmd[0]} 退出码 {r.code}, "
            f"pattern={pattern!r}, path={path}): {r.stderr.strip() or '无错误输出'}"
        )

    out = r.stdout.strip()

    # rg 静默跳过二进制,grep 只在二进制"有匹配"时报 "Binary file X matches"。
    # 无论哪种,被跳过的二进制都必须显式声明,否则 "No matches found" 与
    # "确实无匹配"无法区分。统计在独立线程里做,避免阻塞事件循环。
    binary, total = await asyncio.to_thread(_count_binary_files, path, glob)
    note = _binary_skip_note(binary, total)

    # grep 的 "Binary file X matches" 属于二进制命中而非文本命中,归并到说明里
    binary_hit_lines = [
        l for l in out.splitlines() if l.startswith("Binary file ") and l.endswith(" matches")
    ]
    if binary_hit_lines:
        hit_note = f"(其中 {len(binary_hit_lines)} 个二进制文件含匹配: " + ", ".join(
            l[len("Binary file ") : -len(" matches")] for l in binary_hit_lines
        ) + ")"
        note = f"{note} {hit_note}" if note else hit_note
        out = "\n".join(l for l in out.splitlines() if l not in binary_hit_lines)

    if not out:
        return f"No matches found\n{note}" if note else "No matches found"
    return out + (f"\n{note}" if note else "")


async def _check_es() -> str | None:
    """检查 Everything (es.exe) 是否可用,返回错误信息或 None"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "es",
            "test_sandbox_check",
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
    path_filter: str | None = None,
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

    # argv 直传 es.exe,不经 shell 解释,query/path_filter 中的引号、& 等不会被拼进命令行
    args = ["es"]
    if path_filter:
        args += ["-path", path_filter]
    if max_results:
        args += ["-n", str(max_results)]
    args.append(query)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode(errors="replace").strip()
        return (
            f"{STDERR_MARKER} {error_msg}"
            if error_msg
            else f"{STDERR_MARKER} 命令执行失败 (退出码: {proc.returncode})"
        )

    output = stdout.decode(errors="replace").strip()
    return output if output else "(没有输出)"


# 工具检测结果缓存(3分钟过期)
_tools_cache: dict = {"result": None, "time": 0}
_tools_cache_ttl = 60 * 30


async def get_tools(config=None) -> list:
    """获取Shell工具列表(带缓存,避免重复检测依赖)"""
    now = time.monotonic()
    if (
        _tools_cache["result"] is not None
        and now - _tools_cache["time"] < _tools_cache_ttl
    ):
        return _tools_cache["result"]

    tools = [Bash, Grep]

    _es_err = await _check_es()
    if _es_err:
        if config:
            config.record_unavailable_tools(
                [search_files_with_everything.name],
                f"Everything 不可用: {_es_err}",
            )
    else:
        if config:
            config.clear_unavailable_tools([search_files_with_everything.name])
        tools.append(search_files_with_everything)

    _tools_cache["result"] = tools
    _tools_cache["time"] = now
    return tools


def get_all_tools() -> list:
    """获取所有Shell工具(无条件返回)"""
    return [Bash, Grep, search_files_with_everything]
