"""
agent.py 模块的单元测试

测试事件类、权限检查、消息队列等核心功能
"""

import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from uniclaw.agent import (
    ReturnEvent,
    UserEvent,
    TextChunkEvent,
    ThinkingChunkEvent,
    ThinkingStartEvent,
    AssistantEvent,
    ToolPreparingEvent,
    ToolStartEvent,
    ToolEvent,
    EndEvent,
    InterruptedEvent,
    PermissionRequestEvent,
    SlashCommandEvent,
    ShellCommandEvent,
    AgentStatus,
    _permission_desc,
    _edit_permission_diff,
)


class TestReturnEvent:
    """ReturnEvent 类的测试"""

    def test_initialization_default(self):
        """测试默认初始化"""
        event = ReturnEvent()
        assert event.content is None
        assert hasattr(event, "return_event")
        assert isinstance(event.return_event, asyncio.Event)

    def test_initialization_with_content(self):
        """测试带内容初始化"""
        event = ReturnEvent(default_content="test")
        assert event.content == "test"

    def test_return_event_is_event(self):
        """测试 return_event 是 asyncio.Event"""
        event = ReturnEvent()
        assert not event.return_event.is_set()


class TestEventClasses:
    """事件类的测试"""

    def test_user_event(self):
        """测试 UserEvent"""
        event = UserEvent(content="Hello")
        assert event.content == "Hello"

    def test_text_chunk_event(self):
        """测试 TextChunkEvent"""
        event = TextChunkEvent(content="chunk")
        assert event.content == "chunk"

    def test_thinking_chunk_event(self):
        """测试 ThinkingChunkEvent"""
        event = ThinkingChunkEvent(content="thinking")
        assert event.content == "thinking"

    def test_thinking_start_event(self):
        """测试 ThinkingStartEvent"""
        event = ThinkingStartEvent()
        assert event is not None

    def test_assistant_event(self):
        """测试 AssistantEvent"""
        event = AssistantEvent(
            content="response",
            tool_calls=[{"name": "test"}],
            in_tokens=10,
            out_tokens=20,
            model_name="gpt-4",
            cached_tokens=8,
            cache_write_tokens=2,
            cache_discount=0.4,
        )
        assert event.content == "response"
        assert event.tool_calls == [{"name": "test"}]
        assert event.in_tokens == 10
        assert event.out_tokens == 20
        assert event.model_name == "gpt-4"
        assert event.cached_tokens == 8
        assert event.cache_write_tokens == 2
        assert event.cache_discount == 0.4

    def test_assistant_event_defaults(self):
        """测试 AssistantEvent 默认值"""
        event = AssistantEvent(content="test", tool_calls=[])
        assert event.in_tokens == 0
        assert event.out_tokens == 0
        assert event.model_name == ""
        assert event.cached_tokens == 0
        assert event.cache_write_tokens == 0
        assert event.cache_discount == 0.0

    def test_tool_preparing_event(self):
        """测试 ToolPreparingEvent"""
        event = ToolPreparingEvent(name="Read", args={"file_path": "test.py"})
        assert event.name == "Read"
        assert event.args == {"file_path": "test.py"}

    def test_tool_preparing_event_default_args(self):
        """测试 ToolPreparingEvent 默认参数"""
        event = ToolPreparingEvent(name="Read")
        assert event.args == {}

    def test_tool_start_event(self):
        """测试 ToolStartEvent"""
        event = ToolStartEvent(name="Write", args={"file_path": "test.py"})
        assert event.name == "Write"
        assert event.args == {"file_path": "test.py"}

    def test_tool_event(self):
        """测试 ToolEvent"""
        event = ToolEvent(
            name="Read",
            content="file content",
            tool_call_id="call_123",
            args={"file_path": "test.py"},
        )
        assert event.name == "Read"
        assert event.content == "file content"
        assert event.tool_call_id == "call_123"
        assert event.args == {"file_path": "test.py"}

    def test_tool_event_default_args(self):
        """测试 ToolEvent 默认参数"""
        event = ToolEvent(name="Read", content="content", tool_call_id="call_1")
        assert event.args is None

    def test_end_event(self):
        """测试 EndEvent"""
        event = EndEvent(depth=3)
        assert event.depth == 3

    def test_interrupted_event(self):
        """测试 InterruptedEvent"""
        event = InterruptedEvent()
        assert event.message == "已中断,等待您的补充指令..."

    def test_interrupted_event_custom_message(self):
        """测试 InterruptedEvent 自定义消息"""
        event = InterruptedEvent(message="自定义中断消息")
        assert event.message == "自定义中断消息"


