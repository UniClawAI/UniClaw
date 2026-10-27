"""WebUI 双路径一致性测试 — replay 与 live 渲染结果必须一致。

CLAUDE.md 核心约定:WebUI 展示逻辑有两条路径 ——
- live:WebSocket 实时事件流向(_onXxx handlers)
- replay:历史消息回放(_replayMessages)

"当添加或修改 WebUI 工具块(或任何聊天元素)的展示数据时,两条路径必须同时更新。
如果不保持一致,用户刷新页面后会看到不同的内容。"

本文件用**同一组消息**,分别驱动 replay 路径(预置会话 + 刷新)和 live 路径
(WS 广播),对比两条路径渲染出的聊天区文本,确保一致。

注意:live 路径不会有历史时间戳、msg_idx 等 replay 特有属性,比较时只对比
双方都会渲染的核心内容(每类消息的文本)。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from uniclaw.context import get_app_dir, Scope
from uniclaw.tools.session.session import Session, SessionType, ToolCallMessage

pytestmark = [pytest.mark.e2e]

# 一组"标准消息"用于两条路径对比
USER_TEXT = "帮我查一下今天的天气"
ASSISTANT_TEXT = "好的,今天天气晴朗,气温 25°C。"
SYSTEM_TEXT = "[system] 已切换到项目 /home/user/proj"
TOOL_NAME = "Bash"
TOOL_ARGS = {"command": "ls -la"}
TOOL_RESULT = "README.md  pyproject.toml  src/"
TOOL_CALL_ID = "call_consistency_tool_1"

SESSION_ID = "e2e-consistency-session"


def _assistant_tool_calls() -> list[dict]:
    return [
        {
            "id": TOOL_CALL_ID,
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "arguments": json.dumps(TOOL_ARGS, ensure_ascii=False),
            },
        }
    ]


# ── replay 路径构造 ──────────────────────────────────────────────────────────


def _build_replay_session(e2e_home) -> Session:
    """构造带标准消息的会话(供 replay 路径使用)。"""
    s = Session(
        session_type=SessionType.FREE_CHAT,
        start_time=datetime(2026, 10, 5, 9, 0),
    )
    s.title = "一致性测试会话"
    s.add_user_message(USER_TEXT)
    s.add_assistant_message(
        ASSISTANT_TEXT,
        model_name="e2e/model",
        usage={"input_tokens": 10, "output_tokens": 20},
        finish_reason="stop",
    )
    s.add_user_message(SYSTEM_TEXT)
    s.add_assistant_message(
        "我来执行命令。",
        model_name="e2e/model",
        usage={"input_tokens": 5, "output_tokens": 8},
        tool_calls=_assistant_tool_calls(),
    )
    # 工具结果
    tool_msg = ToolCallMessage(
        name=TOOL_NAME,
        tool_call_id=TOOL_CALL_ID,
        content=TOOL_RESULT,
        args=TOOL_ARGS,
    )
    s._messages.append(tool_msg)
    s.history.append(tool_msg)
    return s


def _write_replay_session(e2e_home, s: Session):
    """把会话写到隔离目录,供前端 replay。"""
    sessions_dir = get_app_dir(Scope.USER) / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": s.id,
        "title": s.title,
        "root_dir": str(s.root_dir) if s.root_dir else None,
        "session_type": s.session_type,
        "system_prompt": None,
        "start_time": s.start_time.isoformat(),
        "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "duration_seconds": 0,
        "message_count": len(s._messages),
        "version": 2,
        "compact_end": 0,
        "compacted": [],
        "history": s.to_history_messages(),
        "messages": s.to_messages(),
        "session_notes": [],
    }
    (sessions_dir / f"{s.id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    meta_file = sessions_dir / "metadata.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    meta[s.id] = {
        "session_id": s.id,
        "title": s.title,
        "start_time": data["start_time"],
        "end_time": data["end_time"],
        "message_count": len(s._messages),
        "root_dir": data["root_dir"],
        "session_type": s.session_type,
        "file_path": f"{s.id}.json",
        "archived": False,
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _open_replay_page(open_page, session_id: str):
    """打开页面,点击预置会话,等待消息渲染完成。"""
    page, cm = open_page()
    try:
        page.wait_for_selector(f'.session-item[data-sid="{session_id}"]', timeout=8000)
        page.click(f'.session-item[data-sid="{session_id}"]', timeout=5000, force=True)
        page.wait_for_selector("#chat-messages .message", timeout=8000)
        # 等工具块渲染也完成
        page.wait_for_function(
            "() => document.querySelectorAll('#chat-messages .tool-block').length > 0",
            timeout=8000,
        )
        page.wait_for_timeout(300)
        return page
    except Exception:
        cm.__exit__(*__import__("sys").exc_info())
        raise


def _chat_text(page) -> str:
    """提取聊天区可见文本(双路径统一口径)。"""
    return page.locator("#chat-messages").inner_text()


def _has_user_message(text: str) -> bool:
    return USER_TEXT in text


def _has_assistant_text(text: str) -> bool:
    return ASSISTANT_TEXT in text


def _has_tool_block(page) -> bool:
    return page.locator("#chat-messages .tool-block").count() >= 1


def _has_system_message(page) -> bool:
    return page.locator("#chat-messages .system-message").count() >= 1


# ── live 路径构造 ────────────────────────────────────────────────────────────


def _make_live_session(open_page, server):
    """打开页面并广播 session_created,建立当前会话(live 路径前置)。"""
    page, cm = open_page()
    page.wait_for_function(
        "() => document.getElementById('connection-dot')?.classList.contains('connected')",
        timeout=8000,
    )
    server.broadcast({"event": "session_created", "session_id": SESSION_ID})
    page.wait_for_timeout(300)
    return page, cm


def _drive_live_path(page, server):
    """通过 WS 广播驱动与 replay 相同的一组消息。"""
    server.broadcast(
        {"event": "user", "session_id": SESSION_ID, "content": USER_TEXT}
    )
    page.wait_for_timeout(200)
    server.broadcast(
        {
            "event": "assistant",
            "session_id": SESSION_ID,
            "content": ASSISTANT_TEXT,
            "model_name": "e2e/model",
            "in_tokens": 10,
            "out_tokens": 20,
        }
    )
    page.wait_for_timeout(200)
    server.broadcast(
        {"event": "user", "session_id": SESSION_ID, "content": SYSTEM_TEXT}
    )
    page.wait_for_timeout(200)
    server.broadcast(
        {
            "event": "assistant",
            "session_id": SESSION_ID,
            "content": "我来执行命令。",
            "tool_calls": _assistant_tool_calls(),
        }
    )
    page.wait_for_timeout(200)
    server.broadcast(
        {
            "event": "tool_end",
            "session_id": SESSION_ID,
            "tool_call_id": TOOL_CALL_ID,
            "name": TOOL_NAME,
            "content": TOOL_RESULT,
            "success": True,
        }
    )
    page.wait_for_selector("#chat-messages .message", timeout=5000)
    page.wait_for_function(
        "() => document.querySelectorAll('#chat-messages .tool-block').length > 0",
        timeout=5000,
    )
    page.wait_for_timeout(300)


# ── 一致性对比测试 ───────────────────────────────────────────────────────────


def test_replay_path_renders_all_message_types(open_page, e2e_home):
    """replay 路径:同一组消息完整渲染(用户/助手/系统/工具块)。"""
    s = _build_replay_session(e2e_home)
    _write_replay_session(e2e_home, s)
    page = _open_replay_page(open_page, s.id)
    text = _chat_text(page)

    assert _has_user_message(text), "replay:用户消息缺失"
    assert _has_assistant_text(text), "replay:助手回复缺失"
    assert _has_system_message(page), "replay:系统消息缺失"
    assert _has_tool_block(page), "replay:工具块缺失"
    # 工具结果文本应出现
    assert TOOL_RESULT.split()[0] in text, "replay:工具结果缺失"


def test_live_path_renders_all_message_types(open_page, webui_server):
    """live 路径:同一组消息完整渲染。"""
    page, cm = _make_live_session(open_page, webui_server)
    try:
        _drive_live_path(page, webui_server)
        text = _chat_text(page)

        assert _has_user_message(text), "live:用户消息缺失"
        assert _has_assistant_text(text), "live:助手回复缺失"
        assert _has_system_message(page), "live:系统消息缺失"
        assert _has_tool_block(page), "live:工具块缺失"
        assert TOOL_RESULT.split()[0] in text, "live:工具结果缺失"
    finally:
        cm.__exit__(*(None,) * 3)


def test_replay_and_live_render_consistent_text(open_page, e2e_home, webui_server):
    """双路径一致性:同一组消息,replay 与 live 渲染出相同内容。"""
    # ── replay 侧 ──
    s = _build_replay_session(e2e_home)
    _write_replay_session(e2e_home, s)
    replay_page = _open_replay_page(open_page, s.id)
    replay_text = _chat_text(replay_page)
    replay_tool_block = replay_page.locator("#chat-messages .tool-block").count()
    replay_system = replay_page.locator("#chat-messages .system-message").count()

    # ── live 侧(同一组消息)──
    live_page, live_cm = _make_live_session(open_page, webui_server)
    try:
        _drive_live_path(live_page, webui_server)
        live_text = _chat_text(live_page)
        live_tool_block = live_page.locator("#chat-messages .tool-block").count()
        live_system = live_page.locator("#chat-messages .system-message").count()
    finally:
        live_cm.__exit__(*(None,) * 3)

    # 用户消息文本一致
    assert USER_TEXT in replay_text and USER_TEXT in live_text
    # 助手回复文本一致
    assert ASSISTANT_TEXT in replay_text and ASSISTANT_TEXT in live_text
    # 系统消息都渲染了注入的 [system] 文本。
    # 注意:live 路径下伪造的 session_created 会让前端向真实后端发 set_active,
    # 后端可能广播 error(会话不存在)而多出一条 ❌ 系统消息 —— 这是测试基建的
    # 噪音,不是双路径的真实差异,故不精确比对 system 数量。
    assert SYSTEM_TEXT in replay_text and SYSTEM_TEXT in live_text
    assert "已切换到项目" in replay_text and "已切换到项目" in live_text
    # 工具块数量一致(真实差异检测点:两条路径必须都渲染工具块)
    assert replay_tool_block == live_tool_block >= 1
    # 工具结果出现
    assert TOOL_RESULT.split()[0] in replay_text
    assert TOOL_RESULT.split()[0] in live_text