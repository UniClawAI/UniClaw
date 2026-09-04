from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR


@tool
async def monitor_start(
    command: str,
    name: str = "",
    watch_pattern: str = "",
    notify_on_match: bool = True,
    timeout: int = 0,
    visible: bool = False,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    启动后台进程。可选监控输出模式,匹配时自动通知。
    适用于:启动服务、运行命令、监控日志等场景。
    使用 monitor_output 可随时查看进程最新输出。
    运行结束后及时使用 monitor_stop 关闭进程,避免资源泄漏。

    Args:
        command: 要执行的命令(如 "npm run dev"、"cargo build")
        name: 进程描述名称,可选
        watch_pattern: 匹配模式(正则表达式),匹配时通知,留空则不监控。
            技巧: 如果不确定命令输出什么关键字,可以在命令末尾追加 echo 标记,
            如 command="some_cmd; echo __DONE__", watch_pattern="__DONE__",
            确保命令结束时一定触发通知,不会无限制等待。
        notify_on_match: 匹配时是否通知模型(默认 True)
        timeout: 超时时间(秒),0 表示不限制,默认 0
        visible: 可视模式(默认 False)。True 时弹出一个真实控制台窗口,
            进程输出实时显示在窗口中,用户可直接在窗口里键入内容与进程交互;
            同时 AI 仍可通过 monitor_output 查看、monitor_input 发送输入。
            适合人机配合场景,如需要用户登录账号、输入验证码、
            确认交互式安装提示等 AI 无法独立完成的步骤。

    Returns:
        str: 启动结果,包含进程 ID
    """
    if not command.strip():
        return f"{TOOL_ERROR}: 命令不能为空"

    config = tool_runtime.config
    root_dir = config.root_dir

    from .manager import MonitorManager

    manager = MonitorManager.get_instance()
    return await manager.start_monitor(
        command.strip(),
        watch_pattern.strip(),
        name,
        timeout,
        notify_on_match,
        root_dir,
        config=config,
        visible=visible,
    )


@tool
async def monitor_stop(monitor_id: str) -> str:
    """
    停止指定进程。

    Args:
        monitor_id: 进程 ID,通过 monitor_start 返回值或 monitor_list 获取

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
        monitor_id: 进程 ID,通过 monitor_start 返回值或 monitor_list 获取
        lines: 返回最后 N 行,默认 50

    Returns:
        str: 进程输出内容
    """
    from .manager import MonitorManager

    manager = MonitorManager.get_instance()
    return await manager.get_output(monitor_id, lines)


@tool
async def monitor_screen(monitor_id: str) -> str:
    """
    获取控制台当前屏幕上显示的全部内容(整屏快照,类似 tmux capture-pane)。
    与 monitor_output 返回滚动输出历史不同,本工具返回此刻的画面,
    包含提示符同行内容、尚未回车的键入内容以及 vim/htop 等全屏程序界面。
    仅可视模式启动的 shell 类进程(ConPTY 会话)拥有真实屏幕;
    其他进程没有独立控制台,退化为返回全部输出历史。

    Args:
        monitor_id: 进程 ID,通过 monitor_start 返回值或 monitor_list 获取

    Returns:
        str: 控制台当前屏幕内容
    """
    from .manager import MonitorManager

    manager = MonitorManager.get_instance()
    return await manager.get_screen(monitor_id)


@tool
async def monitor_input(monitor_id: str, input_text: str) -> str:
    """
    向运行中的进程发送标准输入。

    Args:
        monitor_id: 进程 ID,通过 monitor_start 返回值或 monitor_list 获取
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
        monitor_id: 进程 ID,通过 monitor_start 返回值或 monitor_list 获取

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
        monitor_id: 进程 ID,通过 monitor_start 返回值或 monitor_list 获取
        new_pattern: 新的匹配模式(正则表达式),留空则取消匹配(仅记录输出)

    Returns:
        str: 操作结果
    """
    from .manager import MonitorManager

    manager = MonitorManager.get_instance()
    return await manager.update_pattern(monitor_id, new_pattern.strip())


@tool
async def monitor_clear() -> str:
    """
    批量清理所有已结束的监控条目(运行中的进程保留)。

    用于历史进程累积时手动清理: 自然退出/超时/异常/匹配态等
    非运行条目会从列表中移除。已清理条目的历史输出将不可再查询。

    Returns:
        str: 清理结果
    """
    from .manager import MonitorManager

    manager = MonitorManager.get_instance()
    return await manager.clear_monitors()


def get_tools() -> list:
    """获取监控工具列表"""
    return [
        monitor_start,
        monitor_stop,
        monitor_list,
        monitor_output,
        monitor_screen,
        monitor_input,
        monitor_get_matched,
        monitor_update_pattern,
        monitor_clear,
    ]


def get_all_tools() -> list:
    """获取所有监控工具"""
    return get_tools()
