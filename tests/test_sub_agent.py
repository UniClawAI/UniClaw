"""子代理定义测试 — 覆盖 AgentDefinition 和 load_agent_definitions。"""

import pytest
from unittest.mock import MagicMock, patch

from uniclaw.tools.multi_agent.sub_agent import (
    AgentDefinition,
    get_builtin_agent_definitions,
    load_agent_definitions,
    load_agent_definitions_from_scope,
)


class TestAgentDefinition:
    """AgentDefinition dataclass 测试。"""

    def test_defaults(self):
        """默认值。"""
        d = AgentDefinition(name="test")
        assert d.name == "test"
        assert d.description == ""
        assert d.system_prompt == ""
        assert d.model_name == ""
        assert d.tools == []
        assert d.source == "user"

    def test_custom_fields(self):
        """自定义字段。"""
        d = AgentDefinition(
            name="coder",
            description="编程",
            system_prompt="专注写码",
            model_name="gpt-4o",
            tools=["Read", "Write"],
            source="project",
        )
        assert d.model_name == "gpt-4o"
        assert d.source == "project"


class TestGetBuiltinDefinitions:
    """内置代理定义测试。"""

    def test_contains_key_types(self):
        """包含主要内置类型。"""
        defs = get_builtin_agent_definitions()
        for name in ["general-purpose", "coder", "reviewer", "researcher", "tester", "recon"]:
            assert name in defs

    def test_all_builtin_source(self):
        """所有定义 source 为 built-in。"""
        defs = get_builtin_agent_definitions()
        assert all(d.source == "built-in" for d in defs.values())

    def test_each_has_description(self):
        """每个定义都有描述。"""
        defs = get_builtin_agent_definitions()
        for name, d in defs.items():
            assert d.description, f"{name} 缺少描述"

    def test_coder_has_system_prompt(self):
        """coder 有系统提示词。"""
        defs = get_builtin_agent_definitions()
        assert "编程" in defs["coder"].system_prompt

    def test_general_purpose_all_tools(self):
        """general-purpose 无工具限制(空列表)。"""
        defs = get_builtin_agent_definitions()
        assert defs["general-purpose"].tools == []

    def test_reviewer_limited_tools(self):
        """reviewer 限制工具。"""
        defs = get_builtin_agent_definitions()
        assert "Read" in defs["reviewer"].tools
        assert "Grep" in defs["reviewer"].tools

    def test_returns_fresh_dict(self):
        """每次调用返回独立对象。"""
        d1 = get_builtin_agent_definitions()
        d2 = get_builtin_agent_definitions()
        assert d1 is not d2
        assert d1["coder"] is not d2["coder"]


class TestLoadFromScope:
    """load_agent_definitions_from_scope 测试。"""

    def test_empty_dir(self, tmp_path):
        """无 agents 目录返回空。"""
        defs = load_agent_definitions_from_scope(tmp_path)
        assert defs == {}

    def test_load_md_files(self, tmp_path):
        """解析 agents 目录下的 md 文件。"""
        agents_dir = tmp_path / ".UniClaw" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "coder.md").write_text(
            "---\n"
            "name: coder\n"
            "description: 编程代理\n"
            "model: gpt-4o\n"
            "tools:\n"
            "  - Read\n"
            "  - Write\n"
            "---\n"
            "你是一个编程助手。",
            encoding="utf-8",
        )
        defs = load_agent_definitions_from_scope(tmp_path)
        assert "coder" in defs
        d = defs["coder"]
        assert d.source == "user"
        assert d.description == "编程代理"
        assert d.model_name == "gpt-4o"
        assert d.tools == ["Read", "Write"]
        assert "编程助手" in d.system_prompt


class TestLoadDefinitions:
    """load_agent_definitions 测试。"""

    @patch("uniclaw.tools.multi_agent.sub_agent.load_agent_definitions_from_scope")
    def test_without_root_dir(self, mock_scope):
        """无 root_dir 时合并内置 + 用户。"""
        mock_scope.return_value = {
            "my-agent": AgentDefinition(name="my-agent", source="user")
        }
        defs = load_agent_definitions()
        assert "coder" in defs  # 内置
        assert "my-agent" in defs  # 用户
        assert mock_scope.call_count == 1

    @patch("uniclaw.tools.multi_agent.sub_agent.load_agent_definitions_from_scope")
    def test_with_root_dir(self, mock_scope):
        """有 root_dir 时额外加载项目定义。"""
        mock_scope.side_effect = [
            {"user-agent": AgentDefinition(name="user-agent", source="user")},
            {"proj-agent": AgentDefinition(name="proj-agent", source="project")},
        ]
        defs = load_agent_definitions("project")
        assert "coder" in defs  # 内置
        assert "user-agent" in defs  # 用户
        assert "proj-agent" in defs  # 项目
        assert mock_scope.call_count == 2

    @patch("uniclaw.tools.multi_agent.sub_agent.load_agent_definitions_from_scope")
    def test_project_overrides_user(self, mock_scope):
        """同名时项目覆盖用户。"""
        mock_scope.side_effect = [
            {"dup": AgentDefinition(name="dup", source="user")},
            {"dup": AgentDefinition(name="dup", source="project")},
        ]
        defs = load_agent_definitions("project")
        assert defs["dup"].source == "project"
