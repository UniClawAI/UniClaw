"""顾问模型工具 — 通过调查代理收集信息后向顾问模型提问。"""

import asyncio

from uniclaw.tools.base import tool
from uniclaw.config import AppConfig
from uniclaw.utils.constants import TOOL_ERROR


def _strip_provider(model: str) -> str:
    """去掉提供商前缀,如 'openai/gpt-4o' → 'gpt-4o'。"""
    return model.split("/", 1)[-1] if "/" in model else model


def _match_model(name: str, candidates: list[str]) -> str | None:
    """从候选列表中匹配模型。支持全名或去掉了提供商前缀的名称。"""
    for full in candidates:
        if name == full or name == _strip_provider(full):
            return full
    return None


@tool
def advisor_list(config: AppConfig = None) -> str:
    """
    列出已配置的 AI 顾问模型列表。当遇到难题或需要更强大 AI 模型的意见时,先用此工具查看可用的顾问模型。

    Returns:
        顾问模型列表及编号,若无配置则提示用户先配置。
    """
    if not config or not config.large_model_name:
        return "未配置顾问模型。请先在设置中添加顾问模型(large_model_name),或使用 /model 命令将模型设为顾问模型。"

    lines = [f"已配置 {len(config.large_model_name)} 个顾问模型:"]
    for i, model in enumerate(config.large_model_name, 1):
        lines.append(f"  [{i}] {_strip_provider(model)}")
    lines.append("\n使用 ask_advisor 工具向指定顾问模型提问。")
    return "\n".join(lines)


@tool
async def investigate(query: str, config: AppConfig = None) -> str:
    """
    调查项目信息并返回精炼摘要。内部启动侦察代理收集和整理数据。

    适用场景:
    - 需要了解某个模块/函数的实现细节
    - 需要搜索项目中与某个主题相关的代码和文档
    - 需要收集信息后再做判断

    注意:
    - 返回的是精炼摘要,不是原始数据
    - 提出具体的调查问题会得到更好的结果
    - 可以多次调用以从不同角度调查

    Args:
        query: 具体的调查问题,越详细越好(例如"auth 模块的登录流程是怎样实现的")
    """
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    from uniclaw.agent import MultiAgent

    mgr = MultiAgent.get_instance()
    sub_config = config.create_sub_config(name="recon", prompt=query)
    # recon 使用主 agent 的默认模型(非顾问模型)
    if hasattr(config, "_parent_model_name"):
        sub_config.model_name = list(config._parent_model_name)
    # recon 由工具启动,需要额外一层深度余量
    sub_config.max_agent_depth += 1

    from uniclaw.tools.multi_agent.sub_agent import load_agent_definitions

    from uniclaw.agent import AgentStatus

    recon_def = load_agent_definitions().get("recon")
    if not recon_def:
        return f"{TOOL_ERROR}: 未找到侦察代理(recon)定义"

    task = await mgr.start_sub_agent(
        user_message=query,
        config=sub_config,
        agent_def=recon_def,
        isolation=False,
        inherit_events=True,
    )

    if task.status == AgentStatus.FAILED:
        return f"{TOOL_ERROR}: 侦察代理启动失败: {task.result}"

    await mgr.wait(task.id, timeout=300)
    return task.result or f"(侦察代理未返回结果 — 状态: {task.status})"


@tool
async def ask_advisor(
    model: list[str],
    system_message: str,
    user_message: str,
    config: AppConfig = None,
) -> str:
    """
    向 AI 顾问模型(高级大模型)请教方案和技术建议。当你自己拿不准怎么做时,让更强的模型帮你分析。

    顾问模型通过调查工具(investigate)收集项目信息,基于调查结果给出建议。
    顾问模型不会直接接触项目原始数据,只看到调查代理返回的精炼摘要。

    适用场景(最后手段 — 先自己尝试解决,多轮尝试无果后再使用):
    - 自己反复尝试后仍无法解决的难题
    - 需要验证自己的思路或获取第二意见
    - 涉及专业领域需要更深入的分析

    注意区分:
    - ask_advisor = 问方案(向 AI 模型请教"怎么做更好")
    - AskUserQuestion = 问需求(向用户确认"你想要什么")

    Args:
        model: 顾问模型名称列表,通过 advisor_list 获取可用列表,可指定多个模型同时咨询
        system_message: 系统提示词,定义顾问的角色和专业领域(例如"你是一个专注于性能优化的资深工程师")
        user_message: 用户提问内容,应包含完整的问题描述和背景信息
    """
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    # 验证并解析模型名(支持省略提供商前缀)
    resolved = []
    for m in model:
        full = _match_model(m, config.large_model_name)
        if not full:
            available = (
                ", ".join(_strip_provider(x) for x in config.large_model_name)
                if config.large_model_name
                else "无"
            )
            return f"{TOOL_ERROR}: 模型 '{m}' 不在顾问列表中。可用: {available}"
        resolved.append(full)

    # 构建顾问代理定义: 只有 investigate 一个工具
    from uniclaw.tools.multi_agent.sub_agent import AgentDefinition

    advisor_def = AgentDefinition(
        name="advisor",
        description="顾问代理,通过调查工具收集信息后给出专业建议",
        system_prompt=system_message,
        tools=[investigate.name],
        source="built-in",
    )

    # 为每个模型启动一个顾问子代理
    async def _call_one(m: str) -> str:
        from uniclaw.agent import MultiAgent, AgentStatus

        mgr = MultiAgent.get_instance()
        sub_config = config.create_sub_config(name="advisor", prompt=user_message)
        # 保存主 agent 的默认模型,recon 子代理需要用它
        sub_config._parent_model_name = list(config.model_name)
        sub_config.model_name = [m]

        task = await mgr.start_sub_agent(
            user_message=user_message,
            config=sub_config,
            agent_def=advisor_def,
            isolation=False,
            inherit_events=True,
        )

        if task.status == AgentStatus.FAILED:
            return f"{TOOL_ERROR}: 顾问代理 ({m}) 启动失败: {task.result}"

        await mgr.wait(task.id, timeout=300)
        return task.result or f"(顾问模型未返回结果 — 状态: {task.status})"

    # 并发调用所有顾问模型
    tasks = [_call_one(m) for m in resolved]
    results = await asyncio.gather(*tasks)

    # 汇总结果,标注模型来源
    if len(resolved) == 1:
        return results[0]

    parts = []
    for m, r in zip(resolved, results):
        parts.append(f"=== {_strip_provider(m)} ===\n{r}")
    return "\n\n".join(parts)


def get_tools(config=None) -> list:
    """返回此模块提供的工具列表。

    仅当配置了顾问模型时才返回工具。
    """
    if not config or not config.large_model_name:
        if config:
            config.record_unavailable_tools(
                [advisor_list.name, ask_advisor.name, investigate.name],
                "未配置 large_model_name(顾问模型),顾问工具不可用",
            )
        return []
    config.clear_unavailable_tools(
        [advisor_list.name, ask_advisor.name, investigate.name]
    )
    return [advisor_list, ask_advisor, investigate]


def get_all_tools() -> list:
    """返回此模块的全部工具"""
    return [advisor_list, ask_advisor, investigate]
