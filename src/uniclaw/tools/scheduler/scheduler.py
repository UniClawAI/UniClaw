"""定时任务调度器 — 后台守护线程,定期检查并执行到期任务"""

import asyncio
import contextlib
import io
import json
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from croniter import croniter

from uniclaw.config import AppConfig, RunMode
from uniclaw.console.ui import info, warn, err
from uniclaw.context import get_app_dir, Scope


@dataclass
class Task:
    """定时任务数据"""

    id: str
    name: str
    schedule: str
    action: str
    root_dir: str = ""
    enabled: bool = True
    session_id: str | None = None
    last_run: str | None = None
    created: str | None = None
    updated: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            schedule=data.get("schedule", ""),
            action=data.get("action", ""),
            root_dir=data.get("root_dir", ""),
            enabled=data.get("enabled", True),
            session_id=data.get("session_id"),
            last_run=data.get("last_run"),
            created=data.get("created"),
            updated=data.get("updated"),
        )


def _parse_cron(cron_str: str) -> croniter:
    """解析 Cron 表达式

    支持标准 5 字段格式: 分 时 日 月 周
    最小粒度为 1 分钟,不支持秒级调度

    Returns:
        croniter: 解析后的 croniter 对象

    Raises:
        ValueError: 当 Cron 表达式无效时
    """
    s = cron_str.strip()
    try:
        return croniter(s)
    except (ValueError, KeyError) as e:
        raise ValueError(f"无效的 Cron 表达式: '{cron_str}'") from e


