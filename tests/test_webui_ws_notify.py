"""WebUI 会话创建/切换通知测试 — 多页面场景下事件不得劫持其他页面的当前会话。

行为约定:
- session_created / session_switched 的"切换"动作只发给发起连接
- 其余连接收到 notify_only=True 的同名事件,仅用于刷新会话列表
- /api/sessions 合并内存缓存,未落盘的新会话也能被其他页面列出
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from uniclaw.webui import ws as ws_mod


class FakeWS:
    """记录 send_json 调用的假 WebSocket。"""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data: dict):
        self.sent.append(data)


@pytest.fixture(autouse=True)
def _clean_ws_state():
    """每个测试前后清空连接池/注册表,避免相互污染。"""
    ws_mod._connected_ws.clear()
    ws_mod.pending_session_requests.clear()
    ws_mod._bridge_tasks.clear()
    ws_mod._bridge_configs.clear()
    yield
    ws_mod._connected_ws.clear()
    ws_mod.pending_session_requests.clear()


async def test_notify_session_created_targets_origin_and_broadcasts_notify_only():
    """创建会话:发起连接收完整事件,其他连接收 notify_only 仅刷新列表。"""
    origin = FakeWS()
    other = FakeWS()
    ws_mod._connected_ws.update([origin, other])

    data = {"event": "session_created", "session_id": "s-new"}
    await ws_mod._notify_session_created(origin, data)

    # 发起连接:收到可切换的完整事件(无 notify_only)
    assert {"event": "session_created", "session_id": "s-new"} in origin.sent
    # 其他连接:只收到 notify_only
    assert other.sent == [
        {"event": "session_created", "session_id": "s-new", "notify_only": True}
    ]
    # notify_only 不得出现在"可切换"语义的完整事件中
    assert all("notify_only" not in m for m in origin.sent if not m.get("notify_only"))


async def test_notify_session_switched_targets_ws_send():
    """切换会话:发起连接(config.ws_send)收完整事件,其余收 notify_only。"""
    origin = FakeWS()
    other = FakeWS()
    ws_mod._connected_ws.add(other)

    config = SimpleNamespace(ws_send=origin.send_json)
    await ws_mod.notify_session_switched("s-new", "s-old", config=config)

    assert {"event": "session_switched", "session_id": "s-new", "old_session_id": "s-old"} in origin.sent
    assert other.sent == [
        {
            "event": "session_switched",
            "session_id": "s-new",
            "old_session_id": "s-old",
            "notify_only": True,
        }
    ]


async def test_notify_session_switched_fallback_broadcast_without_ws_send():
    """无发起连接时退回全量广播,单页面场景仍能收到切换通知。"""
    a = FakeWS()
    b = FakeWS()
    ws_mod._connected_ws.update([a, b])

    await ws_mod.notify_session_switched("s-new", "s-old")

    expected = {"event": "session_switched", "session_id": "s-new", "old_session_id": "s-old"}
    assert expected in a.sent
    assert expected in b.sent
    # 兜底广播不应带 notify_only(否则收端只刷新列表,切换丢失)
    assert all("notify_only" not in m for m in a.sent + b.sent)


async def test_notify_session_switched_ignores_send_failure():
    """定向发送失败时仍广播 notify_only,其他页面至少能刷新列表。"""
    other = FakeWS()
    ws_mod._connected_ws.add(other)

    async def _boom(_data):
        raise ConnectionResetError("gone")

    config = SimpleNamespace(ws_send=_boom)
    await ws_mod.notify_session_switched("s-new", "s-old", config=config)

    assert other.sent == [
        {
            "event": "session_switched",
            "session_id": "s-new",
            "old_session_id": "s-old",
            "notify_only": True,
        }
    ]


async def test_api_sessions_merges_unsaved_cache_sessions(tmp_path):
    """/api/sessions 合并 session_cache:未落盘的新会话也能被其他页面列出。"""
    from uniclaw.tools.session.session import Session, SessionType
    from uniclaw.webui import api as api_mod

    session = Session(root_dir=tmp_path, session_type=SessionType.FREE_CHAT)
    fake_config = SimpleNamespace(current_agent=SimpleNamespace(session=session))

    with patch.object(api_mod.SessionManager, "list_sessions", return_value=[]):
        api_mod.session_cache[session.id] = fake_config
        try:
            result = await api_mod.list_sessions()
        finally:
            api_mod.session_cache.pop(session.id, None)

    ids = [s["session_id"] for s in result]
    assert session.id in ids
    matched = next(s for s in result if s["session_id"] == session.id)
    assert matched["session_type"] == SessionType.FREE_CHAT
