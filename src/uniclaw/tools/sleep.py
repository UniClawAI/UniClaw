import asyncio
import time
from datetime import datetime, timedelta
from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR
from uniclaw.utils.wakeup import wake_agent
from uniclaw.tools.base import tool
from uniclaw.config import AppConfig


@tool
def sleep_timer(seconds: int, name: str = "", config: AppConfig = None) -> str:
    """
    异步等待指定秒数后唤醒 AI 继续工作。函数立即返回,不阻塞。
    适用于需要等待的场景,如等待安装/下载完成、等待服务启动、等待冷却等。

    Args:
        seconds: 等待秒数(1-3600)
        name: 可选的等待原因描述,用于日志

    Returns:
        str: 确认消息
    """
    # 验证等待时间是否在合法范围内
    if seconds <= 0 or seconds > 3600:
        return f"{TOOL_ERROR}: 等待秒数必须在 1-3600 之间"

    async def _wakeup():
        """后台任务执行的等待与唤醒逻辑"""
        try:
            await asyncio.sleep(seconds)
            reason = f"({name})" if name else ""
            await wake_agent(
                f"{SYSTEM_PREFIX}(sleep_timer) 已等待{reason}{seconds} 秒,请继续工作。",
                config,
            )
        except asyncio.CancelledError:
            await wake_agent(
                f"{SYSTEM_PREFIX}(sleep_timer) 等待被取消(原定 {seconds} 秒)。",
                config,
            )
        except Exception as e:
            await wake_agent(
                f"{SYSTEM_PREFIX}(sleep_timer) 等待出错: {type(e).__name__}: {e}",
                config,
            )

    asyncio.create_task(_wakeup())

    # 计算并格式化预计唤醒的时间点
    wakeup_time = datetime.now() + timedelta(seconds=seconds)
    time_str = wakeup_time.strftime("%H:%M:%S")

    name_part = f"({name})" if name else ""
    return f"已设置 {seconds} 秒后唤醒{name_part},预计 {time_str} 唤醒,系统将自动以 {SYSTEM_PREFIX}(sleep_timer) 前缀发送唤醒通知,继续等待中..."


@tool
async def wait(seconds: float, config: AppConfig = None) -> str:
    """
    等待指定的秒数。此工具会阻塞当前线程,超过30秒请使用 sleep_timer。

    Args:
        seconds: 等待秒数(1-30)
    """
    if seconds <= 0 or seconds > 30:
        return f"{TOOL_ERROR}: 等待秒数必须在 1-30 之间,超过 30 秒请使用 sleep_timer"

    cancel_event = config.current_agent.cancel_event if config else None

    try:
        # 每 0.5 秒检查一次取消信号
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if cancel_event and cancel_event.is_set():
                elapsed = seconds - (deadline - time.monotonic())
                return (
                    f"{SYSTEM_PREFIX}(wait) 等待被取消(已等待 {max(0, elapsed):.1f} 秒)"
                )
            await asyncio.sleep(min(0.5, deadline - time.monotonic()))
    except asyncio.CancelledError:
        return f"{SYSTEM_PREFIX}(wait) 等待被取消"

    return f"已等待 {seconds} 秒"


def get_tools() -> list:
    """获取睡眠定时器工具列表"""
    return [sleep_timer, wait]


def get_all_tools() -> list:
    """获取所有睡眠定时器工具(无条件返回)"""
    return get_tools()
