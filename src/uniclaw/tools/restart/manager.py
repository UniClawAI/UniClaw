"""进程重启编排:等 agent 结束 → 保存会话 → 写标志 → 关闭服务。

由 tools.py 的 restart_agent 工具调度,在后台异步执行;
serve() 返回后由 webui/launcher.py 拉起新进程并唤醒原会话。
"""

import asyncio
import time

from uniclaw.config import AppConfig
from uniclaw.utils.constants import SYSTEM_PREFIX

# 等待 agent 结束的超时时间(秒):超时后按当前状态强制进入重启流程
WAIT_AGENT_DONE_TIMEOUT = 180.0
# agent 空闲状态需连续保持的检测次数(避免 Goal Mode 等瞬间空闲误判)
_STABLE_CHECKS = 4
_STABLE_INTERVAL = 0.5
# agent 结束后额外等待时间(秒):让 _run_cleanup 的异步保存先落盘
_SAVE_QUIET_PERIOD = 2.0

# 是否已有重启在调度中(tools.py 防重入校验经模块属性读写)
restart_scheduled = False


def _is_agent_busy(task) -> bool:
    """判断 agent 任务是否处于占用状态(不可重启进程)。

    PENDING 需区分两种情形:
    - 已调度(start_agent 已创建 future)、主循环尚未开跑 — 视为忙,
      此时待处理消息仅存于内存,重启会丢失
    - 会话刚在前端创建/点开、从未运行(AgentTask 默认值,future 为 None)— 视为空闲,
      否则未工作的会话会永远阻止重启
    """
    from uniclaw.agent import AgentStatus

    if task.status in (AgentStatus.RUNNING, AgentStatus.WAITING):
        return True
    return task.status == AgentStatus.PENDING and task.future is not None


def find_running_other_sessions(config: AppConfig) -> list[str]:
    """查找除当前会话外仍在运行 agent 的会话标题列表。"""
    from uniclaw.webui.ws import session_cache

    current_id = config.current_agent.session.id
    titles = []
    for sid, cfg in session_cache.items():
        if sid == current_id or cfg is config:
            continue
        other_task = cfg.current_agent
        if other_task and _is_agent_busy(other_task):
            titles.append(other_task.session.title or sid)
    return titles


async def do_restart(config: AppConfig) -> None:
    """后台执行的重启编排:等 agent 结束 → 保存会话 → 关闭服务。"""
    global restart_scheduled

    from uniclaw.console.ui import err, info

    try:
        task = config.current_agent
        session_id = task.session.id

        # 1. 等待 agent 结束:确保工具返回值和 LLM 最终回复都已写入会话
        await wait_agent_done(task, config)

        # 2. 等待 _run_cleanup 的异步保存落盘,再主动补存一次(幂等)兜底
        await asyncio.sleep(_SAVE_QUIET_PERIOD)
        from uniclaw.tools.session.session_manager import SessionManager

        saved_path = await SessionManager.save_session(config)

        await info(f"[restart] 会话已保存: {saved_path}", config)

        # 3. 写重启标志文件(session_id),供新进程恢复会话并唤醒 agent
        #    (唤醒提示词固定为 restart_flag.WAKE_MESSAGE,由 launcher 消费)
        from uniclaw.utils.restart_flag import write_pending_restart

        write_pending_restart(session_id)

        # 4. 通知前端即将重启(失败不影响重启流程,静默跳过)
        try:
            from uniclaw.webui.ws import notify_server_restarting

            await notify_server_restarting(session_id)
        except Exception:
            pass

        # 5. 优雅关闭 uvicorn:serve() 返回后由 launcher 拉起新进程并退出
        from uniclaw.webui import launcher

        await info("[restart] 正在关闭服务,准备拉起新进程...", config)
        launcher.request_shutdown()

    except Exception as e:
        await err(f"[restart] 重启流程异常: {type(e).__name__}: {e}", config)
        # 重启失败时尝试唤醒 agent 告知结果并解除调度锁
        try:
            from uniclaw.utils.wakeup import wake_agent

            await wake_agent(
                f"{SYSTEM_PREFIX}(restart_agent) 重启失败: {type(e).__name__}: {e}。"
                f"进程仍在运行,请继续工作。",
                config,
            )
        finally:
            restart_scheduled = False


async def wait_agent_done(task, config: AppConfig) -> None:
    """等待 agent 结束且状态稳定(连续多次非运行态)。"""
    from uniclaw.console.ui import warn

    deadline = time.monotonic() + WAIT_AGENT_DONE_TIMEOUT
    stable = 0
    while time.monotonic() < deadline:
        if _is_agent_busy(task):
            stable = 0
        else:
            stable += 1
            if stable >= _STABLE_CHECKS:
                return
        await asyncio.sleep(_STABLE_INTERVAL)
    await warn(
        f"[restart] 等待 agent 结束超时({WAIT_AGENT_DONE_TIMEOUT:.0f}s),"
        f"按当前状态(status={task.status})强制进入重启流程",
        config,
    )
