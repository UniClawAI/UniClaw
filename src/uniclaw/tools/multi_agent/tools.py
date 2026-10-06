from uniclaw.tools.base import tool, ToolRuntime
import asyncio
import time
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
    tool_runtime: ToolRuntime = None,
):
    """
    创建并启动一个子智能体任务。

    Args:
        prompt (str): 用户消息或任务提示
        subagent_type (str): 子智能体类型标识符,使用 subagent_list_definitions 查看所有可用类型(含内置和自定义)。
        name (str): 智能体名称
        wait (bool, optional): 是否等待任务完成,默认True。
            - True: 同步执行,等待任务完成后返回结果,不需要调用 subagent_close
            - False: 异步执行,立即返回任务信息;回合结束后用 subagent_check_result
              读取结果。任务保留到显式 subagent_close 才回收,可多轮 send_to_child 协作
        isolation (bool, optional): 是否启用隔离模式,默认False

    Returns:
        str: 执行结果或任务信息。异步模式(wait=False)下可使用 subagent_check_result 查询状态、
             subagent_send_to_child 发送消息;任务保留到显式 subagent_close 才回收

    Example:
        >>> # 同步执行(不需要关闭)
        >>> result = subagent_create(prompt="分析代码", subagent_type="code_analyzer", name="task1")
        >>>
        >>> # 异步执行: 可多轮派活/读取,任务保留到显式 close
        >>> task_info = subagent_create(prompt="编写测试", subagent_type="test_writer", name="task2", wait=False)
        >>> subagent_check_result("task2")           # 读取结果
        >>> subagent_send_to_child("task2", "补充要求")  # 继续派活
        >>> subagent_check_result("task2")           # 再读下一轮
        >>> subagent_close("task2")                  # 结束协作时显式关闭
    """
    from uniclaw.agent import MultiAgent

    config = tool_runtime.config
    # 创建多智能体管理器实例
    mgr = MultiAgent.get_instance()
    # 创建子智能体配置(tool_call_id 用于前端把子代理事件关联回本次工具块)
    sub_config = config.create_sub_config(
        name=name, prompt=prompt, tool_call_id=tool_runtime.tool_call_id
    )
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
        if wait:
            # 同步: 错误已内联返回,调用方不会再碰这个任务,直接回收
            mgr.detach_sub_config(config, task)
            return f"{TOOL_ERROR}: 生成智能体时出错: {task.result}"
        # 异步: 任务保留到显式 subagent_close(不 close 就一直留在
        # subagent_list_tasks 里),故报错要带 task_id 供调用方关闭
        return (
            f"{TOOL_ERROR}: 生成智能体时出错(name={task.name}, 任务ID={task.id}): "
            f"{task.result}\n"
            f"任务已保留在 subagent_list_tasks 中,可用 {subagent_close.name}(task_id=\"{task.id}\") 关闭。"
        )

    # 根据 wait 参数决定是同步等待还是异步返回
    if wait:
        # 同步等待任务完成,每次超时300秒
        await mgr.wait(task.id, timeout=300, config=config)
        result = task.result or f"(无输出 — 状态:{task.status})"
        # 同步结果已内联返回给调用方,回收 sub_config
        # (_run_cleanup 对同步子代理也会回收,此处双保险)
        mgr.detach_sub_config(config, task)
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
            f"状态:{_status_text(task)}",
        ]
        if subagent_type:
            info_parts.append(f"类型:{subagent_type}")
        if task.worktree_branch:
            info_parts.append(f"工作树分支:{task.worktree_branch}")
        info_parts.append(
            f"使用 {subagent_check_result.name} 或 {subagent_send_to_child.name} 与此智能体交互。"
        )
        info_parts.append(
            f"子智能体完成/失败/被取消时会以 {SYSTEM_PREFIX}[child_agent] 前缀消息唤醒你(消息中的 状态 字段标明具体情形);请使用任务ID调用 {subagent_check_result.name} 来读取结果。"
        )
        info_parts.append(
            f"任务仍在运行需提前停止时,使用 {subagent_close.name} 关闭智能体释放资源。"
        )
        return "\n".join(info_parts)


def _status_text(task) -> str:
    """状态的可读表达,额外标出驻留态。

    `status` 只表"本回合结果";keep_alive 子代理回合结束后 status=COMPLETED
    但 future 仍存活(驻留等下一条消息),只显示 completed 会让人以为彻底结束。
    """
    from uniclaw.agent import AgentStatus

    if task.status == AgentStatus.COMPLETED and task.can_accept_messages:
        return f"{task.status}(驻留待消息)"
    return str(task.status)


