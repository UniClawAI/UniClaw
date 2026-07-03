import json

from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR

VALID_PERMISSION_MODES = ("auto", "manual", "accept-all")


def _validate_permission_mode(mode: str) -> str | None:
    """校验权限模式。返回 None 表示合法,否则返回错误消息。"""
    if mode and mode not in VALID_PERMISSION_MODES:
        return f"{TOOL_ERROR}: 无效的权限模式: '{mode}',可选: {', '.join(VALID_PERMISSION_MODES)}"
    return None


def _validate_action(action: str) -> str | None:
    """校验 action JSON 格式。返回 None 表示合法,否则返回错误消息。"""
    action = action.strip()
    try:
        data = json.loads(action)
    except json.JSONDecodeError as e:
        return f'{TOOL_ERROR}: JSON 解析失败: {e}\n你传入的值: "{action}"'
    if "type" not in data:
        return f'{TOOL_ERROR}: JSON 缺少 "type" 字段。示例: {{"type": "shell", "command": "ls"}}'
    atype = data["type"]
    if atype not in ("shell", "agent", "py", "monitor"):
        return f"{TOOL_ERROR}: 未知的 type: '{atype}',可选: shell, agent, py, monitor"
    if atype == "shell" and "command" not in data:
        return f"{TOOL_ERROR}: shell 类型缺少 'command' 字段"
    if atype == "agent" and "message" not in data:
        return f"{TOOL_ERROR}: agent 类型缺少 'message' 字段"
    if atype == "py" and "code" not in data:
        return f"{TOOL_ERROR}: py 类型缺少 'code' 字段"
    if atype == "monitor":
        if "command" not in data:
            return f"{TOOL_ERROR}: monitor 类型缺少 'command' 字段"
        if "agent" not in data or not isinstance(data["agent"], dict):
            return f'{TOOL_ERROR}: monitor 类型缺少 "agent" 字段(对象)。示例: {{"agent": {{"message": "处理异常"}}}}'
        if "message" not in data["agent"]:
            return f"{TOOL_ERROR}: monitor.agent 缺少 'message' 字段"
    return None


@tool
def schedule_create(
    name: str,
    schedule: str,
    action: str,
    permission_mode: str = "auto",
    config=None,
) -> str:
    """
    创建定时任务。每个任务会分配独立的工作目录(返回结果中会显示)。
    如果需要创建任务使用的脚本,放在任务的工作目录里,不要放在当前项目目录中。
    如果只想修改任务内容(如 action、调度时间、权限模式),使用 schedule_update,而不是删除任务重新创建(重新创建会分配新的工作目录)。

    Args:
        name: 任务名称(人类可读),如 "检查 Git 状态"、"每日报告"
        schedule: Cron 表达式(分 时 日 月 周),如:
                  - "0 * * * *" 每小时
                  - "*/5 * * * *" 每 5 分钟
                  - "0 9 * * *" 每天 9:00
                  - "0 9 * * 1-5" 工作日 9:00
                  最小粒度为 1 分钟,不支持秒级调度
        action: JSON 格式字符串,支持以下 type:
                - shell: 每次都执行命令并输出结果
                  '{"type": "shell", "command": "git status"}'
                - agent: 每次都调用 agent 处理(消耗 token)
                  '{"type": "agent", "message": "总结今天的代码变更"}'
                  指定类型: '{"type": "agent", "agent_type": "coder", "message": "重构 utils.py"}'
                - py: 每次都执行 Python 代码
                  '{"type": "py", "code": "print(1+1)"}'
                - monitor: 先执行 shell 命令,退出码非零时才调用 agent
                  '{"type": "monitor", "command": "curl -sf http://localhost:8080/health", "agent": {"message": "服务挂了,排查原因"}}'
                  '{"type": "monitor", "command": "python check_email.py", "agent": {"agent_type": "recon", "message": "有新邮件,读取并总结"}}'

                选型规则:
                - 只需执行命令,不需要 AI 分析 → shell
                - 每次都需要 AI 处理 → agent
                - 先用命令检查,有问题才需要 AI 处理 → monitor

                agent 可用类型(使用 list_agent_definitions 查看完整列表):
                · general-purpose — 通用代理(默认)
                · coder — 编程代理
                · reviewer — 代码审查
                · researcher — 研究代理(只读)
                · tester — 测试专家
                · recon — 侦察代理(只读)
                · project-init — 项目初始化
        permission_mode: agent 运行时的权限模式,默认 auto。可选:
                         - auto — AI 自动判断是否需要权限确认(默认)
                         - manual — 每次都需要用户确认
                         - accept-all — 自动批准所有操作

    Returns:
        str: 创建结果消息,包含任务 ID
    """
    from .scheduler import Scheduler

    action = action.strip()
    err = _validate_action(action)
    if err:
        return err
    err = _validate_permission_mode(permission_mode)
    if err:
        return err

    scheduler = Scheduler.get_instance()
    try:
        task = scheduler.add_task(
            name, schedule, action, permission_mode=permission_mode, config=config
        )
    except ValueError as e:
        return f"{TOOL_ERROR}: {e}"

    rd = task.root_dir or "未设置"
    return (
        f"已创建定时任务: {task.id} ({name}, {schedule})\n"
        f"工作目录: {rd}\n"
        f"如果需要创建任务使用的脚本,放在任务的工作目录里,不要放在当前项目目录中。"
    )


