from uniclaw.tools.base import tool
import asyncio
import time
from uniclaw.config import AppConfig
from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR
from uniclaw.tools.multi_agent.sub_agent import load_agent_definitions
from uniclaw.context import APP_NAME
from uniclaw.tools.session.session import AIMessage


@tool
async def subagent_create(
    prompt: str,
    subagent_type: str,
    name: str,
    wait: bool = True,
    isolation=False,
    config: AppConfig = None,
):
    """
    创建并启动一个子智能体任务。

    Args:
        prompt (str): 用户消息或任务提示
        subagent_type (str): 子智能体类型标识符,使用 subagent_list_definitions 查看所有可用类型(含内置和自定义)。
        name (str): 智能体名称
        wait (bool, optional): 是否等待任务完成,默认True。
            - True: 同步执行,等待任务完成后返回结果,不需要调用 subagent_close
            - False: 异步执行,立即返回任务信息,需要使用 subagent_close 关闭智能体
        isolation (bool, optional): 是否启用隔离模式,默认False

    Returns:
        str: 执行结果或任务信息。异步模式(wait=False)下可使用 subagent_check_result 查询状态、
             subagent_send_message 发送消息,任务完成后需调用 subagent_close 关闭智能体

    Example:
        >>> # 同步执行(不需要关闭)
        >>> result = subagent_create(prompt="分析代码", subagent_type="code_analyzer", name="task1")
        >>>
        >>> # 异步执行,需要使用 subagent_close 关闭
        >>> task_info = subagent_create(prompt="编写测试", subagent_type="test_writer", name="task2", wait=False)
        >>> subagent_check_result("task2")  # 查询结果
        >>> subagent_send_message("task2", "补充要求")  # 发送消息
        >>> subagent_close("task2")  # 任务完成后必须关闭
    """
    from uniclaw.agent import MultiAgent

    # 创建多智能体管理器实例
    mgr = MultiAgent.get_instance()
    # 创建子智能体配置
    sub_config = config.create_sub_config(name=name, prompt=prompt)
    # 启动子智能体任务,配置系统提示、智能体定义和隔离模式等参数
    root_dir = config.root_dir
    task = await mgr.start_sub_agent(
        user_message=prompt,
        config=sub_config,
        system_prompt=None,
        agent_def=load_agent_definitions(root_dir).get(subagent_type),
        isolation=isolation,
        inherit_events=wait,
        notify_parent=not wait,
        keep_alive=not wait,
    )
    from uniclaw.agent import AgentStatus

    # 检查任务启动是否失败,如果失败则返回错误信息
    if task.status == AgentStatus.FAILED:
        return f"{TOOL_ERROR}: 生成智能体时出错: {task.result}"

    # 根据 wait 参数决定是同步等待还是异步返回
    if wait:
        # 异步等待任务完成,每次超时300秒
        await mgr.wait(task.id, timeout=300)
        result = task.result or f"(无输出 — 状态:{task.status})"
        header = f"[智能体:{task.name}"
        if subagent_type:
            header += f" ({subagent_type})"
        if task.worktree_branch:
            header += f", 分支:{task.worktree_branch}"
        header += "]"
        return f"{header}\n\n{result}"
    else:
        # 异步模式:立即返回任务基本信息,供后续查询使用
        info_parts = [
            f"任务 ID: {task.id}",
            f"名称:{task.name}",
            f"状态:{task.status}",
        ]
        if subagent_type:
            info_parts.append(f"类型:{subagent_type}")
        if task.worktree_branch:
            info_parts.append(f"工作树分支:{task.worktree_branch}")
        info_parts.append(
            f"使用 {subagent_check_result.name} 或 {subagent_send_message.name} 与此智能体交互。"
        )
        info_parts.append(
            f"子智能体完成后会发送以 {SYSTEM_PREFIX}[child_agent] 前缀通知;请使用任务ID调用 {subagent_check_result.name} 来读取结果。"
        )
        info_parts.append(f"使用 {subagent_close.name} 可关闭智能体释放资源。")
        return "\n".join(info_parts)


@tool
def subagent_send_message(task_id: str, message: str) -> str:
    """
    向指定的智能体发送消息。

    该函数尝试将消息发送给目标智能体。如果智能体正在运行,消息会被排队等待处理；
    如果智能体不存在或未运行,则返回相应的错误信息。

    Args:
        task_id (str): 目标智能体的名称或ID。
        message (str): 要发送给智能体的消息内容。

    Returns:
        str: 操作结果的状态信息,包含以下情况:
            - 成功时:返回消息已排队的确认信息
            - 智能体不存在时:返回无法找到智能体的错误提示
            - 智能体未运行时:返回包含智能体当前状态的错误信息
    """
    from uniclaw.agent import MultiAgent

    mgr = MultiAgent.get_instance()
    ok = mgr.send_message(task_id, message)
    if ok:
        return f"消息已排队发送给智能体 '{task_id}'。它将在当前工作完成后处理。"

    task = mgr.id2AgentTask.get(task_id)
    if task is None:
        return f"{TOOL_ERROR}: 无法找到智能体 '{task_id}'。请检查名称是否正确。"
    return f"{TOOL_ERROR}: 智能体 '{task_id}' 未运行(状态: {task.status})。无法发送消息。"


