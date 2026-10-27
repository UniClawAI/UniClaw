"""WebUI 前端回放路径测试 — 历史会话刷新后的渲染正确性。

背景:CLAUDE.md 强调 WebUI 展示逻辑有两条路径必须一致:
- live(WebSocket 实时事件, `_onXxx` handlers)
- replay(历史消息回放, `_replayMessages`)

本文件验证 replay 路径:预置带历史消息的会话文件到隔离用户目录,
打开/刷新页面后断言历史消息正确渲染(用户气泡 / 助手回复 / 工具块)。

预置方式:直接在隔离的 ~/.UniClaw/sessions/ 下写会话 JSON + metadata。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

import pytest

from uniclaw.context import get_app_dir, Scope
from uniclaw.tools.session.session import Session, SessionType

pytestmark = [pytest.mark.e2e]


def _make_session_with_history(e2e_home, root_dir: str | None = None) -> Session:
    """构造带历史消息的会话(与生产落盘格式一致)。

    用 FREE_CHAT 类型(WebUI 自由聊天):前端把它归入侧栏"会话"分组,
    无论 root_dir 是否为空都能显示。
    """
    s = Session(
        root_dir=root_dir,
        session_type=SessionType.FREE_CHAT,
        start_time=datetime(2026, 10, 1, 10, 0, 0),
    )
    s.title = "测试历史会话"
    s.add_user_message("帮我写一封请假邮件")
    s.add_assistant_message(
        "好的,这是请假邮件的草稿:\n\n> 尊敬的经理,\n> 因家中有事,我申请请 2 天假。\n\n需要调整语气吗?",
        model_name="test/model",
        usage={"input_tokens": 12, "output_tokens": 40},
        finish_reason="stop",
    )
    return s


def _write_session(e2e_home, s: Session) -> dict:
    """将会话写入隔离用户目录的 sessions/ 目录(含 metadata)。"""
    sessions_dir = get_app_dir(Scope.USER) / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "session_id": s.id,
        "title": s.title,
        "root_dir": str(s.root_dir) if s.root_dir else None,
        "session_type": s.session_type,
        "system_prompt": s.system_prompt,
        "start_time": s.start_time.isoformat(),
        "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "duration_seconds": 0,
        "message_count": len(s._messages),
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "api_calls": 0,
        "version": 2,
        "compact_end": 0,
        "compacted": [],
        "history": s.to_history_messages(),
        "messages": s.to_messages(),
        "session_notes": [],
    }

    file_path = sessions_dir / f"{s.id}.json"
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写 metadata(与 _upsert_metadata 格式一致)
    meta_file = sessions_dir / "metadata.json"
    meta = {}
    if meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    rel = file_path.name
    meta[s.id] = {
        "session_id": s.id,
        "title": s.title,
        "start_time": data.get("start_time"),
        "end_time": data.get("end_time"),
        "message_count": len(s._messages),
        "root_dir": str(s.root_dir) if s.root_dir else None,
        "session_type": s.session_type,
        "file_path": rel,
        "archived": False,
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@pytest.fixture
def replayed_session(webui_server, e2e_home):
    """预置一个带历史消息的会话。"""
    s = _make_session_with_history(e2e_home)
    data = _write_session(e2e_home, s)
    return s, data


def _open_replayed_page(open_page, session_id: str):
    """打开主页并等待历史会话在侧栏出现、点击进入。"""
    page, cm = open_page()
    try:
        # 等待会话树渲染出预置会话
        page.wait_for_selector(f'.session-item[data-sid="{session_id}"]', timeout=8000)
        # 点击会话(force:工具提示浮层动画可能让元素短暂不在稳定态)
        page.click(f'.session-item[data-sid="{session_id}"]', timeout=5000, force=True)
        # 等待历史消息渲染(用户消息出现)
        page.wait_for_function(
            """() => {
                const msgs = document.querySelectorAll('#chat-messages .message');
                return msgs.length > 0;
            }""",
            timeout=8000,
        )
        return page
    except Exception:
        cm.__exit__(*sys.exc_info())
        raise


def test_replay_user_and_assistant_messages(open_page, replayed_session):
    """刷新后历史会话应渲染用户消息 + 助手回复。"""
    s, data = replayed_session
    page = _open_replayed_page(open_page, s.id)

    text = page.locator("#chat-messages").inner_text()
    assert "帮我写一封请假邮件" in text, "用户消息未渲染"
    assert "请假邮件的草稿" in text, "助手回复未渲染"
    assert "尊敬的经理" in text, "Markdown 引用内容未渲染"
    # 离线测试拦截了 CDN,marked.js 不可用 → renderMarkdown 降级为纯文本。
    # 若 external CDN 可用(marked 加载成功),则应渲染成 blockquote。
    if page.evaluate("() => typeof marked !== 'undefined'"):
        assert page.locator("#chat-messages blockquote").count() >= 1, "Markdown 引用未渲染"


def test_replay_system_messages(open_page, e2e_home):
    """replay 应渲染系统消息(如[system]注入消息)。"""
    from uniclaw.tools.session.session import Session, SessionType

    s = Session(session_type=SessionType.FREE_CHAT, start_time=datetime(2026, 10, 2, 9, 0))
    s.title = "含系统消息会话"
    s.add_user_message("[system] 距离上次发言已超过 1 小时,当前时刻为 2026-10-02 10:30")
    s.add_assistant_message(
        "好的,继续之前的讨论。",
        model_name="test/model",
        usage={"input_tokens": 5, "output_tokens": 8},
    )
    _write_session(e2e_home, s)
    page = _open_replayed_page(open_page, s.id)

    text = page.locator("#chat-messages").inner_text()
    assert "距离上次发言已超过 1 小时" in text


def test_replay_tool_blocks(open_page, e2e_home):
    """replay 应渲染 assistant 消息附带的工具调用块(含结果)。"""
    from uniclaw.tools.session.session import Session, SessionType

    s = Session(session_type=SessionType.FREE_CHAT, start_time=datetime(2026, 10, 3, 8, 0))
    s.title = "含工具调用会话"
    s.add_user_message("查一下当前目录文件")
    s.add_assistant_message(
        "我来查找。",
        model_name="test/model",
        usage={"input_tokens": 10, "output_tokens": 5},
        tool_calls=[
            {
                "id": "call_abc123",
                "type": "function",
                "function": {"name": "Bash", "arguments": "{\"command\": \"ls\"}"},
            }
        ],
    )
    # 工具结果消息(role=tool)
    from uniclaw.tools.session.session import ToolCallMessage

    # 直接塞一个工具结果到 _messages 与 history,模拟落盘格式
    s._messages.append(
        ToolCallMessage(
            name="Bash",
            tool_call_id="call_abc123",
            content="README.md  pyproject.toml  src/",
            args={"command": "ls"},
        )
    )
    s.history.append(s._messages[-1])
    _write_session(e2e_home, s)
    page = _open_replayed_page(open_page, s.id)

    text = page.locator("#chat-messages").inner_text()
    assert "README.md" in text, "工具结果未渲染"
    # 工具块应有 Bash 名称
    tool_names = page.locator("#chat-messages .tool-name, #chat-messages .tool-header").all_inner_texts()
    assert any("Bash" in n for n in tool_names), f"工具块名称缺失: {tool_names}"


def test_replay_console_session_without_root_dir_visible(open_page, e2e_home):
    """回归:无 root_dir 的 CONSOLE 会话必须在侧栏可见(修复前被静默丢弃)。

    后端 /api/sessions 会返回这类会话(存在于磁盘,如 /resume 恢复的历史会话),
    前端此前在 _refreshSessions 里 `if (!d) return` 直接跳过,侧栏永远看不到。
    """
    from uniclaw.tools.session.session import Session, SessionType

    s = Session(session_type=SessionType.CONSOLE, start_time=datetime(2026, 10, 4, 14, 0))
    assert s.root_dir is None, "测试前提:CONSOLE 会话无 root_dir"
    s.title = "控制台历史会话"
    s.add_user_message("帮助我")
    s.add_assistant_message(
        "我可以帮你。",
        model_name="test/model",
        usage={"input_tokens": 3, "output_tokens": 4},
    )
    _write_session(e2e_home, s)

    page, cm = open_page()
    try:
        # 侧栏必须出现该会话(修复前此选择器超时)
        page.wait_for_selector(f'.session-item[data-sid="{s.id}"]', timeout=5000)
        # 点击应能加载历史
        page.click(f'.session-item[data-sid="{s.id}"]', timeout=5000, force=True)
        page.wait_for_function(
            "() => document.querySelectorAll('#chat-messages .message').length > 0",
            timeout=5000,
        )
        text = page.locator("#chat-messages").inner_text()
        assert "帮助我" in text, "无目录会话的历史消息未加载"
        assert "我可以帮你" in text, "无目录会话的助手回复未加载"
    finally:
        cm.__exit__(None, None, None)