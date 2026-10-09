"""定时任务调度器 — 后台守护线程,定期检查并执行到期任务"""

import asyncio
import calendar
import contextlib
import io
import json
import logging
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from croniter import croniter, CroniterBadCronError, CroniterBadDateError

from uniclaw.config import AppConfig
from uniclaw.console.ui import info, warn, err
from uniclaw.context import get_app_dir, Scope
from uniclaw.utils.process import run_command


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


CRON_FIELD_COUNT = 5
CRON_FORMAT_HINT = "有效格式: 5 字段 '分 时 日 月 周',如 '0 9 * * *'(每天 9:00)、'*/5 * * * *'(每 5 分钟)"


def _never_fires_reason(fields: list[str]) -> str:
    """解释为什么该 Cron 组合永不触发,返回 '(...)' 形式的原因;无法判定时返回空串。"""
    day_f, month_f = fields[2], fields[3]
    if day_f.isdigit() and month_f.isdigit():
        day, month = int(day_f), int(month_f)
        if 1 <= month <= 12:
            # 用闰年判断,避免把 2/29 误判为永不触发
            if day > calendar.monthrange(2024, month)[1]:
                return f"({month} 月没有 {day} 日)"
    return ""


def _parse_cron(cron_str: str) -> croniter:
    """解析 Cron 表达式

    只支持标准 5 字段格式: 分 时 日 月 周
    最小粒度为 1 分钟,不支持秒级调度(6 字段会被拒绝)

    Returns:
        croniter: 解析后的 croniter 对象

    Raises:
        ValueError: 字段数不对、语法非法,或该组合永不触发(如 2 月 31 日)
    """
    s = cron_str.strip()
    fields = s.split()
    if len(fields) != CRON_FIELD_COUNT:
        raise ValueError(
            f"无效的 Cron 表达式: '{cron_str}' — 需要 {CRON_FIELD_COUNT} 字段"
            f"(分 时 日 月 周),收到 {len(fields)} 个。{CRON_FORMAT_HINT}"
        )
    try:
        it = croniter(s)
    except (ValueError, KeyError, CroniterBadCronError) as e:
        raise ValueError(f"无效的 Cron 表达式: '{cron_str}' — {e}。{CRON_FORMAT_HINT}") from e
    # 向后探测一次: 语法合法但组合永不触发(如 2 月 31 日)时 croniter 抛 BadDateError
    try:
        probe = croniter(s, datetime.now())
        probe.get_next(datetime)
    except CroniterBadDateError:
        reason = _never_fires_reason(fields)
        raise ValueError(
            f"无效的 Cron 表达式: '{cron_str}' — 该 cron 表达式永不会触发{reason}。{CRON_FORMAT_HINT}"
        ) from None
    return it


