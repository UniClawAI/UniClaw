"""帮助工具 — 让 AI 能查询斜杠命令的用法。"""

from uniclaw.tools.base import tool


@tool
async def list_slash_commands() -> str:
    """列出所有可用的斜杠命令及其简要说明。
    返回命令名称、别名和一句话描述,帮助用户了解有哪些命令可用。
    """
    from uniclaw.commands import COMMANDS, COMMAND_SUBCOMMANDS

    # 去重:别名指向同一个 handler 时只保留主命令
    seen_handlers: dict[int, str] = {}  # handler id → 主命令名
    primary_commands: dict[str, callable] = {}
    aliases: dict[str, list[str]] = {}  # 主命令 → [别名列表]

    for name, handler in COMMANDS.items():
        hid = id(handler)
        if hid in seen_handlers:
            primary = seen_handlers[hid]
            aliases.setdefault(primary, []).append(name)
        else:
            seen_handlers[hid] = name
            primary_commands[name] = handler

    lines = ["可用的斜杠命令:\n"]
    for name, handler in primary_commands.items():
        doc = handler.__doc__ or ""
        brief = doc.strip().split("\n")[0] if doc else "无描述"
        alias_list = aliases.get(name, [])
        alias_str = f"  (别名: {', '.join('/' + a for a in alias_list)})" if alias_list else ""
        lines.append(f"/{name}{alias_str} — {brief}")

        # 显示子命令(如果有)
        subcmds = COMMAND_SUBCOMMANDS.get(name)
        if subcmds:
            lines.append(f"  子命令: {', '.join(subcmds)}")

    lines.append(f"\n共 {len(primary_commands)} 个命令(含 {sum(len(v) for v in aliases.values())} 个别名)。")
    lines.append(f"使用 {get_command_help.name} 传入命令名可查看详细用法。")
    return "\n".join(lines)


@tool
async def get_command_help(command_name: str) -> str:
    """获取指定斜杠命令的详细帮助信息。
    传入命令名称(不含 /),返回该命令的完整说明文档,包括参数、用法示例等。

    Args:
        command_name: 命令名称(不含 /),如 "memory"、"checkpoint"、"mcp"
    """
    from uniclaw.commands import COMMANDS, COMMAND_SUBCOMMANDS

    name = command_name.strip().lower().lstrip("/")

    handler = COMMANDS.get(name)
    if not handler:
        # 尝试模糊匹配
        candidates = [cmd for cmd in COMMANDS if name in cmd]
        if candidates:
            return f"未找到命令 '{name}',你是否想输入: {', '.join('/' + c for c in candidates[:5])}"
        return f"未找到命令 '{name}'。使用 {list_slash_commands.name} 查看所有可用命令。"

    doc = handler.__doc__ or "该命令没有帮助文档。"

    # 找到主命令名(处理别名)
    primary_name = name
    for cmd, h in COMMANDS.items():
        if h is handler and cmd != name:
            # 检查哪个是主命令(有子命令的那个)
            if cmd in COMMAND_SUBCOMMANDS:
                primary_name = cmd
                break

    # 构建结果
    lines = [f"/{name}"]
    if primary_name != name:
        lines[0] += f" (别名,主命令: /{primary_name})"
    lines.append("")
    lines.append(doc.strip())

    # 附加子命令信息
    subcmds = COMMAND_SUBCOMMANDS.get(primary_name)
    if subcmds:
        lines.append(f"\n可用子命令: {', '.join(subcmds)}")
        lines.append(f"使用方式: /{name} <子命令> [参数]")

    return "\n".join(lines)


def get_tools() -> list:
    """获取帮助工具列表"""
    return [list_slash_commands, get_command_help]


def get_all_tools() -> list:
    """获取所有帮助工具"""
    return get_tools()