def _resolve_child(config, ref: str):
    """从**当前 agent 自己**的 sub_configs 里解析子代理。

    send_to_child 是父→子单向,只能触达自己创建的子代理。这里不能用
    全局 id2AgentTask 查表,否则会越过边界打到
    别的 agent 的任务、甚至打回自己的父级 —— 也与 subagent_list_tasks 展示的
    范围(list 只列 config.sub_configs)对不上。

    Args:
        config: 当前 agent 的 AppConfig(tool_runtime.config)。
        ref: 子代理名称或 ID。

    Returns:
        (task, err): 成功时 (task, "");失败时 (None, 报错),
        报错列出当前可用的 name/ID 便于调用方自纠。
    """
    if config is None:
        return None, "无法获取当前 agent 配置"
    subs = [
        sub.current_agent
        for sub in (config.sub_configs or [])
        if sub.current_agent is not None
    ]
    if not subs:
        return None, "当前 agent 没有子智能体(可用 subagent_list_tasks 确认)"
    exact = [t for t in subs if t.id == ref]
    if exact:
        return exact[0], ""
    matches = [t for t in subs if t.name == ref]
    available = ", ".join(f"{t.name}/{t.id}" for t in subs) or "无"
    if len(matches) == 1:
        return matches[0], ""
    if len(matches) > 1:
        return (
            None,
            f"'{ref}' 匹配到 {len(matches)} 个同名子智能体,请改用任务 ID。"
            f"当前 agent 可用: {available}",
        )
    return None, f"未找到名为 '{ref}' 的子智能体。当前 agent 可用: {available}"


@tool
def subagent_send_to_child(
    task_id: str, message: str, tool_runtime: ToolRuntime = None
) -> str:
    """
    向指定的子智能体发送消息(父智能体专用,与 subagent_send_to_parent 方向相反)。

    该函数尝试将消息发送给目标子智能体。如果子智能体正在运行,消息会被排队等待处理；
    如果子智能体不存在或未运行,则返回相应的错误信息。
    只能发给**当前 agent 创建的**子智能体(与 subagent_list_tasks 同范围)。

    Args:
        task_id (str): 目标子智能体的名称或ID(仅限当前 agent 的子智能体)。
        message (str): 要发送给智能体的消息内容。

    Returns:
        str: 操作结果的状态信息,包含以下情况:
            - 成功时:返回消息已排队的确认信息
            - 智能体不存在时:返回无法找到智能体的错误提示(含可用 name/ID)
            - 智能体未运行/已关闭时:返回包含智能体当前状态的错误信息
    """
    from uniclaw.agent import MultiAgent

    mgr = MultiAgent.get_instance()
    config = tool_runtime.config if tool_runtime else None
    task, err = _resolve_child(config, task_id)
    if err:
        return f"{TOOL_ERROR}: {err}"
    if task.closed:
        return f"{TOOL_ERROR}: 智能体 '{task.name}' (ID: {task.id}) 已关闭,无法发送消息。"
    ok = mgr.send_message(task.id, message)
    if ok:
        return f"消息已排队发送给智能体 '{task.name}' (ID: {task.id})。它将在当前工作完成后处理。"
    return (
        f"{TOOL_ERROR}: 智能体 '{task.name}' (ID: {task.id}) 未运行"
        f"(状态: {task.status})。无法发送消息。"
    )


