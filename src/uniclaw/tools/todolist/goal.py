"""Goal / 停止条件系统

用户通过 /goal 设置一个目标描述。当 agent 试图停止时,
用独立的 judge 模型评估对话是否满足目标条件:
- 目标达成 → 允许退出
- 等待后台任务 → 退出并说明原因
- 目标未达成 → 注入原因让 agent 继续工作
- 超过最大重入次数 → 允许退出,防止无限循环
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uniclaw.config import AppConfig


class GoalStatus(StrEnum):
    """目标评估状态。"""

    ACHIEVED = "achieved"  # 目标达成
    WAITING = "waiting"  # 等待后台任务 (sleep_timer)
    NOT_ACHIEVED = "not_achieved"  # 目标未达成


# 默认最大重入次数
DEFAULT_MAX_REENTRY = 3


class GoalManager:
    """目标停止条件管理器,每个 AgentTask 实例持有独立的 GoalManager。"""

    def __init__(self, max_reentry: int = DEFAULT_MAX_REENTRY):
        self._goal: str = ""
        self._reentry_count: int = 0
        self._max_reentry: int = max_reentry

    @property
    def active(self) -> bool:
        """有目标且未被清除时为 True。"""
        return bool(self._goal)

    @property
    def goal(self) -> str:
        return self._goal

    @property
    def reentry_count(self) -> int:
        return self._reentry_count

    @property
    def max_reentry(self) -> int:
        return self._max_reentry

    def set_goal(self, goal: str) -> None:
        """设置目标并重置重入计数。"""
        self._goal = goal.strip()
        self._reentry_count = 0

    def clear_goal(self) -> None:
        """清除目标和重入计数。"""
        self._goal = ""
        self._reentry_count = 0

    def check_reentry(self) -> bool:
        """检查是否还能重入。未超过最大次数返回 True。"""
        return self._reentry_count < self._max_reentry

    def increment_reentry(self) -> None:
        """重入计数 +1。"""
        self._reentry_count += 1

    def reset_reentry(self) -> None:
        """重置重入计数。"""
        self._reentry_count = 0

    def get_status(self) -> str:
        """返回当前 goal 状态的可读文本。"""
        if not self._goal:
            return "当前没有设置目标"
        return (
            f"目标: {self._goal}\n"
            f"重入次数: {self._reentry_count}/{self._max_reentry}"
        )


# ── Judge 评估 ─────────────────────────────────────────────────


async def evaluate_goal(
    goal: str, conversation_text: str, config: AppConfig
) -> tuple[GoalStatus, str]:
    """用 mini 模型判断对话是否达成目标。

    Args:
        goal: 目标描述
        conversation_text: 最近的对话文本
        config: 应用配置

    Returns:
        (status, reason): 状态和原因说明
    """
    from uniclaw.provider.fallback import achat

    judge_prompt = (
        f"你是一个严格的目标评估员。请根据以下对话内容,判断目标状态。\n\n"
        f"目标: {goal}\n\n"
        f"对话内容:\n{conversation_text}\n\n"
        f"请判断:\n"
        f"1. 目标是否已经被充分完成(不是部分完成)\n"
        f"2. 产出是否满足目标的所有要求\n"
        f"3. 是否有明显的遗漏或未完成的部分\n"
        f"4. 是否正在等待后台任务完成(如下载、长任务、已调用 sleep_timer 等待唤醒)\n\n"
        f"严格按以下 JSON 格式回复,不要输出其他内容:\n"
        f'{{"status": "achieved/waiting/not_achieved", "reason": "具体原因"}}'
    )

    from uniclaw.tools.session.session import Session

    session = Session()
    session.add_user_message(judge_prompt)

    try:
        result = await achat(
            system_prompt=(
                "你是一个严格的目标评估员。"
                '严格以 JSON 格式回复: {"status": "achieved/waiting/not_achieved", "reason": str}。'
                "status 只能是 achieved、waiting、not_achieved 之一。"
                "不要输出 JSON 以外的任何内容。"
            ),
            session=session,
            config=config,
            model_name=config.mini_model_name,
            temperature=0.0,
            max_tokens=200,
            tools=None,
            enable_thinking=False,
            thinking=False,
            response_format={"type": "json_object"},
        )
        text = (result.content or "").strip()
        if not text:
            return GoalStatus.NOT_ACHIEVED, "judge 无输出"

        from uniclaw.utils.format import parse_json_from_llm

        data = parse_json_from_llm(text)
        if not data:
            # JSON 解析失败,尝试从原始文本中容错提取
            if "achieved" in text.lower():
                return GoalStatus.ACHIEVED, "judge 返回非 JSON,检测到 achieved"
            return GoalStatus.NOT_ACHIEVED, f"judge 返回非 JSON 内容: {text[:200]}"

        status_str = str(data.get("status", "not_achieved")).strip().lower()
        reason = str(data.get("reason", "")).strip()

        if status_str == "achieved":
            return GoalStatus.ACHIEVED, reason
        elif status_str == "waiting":
            return GoalStatus.WAITING, reason
        else:
            return GoalStatus.NOT_ACHIEVED, reason

    except Exception as e:
        # judge 调用失败时,默认放行(不阻塞 agent)
        return GoalStatus.ACHIEVED, f"judge 调用异常(默认放行): {e}"
