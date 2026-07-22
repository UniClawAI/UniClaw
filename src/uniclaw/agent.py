from __future__ import annotations

import asyncio
from enum import StrEnum
import json
import os
import threading
import difflib
import time
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING
import uuid

from uniclaw.tools.registry import search_tools, ExtendedToolManager
from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR
from uniclaw.provider import astream
from uniclaw.tools.session.session import StreamChunk
from uniclaw.tools import get_core_tools, get_tools
from uniclaw.utils.message import MessageRole, extract_text
from dataclasses import dataclass, field
from uniclaw.context import build_system_prompt, get_base_system_prompt
from uniclaw.config import Permissions, AppConfig
from uniclaw.tools.ask import AskUserQuestion

if TYPE_CHECKING:
    from uniclaw.tools.session.session import Session
    from uniclaw.tools.todolist import TodoList
    from uniclaw.tools.todolist.goal import GoalManager
from uniclaw.tools.fs import Edit, Write
from uniclaw.tools.base import tc_name as _tc_name, tc_args as _tc_args, Tool, extract_explains

# 死循环检测:连续相同工具调用次数阈值
LOOP_DETECTION_THRESHOLD = 5
from uniclaw.tools.multi_agent.sub_agent import AgentDefinition
from uniclaw.tools.multi_agent.tools import (
    subagent_check_result,
    subagent_send_message,
    subagent_close,
)
from uniclaw.tools.shell import Bash
from uniclaw.utils.checkpoint import create_checkpoint
from uniclaw.utils.git import (
    create_worktree,
    get_git_root,
    remove_worktree,
)
from uniclaw.utils.truncation import truncate_text_by_lines
from uniclaw.utils.logger import get_logger
from uniclaw.utils.format import format_args_for_display
from uniclaw.tools.hooks.hook_manager import HookError, HookEvent, run_hooks
import traceback

from uniclaw.utils.wrapper import error_catch
from uniclaw.console.ui import info

# 只读工具去重:相同 (name, args) 且结果相同时省略重复内容
DEDUP_TOOLS = frozenset({"Read", "Glob", "Grep", "webFetch", "webSearch"})
DEDUP_MIN_CHARS = 500  # 结果超过此长度才去重


class ReturnEvent:

    def __init__(self, default_content=None):
        self.content = default_content
        self.return_event = asyncio.Event()


@dataclass
class UserEvent:
    content: str | list[dict[str, Any]]


@dataclass
class TextChunkEvent:
    content: str


@dataclass
class ThinkingChunkEvent:
    def __init__(self, content):
        self.content = content


@dataclass
class ThinkingStartEvent:
    pass


@dataclass
class AssistantEvent:
    content: str
    tool_calls: list
    in_tokens: int = 0
    out_tokens: int = 0
    model_name: str = ""


@dataclass
class ToolPreparingEvent:
    """LLM 流式输出中检测到工具调用名称,工具尚未执行。"""

    name: str
    args: dict = field(default_factory=dict)


@dataclass
class ToolStartEvent:
    name: str
    args: dict
    tool_call_id: str = ""
    explain: str = ""


@dataclass
class ToolEvent:
    name: str
    content: str
    tool_call_id: str
    args: dict = None


@dataclass
class ToolStreamEvent:
    """工具执行过程中的流式输出事件(纯 UI 显示用,不参与 LLM 交互)。"""

    name: str
    content: str
    tool_call_id: str = ""


@dataclass
class EndEvent:
    depth: int


@dataclass
class InterruptedEvent:
    message: str = "已中断,等待您的补充指令..."


class PermissionRequestEvent(ReturnEvent):
    def __init__(
        self,
        description: str,
        tool_call: dict = None,
        explanation: str = "",
        agent_name: str = "",
    ):
        super().__init__("无可用的 UI 响应,自动拒绝权限请求")
        self.description: str = description
        self.tool_call: dict = tool_call or {}
        self.explanation: str = explanation
        self.agent_name: str = agent_name


class SlashCommandEvent(ReturnEvent):
    """用户在 agent 运行期间输入了 /command,交由 UI 处理。"""

    def __init__(self, command: str):
        super().__init__()
        self.command: str = command


class ShellCommandEvent(ReturnEvent):
    """用户在 agent 运行期间输入了 !cmd,交由 UI 执行并将结果返回。"""

    def __init__(self, command: str, source: str = "chat"):
        super().__init__()
        self.command: str = command
        self.source: str = source


