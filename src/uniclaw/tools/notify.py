import asyncio
import logging
import sys
from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR

logger = logging.getLogger(__name__)


async def _notify_windows(title: str, message: str) -> bool:
    """Windows Toast 通知"""
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
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-Command",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            print(f"[notify] PowerShell error: {stderr.decode()}")
            return False
        return True
    except Exception as e:
        print(f"[notify] exception: {e}")
        return False


async def _notify_macos(title: str, message: str) -> bool:
    """macOS 通知"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            f'display notification "{message}" with title "{title}"',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        return True
    except Exception as e:
        logger.debug("macOS 通知发送失败: %s", e)
        return False


async def _notify_linux(title: str, message: str) -> bool:
    """Linux 通知 (notify-send)"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "notify-send",
            "-u",
            "critical",
            title,
            message,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        return True
    except Exception as e:
        logger.debug("Linux 通知发送失败: %s", e)
        return False


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
        success = await _notify_windows(title, message)
    elif sys.platform == "darwin":
        success = await _notify_macos(title, message)
    else:
        success = await _notify_linux(title, message)

    if success:
        return f"已发送桌面通知: [{title}] {message}"
    else:
        return f"{TOOL_ERROR}: 当前平台不支持通知: {sys.platform}"


def get_tools() -> list:
    """获取通知工具列表"""
    return [push_notification]


def get_all_tools() -> list:
    """获取所有通知工具"""
    return get_tools()
