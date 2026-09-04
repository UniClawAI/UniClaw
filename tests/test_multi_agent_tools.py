"""多 Agent 工具测试 — 覆盖 subagent_* 工具的调用逻辑。"""

import pytest
from unittest.mock import MagicMock, patch

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.multi_agent.tools import (
    subagent_send_message,
    subagent_close,
    subagent_check_result,
    subagent_list_tasks,
    subagent_list_definitions,
    subagent_get_definition,
    get_tools,
    get_all_tools,
    get_sub_agent_system_prompt,
)


@pytest.fixture
def mock_mgr():
    """返回模拟的 MultiAgent 实例。"""
    mgr = MagicMock()
    mgr.send_message.return_value = True
    mgr.close_agent.return_value = True
    mgr.list_tasks.return_value = []
    return mgr


def _patch_get_instance(mock_mgr):
    """patch uniclaw.agent.MultiAgent.get_instance 返回 mock_mgr。"""
    return patch(
        "uniclaw.agent.MultiAgent.get_instance", return_value=mock_mgr
    )


class TestToolRegistration:
    """工具注册测试。"""

    def test_get_tools_returns_eight(self):
        """get_tools 返回 8 个工具。"""
        result = get_tools()
        assert len(result) == 8

    def test_get_all_tools_same(self):
        """get_all_tools 与 get_tools 相同。"""
        assert len(get_all_tools()) == len(get_tools())

    def test_tool_names(self):
        """工具名称齐全。"""
        names = [t.name for t in get_all_tools()]
        assert "subagent_create" in names
        assert "subagent_send_message" in names
        assert "subagent_close" in names
        assert "subagent_check_result" in names
        assert "subagent_list_tasks" in names
        assert "subagent_discuss" in names
        assert "subagent_get_definition" in names
        assert "subagent_list_definitions" in names


class TestSendMessage:
    """subagent_send_message 测试。"""

    @pytest.mark.asyncio
    async def test_send_success(self, mock_mgr):
        """发送成功。"""
        mock_mgr.send_message.return_value = True
        with _patch_get_instance(mock_mgr):
            result = await subagent_send_message("task-1", "hello")
        assert "已排队" in result

    @pytest.mark.asyncio
    async def test_agent_not_found(self, mock_mgr):
        """智能体不存在。"""
        mock_mgr.send_message.return_value = False
        mock_mgr.id2AgentTask = {}
        with _patch_get_instance(mock_mgr):
            result = await subagent_send_message("nonexistent", "hello")
        assert "无法找到" in result

    @pytest.mark.asyncio
    async def test_agent_not_running(self, mock_mgr):
        """智能体未运行。"""
        mock_mgr.send_message.return_value = False
        mock_task = MagicMock()
        mock_task.status = "stopped"
        mock_mgr.id2AgentTask = {"task-1": mock_task}
        with _patch_get_instance(mock_mgr):
            result = await subagent_send_message("task-1", "hello")
        assert "未运行" in result


class TestClose:
    """subagent_close 测试。"""

    @pytest.mark.asyncio
    async def test_close_success(self, mock_mgr):
        """成功关闭。"""
        mock_mgr.close_agent.return_value = True
        with _patch_get_instance(mock_mgr):
            result = await subagent_close("task-1")
        assert "已向" in result and "关闭信号" in result

    @pytest.mark.asyncio
    async def test_close_not_found(self, mock_mgr):
        """关闭不存在的智能体。"""
        mock_mgr.close_agent.return_value = False
        with _patch_get_instance(mock_mgr):
            result = await subagent_close("nonexistent")
        assert "未找到" in result


