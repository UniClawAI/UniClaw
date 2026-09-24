"""MCP 集成测试 — 覆盖 MCPTransport 枚举和工具注册。"""

import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import patch, MagicMock

import pytest

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


class _FakeClientSession:
    """模拟 mcp.ClientSession:initialize 可注入延迟以暴露并发竞态。"""

    init_count = 0
    init_delay = 0.05

    def __init__(self, read, write):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def initialize(self):
        _FakeClientSession.init_count += 1
        if _FakeClientSession.init_delay:
            import asyncio

            await asyncio.sleep(_FakeClientSession.init_delay)

    async def call_tool(self, name, arguments=None):
        result = MagicMock()
        result.content = []
        return result


@asynccontextmanager
async def _fake_connect(connection):
    yield (None, None)


class TestPersistentSession:
    """_PersistentSession 并发建连/闲置回收行为。"""

    async def test_concurrent_cold_start_shows_one_connect(self):
        """并发冷启动只建立一次连接,所有调用方拿到同一会话。"""
        from uniclaw.tools.mcp import _PersistentSession

        _FakeClientSession.init_count = 0
        _FakeClientSession.init_delay = 0.05
        ps = _PersistentSession("srv", {"transport": "stdio"})

        with patch("uniclaw.tools.mcp._connect_mcp", _fake_connect), patch(
            "mcp.ClientSession", _FakeClientSession
        ):
            results = await asyncio.gather(
                ps.get_session(),
                ps.get_session(),
                ps.get_session(),
                return_exceptions=True,
            )

        assert all(not isinstance(r, Exception) for r in results), results
        assert results[0] is results[1] is results[2]
        assert _FakeClientSession.init_count == 1

    async def test_concurrent_init_failure_raises_for_all(self):
        """建连失败时并发调用方全部收到错误,不会挂起。"""
        from uniclaw.tools.mcp import _PersistentSession

        @asynccontextmanager
        async def failing_connect(connection):
            raise RuntimeError("connect refused")
            yield (None, None)  # pragma: no cover

        ps = _PersistentSession("srv", {"transport": "stdio"})

        with patch("uniclaw.tools.mcp._connect_mcp", failing_connect), patch(
            "mcp.ClientSession", _FakeClientSession
        ):
            results = await asyncio.gather(
                ps.get_session(),
                ps.get_session(),
                return_exceptions=True,
            )

        assert all(isinstance(r, Exception) for r in results), results

    async def test_sweep_skips_busy_and_recent_sessions(self):
        """扫描不回收调用中/近期使用的连接,只回收长期闲置的。"""
        from uniclaw.tools.mcp import (
            PERSISTENT_SESSION_IDLE_TIMEOUT,
            MCPManager,
            _PersistentSession,
        )

        mgr = MCPManager()
        busy = _PersistentSession("srv", {})
        busy._in_flight = 1
        recent = _PersistentSession("srv", {})
        stale = _PersistentSession("srv", {})
        stale._last_used = time.monotonic() - (PERSISTENT_SESSION_IDLE_TIMEOUT + 60)

        mgr._persistent_sessions = {
            ("sess-a", "srv"): busy,
            ("sess-b", "srv"): recent,
            ("sess-c", "srv"): stale,
        }
        await mgr.sweep_orphaned_persistent_sessions()

        assert ("sess-a", "srv") in mgr._persistent_sessions
        assert ("sess-b", "srv") in mgr._persistent_sessions
        assert ("sess-c", "srv") not in mgr._persistent_sessions
