"""WebSocket 管理器:连接、事件桥接、权限交互。"""

from __future__ import annotations

import asyncio
import time
import base64
import traceback
import uuid
from dataclasses import dataclass
from typing import Any
from fastapi import WebSocket, WebSocketDisconnect
from cachetools import LRUCache

from uniclaw.agent import (
    AgentStatus,
    EndEvent,
    InterruptedEvent,
    MultiAgent,
    PermissionRequestEvent,
    ShellCommandEvent,
    TextChunkEvent,
    ThinkingChunkEvent,
    ThinkingStartEvent,
    ToolPreparingEvent,
    ToolStartEvent,
    ToolStreamEvent,
    ToolEvent,
    AssistantEvent,
    UserEvent,
)
from uniclaw.config import AppConfig, RunMode, load_config
from uniclaw.tools.session.session_manager import SessionManager
from uniclaw.tools.base import tc_name, tc_args
from uniclaw.tools.shell import Bash
from uniclaw.webui.spinner import WebSpinner
from uniclaw.utils.constants import SYSTEM_PREFIX
from uniclaw.utils.logger import get_logger
from uniclaw.utils.message import MessageRole
from pathlib import Path

# 会话 LRU 缓存:session_id → AppConfig
session_cache: LRUCache[str, AppConfig] = LRUCache(maxsize=10)

# 所有已连接的 WebSocket(广播用)
_connected_ws: set[WebSocket] = set()
_connected_ws_lock = asyncio.Lock()

# 挂起的权限请求:req_id → asyncio.Future(需要锁保护)
pending_permissions: dict[str, asyncio.Future] = {}
_permissions_lock = asyncio.Lock()

# 挂起的输入请求:req_id → asyncio.Future(需要锁保护)
pending_inputs: dict[str, asyncio.Future] = {}
_inputs_lock = asyncio.Lock()

# 正在运行的 bridge_events 任务:session_id → asyncio.Task(需要锁保护)
_bridge_tasks: dict[str, asyncio.Task] = {}
_bridge_tasks_lock = asyncio.Lock()

# 正在运行的 _watch_user_queue 任务:session_id → asyncio.Task
_watch_tasks: dict[str, asyncio.Task] = {}
_watch_tasks_lock = asyncio.Lock()


@dataclass
class PendingRequest:
    """会话级待处理请求,跨 WebSocket 连接存活。"""

    msg_type: str  # "permission_request" | "input_request"
    msg_data: dict  # 发给前端的完整消息(用于重发)
    future: asyncio.Future  # 等待前端响应的 Future


# session_id → {req_id → PendingRequest},不随 WS 断开清除
pending_session_requests: dict[str, dict[str, PendingRequest]] = {}
_pending_session_lock = asyncio.Lock()


async def get_or_load_session(session_id: str) -> AppConfig:
    """从缓存获取 AppConfig,缓存未命中则从磁盘加载。"""
    if session_id in session_cache:
        config = session_cache[session_id]
        # 确保 event_queue 已初始化
        if config.current_agent.event_queue is None:
            config.current_agent.event_queue = asyncio.Queue()
        return config
    # 从磁盘加载
    session = SessionManager.load_session(session_id)
    if not session:
        raise ValueError(f"会话 {session_id} 不存在")
    spinner = WebSpinner()
    config = load_config(
        root_dir=session.root_dir,
        spinner=spinner,
        session=session,
        run_mode=RunMode.WEBUI,
        session_type=session.session_type,
    )
    # 初始化 event_queue
    config.current_agent.event_queue = asyncio.Queue()
    session_cache[session_id] = config
    return config


async def _register_pending(session_id: str, req_id: str, req: PendingRequest):
    """注册待处理请求到会话级注册表。"""
    async with _pending_session_lock:
        if session_id not in pending_session_requests:
            pending_session_requests[session_id] = {}
        pending_session_requests[session_id][req_id] = req
    # 通知所有连接的前端:该会话有待处理请求
    await _broadcast_attention(session_id, req.msg_type)


async def _unregister_pending(session_id: str, req_id: str):
    """从注册表移除已处理的请求。"""
    async with _pending_session_lock:
        session_reqs = pending_session_requests.get(session_id)
        if session_reqs:
            session_reqs.pop(req_id, None)
            if not session_reqs:
                del pending_session_requests[session_id]
    # 检查该会话是否还有其他待处理请求
    async with _pending_session_lock:
        has_remaining = session_id in pending_session_requests
    if not has_remaining:
        await _broadcast_attention_clear(session_id)


async def _broadcast_attention(session_id: str, reason: str):
    """广播 session_attention 事件。"""
    await _broadcast(
        {
            "event": "session_attention",
            "session_id": session_id,
            "reason": reason,
            "message": "会话有待处理的请求",
        }
    )


async def _broadcast_attention_clear(session_id: str):
    """广播 session_attention_clear 事件。"""
    await _broadcast(
        {
            "event": "session_attention_clear",
            "session_id": session_id,
        }
    )


