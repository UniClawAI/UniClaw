"""todolist/goal.py evaluate_goal 的 JSON 容错解析测试。

mock 掉 provider.achat,只测解析分支:
JSON 正常 / markdown 包裹 / 非 JSON 容错 / 空输出 / 异常放行。
"""

from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, patch

from uniclaw.tools.todolist.goal import GoalStatus, evaluate_goal
from uniclaw.tools.todolist.overseer import OverseerManager


def _make_config() -> SimpleNamespace:
    return SimpleNamespace(mini_model_name="mini-model")


@pytest.fixture(autouse=True)
def mock_achat():
    """默认 patch 掉 achat,每个用例自行设置返回值。"""
    with patch("uniclaw.provider.fallback.achat", new_callable=AsyncMock) as m:
        yield m


def _set_reply(mock_achat, content: str) -> None:
    """设置 achat 返回固定 content 的 AIMessage 形状对象。"""
    mock_achat.side_effect = None
    mock_achat.return_value = SimpleNamespace(content=content)


class TestEvaluateGoalParsing:
    """evaluate_goal 返回值解析分支。"""

    async def test_achieved(self, mock_achat):
        _set_reply(mock_achat, '{"status": "achieved", "reason": "已完成"}')
        status, reason = await evaluate_goal("目标", "对话", _make_config())
        assert status == GoalStatus.ACHIEVED
        assert reason == "已完成"

    async def test_waiting(self, mock_achat):
        _set_reply(mock_achat, '{"status": "waiting", "reason": "后台下载中"}')
        status, reason = await evaluate_goal("目标", "对话", _make_config())
        assert status == GoalStatus.WAITING
        assert reason == "后台下载中"

    async def test_not_achieved(self, mock_achat):
        _set_reply(mock_achat, '{"status": "not_achieved", "reason": "还有测试没跑"}')
        status, reason = await evaluate_goal("目标", "对话", _make_config())
        assert status == GoalStatus.NOT_ACHIEVED

    async def test_json_in_markdown_block(self, mock_achat):
        """markdown 代码块包裹的 JSON 也能解析。"""
        _set_reply(mock_achat, '```json\n{"status": "achieved", "reason": "ok"}\n```')
        status, _ = await evaluate_goal("目标", "对话", _make_config())
        assert status == GoalStatus.ACHIEVED

    async def test_empty_output_defaults_not_achieved(self, mock_achat):
        """judge 无输出时保守判为未达成。"""
        _set_reply(mock_achat, "")
        status, reason = await evaluate_goal("目标", "对话", _make_config())
        assert status == GoalStatus.NOT_ACHIEVED
        assert "无输出" in reason

    async def test_non_json_with_achieved_keyword(self, mock_achat):
        """非 JSON 但含 achieved 关键字时容错判定。"""
        _set_reply(mock_achat, "任务已 achieved,一切正常")
        status, reason = await evaluate_goal("目标", "对话", _make_config())
        assert status == GoalStatus.ACHIEVED
        assert "非 JSON" in reason

    async def test_non_json_without_keyword(self, mock_achat):
        _set_reply(mock_achat, "随便说点什么")
        status, reason = await evaluate_goal("目标", "对话", _make_config())
        assert status == GoalStatus.NOT_ACHIEVED

    async def test_unknown_status_defaults_not_achieved(self, mock_achat):
        """status 字段值非法时归为 not_achieved。"""
        _set_reply(mock_achat, '{"status": "whatever", "reason": "r"}')
        status, _ = await evaluate_goal("目标", "对话", _make_config())
        assert status == GoalStatus.NOT_ACHIEVED

    async def test_exception_defaults_achieved(self, mock_achat):
        """judge 调用异常时默认放行,不阻塞 agent。"""
        mock_achat.side_effect = RuntimeError("网络错误")
        status, reason = await evaluate_goal("目标", "对话", _make_config())
        assert status == GoalStatus.ACHIEVED
        assert "默认放行" in reason


class TestOverseerManager:
    """OverseerManager 开关状态。"""

    def test_initial_inactive(self):
        om = OverseerManager()
        assert om.active is False

    def test_start_stop(self):
        om = OverseerManager()
        om.start()
        assert om.active is True
        om.stop()
        assert om.active is False
