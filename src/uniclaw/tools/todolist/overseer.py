"""监工模式(Overseer Mode)

监工模式下:
1. 标记任务完成需说明做了什么,由子代理审核是否真正完成
2. 重建清单需说明原清单哪里不合理、新清单做了哪些改进,由子代理审核
3. Agent 试图结束时,如有未完成任务则被督促继续
"""

from uniclaw.config import AppConfig


class OverseerManager:
    """监工模式管理器,每个 TodoList 实例持有独立的 OverseerManager。"""

    def __init__(self):
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self):
        self._active = True

    def stop(self):
        self._active = False


async def _run_reviewer(prompt: str, config: AppConfig) -> tuple[bool, str]:
    """启动审核子代理并等待结果。返回 (passed, reason)。"""
    from uniclaw.agent import MultiAgent, AgentStatus
    from dataclasses import replace
    from uniclaw.tools.multi_agent.sub_agent import load_agent_definitions

    try:
        mgr = MultiAgent.get_instance()
        sub_config = config.create_sub_config(name="overseer-reviewer", prompt=prompt)
        root_dir = config.root_dir
        agent_defs = load_agent_definitions(root_dir)
        reviewer_def = agent_defs.get("reviewer")
        if not reviewer_def.model_name:
            reviewer_def = replace(
                reviewer_def,
                model_name=config.mini_model_name[0] if config.mini_model_name else "",
            )
        task = await mgr.start_sub_agent(
            user_message=prompt,
            system_prompt="你是一个严格的审核员。只回复 PASS 或 FAIL:<原因>,不要多说。",
            config=sub_config,
            agent_def=reviewer_def,
        )

        if task.status == AgentStatus.FAILED:
            return False, f"审核子代理启动失败: {task.result}"

        await mgr.wait(task.id, timeout=300)

        # 检查任务是否真正完成
        if task.status == AgentStatus.FAILED:
            return False, f"审核子代理执行失败: {task.result}"
        if task.status not in (AgentStatus.COMPLETED,):
            return False, f"审核子代理超时或未完成(状态: {task.status})"

        text = (task.result or "").strip()

        if not text:
            return False, "审核子代理无输出"

        # 从返回结果中提取 PASS/FAIL
        # 格式: [智能体:overseer-reviewer (reviewer)]\n\nPASS/FAIL:xxx
        # 取最后一行非空内容
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        last = lines[-1] if lines else ""

        if "PASS" in last:
            return True, ""
        if "FAIL" in last:
            reason = last.split("FAIL", 1)[-1].lstrip(":").strip()
            return False, reason or "审核未通过"
        return False, f"审核子代理返回非预期内容: {last[:200]}"

    except Exception as e:
        return False, f"审核异常: {e}"


async def verify_completion(task_content: str, config: AppConfig) -> tuple[bool, str]:
    """用子代理审核任务是否真的完成。

    Args:
        task_content: 刚标记为 completed 的任务描述
        config: 应用配置

    Returns:
        (passed, reason): passed=True 表示审核通过,reason 为不通过原因
    """
    prompt = (
        f"你是一个严格的任务审核员。请验证以下任务是否真正完成。\n\n"
        f"任务: {task_content}\n\n"
        f"请检查:\n"
        f"1. 相关代码/文件是否确实被修改\n"
        f"2. 修改是否正确实现了任务要求\n"
        f"3. 是否有遗漏或错误\n\n"
        f"如果任务确实完成,回复: PASS\n"
        f"如果任务未完成或完成质量不达标,回复: FAIL:<具体原因>\n"
        f"只回复 PASS 或 FAIL:<原因>,不要多说。"
    )
    return await _run_reviewer(prompt, config)


async def verify_modification(
    action: str,
    old_items: list[str],
    new_items: list[str],
    reason: str,
    config: AppConfig,
) -> tuple[bool, str]:
    """用子代理审核 TodoList 修改是否合理。

    Args:
        action: 操作类型,如 "重建清单" / "清空清单"
        old_items: 修改前的任务列表(可为空)
        new_items: 修改后的任务列表(可为空)
        reason: agent 给出的修改理由
        config: 应用配置

    Returns:
        (passed, fail_reason)
    """
    old_text = "\n".join(f"- {item}" for item in old_items) if old_items else "(空)"
    new_text = "\n".join(f"- {item}" for item in new_items) if new_items else "(空)"

    prompt = (
        f"你是一个任务规划审核员。Agent 想要修改任务清单,请审核是否合理。\n\n"
        f"操作: {action}\n"
        f"修改理由: {reason}\n\n"
        f"修改前:\n{old_text}\n\n"
        f"修改后:\n{new_text}\n\n"
        f"请判断:\n"
        f"1. 理由中是否说明了原清单哪里不合理\n"
        f"2. 理由中是否说明了新清单做了哪些改进\n"
        f"3. 改进是否针对原清单的问题(而非无理由重写)\n"
        f"4. 新清单是否完整、可执行,有无遗漏重要步骤\n\n"
        f"如果修改合理,回复: PASS\n"
        f"如果修改不合理,回复: FAIL:<具体原因>\n"
        f"只回复 PASS 或 FAIL:<原因>,不要多说。"
    )
    return await _run_reviewer(prompt, config)