async def _check_permission(tc: dict, config: AppConfig) -> tuple[bool, str]:
    """检查工具调用是否需要用户权限确认。

    根据配置的权限模式和工具类型,判断是否自动批准该工具调用。
    某些安全操作或特定模式下的操作可以自动放行,其他操作需要用户手动确认。

    Args:
        tc (dict): 工具调用字典,包含以下键:
            - name (str): 工具名称,如 "Read", "Write", "Bash" 等
            - args (dict): 工具参数,不同工具有不同的参数字段
        config (AppConfig): 应用配置对象

    Returns:
        tuple[bool, str]: (是否自动批准, LLM解释文本)
            - 第一个元素:True 表示自动批准,False 表示需要用户确认
            - 第二个元素:LLM 生成的安全分析解释(仅 AUTO 模式下 LLM 判定不安全时有值)

    Note:
        - 计划模式切换工具始终自动批准
        - ACCEPT_ALL 模式下所有操作自动批准
        - MANUAL 模式下所有操作都需要用户确认
        - 只读类工具和记忆/技能列表工具自动批准
        - PLAN 模式下,写入计划目录的 Write 操作自动批准
        - Bash 命令通过安全检查后自动批准
        - 写入当前工作目录下文件的 Write 操作自动批准
        - AUTO 模式下,以上快速路径都未命中时,调用 LLM 检测安全性
        - 其他情况默认需要用户确认
    """
    perm_mode = config.permission_mode
    name = _tc_name(tc)

    if perm_mode == Permissions.ACCEPT_ALL:
        return (True, "")
    if perm_mode == Permissions.MANUAL:
        return (False, "")  # 始终询问

    # 安全工具自动批准(只读类工具和管理工具,computer use 启用时包含写入工具)
    from uniclaw.tools.security import is_safe_tool

    if is_safe_tool(name):
        return (True, "")

    # 活跃 skill 声明的工具自动放行
    from uniclaw.tools.skill.tools import get_active_skill_tools

    if name in get_active_skill_tools():
        return (True, "")

    # PLAN 模式下的特殊处理
    if perm_mode == Permissions.PLAN:

        # Write 工具:写入计划目录自动放行
        if name in (Write.name, Edit.name):
            from pathlib import Path

            file_path = _tc_args(tc).get("file_path", "")
            try:
                abs_file = Path(file_path).resolve()
                from uniclaw.tools.plan import get_plans_dir

                if abs_file.is_relative_to(get_plans_dir(config).resolve()):
                    return (True, "")
            except (ValueError, OSError):
                pass

    # Bash 命令安全检查(安全则直接放行,不安全则继续走后续流程包括 LLM 检测)
    if name == Bash.name:
        from uniclaw.tools.security import is_safe_bash

        args = _tc_args(tc)
        command = args.get("command", "").strip()
        if is_safe_bash(command, config.root_dir):
            return (True, "")

    # 其他工具的持久化规则检查
    from uniclaw.tools.security import check_saved_tool_rule

    if check_saved_tool_rule(name, config.root_dir):
        return (True, "")

    # Write 工具:如果写入的是可写目录下的文件,则自动放行
    if name in (Write.name, Edit.name):
        from pathlib import Path

        file_path = _tc_args(tc).get("file_path", "")
        writable_dirs = config.writable_dirs

        if writable_dirs:
            try:
                abs_file = Path(file_path).resolve()
                for d in writable_dirs:
                    abs_dir = Path(d).resolve()
                    if abs_file.is_relative_to(abs_dir):
                        return (True, "")
            except (ValueError, Exception):
                pass

    # 所有快速路径都未命中,调用 LLM 检测安全性
    from uniclaw.tools.security import llm_safe_check

    is_safe, explanation = await llm_safe_check(tc, config)
    if is_safe:
        return (True, "")
    return (False, explanation)


def _permission_desc(tc: dict) -> str:
    """生成权限请求的美观描述信息

    Args:
        tc: 工具调用字典,包含工具名称和参数

    Returns:
        格式化的权限请求描述字符串
    """
    name = _tc_name(tc)
    inp = _tc_args(tc)

    # Bash 命令执行
    if name == Bash.name:
        command = inp.get("command", "")
        return f"🖥️  运行 Shell 命令:\n   {command}"

    # 文件写入操作
    if name == Write.name:
        file_path = inp.get("file_path", "")
        return f"📝 写入文件:\n   {file_path}"

    # 文件编辑操作
    if name == Edit.name:
        file_path = inp.get("file_path", "")
        old_string = inp.get("old_string", "")
        new_string = inp.get("new_string", "")
        replace_all = inp.get("replace_all", False)
        diff = _edit_permission_diff(file_path, old_string, new_string)
        suffix = "\n   replace_all=true" if replace_all else ""
        return f"✏️  编辑文件:\n   {file_path}{suffix}\n\n{diff}"

    # 其他工具调用
    formatted_args = format_args_for_display(inp, 500, ",\n")
    return f"🔧 调用工具: {name}({formatted_args})"


def _edit_permission_diff(file_path: str, old_string: str, new_string: str) -> str:
    """Build a compact preview diff for an Edit permission prompt."""

    def _diff_lines(value: str) -> list[str]:
        lines = str(value).splitlines(keepends=True)
        if not lines and value:
            lines = [str(value)]
        return [line if line.endswith(("\n", "\r")) else f"{line}\n" for line in lines]

    old_lines = _diff_lines(old_string)
    new_lines = _diff_lines(new_string)

    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{Path(file_path).name}",
            tofile=f"b/{Path(file_path).name}",
            n=3,
        )
    )
    if not diff_lines:
        return "拟修改内容无差异。"

    max_lines = 160
    if len(diff_lines) > max_lines:
        hidden = len(diff_lines) - max_lines
        diff_lines = diff_lines[:max_lines]
        diff_lines.append(f"... ({hidden} more diff lines hidden)\n")
    return "拟修改 diff:\n" + "".join(diff_lines)


class AgentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


