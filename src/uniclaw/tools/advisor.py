"""顾问模型工具 — 查询和调用配置的顾问模型。"""

import asyncio

from uniclaw.tools.base import tool
from uniclaw.config import AppConfig


@tool
def advisor_list(config: AppConfig = None) -> str:
    """
    列出已配置的顾问模型列表。当遇到难题或需要更强大模型的意见时,先用此工具查看可用的顾问模型。

    Returns:
        顾问模型列表及编号,若无配置则提示用户先配置。
    """
    if not config or not config.large_model_name:
        return "未配置顾问模型。请先在设置中添加顾问模型(large_model_name),或使用 /model 命令将模型设为顾问模型。"

    lines = [f"已配置 {len(config.large_model_name)} 个顾问模型:"]
    for i, model in enumerate(config.large_model_name, 1):
        lines.append(f"  [{i}] {model}")
    lines.append("\n使用 ask_advisor 工具向指定顾问模型提问。")
    return "\n".join(lines)


async def _call_advisor(model: str, system_message: str, user_message: str, config: AppConfig) -> str:
    """调用单个顾问模型。"""
    from uniclaw.provider.router import achat
    from uniclaw.tools.session.session import Session, UserMessage

    try:
        session = Session()
        session._messages = [UserMessage(content=user_message)]

        result = await achat(
            system_prompt=system_message,
            session=session,
            model_name=model,
            config=config,
            enable_thinking=True,
            thinking=True,
        )
        return result.content or "(顾问模型未返回内容)"
    except Exception as e:
        return f"调用失败: {e}"


@tool
async def ask_advisor(
    model: list[str],
    system_message: str,
    user_message: str,
    config: AppConfig = None,
) -> str:
    """
    向顾问模型提问,获取更强大模型的建议和分析。

    适用场景:
    - 遇到知识不足、不确定该用什么方案时
    - 需要验证思路或获取第二意见时
    - 涉及专业领域需要更深入的分析时

    注意事项:
    - 这是一次性问答,没有除 system_message 和 user_message 以外的上下文
    - 此工具没有探索能力,无法读取文件、搜索代码或执行命令
    - 如果是项目本身的问题(如代码结构、文件内容),应先收集信息再提问
    - user_message 中应尽可能详细地描述:当前遇到的问题、运行环境、已有的信息、可用的工具等

    Args:
        model: 顾问模型名称列表(格式: provider/model),通过 advisor_list 获取可用列表,可指定多个模型同时咨询
        system_message: 系统提示词,定义顾问的角色和专业领域
        user_message: 用户提问内容,应包含完整的问题描述和背景信息
    """
    if not config:
        return "错误: 无法获取配置"

    # 验证模型是否在顾问列表中
    invalid = [m for m in model if m not in config.large_model_name]
    if invalid:
        available = ", ".join(config.large_model_name) if config.large_model_name else "无"
        return f"错误: 模型 '{', '.join(invalid)}' 不在顾问模型列表中。可用的顾问模型: {available}"

    # 并发调用所有顾问模型
    tasks = [_call_advisor(m, system_message, user_message, config) for m in model]
    results = await asyncio.gather(*tasks)

    # 汇总结果,标注模型来源
    if len(model) == 1:
        return results[0]

    parts = []
    for m, r in zip(model, results):
        parts.append(f"=== {m} ===\n{r}")
    return "\n\n".join(parts)


def get_tools(config=None) -> list:
    """返回此模块提供的工具列表。

    仅当配置了顾问模型时才返回工具。
    """
    if not config or not config.large_model_name:
        return []
    return [advisor_list, ask_advisor]


def get_all_tools() -> list:
    """返回此模块的全部工具"""
    return [advisor_list, ask_advisor]