class TestCheckResult:
    """subagent_check_result 测试。"""

    @pytest.mark.asyncio
    async def test_check_not_found(self, mock_mgr):
        """查询不存在的任务。"""
        mock_mgr.id2AgentTask = {}
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result("nonexistent")
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_check_found(self, mock_mgr):
        """查询存在的任务。"""
        mock_task = MagicMock()
        mock_task.status = "finished"
        mock_task.name = "test-agent"
        mock_task.worktree_branch = None
        mock_task.session.get_assistant_messages.return_value = ["结果1", "结果2"]
        mock_task.result_read_index = 0
        mock_mgr.id2AgentTask = {"task-1": mock_task}
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result("task-1")
        assert "状态:" in result
        assert "名称:" in result
        assert "结果1" in result

    @pytest.mark.asyncio
    async def test_check_full(self, mock_mgr):
        """full=True 返回完整历史。"""
        mock_task = MagicMock()
        mock_task.status = "finished"
        mock_task.name = "test-agent"
        mock_task.worktree_branch = None
        mock_task.session.get_assistant_messages.return_value = ["结果1", "结果2"]
        mock_task.result_read_index = 0
        mock_task.result = "最终结果"
        mock_mgr.id2AgentTask = {"task-1": mock_task}
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result("task-1", full=True)
        assert "最终结果" in result or "结果1" in result


class TestListTasks:
    """subagent_list_tasks 测试。"""

    @pytest.mark.asyncio
    async def test_list_empty(self, mock_mgr):
        """无任务时返回提示。"""
        mock_mgr.list_tasks.return_value = []
        with _patch_get_instance(mock_mgr):
            result = await subagent_list_tasks()
        assert "没有子智能体任务" in result

    @pytest.mark.asyncio
    async def test_list_with_tasks(self, mock_mgr):
        """有任务时返回表格。"""
        mock_task = MagicMock()
        mock_task.id = "abc123"
        mock_task.name = "test"
        mock_task.status = "running"
        mock_task.worktree_branch = None
        mock_task.prompt = "do something"
        mock_mgr.list_tasks.return_value = [mock_task]
        with _patch_get_instance(mock_mgr):
            result = await subagent_list_tasks()
        assert "ID" in result
        assert "abc123" in result


class TestListDefinitions:
    """subagent_list_definitions 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    async def test_list_empty(self, mock_load, mock_mgr):
        """无定义时返回提示。"""
        mock_load.return_value = {}
        config = MagicMock()
        config.root_dir = None
        result = await subagent_list_definitions(tool_runtime=ToolRuntime(config=config))
        assert "没有可用的智能体类型" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    async def test_list_with_definitions(self, mock_load, mock_mgr):
        """有定义时返回列表。"""
        mock_def = MagicMock()
        mock_def.source = "built-in"
        mock_def.description = "test agent"
        mock_def.model_name = None
        mock_def.tools = None
        mock_load.return_value = {"coder": mock_def}
        config = MagicMock()
        config.root_dir = None
        result = await subagent_list_definitions(tool_runtime=ToolRuntime(config=config))
        assert "coder" in result
        assert "可用的智能体类型" in result


class TestGetDefinition:
    """subagent_get_definition 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    async def test_get_not_found(self, mock_load, mock_mgr):
        """查询不存在的类型。"""
        mock_load.return_value = {}
        config = MagicMock()
        config.root_dir = None
        result = await subagent_get_definition("nonexistent", tool_runtime=ToolRuntime(config=config))
        assert "未找到类型" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    async def test_get_found(self, mock_load, mock_mgr):
        """查询存在的类型。"""
        mock_def = MagicMock()
        mock_def.name = "coder"
        mock_def.source = "built-in"
        mock_def.description = "code writer"
        mock_def.model_name = "gpt-4o"
        mock_def.tools = ["Read", "Write"]
        mock_def.system_prompt = "You are a coder."
        mock_load.return_value = {"coder": mock_def}
        config = MagicMock()
        config.root_dir = None
        result = await subagent_get_definition("coder", tool_runtime=ToolRuntime(config=config))
        assert "coder" in result
        assert "code writer" in result
        assert "gpt-4o" in result


class TestGetSubAgentSystemPrompt:
    """get_sub_agent_system_prompt 测试。"""

    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    def test_empty_when_no_definitions(self, mock_load):
        """无定义时返回空字符串。"""
        mock_load.return_value = {}
        result = get_sub_agent_system_prompt()
        assert result == ""

    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    def test_contains_type_names(self, mock_load):
        """包含类型名称。"""
        mock_def = MagicMock()
        mock_def.tools = None
        mock_load.return_value = {"coder": mock_def, "reviewer": mock_def}
        result = get_sub_agent_system_prompt()
        assert "coder" in result
        assert "reviewer" in result
