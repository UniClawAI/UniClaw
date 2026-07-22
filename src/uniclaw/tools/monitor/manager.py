import asyncio
import os
import re
import signal
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR
from uniclaw.utils.format import sanitize_progress_line

from .models import Monitor, MonitorStatus


class MonitorManager:
    """全局进程监控管理器(单例)"""

    _instance: "MonitorManager | None" = None

    def __init__(self):
        self._monitors: dict[str, Monitor] = {}
        self._max_concurrent: int = 10
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
        task=None,
        cwd: Path | None = None,
    ) -> str:
        """启动新进程监控"""
        # 验证正则表达式
        if pattern:
            try:
                re.compile(pattern)
            except re.error as e:
                return f"{TOOL_ERROR}: 无效的正则表达式 - {e}"

        async with self._manager_lock:
            # 先检查并发数,再创建进程
            if len(self._monitors) >= self._max_concurrent:
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

            # 异步创建子进程
            try:
                creationflags = 0
                if os.name == "nt":
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.PIPE,
                    cwd=str(cwd) if cwd else None,
                    limit=2**20,
                    **({"creationflags": creationflags} if creationflags else {}),
                )
            except Exception as e:
                return f"{TOOL_ERROR}: {e}"

            monitor_id = uuid.uuid4().hex[:8]
            monitor = Monitor(
                monitor_id, command, pattern, description, timeout, notify_model, cwd
            )
            monitor._task = task
            monitor.process = process
            self._monitors[monitor_id] = monitor

        # 启动异步读取任务
        monitor.thread = asyncio.create_task(self._read_output(monitor))

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
        )

    async def _read_output(self, monitor: Monitor):
        """异步读取进程输出并匹配模式"""
        deadline = None
        if monitor.timeout > 0:
            deadline = asyncio.get_event_loop().time() + monitor.timeout

        try:
            while True:
                line = await monitor.process.stdout.readline()
                if not line:
                    break

                line = sanitize_progress_line(line.decode(errors="replace"))
                line = line.strip()
                if not line:
                    continue

                # 保存输出
                monitor.output_lines.append(line)

                # 检查是否匹配
                if monitor.pattern and re.search(monitor.pattern, line):
                    monitor.matched_lines.append(line)
                    monitor.match_time = datetime.now()
                    monitor.status = MonitorStatus.MATCHED
                    await self._notify_match(monitor, line)

                # 检查超时
                if deadline and asyncio.get_event_loop().time() > deadline:
                    monitor.status = MonitorStatus.TIMEOUT
                    break

            # 进程正常结束
            if monitor.status == MonitorStatus.RUNNING:
                monitor.status = MonitorStatus.STOPPED

        except asyncio.CancelledError:
            pass
        except Exception:
            if monitor.status == MonitorStatus.RUNNING:
                monitor.status = MonitorStatus.ERROR

        finally:
            # 确保进程已结束
            if monitor.process and monitor.process.returncode is None:
                await self._kill_process_tree(monitor.process)

    async def _notify_match(self, monitor: Monitor, line: str):
        """匹配成功时通知用户和模型"""
        # 1. 发送桌面通知给用户
        try:
            from ..notify import push_notification

            desc = f" [{monitor.description}]" if monitor.description else ""
            msg = f"监控{desc}匹配到: {line[:100]}"
            await push_notification(message=msg, title="UniClaw 监控")
        except Exception:
            pass

        # 2. 通知模型
        if monitor.notify_model and monitor._task:
            try:
                desc = (
                    f"[{monitor.description}]"
                    if monitor.description
                    else f"[监控 {monitor.id}]"
                )
                system_msg = (
                    f"{SYSTEM_PREFIX}(monitor) {desc} 监控匹配成功！\n"
                    f"  匹配模式: {monitor.pattern}\n"
                    f"  匹配内容: {line}\n"
                    f"  监控 ID: {monitor.id}\n"
                    f"请根据匹配结果继续处理。"
                )
                monitor._task.user_queue.put_nowait(system_msg)
            except Exception:
                pass

    async def stop_monitor(self, monitor_id: str) -> str:
        """停止进程(杀掉整棵进程树)"""
        async with self._manager_lock:
            monitor = self._monitors.get(monitor_id)
            if not monitor:
                return f"{TOOL_ERROR}: 进程 '{monitor_id}' 不存在"

            monitor.status = MonitorStatus.STOPPED
            process = monitor.process
            task = monitor.thread
            del self._monitors[monitor_id]

        # 取消读取任务
        if task and not task.done():
            task.cancel()

        # 异步杀进程
        if process and process.returncode is None:
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
        except Exception:
            # 兜底: 直接 kill 主进程
            try:
                process.kill()
            except Exception:
                pass

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

            output = list(monitor.output_lines)[-lines:]
            header = f"[{monitor.id}] {monitor.description or monitor.command[:50]}"
            return f"{header}\n" + "\n".join(output)

    async def send_input(self, monitor_id: str, input_text: str) -> str:
        """向进程发送输入"""
        async with self._manager_lock:
            monitor = self._monitors.get(monitor_id)
            if not monitor:
                return f"{TOOL_ERROR}: 进程 '{monitor_id}' 不存在"

            if not monitor.process or monitor.process.returncode is not None:
                return f"{TOOL_ERROR}: 进程 {monitor_id} 已结束,无法发送输入"

            try:
                monitor.process.stdin.write((input_text + "\n").encode())
                await monitor.process.stdin.drain()
                return f"已向进程 {monitor_id} 发送输入: {input_text}"
            except Exception as e:
                return f"{TOOL_ERROR}: {e}"

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
            for line in monitor.matched_lines[-20:]:
                lines.append(f"  {line}")
            return "\n".join(lines)
