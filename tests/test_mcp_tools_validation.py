"""tools/mcp/tools.py 参数校验分支测试。

mock MCPManager,只测工具函数的参数校验与错误信息拼装:
- mcp_add_server: 重复名/stdio 缺 command/sse 缺 url/websocket 已移除/成功路径
- mcp_remove_server / mcp_toggle_server: 不存在/成功
- mcp_list_servers: 空列表/条目渲染
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.mcp.tools import (
    mcp_add_server,
    mcp_list_servers,
    mcp_remove_server,
    mcp_toggle_server,
)
from uniclaw.utils.constants import TOOL_ERROR


def _f(tool_obj):
    """@tool 装饰器对象 → 原始协程函数。"""
    return tool_obj.func


@pytest.fixture
def manager():
    """mock MCPManager 单例,记录调用。"""
    m = MagicMock()
    m.get_server = AsyncMock(return_value=None)
    m.add_server = AsyncMock()
    m.remove_server = AsyncMock()
    m.toggle_server = AsyncMock()
    m.get_mcp_tools = AsyncMock(return_value=["t1", "t2"])
    m.list_servers = AsyncMock(return_value=[])
    m.get_tools_info = MagicMock(return_value=[])
    with patch("uniclaw.tools.mcp.tools.MCPManager") as cls:
        cls.get_instance.return_value = m
        yield m


# ── mcp_add_server ───────────────────────────────────────────


class TestAddServer:
    async def test_duplicate_rejected(self, manager):
        manager.get_server.return_value = {"name": "fs"}
        result = await mcp_add_server.func(name="fs", tool_runtime=ToolRuntime(config=None))
        assert TOOL_ERROR in result
        assert "已存在" in result
        manager.add_server.assert_not_awaited()

    async def test_stdio_requires_command(self, manager):
        result = await mcp_add_server.func(
            name="fs", transport="stdio", command=None, tool_runtime=ToolRuntime(config=None)
        )
        assert TOOL_ERROR in result
        assert "command" in result

    async def test_stdio_success(self, manager):
        result = await mcp_add_server.func(
            name="fs",
            transport="stdio",
            command="npx",
            command_args=["-y", "server"],
            env={"A": "1"},
            tool_runtime=ToolRuntime(config=None),
        )
        assert "成功" in result
        connection = manager.add_server.await_args.args[1]
        assert connection["transport"] == "stdio"
        assert connection["command"] == "npx"
        assert connection["args"] == ["-y", "server"]
        assert connection["env"] == {"A": "1"}

    async def test_sse_requires_url(self, manager):
        result = await mcp_add_server.func(
            name="web", transport="sse", url=None, tool_runtime=ToolRuntime(config=None)
        )
        assert TOOL_ERROR in result
        assert "url" in result

    async def test_streamable_http_with_headers_and_timeout(self, manager):
        result = await mcp_add_server.func(
            name="web",
            transport="streamable_http",
            url="http://localhost:8080/mcp",
            headers={"Authorization": "Bearer x"},
            timeout=30.0,
            tool_runtime=ToolRuntime(config=None),
        )
        assert "成功" in result
        connection = manager.add_server.await_args.args[1]
        assert connection["url"] == "http://localhost:8080/mcp"
        assert connection["headers"] == {"Authorization": "Bearer x"}
        assert connection["timeout"] == 30.0

    async def test_websocket_rejected(self, manager):
        """mcp 2.x 已移除 websocket 客户端,该传输直接拒绝。"""
        result = await mcp_add_server.func(
            name="ws", transport="websocket", url="ws://localhost:9000", tool_runtime=ToolRuntime(config=None)
        )
        assert TOOL_ERROR in result
        assert "不支持的传输类型" in result
        manager.add_server.assert_not_called()

    async def test_add_failure_returns_error(self, manager):
        manager.add_server.side_effect = ValueError("连接验证失败")
        result = await mcp_add_server.func(
            name="fs", transport="stdio", command="npx", tool_runtime=ToolRuntime(config=None)
        )
        assert TOOL_ERROR in result
        assert "连接验证失败" in result

    async def test_add_unexpected_exception(self, manager):
        manager.add_server.side_effect = RuntimeError("boom")
        result = await mcp_add_server.func(
            name="fs", transport="stdio", command="npx", tool_runtime=ToolRuntime(config=None)
        )
        assert TOOL_ERROR in result
        assert "boom" in result


# ── mcp_remove_server ────────────────────────────────────────


class TestRemoveServer:
    async def test_not_found(self, manager):
        manager.get_server.return_value = None
        result = await mcp_remove_server.func(name="nope", tool_runtime=ToolRuntime(config=None))
        assert TOOL_ERROR in result
        assert "不存在" in result

    async def test_success(self, manager):
        manager.get_server.return_value = {"name": "fs"}
        result = await mcp_remove_server.func(name="fs", tool_runtime=ToolRuntime(config=None))
        assert "成功" in result
        manager.remove_server.assert_awaited_once_with("fs", None)

    async def test_remove_failure(self, manager):
        manager.get_server.return_value = {"name": "fs"}
        manager.remove_server.side_effect = RuntimeError("io error")
        result = await mcp_remove_server.func(name="fs", tool_runtime=ToolRuntime(config=None))
        assert TOOL_ERROR in result
        assert "io error" in result


# ── mcp_toggle_server ────────────────────────────────────────


class TestToggleServer:
    async def test_not_found(self, manager):
        manager.get_server.return_value = None
        result = await mcp_toggle_server.func(name="nope", enabled=True, tool_runtime=ToolRuntime(config=None))
        assert TOOL_ERROR in result
        assert "不存在" in result

    async def test_enable(self, manager):
        manager.get_server.return_value = {"name": "fs"}
        result = await mcp_toggle_server.func(name="fs", enabled=True, tool_runtime=ToolRuntime(config=None))
        assert "成功" in result
        assert "启用" in result
        manager.toggle_server.assert_awaited_once_with("fs", True, None)

    async def test_disable(self, manager):
        manager.get_server.return_value = {"name": "fs"}
        result = await mcp_toggle_server.func(name="fs", enabled=False, tool_runtime=ToolRuntime(config=None))
        assert "禁用" in result
        manager.toggle_server.assert_awaited_once_with("fs", False, None)


# ── mcp_list_servers ─────────────────────────────────────────


class TestListServers:
    async def test_empty(self, manager):
        manager.list_servers.return_value = []
        result = await mcp_list_servers.func(tool_runtime=ToolRuntime(config=None))
        assert "暂无 MCP 服务器配置" in result

    async def test_stdio_entry_rendering(self, manager):
        manager.list_servers.return_value = [
            {
                "name": "fs",
                "transport": "stdio",
                "enabled": True,
                "command": "npx",
                "args": ["-y", "server-fs", "/tmp"],
            }
        ]
        result = await mcp_list_servers.func(tool_runtime=ToolRuntime(config=None))
        assert "共 1 个" in result
        assert "[✓ 启用] fs (stdio)" in result
        assert "npx -y server-fs /tmp" in result

    async def test_disabled_sse_entry(self, manager):
        manager.list_servers.return_value = [
            {
                "name": "web",
                "transport": "sse",
                "enabled": False,
                "url": "http://localhost:8080/sse",
            }
        ]
        result = await mcp_list_servers.func(tool_runtime=ToolRuntime(config=None))
        assert "[✗ 禁用] web (sse)" in result
        assert "http://localhost:8080/sse" in result

    async def test_tools_info_rendering(self, manager):
        manager.list_servers.return_value = [
            {"name": "fs", "transport": "stdio", "enabled": True, "command": "npx"}
        ]
        manager.get_tools_info.return_value = [
            {"name": "read_file", "description": "读取文件内容"}
        ]
        result = await mcp_list_servers.func(tool_runtime=ToolRuntime(config=None))
        assert "工具数量: 1 个" in result
        assert "read_file: 读取文件内容" in result

    async def test_long_description_truncated(self, manager):
        manager.list_servers.return_value = [
            {"name": "fs", "transport": "stdio", "enabled": True, "command": "npx"}
        ]
        long_desc = "长" * 150
        manager.get_tools_info.return_value = [{"name": "t", "description": long_desc}]
        result = await mcp_list_servers.func(tool_runtime=ToolRuntime(config=None))
        assert "..." in result
        assert long_desc not in result

    async def test_zero_tools(self, manager):
        manager.list_servers.return_value = [
            {"name": "fs", "transport": "stdio", "enabled": True, "command": "npx"}
        ]
        manager.get_tools_info.return_value = []
        result = await mcp_list_servers.func(tool_runtime=ToolRuntime(config=None))
        assert "工具数量: 0 个" in result