class TestPermissionRequestEvent:
    """PermissionRequestEvent 的测试"""

    def test_initialization(self):
        """测试初始化"""
        event = PermissionRequestEvent(
            description="运行命令",
            tool_call={"name": "Bash", "args": {"command": "ls"}},
            explanation="需要执行命令",
        )
        assert event.description == "运行命令"
        assert event.tool_call == {"name": "Bash", "args": {"command": "ls"}}
        assert event.explanation == "需要执行命令"
        assert event.content == "无可用的 UI 响应,自动拒绝权限请求"
        assert hasattr(event, "return_event")

    def test_default_tool_call(self):
        """测试默认 tool_call"""
        event = PermissionRequestEvent(description="测试")
        assert event.tool_call == {}

    def test_default_explanation(self):
        """测试默认 explanation"""
        event = PermissionRequestEvent(description="测试")
        assert event.explanation == ""


class TestSlashCommandEvent:
    """SlashCommandEvent 的测试"""

    def test_initialization(self):
        """测试初始化"""
        event = SlashCommandEvent(command="/help")
        assert event.command == "/help"
        assert event.content is None
        assert hasattr(event, "return_event")


class TestShellCommandEvent:
    """ShellCommandEvent 的测试"""

    def test_initialization(self):
        """测试初始化"""
        event = ShellCommandEvent(command="ls -la")
        assert event.command == "ls -la"
        assert event.content is None
        assert hasattr(event, "return_event")


class TestAgentStatus:
    """AgentStatus 枚举的测试"""

    def test_status_values(self):
        """测试状态值"""
        assert AgentStatus.PENDING == "pending"
        assert AgentStatus.RUNNING == "running"
        assert AgentStatus.WAITING == "waiting"
        assert AgentStatus.COMPLETED == "completed"
        assert AgentStatus.FAILED == "failed"
        assert AgentStatus.CANCELLED == "cancelled"
        assert AgentStatus.LOST == "lost"

    def test_status_is_string(self):
        """测试状态是字符串"""
        assert isinstance(AgentStatus.PENDING, str)
        assert isinstance(AgentStatus.COMPLETED, str)


class TestPermissionDesc:
    """_permission_desc 函数的测试"""

    def test_bash_command(self):
        """测试 Bash 命令描述"""
        tc = {"name": "Bash", "args": {"command": "ls -la"}}
        desc = _permission_desc(tc)
        assert "🖥️" in desc
        assert "ls -la" in desc

    def test_write_file(self):
        """测试 Write 工具描述"""
        tc = {"name": "Write", "args": {"file_path": "/tmp/test.py"}}
        desc = _permission_desc(tc)
        assert "📝" in desc
        assert "/tmp/test.py" in desc

    def test_edit_file(self):
        """测试 Edit 工具描述"""
        tc = {
            "name": "Edit",
            "args": {
                "file_path": "/tmp/test.py",
                "old_string": "old",
                "new_string": "new",
                "replace_all": False,
            },
        }
        desc = _permission_desc(tc)
        assert "✏️" in desc
        assert "/tmp/test.py" in desc

    def test_other_tool(self):
        """测试其他工具描述"""
        tc = {"name": "Read", "args": {"file_path": "/tmp/test.py"}}
        desc = _permission_desc(tc)
        assert "🔧" in desc
        assert "Read" in desc


class TestEditPermissionDiff:
    """_edit_permission_diff 函数的测试"""

    def test_simple_diff(self):
        """测试简单差异"""
        diff = _edit_permission_diff("test.py", "old content", "new content")
        assert "拟修改 diff:" in diff
        assert "-old content" in diff
        assert "+new content" in diff

    def test_no_diff(self):
        """测试无差异"""
        diff = _edit_permission_diff("test.py", "same content", "same content")
        assert "拟修改内容无差异" in diff

    def test_multiline_diff(self):
        """测试多行差异"""
        old = "line1\nline2\nline3"
        new = "line1\nmodified\nline3"
        diff = _edit_permission_diff("test.py", old, new)
        assert "-line2" in diff
        assert "+modified" in diff

    def test_large_diff_truncation(self):
        """测试大差异截断"""
        old = "\n".join([f"old line {i}" for i in range(200)])
        new = "\n".join([f"new line {i}" for i in range(200)])
        diff = _edit_permission_diff("test.py", old, new)
        assert "more diff lines hidden" in diff
