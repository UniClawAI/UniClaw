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


def test_live_async_subagent_view_switch(open_page, webui_server):
    """异步 subagent(wait=False): 事件渲染到独立视图, 状态栏出现 👥 切换项, 菜单可切换查看。"""
    page, cm = _make_session(open_page, webui_server)
    try:
        creator_id = "call_sub_create_1"

        def _wait(ms):
            page.wait_for_timeout(ms)

        # 无子代理时: 状态栏切换项隐藏
        assert page.locator("#status-agents").is_hidden() or page.locator("#status-agents").evaluate("(el) => getComputedStyle(el).display === 'none'"), "无子代理时状态栏不应显示"

        # 1) 主 agent 的 subagent_create 工具启动(wait=false → 异步) → 建立独立视图 + 状态栏显示
        webui_server.broadcast(
            {
                "event": "tool_start",
                "session_id": SESSION_ID,
                "name": "subagent_create",
                "args": {"name": "调研助手", "prompt": "调研", "subagent_type": "general-purpose", "wait": False},
                "tool_call_id": creator_id,
                "is_subagent": False,
            }
        )
        _wait(200)
        # 状态栏出现 👥 切换项
        page.wait_for_function(
            "() => getComputedStyle(document.getElementById('status-agents')).display !== 'none'",
            timeout=5000,
        )

        # 2) 推送 subagent 的 user 事件: [system] 时间感知 + 任务提示(应进独立视图, 不进主视图)
        webui_server.broadcast(
            {
                "event": "user",
                "session_id": SESSION_ID,
                "content": "[system] 当前时间为 14:32,距上次对话较久",
                "is_subagent": True,
                "is_async": True,
                "agent_name": "调研助手",
                "creator_tool_call_id": creator_id,
            }
        )
        _wait(150)
        webui_server.broadcast(
            {
                "event": "user",
                "session_id": SESSION_ID,
                "content": "帮我调研一下 AI 市场现状",
                "is_subagent": True,
                "is_async": True,
                "agent_name": "调研助手",
                "creator_tool_call_id": creator_id,
            }
        )
        _wait(150)
        # 主视图不应出现 subagent 的 [system]/任务提示
        main_before = page.locator("#chat-messages").inner_text()
        assert "当前时间为 14:32" not in main_before, "subagent [system] 消息不应进入主视图"
        assert "帮我调研一下 AI 市场现状" not in main_before, "subagent 任务提示不应进入主视图"

        # 3) 推送 subagent 的 thinking / 流式文本 + 工具
        webui_server.broadcast(
            {
                "event": "thinking_start",
                "session_id": SESSION_ID,
                "is_subagent": True,
                "is_async": True,
                "agent_name": "调研助手",
                "creator_tool_call_id": creator_id,
            }
        )
        webui_server.broadcast(
            {
                "event": "thinking",
                "session_id": SESSION_ID,
                "content": "我需要先搜索市场概况",
                "is_subagent": True,
                "is_async": True,
                "agent_name": "调研助手",
                "creator_tool_call_id": creator_id,
            }
        )
        _wait(200)
        webui_server.broadcast(
            {
                "event": "text",
                "session_id": SESSION_ID,
                "content": "开始调研市场:",
                "is_subagent": True,
                "is_async": True,
                "agent_name": "调研助手",
                "creator_tool_call_id": creator_id,
            }
        )
        _wait(200)
        webui_server.broadcast(
            {
                "event": "tool_start",
                "session_id": SESSION_ID,
                "name": "webSearch",
                "args": {"query": "AI 市场"},
                "tool_call_id": "call_sub_tool_1",
                "is_subagent": True,
                "is_async": True,
                "agent_name": "调研助手",
                "creator_tool_call_id": creator_id,
            }
        )
        _wait(200)

        # 4) 点击状态栏切换项 → 弹出菜单 → 点击"调研助手"切换到子代理视图
        page.click("#status-agents", timeout=5000)
        page.wait_for_selector("#agent-switch-menu .context-menu-item", timeout=5000)
        menu_texts = page.evaluate("() => Array.from(document.querySelectorAll('#agent-switch-menu .context-menu-item')).map(el => el.textContent)")
        assert len(menu_texts) >= 2, f"菜单应含主 Agent 与子代理: {menu_texts}"
        assert any("调研助手" in t for t in menu_texts), f"菜单应含子代理名: {menu_texts}"
        page.click("#agent-switch-menu .context-menu-item:nth-child(2)", timeout=5000)
        page.wait_for_selector(".agent-chat", timeout=5000)
        # 主聊天区隐藏,子代理视图显示
        assert page.locator("#chat-messages").evaluate("(el) => getComputedStyle(el).display === 'none'"), "主视图应隐藏"
        sub_text = page.locator(".agent-chat").inner_text()
        assert "开始调研市场:" in sub_text, "subagent 流式文本未渲染到独立视图"
        assert "webSearch" in sub_text, "subagent 工具块未渲染到独立视图"
        assert "思考" in sub_text, "subagent thinking 未渲染"
        assert "当前时间为 14:32" in sub_text, "subagent [system] 消息未渲染到独立视图"
        assert "帮我调研一下 AI 市场现状" in sub_text, "subagent 任务提示未渲染到独立视图"

        # 5) subagent 中断事件 → 独立视图显示中断提示, 主视图不受污染
        webui_server.broadcast(
            {
                "event": "interrupted",
                "session_id": SESSION_ID,
                "message": "用户取消了子代理",
                "is_subagent": True,
                "is_async": True,
                "agent_name": "调研助手",
                "creator_tool_call_id": creator_id,
            }
        )
        _wait(200)
        assert "用户取消了子代理" in page.locator(".agent-chat").inner_text(), "subagent 中断提示未渲染到独立视图"
        assert "用户取消了子代理" not in page.locator("#chat-messages").inner_text(), "subagent 中断提示不应进入主视图"

        # 5b) 中断后状态栏菜单: 子代理应标注"完成(取消)"(非"运行中")
        page.click("#status-agents", timeout=5000)
        page.wait_for_selector("#agent-switch-menu .context-menu-item", timeout=5000)
        badged = page.evaluate(
            "() => Array.from(document.querySelectorAll('#agent-switch-menu .context-menu-item')).map(el => el.textContent)"
        )
        assert any("调研助手" in t and "取消" in t for t in badged), f"菜单应标注取消: {badged}"
        assert not any("调研助手" in t and "运行中" in t for t in badged), f"取消后不应再标运行中: {badged}"
        # 关闭菜单(点击菜单外任意处触发 once listener)
        page.mouse.click(5, 5)
        _wait(100)
        assert page.locator("#agent-switch-menu").count() == 0, "菜单应已关闭"

        # 6) 切回主视图:再次打开菜单,点击"主 Agent"
        page.click("#status-agents", timeout=5000)
        page.wait_for_selector("#agent-switch-menu .context-menu-item", timeout=5000)
        page.click("#agent-switch-menu .context-menu-item:nth-child(1)", timeout=5000)
        _wait(200)
        assert page.locator("#chat-messages").is_visible(), "主视图应恢复显示"
        main_text = page.locator("#chat-messages").inner_text()
        assert "subagent_create" in main_text, "主视图应保留 subagent_create 工具块"
    finally:
        cm.__exit__(*((None,) * 3))


