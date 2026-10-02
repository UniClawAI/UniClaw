import asyncio
import time
from datetime import datetime, timedelta
from uniclaw.config import AppConfig
from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR
from uniclaw.utils.wakeup import wake_agent
from uniclaw.tools.base import tool, ToolRuntime


def sleep_timer_available(config: AppConfig | None) -> bool:
    """判断 sleep_timer 是否可用:主 agent 与异步子代理可用,同步子代理不可用。

    同步子代理随当前回合结束即向父代理返回结果,之后的定时唤醒无人接收;
    异步子代理回合结束后仍驻留等待消息(keep_alive),可被定时唤醒继续工作。

    Args:
        config: AppConfig 实例,可为 None(视为不限制,直接可用)。

    Returns:
        bool: 可用返回 True。
    """
    if config is None or not config.is_sub:
        return True
    task = config.current_agent
    return bool(task is not None and task.keep_alive)


@tool
def sleep_timer(seconds: int, name: str = "", tool_runtime: ToolRuntime = None) -> str:
    """
    异步等待指定秒数后唤醒 AI 继续工作。函数立即返回,不阻塞。
    适用于需要等待的场景,如等待安装/下载完成、等待服务启动、等待冷却等。

    Args:
        seconds: 等待秒数(1-3600)
        name: 可选的等待原因描述,用于日志

    Returns:
        str: 确认消息
    """
    config = tool_runtime.config
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
async def wait(seconds: float, tool_runtime: ToolRuntime = None) -> str:
    """
    等待指定的秒数。此工具会阻塞当前线程,最长 30 秒。
    需要更长的非阻塞等待时,主 agent 与异步子代理可改用 sleep_timer;
    同步子代理没有该工具,应把长等待交回父代理处理。

    Args:
        seconds: 等待秒数(1-30)
    """
    config = tool_runtime.config
    if seconds <= 0 or seconds > 30:
        if not sleep_timer_available(config):
            hint = f"同步子代理没有 {sleep_timer.name},请将长等待(>30 秒)交回父agent处理"
        else:
            hint = f"超过 30 秒请使用 {sleep_timer.name}"
        return f"{TOOL_ERROR}: 等待秒数必须在 1-30 之间,{hint}"

    deadline = time.monotonic() + seconds
    last_displayed = -1

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        sec = int(remaining)

        if sec != last_displayed:
            last_displayed = sec
            await tool_runtime.stream(f"\r⏳ 剩余 {sec}s")

        await asyncio.sleep(min(0.2, remaining))

    await tool_runtime.stream("✅ 完成")
    return f"已等待 {seconds} 秒"


def get_tools(config: AppConfig | None = None) -> list:
    """获取睡眠定时器工具列表。

    sleep_timer 仅主 agent 与异步子代理可用,同步子代理过滤掉并记录不可用原因
    (供 search_tools 提示);wait 始终可用。

    Args:
        config: AppConfig 实例,用于判断子代理同步/异步。None 时不限制。

    Returns:
        list: 当前可用的工具列表。
    """
    if sleep_timer_available(config):
        if config is not None:
            config.clear_unavailable_tools([sleep_timer.name])
        return [sleep_timer, wait]
    if config is not None:
        config.record_unavailable_tools(
            [sleep_timer.name],
            "同步子代理不可用:回合结束即返回结果,定时唤醒无人接收,"
            "请将长等待交回父agent处理",
        )
    return [wait]


def get_all_tools() -> list:
    """获取所有睡眠定时器工具(无条件返回,供 registry BM25 索引发现)"""
    return [sleep_timer, wait]
