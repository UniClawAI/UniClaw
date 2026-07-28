from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from uniclaw.tools.memory.consolidate import consolidate_session
from uniclaw.tools.memory.memory import Memory

if TYPE_CHECKING:
    from uniclaw.config import AppConfig

REVIEW_INTERVAL_MESSAGES = 10


async def review_and_save_if_due(config: AppConfig) -> list[Memory]:
    """每 10 条用户消息自动回顾一次会话,提取值得长期保存的记忆。

    通过比较当前消息数与上次回顾时的消息数,判断是否达到回顾间隔。
    达到间隔时,将会话交给 consolidate_session 分析,
    由 LLM 提取有价值的信息写入持久化记忆。

    Args:
        config: 应用配置,传递给 consolidate_session

    Returns:
        本次回顾保存的记忆列表,未达到间隔时返回空列表
    """
    task = config.current_agent
    current_count = len(task.session)
    last_reviewed = int(getattr(task, "memory_review_user_count", 0) or 0)

    if current_count - last_reviewed < REVIEW_INTERVAL_MESSAGES:
        return []

    start = max(last_reviewed - REVIEW_INTERVAL_MESSAGES // 2, 0)
    memories = await consolidate_session(task.session[start:], config)
    setattr(task, "memory_review_user_count", current_count)
    return memories
