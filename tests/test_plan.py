"""计划模式测试 — 覆盖 ExitPermission 枚举和权限切换逻辑。"""

import pytest
from unittest.mock import MagicMock

from uniclaw.tools.plan import (
    ExitPermission,
    enter_plan_mode,
    exit_plan_mode,
    get_plan_system_prompt,
    get_tools,
    get_all_tools,
)
from uniclaw.config import Permissions


class TestExitPermission:
    """ExitPermission 枚举测试。"""

    def test_values(self):
        """枚举值正确。"""
        assert ExitPermission.AUTO == "auto"
        assert ExitPermission.ACCEPT_ALL == "accept-all"

    def test_count(self):
        """共 2 种退出权限。"""
        assert len(ExitPermission) == 2


class TestPlanToolsRegistration:
    """工具注册测试。"""

    def test_get_tools_returns_two(self):
        """返回 enter 和 exit 两个工具。"""
        result = get_tools()
        assert len(result) == 2

    def test_get_all_tools_same(self):
        """get_all_tools 与 get_tools 相同。"""
        assert len(get_all_tools()) == len(get_tools())


class TestEnterPlanMode:
    """enter_plan_mode 测试。"""

    @pytest.mark.asyncio
    async def test_sets_permission_mode(self):
        """进入计划模式后权限变为 PLAN。"""
        config = MagicMock()
        config.permission_mode = Permissions.AUTO
        config.is_console = True
        config.root_dir = None
        result = await enter_plan_mode(config=config)
        assert config.permission_mode == Permissions.PLAN
        assert "计划模式" in result

    @pytest.mark.asyncio
    async def test_returns_instructions(self):
        """返回包含操作指引的消息。"""
        config = MagicMock()
        config.permission_mode = Permissions.AUTO
        config.is_console = True
        config.root_dir = None
        result = await enter_plan_mode(config=config)
        assert "计划" in result


class TestExitPlanMode:
    """exit_plan_mode 测试。"""

    @pytest.mark.asyncio
    async def test_exit_to_auto(self):
        """退出到 auto 模式。"""
        config = MagicMock()
        result = await exit_plan_mode(permission_mode=ExitPermission.AUTO, config=config)
        assert "已退出计划模式" in result
        assert "auto" in result

    @pytest.mark.asyncio
    async def test_exit_to_accept_all(self):
        """退出到 accept-all 模式。"""
        config = MagicMock()
        result = await exit_plan_mode(permission_mode=ExitPermission.ACCEPT_ALL, config=config)
        assert "已退出计划模式" in result
        assert "accept-all" in result


class TestGetPlanSystemPrompt:
    """get_plan_system_prompt 测试。"""

    def test_non_root_agent_returns_empty(self):
        """非 root agent 返回空字符串。"""
        config = MagicMock()
        config.current_agent.name = "sub-agent"
        result = get_plan_system_prompt(config)
        assert result == ""

    def test_non_plan_mode_returns_intro(self):
        """非计划模式返回简介。"""
        config = MagicMock()
        config.current_agent.name = "root"
        config.permission_mode = Permissions.AUTO
        result = get_plan_system_prompt(config)
        assert "计划模式" in result
        assert "enter_plan_mode" in result

    def test_plan_mode_returns_full_instructions(self):
        """计划模式返回完整指引。"""
        config = MagicMock()
        config.current_agent.name = "root"
        config.permission_mode = Permissions.PLAN
        config.is_console = True
        config.root_dir = None
        result = get_plan_system_prompt(config)
        assert "计划模式" in result
        assert "审核流程" in result
