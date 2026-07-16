"""微信消息工具 — 通过微信发送文字、图片、文件,以及查询可用账号。"""

from pathlib import Path

from uniclaw.config import AppConfig
from uniclaw.ilink_bot.exceptions import NoContextError
from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR


def _resolve_bot(config: AppConfig, bot_name: str = ""):
    """解析目标 bot 实例。

    优先级:
    1. 指定 bot_name → BotManager 中按名称查找
    2. config.wechat_ctx → 当前对话的 bot
    3. BotManager 中第一个已登录的 bot
    """
    from uniclaw.ilink_bot.manager import BotManager

    if bot_name:
        bot = BotManager().get(bot_name)
        if not bot:
            return None, f"{TOOL_ERROR}: 未找到名为 '{bot_name}' 的微信机器人"
        if not bot.is_logged_in:
            return None, f"{TOOL_ERROR}: 微信机器人 '{bot_name}' 未登录"
        return bot, None

    # 优先使用当前会话的 bot
    bot = getattr(config, "wechat_ctx", None)
    if bot and bot.is_logged_in:
        return bot, None

    # 回退到 BotManager 中第一个已登录的 bot
    manager = BotManager()
    for b in manager.bots:
        if b.is_logged_in:
            return b, None

    return None, f"{TOOL_ERROR}: 没有可用的已登录微信机器人"


def _resolve_path(file_path: str, config: AppConfig) -> tuple[Path | None, str]:
    """解析文件路径,返回 (绝对路径, 错误信息)。"""
    p = Path(file_path)
    if not p.is_absolute():
        root_dir = config.root_dir
        if root_dir is None:
            return None, f"{TOOL_ERROR}: 当前会话无工作目录,请使用绝对路径"
        p = root_dir / p
    if not p.exists():
        return None, f"{TOOL_ERROR}: 文件不存在: {file_path}"
    return p, ""


@tool
def wechat_list_contacts(config: AppConfig = None) -> str:
    """列出当前已登录的微信机器人名称。可用于确认哪些微信号可以收发消息。

    Returns:
        已登录机器人名称的换行分隔列表；若无机器人或未登录则返回提示信息。
    """
    from uniclaw.ilink_bot.manager import BotManager

    if config is None:
        return f"{TOOL_ERROR}: 无法获取配置"

    manager = BotManager()
    if not manager.bots:
        return "当前没有注册任何微信机器人。请先通过 /wechat 命令添加并登录。"

    active_bots = [b for b in manager.bots if b.is_logged_in]
    if not active_bots:
        return "当前没有已登录的微信机器人。请先通过 /wechat 命令添加并登录。"

    bot_names = "\n".join(f"- {b.name}" for b in active_bots)
    return f"已登录的微信机器人:\n{bot_names}"


@tool
async def wechat_send_text(
    text: str,
    bot_name: str = "",
    config: AppConfig = None,
) -> str:
    """通过微信向当前对话用户发送文字消息。

    Args:
        text: 要发送的文字内容
        bot_name: 指定使用的机器人名称,可通过 wechat_list_contacts 获取

    Returns:
        str: 发送结果消息
    """
    if config is None:
        return f"{TOOL_ERROR}: 无法获取配置"

    bot, err = _resolve_bot(config, bot_name)
    if err:
        return err

    try:
        bot.reply_text(text)
        return "文字消息已发送"
    except NoContextError:
        return f"{TOOL_ERROR}: 当前没有对话用户,请先让用户给机器人发一条消息"
    except Exception as e:
        return f"发送失败: {e}"


@tool
async def wechat_send_image(
    image_path: str,
    caption: str = "",
    bot_name: str = "",
    config: AppConfig = None,
) -> str:
    """通过微信向当前对话用户发送图片。

    Args:
        image_path: 图片文件的绝对路径或相对于工作目录的路径
        caption: 图片附带的文字说明(可选)
        bot_name: 指定使用的机器人名称,可通过 wechat_list_contacts 获取

    Returns:
        str: 发送结果消息
    """
    if config is None:
        return f"{TOOL_ERROR}: 无法获取配置"

    p, err = _resolve_path(image_path, config)
    if err:
        return err

    bot, err = _resolve_bot(config, bot_name)
    if err:
        return err

    try:
        bot.reply_image(p, caption=caption or None)
        return f"图片已发送: {p.name}"
    except NoContextError:
        return f"{TOOL_ERROR}: 当前没有对话用户,请先让用户给机器人发一条消息"
    except Exception as e:
        return f"图片发送失败: {e}"


@tool
async def wechat_send_file(
    file_path: str,
    file_name: str = "",
    bot_name: str = "",
    config: AppConfig = None,
) -> str:
    """通过微信向当前对话用户发送文件。

    Args:
        file_path: 文件的绝对路径或相对于工作目录的路径
        file_name: 自定义文件名(可选,默认使用原始文件名)
        bot_name: 指定使用的机器人名称,可通过 wechat_list_contacts 获取

    Returns:
        str: 发送结果消息
    """
    if config is None:
        return f"{TOOL_ERROR}: 无法获取配置"

    p, err = _resolve_path(file_path, config)
    if err:
        return err
    if p.is_dir():
        return f"{TOOL_ERROR}: 路径是一个目录,不是文件: {file_path}"

    bot, err = _resolve_bot(config, bot_name)
    if err:
        return err

    try:
        bot.reply_file(p, file_name=file_name or None)
        return f"文件已发送: {file_name or p.name}"
    except NoContextError:
        return f"{TOOL_ERROR}: 当前没有对话用户,请先让用户给机器人发一条消息"
    except Exception as e:
        return f"文件发送失败: {e}"


def get_tools() -> list:
    return [wechat_list_contacts, wechat_send_text, wechat_send_image, wechat_send_file]


def get_all_tools() -> list:
    return get_tools()
