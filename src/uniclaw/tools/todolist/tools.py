from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.config import AppConfig

from .todolist import TodoList, TodoStatus

# ── 普通模式工具 ──────────────────────────────────────────────


@tool
async def todolist_create(
    items: list[str], reason: str = "", tool_runtime: ToolRuntime = None
) -> str:
    """
    创建一个新的任务清单(todolist),替换现有内容。如果已有清单则覆盖。
    用于将复杂任务分解为尽可能多的细粒度步骤进行跟踪。
    每个步骤应该是具体、独立、可验证的小任务,不应超过 20 字描述。
    第一个步骤自动标记为正在进行。
    监工模式下需提供 reason 说明原清单问题和新清单改进。

    Args:
        items: 任务步骤列表,每个元素是一个步骤的描述。优先分解为更多细粒度步骤,避免步骤过于宽泛。
        reason: 监工模式下必填,说明重建理由(原清单问题 + 新清单改进)。非监工模式可留空。
    """
    config = tool_runtime.config
    todo = config.current_agent.todolist
    if todo.overseer.active:
        return await _overseer_create(items, reason, config)
    todo.clear()
    for content in items:
        todo.add(content)
    if todo.items:
        todo.items[0].status = TodoStatus.IN_PROGRESS
    return f"已创建任务清单,共 {len(todo.items)} 个步骤:\n{todo.get_list()}"


@tool
async def todolist_update(
    step: int, status: str, reason: str = "", tool_runtime: ToolRuntime = None
) -> str:
    """
    更新任务清单中指定步骤的状态。
    同一时间只能有一个步骤处于 in_progress 状态,设置新的 in_progress 时,原有的 in_progress 会自动变为 pending。
    监工模式下需提供 reason 说明完成内容。

    Args:
        step: 步骤的索引(从 0 开始)
        status: 新状态,可选值为 "pending"(未完成)、"in_progress"(正在进行)、"completed"(已完成)
        reason: 监工模式下必填,完成说明(做了什么、改了哪些文件)。非监工模式可留空。
    """
    config = tool_runtime.config
    try:
        todo_status = TodoStatus(status)
    except ValueError:
        return f"{TOOL_ERROR}: 无效状态 '{status}',可选值为 {', '.join(TodoStatus)}"
    todo = config.current_agent.todolist
    if todo.overseer.active:
        return await _overseer_update(step, todo_status, reason, config)
    if todo.is_empty():
        return f"{TOOL_ERROR}: 当前没有任务清单,请先使用 {todolist_create.name} 创建"
    result = todo.update_status(step, todo_status)
    if not result.startswith(TOOL_ERROR):
        result += _all_completed_hint(todo)
    return result


@tool
def todolist_clear(tool_runtime: ToolRuntime = None) -> str:
    """清空当前任务清单。当所有步骤完成后调用此工具。"""
    config = tool_runtime.config
    todo = config.current_agent.todolist
    count = len(todo.items)
    todo.clear()
    return f"已清空任务清单(共 {count} 个步骤)"


@tool
def todolist_cancel(tool_runtime: ToolRuntime = None) -> str:
    """
    取消当前任务清单。用户明确要求暂停或取消时调用,允许 agent 退出会话。
    设置取消事件,使 agent 可以正常退出。
    """
    config = tool_runtime.config
    config.current_agent.cancel_event.set()
    return "任务暂停,等待用户下一步指示..."


@tool
def todolist_list(tool_runtime: ToolRuntime = None) -> str:
    """列出当前任务清单的所有步骤及状态。"""
    config = tool_runtime.config
    todo = config.current_agent.todolist
    if todo.is_empty():
        return "当前没有任务清单"
    return todo.get_list()


# ── 内部辅助 ────────────────────────────────────────────────


def _all_completed_hint(todo: TodoList) -> str:
    """所有步骤均为 completed 时返回详细提示引导调用 todolist_clear,否则返回空串"""
    if any(item.status != TodoStatus.COMPLETED for item in todo.items):
        return ""
    return (
        f"\n\n🎉 全部 {len(todo.items)} 个步骤均已完成,整个任务已结束!\n"
        f"请立即调用 {todolist_clear.name} 工具清空任务清单,"
        f"然后向用户汇报整体完成情况。"
    )


# ── 监工模式内部函数(由 todolist_create/todolist_update 分派)──


