"""新进程侧的重启恢复:读取重启标志,服务就绪后唤醒原 agent 继续工作。

由 webui/launcher.py 在 launch() 开始时调用 schedule_pending_restart_resume():
检测到旧进程留下的标志则安排后台任务 — 等待端口就绪 → 加载会话 →
按固定提示词(restart_flag.WAKE_MESSAGE)wake_agent。
"""

import asyncio
import time

from uniclaw.utils.logger import get_logger

# 进程级基础设施,无 session/config 上下文:日志落用户级 logs/
logger = get_logger("restart", None)


def schedule_pending_restart_resume(host: str, port: int) -> None:
    """新进程启动时检查重启标志,安排恢复会话并唤醒 agent 的后台任务。"""
    from uniclaw.utils.restart_flag import clear_pending_restart, read_pending_restart

    pending = read_pending_restart()
    if not pending:
        return
    clear_pending_restart()  # 读到即删,避免下次启动误触
    session_id = pending.get("session_id") or ""
    if not session_id:
        return
    logger.info(f"[restart] 检测到重启标志,服务就绪后将恢复会话 {session_id}")
    asyncio.create_task(_resume_after_restart(session_id, host, port))


async def _resume_after_restart(session_id: str, host: str, port: int) -> None:
    """等待本服务端口就绪后加载会话,并按固定提示词唤醒原 agent 继续工作。"""
    try:
        await _wait_port_ready(host, port)
        from uniclaw.utils.restart_flag import WAKE_MESSAGE
        from uniclaw.webui.ws import get_or_load_session
        from uniclaw.utils.wakeup import wake_agent

        config = await get_or_load_session(session_id)
        ok = await wake_agent(WAKE_MESSAGE, config)
        logger.info(f"[restart] 会话 {session_id} 恢复{'成功' if ok else '失败'}")
    except Exception as e:
        logger.exception(f"[restart] 恢复会话 {session_id} 失败: {e}")


async def _wait_port_ready(host: str, port: int, timeout: float = 60.0) -> None:
    """轮询等待本服务端口可连接(uvicorn 已开始接受请求)。"""
    check_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _, writer = await asyncio.open_connection(check_host, port)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return
        except OSError as e:
            last_err = e
            await asyncio.sleep(0.5)
    raise TimeoutError(
        f"等待端口 {check_host}:{port} 就绪超时({timeout:.0f}s): {last_err}"
    )
