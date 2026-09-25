"""MCP 集成测试 — 覆盖 MCPTransport 枚举和工具注册。"""

import asyncio
import sys
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from uniclaw.tools.mcp.tools import MCPTransport


class TestMCPTransport:
    """MCPTransport 枚举测试。"""

    def test_transport_values(self):
        """传输类型枚举值正确。"""
        assert MCPTransport.stdio == "stdio"
        assert MCPTransport.sse == "sse"
        assert MCPTransport.streamable_http == "streamable_http"

    def test_transport_count(self):
        """共 3 种传输类型(mcp 2.x 已移除 websocket)。"""
        assert len(MCPTransport) == 3
        assert not hasattr(MCPTransport, "websocket")


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
                _Blk("image", data="aGVsbG8=", mime_type="image/png"),
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

        out = _mcp_blocks_to_content([_Blk("audio", data="QQ==", mime_type="audio/mpeg")])
        assert out[0]["type"] == "input_audio"
        assert out[0]["input_audio"]["data"] == "data:audio/mpeg;base64,QQ=="

    def test_video_arrives_as_resource_blob(self):
        """MCP 无 VideoContent,视频只能以 resource blob 到来,应转成 video_url 块。"""
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        out = _mcp_blocks_to_content(
            [
                _Blk(
                    "resource",
                    resource=_Blk("blob", blob="Qg==", mime_type="video/mp4"),
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
                    resource=_Blk("blob", blob="aGVsbG8=", mime_type="image/jpeg"),
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
            [_Blk("image", data="aGVsbG8=", mime_type="image/png")]
        )
        assert isinstance(out, list)
        assert any(b.get("type") in ("image_url", "input_audio", "video_url") for b in out)


class TestFlatError:
    """_flat_error 异常扁平化 — TaskGroup 报错须展开真实原因。"""

    def test_plain_exception_keeps_message(self):
        from uniclaw.tools.mcp import _flat_error

        assert _flat_error(ValueError("boom")) == "boom"

    def test_empty_str_falls_back_to_type_name(self):
        from uniclaw.tools.mcp import _flat_error

        assert _flat_error(TimeoutError()) == "TimeoutError"

    def test_group_unwraps_single_sub_exception(self):
        """TaskGroup 包装的连接失败,展开后露出真实原因。"""
        from uniclaw.tools.mcp import _flat_error

        group = ExceptionGroup(
            "unhandled errors in a TaskGroup (1 sub-exception)",
            [RuntimeError("All connection attempts failed")],
        )
        assert _flat_error(group) == "All connection attempts failed"

    def test_nested_group_flattens_all_leaves(self):
        from uniclaw.tools.mcp import _flat_error

        group = ExceptionGroup(
            "outer",
            [
                ExceptionGroup("inner", [RuntimeError("a"), RuntimeError("b")]),
                RuntimeError("c"),
            ],
        )
        assert _flat_error(group) == "a; b; c"


class TestTransportSupport:
    """mcp 2.x 无 websocket 客户端,该传输须直接报错并给出替代方案。"""

    async def test_websocket_unavailable(self):
        from uniclaw.tools.mcp import _connect_mcp

        with pytest.raises(RuntimeError, match="mcp 2.x 已移除"):
            async with _connect_mcp({"transport": "websocket", "url": "ws://x"}):
                pass


class TestConnectionErrorDisplay:
    """test_connection 的错误输出 — 用户可见的"连接验证失败"报错来源。"""

    async def test_exception_group_flattened(self):
        """TaskGroup 报错展开后显示真实原因,不再是 "unhandled errors in a TaskGroup"。"""
        from uniclaw.tools.mcp import MCPManager

        group = ExceptionGroup(
            "unhandled errors in a TaskGroup (1 sub-exception)",
            [RuntimeError("All connection attempts failed")],
        )
        with patch("uniclaw.tools.mcp.err", new=AsyncMock()) as err_mock, patch(
            "uniclaw.tools.mcp._discover_tools_async", new=AsyncMock(side_effect=group)
        ):
            ok = await MCPManager.get_instance().test_connection(
                {"transport": "sse", "url": "http://127.0.0.1:1/sse"}
            )

        assert ok is False
        msg = err_mock.await_args.args[0]
        assert "All connection attempts failed" in msg
        assert "TaskGroup" not in msg
        # 异常对象原样传给 err,完整堆栈由 err 落日志
        assert err_mock.await_args.kwargs["e"] is group

    async def test_timeout_passes_exception(self):
        """超时分支同样传异常对象,方便排查卡在哪个环节。"""
        from uniclaw.tools.mcp import MCPManager

        with patch("uniclaw.tools.mcp.err", new=AsyncMock()) as err_mock, patch(
            "uniclaw.tools.mcp._discover_tools_async",
            new=AsyncMock(side_effect=TimeoutError("deadline")),
        ):
            ok = await MCPManager.get_instance().test_connection(
                {"transport": "sse", "url": "http://127.0.0.1:1/sse"}
            )

        assert ok is False
        assert "超时" in err_mock.await_args.args[0]
        assert isinstance(err_mock.await_args.kwargs["e"], TimeoutError)


class TestMcp2xApi:
    """mcp 2.x API — 媒体块字段是 snake_case,streamable_http_client 只 yield (read, write)。"""

    def test_media_block_mime_type(self):
        """媒体块字段 mime_type 要能读到,不能取空降级成占位文本。"""
        from uniclaw.tools.mcp import _mcp_blocks_to_content

        blk = SimpleNamespace(type="image", data="aGVsbG8=", mime_type="image/png")
        out = _mcp_blocks_to_content([blk])
        assert isinstance(out, list)
        assert out[0]["type"] == "image_url"

    async def test_streamable_http_two_tuple_yield(self):
        """streamable_http_client 只 yield (read, write),按 2 元组解包。"""
        from uniclaw.tools.mcp import _connect_mcp

        fake_read, fake_write = object(), object()

        @asynccontextmanager
        async def fake_client(**kwargs):
            yield fake_read, fake_write

        with patch.dict(
            sys.modules,
            {
                "mcp.client.streamable_http": SimpleNamespace(
                    streamable_http_client=fake_client
                )
            },
        ):
            async with _connect_mcp(
                {"transport": "streamable_http", "url": "http://x"}
            ) as (r, w):
                assert r is fake_read
                assert w is fake_write