@tool
async def subagent_send_to_parent(message: str, tool_runtime: ToolRuntime = None) -> str:
    """
    向父智能体发送消息(子智能体专用,与 subagent_send_to_child 方向相反)。

    将消息注入父智能体的消息队列:父智能体空闲时会被唤醒,运行中时会在下一次
    检查消息队列时看到。适用于向父智能体汇报进度、请求指示或提交阶段性结果,
    调用后立即返回,无需等待父智能体响应。
    仅异步子智能体(subagent_create wait=False)可用:同步子智能体(wait=True)的
    父智能体正阻塞等待你的最终结果,中途消息无法被及时处理,请直接把要汇报的
    内容写入最终输出返回。

    Args:
        message (str): 要发送给父智能体的消息内容。

    Returns:
        str: 操作结果状态信息。成功时返回送达说明;当前没有父智能体或同步
            子智能体调用时返回错误提示。
    """
    from uniclaw.utils.wakeup import wake_agent

    config = tool_runtime.config
    task = config.current_agent
    parent_config = config.parent_config
    parent_task = config.parent_agent
    # 前置校验: 根 agent(或父级链断裂/自指)不能假装发送成功
    if parent_config is None or parent_task is None or parent_task is task:
        return f"{TOOL_ERROR}: 当前是根 agent,没有父智能体可发送"

    if not task.notify_parent:
        return (
            f"{TOOL_ERROR}: 同步子智能体不能向父智能体发送消息(父智能体正阻塞"
            f"等待你的最终结果,消息无法被及时处理)。请将要汇报的内容直接写入"
            f"最终输出返回。"
        )
    text = (
        f"{SYSTEM_PREFIX}[child_agent]\n"
        f"名称: {task.name}\n"
        f"任务ID: {task.id}\n"
        f"状态: {task.status}\n"
        f"消息: {message}\n"
        f'- 请调用 {subagent_check_result.name}(task_id="{task.id}") 读取该智能体的输出\n'
        f'- 使用 {subagent_send_to_child.name}(task_id="{task.id}", message="...") 回复该智能体'
    )
    if await wake_agent(text, parent_config):
        # 报明收件方身份 —— 否则调用方无法确认消息发给了谁、是否真是父级
        return (
            f"消息已发送给父智能体 '{parent_task.name}' (ID: {parent_task.id}),"
            f"它将在下次处理消息时看到。"
        )
    return f"{TOOL_ERROR}: 向父智能体发送消息失败。"


@tool
def subagent_close(task_id: str, tool_runtime: ToolRuntime = None) -> str:
    """
    关闭后台子智能体,当父智能体决定其任务已完成时调用。

    支持按名称或任务 ID 定位(与 subagent_check_result 一致)。
    **只能关闭当前 agent 自己创建的子智能体**(与 subagent_list_tasks 同范围)。
    关闭后任务从 subagent_list_tasks 列表中移除,不再出现;再次调用同一 task_id
    会报「未找到」。

    Args:
        task_id (str): 要关闭的子智能体名称或任务ID(仅限当前 agent 的子智能体)。

    Returns:
        str: 操作结果信息(已关闭/已关闭过/未找到,未找到时列出可用 name/ID)。
    """
    from uniclaw.agent import MultiAgent, AgentStatus

    mgr = MultiAgent.get_instance()
    config = tool_runtime.config if tool_runtime else None
    # 只能关自己的子代理 —— 全局 id2AgentTask 查表会越界关掉别人的任务,
    # 而下面的 detach_sub_config 只在自己的 sub_configs 里找,静默空转
    task, err = _resolve_child(config, task_id)
    if err:
        return f"{TOOL_ERROR}: {err}"
    name_id = f"'{task.name}' (ID: {task.id})"
    if task.closed:
        return f"子智能体 {name_id} 已关闭过,无需重复关闭。"
    was_finished = task.is_finished
    status = mgr.close_agent(task)
    mgr.detach_sub_config(config, task)
    if status == "already_closed":
        return f"子智能体 {name_id} 已关闭过,无需重复关闭。"
    if was_finished:
        return (
            f"子智能体 {name_id} 已结束(状态: {task.status}),"
            f"已从任务列表移除。"
        )
    return f"已关闭子智能体 {name_id} 并从任务列表移除。"


