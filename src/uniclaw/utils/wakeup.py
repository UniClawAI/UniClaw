"""Agent 唤醒工具。

为 sleep_timer、monitor、scheduler 和其他需要在异步事件后通知或重启 agent 的组件提供统一的唤醒逻辑。

唤醒策略(按优先级顺序):
1. Agent 运行中/等待中 → 注入消息到 user_queue(非阻塞)
2. Agent 空闲/已完成/失败 → start_agent 重新进入循环
3. WebUI 模式 → 启动 agent 前先启动事件桥接
4. TUI / WeChat,无活跃消费者 → 在后台消费事件
"""

import asyncio
import logging

from uniclaw.config import AppConfig, RunMode
from uniclaw.console.ui import info

logger = logging.getLogger(__name__)


async def wake_agent(message: str, config: AppConfig) -> bool:
    """统一的 agent 唤醒函数。

    根据 agent 状态和运行模式决定合适的唤醒方式:
    - Agent 运行中/等待中 → 推送消息到 user_queue(agent 在下次 drain_user_queue 调用或 keep_alive 循环迭代时处理)
    - Agent 空闲 → start_agent 重新进入循环
    - WebUI 模式 → 启动 agent 前自动启动事件桥接
    - TUI 非活跃会话 / WeChat 无内联消费者 → 在后台消费事件

    Args:
        message: 要传递给 agent 的消息内容(调用方负责格式化,例如添加 SYSTEM_PREFIX 前缀)
        config: AppConfig 实例(必须已设置 current_agent)

    Returns:
        如果唤醒已调度返回 True,出错返回 False
    """
    if config is None:
        logger.warning("[wake_agent] config 为 None,无法唤醒 agent")
        return False

    task = config.current_agent
    if task is None:
        logger.warning("[wake_agent] current_agent 为 None,无法唤醒 agent")
        return False

    try:
        from uniclaw.agent import AgentStatus, MultiAgent

        status = task.status

        # Agent 正在处理中 → 注入到队列
        if status in (AgentStatus.RUNNING, AgentStatus.WAITING):
            task.user_queue.put_nowait(message)
            return True

        # Agent 空闲 → 启动新的 agent 运行
        # WebUI 模式:需要事件桥接来推送事件到前端
        if config.run_mode == RunMode.WEBUI:
            await _ensure_webui_bridge(config)

        multi_agent = MultiAgent.get_instance()
        multi_agent.start_agent(message, config=config)

        # 当没有内联消费者时需要后台消费:
        # - TUI:活跃会话有自己的消费循环；其他会话没有
        # - WeChat:_collect_response 按消息消费；如果我们从后台任务(scheduler/sleep)启动 agent,没有人在监听
        if _needs_drain(config):
            asyncio.create_task(_drain_and_log(task))

        return True

    except Exception as e:
        logger.exception("[wake_agent] 唤醒 agent 失败: %s", e)
        return False


def _needs_drain(config: AppConfig) -> bool:
    """检查 event_queue 是否需要后台消费任务。"""
    task = config.current_agent

    if config.run_mode == RunMode.CONSOLE:
        # TUI:仅当这不是活跃会话时需要消费
        return not _is_active_tui_session(config)

    if config.is_wechat:
        # WeChat:如果 _collect_response 当前未运行则需要消费。
        # 该标志由 mark_draining() 设置 / _collect_response 退出时清除。
        return not getattr(task, "_wechat_draining", False)

    return False


def mark_draining(task, draining: bool = True) -> None:
    """标记此任务是否有内联事件消费处于活跃状态。

    由 WeChat 的 _collect_response 调用,以便 wake_agent 知道不要启动竞争的后台消费。
    """
    task._wechat_draining = draining


def _is_active_tui_session(config: AppConfig) -> bool:
    """检查此 config 的会话是否是 TUI 当前显示的会话。"""
    try:
        from uniclaw.console.run import TUIApp

        tui = TUIApp.get_instance()
        if tui and tui.config:
            return (
                tui.config.current_agent.session.id == config.current_agent.session.id
            )
    except Exception as e:
        logger.debug("[wake_agent] 检查活跃 TUI 会话失败: %s", e)
    return False


def _last_assistant_message(task) -> str | None:
    """获取会话中最后一条 assistant 消息。"""
    messages = task.session.get_assistant_messages(separator=None)
    return messages[-1] if messages else None


async def _drain_and_log(task) -> None:
    """当没有内联消费者时消费 event_queue。

    如果不这样做,事件会无限累积且永远不会被消费。
    我们只消费并丢弃 — agent 的实际工作仍会执行。
    如果 agent 产生了重要输出,会发送通知。
    """
    try:
        from uniclaw.agent import AgentStatus

        queue = task.event_queue

        while task.status in (AgentStatus.RUNNING, AgentStatus.PENDING):
            if queue:
                try:
                    await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
            else:
                await asyncio.sleep(1.0)

        # Agent 完成 — 如果有有用输出则发送通知
        # task.result 仅为子 agent 设置；对于普通 agent,改为从会话获取最后一条 assistant 消息。
        result = task.result or _last_assistant_message(task)
        if result:
            from uniclaw.tools.notify import push_notification

            await push_notification(
                f"[{task.name}] 执行完成: {result}",
                title="UniClaw 后台任务",
            )
            await info(f"{task.name} 完成: {result}")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug("[wake_agent] drain_and_log 错误: %s", e, exc_info=True)


async def _ensure_webui_bridge(config: AppConfig) -> None:
    """为 WebUI 模式启动 WebSocket 事件桥接。"""
    try:
        session = config.current_agent.session
        if session is None:
            return

        from uniclaw.webui.ws import _start_bridge

        await _start_bridge(session.id, config)
    except ImportError:
        pass
    except Exception as e:
        logger.debug("[wake_agent] 启动 WebUI 桥接失败: %s", e, exc_info=True)
