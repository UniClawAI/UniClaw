from __future__ import annotations

import asyncio
import json

from uniclaw.config import AppConfig
from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR
from .client import A2AClient, extract_a2a_text
from .manager import A2AManager


_TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}
_watchers: dict[tuple[str, str, str], asyncio.Task] = {}


async def _watch_a2a_task(agent_name: str, task_id: str, context_id: str, client: A2AClient, config: AppConfig) -> None:
    """Poll one remote task and wake the originating agent exactly once."""
    key = (config.current_agent.id, agent_name, task_id)
    try:
        for _ in range(86_400):  # at most 24 hours, one request per second
            await asyncio.sleep(1)
            result = await client.get_task(task_id)
            state = result.get("status", {}).get("state", "unknown")
            if state not in _TERMINAL_STATES:
                continue
            from uniclaw.utils.constants import SYSTEM_PREFIX
            from uniclaw.utils.wakeup import wake_agent

            content = extract_a2a_text(result)
            await wake_agent(
                f"{SYSTEM_PREFIX}[a2a] 远程任务已{state}\n"
                f"agent: {agent_name}\ntask_id: {task_id}\ncontext_id: {context_id}\n结果:\n{content}",
                config,
            )
            return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        from uniclaw.utils.constants import SYSTEM_PREFIX
        from uniclaw.utils.wakeup import wake_agent

        await wake_agent(
            f"{SYSTEM_PREFIX}[a2a] 远程任务状态查询失败\nagent: {agent_name}\ntask_id: {task_id}\n错误: {exc}",
            config,
        )
    finally:
        _watchers.pop(key, None)


def _start_task_watcher(agent_name: str, result: dict, client: A2AClient, config: AppConfig) -> None:
    task_id = result.get("id")
    state = result.get("status", {}).get("state")
    if not task_id or state in _TERMINAL_STATES or config is None:
        return
    key = (config.current_agent.id, agent_name, task_id)
    if key not in _watchers:
        _watchers[key] = asyncio.create_task(
            _watch_a2a_task(agent_name, task_id, result.get("contextId", ""), client, config)
        )


@tool
async def a2a_send_task(agent_name: str, message: str, context_id: str = "") -> str:
    """向已配置的外部 A2A Agent 发送任务并等待最终回复;可传 context_id 继续多轮对话。

    Args:
        agent_name: 通过 a2a_add_agent 添加的远程 Agent 名称。
        message: 要委托给远程 Agent 的完整任务说明。
        context_id: 可选的前一轮 context_id,用于继续同一远程对话。
    """
    remote = await A2AManager.get_instance().get_agent(agent_name)
    if not remote:
        return f"{TOOL_ERROR}: 未找到 A2A Agent '{agent_name}';请先用 a2a_add_agent 添加。"
    try:
        client = A2AClient(remote["url"], remote.get("token", ""))
        submitted = await client.send_message(message, context_id)
        task_id = submitted.get("id")
        ctx = submitted.get("contextId", "")
        if not task_id:
            text = extract_a2a_text(submitted)
            return json.dumps({"context_id": ctx, "result": text}, ensure_ascii=False)
        for _ in range(120):
            result = await client.get_task(task_id)
            if result.get("status", {}).get("state") not in ("working", "submitted", "input-required"):
                text = extract_a2a_text(result)
                return json.dumps({"task_id": task_id, "context_id": ctx, "result": text}, ensure_ascii=False)
            await asyncio.sleep(1)
        return f"{TOOL_ERROR}: A2A 任务超时;可用 a2a_get_task 查询 task_id={task_id}"
    except Exception as exc:
        return f"{TOOL_ERROR}: A2A Agent '{agent_name}' 调用失败: {exc}"


@tool
async def a2a_add_agent(agent_name: str, url: str, token: str = "") -> str:
    """添加或更新一个可供调用的外部 A2A Agent。

    Args:
        agent_name: 本地使用的唯一 Agent 名称。
        url: 对方 A2A 服务 URL、服务根 URL 或 Agent Card URL。
        token: 可选的 Bearer Token;局域网 UniClaw A2A 服务通常需要它。
    """
    try:
        card = await A2AClient(url, token).get_card()
        await A2AManager.get_instance().add_agent(agent_name, url, token, card=card)
        summary = {"name": card.get("name", agent_name), "description": card.get("description", "")}
        if card.get("skills"):
            summary["skills"] = [
                {"name": s.get("name", ""), "description": s.get("description", "")}
                for s in card["skills"]
            ]
        if card.get("capabilities"):
            summary["capabilities"] = card["capabilities"]
        return f"已添加 A2A Agent '{agent_name}':\n{json.dumps(summary, ensure_ascii=False, indent=2)}"
    except Exception as exc:
        return f"{TOOL_ERROR}: 无法添加 A2A Agent: {exc}"