@tool
def schedule_monitor(
    name: str,
    schedule: str,
    command: str,
    agent_message: str,
    agent_type: str = "general-purpose",
    permission_mode: str = "auto",
    config=None,
) -> str:
    """
    创建定时监控任务:周期执行 shell 命令,退出码非零时调用 agent 处理。
    适用于健康检查、异常检测等"没问题就不处理"的场景。
    每个任务会分配独立的工作目录(返回结果中会显示),如有检查脚本需要创建,放在任务的工作目录里。
    如果只想修改监控任务内容(如检查命令、agent 提示词、调度时间),使用 schedule_monitor_update,而不是删除任务重新创建(重新创建会分配新的工作目录)。

    工作流程:
    1. 按 schedule 周期执行 command
    2. command 退出码 = 0 → 正常,不调用 agent,仅输出 stdout
    3. command 退出码 ≠ 0 → 调用 agent,将 stdout/stderr 作为上下文交给 agent 处理

    Args:
        name: 任务名称(人类可读),如 "监控服务健康"、"检查邮箱"、"检测磁盘空间"
        schedule: Cron 表达式(分 时 日 月 周),如:
                  - "*/5 * * * *" 每 5 分钟
                  - "0 * * * *" 每小时
                  - "0 9 * * *" 每天 9:00
                  - "0 9 * * 1-5" 工作日 9:00
                  最小粒度为 1 分钟
        command: 要执行的 shell 命令,退出码决定是否触发 agent:
                 - 退出码 = 0 → 正常,不触发 agent
                 - 退出码 ≠ 0 → 触发 agent 处理
                 示例: "curl -sf http://localhost:8080/health"、"python check.py"、"test -f /tmp/alert.flag"
                 如需编写检查脚本,放在任务的工作目录中
        agent_message: 触发 agent 时的提示词,描述需要 agent 做什么。command 的 stdout/stderr 会自动附在提示词后面作为上下文。
                       示例: "服务挂了,排查原因并修复"、"有新邮件,读取并总结"
        agent_type: agent 类型,默认 general-purpose。可选:
                    - general-purpose — 通用代理(默认),研究复杂问题、多步骤任务
                    - coder — 编程代理,专注编写/阅读/修改代码
                    - reviewer — 代码审查,分析正确性、安全漏洞、性能问题
                    - researcher — 研究代理,探索代码库、回答问题,只读不写
                    - tester — 测试专家,编写和运行测试
                    - recon — 侦察代理,读取文件/网页/媒体,提取关键信息返回精简摘要
        permission_mode: agent 运行时的权限模式,默认 auto。可选:
                         - auto — AI 自动判断是否需要权限确认(默认)
                         - manual — 每次都需要用户确认
                         - accept-all — 自动批准所有操作

    Returns:
        str: 创建结果消息,包含任务 ID、检查命令、触发条件和工作目录
    """
    from .scheduler import Scheduler

    err = _validate_permission_mode(permission_mode)
    if err:
        return err

    action = json.dumps(
        {
            "type": "monitor",
            "command": command,
            "agent": {"agent_type": agent_type, "message": agent_message},
        },
        ensure_ascii=False,
    )

    scheduler = Scheduler.get_instance()
    try:
        task = scheduler.add_task(
            name, schedule, action, permission_mode=permission_mode, config=config
        )
    except ValueError as e:
        return f"{TOOL_ERROR}: {e}"

    rd = task.root_dir or "未设置"
    return (
        f"已创建监控任务: {task.id} ({name}, {schedule})\n"
        f"检查命令: {command}\n"
        f"触发条件: 退出码非零\n"
        f"工作目录: {rd}\n"
        f"如果需要创建检查脚本,放在任务的工作目录里。"
    )


