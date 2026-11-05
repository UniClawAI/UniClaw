"""WebUI 会话分叉 API 测试(侧栏右键菜单「分叉」)。

行为约定:
- POST /api/sessions/{sid}/fork 从磁盘会话文件分叉,原会话不受影响
- 内存缓存中有未落盘消息时,先保存再分叉(否则分叉结果缺新消息)
- 分叉成功后广播 session_created(notify_only)让所有标签页刷新列表
- 非法分叉点(越界)返回 400 而非 500
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from uniclaw.agent import AgentTask
from uniclaw.config import AppConfig, ProviderProfile
from uniclaw.tools.session.session import Session, SessionType
from uniclaw.tools.session.session_manager import SessionManager
from uniclaw.webui import api as api_mod


def _make_config(task: AgentTask) -> AppConfig:
    """构造真实 AppConfig(分叉路径用到的字段)。"""
    return AppConfig(
        current_agent=task,
        model_name=["test-model"],
        mini_model_name=["test-model"],
        providers={
            "test": ProviderProfile(
                name="test",
                protocol="openai",
                api_key="test",
                base_url="https://api.test.com/v1",
            )
        },
        permission_mode="auto",
        verbose=False,
    )


def _make_task(
    tmp_path: Path, session_type: SessionType = SessionType.CONSOLE
) -> AgentTask:
    session = Session(root_dir=tmp_path, session_type=session_type)
    return AgentTask(name="root", prompt="", session=session)


@pytest.fixture(autouse=True)
def _patch_session_dir(tmp_path, monkeypatch):
    """将 SessionManager 的存储目录重定向到测试临时目录。"""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        SessionManager, "_default_dir", staticmethod(lambda: session_dir)
    )


@pytest.fixture(autouse=True)
def _clean_ws_state():
    """每个测试前后清空 ws 连接池与 session_cache,避免相互污染。"""
    import uniclaw.webui.ws as ws_mod

    ws_mod._connected_ws.clear()
    ws_mod.pending_session_requests.clear()
    ws_mod._bridge_tasks.clear()
    ws_mod._bridge_configs.clear()
    api_mod.session_cache.clear()
    yield
    ws_mod._connected_ws.clear()
    ws_mod.pending_session_requests.clear()
    ws_mod._bridge_tasks.clear()
    ws_mod._bridge_configs.clear()
    api_mod.session_cache.clear()


async def test_fork_api_creates_new_session(tmp_path):
    """fork API: 从第 2 条消息处分叉,新会话包含前 2 条消息,原会话不变。"""
    task = _make_task(tmp_path)
    task.session.add_user_message(content="第一个问题")
    task.session.add_assistant_message(
        content="第一个回答", model_name="test-model", usage={}
    )
    task.session.add_user_message(content="第二个问题")
    config = _make_config(task)
    await SessionManager.save_session(config)
    sid = task.session.id

    result = await api_mod.fork_session(sid, SimpleNamespace(message_idx=1))

    assert result["ok"] is True
    new_sid = result["session_id"]
    assert new_sid != sid
    # 新会话是前 2 条消息,原会话 3 条不变
    forked = SessionManager.load_session(new_sid)
    assert forked is not None
    assert [m.to_content() for m in forked] == ["第一个问题", "第一个回答"]
    original = SessionManager.load_session(sid)
    assert [m.to_content() for m in original] == [
        "第一个问题",
        "第一个回答",
        "第二个问题",
    ]


async def test_fork_api_flushes_unsaved_messages(tmp_path):
    """fork API: 内存缓存中未落盘的新消息先保存再分叉,不丢消息。"""
    task = _make_task(tmp_path)
    task.session.add_user_message(content="问题一")
    task.session.add_assistant_message(
        content="回答一", model_name="test-model", usage={}
    )
    config = _make_config(task)
    await SessionManager.save_session(config)
    sid = task.session.id

    # 缓存中追加未落盘消息(前端正在运行/未保存)
    task.session.add_user_message(content="新问题未落盘")
    api_mod.session_cache[sid] = _make_config(task)

    result = await api_mod.fork_session(sid, SimpleNamespace(message_idx=2))

    new_sid = result["session_id"]
    forked = SessionManager.load_session(new_sid)
    assert forked is not None
    assert [m.to_content() for m in forked] == ["问题一", "回答一", "新问题未落盘"]


async def test_fork_api_broadcasts_notify_only(tmp_path):
    """fork 广播 session_created(notify_only),所有标签页仅刷新列表。"""
    task = _make_task(tmp_path)
    task.session.add_user_message(content="唯一消息")
    config = _make_config(task)
    await SessionManager.save_session(config)
    sid = task.session.id

    class FakeWS:
        def __init__(self):
            self.sent: list[dict] = []

        async def send_json(self, data: dict):
            self.sent.append(data)

    import uniclaw.webui.ws as ws_mod

    a = FakeWS()
    b = FakeWS()
    ws_mod._connected_ws.update([a, b])

    await api_mod.fork_session(sid, SimpleNamespace(message_idx=0))

    for ws in (a, b):
        created = [m for m in ws.sent if m.get("event") == "session_created"]
        assert created, "每个标签页都应收到 session_created 刷新事件"
        for m in created:
            assert m.get("notify_only") is True, "分叉事件不得劫持其他页面当前会话"
            assert m.get("session_id")


async def test_fork_api_out_of_range_returns_400(tmp_path):
    """消息序越界返回 400,而非吞成 500。"""
    task = _make_task(tmp_path)
    task.session.add_user_message(content="唯一消息")
    config = _make_config(task)
    await SessionManager.save_session(config)
    sid = task.session.id

    with pytest.raises(HTTPException) as exc:
        await api_mod.fork_session(sid, SimpleNamespace(message_idx=99))
    assert exc.value.status_code == 400


async def test_fork_api_missing_session_returns_404(tmp_path):
    """会话不存在返回 404。"""
    with pytest.raises(HTTPException) as exc:
        await api_mod.fork_session("no-such-session", SimpleNamespace(message_idx=0))
    assert exc.value.status_code == 404