@dataclass
class AgentTask:

    name: str
    prompt: str
    session: Session
    user_queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)
    status: str = AgentStatus.PENDING
    result: Optional[str] = None
    result_read_index: int = 0

    worktree_path: str = ""
    worktree_branch: str = ""
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    future: Optional[asyncio.Task] = field(default=None, repr=False)
    event_queue: Optional[asyncio.Queue] = field(default=None, repr=False)
    todolist: Optional[TodoList] = field(default=None, repr=False)
    goal_manager: GoalManager = field(default=None, repr=False)
    extended_mgr: ExtendedToolManager = field(
        default_factory=ExtendedToolManager, repr=False
    )
    allowed_tools_set: Optional[set[str]] = field(default=None, repr=False)

    @property
    def id(self) -> str:
        return self.session.id

    async def drain_user_queue(self, multi_agent: MultiAgent, config: AppConfig) -> str:
        """从 user_queue 取出所有待处理消息,分类处理:
        - !cmd → 执行 shell 命令,结果追加到 messages 让 LLM 可见
        - /command → 交由 UI 处理斜杠命令(不追加到 messages)
        - 其他 → 合并为一条用户消息追加到 messages
        返回合并后的普通用户文本。"""
        messages = []
        while not self.user_queue.empty():
            try:
                messages.append(self.user_queue.get_nowait())
            except Exception:
                break
        if not messages:
            return ""

        self.cancel_event.clear()
        text_parts = []
        for msg in messages:
            # 多模态消息(list): 提取文本部分参与合并
            if isinstance(msg, list):
                text = extract_text(msg)
                if text:
                    text_parts.append(text)
                continue
            stripped = msg.strip()
            if stripped.startswith("!"):
                # !!cmd → 控制台命令(不注入 session)；!cmd → 聊天区命令(注入 session)
                if stripped.startswith("!!"):
                    cmd = stripped[2:].strip()
                    source = "console"
                else:
                    cmd = stripped[1:].strip()
                    source = "chat"
                if cmd:
                    event = ShellCommandEvent(cmd, source=source)
                    shell_output = (
                        await multi_agent.send_event_to_user(event, config) or ""
                    )
                    if source == "chat":
                        self.session.add_message(
                            MessageRole.USER,
                            f"{SYSTEM_PREFIX}(用户执行Shell命令)\n$ {cmd}\n{shell_output}",
                        )
            elif stripped.startswith("/"):
                event = SlashCommandEvent(stripped)
                await multi_agent.send_event_to_user(event, config)
            else:
                text_parts.append(msg)

        if text_parts:
            content = "\n\n".join(text_parts)
            self.session.add_message(MessageRole.USER, content)
            await multi_agent.send_event_to_user(UserEvent(content), config)
            return content
        return ""


