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


class _Blk:
    """轻量 MCP content 块替身,只带转换函数会读到的字段。"""

    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)

    def __repr__(self):  # pragma: no cover - 用于确认不再走 str() 倾泻
        return f"<Blk {self.type}>"


class TestMcpBlocksToContent:
    """_mcp_blocks_to_content / _mcp_media_block 的块转换。"""

    def test_pure_text_returns_str(self):
        """纯文本结果保持 str,不影响既有调用方。"""
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        out = _mcp_blocks_to_content(
            [_Blk("text", text="hello"), _Blk("text", text="world")]
        )
        assert out == "hello\nworld"

    def test_empty_returns_placeholder(self):
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        assert _mcp_blocks_to_content([]) == "(无输出)"

    def test_image_becomes_data_uri_block(self):
        """图片转成 OpenAI image_url 块,data URI 带原始 MIME。"""
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        out = _mcp_blocks_to_content(
            [
                _Blk("text", text="[截图]"),
                _Blk("image", data="aGVsbG8=", mimeType="image/png"),
            ]
        )
        assert isinstance(out, list)
        assert out[0] == {"type": "text", "text": "[截图]"}
        assert out[1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aGVsbG8="},
        }

    def test_audio_block(self):
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        out = _mcp_blocks_to_content([_Blk("audio", data="QQ==", mimeType="audio/mpeg")])
        assert out[0]["type"] == "input_audio"
        assert out[0]["input_audio"]["data"] == "data:audio/mpeg;base64,QQ=="

    def test_video_arrives_as_resource_blob(self):
        """MCP 无 VideoContent,视频只能以 resource blob 到来,应转成 video_url 块。"""
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        out = _mcp_blocks_to_content(
            [
                _Blk(
                    "resource",
                    resource=_Blk("blob", blob="Qg==", mimeType="video/mp4"),
                )
            ]
        )
        assert out[0]["type"] == "video_url"
        assert out[0]["video_url"]["url"] == "data:video/mp4;base64,Qg=="

    def test_resource_text_and_blob(self):
        """EmbeddedResource: 文本资源进 text,图片 blob 进 image_url。"""
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        out = _mcp_blocks_to_content(
            [
                _Blk("resource", resource=_Blk("text", text="resource body")),
                _Blk(
                    "resource",
                    resource=_Blk("blob", blob="aGVsbG8=", mimeType="image/jpeg"),
                ),
            ]
        )
        assert out[0] == {"type": "text", "text": "resource body"}
        assert out[1]["image_url"]["url"] == "data:image/jpeg;base64,aGVsbG8="

    def test_resource_link_placeholder(self):
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        out = _mcp_blocks_to_content(
            [_Blk("resource_link", name="doc.pdf", uri="file://doc.pdf")]
        )
        assert out == "[resource_link: doc.pdf (unknown)]"

    def test_unknown_block_does_not_dump_repr(self):
        """未知块类型只留类型占位,不把 repr(含 base64)倾泻进上下文。"""
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        out = _mcp_blocks_to_content([_Blk("weird", data="U0VDUkVU")])
        assert out == "[weird]"
        assert "U0VDUkVU" not in out

    def test_oversized_media_degrades_to_text(self):
        """超限媒体降级为文本占位,避免撑爆上下文。"""
        from uniclaw.tools.mcp import _mcp_media_block

        # base64 长度 * 3/4 = 原始字节;28MB base64 ≈ 21MB 原始,超过 image 20MB 上限
        huge = "A" * (28 * 1024 * 1024)
        blk = _mcp_media_block("image/png", huge)
        assert blk["type"] == "text"
        assert "内容过大" in blk["text"]

    def test_media_mixed_returns_list(self):
        """含媒体块时返回 list,multi_agent 的多模态分支才会触发。"""
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        out = _mcp_blocks_to_content(
            [_Blk("image", data="aGVsbG8=", mimeType="image/png")]
        )
        assert isinstance(out, list)
        assert any(b.get("type") in ("image_url", "input_audio", "video_url") for b in out)