def _log_task_error(task: asyncio.Task):
    """done_callback: 记录未处理的 Task 异常到日志。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        get_logger("webui", Path.cwd()).error(
            f"未捕获的异步任务异常: {exc}\n{''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}"
        )


async def _broadcast(data: dict):
    """向所有已连接的 WebSocket 广播消息(前端按 session_id 过滤)。"""
    async with _connected_ws_lock:
        targets = list(_connected_ws)
    for w in targets:
        await _safe_send(w, data)


async def _resend_pending_requests(session_id: str):
    """当 set_active 或 chat 时,重新发送该会话的所有待处理请求。"""
    async with _pending_session_lock:
        reqs = dict(pending_session_requests.get(session_id, {}))
    if reqs:
        get_logger("webui", Path.cwd()).info(
            f"[{session_id}] 重发 {len(reqs)} 个待处理请求: {list(reqs.keys())}"
        )
    for req_id, req in reqs.items():
        if not req.future.done():
            await _broadcast(req.msg_data)


async def handle_permission_response(
    req_id: str, approved: bool, reason: str = "", always: bool = False
):
    """处理权限响应,唤醒等待的 bridge_events。"""
    async with _permissions_lock:
        future = pending_permissions.get(req_id)
    if future and not future.done():
        future.set_result({"approved": approved, "reason": reason, "always": always})


async def handle_input_response(req_id: str, value: str = ""):
    """处理输入响应,唤醒等待的命令。"""
    async with _inputs_lock:
        future = pending_inputs.get(req_id)
    if future and not future.done():
        future.set_result(value)


async def bridge_events(session_id: str, config: AppConfig):
    """读取 agent 的 event_queue,将事件广播给所有订阅该会话的连接。"""
    task = config.current_agent
    get_logger("webui", Path.cwd()).info(f"[{session_id}] bridge_events 启动")
    # 通知前端 agent 状态
    await _broadcast({"event": "status", "session_id": session_id, "status": "running"})
    while True:
        try:
            queued_task, event = await task.event_queue.get()
            is_subagent = queued_task is not task
            agent_name = queued_task.name if is_subagent else ""
            get_logger("webui", Path.cwd()).info(
                f"[{session_id}] 收到事件: {type(event).__name__}"
                + (f" (来自子智能体: {agent_name})" if is_subagent else "")
            )
        except Exception as e:
            get_logger("webui", Path.cwd()).error(
                f"[{session_id}] bridge_events 异常: {e}"
            )
            break

        # === 生命周期事件 ===
        if isinstance(event, EndEvent):
            config.spinner.stop(wait_id=queued_task.id)
            if event.depth == 0:
                # 主 agent 或所有子代理完成,广播 end 事件并退出 bridge
                await _broadcast(
                    {"event": "status", "session_id": session_id, "status": "completed"}
                )
                await _broadcast(
                    {"event": "end", "session_id": session_id, "depth": event.depth}
                )
                break
            else:
                # subagent 结束(depth > 0),通知前端子智能体完成
                await _broadcast(
                    {
                        "event": "subagent_end",
                        "session_id": session_id,
                        "agent_name": agent_name,
                        "depth": event.depth,
                    }
                )
            continue

        # === 阻塞事件:需要等待前端响应 ===
        elif isinstance(event, PermissionRequestEvent):
            req_id = f"perm_{id(event)}"
            _tn = tc_name(event.tool_call)
            _ta = tc_args(event.tool_call)
            get_logger("webui", Path.cwd()).info(
                f"[{session_id}] 权限请求: tool={_tn}, args={_ta}, raw_tool_call_keys={list(event.tool_call.keys())}"
            )
            _created_at = int(time.time())
            _timeout = config.permission_timeout if config else 300
            permission_msg = {
                "event": "permission_request",
                "id": req_id,
                "session_id": session_id,
                "tool_name": _tn,
                "args": _ta,
                "description": event.description,
                "explanation": event.explanation,
                "agent_name": event.agent_name,
                "created_at": _created_at,
                "timeout": _timeout,
            }

            # 创建 Future 并注册到会话级注册表(跨 WS 连接存活)
            perm_future: asyncio.Future = asyncio.get_event_loop().create_future()
            pending_req = PendingRequest(
                msg_type="permission_request",
                msg_data=permission_msg,
                future=perm_future,
            )
            async with _permissions_lock:
                pending_permissions[req_id] = perm_future
            await _register_pending(session_id, req_id, pending_req)

            # 广播给所有订阅该会话的连接
            await _broadcast(permission_msg)

            try:
                response = await asyncio.wait_for(perm_future, timeout=_timeout)
            except asyncio.TimeoutError:
                response = {"approved": False, "reason": "权限请求超时"}
            finally:
                async with _permissions_lock:
                    pending_permissions.pop(req_id, None)
                await _unregister_pending(session_id, req_id)

            if response["approved"]:
                event.content = True
                # "始终允许":将规则持久化
                if response.get("always") and config.root_dir:
                    try:
                        from uniclaw.tools.security.security import add_permission_rule

                        tool_name = tc_name(event.tool_call)
                        add_permission_rule("tool", tool_name, Path(config.root_dir))
                    except Exception:
                        pass
            else:
                event.content = response["reason"] if response["reason"] else False
            event.return_event.set()

        # === 流式事件 ===
        elif isinstance(event, ThinkingStartEvent):
            config.spinner.start("Thinking...", wait_id=queued_task.id)
            await _broadcast(
                {
                    "event": "thinking_start",
                    "session_id": session_id,
                    "is_subagent": is_subagent,
                    "agent_name": agent_name,
                }
            )

        elif isinstance(event, ThinkingChunkEvent):
            config.spinner.start("Thinking...", wait_id=queued_task.id)
            await _broadcast(
                {
                    "event": "thinking",
                    "session_id": session_id,
                    "content": event.content,
                    "is_subagent": is_subagent,
                    "agent_name": agent_name,
                }
            )

        elif isinstance(event, TextChunkEvent):
            config.spinner.stop(wait_id=queued_task.id)
            await _broadcast(
                {
                    "event": "text",
                    "session_id": session_id,
                    "content": event.content,
                    "is_subagent": is_subagent,
                    "agent_name": agent_name,
                }
            )
            # 语音模式: 流式积累文本,按句子边界触发 TTS
            if (
                config.voice_mode
                and config.tts_model
                and config.audio
                and not is_subagent
            ):
                from uniclaw.tools.tts.tts import tts_enqueue

                tts_enqueue(session_id, event.content, config)

        elif isinstance(event, ToolPreparingEvent):
            config.spinner.start(f"'{event.name}'...", wait_id=queued_task.id)
            await _broadcast(
                {
                    "event": "tool_preparing",
                    "session_id": session_id,
                    "name": event.name,
                    "args": event.args,
                    "is_subagent": is_subagent,
                    "agent_name": agent_name,
                }
            )

        # === 批量事件 ===
        elif isinstance(event, UserEvent):
            # subagent 的 UserEvent 不广播(已作为工具参数显示)
            if not is_subagent:
                msg = {
                    "event": "user",
                    "session_id": session_id,
                    "content": event.content,
                }
                # 附带消息在后端列表中的索引,供前端删除功能使用
                try:
                    session = config.current_agent.session
                    msg["msg_idx"] = len(session._messages) - 1
                except Exception:
                    pass
                await _broadcast(msg)

        elif isinstance(event, AssistantEvent):
            config.spinner.stop(wait_id=queued_task.id)
            await _broadcast(
                {
                    "event": "assistant",
                    "session_id": session_id,
                    "content": event.content,
                    "tool_calls": event.tool_calls,
                    "in_tokens": event.in_tokens,
                    "out_tokens": event.out_tokens,
                    "model_name": event.model_name,
                    "is_subagent": is_subagent,
                    "agent_name": agent_name,
                }
            )
            # 语音模式: flush 剩余文本到 TTS 队列
            if (
                config.voice_mode
                and event.content
                and config.tts_model
                and config.audio
                and not is_subagent
            ):
                from uniclaw.tools.tts.tts import tts_flush

                await tts_flush(session_id, config)

        elif isinstance(event, ToolStartEvent):
            config.spinner.stop(wait_id=queued_task.id)
            config.spinner.start(f"'{event.name}' 执行中...", wait_id=queued_task.id)
            await _broadcast(
                {
                    "event": "tool_start",
                    "session_id": session_id,
                    "name": event.name,
                    "args": event.args,
                    "tool_call_id": event.tool_call_id,
                    "is_subagent": is_subagent,
                    "agent_name": agent_name,
                }
            )

        elif isinstance(event, ToolStreamEvent):
            # 工具流式输出(纯 UI 显示用,不参与 LLM 交互)
            await _broadcast(
                {
                    "event": "tool_stream",
                    "session_id": session_id,
                    "name": event.name,
                    "content": event.content,
                    "tool_call_id": event.tool_call_id,
                }
            )

        elif isinstance(event, ToolEvent):
            config.spinner.stop(wait_id=queued_task.id)
            await _broadcast(
                {
                    "event": "tool_end",
                    "session_id": session_id,
                    "name": event.name,
                    "content": event.content,
                    "tool_call_id": event.tool_call_id,
                    "args": event.args,
                    "is_subagent": is_subagent,
                    "agent_name": agent_name,
                }
            )
            await _notify_config_changed(session_id)

        # === 用户 Shell 命令(agent 运行时) ===
        elif isinstance(event, ShellCommandEvent):
            config.spinner.stop(wait_id=queued_task.id)
            await _broadcast(
                {
                    "event": "shell_running",
                    "session_id": session_id,
                    "command": event.command,
                }
            )
            try:
                out = await Bash.func(event.command, config=config)
            except Exception as e:
                out = f"命令执行失败: {e}"
            # 计算 msg_idx: drain_user_queue 会在 send_event_to_user 返回后
            # 调用 session.add_message 将消息添加到 _messages 末尾,
            # 此时 add_message 尚未执行,所以当前 len 即为即将添加的索引。
            shell_msg_idx = -1
            if event.source == "chat":
                try:
                    shell_msg_idx = len(config.current_agent.session._messages)
                except Exception:
                    pass
            await _broadcast(
                {
                    "event": "shell_result",
                    "session_id": session_id,
                    "command": event.command,
                    "output": out,
                    "success": True,
                    "source": event.source,
                    "msg_idx": shell_msg_idx,
                }
            )
            event.content = out
            event.return_event.set()
            continue

        # === 状态事件 ===
        elif isinstance(event, InterruptedEvent):
            config.spinner.stop(wait_id=queued_task.id)
            await _broadcast(
                {
                    "event": "interrupted",
                    "session_id": session_id,
                    "message": event.message,
                }
            )

        else:
            get_logger("webui", Path.cwd()).warning(
                f"未知事件类型: {type(event).__name__}"
            )


async def _safe_send(ws: WebSocket, data: dict):
    """安全发送 WebSocket 消息,忽略断开异常。"""
    try:
        await ws.send_json(data)
    except Exception:
        get_logger("webui", Path.cwd()).debug(
            f"WebSocket 发送失败(连接可能已断开): {traceback.format_exc()}"
        )


def _make_output_callback(ws: WebSocket, session_id: str):
    """创建 info/ok/warn/err 的异步输出回调,实时发送到 WebSocket。"""

    async def _send(msg_text: str, level: str):
        await _safe_send(
            ws,
            {
                "event": "command_output",
                "session_id": session_id,
                "content": msg_text,
                "level": level,
            },
        )

    return _send


def _make_broadcast_callback(session_id: str):
    """创建广播版输出回调,发送到所有已连接的 WebSocket。"""

    async def _send(msg_text: str, level: str):
        await _broadcast(
            {
                "event": "command_output",
                "session_id": session_id,
                "content": msg_text,
                "level": level,
            }
        )

    return _send


async def _notify_config_changed(session_id: str):
    """通知前端 config 已变更,前端应重新获取配置(含 todolist 等)。"""
    await _broadcast({"event": "config_changed", "session_id": session_id})


async def _cancel_watch_task(session_id: str):
    """取消指定 session 的 _watch_user_queue 任务(如有)。"""
    async with _watch_tasks_lock:
        wt = _watch_tasks.pop(session_id, None)
    if wt and not wt.done():
        wt.cancel()


async def _watch_user_queue(session_id: str, config: AppConfig):
    """bridge_events 结束后监听 user_queue,捕获延迟唤醒消息(如 sleep_timer)并重新触发 agent。

    超时 5 分钟无唤醒消息则自动退出,避免 task 泄漏。
    agent 被其他入口(如 handle_ws_message)启动时,主循环会检测到并退出。
    """
    async with _watch_tasks_lock:
        _watch_tasks[session_id] = asyncio.current_task()
    task = config.current_agent
    try:
        while True:
            if task.status == AgentStatus.RUNNING:
                break
            try:
                msg = task.user_queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(1)
                continue
            if not msg:
                continue
            multi_agent = MultiAgent.get_instance()
            multi_agent.start_agent(msg, config)
            await _start_bridge(session_id, config)
            break
    except asyncio.CancelledError:
        pass
    except Exception:
        get_logger("webui", Path.cwd()).warning(
            f"[{session_id}] _watch_user_queue 异常: {traceback.format_exc()}"
        )
    finally:
        async with _watch_tasks_lock:
            _watch_tasks.pop(session_id, None)


async def _start_bridge(session_id: str, config: AppConfig):
    """启动 bridge_events 任务(按 session 管理,不绑 WebSocket)。"""
    async with _bridge_tasks_lock:
        task = _bridge_tasks.get(session_id)
        if task and not task.done():
            return  # 已在运行
        t = asyncio.create_task(bridge_events(session_id, config))
        _bridge_tasks[session_id] = t

        def _on_bridge_done(done_task: asyncio.Task):
            _bridge_tasks.pop(session_id, None)
            # bridge 结束后启动 user_queue 监听
            wt = asyncio.create_task(_watch_user_queue(session_id, config))
            wt.add_done_callback(_log_task_error)

        t.add_done_callback(_on_bridge_done)
        t.add_done_callback(_log_task_error)


async def handle_ws_message(ws: WebSocket, msg: dict):
    """处理客户端发来的 WS 消息。"""
    msg_type = msg.get("type", "")

    # === chat 消息:区分创建会话和已有会话 ===
    if msg_type == "chat":
        root_dir = msg.get("root_dir")
        session_id = msg.get("session_id")
        free_chat = msg.get("free_chat", False)

        if free_chat and not session_id:
            # 创建自由聊天会话(无需项目目录,load_config 自动分配工作目录)
            spinner = WebSpinner()
            from uniclaw.tools.session.session import SessionType

            config = load_config(
                spinner=spinner,
                run_mode=RunMode.WEBUI,
                session_type=SessionType.FREE_CHAT,
            )
            session_id = config.current_agent.session.id
            spinner.set_session_id(session_id)
            config.current_agent.event_queue = asyncio.Queue()
            session_cache[session_id] = config

            async def _broadcast_send_free(data):
                await _broadcast(data)

            spinner.set_send_callback(_broadcast_send_free)
            config.output_callback = _make_broadcast_callback(session_id)
            await _safe_send(
                ws,
                {
                    "event": "session_created",
                    "session_id": session_id,
                },
            )
            await _start_bridge(session_id, config)
        elif root_dir and not session_id:
            # 创建新会话
            spinner = WebSpinner()
            config = load_config(
                root_dir=Path(root_dir), spinner=spinner, run_mode=RunMode.WEBUI
            )
            session_id = config.current_agent.session.id
            spinner.set_session_id(session_id)
            # 初始化 event_queue
            config.current_agent.event_queue = asyncio.Queue()
            session_cache[session_id] = config

            # spinner/output 回调广播到所有连接
            async def _broadcast_send(data):
                await _broadcast(data)

            spinner.set_send_callback(_broadcast_send)
            config.output_callback = _make_broadcast_callback(session_id)
            await _safe_send(
                ws,
                {
                    "event": "session_created",
                    "session_id": session_id,
                    "root_dir": root_dir,
                },
            )
            await _start_bridge(session_id, config)
        elif session_id and not root_dir:
            # 已有会话
            config = await get_or_load_session(session_id)
            config.spinner.set_session_id(session_id)
            await _start_bridge(session_id, config)
            # 重发待处理请求(处理新浏览器连接的场景)
            await _resend_pending_requests(session_id)
        else:
            await _safe_send(
                ws,
                {
                    "event": "error",
                    "message": "chat 消息必须带 root_dir、session_id 或 free_chat",
                },
            )
            return

        # 存储 ws 回调,供 web_input / AskUserQuestion 等使用
        config.ws_send = ws.send_json
        task = config.current_agent
        raw_content = msg.get("content", "")

        content = _build_content_with_files(raw_content, msg.get("files", []), config)
        get_logger("webui", Path.cwd()).info(
            f"[{session_id}] 准备启动 agent, task.status={task.status}"
        )
        if task.status != AgentStatus.RUNNING:
            get_logger("webui", Path.cwd()).info(f"[{session_id}] 启动 agent")
            await _cancel_watch_task(session_id)
            multi_agent = MultiAgent.get_instance()
            agent_task = multi_agent.start_agent(content, config)
            get_logger("webui", Path.cwd()).info(
                f"[{session_id}] Agent 启动成功, future={agent_task.future}"
            )
            # 添加异常回调,防止异常被静默吞掉
            if agent_task.future:
                agent_task.future.add_done_callback(
                    lambda t: (
                        get_logger("webui", Path.cwd()).error(
                            f"[{session_id}] Agent error: {t.exception()}"
                        )
                        if t.exception()
                        else None
                    )
                )
        else:
            # Agent 正在运行,将消息放入队列(drain_user_queue 会处理)
            task.user_queue.put_nowait(content)
        return

    # === 其他消息:必须带 session_id ===
    session_id = msg.get("session_id")
    if not session_id:
        await _safe_send(
            ws, {"event": "error", "message": f"{msg_type} 消息必须带 session_id"}
        )
        return

    try:
        config = await get_or_load_session(session_id)
    except ValueError as e:
        await _safe_send(
            ws, {"event": "error", "message": str(e), "session_id": session_id}
        )
        return

    task = config.current_agent

    if msg_type == "shell":
        cmd = msg.get("command", "")
        source = msg.get("source", "chat")
        if task.status == AgentStatus.RUNNING:
            # Agent 运行中:放入 user_queue,drain_user_queue 会创建 ShellCommandEvent
            # !!前缀=控制台命令(不注入session)；!前缀=聊天区命令(注入session)
            prefix = "!!" if source == "console" else "!"
            task.user_queue.put_nowait(f"{prefix}{cmd}")
        else:
            # Agent 空闲:直接执行；仅聊天区命令注入 session
            try:
                output = await Bash.func(cmd, config=config)
            except Exception as e:
                output = f"命令执行失败: {e}"
            if source == "chat":
                task.session.add_message(
                    MessageRole.USER,
                    f"{SYSTEM_PREFIX}(用户执行Shell命令)\n$ {cmd}\n{output}",
                )
            await _safe_send(
                ws,
                {
                    "event": "shell_result",
                    "session_id": session_id,
                    "command": cmd,
                    "output": output,
                    "success": True,
                    "source": source,
                    "msg_idx": (
                        len(task.session._messages) - 1 if source == "chat" else -1
                    ),
                },
            )

    elif msg_type == "command":
        from uniclaw.commands import handle_slash

        # 始终绑定当前 WebSocket 的回调(与 chat 路径一致)
        config.output_callback = _make_output_callback(ws, session_id)
        config.ws_send = ws.send_json
        source = msg.get("source", "chat")
        result = await handle_slash(msg.get("command", ""), config)
        # info/ok/warn/err 已通过 output_callback 实时发送
        # 仅 str 返回值(技能路径)需要额外发送
        if isinstance(result, str) and result:
            await _safe_send(
                ws,
                {
                    "event": "command_result",
                    "session_id": session_id,
                    "command": msg.get("command", ""),
                    "output": result,
                    "source": source,
                },
            )

    elif msg_type == "permission_response":
        await handle_permission_response(
            msg.get("id", ""),
            msg.get("approved", False),
            msg.get("reason", ""),
            msg.get("always", False),
        )

    elif msg_type == "input_response":
        await handle_input_response(
            msg.get("id", ""),
            msg.get("value", ""),
        )

    elif msg_type == "cancel":
        task.cancel_event.set()

    elif msg_type == "set_active":
        # 前端通知当前活跃会话:重发待处理请求 + 通知刷新 config
        await _resend_pending_requests(session_id)
        await _notify_config_changed(session_id)

    elif msg_type == "voice_mode":
        enabled = msg.get("enabled", False)
        if enabled and (not config.tts_model or not config.audio):
            await _safe_send(
                ws,
                {
                    "event": "error",
                    "session_id": session_id,
                    "message": "TTS 未配置(tts_model 或 audio 为空)",
                },
            )
        else:
            config.voice_mode = enabled
            # 关闭语音模式: 清理 TTS 队列
            if not enabled:
                from uniclaw.tools.tts.tts import tts_cleanup

                await tts_cleanup(session_id)
            await _notify_config_changed(session_id)

    elif msg_type == "asr_stream":
        # 免提语音:ASR → 过滤口语 → 直接发送给 Agent
        await _handle_asr_stream(ws, session_id, config, msg)

    elif msg_type == "asr_stream_audio":
        # 接收音频数据,实时识别
        await _handle_asr_stream_audio(ws, session_id, config, msg)

    elif msg_type == "asr_stream_reset":
        # 清空流式 ASR 缓冲区,取消待执行的识别任务
        session = _asr_stream_sessions.pop(session_id, None)
        if session and session.get("timer") and not session["timer"].done():
            session["timer"].cancel()


def _build_content_with_files(content: str, files: list[dict], config) -> Any:
    """构建带附件的消息内容。

    - 图片/视频/音频:转为多模态块
    - 其他文件:写入 root_dir,内容追加到消息中
    """
    import base64

    from uniclaw.tools.session.session import MultimodalBlock

    if not files:
        return content

    MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
    blocks = [MultimodalBlock(type="text", text=content)]
    for f in files:
        name = f.get("name", "")
        data = f.get("data", "")
        # 检查文件大小(base64 编码后约为原始大小的 4/3)
        if len(data) * 3 / 4 > MAX_FILE_SIZE:
            get_logger("webui", Path.cwd()).warning(f"附件 {name} 超过500MB限制,已跳过")
            blocks.append(
                MultimodalBlock(
                    type="text", text=f"[附件: {name} 超过500MB限制,已跳过]"
                )
            )
            continue
        mime = f.get("mime", "")

        if mime.startswith("image/"):
            blocks.append(
                MultimodalBlock(
                    type="image_url", image_url={"url": f"data:{mime};base64,{data}"}
                )
            )
        elif mime.startswith("video/"):
            blocks.append(
                MultimodalBlock(
                    type="video_url", video_url={"url": f"data:{mime};base64,{data}"}
                )
            )
        elif mime.startswith("audio/"):
            # input_audio 需要 format 字段(wav/mp3 等)
            audio_format = mime.split("/")[-1]  # e.g. "audio/wav" → "wav"
            blocks.append(
                MultimodalBlock(
                    type="input_audio",
                    input_audio={"data": data, "format": audio_format},
                )
            )
        else:
            # 非多媒体文件:写入 root_dir,由 agent 自行处理
            try:
                file_path = config.root_dir / name
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_bytes = base64.b64decode(data)
                file_path.write_bytes(file_bytes)
                blocks.append(MultimodalBlock(type="text", text=f"[附件: {name}]"))
            except Exception as e:
                get_logger("webui", Path.cwd()).error(f"保存附件失败: {e}")
                blocks.append(
                    MultimodalBlock(type="text", text=f"[附件: {name} 保存失败: {e}]")
                )
    # 转换为字典列表,确保可JSON序列化
    return [block.to_dict() for block in blocks]


async def _handle_asr_stream(
    ws: WebSocket, session_id: str, config: AppConfig, msg: dict
):
    """处理语音消息:过滤口语 → 直接发送给 Agent。"""
    text = msg.get("audio", "").strip()

    if not text:
        await _safe_send(
            ws,
            {
                "event": "asr_stream_result",
                "session_id": session_id,
                "status": "error",
                "message": "缺少文本内容",
            },
        )
        return

    try:
        # 过滤口语词
        processed = await _filter_filler_words(text, config)

        # 判断是否有意义
        if not processed["meaningful"]:
            await _safe_send(
                ws,
                {
                    "event": "asr_stream_result",
                    "session_id": session_id,
                    "status": "ignored",
                    "raw": text,
                    "text": processed["text"],
                    "reason": "filler_only",
                },
            )
            return

        # 发送给 Agent
        final_text = processed["text"]
        task = config.current_agent

        if task.status != AgentStatus.RUNNING:
            multi_agent = MultiAgent.get_instance()
            multi_agent.start_agent(final_text, config)
            await _start_bridge(session_id, config)
        else:
            task.user_queue.put_nowait(final_text)

        await _safe_send(
            ws,
            {
                "event": "asr_stream_result",
                "session_id": session_id,
                "status": "sent",
                "raw": text,
                "text": final_text,
            },
        )

    except Exception as e:
        get_logger("webui", Path.cwd()).error(f"语音消息处理失败: {e}")
        await _safe_send(
            ws,
            {
                "event": "asr_stream_result",
                "session_id": session_id,
                "status": "error",
                "message": str(e),
            },
        )


# 流式 ASR 会话状态:session_id → {buffer, last_text, sample_rate, last_asr_time, timer}
_asr_stream_sessions: dict[str, dict] = {}

# ASR 识别间隔(秒)
ASR_INTERVAL = 2.0


async def _schedule_asr(
    ws: WebSocket, session_id: str, config: AppConfig, delay: float = 0
):
    """延迟执行 ASR 识别。"""
    import time

    # 等待一段时间后执行
    if delay > 0:
        await asyncio.sleep(delay)

    if session_id not in _asr_stream_sessions:
        return

    session_state = _asr_stream_sessions[session_id]
    buffer = bytes(session_state["buffer"])
    sample_rate = session_state["sample_rate"]

    if not buffer:
        return

    # 执行识别
    raw_text = await _asr_recognize(buffer, sample_rate, config)

    # session 可能在识别期间被 reset
    if session_id not in _asr_stream_sessions:
        return

    session_state["last_asr_time"] = time.time()

    if not raw_text:
        return

    # 与上次结果比较,避免重复
    if raw_text == session_state["last_text"]:
        return

    session_state["last_text"] = raw_text

    # 返回识别结果(实时显示)
    await _safe_send(
        ws,
        {
            "event": "asr_stream_result",
            "session_id": session_id,
            "status": "recognition",
            "text": raw_text,
        },
    )


async def _asr_recognize(
    buffer: bytes, sample_rate: int, config: AppConfig
) -> str | None:
    """执行 ASR 识别,返回识别文本。"""
    import tempfile
    from uniclaw.utils.audio import pcm_to_wav, asr as asr_func

    tmp_path = None
    try:
        # 转为 WAV
        wav_data = pcm_to_wav(buffer, sample_rate)

        # 写入临时文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_data)
            tmp_path = f.name

        # ASR 识别
        raw_text = await asr_func(tmp_path, config)

        if not raw_text or not raw_text.strip():
            return None

        return raw_text.strip()

    except Exception as e:
        get_logger("webui", Path.cwd()).error(f"ASR 识别失败: {e}")
        return None
    finally:
        # 确保异常时也能清理临时文件
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


async def _handle_asr_stream_audio(
    ws: WebSocket, session_id: str, config: AppConfig, msg: dict
):
    """处理流式音频数据,累积到缓冲区,智能调度识别。"""

    audio_data = msg.get("audio", "")
    if not audio_data:
        return

    if not config.asr_model:
        return

    # 获取或创建会话状态
    if session_id not in _asr_stream_sessions:
        _asr_stream_sessions[session_id] = {
            "buffer": bytearray(),
            "last_text": "",
            "sample_rate": msg.get("sample_rate", 16000),
            "last_asr_time": time.time(),
            "timer": None,
            "lock": asyncio.Lock(),
        }

    session_state = _asr_stream_sessions[session_id]

    # 同一会话串行处理,避免 buffer/定时器竞态
    async with session_state["lock"]:
        # 解码并累积音频数据
        audio_bytes = base64.b64decode(audio_data)
        session_state["buffer"].extend(audio_bytes)

        # 取消之前的定时器
        if session_state["timer"] and not session_state["timer"].done():
            session_state["timer"].cancel()

        # 检查距离上次识别的时间
        now = time.time()
        time_since_last = now - session_state["last_asr_time"]

        # buffer 太小额外延迟,等待更多数据(buffer 越小延迟越大)
        buffer = bytes(session_state["buffer"])
        extra_delay = (1 - len(buffer) / 3200) * 0.25 if len(buffer) < 3200 else 0

        # 离上次识别越近延迟越大,最大0.25s + buffer延迟
        interval_delay = (ASR_INTERVAL - time_since_last) * 0.25 if time_since_last < ASR_INTERVAL else 0
        delay = interval_delay + extra_delay
        session_state["timer"] = asyncio.create_task(
            _schedule_asr(ws, session_id, config, delay=delay)
        )


async def _filter_filler_words(text: str, config: AppConfig) -> dict:
    """使用小模型过滤口语词,修正识别错误,整合成连贯文字。"""
    from uniclaw.provider.router import achat
    from uniclaw.tools.session.session import Session
    from uniclaw.utils.message import MessageRole
    from uniclaw.utils.format import parse_json_from_llm

    if not config.mini_model_name:
        return {"text": text, "meaningful": True}

    # 创建临时 session 用于单轮对话
    session = Session()
    session.add_message(MessageRole.USER, text)

    try:
        ai_msg = await achat(
            system_prompt=_ASR_FILTER_PROMPT,
            session=session,
            model_name=config.mini_model_name[0],
            config=config,
            enable_thinking=False,
            thinking=False,
        )
        result = parse_json_from_llm(ai_msg.content)
        if result:
            return {
                "text": result.get("text", "").strip(),
                "meaningful": bool(result.get("meaningful", False)),
            }
        return {"text": text, "meaningful": True}
    except Exception:
        # LLM 调用失败,返回原文(视为有意义)
        return {"text": text, "meaningful": True}


_ASR_FILTER_PROMPT = """语音识别后处理助手。将语音识别结果整理成通顺的书面文字。

