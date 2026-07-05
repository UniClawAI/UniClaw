"""Computer Use 模式切换命令。"""

from uniclaw.config import AppConfig
from uniclaw.console.ui import ok


async def cmd_cu(args: str, config: AppConfig) -> bool:
    """切换 Computer Use 模式。/cu [on|off]"""
    args = args.strip().lower()
    if args in ("on", "1", "true"):
        config.computer_use_enabled = True
    elif args in ("off", "0", "false"):
        config.computer_use_enabled = False
    elif args == "":
        mode = "开启" if config.computer_use_enabled else "关闭"
        await ok(f"Computer Use: {mode}", config)
        return True
    else:
        await ok("用法: /cu [on|off]", config)
        return True

    mode = "开启" if config.computer_use_enabled else "关闭"
    await ok(f"Computer Use 模式已{mode}", config)
    return True
