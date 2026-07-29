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
            else:
                text_parts.append(msg)

        if text_parts:
            content = "\n\n".join(text_parts)
            self.session.add_message(MessageRole.USER, content)
            await multi_agent.send_event_to_user(UserEvent(content), config)
            return content
        return ""
