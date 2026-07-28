import json

from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn, err

# 子命令列表
SUBCOMMANDS = ["list", "add", "remove", "enable", "disable"]


async def cmd_schedule(args: str, config: AppConfig) -> bool:
    """定时任务管理命令

    支持以下子命令:
    - list: 列出所有定时任务及其状态(默认命令)
    - add <调度> <动作> [名称]: 创建新的定时任务(ID 自动生成)
    - remove <id>: 删除指定的定时任务
    - enable <id>: 启用指定的定时任务
    - disable <id>: 禁用指定的定时任务

    调度格式(Cron 表达式):
    - 分 时 日 月 周(5 字段),如 "0 9 * * *" 每天 9:00
    - 最小粒度为 1 分钟,不支持秒级调度

    动作类型:
    - shell: <命令>: 执行 Shell 命令
    - agent: <消息>: 发送给 AI 处理
    - py: <Python代码>: 在当前 Python 环境执行代码

    Args:
        args: 命令参数,格式为 "<子命令> [参数]"
        task: 当前代理任务对象
        config: 配置字典

    Returns:
        bool: 始终返回 True 表示命令执行完成
    """
    from uniclaw.tools.scheduler.scheduler import Scheduler

    scheduler = Scheduler.get_instance()
    parts = args.strip().split(None, 1) if args else []
    subcmd = parts[0].lower() if parts else "list"
    subargs = parts[1] if len(parts) > 1 else ""

    if subcmd == "list" or not subcmd:
        await _schedule_list(scheduler, config=config)
    elif subcmd == "add":
        await _schedule_add(scheduler, subargs, config=config)
    elif subcmd == "remove":
        await _schedule_remove(scheduler, subargs, config=config)
    elif subcmd == "enable":
        await _schedule_toggle(scheduler, subargs, True, config=config)
    elif subcmd == "disable":
        await _schedule_toggle(scheduler, subargs, False, config=config)
    else:
        await err(f"未知子命令: {subcmd}", config)
        await info("可用命令: list, add, remove, enable, disable", config)
    return True


async def _schedule_list(scheduler, config: AppConfig) -> bool:
    """列出所有定时任务及其状态

    显示每个任务的 ID、名称、调度规则、动作、启用状态和上次执行时间。

    Args:
        scheduler: Scheduler 实例

    Returns:
        bool: 始终返回 True
    """
    tasks = scheduler.list_tasks()
    if not tasks:
        await warn("暂无定时任务", config)
        await info(
            "使用 /schedule add <id> <schedule> <action> 添加任务\n"
            '示例: /schedule add check-git "0 * * * *" "shell: git status"',
            config,
        )
        return True

    lines = [f"\n定时任务 (共 {len(tasks)} 个):\n"]
    for t in tasks:
        tid = t["id"]
        name = t.get("name", tid)
        schedule = t.get("schedule", "")
        action = t.get("action", "")
        enabled = t.get("enabled", True)
        last_run = t.get("last_run", "从未执行")
        root_dir = t.get("root_dir", "")
        permission_mode = t.get("permission_mode", "auto")

        # 格式化 action
        try:
            action_data = json.loads(action)
            action_type = action_data.get("type", "")
            if action_type == "shell":
                action_str = f"shell: {action_data.get('command', '')}"
            elif action_type == "agent":
                agent_type = action_data.get("agent_type", "general-purpose")
                action_str = f"agent[{agent_type}]: {action_data.get('message', '')}"
            elif action_type == "monitor":
                cmd = action_data.get("command", "")
                agent = action_data.get("agent", {})
                agent_msg = agent.get("message", "")
                action_str = f"monitor: {cmd} → agent: {agent_msg}"
            elif action_type == "py":
                action_str = f"py: {action_data.get('code', '')}"
            else:
                action_str = action
        except (json.JSONDecodeError, AttributeError):
            action_str = action

        status = "✓ 启用" if enabled else "✗ 禁用"
        lines.append(f"  [{status}] {tid}")
        lines.append(f"    名称: {name}")
        lines.append(f"    调度: {schedule}")
        lines.append(f"    动作: {action_str}")
        lines.append(f"    工作目录: {root_dir}")
        lines.append(f"    权限模式: {permission_mode}")
        lines.append(f"    上次执行: {last_run}")
        lines.append("")
    await info("\n".join(lines), config)
    return True


async def _schedule_add(scheduler, args_str: str, config: AppConfig) -> bool:
    """添加定时任务

    用法: /schedule add <schedule> <action> [name]
    示例: /schedule add "0 * * * *" "shell: git status"

    Args:
        scheduler: Scheduler 实例
        args_str: 参数字符串,包含调度规则、动作和可选名称

    Returns:
        bool: 始终返回 True
    """
    parts = _parse_quoted_args(args_str)
    if len(parts) < 2:
        await err("参数不足: /schedule add <schedule> <action> [name]", config)
        await info(
            '示例: /schedule add "0 * * * *" "shell: git status"\n'
            "调度格式: Cron 表达式(分 时 日 月 周),最小粒度 1 分钟\n"
            "动作类型: shell: <命令>、agent: <消息> 或 py: <Python代码>",
            config,
        )
        return True

    schedule, action = parts[0], parts[1]
    name = parts[2] if len(parts) > 2 else ""

    try:
        task_id = scheduler.add_task(name, schedule, action)
    except ValueError as e:
        await err(str(e), config)
        return True

    await ok(f"✓ 已添加定时任务: {task_id} ({schedule})", config)
    return True


async def _schedule_remove(scheduler, task_id: str, config: AppConfig) -> bool:
    """删除指定的定时任务

    Args:
        scheduler: Scheduler 实例
        task_id: 要删除的任务 ID

    Returns:
        bool: 始终返回 True
    """
    task_id = task_id.strip()
    if not task_id:
        await err("请指定任务 ID: /schedule remove <id>", config)
        return True

    if scheduler.remove_task(task_id):
        await ok(f"✓ 已删除定时任务: {task_id}", config)
    else:
        await err(f"任务 '{task_id}' 不存在", config)
    return True


async def _schedule_toggle(
    scheduler, task_id: str, enabled: bool, config: AppConfig
) -> bool:
    """启用或禁用指定的定时任务

    Args:
        scheduler: Scheduler 实例
        task_id: 任务 ID
        enabled: True 表示启用,False 表示禁用

    Returns:
        bool: 始终返回 True
    """
    task_id = task_id.strip()
    if not task_id:
        cmd = "enable" if enabled else "disable"
        await err(f"请指定任务 ID: /schedule {cmd} <id>", config)
        return True

    action = "启用" if enabled else "禁用"
    if scheduler.toggle_task(task_id, enabled):
        await ok(f"✓ 已{action}定时任务: {task_id}", config)
    else:
        await err(f"任务 '{task_id}' 不存在", config)
    return True


def _parse_quoted_args(s: str) -> list[str]:
    """解析带引号的参数,支持单引号和双引号

    能够正确处理包含空格的参数,例如:
    - 'arg with space'
    - "arg with space"

    Args:
        s: 待解析的参数字符串

    Returns:
        list[str]: 解析后的参数列表
    """
    args = []
    current = ""
    in_quote = None

    for ch in s:
        if in_quote:
            if ch == in_quote:
                in_quote = None
            else:
                current += ch
        elif ch in ('"', "'"):
            in_quote = ch
        elif ch == " ":
            if current:
                args.append(current)
                current = ""
        else:
            current += ch

    if current:
        args.append(current)
    return args