规则:
1. 去除语气词/口头禅(嗯、啊、哦、呃、那个、就是说、对对对等)
2. 修正谐音错误(在那→在哪、什么时后→什么时候、因该→应该等)
3. 补充标点,调整语序,保留原意
4. 判断是否有实质意义(纯语气词、<2字视为无意义)

返回JSON:{"text": "整理后文字", "meaningful": true/false}"""


async def websocket_endpoint(ws: WebSocket):
    """WebSocket 入口(需要 JWT 认证,可信 IP 跳过)。"""
    # 可信 IP 跳过认证
    client_ip = ws.client.host if ws.client else ""
    from uniclaw.webui.app import _is_trusted_ip

    if not _is_trusted_ip(client_ip):
        # 从 query param 提取 token
        token = ws.query_params.get("token", "")
        if not token:
            await ws.close(code=4001, reason="未登录")
            return
        from uniclaw.webui.auth import verify_token

        username = verify_token(token)
        if not username:
            await ws.close(code=4001, reason="登录已过期")
            return

    await ws.accept()
    async with _connected_ws_lock:
        _connected_ws.add(ws)
    try:
        while True:
            data = await ws.receive_json()
            get_logger("webui", Path.cwd()).info(
                f"收到 WS 消息: {data.get('type', 'unknown')}"
            )
            # 并发处理,避免接收循环被阻塞(如 command 等待 input_response 时死锁)
            t = asyncio.create_task(handle_ws_message(ws, data))
            t.add_done_callback(_log_task_error)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        get_logger("webui", Path.cwd()).error(
            f"WebSocket 错误: {traceback.format_exc()}"
        )
    finally:
        async with _connected_ws_lock:
            _connected_ws.discard(ws)
        # 不取消 bridge/watch 任务 — 让 agent 继续运行
        # _safe_send 会静默处理断开的 WS
        # 用户重连后 set_active 会重新广播


# ── 模块级便捷接口(供 commands/ 导入)──────────────────────


async def notify_session_switched(session_id: str, old_session_id: str = ""):
    """通知前端会话已切换(用于 fork 后切换到新会话)。"""
    await _broadcast(
        {
            "event": "session_switched",
            "session_id": session_id,
            "old_session_id": old_session_id,
        }
    )


async def notify_session_deleted(session_id: str, root_dir=None):
    """通知前端会话已删除。如果删除的是当前活跃会话,前端应进入新建会话状态。"""
    await _broadcast(
        {
            "event": "session_deleted",
            "session_id": session_id,
            "root_dir": str(root_dir) if root_dir else None,
        }
    )


async def web_input(prompt: str, title: str = "输入", config=None) -> str:
    """WebUI 模式的输入便捷函数,注册到会话级注册表并等待回答。"""
    session_id = config.current_agent.session.id
    if not session_id:
        return ""
    req_id = f"input_{uuid.uuid4().hex[:8]}"
    _created_at = int(time.time())
    _timeout = config.permission_timeout if config else 300
    input_msg = {
        "event": "input_request",
        "id": req_id,
        "session_id": session_id,
        "prompt": prompt,
        "title": title,
        "created_at": _created_at,
        "timeout": _timeout,
    }

    # 创建 Future 并注册到会话级注册表
    input_future: asyncio.Future = asyncio.get_event_loop().create_future()
    pending_req = PendingRequest(
        msg_type="input_request",
        msg_data=input_msg,
        future=input_future,
    )
    async with _inputs_lock:
        pending_inputs[req_id] = input_future
    await _register_pending(session_id, req_id, pending_req)

    await _broadcast(input_msg)

    try:
        return await asyncio.wait_for(input_future, timeout=300)
    except asyncio.TimeoutError:
        return ""
    finally:
        async with _inputs_lock:
            pending_inputs.pop(req_id, None)
        await _unregister_pending(session_id, req_id)


async def web_multi_input(
    questions: list[dict], title: str = "请选择", config=None
) -> str:
    """WebUI 模式的多问题 Tab 输入函数。"""
    session_id = config.current_agent.session.id
    if not session_id:
        return ""
    req_id = f"input_{uuid.uuid4().hex[:8]}"
    _created_at = int(time.time())
    _timeout = config.permission_timeout if config else 300
    input_msg = {
        "event": "multi_input_request",
        "id": req_id,
        "session_id": session_id,
        "questions": questions,
        "title": title,
        "created_at": _created_at,
        "timeout": _timeout,
    }

    # 创建 Future 并注册到会话级注册表(复用 input_response 处理)
    input_future: asyncio.Future = asyncio.get_event_loop().create_future()
    pending_req = PendingRequest(
        msg_type="multi_input_request",
        msg_data=input_msg,
        future=input_future,
    )
    async with _inputs_lock:
        pending_inputs[req_id] = input_future
    await _register_pending(session_id, req_id, pending_req)

    await _broadcast(input_msg)

    try:
        return await asyncio.wait_for(input_future, timeout=300)
    except asyncio.TimeoutError:
        return ""
    finally:
        async with _inputs_lock:
            pending_inputs.pop(req_id, None)
        await _unregister_pending(session_id, req_id)
