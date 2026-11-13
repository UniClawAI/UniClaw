"""WebUI bridge_events 的 spinner 前缀测试 — subagent 的 spinner 行必须带 agent 名字。

背景: console 模式(console/run.py)用 `agent_prefix = f"[{queued_task.name}] "`
拼进 spinner 文本; WebUI 的 bridge_events 之前漏了这一步, 导致前端 spinner-area
里 subagent 的行只有 "Thinking..."/工具名, 看不到是哪个子代理在跑。
本测试锁定该行为: subagent 的事件触发的 spinner.start 文本必须以 `[名字] ` 开头,
主 agent 的事件不带前缀。
"""

import asyncio
from types import SimpleNamespace

import pytest

from uniclaw.agent import EndEvent, ThinkingStartEvent
from uniclaw.webui import ws as ws_mod


@pytest.fixture(autouse=True)
def _clean_ws_state():
    """每个测试前后清空连接池,避免 bridge_events 的 _broadcast 向假连接发送。"""
    ws_mod._connected_ws.clear()
    yield
    ws_mod._connected_ws.clear()


def _make_task(name: str, task_id: str, is_root: bool = False) -> SimpleNamespace:
    """构造最小可用的 task 桩。id 属性由 AgentTask.id property(session.id)提供。"""
    return SimpleNamespace(
        name=name,
        id=task_id,
        session=SimpleNamespace(id=task_id),
        tool_call_id="" if is_root else "creator-1",
        notify_parent=True,  # 异步子代理(is_async → True), 事件进独立视图
        status="running",
        closed=False,
    )


class _SpinnerSpy:
    """记录 spinner.start/stop 调用的假 spinner。"""

    def __init__(self):
        self.started: list[tuple[str, str]] = []  # (text, wait_id)
        self.stopped: list[str] = []

    def start(self, text: str, wait_id: str = "") -> str:
        self.started.append((text, wait_id))
        return wait_id

    def stop(self, wait_id: str) -> None:
        self.stopped.append(wait_id)


async def _run_bridge_with_events(main_task, events: list) -> _SpinnerSpy:
    """构造主 agent + 事件队列, 驱动一次 bridge_events 直到事件耗尽退出。"""
    spinner = _SpinnerSpy()
    config = SimpleNamespace(
        current_agent=main_task,
        spinner=spinner,
        voice_mode=False,
        audio=False,
        tts_model=None,
    )
    main_task.event_queue = asyncio.Queue()
    for queued_task, event in events:
        main_task.event_queue.put_nowait((queued_task, event))
    await ws_mod.bridge_events("main-session", config)
    return spinner


async def test_subagent_thinking_spinner_has_agent_name():
    """subagent 的 ThinkingStartEvent 的 spinner 文本必须带 `[名字] ` 前缀。"""
    sub_task = _make_task("调研助手", "sub-session")
    main_task = _make_task("root", "main-session", is_root=True)
    # depth=1 的 EndEvent 让 subagent 回合结束; depth=0 让 bridge 退出
    spinner = await _run_bridge_with_events(
        main_task,
        [
            (sub_task, ThinkingStartEvent()),
            (sub_task, EndEvent(depth=1)),
            (sub_task, EndEvent(depth=0)),
        ],
    )
    assert spinner.started, "应有 spinner.start 调用"
    text, _ = spinner.started[0]
    assert text.startswith("[调研助手] "), f"subagent spinner 文本应带名字前缀, 实际: {text!r}"
    assert text.endswith("Thinking..."), f"文本应保留原始内容, 实际: {text!r}"


async def test_main_agent_spinner_has_no_prefix():
    """主 agent 的 ThinkingStartEvent 的 spinner 文本不应带任何前缀。"""
    main_task = _make_task("root", "main-session", is_root=True)
    spinner = await _run_bridge_with_events(
        main_task,
        [
            (main_task, ThinkingStartEvent()),
            (main_task, EndEvent(depth=0)),
        ],
    )
    assert spinner.started, "应有 spinner.start 调用"
    text, _ = spinner.started[0]
    assert text == "Thinking...", f"主 agent spinner 文本不应带前缀, 实际: {text!r}"