@tool
def subagent_close(task_id: str) -> str:
    """
    关闭后台子智能体,当父智能体决定其任务已完成时调用。

    Args:
        task_id (str): 要关闭的子智能体任务ID

    Returns:
        str: 操作结果信息
    """
    from uniclaw.agent import MultiAgent

    mgr = MultiAgent.get_instance()
    ok = mgr.close_agent(task_id)
    if ok:
        return f"已向子智能体 '{task_id}' 发送关闭信号。"
    return f"{TOOL_ERROR}: 未找到子智能体 '{task_id}'。"


@tool
def subagent_check_result(task_id: str, full: bool = False) -> str:
    """
    检查指定任务ID的执行结果和状态信息。

    该函数通过 MultiAgent 管理器查询指定任务的状态、名称、工作树分支和执行结果,
    并以格式化的字符串形式返回这些信息。如果任务不存在,则返回错误提示。

    Args:
        task_id (str): 要查询的任务唯一标识符。
        full (bool): 是否返回完整的历史消息。默认为 False,仅返回最新结果。

    Returns:
        str: 格式化的任务信息字符串,包含以下内容:
             - 状态:任务的当前执行状态
             - 名称:任务的名称
             - 工作树分支(如果存在):任务关联的工作树分支信息
             - 结果(如果存在):任务的执行结果内容
             如果任务不存在,返回错误提示信息。
    """
    from uniclaw.agent import MultiAgent

    mgr = MultiAgent.get_instance()
    task = mgr.id2AgentTask.get(task_id)
    if task is None:
        return f"{TOOL_ERROR}: 不存在 ID 为 '{task_id}' 的任务"
    lines = [f"状态:{task.status}", f"名称:{task.name}"]
    if task.worktree_branch:
        lines.append(f"工作树分支:{task.worktree_branch}")
    assistant_items = task.session.get_assistant_messages(separator=None)
    if full:
        result = "\n".join(assistant_items) or task.result
        if result:
            lines.append(f"\n结果: \n{result}")
        task.result_read_index = len(assistant_items)
        return "\n".join(lines)

    start = min(task.result_read_index, len(assistant_items))
    new_items = assistant_items[start:]
    task.result_read_index = len(assistant_items)
    if new_items:
        lines.append("\n新增结果: \n" + "\n".join(new_items))
    else:
        lines.append("\n新增结果: \n(暂无新增输出)")
    return "\n".join(lines)


@tool
def subagent_list_tasks() -> str:
    """
    列出所有子智能体任务的当前状态和信息。

    该函数通过 MultiAgent 管理器获取所有正在运行的任务,并以格式化的表格形式展示每个任务的关键信息,
    包括任务ID、名称、状态、工作树分支和提示内容(截断显示)。

    Returns:
        str: 格式化后的任务列表字符串。如果没有任务,返回提示信息;否则返回包含表头和所有任务信息的表格字符串,
             每行包含任务ID、名称(最多8字符)、状态、工作树分支(最多15字符)和提示内容(最多50字符)。
    """
    from uniclaw.agent import MultiAgent

    mgr = MultiAgent.get_instance()
    tasks = mgr.list_tasks()

    # 检查是否存在任务,若无则返回提示信息
    if not tasks:
        return "没有子智能体任务。"

    # 构建表格头部和分隔线
    lines = ["ID           | 名称     | 状态      | 工作树分支       | 提示"]
    lines.append("-------------|----------|-----------|-----------------|------")

    # 遍历所有任务,格式化每个任务的信息并添加到列表中
    for task in tasks:
        prompt_short = task.prompt[:50] + ("..." if len(task.prompt) > 50 else "")
        wt = task.worktree_branch[:15] if task.worktree_branch else "-"
        lines.append(
            f"{task.id} | {task.name[:8]:8s} | {task.status:9s} | {wt:15s} | {prompt_short}"
        )

    # 将所有行连接成完整的表格字符串并返回
    return "\n".join(lines)


