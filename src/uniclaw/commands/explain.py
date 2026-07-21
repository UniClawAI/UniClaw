"""工具解释模式切换命令。"""

from uniclaw.config import AppConfig, RunMode
from uniclaw.console.ui import ok


async def cmd_explain(args: str, config: AppConfig) -> bool:
    """切换工具解释模式。/explain [on|off|tool_name ...]"""
    args = args.strip()
    if args in ("on", "1", "true"):
        config.explain_mode = True
    elif args in ("off", "0", "false"):
        config.explain_mode = False
    elif args == "":
        if config.explain_mode is True:
            await ok("工具解释模式: 所有工具", config)
        elif config.explain_mode is False:
            await ok("工具解释模式: 关闭", config)
        elif isinstance(config.explain_mode, set):
            await ok(f"工具解释模式: {', '.join(sorted(config.explain_mode))}", config)
        return True
    else:
        # 指定工具名: /explain Bash Read Edit
        tool_names = [t.strip() for t in args.replace(",", " ").split() if t.strip()]
        if tool_names:
            config.explain_mode = set(tool_names)
            await ok(f"工具解释模式: {', '.join(tool_names)}", config)
        else:
            await ok("用法: /explain [on|off|tool_name ...]", config)
        return True

    mode = "开启" if config.explain_mode else "关闭"
    await ok(f"工具解释模式已{mode}", config)

    # WebUI 模式: 通知前端刷新配置
    if config.run_mode == RunMode.WEBUI:
        from uniclaw.webui.ws import _notify_config_changed

        session_id = config.current_agent.session.id
        await _notify_config_changed(session_id)
    return True
