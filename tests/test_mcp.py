"""MCP 集成测试 — 覆盖 MCPTransport 枚举和工具注册。"""

import pytest
from unittest.mock import patch, MagicMock

from uniclaw.tools.mcp.tools import MCPTransport


class TestMCPTransport:
    """MCPTransport 枚举测试。"""

    def test_transport_values(self):
        """传输类型枚举值正确。"""
        assert MCPTransport.stdio == "stdio"
        assert MCPTransport.sse == "sse"
        assert MCPTransport.streamable_http == "streamable_http"
        assert MCPTransport.websocket == "websocket"

    def test_transport_count(self):
        """共 4 种传输类型。"""
        assert len(MCPTransport) == 4


class TestMCPToolsRegistration:
    """MCP 工具注册测试。"""

    def test_get_tools_returns_list(self):
        """get_tools 返回列表。"""
        from uniclaw.tools.mcp.tools import get_tools

        result = get_tools()
        assert isinstance(result, list)

    def test_get_all_tools_returns_list(self):
        """get_all_tools 返回列表。"""
        from uniclaw.tools.mcp.tools import get_all_tools

        result = get_all_tools()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_tools_have_descriptions(self):
        """所有工具都有描述。"""
        from uniclaw.tools.mcp.tools import get_all_tools

        for t in get_all_tools():
            assert t.description, f"{t.name} 缺少描述"
