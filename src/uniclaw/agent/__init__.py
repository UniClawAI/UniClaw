"""UniClaw 代理核心模块。

从 uniclaw.agent 导入所有符号保持向后兼容:
    from uniclaw.agent import MultiAgent, AgentTask, AgentStatus, ...
"""

from uniclaw.agent.types import AgentStatus, AgentTask
from uniclaw.agent.multi_agent import (
    # 事件类
    ReturnEvent,
    UserEvent,
    TextChunkEvent,
    ThinkingChunkEvent,
    ThinkingStartEvent,
    AssistantEvent,
    ToolPreparingEvent,
    ToolStartEvent,
    ToolEvent,
    ToolStreamEvent,
    CheckpointStartEvent,
    CheckpointEndEvent,
    EndEvent,
    InterruptedEvent,
    PermissionRequestEvent,
    SlashCommandEvent,
    ShellCommandEvent,
    # 核心类
    MultiAgent,
    # 函数
    _check_permission,
    _permission_desc,
    _edit_permission_diff,
)

__all__ = [
    "AgentStatus",
    "AgentTask",
    "ReturnEvent",
    "UserEvent",
    "TextChunkEvent",
    "ThinkingChunkEvent",
    "ThinkingStartEvent",
    "AssistantEvent",
    "ToolPreparingEvent",
    "ToolStartEvent",
    "ToolEvent",
    "ToolStreamEvent",
    "CheckpointStartEvent",
    "CheckpointEndEvent",
    "EndEvent",
    "InterruptedEvent",
    "PermissionRequestEvent",
    "SlashCommandEvent",
    "ShellCommandEvent",
    "MultiAgent",
    "_check_permission",
    "_permission_desc",
    "_edit_permission_diff",
]
