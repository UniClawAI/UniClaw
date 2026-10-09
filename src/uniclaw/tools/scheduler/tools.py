import json
import sys

from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR


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
    if atype == "shell":
        if "command" not in data:
            return f"{TOOL_ERROR}: shell 类型缺少 'command' 字段"
        if not str(data["command"]).strip():
            return f"{TOOL_ERROR}: shell 类型的 'command' 不能为空"
    if atype == "agent" and "message" not in data:
        return f"{TOOL_ERROR}: agent 类型缺少 'message' 字段"
    if atype == "py" and "code" not in data:
        return f"{TOOL_ERROR}: py 类型缺少 'code' 字段"
    if atype == "monitor":
        if "command" not in data:
            return f"{TOOL_ERROR}: monitor 类型缺少 'command' 字段"
        if not str(data["command"]).strip():
            return f"{TOOL_ERROR}: monitor 类型的 'command' 不能为空"
        if "agent" not in data or not isinstance(data["agent"], dict):
            return f'{TOOL_ERROR}: monitor 类型缺少 "agent" 字段(对象)。示例: {{"agent": {{"message": "处理异常"}}}}'
        if "message" not in data["agent"]:
            return f"{TOOL_ERROR}: monitor.agent 缺少 'message' 字段"
    return None


def _win32_bash_syntax_warning(action_json: str) -> str:
    """Windows 下 shell/monitor 命令含 bash 扩展语法时给出透明提示。

    Windows 的 shell 任务经 cmd.exe 执行,`$(date +%H%M%S)`/反引号不会被展开,
    字面量原样进输出 —— 不提示用户会以为定时器坏了。

    Args:
        action_json: action 的 JSON 字符串。

    Returns:
        str: 警告文本;无需警告时返回空串。
    """
    if sys.platform != "win32":
        return ""
    try:
        data = json.loads(action_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict) or data.get("type") not in ("shell", "monitor"):
        return ""
    cmd = data.get("command", "")
    if not isinstance(cmd, str):
        return ""
    if "bash" in cmd.split(" ", 1)[0] or "bash -c" in cmd:
        return ""  # 显式走 bash 的命令会展开
    if "$(" in cmd or "`" in cmd or "${" in cmd:
        return (
            "\n⚠ 注意: 命令含 bash 扩展语法($()/反引号/`${}`),Windows 下定时任务"
            "经 cmd.exe 执行不会展开,输出中将出现字面量(如字面的 `$(date +%H%M%S)`)。"
            "如需展开,请用 bash 包裹执行,例如: bash -c 'echo $(date +%H%M%S)'"
        )
    return ""