def test_live_sync_subagent_nested_display(open_page, webui_server):
    """同步 subagent(wait=true): 保持老办法 —— 事件嵌套在创建它的工具块内, 不建独立视图。"""
    page, cm = _make_session(open_page, webui_server)
    try:
        creator_id = "call_sync_sub_1"

        def _wait(ms):
            page.wait_for_timeout(ms)

        # 主 agent 创建同步子代理(wait=true): 不产生独立视图/状态栏切换项
        webui_server.broadcast(
            {
                "event": "tool_start",
                "session_id": SESSION_ID,
                "name": "subagent_create",
                "args": {"name": "同步小助", "prompt": "帮我算", "subagent_type": "general-purpose", "wait": True},
                "tool_call_id": creator_id,
                "is_subagent": False,
            }
        )
        _wait(200)
        # 状态栏切换项不应出现(同步不建独立视图)
        assert page.locator("#status-agents").is_hidden() or page.locator("#status-agents").evaluate("(el) => getComputedStyle(el).display === 'none'"), "同步子代理不应显示状态栏切换项"
        assert page.locator(".agent-chat").count() == 0, "同步子代理不应创建独立视图容器"

        # 同步子代理的 thinking/text 事件(不带 is_async → 同步路径)
        webui_server.broadcast(
            {
                "event": "thinking_start",
                "session_id": SESSION_ID,
                "is_subagent": True,
                "is_async": False,
                "agent_name": "同步小助",
                "creator_tool_call_id": creator_id,
            }
        )
        webui_server.broadcast(
            {
                "event": "thinking",
                "session_id": SESSION_ID,
                "content": "计算中",
                "is_subagent": True,
                "is_async": False,
                "agent_name": "同步小助",
                "creator_tool_call_id": creator_id,
            }
        )
        webui_server.broadcast(
            {
                "event": "text",
                "session_id": SESSION_ID,
                "content": "我来算一下:",
                "is_subagent": True,
                "is_async": False,
                "agent_name": "同步小助",
                "creator_tool_call_id": creator_id,
            }
        )
        _wait(200)
        webui_server.broadcast(
            {
                "event": "tool_start",
                "session_id": SESSION_ID,
                "name": "Bash",
                "args": {"command": "echo 3"},
                "tool_call_id": "call_sync_tool_1",
                "is_subagent": True,
                "is_async": False,
                "agent_name": "同步小助",
                "creator_tool_call_id": creator_id,
            }
        )
        webui_server.broadcast(
            {
                "event": "tool_end",
                "session_id": SESSION_ID,
                "name": "Bash",
                "content": "3",
                "tool_call_id": "call_sync_tool_1",
                "args": {"command": "echo 3"},
                "is_subagent": True,
                "is_async": False,
                "agent_name": "同步小助",
                "creator_tool_call_id": creator_id,
            }
        )
        _wait(200)

        # 同步子代理内容应嵌套在主视图的 subagent_create 工具块内
        main_text = page.locator("#chat-messages").inner_text()
        assert "同步小助" in main_text, "同步子代理名字应出现在主视图"
        assert "我来算一下" in main_text, "同步子代理流式文本应嵌套在主视图工具块内"
        assert "echo 3" in main_text, "同步子代理工具应嵌套在主视图工具块内"
        # 不应有独立视图
        assert page.locator(".agent-chat").count() == 0, "同步子代理不应创建独立视图"
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