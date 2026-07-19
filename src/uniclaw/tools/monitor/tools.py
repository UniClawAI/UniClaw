from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.config import AppConfig


@tool
async def monitor_start(
    command: str,
    name: str = "",
    watch_pattern: str = "",
    notify_on_match: bool = True,
    timeout: int = 0,
    config: AppConfig = None,
) -> str:
    """
    启动后台进程。可选监控输出模式,匹配时自动通知。
    适用于:启动服务、运行命令、监控日志等场景。
    AI 休息前要使用 sleep_timer 设置唤醒,防止长时间无响应。
    运行结束后及时使用 monitor_stop 关闭进程,避免资源泄漏。

    Args:
        command: 要执行的命令(如 "npm run dev"、"cargo build")
        name: 进程描述名称,可选
        watch_pattern: 匹配模式(正则表达式),匹配时通知,留空则不监控
        notify_on_match: 匹配时是否通知模型(默认 True)
        timeout: 超时时间(秒),0 表示不限制,默认 0
        config: 内部参数,由系统自动注入

    Returns:
        str: 启动结果,包含进程 ID
    """
    if not command.strip():
        return f"{TOOL_ERROR}: 命令不能为空"

    # 从 config 中获取当前任务对象
    task = config.current_agent
    root_dir = config.root_dir

    from .manager import MonitorManager
    manager = MonitorManager.get_instance()
    return await manager.start_monitor(
        command.strip(), watch_pattern.strip(), name, timeout, notify_on_match, task, root_dir
    )


@tool
async def monitor_stop(monitor_id: str) -> str:
    """
    停止指定进程。

    Args:
        monitor_id: 进程 ID

    Returns:
        str: 操作结果
    """
    from .manager import MonitorManager
    manager = MonitorManager.get_instance()
    return await manager.stop_monitor(monitor_id)


@tool
async def monitor_list() -> str:
    """
    列出所有运行中的进程。

    Returns:
        str: 进程列表
    """
    from .manager import MonitorManager
    manager = MonitorManager.get_instance()
    return await manager.list_monitors()


@tool
async def monitor_output(monitor_id: str, lines: int = 50) -> str:
    """
    获取进程的最新输出。

    Args:
        monitor_id: 进程 ID
        lines: 返回最后 N 行,默认 50

    Returns:
        str: 进程输出内容
    """
    from .manager import MonitorManager
    manager = MonitorManager.get_instance()
    return await manager.get_output(monitor_id, lines)


@tool
async def monitor_input(monitor_id: str, input_text: str) -> str:
    """
    向运行中的进程发送标准输入。

    Args:
        monitor_id: 进程 ID
        input_text: 要发送的文本(自动添加换行符)

    Returns:
        str: 操作结果消息
    """
    from .manager import MonitorManager
    manager = MonitorManager.get_instance()
    return await manager.send_input(monitor_id, input_text)


@tool
async def monitor_get_matched(monitor_id: str) -> str:
    """
    获取进程的匹配结果(如果设置了 watch_pattern)。

    Args:
        monitor_id: 进程 ID

    Returns:
        str: 匹配到的内容
    """
    from .manager import MonitorManager
    manager = MonitorManager.get_instance()
    return await manager.get_matched(monitor_id)


@tool
async def monitor_update_pattern(monitor_id: str, new_pattern: str) -> str:
    """
    修改运行中进程的匹配模式(watch_pattern)。
    支持动态切换监控的正则表达式,无需重启进程。

    Args:
        monitor_id: 进程 ID
        new_pattern: 新的匹配模式(正则表达式),留空则取消匹配(仅记录输出)

    Returns:
        str: 操作结果
    """
    from .manager import MonitorManager
    manager = MonitorManager.get_instance()
    return await manager.update_pattern(monitor_id, new_pattern.strip())


def get_tools() -> list:
    """获取监控工具列表"""
    return [monitor_start, monitor_stop, monitor_list, monitor_output, monitor_input, monitor_get_matched, monitor_update_pattern]


def get_all_tools() -> list:
    """获取所有监控工具"""
    return get_tools()
