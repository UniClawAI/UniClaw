"""tools/help.py 测试 — 斜杠命令帮助工具(原本 17% 覆盖)。

用真实 COMMANDS/COMMAND_SUBCOMMANDS 注册表测试,确保命令列表与帮助生成正确。
"""

import pytest
from unittest.mock import MagicMock, patch

from uniclaw.tools.help import (
    list_slash_commands,
    get_command_help,
    list_builtin_tools,
    get_tools,
    get_all_tools,
)
from uniclaw.commands import COMMANDS


class TestGetTools:
    def test_registered(self):
        names = [t.name for t in get_tools()]
        assert names == ["list_slash_commands", "get_command_help", "list_builtin_tools"]
        assert get_all_tools() == get_tools()


class TestListSlashCommands:
    @pytest.mark.asyncio
    async def test_returns_all_primary_commands(self):
        result = await list_slash_commands.func()
        assert "可用的斜杠命令" in result
        # 至少列出多个命令
        assert result.count("\n/") >= 5

    @pytest.mark.asyncio
    async def test_mentions_use_get_command_help(self):
        result = await list_slash_commands.func()
        assert get_command_help.name in result

    @pytest.mark.asyncio
    async def test_counts_commands_and_aliases(self):
        result = await list_slash_commands.func()
        assert "共 " in result
        assert "个命令(含 " in result

    @pytest.mark.asyncio
    async def test_known_command_present(self):
        result = await list_slash_commands.func()
        assert "/clear" in result or "/memory" in result or "/help" in result


class TestGetCommandHelp:
    @pytest.mark.asyncio
    async def test_known_command(self):
        # 找一个真实命令
        a_cmd = next(iter(COMMANDS))
        result = await get_command_help.func(command_name=a_cmd)
        assert result.startswith(f"/{a_cmd}")

    @pytest.mark.asyncio
    async def test_unknown_command_suggests(self):
        result = await get_command_help.func(command_name="nonexistent_cmd_xyz")
        assert "未找到命令" in result
        assert "你是否想输入" in result or list_slash_commands.name in result

    @pytest.mark.asyncio
    async def test_fuzzy_match(self):
        """输入子串能匹配到近似命令。"""
        # 找一个真实命令名,用其前缀去模糊匹配
        a_cmd = next(iter(COMMANDS))
        prefix = a_cmd[:3]
        result = await get_command_help.func(command_name=prefix)
        assert "未找到命令" not in result or "你是否想输入" in result

    @pytest.mark.asyncio
    async def test_strips_slash_and_case(self):
        """去掉前缀 / 并转小写。"""
        a_cmd = next(iter(COMMANDS))
        result_upper = await get_command_help.func(command_name=f"/{a_cmd.upper()}")
        result_plain = await get_command_help.func(command_name=a_cmd)
        assert result_upper == result_plain

    @pytest.mark.asyncio
    async def test_handler_with_doc(self):
        """有 docstring 的命令展示文档。"""
        for name, handler in list(COMMANDS.items())[:20]:
            if handler.__doc__:
                result = await get_command_help.func(command_name=name)
                assert handler.__doc__.strip().split("\n")[0] in result
                return
        pytest.skip("没有找到带 docstring 的命令")

    @pytest.mark.asyncio
    async def test_unknown_no_fuzzy_fallback(self):
        """完全无匹配时提示用 list_slash_commands。"""
        result = await get_command_help.func(command_name="zzz_zzz_zzz_qwerty")
        assert list_slash_commands.name in result


class TestListBuiltinTools:
    @pytest.mark.asyncio
    async def test_returns_names(self):
        with patch(
            "uniclaw.tools.registry.get_builtin_tool_names",
            return_value=["Read", "Write", "Bash"],
        ):
            result = await list_builtin_tools.func()
        assert "Read" in result
        assert "Write" in result
        assert "3 个" in result

    @pytest.mark.asyncio
    async def test_empty_registry(self):
        with patch(
            "uniclaw.tools.registry.get_builtin_tool_names",
            return_value=[],
        ):
            result = await list_builtin_tools.func()
        assert "0 个" in result