class MultiAgent:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self.id2AgentTask: dict[str, AgentTask] = {}
            self.loop: asyncio.AbstractEventLoop | None = None  # 主事件循环引用
            self._initialized = True

    @classmethod
    def get_instance(cls):
        """获取 MultiAgent 单例实例"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = object.__new__(cls)
                    cls._instance.__init__()
        return cls._instance

    async def send_event_to_user(self, event, config: AppConfig):
        """将事件放入队列。对于有 return_event 的事件,等待 UI 处理后返回内容。
        普通事件:仅发送到 task 自己的队列;无队列则丢弃。
        阻塞事件(带 return_event):队列查找链 → task 自身 → root_config → TUI 当前会话。"""
        task = config.current_agent
        queue = task.event_queue
        if not queue and hasattr(event, "return_event"):
            # root_config: 祖子代理场景的父级队列
            if config.root_config:
                queue = config.root_config.current_agent.event_queue
            # TUI: 后台任务(如 scheduler)路由到当前活跃会话
            if not queue:
                try:
                    from uniclaw.console.run import TUIApp
                    tui = TUIApp.get_instance()
                    if tui and tui.config:
                        queue = tui.config.current_agent.event_queue
                except Exception:
                    pass
        if queue:
            await queue.put((task, event))

        if hasattr(event, "return_event"):
            if queue:
                await event.return_event.wait()
            return event.content

    async def wait(self, task_id: str, timeout: float = None):
        """
        异步等待指定任务完成并返回任务对象。

        如果设置了 timeout,每次超时后会检查 messages 是否有新增:
        有新内容则继续等待,无新内容则返回。

        Args:
            task_id (str): 任务的唯一标识符。
            timeout (float, optional): 每轮等待的超时时间(秒)。

        Returns:
            AgentTask or None: 返回对应的任务对象。
        """
        task = self.id2AgentTask.get(task_id)
        if task is None:
            return None
        if task.future is None:
            return task

        last_msg_count = len(task.session)
        while True:
            try:
                await asyncio.wait_for(asyncio.shield(task.future), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass
            # 任务已完成
            if task.status in (
                AgentStatus.COMPLETED,
                AgentStatus.FAILED,
                AgentStatus.CANCELLED,
            ):
                break
            # 有 timeout 时:检查 messages 是否有新增
            current_msg_count = len(task.session)
            if current_msg_count > last_msg_count:
                last_msg_count = current_msg_count
            else:
                break  # 无新内容,结束等待
        return task

    def start_agent(
        self,
        user_message: str | list[dict[str, Any]],
        config: AppConfig,
        system_prompt: Optional[str] = None,
    ) -> AgentTask:
        task = config.current_agent
        task.prompt = user_message
        task.status = AgentStatus.PENDING
        task.cancel_event.clear()  # 清除取消标志,防止新任务被立即中断
        self.id2AgentTask[task.id] = task
        task.future = asyncio.create_task(self.run(user_message, system_prompt, config))
        return task

    async def start_sub_agent(
        self,
        user_message: str,
        config: AppConfig,
        system_prompt: str | None = None,
        agent_def: Optional[AgentDefinition] = None,
        isolation: bool = False,
        inherit_events: bool = False,
        notify_parent: bool = False,
        keep_alive: bool = False,
    ) -> AgentTask:
        """启动子代理。config 应通过 create_sub_config() 预先创建。"""
        parent_task = config.parent_agent
        task = config.current_agent
        task.prompt = user_message
        task.status = AgentStatus.PENDING
        root_dir = config.root_dir

        if (
            inherit_events
            and parent_task is not None
            and parent_task.event_queue is not None
        ):
            task.event_queue = parent_task.event_queue
            task.cancel_event = parent_task.cancel_event
        self.id2AgentTask[task.id] = task

        base_system_prompt = get_base_system_prompt(config)
        allowed_tools = None
        if agent_def:
            if agent_def.model_name:
                config.model_name = (
                    [agent_def.model_name]
                    if isinstance(agent_def.model_name, str)
                    else agent_def.model_name
                )
            if agent_def.tools:
                allowed_tools = agent_def.tools
            if agent_def.system_prompt:
                base_system_prompt += f"\n\n{agent_def.system_prompt}"

        if not allowed_tools:
            allowed_tools = await get_tools(config)
        else:
            from uniclaw.tools.registry import ToolRegistry
            allowed_tools = ToolRegistry.get_instance().resolve_tools(allowed_tools)
        # 子代理展示可搜索的扩展工具
        from uniclaw.tools.registry import get_registry_system_prompt

        registry_ctx = await get_registry_system_prompt(config)
        if registry_ctx:
            base_system_prompt += f"\n\n{registry_ctx}"
        # 用户传递的系统提示词放在最后
        system_prompt = f"{base_system_prompt}\n\n{'' if system_prompt is None else system_prompt}"
        
        if isolation:
            if root_dir is None:
                task.status = AgentStatus.FAILED
                task.result = "isolation需要root_dir(当前为None)"
                return task
            git_root = await get_git_root(root_dir)
            if not git_root:
                task.status = AgentStatus.FAILED
                task.result = "isolation需要git仓库"
                return task
            try:
                worktree_path, worktree_branch = await create_worktree(git_root)
            except Exception as e:
                task.status = AgentStatus.FAILED
                task.result = f"isolation创建工作树失败: {e}"
                return task
            task.worktree_path = worktree_path
            task.worktree_branch = worktree_branch
            notice = (
                f"\n\n[注意:你正在一个隔离的 git worktree 中工作,位于 "
                f"{worktree_path}(分支:{worktree_branch})。"
                f"你的更改与主工作区 {git_root} 隔离。"
                f"在完成之前提交你的更改,以便可以审查/合并。]"
            )
            system_prompt = system_prompt + notice
            config.writable_dirs.insert(0, worktree_path)
            task.session.root_dir = Path(worktree_path)

        async def _run_proc(user_message, system_prompt, config, task: AgentTask):
            try:
                task.user_queue.put_nowait(user_message)
                while not task.cancel_event.is_set():
                    if keep_alive:
                        task.status = AgentStatus.WAITING
                    try:
                        msg = await asyncio.wait_for(
                            task.user_queue.get(),
                            timeout=0.2 if keep_alive else None,
                        )
                    except asyncio.TimeoutError:
                        if keep_alive:
                            continue
                        break
                    if msg == "__agent_close__":
                        task.status = AgentStatus.COMPLETED
                        break
                    await self.run(msg, system_prompt, config, allowed_tools)
                    if task.cancel_event.is_set():
                        task.result = "任务已取消。"
                        return
                    task.result = task.session.get_assistant_messages()
                    if (
                        notify_parent
                        and parent_task is not None
                        and parent_task is not task
                    ):
                        parent_task.user_queue.put_nowait(
                            f"{SYSTEM_PREFIX}[child_agent]\n"
                            f"名称: {task.name}\n"
                            f"任务ID: {task.id}\n"
                            f"状态: {task.status}\n"
                            "消息: 此子智能体有新的输出。\n"
                            f'- 请调用 {subagent_check_result.name}(task_id="{task.id}") 来读取结果\n'
                            f'- 使用 {subagent_send_message.name}(task_id="{task.id}", message="...") 发送消息\n'
                            f'- 使用 {subagent_close.name}(task_id="{task.id}") 关闭智能体'
                        )
                    if not keep_alive:
                        break
                if not task.result:
                    task.result = task.session.get_assistant_messages()
            except Exception as e:
                task.result = f"任务处理失败:{str(e)}"
                task.status = AgentStatus.FAILED
            finally:
                if task.status == AgentStatus.WAITING:
                    task.status = AgentStatus.COMPLETED
                if task.worktree_path:
                    await remove_worktree(
                        task.worktree_path, task.worktree_branch, root_dir
                    )

        task.future = asyncio.create_task(
            _run_proc(user_message, system_prompt, config, task)
        )
        return task

    def list_tasks(self) -> list[AgentTask]:
        return list(self.id2AgentTask.values())

    def send_message(self, task_id: str, message: str) -> bool:
        task = self.id2AgentTask.get(task_id)
        if task is None:
            return False
        if task.status not in (
            AgentStatus.RUNNING,
            AgentStatus.PENDING,
            AgentStatus.WAITING,
        ):
            return False
        task.user_queue.put_nowait(message)
        return True

    def close_agent(self, task_id: str) -> bool:
        task = self.id2AgentTask.get(task_id)
        if task is None:
            return False
        if task.status in (
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
        ):
            return True
        task.user_queue.put_nowait("__agent_close__")
        return True

    async def _run_init(self, user_message, config: AppConfig) -> bool:
        """初始化 run 环境:深度检查、钩子、消息。成功返回 True,失败返回 False。"""
        task = config.current_agent
        if config.depth >= config.max_agent_depth:
            task.status = AgentStatus.FAILED
            task.result = f"错误:超过最大深度 ({config.max_agent_depth})"
            return False
        task.status = AgentStatus.RUNNING
        if not config.is_sub:
            await run_hooks(
                HookEvent.SESSION_START,
                {"user_message": extract_text(user_message), "depth": config.depth},
                config=config,
                task=task,
            )
        task.session.add_message(MessageRole.USER, user_message)
        await self.send_event_to_user(UserEvent(user_message), config)
        return True

    async def _stream_response(
        self, task, system_message, config: AppConfig, tools, model_ref: str = ""
    ) -> StreamChunk | None:
        """异步流式调用 LLM,处理 thinking/text chunk。返回 resp,取消返回 None。

        Args:
            model_ref: 模型引用(带 provider 前缀),如 "openrouter/openai/gpt-4o"。
                       为空时使用 config 中的默认配置。
        """
        try:
            resp = None
            async for chunk in astream(
                system_message,
                task.session,
                model_name=model_ref,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                top_p=config.top_p,
                tools=tools,
                config=config,
            ):
                if task.cancel_event.is_set():
                    task.status = AgentStatus.CANCELLED
                    await self.send_event_to_user(InterruptedEvent(), config)
                    return None
                if resp is None:
                    resp = chunk
                else:
                    resp += chunk
                if chunk.reasoning_content:
                    await self.send_event_to_user(
                        ThinkingChunkEvent(chunk.reasoning_content), config
                    )
                if chunk.content:
                    await self.send_event_to_user(TextChunkEvent(chunk.content), config)
                if chunk.new_tool_call_name:
                    await self.send_event_to_user(
                        ToolPreparingEvent(
                            chunk.new_tool_call_name, chunk.new_tool_call_args
                        ),
                        config,
                    )
            if task.cancel_event.is_set():
                await self.send_event_to_user(InterruptedEvent(), config)
                return None
            return resp
        except Exception:
            error_traceback = traceback.format_exc()
            get_logger("agent", task.session.root_dir).error(error_traceback)
            raise  # 向上抛出异常,由调用方处理 fallback

    async def _process_response(self, resp, task, config: AppConfig):
        """处理 LLM 响应:构建消息、记录 usage、发送事件。返回 (tool_calls, tool_explains)。"""
        content = resp.content or ""
        tool_calls = resp.tool_calls
        reasoning = resp.reasoning_content or ""

        # 提取 _explain 参数并从 tool_calls 中移除(explain 模式,sub-agent 不受影响)
        if config.explain_mode and not config.is_sub and tool_calls:
            tool_calls, tool_explains = extract_explains(tool_calls)
        else:
            tool_explains = {}

        in_tokens = resp.usage.input_tokens if resp.usage else 0
        out_tokens = resp.usage.output_tokens if resp.usage else 0
        total_tokens = resp.usage.total_tokens if resp.usage else in_tokens + out_tokens
        actual_model = resp.model_name or (
            config.model_name[0] if config.model_name else ""
        )
        usage_dict = {
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "total_tokens": total_tokens,
        }

        task.session.add_message(
            MessageRole.ASSISTANT,
            content,
            model_name=actual_model,
            usage=usage_dict,
            tool_calls=tool_calls,
            reasoning_content=reasoning or None,
        )

        await run_hooks(
            HookEvent.PRE_ASSISTANT,
            {
                "content": resp.content,
                "tool_calls": resp.tool_calls,
                "in_tokens": in_tokens,
                "out_tokens": out_tokens,
                "model_name": actual_model,
            },
            config=config,
            task=task,
        )
        await self.send_event_to_user(
            AssistantEvent(
                content=resp.content,
                tool_calls=resp.tool_calls,
                in_tokens=in_tokens,
                out_tokens=out_tokens,
                model_name=actual_model,
            ),
            config,
        )
        from uniclaw.utils.usage import record_usage

        await record_usage(
            in_tokens, out_tokens, len(resp.tool_calls), model=actual_model
        )
        return tool_calls, tool_explains

    async def _execute_single_tool(
        self, tool_call, name2tool, config: AppConfig, explain: str | None = None
    ) -> tuple[dict, Any]:
        """执行单个工具调用(权限检查 + hooks + 执行 + UI 事件)。

        返回 (tool_call, tool_resp_content)。
        """
        task = config.current_agent
        tool_resp_content = None
        tc_name = _tc_name(tool_call)
        tc_args = _tc_args(tool_call)

        # 查找工具
        try:
            tool = name2tool[tc_name]
        except KeyError:
            if task.allowed_tools_set and tc_name in task.allowed_tools_set:
                tool_resp_content = (
                    f"{TOOL_ERROR}: '{tc_name}' 是扩展工具,当前未加载。"
                    f'请先使用 {search_tools.name} 搜索 "{tc_name}" 来加载该工具,然后重试。'
                )
            else:
                tool_resp_content = f"{TOOL_ERROR}: 工具不存在: {tc_name}"

        # PRE_TOOL_USE hook
        if tool_resp_content is None:
            try:
                await run_hooks(
                    HookEvent.PRE_TOOL_USE,
                    {
                        "tool_name": tc_name,
                        "tool_call": tool_call,
                        "args": tc_args,
                    },
                    config=config,
                    task=task,
                )
            except HookError as e:
                tool_resp_content = f"{TOOL_ERROR}: Hook 阻止了工具调用: {e}"

        # 权限检查
        if tool_resp_content is None:
            permitted, llm_explanation = await _check_permission(tool_call, config)
            if not permitted:
                description = _permission_desc(tool_call)
                try:
                    await run_hooks(
                        HookEvent.PERMISSION_REQUEST,
                        {
                            "tool_name": tc_name,
                            "tool_call": tool_call,
                            "args": tc_args,
                            "description": description,
                            "explanation": llm_explanation,
                        },
                        config=config,
                        task=task,
                    )
                    req = PermissionRequestEvent(
                        description=description,
                        tool_call=tool_call,
                        explanation=llm_explanation,
                        agent_name=task.name,
                    )
                    permitted = await self.send_event_to_user(req, config)
                except HookError as e:
                    permitted = f"Hook blocked permission request: {e}"
                await run_hooks(
                    HookEvent.PERMISSION_RESPONSE,
                    {
                        "tool_name": tc_name,
                        "tool_call": tool_call,
                        "args": tc_args,
                        "permitted": permitted is True,
                        "response": permitted,
                    },
                    config=config,
                    task=task,
                )
            if permitted is True:
                tc_id = tool_call.get("id", "")
                await self.send_event_to_user(
                    ToolStartEvent(tc_name, dict(tc_args), tool_call_id=tc_id, explain=explain or ""),
                    config,
                )
                try:
                    async def _stream_cb(content: str, _tc_id=tc_id, _tc_name=tc_name):
                        await self.send_event_to_user(
                            ToolStreamEvent(name=_tc_name, content=content, tool_call_id=_tc_id),
                            config,
                        )
                    tool_resp_content = await tool(**tc_args, config=config, stream_callback=_stream_cb)
                    # 标记扩展工具已使用(LRU:移到最前,防止被淘汰),核心工具不参与能量管理
                    from uniclaw.tools.registry import CORE_TOOL_NAMES
                    if tc_name not in CORE_TOOL_NAMES:
                        task.extended_mgr.touch(tc_name)
                    if isinstance(tool_resp_content, str):
                        tool_resp_content = truncate_text_by_lines(tool_resp_content)
                    # 只读工具去重:结果与之前相同且较大时省略
                    dedup_msg = task.session.check_dedup(
                        tc_name, tc_args, tool_resp_content
                    )
                    if dedup_msg:
                        tool_resp_content = dedup_msg
                except Exception as e:
                    get_logger("agent", task.session.root_dir).error(
                        f"{TOOL_ERROR}: [{tc_name}]\n参数: {tc_args}\n{traceback.format_exc()}"
                    )
                    tool_resp_content = f"{TOOL_ERROR}: {e}"
            else:
                tool_resp_content = (
                    f"{TOOL_ERROR}: 用户拒绝: {permitted}"
                    if isinstance(permitted, str) and permitted.strip()
                    else f"{TOOL_ERROR}: 用户拒绝执行"
                )

        # POST_TOOL_USE hook
        await run_hooks(
            HookEvent.POST_TOOL_USE,
            {
                "tool_name": tc_name,
                "tool_call": tool_call,
                "args": tc_args,
                "result": extract_text(tool_resp_content),
            },
            config=config,
            task=task,
        )
        display_content = (
            tool_resp_content
            if isinstance(tool_resp_content, str)
            else extract_text(tool_resp_content)
        )
        await self.send_event_to_user(
            ToolEvent(
                name=tc_name,
                content=display_content,
                tool_call_id=tool_call.get("id", ""),
                args=tc_args,
            ),
            config,
        )
        return tool_call, tool_resp_content

    async def _execute_tool_calls(
        self,
        tool_calls,
        name2tool,
        config: AppConfig,
        tools: list = None,
        tool_explains: dict[str, str] | None = None,
    ) -> bool:
        """并行执行工具调用列表。返回 True 表示被 cancel。"""
        task = config.current_agent
        if tool_explains is None:
            tool_explains = {}

        # 并行执行所有工具
        results = await asyncio.gather(
            *[self._execute_single_tool(tc, name2tool, config, tool_explains.get(tc.get("id", ""), "")) for tc in tool_calls]
        )

        # 按顺序处理结果: add_message + cancel 检查
        for tool_call, tool_resp_content in results:
            tc_name = _tc_name(tool_call)
            tc_id = tool_call.get("id", "")
            explain_text = tool_explains.get(tc_id, "")
            # 检查是否为多模态内容(如图片),需要特殊处理
            _mm_types = {"image_url", "input_audio", "video_url"}
            if isinstance(tool_resp_content, list) and any(
                isinstance(b, dict) and b.get("type") in _mm_types
                for b in tool_resp_content
            ):
                # 提取文本部分作为 tool 回复
                extracted = extract_text(tool_resp_content, separator="\n")
                task.session.add_message(
                    MessageRole.TOOL,
                    extracted or "(见下方多媒体内容)",
                    name=tc_name,
                    tool_call_id=tc_id,
                    explain=explain_text,
                )
                # 将多模态内容作为 user 消息,让 LLM 能看到图片/音频/视频
                task.session.add_message(MessageRole.USER, tool_resp_content)
                # 广播给前端,让流式输出期间也能显示图片
                await self.send_event_to_user(
                    UserEvent(tool_resp_content), config
                )
            else:
                # TOOL 消息 content 必须是 str,非 str 内容需转换
                final_content = (
                    tool_resp_content
                    if isinstance(tool_resp_content, str)
                    else extract_text(tool_resp_content)
                )
                task.session.add_message(
                    MessageRole.TOOL,
                    final_content,
                    name=tc_name,
                    tool_call_id=tc_id,
                    explain=explain_text,
                )
        if task.cancel_event.is_set():
            task.status = AgentStatus.CANCELLED
            await self.send_event_to_user(InterruptedEvent(), config)
            return True
        # 加载待发现工具,清理被淘汰的扩展工具
        if tools is not None:
            task.extended_mgr.apply(tools, name2tool)
        return False

    async def _save_session(self, config: AppConfig):
        """保存会话。"""
        try:
            from uniclaw.tools.session.session_manager import SessionManager

            await SessionManager.save_session(config)
        except Exception:
            get_logger("agent", config.root_dir).warning(
                f"保存会话失败:\n{traceback.format_exc()}"
            )

    async def _save_memory(self, config: AppConfig):
        """保存记忆(异步,不阻塞主流程)。"""
        try:
            from uniclaw.tools.memory.auto_review import review_and_save_if_due
            from uniclaw.console.ui import info

            saved = await review_and_save_if_due(config)
            for memory in saved:
                await info(
                    f"已保存一条新记忆: {memory.name}\n{memory.description}", config
                )
        except Exception:
            get_logger("agent", config.root_dir).warning(
                f"保存记忆失败:\n{traceback.format_exc()}"
            )

    async def _run_cleanup(self, task, config: AppConfig):
        """设置最终状态,触发 SESSION_END 钩子,保存会话和记忆,发送 EndEvent。"""
        if task.status == AgentStatus.RUNNING:
            task.status = AgentStatus.COMPLETED
        # 从父代理的 sub_configs 中移除自己
        parent_config = config.parent_config
        if parent_config:
            try:
                parent_config.sub_configs.remove(config)
            except ValueError:
                pass
        if not config.is_sub:
            await run_hooks(
                HookEvent.SESSION_END,
                {"status": task.status, "depth": config.depth},
                config=config,
                task=task,
            )
            # 异步保存,不阻塞(完成后发 SessionSavedEvent)
            asyncio.create_task(self._save_session(config))
            asyncio.create_task(self._save_memory(config))
        # 主 agent: 有正在运行的 subagent 时不发 EndEvent,等它们完成
        # WAITING/PENDING 状态的子代理不等待(下一轮对话可能被唤醒)
        if not config.is_sub and config.has_running_subs():
            get_logger("agent", config.root_dir).info(
                f"主 agent 已结束,等待 {len(config.get_running_subs())} 个子代理完成..."
            )
            return
        # 子代理: 发送 depth>0 的 EndEvent,通知前端子代理完成
        # 主 agent: 发送 depth=0 的 EndEvent,通知 bridge 退出
        await self.send_event_to_user(EndEvent(depth=config.depth), config)
        # 子代理结束后,帮父 agent 检查:父 agent 已结束且无其他 RUNNING 的 subagent,发 EndEvent(depth=0)
        if parent_config and not parent_config.has_running_subs():
            parent_task = parent_config.current_agent
            if parent_task and parent_task.status == AgentStatus.COMPLETED:
                await self.send_event_to_user(EndEvent(depth=0), parent_config)

    @error_catch("agent")
    async def run(
        self,
        user_message: str | list[dict[str, Any]],
        system_message: Optional[str] = None,
        config: AppConfig = None,
        allowed_tools: list[Tool] | None = None,
    ):
        task = config.current_agent
        if not await self._run_init(user_message, config):
            return

        # 自动创建 Git 检查点(使用用户消息的文本部分作为描述)
        await create_checkpoint(
            task.session.root_dir, message=extract_text(user_message)
        )
        if system_message is None:
            system_message = await build_system_prompt(config)
        # 使用核心工具(约 15 个)+ search_tools,扩展工具按需加载
        is_sub = config.is_sub
        tools = list(await get_core_tools(sub_agent=is_sub))
        if is_sub:
            ext_names = {t.name for t in allowed_tools}
            task.allowed_tools_set = {t.name for t in tools} | ext_names
        else:
            task.allowed_tools_set = {t.name for t in await get_tools(config)}

        name2tool = {tool.name: tool for tool in tools}
        # 恢复上次会话加载过的扩展工具
        mgr = task.extended_mgr
        if mgr.loaded:
            from uniclaw.tools.registry import ToolRegistry

            _entries = ToolRegistry.get_instance().get_all_entries()
            mgr.restore_session(
                list(mgr.loaded), _entries, name2tool, tools, task.allowed_tools_set
            )
        compact_task: asyncio.Task | None = None
        model_list = config.model_name if config.model_name else [""]
        # 死循环检测:记录最近的工具调用签名
        recent_tool_calls: list[tuple[str, str]] = []  # [(tool_name, args_json), ...]
        while True:
            while True:
                if task.cancel_event.is_set():
                    task.status = AgentStatus.CANCELLED
                    await self.send_event_to_user(InterruptedEvent(), config)
                    break

                await self.send_event_to_user(ThinkingStartEvent(), config)

                # Fallback: 依次尝试 model_list 中的每个模型
                resp = None
                errors: list[str] = []
                for i, model_ref in enumerate(model_list):
                    try:
                        resp = await self._stream_response(
                            task, system_message, config, tools, model_ref=model_ref
                        )
                        break  # 成功则跳出 fallback 循环
                    except Exception as e:
                        err_msg = f"{model_ref or '(默认)'}: {e}"
                        errors.append(err_msg)
                        if i < len(model_list) - 1:
                            # 还有 fallback 模型,通知用户并继续
                            await self.send_event_to_user(
                                TextChunkEvent(
                                    f"\n⚠️ 模型 {err_msg}\n尝试 fallback 模型...\n"
                                ),
                                config,
                            )
                        else:
                            # 所有模型都失败了
                            detail = "\n  - ".join(errors)
                            await self.send_event_to_user(
                                TextChunkEvent(
                                    f"\n⚠️ 所有模型请求失败:\n  - {detail}\n"
                                ),
                                config,
                            )
                if resp is None:
                    break

                # LLM 推理期间压缩可能在后台运行,此处等待完成
                if compact_task is not None and not compact_task.done():
                    await compact_task
                compact_task = None

                tool_calls, tool_explains = await self._process_response(resp, task, config)
                if not tool_calls:
                    content = await task.drain_user_queue(self, config)
                    if content:
                        continue
                    break

                # 死循环检测:检查是否连续调用相同工具且参数相同
                current_calls = tuple(
                    sorted(
                        (_tc_name(tc), json.dumps(_tc_args(tc), sort_keys=True))
                        for tc in tool_calls
                    )
                )
                recent_tool_calls.append(current_calls)
                # 只保留最近 N 条记录
                if len(recent_tool_calls) > LOOP_DETECTION_THRESHOLD:
                    recent_tool_calls = recent_tool_calls[-LOOP_DETECTION_THRESHOLD:]
                # 检测连续相同调用
                if len(recent_tool_calls) == LOOP_DETECTION_THRESHOLD:
                    if len(set(recent_tool_calls)) == 1:
                        # 连续 N 次完全相同的工具调用,判定为死循环
                        tool_names = [name for name, _ in current_calls]
                        tool_name_str = ", ".join(tool_names)
                        await self.send_event_to_user(
                            TextChunkEvent(
                                f"\n⚠️ 检测到死循环:工具 `{tool_name_str}` 连续调用 "
                                f"{LOOP_DETECTION_THRESHOLD} 次且参数完全相同。\n"
                            ),
                            config,
                        )
                        # 给 AI 发消息打破死循环,让它改变策略
                        task.user_queue.put_nowait(
                            f"{SYSTEM_PREFIX}你已经连续 {LOOP_DETECTION_THRESHOLD} 次使用相同的参数调用工具 `{tool_name_str}`,"
                            f"这表明你可能陷入了死循环。请立即停止当前操作,换一种不同的方法或思路来完成任务。"
                        )
                        recent_tool_calls.clear()
                        # 立即读取队列消息并跳过本次工具执行
                        content = await task.drain_user_queue(self, config)
                        continue

                if task.cancel_event.is_set():
                    task.status = AgentStatus.CANCELLED
                    await self.send_event_to_user(InterruptedEvent(), config)
                    break
                if await self._execute_tool_calls(tool_calls, name2tool, config, tools, tool_explains):
                    break
                content = await task.drain_user_queue(self, config)

                # 工具执行完成后,启动后台压缩(与 LLM 推理并行)
                if compact_task is None or compact_task.done():
                    compact_task = asyncio.create_task(
                        task.session.maybe_compact(config)
                    )
                # ── 能量扣减:每轮工具调用结束,扩展工具能量-1,耗尽则卸载 ──
                evicted_energy = task.extended_mgr.drain_energy()
                if evicted_energy and tools is not None:
                    tools[:] = [t for t in tools if t.name not in set(evicted_energy)]
                    for name in evicted_energy:
                        name2tool.pop(name, None)
            # 内层循环结束,取消未完成的后台压缩任务
            if compact_task is not None and not compact_task.done():
                compact_task.cancel()
                try:
                    await compact_task
                except asyncio.CancelledError:
                    pass
            compact_task = None
            # ── goal check: 独立 judge 评估目标是否达成 ──
            goal_mgr = task.goal_manager
            if (
                goal_mgr
                and goal_mgr.active
                and not config.is_sub
                and not task.cancel_event.is_set()
            ):
                from uniclaw.tools.todolist.goal import GoalStatus, evaluate_goal

                conversation = task.session.get_recent_text(max_chars=8000)
                status, reason = await evaluate_goal(
                    goal_mgr.goal, conversation, config
                )
                # status = GoalStatus.NOT_ACHIEVED
                # reason = "test"
                if status == GoalStatus.ACHIEVED:
                    await info(f"目标已达成: {goal_mgr.goal} | 原因: {reason}", config)
                    goal_mgr.clear_goal()
                elif status == GoalStatus.WAITING:
                    await info(
                        f"等待后台任务: {goal_mgr.goal} | 原因: {reason}", config
                    )
                elif goal_mgr.check_reentry():
                    # NOT_ACHIEVED 且未超过重入次数
                    goal_mgr.increment_reentry()
                    msg = (
                        f"{SYSTEM_PREFIX}目标尚未达成,请继续工作。\n"
                        f"目标: {goal_mgr.goal}\n"
                        f"原因: {reason}"
                    )
                    task.user_queue.put_nowait(msg)
                    content = await task.drain_user_queue(self, config)
                    continue
                # else: 超过最大重入次数,允许退出
            # ── overseer check: TodoList 未完成项 ──
            todo = task.todolist
            incomplete = todo.get_incomplete() if todo else []
            if (
                todo
                and todo.overseer.active
                and not config.is_sub
                and not task.cancel_event.is_set()
                and incomplete
            ):
                msg = f"{SYSTEM_PREFIX}还有以下任务未完成,请继续:\n" + "\n".join(
                    f"- {item}" for item in incomplete
                )
                msg += f"\n\n请查看TodoList当前任务列表并继续完成剩余任务。如需与用户交流,请使用 {AskUserQuestion.name} 工具。"
                task.user_queue.put_nowait(msg)
                content = await task.drain_user_queue(self, config)
                continue
            else:
                break

        await self._run_cleanup(task, config)
        return