async def _overseer_create(items: list[str], reason: str, config: AppConfig) -> str:
    """
    监工模式:创建/重建清单,需经审核。
    """
    from .overseer import verify_modification

    todo = config.current_agent.todolist
    if len(todo.items) > 0:
        old_items = [f"{item.content} ({item.status})" for item in todo.items]
        passed, fail_reason = await verify_modification(
            "重建清单", old_items, items, reason, config
        )
        if not passed:
            return (
                f"❌ 审核未通过,清单未重建:\n"
                f"你的理由: {reason}\n"
                f"不通过原因: {fail_reason}\n"
                f"请修正后重试。"
            )

    todo.clear()
    for content in items:
        todo.add(content)
    if todo.items:
        todo.items[0].status = TodoStatus.IN_PROGRESS
    return f"✅ 已重建清单(共 {len(todo.items)} 个步骤):\n{todo.get_list()}"


async def _overseer_update(
    step: int, status: TodoStatus, reason: str, config: AppConfig
) -> str:
    """
    监工模式:更新步骤状态,完成时需经审核。
    调用方已将 status 转为 TodoStatus,此处直接使用。
    """
    from .overseer import verify_completion

    todo = config.current_agent.todolist
    if todo.is_empty():
        return f"{TOOL_ERROR}: 当前没有任务清单,请先使用 {todolist_create.name} 创建"

    if status == TodoStatus.COMPLETED:
        item = todo.items[step]
        old_status = item.status
        passed, fail_reason = await verify_completion(
            f"{item.content}\n\n完成说明: {reason}", config
        )
        if not passed:
            item.status = old_status
            return (
                f"❌ 审核未通过,步骤 {step} 未标记为完成:\n"
                f"任务: {item.content}\n"
                f"你的说明: {reason}\n"
                f"不通过原因: {fail_reason}\n"
                f"请修正后重试。"
            )

    result = todo.update_status(step, status)
    if not result.startswith(TOOL_ERROR):
        result += _all_completed_hint(todo)
    if status == TodoStatus.COMPLETED:
        return f"✅ 审核通过,步骤 {step} 已标记为完成:\n{result}"
    return f"已更新步骤 {step} 状态为 {status}:\n{result}"


# ── 系统提示 ────────────────────────────────────────────────


def get_list_system_prompt(todolist: TodoList) -> str:
    """返回 todolist 工具说明用于注入 system_prompt,不包含动态状态以避免破坏缓存"""
    if todolist is None:
        return ""

    lines = [
        "# TodoList",
        f"- **{todolist_create.name}**:创建任务清单,将复杂任务分解为多个步骤进行跟踪",
        f"- **{todolist_update.name}**:更新指定步骤的状态(pending/in_progress/completed)",
        f"- **{todolist_clear.name}**:清空任务清单,任务全部完成后调用",
        f"- **{todolist_list.name}**:列出当前任务清单的所有步骤及状态",
    ]

    if not todolist.items:
        lines.append("")
        lines.append(
            f"遇到复杂任务时,使用 {todolist_create.name} 将其拆解为多个步骤并逐步完成。"
        )
        return "\n".join(lines)

    is_overseer = todolist.overseer.active

    lines.append("")
    lines.append("重要指令:")
    lines.append("- 你必须主动推进任务完成,不要等待用户催促")

    if is_overseer:
        lines.append(
            f"- 每完成一步,立即调用 {todolist_update.name} 将状态更新为 {TodoStatus.COMPLETED} 并在 reason 中说明做了什么,然后将下一步更新为 {TodoStatus.IN_PROGRESS}"
        )
        lines.append(
            f"- 需要重建清单时,调用 {todolist_create.name} 并在 reason 中说明原清单问题和新清单改进"
        )
    else:
        lines.append(
            f"- 每完成一步,立即调用 {todolist_update.name} 将状态更新为 {TodoStatus.COMPLETED},然后将下一步更新为 {TodoStatus.IN_PROGRESS},reason 参数无需填写"
        )
    lines.append(f"- 全部完成后调用 {todolist_clear.name} 清空清单")

    lines.append("- 完成当前步骤后,自动开始下一步,不要停下来问用户")
    lines.append("- 如果遇到阻塞,记录问题并继续推进其他步骤")
    lines.append(f"- 需要查看当前进度时,调用 {todolist_list.name}")
    return "\n".join(lines)


# ── 工具列表 ────────────────────────────────────────────────


def get_tools() -> list:
    """获取 todolist 工具(仅 root agent 使用)"""
    return [
        todolist_create,
        todolist_update,
        todolist_clear,
        todolist_list,
        todolist_cancel,
    ]


def get_all_tools() -> list:
    """获取所有待办工具(文档用)"""
    return [
        todolist_create,
        todolist_update,
        todolist_clear,
        todolist_list,
        todolist_cancel,
    ]