@tool
async def subagent_discuss(topic: str, participants: list[str], rounds: int = 2) -> str:
    """
    让现有的后台子智能体围绕指定主题进行有限轮次的讨论。

    participants 应包含子智能体的任务ID。父智能体作为协调者:
    它将每轮的讨论记录发送给每个参与者,等待他们的回复,并返回完整的讨论文本。
    当父智能体决定不再需要这些子智能体时,应使用 subagent_close 关闭它们。

    Args:
        topic (str): 讨论的主题
        participants (list[str]): 参与讨论的子智能体任务ID列表
        rounds (int): 讨论轮数,默认为2,范围为1-5

    Returns:
        str: 完整的讨论文本,包含主题和每轮各参与者的发言
    """
    from uniclaw.agent import MultiAgent, AgentStatus

    mgr = MultiAgent.get_instance()
    tasks = []
    # 验证所有参与者是否存在且状态可用
    for task_id in participants:
        task = mgr.id2AgentTask.get(task_id)
        if task is None:
            return f"{TOOL_ERROR}: child agent '{task_id}' was not found."
        if task.status not in (
            AgentStatus.RUNNING,
            AgentStatus.PENDING,
            AgentStatus.WAITING,
        ):
            return f"{TOOL_ERROR}: child agent '{task_id}' is not available (status: {task.status})."
        tasks.append(task)

    # 限制讨论轮数在1-5之间
    rounds = max(1, min(int(rounds), 5))
    transcript = [f"Topic: {topic}"]

    # 执行多轮讨论
    for round_no in range(1, rounds + 1):
        round_entries = []
        context = "\n\n".join(transcript)
        for task in tasks:
            before_count = len(task.session)
            # 构造本轮的提示词,包含当前轮次信息和历史讨论文本
            prompt = (
                f"讨论第 {round_no}/{rounds} 轮。\n"
                f"主题:{topic}\n\n"
                f"当前讨论记录:\n{context}\n\n"
                "请回复你当前的观点、不同意见和具体建议。"
            )
            # 发送消息给子智能体
            if not mgr.send_message(task.id, prompt):
                round_entries.append(f"[{task.name}] could not receive the message.")
                continue

            # 等待子智能体响应,最多等待300秒
            deadline = time.time() + 300
            while time.time() < deadline:
                if task.status in (AgentStatus.FAILED, AgentStatus.CANCELLED):
                    break
                # 检查是否有新消息且状态为WAITING(表示已完成回复)
                if (
                    len(task.session) > before_count
                    and task.status == AgentStatus.WAITING
                ):
                    break
                await asyncio.sleep(0.2)

            # 获取最新的助手回复
            latest = ""
            for message in reversed(task.session):
                if isinstance(message, AIMessage) and message.content:
                    latest = message.to_content()
                    break
            round_entries.append(
                f"[{task.name} / {task.id}]\n{latest or task.result or '(no response)'}"
            )
        transcript.append(f"Round {round_no}\n" + "\n\n".join(round_entries))

    return "\n\n".join(transcript)


@tool
def subagent_list_definitions(config: AppConfig = None) -> str:
    """
    列出所有可用的智能体类型定义。

    该函数加载并格式化显示系统中所有已定义的智能体类型信息,包括每个智能体的名称、
    调用 subagent_create 时使用类型名称作为 subagent_type。
    """
    root_dir = config.root_dir
    defs = load_agent_definitions(root_dir)
    if not defs:
        return "没有可用的智能体类型。"

    # 构建智能体类型列表的输出内容
    lines = ["可用的智能体类型:", ""]
    for aname, d in sorted(defs.items()):
        model_info = f"  模型:{d.model_name}" if d.model_name else ""
        tools_info = f"  工具:{', '.join(d.tools)}" if d.tools else ""
        lines.append(f"  {aname:20s}  [{d.source:8s}]  {d.description}")
        if model_info:
            lines.append(f"                           {model_info}")
        if tools_info:
            lines.append(f"                           {tools_info}")
    return "\n".join(lines)


@tool
def subagent_get_definition(subagent_type: str, config: AppConfig = None) -> str:
    """
    获取指定智能体类型的详细信息。

    Args:
        subagent_type: 智能体类型标识符(如 "coder"、"recon")
    """
    defs = load_agent_definitions(config.root_dir)
    d = defs.get(subagent_type)
    if not d:
        available = "、".join(defs.keys())
        return f"{TOOL_ERROR}: 未找到类型 '{subagent_type}'。可用类型: {available}"
    lines = [
        f"类型: {d.name}",
        f"来源: {d.source}",
        f"描述: {d.description}",
    ]
    if d.model_name:
        lines.append(f"模型: {d.model_name}")
    if d.tools:
        lines.append(f"工具: {', '.join(d.tools)}")
    else:
        lines.append("工具: 全部可用")
    if d.system_prompt:
        lines.append(f"\n系统提示词:\n{d.system_prompt}")
    return "\n".join(lines)


def get_tools() -> list:
    """获取多智能体工具列表"""
    return [
        subagent_create,
        subagent_send_message,
        subagent_close,
        subagent_check_result,
        subagent_list_tasks,
        subagent_discuss,
        subagent_get_definition,
        subagent_list_definitions,
    ]


def get_all_tools() -> list:
    """获取所有多智能体工具(无条件返回)"""
    return get_tools()


def get_sub_agent_system_prompt() -> str:
    """生成子代理系统提示词(静态内容,保护缓存)"""
    defs = load_agent_definitions()
    if not defs:
        return ""
    lines = [
        "# 子代理",
        f"当遇到以下情况时,使用 {subagent_create.name} 启动子代理:",
        "- 大文件/多文件分析,避免污染主上下文",
        "- 需要并行处理多个独立任务(wait=False 异步启动)",
        "- 隔离执行有风险的操作",
        f"可用subagent_type({len(defs)}个):",
    ]
    lines.append("  " + "、".join(defs.keys()))
    lines.append(f"使用 {subagent_list_definitions.name} 查看详细说明。")
    return "\n".join(lines)