@tool
async def a2a_list_agents() -> str:
    """列出已经配置、可供 UniClaw 调用的外部 A2A Agent。"""
    agents = await A2AManager.get_instance().list_agents()
    if not agents:
        return "尚未配置外部 A2A Agent。"
    lines = []
    for item in agents:
        card = item.get("card", {})
        desc = card.get("description", "")
        line = f"- {item['name']}: {item['url']}"
        if desc:
            line += f"\n  描述: {desc}"
        skills = card.get("skills")
        if skills:
            skill_strs = [s.get("name", "") for s in skills if s.get("name")]
            if skill_strs:
                line += f"\n  技能: {', '.join(skill_strs)}"
        lines.append(line)
    return "\n".join(lines)


@tool
async def a2a_submit_task(agent_name: str, message: str, context_id: str = "", tool_runtime: ToolRuntime = None) -> str:
    """异步提交远程 A2A 任务,立即返回 task_id 和 context_id;传入 context_id 可继续多轮对话。

    Args:
        agent_name: 已配置的远程 A2A Agent 名称。
        message: 本轮消息或任务说明。
        context_id: 可选的前一轮返回 context_id,用于继续同一对话。
    """
    config = tool_runtime.config
    remote = await A2AManager.get_instance().get_agent(agent_name)
    if not remote:
        return f"{TOOL_ERROR}: 未找到 A2A Agent '{agent_name}'。"
    try:
        client = A2AClient(remote["url"], remote.get("token", ""))
        result = await client.send_message(message, context_id)
        _start_task_watcher(agent_name, result, client, config)
        return json.dumps({"task_id": result.get("id"), "context_id": result.get("contextId"), "state": result.get("status", {}).get("state")}, ensure_ascii=False)
    except Exception as exc:
        return f"{TOOL_ERROR}: A2A 任务提交失败: {exc}"


@tool
async def a2a_get_task(agent_name: str, task_id: str) -> str:
    """查询异步 A2A 任务状态及完成结果。

    Args:
        agent_name: 已配置的远程 A2A Agent 名称。
        task_id: a2a_submit_task 返回的任务 ID。
    """
    remote = await A2AManager.get_instance().get_agent(agent_name)
    if not remote:
        return f"{TOOL_ERROR}: 未找到 A2A Agent '{agent_name}'。"
    try:
        result = await A2AClient(remote["url"], remote.get("token", "")).get_task(task_id)
        return json.dumps({"task_id": result.get("id"), "context_id": result.get("contextId"), "state": result.get("status", {}).get("state"), "result": extract_a2a_text(result)}, ensure_ascii=False)
    except Exception as exc:
        return f"{TOOL_ERROR}: A2A 任务查询失败: {exc}"


@tool
async def a2a_cancel_task(agent_name: str, task_id: str) -> str:
    """取消仍在执行的异步 A2A 任务。

    Args:
        agent_name: 已配置的远程 A2A Agent 名称。
        task_id: 要取消的任务 ID。
    """
    remote = await A2AManager.get_instance().get_agent(agent_name)
    if not remote:
        return f"{TOOL_ERROR}: 未找到 A2A Agent '{agent_name}'。"
    try:
        result = await A2AClient(remote["url"], remote.get("token", "")).cancel_task(task_id)
        return f"A2A 任务 {result.get('id', task_id)} 状态:{result.get('status', {}).get('state', 'unknown')}"
    except Exception as exc:
        return f"{TOOL_ERROR}: A2A 任务取消失败: {exc}"


def get_tools() -> list:
    return [a2a_send_task, a2a_add_agent, a2a_list_agents, a2a_submit_task, a2a_get_task, a2a_cancel_task]


def get_all_tools() -> list:
    return get_tools()
