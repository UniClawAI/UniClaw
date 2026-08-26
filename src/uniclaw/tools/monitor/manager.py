import asyncio
import logging
import os
import re
import shutil
import signal
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.format import sanitize_progress_line

from .models import Monitor, MonitorStatus
from .viewer import build_viewer_argv, ensure_viewer_script, viewer_paths


class MonitorManager:
    """全局进程监控管理器(单例)"""

    _instance: "MonitorManager | None" = None

    # shell 类命令会自行回显 prompt+输入, 可视模式需关闭查看器本地回显。
    # 两分支: ① 裸命令(cmd/bash) ② 带完整路径的 shell
    #   (支持 "C:\Windows\System32\cmd.exe"、路径含空格
    #   如 "D:\Program Files\Git\bin\bash.exe")
    _SHELL_CMD_RE = re.compile(
        r'^\s*"?(cmd|powershell|pwsh|bash|sh|wsl|zsh)(?:\.exe)?"?\b'
        r"|^\s*\"?(?:[A-Za-z]:)?[\\/](?:[^\"\\/]*[\\/])*"
        r'(cmd|powershell|pwsh|bash|sh|wsl|zsh)(?:\.exe)?"?\b',
        re.IGNORECASE,
    )

    def __init__(self):
        self._monitors: dict[str, Monitor] = {}
        self._max_concurrent: int = 10
        # stopped 历史保留上限: 超过后淘汰最旧(保留 monitor_output 查询能力,
        # 同时防止自然退出条目无限积累)
        self._max_archived: int = 50
        # 匹配通知节流间隔(秒): 同一监控任务在此间隔内只通知一次,
        # 防止高频匹配时刷屏(匹配行仍全部记录到 matched_lines, 信息不丢)
        self._notify_interval: float = 10.0
        self._manager_lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "MonitorManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start_monitor(
        self,
        command: str,
        pattern: str,
        description: str,
        timeout: int,
        notify_model: bool = True,
        cwd: Path | None = None,
        config=None,
        visible: bool = False,
    ) -> str:
        """启动新进程监控

        Args:
            visible: 可视模式,弹出真实控制台窗口,用户可在窗口中查看输出并直接键入交互
        """
        # 验证正则表达式
        if pattern:
            try:
                re.compile(pattern)
            except re.error as e:
                return f"{TOOL_ERROR}: 无效的正则表达式 - {e}"

        # 多行命令不支持: create_subprocess_shell 在 Windows 上经 cmd.exe 执行,
        # 换行符会导致第二行及以后被静默截断, 提前拦截并明确报错
        if "\n" in command or "\r" in command:
            return (
                f"{TOOL_ERROR}: 命令不能包含换行符(多行命令不受支持)。\n"
                f"请改用单行命令(用 && 或 ; 连接多条语句),"
                f"或先写成脚本文件再执行。"
            )

        async with self._manager_lock:
            # 淘汰最旧的 stopped 条目: 保留历史查询能力同时防止无限积累。
            # 超过 MAX_ARCHIVED 个 stopped 时, 移除最旧的若干条
            stopped = [
                m for m in self._monitors.values() if m.status != MonitorStatus.RUNNING
            ]
            if len(stopped) > self._max_archived:
                for m in sorted(stopped, key=lambda x: x.start_time)[
                    : len(stopped) - self._max_archived
                ]:
                    self._monitors.pop(m.id, None)
            # 并发数检查: 只统计正在运行的进程。
            # 自然退出的 [stopped] 条目保留在 _monitors 供 monitor_output
            # 查询历史输出, 不计入并发数
            active = sum(
                1 for m in self._monitors.values() if m.status == MonitorStatus.RUNNING
            )
            if active >= self._max_concurrent:
                # 列出当前进程供 agent 决策关闭哪些
                info_lines = []
                for m in self._monitors.values():
                    uptime = int((datetime.now() - m.start_time).total_seconds())
                    info_lines.append(
                        f"  [{m.status.value}] {m.description or m.command[:30]} "
                        f"(ID:{m.id} | 运行:{uptime}s)"
                    )
                proc_list = "\n".join(info_lines)
                from .tools import monitor_stop

                return (
                    f"{TOOL_ERROR}: 已达到最大并发数({self._max_concurrent})\n"
                    f"当前进程列表:\n{proc_list}\n"
                    f"请先用 {monitor_stop.name} 关闭不需要的进程,再重新启动。"
                )

            monitor_id = uuid.uuid4().hex[:8]
            monitor = Monitor(
                monitor_id,
                command,
                pattern,
                description,
                timeout,
                notify_model,
                cwd,
                config=config,
                visible=visible,
            )

            # shell 类命令 + 可视模式 → ConPTY 伪控制台:
            # 提示符同行、原生回显、中文正常, 完整还原真实终端行为
            use_pty = (
                visible and os.name == "nt" and bool(self._SHELL_CMD_RE.match(command))
            )

            if use_pty:
                try:
                    from .pty_session import PtySession

                    session = PtySession(
                        command,
                        monitor,
                        asyncio.get_running_loop(),
                        self._notify_match,
                    )
                    monitor.pty = session
                except Exception as e:
                    return f"{TOOL_ERROR}: ConPTY 启动失败 - {e}"
                if visible:
                    self._setup_visible_paths(monitor)
                    session.raw_log_path = monitor.log_path
                    session.exit_path = monitor.exit_path
                self._monitors[monitor_id] = monitor
            else:
                # 异步创建子进程(管道模式)
                try:
                    creationflags = 0
                    if os.name == "nt":
                        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

                    process = await asyncio.create_subprocess_shell(
                        command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        stdin=asyncio.subprocess.PIPE,
                        cwd=str(cwd) if cwd else None,
                        limit=2**20,
                        **({"creationflags": creationflags} if creationflags else {}),
                    )
                except Exception as e:
                    return f"{TOOL_ERROR}: {e}"

                monitor.process = process
                if visible:
                    self._setup_visible_paths(monitor)
                self._monitors[monitor_id] = monitor

        if monitor.pty is not None:
            # ConPTY 模式: 启动读线程 + raw-terminal 查看器
            monitor.pty.start()
            visible_note = ""
            if visible:
                spawn_error = await self._spawn_viewer(monitor, raw_term=True)
                if spawn_error is None:
                    visible_note = (
                        "\n  可视窗口: 已弹出(ConPTY 终端模式,"
                        "用户可在窗口中直接操作)"
                    )
                else:
                    visible_note = (
                        f"\n  可视窗口: 弹出失败({spawn_error}),已回退为后台模式"
                    )
                monitor.viewer_thread = asyncio.create_task(
                    self._poll_user_input(monitor)
                )
        else:
            # 管道模式: 启动 stdout 和 stderr 读取任务
            monitor.stdout_thread = asyncio.create_task(self._read_output(monitor))
            monitor.stderr_thread = asyncio.create_task(
                self._read_output(monitor, is_stderr=True)
            )

            visible_note = ""
            if visible:
                spawn_error = await self._spawn_viewer(monitor)
                if spawn_error is None:
                    visible_note = (
                        "\n  可视窗口: 已弹出,用户可在窗口中查看输出并直接键入交互"
                    )
                else:
                    visible_note = (
                        f"\n  可视窗口: 弹出失败({spawn_error}),已回退为后台模式"
                    )
                monitor.viewer_thread = asyncio.create_task(
                    self._poll_user_input(monitor)
                )

        notify_info = ""
        if pattern:
            notify_info = (
                "(匹配时通知模型+桌面)" if notify_model else "(匹配时仅通知桌面)"
            )
        else:
            notify_info = "(仅记录输出)"

        desc_part = f" ({description})" if description else ""
        return (
            f"进程已启动\n"
            f"  ID: {monitor_id}\n"
            f"  命令: {command}{desc_part}\n"
            f"  匹配模式: {pattern or '无'}\n"
            f"  通知: {notify_info}"
            f"{visible_note}"
        )

    async def register_existing_process(
        self,
        process: asyncio.subprocess.Process,
        command: str,
        description: str = "",
        timeout: int = 0,
        cwd: Path | None = None,
        config=None,
    ) -> tuple[str, "Monitor"]:
        """将一个已运行的进程注册到监控系统。

        用于 Bash 超时后将仍在运行的进程转移到 monitor 管理。

        Args:
            process: 已在运行的 asyncio.subprocess.Process
            command: 原始命令字符串
            description: 进程描述
            timeout: 剩余超时时间(秒),0 表示不限制
            cwd: 工作目录
            config: AppConfig 实例

        Returns:
            tuple[str, Monitor]: (monitor_id, monitor 对象)
        """
        async with self._manager_lock:
            # 只统计 running 进程(与 start_monitor 一致),
            # 不让大量 stopped 条目阻塞 Bash 超时转交
            active = sum(
                1 for m in self._monitors.values() if m.status == MonitorStatus.RUNNING
            )
            if active >= self._max_concurrent:
                info_lines = []
                for m in self._monitors.values():
                    uptime = int((datetime.now() - m.start_time).total_seconds())
                    info_lines.append(
                        f"  [{m.status.value}] {m.description or m.command[:30]} "
                        f"(ID:{m.id} | 运行:{uptime}s)"
                    )
                proc_list = "\n".join(info_lines)
                raise RuntimeError(
                    f"已达到最大并发数({self._max_concurrent})\n"
                    f"当前进程列表:\n{proc_list}\n"
                    f"请先关闭不需要的进程。"
                )

            monitor_id = uuid.uuid4().hex[:8]
            monitor = Monitor(
                monitor_id,
                command,
                "",
                description,
                timeout,
                False,
                cwd,
                config=config,
            )
            monitor.process = process
            self._monitors[monitor_id] = monitor

        # 启动 stdout 读取任务
        monitor.stdout_thread = asyncio.create_task(self._read_output(monitor))

        # 如果 stderr 是独立 PIPE(非 STDOUT),启动单独的读取任务防止缓冲区满导致阻塞
        if process.stderr is not None and process.stderr is not process.stdout:
            monitor.stderr_thread = asyncio.create_task(
                self._read_output(monitor, is_stderr=True)
            )

        return monitor_id, monitor

    # ---------- 可视模式 ----------

    @staticmethod
    def _setup_visible_paths(monitor: Monitor):
        """初始化可视模式的镜像日志/输入/退出标记文件"""
        log_path, input_path, exit_path = viewer_paths(monitor.id)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_bytes(b"")
            input_path.write_bytes(b"")
            exit_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("初始化可视监控文件失败: %s", e)
        monitor.log_path = log_path
        monitor.input_path = input_path
        monitor.exit_path = exit_path

    async def _spawn_viewer(
        self, monitor: Monitor, raw_term: bool = False
    ) -> str | None:
        """弹出一个终端窗口运行查看器脚本。

        Args:
            raw_term: ConPTY 模式, 查看器以原始终端方式工作
                (输出透传 VT 流, 键盘逐字符转发)

        Returns:
            str | None: 失败原因,成功返回 None
        """
        try:
            script = ensure_viewer_script()
            label = (
                f"{monitor.description} (ID:{monitor.id})"
                if monitor.description
                else f"ID:{monitor.id}"
            )
            argv = build_viewer_argv(
                script,
                monitor.log_path,
                monitor.input_path,
                monitor.exit_path,
                label,
                no_echo=raw_term or bool(self._SHELL_CMD_RE.match(monitor.command)),
                raw_term=raw_term,
                size=(120, 30) if raw_term else None,
            )
            if raw_term and os.name == "nt":
                # conhost 包裹: 强制经典独立控制台窗口。
                # Win11 默认终端(WT)会把 CREATE_NEW_CONSOLE 合并为标签,
                # viewer 窗口容易被已有窗口吞掉而不可见
                argv = ["conhost.exe"] + argv
            kwargs: dict = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
            else:
                terminal = self._find_terminal()
                if terminal is None:
                    return "未找到可用的终端模拟器"
                argv = terminal + argv
                kwargs["start_new_session"] = True
            monitor.viewer_process = await asyncio.create_subprocess_exec(
                *argv, **kwargs
            )
        except Exception as e:
            return str(e)
        return None

    @staticmethod
    def _find_terminal() -> list[str] | None:
        """查找可用的终端模拟器(仅非 Windows 需要),返回 [程序, 执行参数] 前缀"""
        for name, flag in (
            ("x-terminal-emulator", "-e"),
            ("gnome-terminal", "--"),
            ("konsole", "-e"),
            ("xfce4-terminal", "-x"),
            ("xterm", "-e"),
        ):
            path = shutil.which(name)
            if path:
                return [path, flag]
        return None

    @staticmethod
    def _touch_exit(monitor: Monitor):
        """写入退出标记文件,通知查看器窗口关闭"""
        if monitor.exit_path is None:
            return
        try:
            monitor.exit_path.touch()
        except OSError:
            pass

    @staticmethod
    def _schedule_viewer_cleanup(monitor: Monitor):
        """进程自然退出后, 延迟清理可视模式临时文件(等查看器自行关闭)"""
        paths = [
            monitor.log_path,
            monitor.input_path,
            monitor.exit_path,
            monitor.input_path.with_suffix(".size") if monitor.input_path else None,
        ]
        asyncio.create_task(MonitorManager._cleanup_visible_files(paths))

    async def _poll_user_input(self, monitor: Monitor):
        """轮询查看器窗口的用户输入文件并转发到进程 stdin

        - ConPTY 模式: 原始字节透传(保留方向键等 VT 序列)
        - 管道模式: 按行读取转发
        - ConPTY 模式额外处理 .size 文件: 用户拖动 viewer 窗口后
          同步 ConPTY 尺寸(setwinsize), 保持两端一致
        """
        pos = 0
        last_size_txt: str | None = None
        try:
            while True:
                await asyncio.sleep(0.2)
                # 进程存活检测:
                # - ConPTY: 读线程 EOF 时置位 exited 事件, 此处只查内存标志。
                #   严禁高频调用 proc.isalive()——它与 ConPTY teardown 竞争
                #   会概率性死锁子进程的 ExitProcess(cmd exit 挂死)
                # - 管道模式: returncode 判定(asyncio subprocess, 无此问题)
                if monitor.pty is not None:
                    if monitor.pty.exited.is_set():
                        self._touch_exit(monitor)
                        self._schedule_viewer_cleanup(monitor)
                        break
                else:
                    if monitor.process.returncode is not None:
                        self._touch_exit(monitor)
                        self._schedule_viewer_cleanup(monitor)
                        break
                # 用户关闭 viewer 窗口(X 按钮): ConPTY 输出无消费者,
                # cmd 随 conhost 一起退出但读线程可能卡死(read 阻塞),
                # 需主动终止会话并清理, 否则 OpenConsole 进程泄漏、
                # monitor 永远显示 [running] 且文件残留
                if (
                    monitor.pty is not None
                    and monitor.viewer_process is not None
                    and monitor.viewer_process.returncode is not None
                ):
                    try:
                        monitor.pty.terminate()
                    except Exception:
                        pass
                    self._touch_exit(monitor)
                    self._schedule_viewer_cleanup(monitor)
                    break
                # ConPTY 模式超时检查: 管道模式的超时由 _read_output 的
                # deadline 处理, 此处补上可视模式(ConPTY)的对应逻辑,
                # 超时后终止会话并标记 TIMEOUT, 避免 shell 无限运行
                if monitor.timeout > 0 and monitor.pty is not None:
                    elapsed = (datetime.now() - monitor.start_time).total_seconds()
                    if elapsed > monitor.timeout:
                        monitor.status = MonitorStatus.TIMEOUT
                        try:
                            monitor.pty.terminate()
                        except Exception:
                            pass
                        self._touch_exit(monitor)
                        self._schedule_viewer_cleanup(monitor)
                        break
                path = monitor.input_path
                if path is None:
                    break
                # 用户调整了 viewer 窗口 -> 同步 ConPTY 尺寸
                if monitor.pty is not None and monitor.visible:
                    size_file = path.with_suffix(".size")
                    try:
                        txt = size_file.read_text(encoding="utf-8").strip()
                    except OSError:
                        txt = ""
                    if txt and txt != last_size_txt:
                        try:
                            cols_s, rows_s = txt.split("x", 1)
                            cols, rows = int(cols_s), int(rows_s)
                            if cols > 0 and rows > 0:
                                monitor.pty.proc.setwinsize(rows, cols)
                                # 屏幕网格同步新尺寸, 抓屏结果与窗口一致
                                monitor.pty.resize_screen(rows, cols)
                                last_size_txt = txt
                        except Exception:
                            pass
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size < pos:
                    pos = 0  # 文件被重建
                if size <= pos:
                    continue
                if monitor.pty is not None:
                    # 原始字节透传(键盘逐字符, 含 VT 序列)
                    try:
                        with open(path, "rb") as f:
                            f.seek(pos)
                            data = f.read()
                            pos = f.tell()
                    except OSError:
                        continue
                    if data:
                        monitor.pty.write(data)
                else:
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(pos)
                            data = f.read()
                            pos = f.tell()
                    except OSError:
                        continue
                    for line in data.splitlines():
                        await self._write_stdin(monitor, line)

            # 进程自然结束: 保留在 _monitors(状态 STOPPED)供 monitor_output
            # 查询历史输出; 并发计数已改为只统计 RUNNING, 不会误判。
            # 大量 stopped 条目的清理由 start_monitor 的淘汰逻辑负责。
            if monitor.status == MonitorStatus.RUNNING:
                monitor.status = MonitorStatus.STOPPED
        except asyncio.CancelledError:
            pass

    @staticmethod
    async def _cleanup_visible_files(paths):
        """延迟清理可视模式的临时文件(等待查看器进程退出释放句柄)"""
        await asyncio.sleep(3)
        for path in paths:
            if path is None:
                continue
            try:
                path.unlink()
            except OSError:
                pass

    @staticmethod
    def _decode_output(raw: bytes) -> str:
        """解码进程输出字节流。

        优先 UTF-8(Python 等现代程序), 失败退回 GBK(cmd 等原生命令在中文
        Windows 上按系统代码页输出), 仍失败则用替换字符兜底。
        """
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return raw.decode("gbk")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace")

    async def _read_output(self, monitor: Monitor, is_stderr: bool = False):
        """异步读取进程输出并匹配模式。

        使用分块读取而非 readline():交互式程序(如 cmd/bash)输出的提示符
        不带换行符,readline() 会将其永久缓冲导致提示符无法及时显示;
        分块读取配合短暂超时可将这类残留内容及时刷出。

        Args:
            monitor: 监控对象
            is_stderr: 是否读取 stderr 流,默认 False(读取 stdout)
        """
        stream = monitor.process.stderr if is_stderr else monitor.process.stdout
        prefix = "[stderr] " if is_stderr else ""

        deadline = None
        if monitor.timeout > 0:
            deadline = asyncio.get_event_loop().time() + monitor.timeout

        async def _emit(raw: str):
            """处理并记录一段输出(完整行或不完整残留)"""
            line = sanitize_progress_line(raw).rstrip()
            if not line:
                return
            output_line = f"{prefix}{line}"

            # 输出缓冲区达到上限(1000行)时插入一次性警告, 提醒用户尾部可能会丢失
            if (
                len(monitor.output_lines) == monitor.output_lines.maxlen
                and not monitor.buffer_full_warned
            ):
                monitor.buffer_full_warned = True
                monitor.output_lines.append(
                    "⚠️ 输出缓冲区已满(1000行), 最旧的输出将被丢弃"
                )
            monitor.output_lines.append(output_line)

            # 可视模式:镜像到日志文件供查看器窗口实时显示
            if monitor.visible and monitor.log_path:
                try:
                    with open(monitor.log_path, "a", encoding="utf-8") as f:
                        f.write(output_line + "\n")
                except OSError:
                    pass

            # 检查匹配
            if monitor.pattern and re.search(monitor.pattern, line):
                monitor.matched_lines.append(line)
                monitor.match_time = datetime.now()
                monitor.status = MonitorStatus.MATCHED
                await self._notify_match(monitor, line)
            elif monitor.status == MonitorStatus.MATCHED:
                # MATCHED 是准瞬态: 后续未匹配输出恢复 RUNNING
                monitor.status = MonitorStatus.RUNNING

        try:
            buf = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(stream.read(8192), timeout=0.5)
                except asyncio.TimeoutError:
                    # 短暂无新数据:把残留的不完整行(如交互式提示符)先刷出
                    if buf:
                        await _emit(self._decode_output(buf))
                        buf = b""
                    if deadline and asyncio.get_event_loop().time() > deadline:
                        monitor.status = MonitorStatus.TIMEOUT
                        break
                    continue
                if not chunk:
                    break
                buf += chunk
                # 按 \n 切分完整行; \r\n 的 \r 在此剥掉,避免被
                # sanitize_progress_line 误判为进度条回车而清空整行。
                # 按字节切分保证多字节字符跨块时不会解码出错。
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    if raw_line.endswith(b"\r"):
                        raw_line = raw_line[:-1]
                    await _emit(self._decode_output(raw_line))
                if deadline and asyncio.get_event_loop().time() > deadline:
                    monitor.status = MonitorStatus.TIMEOUT
                    break

            # 进程结束前刷出剩余缓冲
            if buf:
                await _emit(self._decode_output(buf))

            # 进程正常结束(仅 stdout 任务负责更新状态,避免竞争)
            # 包含 MATCHED: 匹配后若进程随即结束, 状态不得卡在 matched,
            # 应归为 stopped(否则 monitor_list 出现永久 [matched] 僵尸条目)
            if not is_stderr and monitor.status in (
                MonitorStatus.RUNNING,
                MonitorStatus.MATCHED,
            ):
                monitor.status = MonitorStatus.STOPPED
                if monitor.visible:
                    self._touch_exit(monitor)
            # 自然退出的条目保留在 _monitors(状态 STOPPED)供 monitor_output
            # 查询历史输出; 并发计数只统计 RUNNING, stopped 不会阻塞新启动,
            # 大量 stopped 由 start_monitor 的淘汰逻辑(MAX_ARCHIVED)清理

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(
                "读取进程 %s 输出失败: %s", "stderr" if is_stderr else "stdout", e
            )
            if monitor.status == MonitorStatus.RUNNING:
                monitor.status = MonitorStatus.ERROR

    async def _notify_match(self, monitor: Monitor, line: str):
        """匹配成功时通知用户和模型(带节流, 防止高频匹配刷屏)"""
        # 节流: 同一监控任务在 _notify_interval 秒内只通知一次。
        # 匹配行仍全部记录到 matched_lines, 信息不丢失, 只是降低主动通知频率。
        now = datetime.now()
        if (
            monitor.last_notify_time is not None
            and (now - monitor.last_notify_time).total_seconds() < self._notify_interval
        ):
            return
        monitor.last_notify_time = now

        # 1. 发送桌面通知给用户
        try:
            from ..notify import push_notification

            desc = f" [{monitor.description}]" if monitor.description else ""
            msg = f"监控{desc}匹配到: {line[:100]}"
            await push_notification(message=msg, title="UniClaw 监控")
        except Exception as e:
            logger.warning("发送桌面通知失败: %s", e)

        # 2. 通知模型(使用 wake_agent 统一唤醒逻辑)
        if monitor.notify_model and monitor.config:
            try:
                from uniclaw.utils.wakeup import wake_agent
                from uniclaw.utils.constants import SYSTEM_PREFIX

                desc = (
                    f"[{monitor.description}]"
                    if monitor.description
                    else f"[监控 {monitor.id}]"
                )
                message = (
                    f"{SYSTEM_PREFIX}(monitor) {desc} 监控匹配成功！\n"
                    f"  匹配模式: {monitor.pattern}\n"
                    f"  匹配内容: {line}\n"
                    f"  监控 ID: {monitor.id}\n"
                    f"请根据匹配结果继续处理。"
                )
                await wake_agent(message, monitor.config)
            except Exception as e:
                logger.warning("唤醒模型失败: %s", e)

    async def stop_monitor(self, monitor_id: str) -> str:
        """停止进程(杀掉整棵进程树)"""
        async with self._manager_lock:
            monitor = self._monitors.get(monitor_id)
            if not monitor:
                return f"{TOOL_ERROR}: 进程 '{monitor_id}' 不存在"

            monitor.status = MonitorStatus.STOPPED
            process = monitor.process
            stdout_task = monitor.stdout_thread
            stderr_task = monitor.stderr_thread
            viewer_process = monitor.viewer_process
            input_task = monitor.viewer_thread
            visible_files = (
                (
                    monitor.log_path,
                    monitor.input_path,
                    monitor.exit_path,
                    (
                        monitor.input_path.with_suffix(".size")
                        if monitor.input_path
                        else None
                    ),
                )
                if monitor.visible
                else ()
            )
            del self._monitors[monitor_id]

        # 取消读取任务
        if stdout_task and not stdout_task.done():
            stdout_task.cancel()
        if stderr_task and not stderr_task.done():
            stderr_task.cancel()
        if input_task and not input_task.done():
            input_task.cancel()

        # 可视模式:先写退出标记让查看器窗口自行关闭,超时再强杀
        if visible_files:
            self._touch_exit(monitor)
            if viewer_process and viewer_process.returncode is None:
                try:
                    await asyncio.wait_for(viewer_process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    await self._kill_process_tree(viewer_process)
            asyncio.create_task(self._cleanup_visible_files(visible_files))

        # 终止主进程: ConPTY 会话或管道进程树
        if monitor.pty is not None:
            monitor.pty.terminate()
        elif process and process.returncode is None:
            await self._kill_process_tree(process)

        return f"进程已停止: {monitor_id}"

    @staticmethod
    async def _kill_process_tree(process):
        """异步杀掉进程及其所有子进程"""
        try:
            if os.name == "nt":
                # Windows: 用 taskkill /T 杀整棵树, /F 强制
                proc = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(process.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=5)
            else:
                # Unix: 发 SIGKILL 给进程组
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception as e:
            logger.warning("终止进程树失败,尝试直接 kill: %s", e)
            try:
                process.kill()
            except Exception as e2:
                logger.warning("直接 kill 进程失败: %s", e2)

    async def clear_monitors(self) -> str:
        """批量清理所有已结束的监控条目(保留 running)。

        用于历史进程累积时手动清理: 自然退出/超时/异常/匹配态等
        非运行条目会从 _monitors 移除, 释放列表空间。
        """
        async with self._manager_lock:
            removed = [
                mid
                for mid, m in self._monitors.items()
                if m.status != MonitorStatus.RUNNING
            ]
            for mid in removed:
                self._monitors.pop(mid, None)

        if not removed:
            return "没有可清理的已结束进程。"

        return f"已清理 {len(removed)} 个已结束进程:\n" + "\n".join(
            f"  {mid}" for mid in removed
        )

    async def list_monitors(self) -> str:
        """列出所有进程"""
        async with self._manager_lock:
            monitors = list(self._monitors.values())

        if not monitors:
            return "当前没有运行中的进程。"

        lines = [f"共 {len(monitors)} 个进程:"]
        for m in monitors:
            info = m.to_dict()
            uptime = f"{info['uptime_seconds']}s"
            lines.append(
                f"  [{info['status']}] {info['description'] or info['command'][:30]} "
                f"(ID:{info['id']} | 运行:{uptime} | 输出:{info['output_lines']}行)"
            )
        return "\n".join(lines)

    async def get_output(self, monitor_id: str, lines: int = 50) -> str:
        """获取进程输出"""
        async with self._manager_lock:
            monitor = self._monitors.get(monitor_id)
            if not monitor:
                return f"{TOOL_ERROR}: 进程 '{monitor_id}' 不存在"

            if not monitor.output_lines:
                return f"进程 {monitor_id} 暂无输出。"

            n = max(lines, 0)
            output = list(monitor.output_lines)[-n:] if n > 0 else []
            header = f"[{monitor.id}] {monitor.description or monitor.command[:50]}"
            return f"{header}\n" + "\n".join(output)

    async def get_screen(self, monitor_id: str) -> str:
        """获取控制台当前屏幕的全部内容(整屏快照)

        ConPTY 会话(可视模式的 shell 类进程)返回此刻可见画面,
        包含提示符同行残留、未回车的键入和 TUI 全屏界面;
        管道模式进程没有真实控制台, 用全部输出历史代替。
        """
        async with self._manager_lock:
            monitor = self._monitors.get(monitor_id)
            if not monitor:
                return f"{TOOL_ERROR}: 进程 '{monitor_id}' 不存在"

            header = f"[{monitor.id}] {monitor.description or monitor.command[:50]}"
            if monitor.pty is not None:
                body = monitor.pty.screen_text()
                pipe_fallback = False
            else:
                body = "\n".join(list(monitor.output_lines))
                pipe_fallback = True

        if pipe_fallback:
            if not body.strip():
                return f"{header}\n暂无输出。"
            return f"{header} (管道模式无真实控制台, 返回全部输出历史):\n{body}"

        if not body.strip():
            return f"{header}\n屏幕空白。"
        return f"{header} 当前屏幕:\n{body}"

    async def send_input(self, monitor_id: str, input_text: str) -> str:
        """向进程发送输入"""
        async with self._manager_lock:
            monitor = self._monitors.get(monitor_id)
            if not monitor:
                return f"{TOOL_ERROR}: 进程 '{monitor_id}' 不存在"

            if monitor.pty is not None:
                if monitor.pty.exited.is_set():
                    return f"{TOOL_ERROR}: 进程 {monitor_id} 已结束,无法发送输入"
                try:
                    # ConPTY: UTF-8 文本 + 回车(伪控制台原生处理行编辑)
                    monitor.pty.proc.write(input_text + "\r")
                    return f"已向进程 {monitor_id} 发送输入: {input_text}"
                except Exception as e:
                    return f"{TOOL_ERROR}: {e}"

            if not monitor.process or monitor.process.returncode is not None:
                return f"{TOOL_ERROR}: 进程 {monitor_id} 已结束,无法发送输入"

            try:
                await self._write_stdin(monitor, input_text)
                return f"已向进程 {monitor_id} 发送输入: {input_text}"
            except Exception as e:
                return f"{TOOL_ERROR}: {e}"

    @staticmethod
    async def _write_stdin(monitor: Monitor, input_text: str):
        """写入一行文本到进程 stdin(自动添加换行符)。

        用 UTF-8 编码发送: Python 3.14+ 默认 UTF-8 模式(PEP 686),
        cmd 等原生命令按系统代码页解析但 UTF-8 字节对 ASCII 无影响。
        """
        monitor.process.stdin.write(
            (input_text + "\n").encode("utf-8", errors="replace")
        )
        await monitor.process.stdin.drain()

    async def update_pattern(self, monitor_id: str, new_pattern: str) -> str:
        """修改进程的匹配模式"""
        # 验证正则表达式
        if new_pattern:
            try:
                re.compile(new_pattern)
            except re.error as e:
                return f"{TOOL_ERROR}: 无效的正则表达式 - {e}"

        async with self._manager_lock:
            monitor = self._monitors.get(monitor_id)
            if not monitor:
                return f"{TOOL_ERROR}: 进程 '{monitor_id}' 不存在"

            old_pattern = monitor.pattern or "无"
            monitor.pattern = new_pattern

        notify_info = ""
        if new_pattern:
            notify_info = (
                "(匹配时通知)" if monitor.notify_model else "(匹配时仅通知桌面)"
            )
        else:
            notify_info = "(仅记录输出)"

        return (
            f"进程 {monitor_id} 匹配模式已更新\n"
            f"  旧模式: {old_pattern}\n"
            f"  新模式: {new_pattern or '无'}\n"
            f"  通知: {notify_info}"
        )

    async def get_matched(self, monitor_id: str) -> str:
        """获取匹配结果"""
        async with self._manager_lock:
            monitor = self._monitors.get(monitor_id)
            if not monitor:
                return f"{TOOL_ERROR}: 进程 '{monitor_id}' 不存在"

            if not monitor.matched_lines:
                return f"进程 {monitor_id} 尚未匹配到任何内容。"

            lines = [f"进程 {monitor_id} 匹配到 {len(monitor.matched_lines)} 行:"]
            # deque 不支持切片, 转 list 取最后 20 条
            for line in list(monitor.matched_lines)[-20:]:
                lines.append(f"  {line}")
            return "\n".join(lines)