@tool
def subagent_check_result(
    task_id: str, full: bool = False, tool_runtime: ToolRuntime = None
) -> str:
    """
    检查指定任务的执行结果和状态信息。

    该函数查询指定任务的状态、名称、工作树分支和执行结果,并以格式化的字符串
    形式返回这些信息。支持按名称或任务 ID 定位(与 subagent_close 一致)。
    **只能查询当前 agent 自己创建的子智能体**(与 subagent_list_tasks 同范围)。
    如果任务不存在,则返回错误提示并列出可用的 name/ID。

    Args:
        task_id (str): 要查询的子智能体名称或唯一标识符(仅限当前 agent 的子智能体)。
        full (bool): 是否返回完整的历史消息。默认为 False,仅返回最新结果。

    Returns:
        str: 格式化的任务信息字符串,包含以下内容:
             - 状态:任务的当前执行状态
             - 名称:任务的名称
             - 工作树分支(如果存在):任务关联的工作树分支信息
             - 结果(如果存在):任务的执行结果内容
             如果任务不存在,返回错误提示信息(含可用 name/ID)。
    """
    config = tool_runtime.config if tool_runtime else None
    # 只查自己的子代理 —— 全局 id2AgentTask 查表会把别的 agent 的任务结果也泄露出来
    task, err = _resolve_child(config, task_id)
    if err:
        return f"{TOOL_ERROR}: {err}"
    lines = [f"状态:{_status_text(task)}", f"名称:{task.name}"]
    if task.closed:
        lines.append("已关闭: 是")
    if task.worktree_branch:
        lines.append(f"工作树分支:{task.worktree_branch}")
    assistant_items = task.session.get_assistant_messages(separator=None)
    if full:
        result = "\n".join(assistant_items) or task.result
        if result:
            lines.append(f"\n结果: \n{result}")
        task.result_read_index = len(assistant_items)
    else:
        start = min(task.result_read_index, len(assistant_items))
        new_items = assistant_items[start:]
        task.result_read_index = len(assistant_items)
        if new_items:
            lines.append("\n新增结果: \n" + "\n".join(new_items))
        else:
            lines.append("\n新增结果: \n(暂无新增输出)")

    return "\n".join(lines)


@tool
def subagent_list_tasks(tool_runtime: ToolRuntime = None) -> str:
    """
    列出当前 agent 创建的子智能体任务的当前状态和信息。

    返回当前 agent 直接创建的子代理任务列表(运行中/驻留待消息/已结束的),
    输出带表头,每行一个任务,字段顺序为:任务ID | 名称 | 状态 | 工作树分支(无则-) | 提示。
    主/根会话任务不会出现在列表中;只有经 subagent_close 关闭的任务才不再出现。

    Returns:
        str: 没有子代理任务时返回提示信息;否则第一行为表头,
             之后每行一个任务,含任务ID、名称、状态、工作树分支和提示内容。
    """
    config = tool_runtime.config if tool_runtime else None
    tasks = (
        [
            sub.current_agent
            for sub in config.sub_configs
            if sub.current_agent and not sub.current_agent.closed
        ]
        if config
        else []
    )

    # 检查是否存在任务,若无则返回提示信息
    if not tasks:
        return "没有子智能体任务。"

    # 每行一个任务,字段用 | 分隔(输出供 AI 阅读,无需表格对齐)
    lines = ["任务ID | 名称 | 状态 | 工作树分支 | 提示"]
    for task in tasks:
        wt = task.worktree_branch or "-"
        lines.append(f"{task.id} | {task.name} | {_status_text(task)} | {wt} | {task.prompt}")
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
        if task.status not in (AgentStatus.RUNNING, AgentStatus.PENDING):
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
                # 检查是否有新消息且状态为 COMPLETED(本回合已结束,表示已完成回复)
                if (
                    len(task.session) > before_count
                    and task.status == AgentStatus.COMPLETED
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
def subagent_list_definitions(tool_runtime: ToolRuntime = None) -> str:
    """
    列出所有可用的智能体类型定义。

    该函数加载并格式化显示系统中所有已定义的智能体类型信息,包括每个智能体的名称、
    调用 subagent_create 时使用类型名称作为 subagent_type。
    """
    config = tool_runtime.config
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
def subagent_get_definition(
    subagent_type: str, tool_runtime: ToolRuntime = None
) -> str:
    """
    获取指定智能体类型的详细信息。

    Args:
        subagent_type: 智能体类型标识符(如 "coder"、"recon")
    """
    config = tool_runtime.config
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
    # subagent_send_to_parent 不在列表中: 子代理专属,仅由 agent/_run 强制注入,
    # 不注册进 registry,主 agent 通过 get_tools()/search_tools 均不可见
    return [
        subagent_create,
        subagent_send_to_child,
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


# subagent_send_to_parent 是子代理专属核心工具(不进 get_tools()/registry,
# 主 agent 经 get_tools/search_tools 均不可见)。加入 CORE_TOOL_NAMES:
# 1) 不参与扩展工具能量淘汰(agent 主循环 touch/drain_energy 跳过,始终加载);
# 2) get_builtin_tool_names 覆盖插件重名检测 — 该检测在 init_registry 之前执行,
#    因此登记必须在模块加载时完成,不能依赖 init_registry。
from uniclaw.tools.registry import CORE_TOOL_NAMES

CORE_TOOL_NAMES.add(subagent_send_to_parent.name)


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