class Scheduler:
    """定时任务调度器(单例)"""

    _instance: "Scheduler | None" = None
    _lock = threading.Lock()

    def __init__(self):
        self._running: set[str] = set()  # 执行中任务 id(防 tick 重入)
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
                # last_run 是"真实执行过"的事实记录,更新配置不清零;
                # 是否在新配置下执行过由 schedule_list 对比 updated 判断
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
            # 只有真正执行过才写 last_run;注册时刻绝不能冒充执行时刻
            last_run=None,
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
        # last_run 保留: 它记录的是"何时真实执行过",配置变更不改变历史事实
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
        # last_run 保留(同 update_action): 历史执行事实不因配置变更而消失
        task.updated = datetime.now().isoformat(timespec="seconds")
        self.save_config()
        return True

    def toggle_task(self, task_id: str, enabled: bool) -> bool:
        self.load_config()
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.enabled = enabled
        if enabled:
            # 重新启用不清 last_run —— 保留真实执行时间,
            # "启用后是否执行过"由 updated 对比体现
            task.updated = datetime.now().isoformat(timespec="seconds")
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
                await err(f"[scheduler] 调度器检查失败: {e}", config, e=e)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    async def _check_and_run_tasks(self, config: AppConfig | None = None):
        """检查所有任务,执行到期的任务。

        last_run 只在任务真正执行过之后才写入 —— 不能在派发前预写,
        否则注册/派发时刻会冒充执行时刻。
        """
        self.load_config()
        now = datetime.now()
        pending = []

        for task_id, task in self._tasks.items():
            if not task.enabled:
                continue

            try:
                cron = _parse_cron(task.schedule)
            except ValueError:
                # 非法/永不触发的 cron: 跳过,不让单个坏任务打断整个调度循环
                continue

            try:
                if task.last_run is None:
                    # 首次运行,计算上一次应该运行的时间
                    prev_time = cron.get_prev(datetime)
                    should_run = prev_time <= now
                else:
                    last_dt = datetime.fromisoformat(task.last_run)
                    next_time = cron.get_next(datetime, start_time=last_dt)
                    should_run = next_time <= now
            except (CroniterBadDateError, ValueError):
                continue

            # 执行中的任务不重复派发: 慢任务(>1 个 tick)在 last_run 落地前
            # 会被下一轮再次判定为到期,导致同一任务重复执行(update 中途
            # 换 action 时表现为"旧+新命令各跑一行")
            if should_run and task_id not in self._running:
                pending.append((task_id, task))

        if not pending:
            return

        async def _run_and_record(task_id: str, task: Task):
            self._running.add(task_id)
            try:
                ran = await self._execute_task(task_id, task, config)
                if ran:
                    # 真正执行过才落 last_run,时刻取实际执行时刻。
                    # 必须同步写到 self._tasks 的当前对象 —— 执行期间
                    # 用户 update 会 load_config 重建任务对象,只写快照
                    # 会被丢弃 → 下个 tick 判定到期 → 任务无限循环重跑
                    ts = datetime.now().isoformat(timespec="seconds")
                    task.last_run = ts
                    live = self._tasks.get(task_id)
                    if live is not None and live is not task:
                        live.last_run = ts
            finally:
                self._running.discard(task_id)

        await asyncio.gather(
            *(_run_and_record(tid, t) for tid, t in pending),
            return_exceptions=True,
        )
        self.save_config()

    async def _run_agent(
        self,
        message: str,
        task_name: str,
        session_id: str | None,
        config: AppConfig | None = None,
    ):
        """复用当前会话执行 agent 消息。由 wake_agent 统一处理唤醒策略。"""
        config = await self._find_config(session_id, config)
        if config is None:
            await err(
                f"[{task_name}] 会话 {session_id or '(无)'} 不存在,已跳过", config
            )
            return

        from uniclaw.utils.wakeup import wake_agent
        from uniclaw.utils.constants import SYSTEM_PREFIX

        full_message = f"{SYSTEM_PREFIX}(scheduler:{task_name})\n{message}"
        await wake_agent(full_message, config)

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
            except Exception as e:
                logger.warning("WebUI 加载会话失败: %s", e)

        else:
            try:
                if tui and tui.config:
                    tui_session_id = tui.config.current_agent.session.id
                    if tui_session_id == session_id:
                        return tui.config
            except Exception as e:
                logger.warning("TUI 获取会话失败: %s", e)

        # 从磁盘加载 session
        from uniclaw.tools.session.session_manager import SessionManager
        from uniclaw.config import load_config
        from uniclaw.spinner import NoopSpinner

        session = SessionManager.load_session(session_id)
        if session:
            return load_config(session=session, spinner=NoopSpinner())

        return None

    async def _execute_task(
        self, task_id: str, task: Task, config: AppConfig | None = None
    ) -> bool:
        """执行单个任务(JSON 格式)。

        Returns:
            bool: 动作是否真正被执行(派发到具体 handler)。
                  JSON 非法 / 未知 type 等"根本没跑起来"的情况返回 False,
                  调用方据此决定是否写 last_run。
        """
        action = task.action
        name = task.name or task_id
        await info(f"[scheduler] 执行任务: {name}", config)

        try:
            data = json.loads(action)
        except json.JSONDecodeError as e:
            await err(f"任务 {name} 执行失败: action 不是合法 JSON: {e}", config)
            return False

        action_type = data.get("type")
        try:
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
                return False
        except TimeoutError as e:
            await warn(f"任务 {name} 执行超时已中止: {e}", config)
            # 超时中止也算真实执行过一次(派发过),记 last_run 防立刻重跑
            return True
        except Exception as e:
            await err(f"任务 {name} 执行失败: {e}", config, e=e)
            # 已派发到具体动作(命令真的跑过),属于真实执行时刻
            return True
        return True

    async def _exec_shell(self, cmd: str, task: Task, config: AppConfig | None = None):
        """执行 shell 命令。"""
        from uniclaw.tools.notify import push_notification

        cwd = task.root_dir or None
        # 超时的子进程树由 run_command 终止(taskkill /T 或 killpg),
        # 否则 sleep70 之类会继续跑完;超时不算"执行失败"(是被保护中止)
        r = await run_command(cmd, cwd=cwd, timeout=60)
        if r.timed_out:
            raise TimeoutError(
                "执行超过 60s 超时保护,已终止子进程(如需长任务请拆分或用 monitor 类型)"
            )
        out = r.stdout.strip()
        err_text = r.stderr.strip()
        if out:
            await info(out, config)
        if err_text:
            await warn(f"[stderr] {err_text}", config)
        name = task.name or task.id
        status = "成功" if r.code == 0 else f"失败(exit={r.code})"
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
        # 超时同样走 run_command 的进程树回收,避免挂起的子进程残留
        r = await run_command(cmd, cwd=cwd, timeout=60)
        if r.timed_out:
            raise TimeoutError("monitor 命令超过 60s 超时保护,已终止子进程")

        if r.code != 0:
            await info(f"[monitor] 触发 (exit={r.code}): {cmd}", config)
            out_text = r.stdout.strip()
            err_text = r.stderr.strip()
            if err_text:
                await info(f"[monitor] stderr: {err_text}", config)

            # 将触发命令、退出码、stdout/stderr 拼接到 agent 提示词前面
            # 这些上下文信息帮助 agent 理解当前状况,便于排查和处理问题
            message = agent_data.get("message", "")
            full_message = (
                f"用户刚才执行了以下命令\n\n"
                f"执行的命令: {cmd}\n"
                f"退出码: {r.code}\n"
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
            out = r.stdout.strip()
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
