import sys
from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.process import run_command, smart_decode


async def _notify_windows(title: str, message: str) -> str:
    """Windows Toast 通知。

    Returns:
        str: 成功返回空串;失败返回具体原因,由调用方转成 TOOL_ERROR。
    """
    # 用 PowerShell 自带的 AppId,避免自定义 AppId 注册问题
    app_id = (
        r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
    )

    # XML 中不能有 $ 等特殊字符,用单引号拼接避免 PowerShell 变量展开
    title_esc = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    message_esc = (
        message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )

    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        "$xml.LoadXml("
        f'\'<toast scenario="reminder" duration="long"><visual><binding template="ToastGeneric"><text>{title_esc}</text><text>{message_esc}</text></binding></visual><audio src="ms-winsoundevent:Notification.Default"/></toast>\''
        ")\n"
        f"$appId = '{app_id}'\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)\n"
        "Start-Sleep 2"
    )
    try:
        r = await run_command(
            ["powershell", "-Command", script], timeout=15, decode=smart_decode
        )
    except FileNotFoundError:
        return "找不到 powershell 可执行文件"
    except Exception as e:
        return f"调用 PowerShell 异常: {e}"
    if r.timed_out:
        return "PowerShell 超时(15 秒无响应)"
    if r.code != 0:
        return f"PowerShell 退出码 {r.code}: {r.stderr.strip()}"
    return ""


async def _notify_macos(title: str, message: str) -> str:
    """macOS 通知(osascript)。

    Returns:
        str: 成功返回空串;失败返回具体原因,由调用方转成 TOOL_ERROR。
    """
    try:
        r = await run_command(
            ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
            timeout=10,
            decode=smart_decode,
        )
    except FileNotFoundError:
        return "找不到 osascript 可执行文件"
    except Exception as e:
        return f"调用 osascript 异常: {e}"
    if r.timed_out:
        return "osascript 超时(10 秒无响应)"
    if r.code != 0:
        return f"osascript 退出码 {r.code}: {r.stderr.strip()}"
    return ""


async def _notify_linux(title: str, message: str) -> str:
    """Linux 通知(notify-send)。

    Returns:
        str: 成功返回空串;失败返回具体原因,由调用方转成 TOOL_ERROR。
    """
    try:
        r = await run_command(
            ["notify-send", "-u", "critical", title, message],
            timeout=10,
            decode=smart_decode,
        )
    except FileNotFoundError:
        return "找不到 notify-send,请先安装: sudo apt install libnotify-bin"
    except Exception as e:
        return f"调用 notify-send 异常: {e}"
    if r.timed_out:
        return "notify-send 超时(10 秒无响应)"
    if r.code != 0:
        return f"notify-send 退出码 {r.code}: {r.stderr.strip()}"
    return ""


@tool
async def push_notification(
    message: str,
    title: str = "UniClaw",
) -> str:
    """
    发送桌面通知。
    后台任务完成、监控匹配、子 Agent 完成时可调用此工具通知用户。

    Args:
        message: 通知内容
        title: 通知标题,默认 "UniClaw"

    Returns:
        str: 发送结果
    """
    if not message:
        return f"{TOOL_ERROR}: 通知内容不能为空"

    if sys.platform == "win32":
        reason = await _notify_windows(title, message)
    elif sys.platform == "darwin":
        reason = await _notify_macos(title, message)
    else:
        reason = await _notify_linux(title, message)

    if not reason:
        return f"已发送桌面通知: [{title}] {message}"
    # 必须回传真实原因 —— 三个平台都已实现通知,失败一律是环境或内容问题,
    # 报"平台不支持"会让 LLM 误判为无解而放弃重试
    return f"{TOOL_ERROR}: 桌面通知发送失败[{sys.platform}]: {reason}"


def get_tools() -> list:
    """获取通知工具列表"""
    return [push_notification]


def get_all_tools() -> list:
    """获取所有通知工具"""
    return get_tools()
