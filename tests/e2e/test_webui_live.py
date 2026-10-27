"""WebUI 前端 live 路径测试 — WebSocket 实时事件的渲染正确性。

背景:与 replay 路径(历史消息回放)相对,live 路径由 WebSocket 实时
事件驱动(`_onXxx` handlers)。CLAUDE.md 强调两条路径必须同步更新。

本文件通过服务端事件循环注入 _broadcast 事件,模拟真实的 agent 事件流,
验证前端实时渲染:用户消息 / 流式文本 / 完整回复 / 工具调用块。

会话建立:先广播 session_created 让前端建立当前会话(否则事件被 session_id
过滤器丢弃),后续事件携带同一 session_id。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]

SESSION_ID = "e2e-live-session-001"


def _make_session(open_page, server):
    """打开页面并广播 session_created,建立当前会话。

    - 先等前端 WebSocket 已连接(connection-dot 有 connected class),
      否则 broadcast 的消息到达时无人接收
    - session_created 事件处理后 Chat.currentSessionId 被设为 SESSION_ID,
      后续携带同 id 的事件才会通过过滤器渲染
    """
    page, cm = open_page()
    page.wait_for_function(
        "() => document.getElementById('connection-dot')?.classList.contains('connected')",
        timeout=8000,
    )
    server.broadcast({"event": "session_created", "session_id": SESSION_ID})
    # 给前端一小段时间处理 session_created 并建立 currentSessionId
    page.wait_for_timeout(300)
    return page, cm


def test_live_user_message_renders(open_page, webui_server):
    """live 用户消息事件应渲染为用户气泡。"""
    page, cm = _make_session(open_page, webui_server)
    try:
        webui_server.broadcast(
            {
                "event": "user",
                "session_id": SESSION_ID,
                "content": "你好,帮我看看这个",
                "msg_idx": 0,
            }
        )
        page.wait_for_selector("#chat-messages .message.user", timeout=5000)
        text = page.locator("#chat-messages").inner_text()
        assert "你好,帮我看看这个" in text, "live 用户消息未渲染"
    finally:
        cm.__exit__(*((None,) * 3))


def test_live_streaming_text(open_page, webui_server):
    """live 流式 text 事件应逐步累加到助手气泡。"""
    page, cm = _make_session(open_page, webui_server)
    try:
        webui_server.broadcast(
            {"event": "text", "session_id": SESSION_ID, "content": "好的,"}
        )
        page.wait_for_function(
            """() => {
                const el = document.querySelector('#chat-messages .message.assistant');
                return el && el.innerText.includes('好的,');
            }""",
            timeout=5000,
        )
        webui_server.broadcast(
            {"event": "text", "session_id": SESSION_ID, "content": "我来看看"}
        )
        page.wait_for_function(
            """() => {
                const refs = document.querySelectorAll('#chat-messages .markdown-body');
                const found = Array.from(refs).some(el => el.innerText.includes('好的,我来看看'));
                return found;
            }""",
            timeout=5000,
        )
    finally:
        cm.__exit__(*((None,) * 3))


def test_live_assistant_final_message(open_page, webui_server):
    """live assistant 最终事件应替换流式内容为完整回复。"""
    page, cm = _make_session(open_page, webui_server)
    try:
        webui_server.broadcast(
            {
                "event": "assistant",
                "session_id": SESSION_ID,
                "content": "这是完整回复内容。",
                "model_name": "e2e/test-model",
                "in_tokens": 10,
                "out_tokens": 20,
            }
        )
        page.wait_for_function(
            """() => {
                const refs = document.querySelectorAll('#chat-messages .markdown-body');
                return Array.from(refs).some(el => el.innerText.includes('这是完整回复内容。'));
            }""",
            timeout=5000,
        )
    finally:
        cm.__exit__(*((None,) * 3))


def test_live_tool_start_and_end(open_page, webui_server):
    """live 工具调用事件应渲染工具块(名称 + 结果)。"""
    page, cm = _make_session(open_page, webui_server)
    try:
        tool_call_id = "call_live_tool_1"
        # 先来一条 assistant 携带 tool_calls
        webui_server.broadcast(
            {
                "event": "assistant",
                "session_id": SESSION_ID,
                "content": "我来执行工具。",
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": "Bash",
                            "arguments": '{"command": "echo hi"}',
                        },
                    }
                ],
            }
        )
        page.wait_for_function(
            """() => {
                const blocks = document.querySelectorAll('#chat-messages .tool-block');
                return blocks.length > 0;
            }""",
            timeout=5000,
        )
        # 工具结束事件(携带结果)
        webui_server.broadcast(
            {
                "event": "tool_end",
                "session_id": SESSION_ID,
                "tool_call_id": tool_call_id,
                "name": "Bash",
                "content": "hi",
                "success": True,
            }
        )
        page.wait_for_function(
            """() => {
                const blocks = document.querySelectorAll('#chat-messages .tool-block');
                return Array.from(blocks).some(b => b.innerText.includes('hi'));
            }""",
            timeout=5000,
        )
    finally:
        cm.__exit__(*((None,) * 3))


def test_live_system_message(open_page, webui_server):
    """live [system] 前缀用户消息应渲染为系统消息样式。"""
    page, cm = _make_session(open_page, webui_server)
    try:
        webui_server.broadcast(
            {
                "event": "user",
                "session_id": SESSION_ID,
                "content": "[system] 当前时间为 12:00,今日未进行会话",
            }
        )
        page.wait_for_selector(
            "#chat-messages .system-message", timeout=5000
        )
        text = page.locator("#chat-messages").inner_text()
        assert "当前时间为 12:00" in text, "系统消息未渲染"
    finally:
        cm.__exit__(*((None,) * 3))


def test_live_error_system_message(open_page, webui_server):
    """live error 事件应渲染为系统消息。"""
    page, cm = _make_session(open_page, webui_server)
    try:
        webui_server.broadcast(
            {
                "event": "error",
                "session_id": SESSION_ID,
                "message": "模型请求失败:rate limit exceeded",
            }
        )
        page.wait_for_selector(
            "#chat-messages .system-message", timeout=5000
        )
        text = page.locator("#chat-messages").inner_text()
        assert "模型请求失败" in text, "错误系统消息未渲染"
    finally:
        cm.__exit__(*((None,) * 3))