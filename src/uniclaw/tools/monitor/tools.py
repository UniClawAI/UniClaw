import sys

from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR


def _screen_supported() -> bool:
    """monitor_screen 是否有真实屏幕可看。

    真实屏幕快照依赖 ConPTY,前置条件是 Windows + pywinpty(导入名 winpty)。
    其余平台/环境只能退化成"返回输出历史",与 monitor_output 重复 ——
    不注册,免得误导调用方。
    """
    if sys.platform != "win32":
        return False
    try:
        import winpty  # noqa: F401
    except ImportError:
        return False
    return True


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
        command: 要执行的命令(如 "npm run dev"、"cargo build")。
            Windows 注意: `cmd /k "带空格路径"` 会把引号内路径当命令名
            (报"不是内部或外部命令"),需写成 `cmd /k ""带 空格路径" 参数"`
            (外层再包一对引号)或直接用 `bash -c '...'`。
        name: 进程描述名称,可选
        watch_pattern: 匹配模式(正则表达式),匹配时通知,留空则不监控。
            技巧: 如果不确定命令输出什么关键字,可以在命令末尾追加 echo 标记,
            如 command="some_cmd; echo __DONE__", watch_pattern="__DONE__",
            确保命令结束时一定触发通知,不会无限制等待。
        notify_on_match: 匹配时是否通知模型(默认 True)
        timeout: 超时时间(秒),0 表示不限制,默认 0
        visible: 可视模式(默认 False)。True 时弹出一个控制台窗口显示
            进程输出,用户可在窗口里键入内容与进程交互;
            同时 AI 仍可通过 monitor_output 查看、monitor_input 发送输入。
            适合人机配合场景,如需要用户登录账号、输入验证码、
            确认交互式安装提示等 AI 无法独立完成的步骤。
            注意: 真实控制台(ConPTY,支持 monitor_screen 整屏快照)需要
            Windows + shell 类命令(cmd/bash/powershell 等) + pywinpty 库;
            不满足条件时退化为管道输出镜像窗口(monitor_screen 返回输出历史)。

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
    前置条件: 仅 Windows + shell 类命令 + visible=True + pywinpty 的
    ConPTY 会话拥有真实屏幕;其他情况退化为返回全部输出历史,
    并在返回中说明缺少真实控制台的根因。

    Args:
        monitor_id: 进程 ID,通过 monitor_start 返回值或 monitor_list 获取

    Returns:
        str: 控制台当前屏幕内容;无真实控制台时为输出历史及原因说明
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
    会清空旧匹配记录,并用新模式回扫已有输出(历史行也会被匹配),
    之后的新输出按新模式实时匹配。

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


def get_all_tools() -> list:
    """获取所有监控工具(全量,不受运行环境限制)。

    供登记/目录等需要知道"模块提供了哪些工具"的场合使用;
    运行时该用哪些请用 get_tools()。
    """
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


def get_tools(config=None) -> list:
    """获取当前环境可用的监控工具列表(按运行环境裁剪)。

    monitor_screen 仅在 Windows + pywinpty 下注册 —— 没有真实屏幕时它
    只能退化成"返回输出历史",与 monitor_output 重复。裁剪时同步把根因
    写入 config.unavailable_tool_reasons(与 Docker/顾问域口径一致),
    直接调用 monitor_screen 时报"工具 X 未启用: 根因"而非"工具不存在"。

    Args:
        config: AppConfig 实例,用于记录/清除工具不可用原因;可为 None。
    """
    tools = get_all_tools()
    if not _screen_supported():
        tools = [t for t in tools if t is not monitor_screen]
        if config is not None:
            config.record_unavailable_tools(
                [monitor_screen.name],
                "缺少真实屏幕快照支持(需要 Windows + pywinpty)",
            )
    elif config is not None:
        config.clear_unavailable_tools([monitor_screen.name])
    return tools