@tool
def schedule_create(
    name: str,
    schedule: str,
    action: str,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    创建定时任务。
    如果只想修改任务内容(如 action、调度时间),使用 schedule_update,而不是删除任务重新创建。

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
                - py: 每次都执行 Python 代码
                  '{"type": "py", "code": "print(1+1)"}'
                - monitor: 先执行 shell 命令,退出码非零时才调用 agent
                  '{"type": "monitor", "command": "curl -sf http://localhost:8080/health", "agent": {"message": "服务挂了,排查原因"}}'

                选型规则:
                - 只需执行命令,不需要 AI 分析 → shell
                - 每次都需要 AI 处理 → agent
                - 先用命令检查,有问题才需要 AI 处理 → monitor

                执行环境: shell/monitor 命令在 Windows 上经 cmd.exe 执行
                (POSIX 上为 sh),不展开 bash 的 `$()`/反引号;需要 bash 语法
                时用 `bash -c '...'` 包裹。

    Returns:
        str: 创建结果消息,包含任务 ID
    """
    config = tool_runtime.config
    from .scheduler import Scheduler

    action = action.strip()
    err = _validate_action(action)
    if err:
        return err

    scheduler = Scheduler.get_instance()

    # 重复创建检查: name+schedule+action 三项全同视为同一任务 ——
    # 不拒绝的话每次调用都多一个 task_id,定时器会把同一件事执行 N 遍
    def _norm_action(text: str) -> str:
        try:
            return json.dumps(json.loads(text), sort_keys=True, ensure_ascii=False)
        except json.JSONDecodeError:
            return text

    for t in scheduler.list_tasks():
        if not t.get("enabled", True):
            continue
        if (
            t.get("name", "") == name
            and t.get("schedule", "").strip() == schedule.strip()
            and _norm_action(t.get("action", "")) == _norm_action(action)
        ):
            return (
                f"{TOOL_ERROR}: 完全相同的定时任务已存在"
                f"(task_id={t['id']}, {name}, {schedule})"
                f"——name/schedule/action 三项全同,再创建会重复执行。"
                f"如需修改请用 {schedule_update.name}(task_id='{t['id']}');"
                f"确要并存第二个同配置任务,请调整 name 或 action 以示区分"
            )

    try:
        task = scheduler.add_task(name, schedule, action, config=config)
    except ValueError as e:
        # _parse_cron 已给出字段数/格式/永不触发等原因,原样透出即可
        return f"{TOOL_ERROR}: {e}"
    except OSError as e:
        return (
            f"{TOOL_ERROR}: 保存定时任务配置失败(任务: {name}, cron: {schedule}): "
            f"{type(e).__name__}: {e}"
        )

    return (
        f"已创建定时任务: {task.id} ({name}, {schedule})"
        f"{_win32_bash_syntax_warning(action)}"
    )



@tool
def schedule_list() -> str:
    """
    列出所有定时任务。

    Returns:
        str: 格式化的任务列表,包含任务 ID、名称、调度、动作、状态、上次执行时间
    """
    from .scheduler import Scheduler

    scheduler = Scheduler.get_instance()
    tasks = scheduler.list_tasks()
    if not tasks:
        return "暂无定时任务。"

    lines = [f"共 {len(tasks)} 个定时任务:"]
    for t in tasks:
        tid = t["id"]
        name = t.get("name", tid)
        schedule = t.get("schedule", "")
        action = t.get("action", "")
        enabled = t.get("enabled", True)
        last_run = t.get("last_run") or "从未执行"

        status = "启用" if enabled else "禁用"
        lines.append(f"  [{status}] {tid}")
        lines.append(f"    名称: {name}")
        lines.append(f"    调度: {schedule}")
        lines.append(f"    动作: {action}")
        # last_run 是历史执行事实;配置更新时间更晚时注明"更新后未执行",
        # 而不是把真实执行记录清成"从未执行"
        updated = t.get("updated") or ""
        if t.get("last_run") and updated and t["last_run"] < updated:
            last_run = f"{t['last_run']}(配置更新于 {updated},更新后未执行)"
        lines.append(f"    上次执行: {last_run}")
    return "\n".join(lines)


@tool
def schedule_remove(task_id: str) -> str:
    """
    删除定时任务。
    如果只想修改任务内容(如 action、调度时间),使用 schedule_update,而不是删除重建。

    Args:
        task_id: 要删除的任务 ID

    Returns:
        str: 删除结果消息
    """
    from .scheduler import Scheduler

    scheduler = Scheduler.get_instance()
    try:
        removed = scheduler.remove_task(task_id)
    except OSError as e:
        return (
            f"{TOOL_ERROR}: 保存定时任务配置失败(任务: {task_id}): "
            f"{type(e).__name__}: {e}"
        )
    if removed:
        return f"已删除定时任务: {task_id}"
    return f"{TOOL_ERROR}: 任务 '{task_id}' 不存在"


@tool
def schedule_update(
    task_id: str,
    action: str = "",
    schedule: str = "",
) -> str:
    """
    修改定时任务的执行动作和/或调度时间。至少提供 action 或 schedule 之一。

    Args:
        task_id: 任务 ID
        action: 新的 action(可选),JSON 格式字符串,支持以下 type:
                - shell: '{"type": "shell", "command": "git status"}'
                - agent: '{"type": "agent", "message": "总结今天的代码变更"}'
                - py: '{"type": "py", "code": "print(1+1)"}'
                - monitor: 监控命令。执行 command,根据退出码决定是否触发 agent:
                  '{"type": "monitor", "command": "curl -sf http://localhost:8080/health", "agent": {"message": "服务挂了"}}'
        schedule: 新的 Cron 表达式(可选),如 "*/10 * * * *"

    Returns:
        str: 操作结果消息
    """
    from .scheduler import Scheduler

    if not action and not schedule:
        return f"{TOOL_ERROR}: 至少需要提供 action 或 schedule 之一"

    scheduler = Scheduler.get_instance()

    try:
        if action:
            action = action.strip()
            err = _validate_action(action)
            if err:
                return err
            if not scheduler.update_action(task_id, action):
                return f"{TOOL_ERROR}: 任务 '{task_id}' 不存在"

        if schedule:
            if not scheduler.update_schedule(task_id, schedule.strip()):
                return f"{TOOL_ERROR}: 任务 '{task_id}' 不存在"
    except ValueError as e:
        return f"{TOOL_ERROR}: {e}"
    except OSError as e:
        return (
            f"{TOOL_ERROR}: 保存定时任务配置失败(任务: {task_id}, cron: '{schedule}'): "
            f"{type(e).__name__}: {e}"
        )

    return f"已更新任务 {task_id}"



@tool
def schedule_toggle(
    task_id: str,
    enabled: bool,
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
    try:
        toggled = scheduler.toggle_task(task_id, enabled)
    except OSError as e:
        return (
            f"{TOOL_ERROR}: 保存定时任务配置失败(任务: {task_id}): "
            f"{type(e).__name__}: {e}"
        )
    if toggled:
        return f"已{action}定时任务: {task_id}"
    return f"{TOOL_ERROR}: 任务 '{task_id}' 不存在"


def get_tools() -> list:
    """获取调度器工具列表"""
    return [
        schedule_create,
        schedule_list,
        schedule_update,
        schedule_remove,
        schedule_toggle,
    ]


def get_all_tools() -> list:
    """获取所有调度器工具(无条件返回)"""
    return get_tools()
