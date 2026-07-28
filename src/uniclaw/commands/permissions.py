from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn, err

# 子命令列表
SUBCOMMANDS = ["list", "remove"]


async def cmd_permissions(args: str, config: AppConfig) -> bool:
    """权限规则管理命令

    用于查看和管理持久化权限规则,支持以下子命令:
    - list: 列出所有已保存的权限规则(默认命令)
    - remove <类型> <模式>: 删除指定的权限规则

    权限规则分为两种类型:
    - bash: 基于命令前缀匹配的 Bash 命令规则(如 "git commit")
    - tool: 基于工具名称精确匹配的工具规则(如 "Write")

    Args:
        args: 命令参数,格式为 "<子命令> [参数]"
        task: 当前代理任务对象
        config: 配置字典

    Returns:
        bool: 始终返回 True 表示命令执行完成
    """
    from uniclaw.tools.security import list_permission_rules, remove_permission_rule

    task = config.current_agent
    parts = args.strip().split()

    if not parts or parts[0] == "list":
        # 列出所有权限规则
        rules = list_permission_rules(task.session.root_dir)
        if not rules:
            await warn("暂无保存的权限规则", config)
            return True
        lines = [f"\n共 {len(rules)} 条权限规则:\n"]
        for i, r in enumerate(rules, 1):
            created = r.get("created", "")
            lines.append(f"  {i}. [{r['type']}] {r['pattern']}  (创建: {created})")
        lines.append(f"\n使用 /permissions remove <type> <pattern> 删除规则")
        await info("\n".join(lines), config)
        return True

    if parts[0] == "remove" and len(parts) >= 3:
        # 删除指定权限规则
        rule_type = parts[1]
        pattern = " ".join(parts[2:])
        if remove_permission_rule(rule_type, pattern, task.session.root_dir):
            await ok(f"已删除规则: [{rule_type}] {pattern}", config)
        else:
            await err(f"未找到规则: [{rule_type}] {pattern}", config)
        return True

    await warn(
        "用法: /permissions [list] 或 /permissions remove <type> <pattern>", config
    )
    return True
