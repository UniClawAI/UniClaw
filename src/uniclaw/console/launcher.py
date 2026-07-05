"""
UniClaw 控制台启动器模块

提供应用启动相关功能,包括 Logo 显示、欢迎信息展示和 REPL 循环启动。
"""

import os
from PIL import Image
from uniclaw.config import load_config, AppConfig
from uniclaw.console.run import repl_run


def image_to_ascii(image_path: str, width: int = 80) -> str:
    """
    将图片转换为 ASCII 艺术字

    参数:
        image_path: 图片文件路径
        width: ASCII 艺术的宽度(字符数)

    返回:
        ASCII 艺术字符串
    """
    img = Image.open(image_path)
    gray_img = img.convert("L")
    aspect_ratio = gray_img.height / gray_img.width
    height = int(width * aspect_ratio * 0.5)
    gray_img = gray_img.resize((width, height))
    chars = r"$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\|()1{}[]?-_+~<>i!lI;:,\"^`'.  "
    pixels = list(gray_img.get_flattened_data())
    min_pixel = min(pixels)
    max_pixel = max(pixels)
    pixel_range = max_pixel - min_pixel
    if pixel_range == 0:
        pixel_range = 1
    ascii_art = ""
    for i, pixel in enumerate(pixels):
        normalized_value = (pixel - min_pixel) / pixel_range
        char_index = int(normalized_value * (len(chars) - 1))
        ascii_art += chars[char_index]
        if (i + 1) % width == 0:
            ascii_art += "\n"
    lines = ascii_art.split("\n")
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[len(lines) - 1].strip() == "":
        lines.pop()
    ascii_art = "\n".join(lines) + "\n"
    return ascii_art


def get_logo() -> str:
    """返回 UniClaw 的 ASCII Logo 字符串。"""
    try:
        logo_path = os.path.join(os.path.dirname(__file__), "..", "assets/logo.png")
        return image_to_ascii(logo_path, width=60)
    except Exception as e:
        return f"加载 Logo 失败: {e}\n"


def get_welcome(config: AppConfig) -> str:
    """返回欢迎信息和当前配置摘要字符串。"""
    lines = [
        "UniClaw",
        "=" * 60,
        "🦞 欢迎使用 (UniClaw)",
        "=" * 60,
        f"🤖 模型名称: {config.model_name[0] if config.model_name else '(未设置)'}",
        f"⚙️  权限模式: {config.permission_mode}",
        f"📂 当前目录: {config.root_dir}",
        "=" * 60,
    ]
    return "\n".join(lines)


async def launch():
    """
    启动 UniClaw 应用

    完整的启动流程:
    1. 显示 ASCII Logo
    2. 加载配置
    3. 显示欢迎信息
    4. 启动 REPL 交互循环
    """
    from pathlib import Path
    from uniclaw.config import Permissions
    from uniclaw.console.ui import TUISpinner

    config = load_config(root_dir=Path.cwd(), spinner=TUISpinner())
    config.permission_mode = Permissions.AUTO

    initial_output = [
        get_logo(),
        get_welcome(config),
    ]

    from uniclaw.tools.scheduler.scheduler import Scheduler

    await Scheduler.get_instance().start()

    # 注册 Computer Use 紧急停止热键 (Ctrl+U)
    from uniclaw.tools.computer_use import register_emergency_hotkey

    register_emergency_hotkey()

    await repl_run(config, initial_output=initial_output)