@tool
def schedule_list(config=None) -> str:
    """
    列出所有定时任务。

    Returns:
        str: 格式化的任务列表,包含任务 ID、名称、调度、动作、状态、上次执行时间
    """
    from .scheduler import Scheduler

    scheduler = Scheduler.get_instance()
    tasks = scheduler.list_tasks(config)
    if not tasks:
        return "暂无定时任务。"

    lines = [f"共 {len(tasks)} 个定时任务:"]
    for t in tasks:
        tid = t["id"]
        name = t.get("name", tid)
        schedule = t.get("schedule", "")
        action = t.get("action", "")
        enabled = t.get("enabled", True)
        last_run = t.get("last_run", "从未执行")

        root_dir = t.get("root_dir", "")
        permission_mode = t.get("permission_mode", "auto")
        status = "启用" if enabled else "禁用"
        lines.append(f"  [{status}] {tid}")
        lines.append(f"    名称: {name}")
        lines.append(f"    调度: {schedule}")
        lines.append(f"    动作: {action}")
        lines.append(f"    工作目录: {root_dir}")
        lines.append(f"    权限模式: {permission_mode}")
        lines.append(f"    上次执行: {last_run}")
    return "\n".join(lines)


@tool
def schedule_remove(task_id: str, config=None) -> str:
    """
    删除定时任务。任务的工作目录也会被删除。
    如果只想修改任务内容(如 action、调度时间),使用 schedule_update 或 schedule_monitor_update,而不是删除重建。

    Args:
        task_id: 要删除的任务 ID

    Returns:
        str: 删除结果消息
    """
    from .scheduler import Scheduler

    scheduler = Scheduler.get_instance()
    if scheduler.remove_task(task_id, config):
        return f"已删除定时任务: {task_id}"
    return f"{TOOL_ERROR}: 任务 '{task_id}' 不存在"


@tool
def schedule_update(
    task_id: str,
    action: str = "",
    schedule: str = "",
    permission_mode: str = "",
    config=None,
) -> str:
    """
    修改定时任务的执行动作和/或调度时间。至少提供 action 或 schedule 之一。

    Args:
        task_id: 任务 ID
        action: 新的 action(可选),JSON 格式字符串,支持以下 type:
                - shell: '{"type": "shell", "command": "git status"}'
                - agent: '{"type": "agent", "message": "总结今天的代码变更"}'
                  指定类型: '{"type": "agent", "agent_type": "coder", "message": "重构 utils.py"}'
                - py: '{"type": "py", "code": "print(1+1)"}'
                - monitor: 监控命令。执行 command,根据退出码决定是否触发 agent:
                  退出码 = 0 → 正常,不执行 agent,仅输出 command 的 stdout
                  退出码 ≠ 0 → 触发 agent,将 command 的 stdout/stderr 作为上下文交给 agent 处理
                  '{"type": "monitor", "command": "curl -sf http://localhost:8080/health", "agent": {"message": "服务挂了"}}'
        schedule: 新的 Cron 表达式(可选),如 "*/10 * * * *"
        permission_mode: 新的权限模式(可选),如 auto, manual, accept-all

    Returns:
        str: 操作结果消息
    """
    from .scheduler import Scheduler

    if not action and not schedule and not permission_mode:
        return f"{TOOL_ERROR}: 至少需要提供 action、schedule 或 permission_mode 之一"

    if permission_mode:
        err = _validate_permission_mode(permission_mode)
        if err:
            return err

    scheduler = Scheduler.get_instance()

    if action:
        action = action.strip()
        err = _validate_action(action)
        if err:
            return err
        if not scheduler.update_action(task_id, action, config):
            return f"{TOOL_ERROR}: 任务 '{task_id}' 不存在"

    if schedule:
        if not scheduler.update_schedule(task_id, schedule.strip(), config):
            return f"{TOOL_ERROR}: 任务 '{task_id}' 不存在"

    if permission_mode:
        if not scheduler.update_permission_mode(task_id, permission_mode, config):
            return f"{TOOL_ERROR}: 任务 '{task_id}' 不存在"

    return f"已更新任务 {task_id}"


