"""代理状态和任务数据类型 — 从 agent.py 拆分以避免循环导入。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Optional

from uniclaw.tools.registry import ExtendedToolManager
from uniclaw.utils.constants import SYSTEM_PREFIX
from uniclaw.utils.message import MessageRole, extract_text
from uniclaw.utils.logger import get_logger

if TYPE_CHECKING:
    from uniclaw.agent.multi_agent import MultiAgent
    from uniclaw.config import AppConfig
    from uniclaw.tools.session.session import Session
    from uniclaw.tools.todolist import TodoList
    from uniclaw.tools.todolist.goal import GoalManager


class AgentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


@dataclass
class AgentTask:

    name: str
    prompt: str
    session: Session
    tool_call_id: str = ""  # 创建本(子)任务的那次工具调用的 ID, 主任务为空
    user_queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)
    status: str = AgentStatus.PENDING
    result: Optional[str] = None
    result_read_index: int = 0
    notify_parent: bool = False  # 异步子代理:执行中/结束时唤醒父 agent(run 主循环读取)
    keep_alive: bool = False  # 异步子代理:回合结束后驻留等待消息(sleep_timer 等唤醒依赖此)
    closed: bool = False  # 已被 subagent_close 关闭(从列表移除,重复 close 提示已关闭)

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

    @property
    def is_finished(self) -> bool:
        """执行过程是否已真正结束(future 已完成,或从未启动且已终态)。

        与 status 的"本回合结果"维度正交: keep_alive 子代理回合结束后
        status=COMPLETED 但 future 未完成(驻留等下一条消息),此时仍算
        "未结束"。用于 close_agent 判断是否需要发取消信号。
        """
        if self.future is not None:
            return self.future.done()
        return self.status in (
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
        )

    @property
    def can_accept_messages(self) -> bool:
        """是否还能接收消息(存活/驻留维度,与 status 的回合结果维度正交)。

        keep_alive 子代理回合结束后 status=COMPLETED 但 future 存活(驻留),
        仍可接收消息 —— 这正是 keep_alive 的用途。已关闭或执行过程已真正
        结束的任务不能再收消息。
        """
        if self.closed:
            return False
        return not self.is_finished

    async def drain_user_queue(self, multi_agent: MultiAgent, config: AppConfig) -> str:
        """从 user_queue 取出所有待处理消息,分类处理:
        - !cmd → 执行 shell 命令,结果追加到 messages 让 LLM 可见
        - /command → 交由 UI 处理斜杠命令(不追加到 messages)
        - [system] 注入消息 → 单独成条追加到 messages(不与用户文本合并)
        - 其他 → 合并为一条用户消息追加到 messages
        返回需要 LLM 继续处理的消息文本:普通路径返回合并后的用户文本;
        仅系统注入消息时返回其内容,调用方据此让 LLM 在当前循环内看到它。"""
        from uniclaw.agent.multi_agent import (
            ShellCommandEvent,
            SlashCommandEvent,
            UserEvent,
        )

        messages = []
        while not self.user_queue.empty():
            try:
                messages.append(self.user_queue.get_nowait())
            except Exception as e:
                get_logger("agent", config.root_dir).debug(
                    "从用户队列获取消息失败: %s", e
                )
                break
        if not messages:
            return ""

        self.cancel_event.clear()
        text_parts = []
        # [system] 注入消息(压缩预警/goal 续工/overseer 续工/wakeup 等)是元信息,
        # 不与普通用户文本合并,单独成条注入 session,避免污染"用户说了什么"
        system_parts = []
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
                slash_output = await multi_agent.send_event_to_user(event, config)
                if isinstance(slash_output, str) and slash_output:
                    self.session.add_message(MessageRole.USER, slash_output)
            elif stripped.startswith(SYSTEM_PREFIX):
                system_parts.append(msg)
            else:
                text_parts.append(msg)

        # [system] 消息:每条单独注入(不互相合并,也不与普通文本合并)
        for sys_msg in system_parts:
            self.session.add_message(MessageRole.USER, sys_msg)
            await multi_agent.send_event_to_user(UserEvent(sys_msg), config)

        if text_parts:
            content = "\n\n".join(text_parts)
            self.session.add_message(MessageRole.USER, content)
            await multi_agent.send_event_to_user(UserEvent(content), config)
            return content
        # 只有系统注入消息时,返回其内容让调用方继续循环,确保 LLM 本轮能看到
        return "\n\n".join(system_parts)