class Scheduler:
    """定时任务调度器(单例)"""

    _instance: "Scheduler | None" = None
    _lock = threading.Lock()

    def __init__(self):
        self._config_path: Path = (
            get_app_dir(Scope.USER) / "schedule" / "scheduler.json"
        )
        self._tasks: dict[str, Task] = {}
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    @classmethod
    def get_instance(cls) -> "Scheduler":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 配置 CRUD ──────────────────────────────────────────────────

    def load_config(self):
        """从 JSON 文件加载任务,转为 Task 对象"""
        if not self._config_path.exists():
            self._tasks = {}
            return
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
            tasks_raw = raw.get("tasks", {})
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: [scheduler] 加载配置失败: {e}")
            tasks_raw = {}
        self._tasks = {tid: Task.from_dict(data) for tid, data in tasks_raw.items()}

    def save_config(self):
        """将 Task 对象序列化为 JSON 写入文件"""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"tasks": {tid: task.to_dict() for tid, task in self._tasks.items()}}
        self._config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_task(
        self,
        name: str,
        schedule: str,
        action: str,
        unique_by_name: bool = False,
        config: AppConfig | None = None,
    ) -> Task:
        """添加任务,自动生成 UUID 作为任务 ID,并分配独立工作目录。

        schedule 格式为 Cron 表达式,例如:
        - '* * * * *' — 每分钟
        - '*/5 * * * *' — 每 5 分钟
        - '0 9 * * *' — 每天 9:00
        - '0 9 * * 1-5' — 工作日 9:00

        Returns:
            Task: 成功时返回任务对象

        Raises:
            ValueError: 当 Cron 表达式无效时
        """
        self.load_config()
        _parse_cron(schedule)
        now = datetime.now().isoformat(timespec="seconds")

        if unique_by_name and name:
            matches = [tid for tid, t in self._tasks.items() if t.name == name]
            if matches:
                primary = matches[0]
                task = self._tasks[primary]
                task.name = name
                task.schedule = schedule
                task.action = action
                task.enabled = True
                task.updated = now
                for duplicate in matches[1:]:
                    del self._tasks[duplicate]
                self.save_config()
                return task

        task_id = uuid.uuid4().hex[:8]
        session_id = None
        root_dir = ""
        if config:
            root_dir = str(config.root_dir or "")
            if config.current_agent:
                session_id = config.current_agent.session.id
        task = Task(
            id=task_id,
            name=name or task_id,
            schedule=schedule,
            action=action,
            root_dir=root_dir,
            enabled=True,
            session_id=session_id,
            last_run=now if unique_by_name else None,
            created=now,
        )
        self._tasks[task_id] = task
        self.save_config()
        return task

    def remove_task(self, task_id: str) -> bool:
        self.load_config()
        if task_id not in self._tasks:
            return False
        del self._tasks[task_id]
        self.save_config()
        return True

    def list_tasks(self) -> list[dict]:
        self.load_config()
        return [{"id": tid, **task.to_dict()} for tid, task in self._tasks.items()]

    def get_task(self, task_id: str) -> Task | None:
        """获取单个任务。"""
        self.load_config()
        return self._tasks.get(task_id)

    def update_action(self, task_id: str, action: str) -> bool:
        """更新任务的 action。"""
        self.load_config()
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.action = action
        task.updated = datetime.now().isoformat(timespec="seconds")
        self.save_config()
        return True

    def update_schedule(self, task_id: str, schedule: str) -> bool:
        """更新任务的调度时间。"""
        self.load_config()
        task = self._tasks.get(task_id)
        if task is None:
            return False
        _parse_cron(schedule)
        task.schedule = schedule
        task.updated = datetime.now().isoformat(timespec="seconds")
        self.save_config()
        return True

    def toggle_task(self, task_id: str, enabled: bool) -> bool:
        self.load_config()
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.enabled = enabled
        self.save_config()
        return True

    # ── 后台调度 ──────────────────────────────────────────────────

    async def start(self, config: AppConfig | None = None):
        """启动后台调度任务"""
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(config))
        await info("[scheduler] 定时任务调度器已启动", config)

    def stop(self):
        """停止调度器"""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    async def _run_loop(self, config: AppConfig | None = None):
        """后台循环:每 10 秒检查一次到期任务"""
        while not self._stop_event.is_set():
            try:
                await self._check_and_run_tasks(config)
            except Exception as e:
                await err(f"[scheduler] 调度器检查失败: {e}", config)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    async def _check_and_run_tasks(self, config: AppConfig | None = None):
        """检查所有任务,执行到期的任务"""
        self.load_config()
        now = datetime.now()
        changed = False
        pending = []

        for task_id, task in self._tasks.items():
            if not task.enabled:
                continue

            cron = _parse_cron(task.schedule)
            if cron is None:
                continue

            if task.last_run is None:
                # 首次运行,计算上一次应该运行的时间
                prev_time = cron.get_prev(datetime)
                should_run = prev_time <= now
            else:
                last_dt = datetime.fromisoformat(task.last_run)
                next_time = cron.get_next(datetime, start_time=last_dt)
                should_run = next_time <= now

            if should_run:
                task.last_run = now.isoformat(timespec="seconds")
                changed = True
                pending.append(
                    asyncio.create_task(self._execute_task(task_id, task, config))
                )

        if changed:
            self.save_config()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _run_agent(
        self,
        message: str,
        task_name: str,
        session_id: str | None,
        config: AppConfig | None = None,
    ):
        """复用当前会话执行 agent 消息。

        - TUI 模式且当前会话 != 任务会话 → 后台执行
        - 其他情况 → 注入消息到 user_queue
        """
        from uniclaw.utils.constants import SYSTEM_PREFIX

        config = await self._find_config(session_id, config)
        if config is None:
            await err(
                f"[{task_name}] 会话 {session_id or '(无)'} 不存在,已跳过", config
            )
            return

        # TUI 模式:检查当前活跃会话是否就是任务的会话
        is_tui = config.run_mode == RunMode.CONSOLE
        if is_tui:
            active_id = self._get_active_session_id()
            if active_id and active_id != session_id:
                # 当前会话不匹配 → 后台执行
                await self._run_in_background(config, message, task_name)
                return

        # agent 运行中:注入队列;空闲:start_agent
        from uniclaw.agent import MultiAgent, AgentStatus

        full_message = f"{SYSTEM_PREFIX}(scheduler:{task_name})\n{message}"
        if config.current_agent.status == AgentStatus.RUNNING:
            config.current_agent.user_queue.put_nowait(full_message)
        else:
            multi_agent = MultiAgent.get_instance()
            
            # WebUI 模式需要启动 bridge 才能把事件推到前端
            if config.run_mode == RunMode.WEBUI:
                try:
                    from uniclaw.webui.ws import _start_bridge

                    await _start_bridge(session_id, config)
                except ImportError:
                    pass
            multi_agent.start_agent(full_message, config=config)

    async def _find_config(
        self, session_id: str | None, config: AppConfig | None = None
    ):
        """根据 session_id 查找对应的 AppConfig。

        根据启动模式选择加载方式:
        - WebUI: 使用 get_or_load_session
        - TUI: 当前会话匹配则直接返回,否则从磁盘加载
        """
        if not session_id:
            return None
        from uniclaw.console.run import TUIApp
        tui = TUIApp.get_instance()
        # WebUI 模式:使用 get_or_load_session
        is_webui = tui is None
        if is_webui:
            try:
                from uniclaw.webui.ws import get_or_load_session

                return await get_or_load_session(session_id)
            except Exception:
                pass

        else:
            try:
                if tui and tui.config:
                    tui_session_id = tui.config.current_agent.session.id
                    if tui_session_id == session_id:
                        return tui.config
            except Exception:
                pass

        # 从磁盘加载 session
        from uniclaw.tools.session.session_manager import SessionManager
        from uniclaw.config import load_config
        from uniclaw.spinner import NoopSpinner

        session = SessionManager.load_session(session_id)
        if session:
            return load_config(session=session, spinner=NoopSpinner())

        return None

    def _get_active_session_id(self) -> str | None:
        """获取当前 UI 活跃的会话 ID(仅 TUI)。"""
        try:
            from uniclaw.console.run import TUIApp

            tui = TUIApp.get_instance()
            if tui and tui.config:
                return tui.config.current_agent.session.id
        except Exception:
            pass
        return None

    async def _run_in_background(self, config: AppConfig, message: str, task_name: str):
        """后台执行定时任务(TUI 当前会话不匹配时)。"""
        from uniclaw.agent import MultiAgent
        from uniclaw.tools.notify import push_notification

        multi_agent = MultiAgent.get_instance()
        agent_task = multi_agent.start_agent(message, config=config)
        await multi_agent.wait(agent_task.id, timeout=300)
        if agent_task.result:
            await info(f"[{task_name}] {agent_task.result}")
            await push_notification(f"{task_name} 执行完成", title="定时任务")

    async def _execute_task(
        self, task_id: str, task: Task, config: AppConfig | None = None
    ):
        """执行单个任务(JSON 格式)。"""
        action = task.action
        name = task.name or task_id
        await info(f"[scheduler] 执行任务: {name}", config)

        try:
            data = json.loads(action)
            action_type = data["type"]

            if action_type == "shell":
                await self._exec_shell(data["command"], task, config)

            elif action_type == "agent":
                await self._run_agent(
                    data["message"],
                    f"scheduler:{name}",
                    task.session_id,
                    config,
                )

            elif action_type == "monitor":
                await self._exec_monitor(
                    data["command"], data.get("agent", {}), task, name, config
                )

            elif action_type == "py":
                await self._exec_py(data["code"], config)

            else:
                await warn(f"未知的 action 类型: {action_type}", config)

        except Exception as e:
            await err(f"任务 {name} 执行失败: {e}", config)

    async def _exec_shell(self, cmd: str, task: Task, config: AppConfig | None = None):
        """执行 shell 命令。"""
        from uniclaw.tools.notify import push_notification

        cwd = task.root_dir or None
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
        err_text = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
        if out:
            await info(out, config)
        if err_text:
            await warn(f"[stderr] {err_text}", config)
        name = task.name or task.id
        status = "成功" if proc.returncode == 0 else f"失败(exit={proc.returncode})"
        await push_notification(f"{name} {status}", title="定时任务")

    async def _exec_monitor(
        self,
        cmd: str,
        agent_data: dict,
        task: Task,
        name: str,
        config: AppConfig | None = None,
    ):
        """执行 monitor: shell 命令,退出码非零时触发 agent。"""
        cwd = task.root_dir or None
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        if proc.returncode != 0:
            await info(f"[monitor] 触发 (exit={proc.returncode}): {cmd}", config)
            out_text = (
                stdout.decode("utf-8", errors="replace").strip() if stdout else ""
            )
            err_text = (
                stderr.decode("utf-8", errors="replace").strip() if stderr else ""
            )
            if err_text:
                await info(f"[monitor] stderr: {err_text}", config)

            # 将触发命令、退出码、stdout/stderr 拼接到 agent 提示词前面
            # 这些上下文信息帮助 agent 理解当前状况,便于排查和处理问题
            message = agent_data.get("message", "")
            full_message = (
                f"用户刚才执行了以下命令\n\n"
                f"执行的命令: {cmd}\n"
                f"退出码: {proc.returncode}\n"
            )
            if out_text:
                full_message += f"\n命令输出(stdout):\n{out_text}\n"
            if err_text:
                full_message += f"\n错误输出(stderr):\n{err_text}\n"
            full_message += f"\n用户要求: {message}"
            await self._run_agent(
                full_message,
                f"monitor:{name}",
                task.session_id,
                config,
            )
        else:
            out = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
            await info(f"[monitor] 未触发 ({cmd}): {out or '(无输出)'}", config)

    async def _exec_py(self, code: str, config: AppConfig | None = None):
        """执行 Python 代码。"""
        stdout = io.StringIO()
        env = {"__builtins__": __builtins__}
        with contextlib.redirect_stdout(stdout):
            try:
                result = eval(code, env, env)
            except SyntaxError:
                exec(code, env, env)
                result = env.get("result")
        output = stdout.getvalue().strip()
        if output:
            await info(output, config)
        if result is not None:
            await info(str(result), config)