@tool
def schedule_monitor_update(
    task_id: str,
    command: str = "",
    agent_message: str = "",
    agent_type: str = "",
    schedule: str = "",
    permission_mode: str = "",
    config=None,
) -> str:
    """
    修改监控任务的检查命令、agent 提示词和/或调度时间。至少提供一个修改项。

    Args:
        task_id: 任务 ID
        command: 新的检查命令(可选)。退出码 = 0 → 正常,非零 → 触发 agent
        agent_message: 新的 agent 提示词(可选),描述触发时需要 agent 做什么
        agent_type: 新的 agent 类型(可选),如 coder, reviewer, researcher, tester, recon
        schedule: 新的 Cron 表达式(可选),如 "*/10 * * * *"
        permission_mode: 新的权限模式(可选),如 auto, manual, accept-all

    Returns:
        str: 操作结果消息
    """
    from .scheduler import Scheduler

    if (
        not command
        and not agent_message
        and not agent_type
        and not schedule
        and not permission_mode
    ):
        return f"{TOOL_ERROR}: 至少需要提供 command、agent_message、agent_type、schedule 或 permission_mode 之一"

    if permission_mode:
        err = _validate_permission_mode(permission_mode)
        if err:
            return err

    scheduler = Scheduler.get_instance()
    task_data = scheduler.get_task(task_id, config)
    if not task_data:
        return f"{TOOL_ERROR}: 任务 '{task_id}' 不存在"

    import json

    action_data = json.loads(task_data.action)
    agent_data = action_data.get("agent", {})

    if command:
        action_data["command"] = command
    if agent_message:
        agent_data["message"] = agent_message
    if agent_type:
        agent_data["agent_type"] = agent_type
    if agent_data:
        action_data["agent"] = agent_data

    new_action = json.dumps(action_data, ensure_ascii=False)
    scheduler.update_action(task_id, new_action, config)

    if schedule:
        scheduler.update_schedule(task_id, schedule.strip(), config)

    if permission_mode:
        scheduler.update_permission_mode(task_id, permission_mode, config)

    return f"已更新监控任务 {task_id}"


@tool
def schedule_toggle(
    task_id: str,
    enabled: bool,
    config=None,
) -> str:
    """
    启用或禁用定时任务。

    Args:
        task_id: 任务 ID
        enabled: True 启用,False 禁用

    Returns:
        str: 操作结果消息
    """
    from .scheduler import Scheduler

    scheduler = Scheduler.get_instance()
    action = "启用" if enabled else "禁用"
    if scheduler.toggle_task(task_id, enabled, config):
        return f"已{action}定时任务: {task_id}"
    return f"{TOOL_ERROR}: 任务 '{task_id}' 不存在"


def get_tools() -> list:
    """获取调度器工具列表"""
    return [
        schedule_create,
        schedule_monitor,
        schedule_list,
        schedule_update,
        schedule_monitor_update,
        schedule_remove,
        schedule_toggle,
    ]


def get_all_tools() -> list:
    """获取所有调度器工具(无条件返回)"""
    return get_tools()
