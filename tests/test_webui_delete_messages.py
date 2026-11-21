"""WebUI 删除消息 API 测试 — 覆盖 checkpoint 回退行为。

行为约定:
- restore_checkpoint=false:仅删除消息,不触碰工作区
- restore_checkpoint=true 且回退成功:先回退 checkpoint,再删除消息
- restore_checkpoint=true 且回退失败(无工作目录/找不到 checkpoint):
  不删除消息,返回 ok=false,避免"消息已删但文件未回退"的不一致
- 无文件改动的 turn 会落时间锚点,删除该消息也能回退
"""

import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch

from uniclaw.agent import AgentTask
from uniclaw.config import AppConfig, ProviderProfile
from uniclaw.tools.session.session import Session, SessionType
from uniclaw.utils.message import MessageRole
from uniclaw.webui.models import MessageDelete
from uniclaw.webui.api import delete_messages


def _make_config(tmp_path: Path) -> AppConfig:
    """构造带 root_dir 会话的 AppConfig。"""
    session = Session(root_dir=tmp_path)
    task = AgentTask(name="main", session=session, prompt="")
    return AppConfig(
        current_agent=task,
        model_name=["test-model"],
        mini_model_name=["test-model"],
        providers={
            "test": ProviderProfile(
                name="test",
                protocol="openai",
                api_key="test",
                base_url="https://api.test.com/v1",
            )
        },
        permission_mode="auto",
        verbose=False,
    )


def _add_msgs(session: Session, n: int, ts_offset: int = 0):
    """按 turn 语义添加消息:每条消息后伴随一次 checkpoint 创建。"""
    from uniclaw.utils.checkpoint import create_checkpoint

    times = []
    for i in range(n):
        ts = int(time.time() * 1000) + i * 10 + ts_offset
        times.append(ts)
        session.add_message(
            MessageRole.USER,
            f"消息{i}",
            created_at=datetime.fromtimestamp(ts / 1000),
        )
    return times


async def _create_checkpoints(root: Path, times: list[int]):
    """为每个 turn 创建 checkpoint(文件模式需先建文件)。"""
    from uniclaw.utils.checkpoint import create_checkpoint

    (root / "a.txt").write_text("v0", encoding="utf-8")
    for ts in times:
        await create_checkpoint(root, "轮", created_at=ts)


class TestDeleteMessagesNoCheckpoint:
    async def test_plain_delete_only(self, tmp_path):
        """restore_checkpoint=false:仅删消息,不动文件。"""
        config = _make_config(tmp_path)
        session = config.current_agent.session
        times = _add_msgs(session, 3)
        await _create_checkpoints(tmp_path, times)
        (tmp_path / "a.txt").write_text("RUNNING", encoding="utf-8")

        body = MessageDelete(from_idx=1, source="messages", restore_checkpoint=False)
        with patch(
            "uniclaw.webui.api.get_or_load_session", new_callable=AsyncMock, return_value=config
        ), patch(
            "uniclaw.webui.api.SessionManager.save_session", new_callable=AsyncMock
        ):
            resp = await delete_messages("sid", body)

        assert resp["ok"] is True
        assert resp["deleted"] == 2  # 消息1、2 被删
        assert resp["checkpoint"] is None
        # 文件保持 RUNNING,未被改动
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "RUNNING"
        # 消息 0 保留
        assert len(session._messages) == 1
        assert "消息0" in session._messages[0].to_content()


class TestDeleteMessagesWithCheckpoint:
    async def test_restore_success_deletes_after(self, tmp_path):
        """restore_checkpoint=true 且回退成功:先回退,再删消息。"""
        import uniclaw.utils.checkpoint as cp

        config = _make_config(tmp_path)
        session = config.current_agent.session
        times = _add_msgs(session, 3)
        await _create_checkpoints(tmp_path, times)
        # agent 改文件,模拟后续操作改变了工作区
        (tmp_path / "a.txt").write_text("AGENT改", encoding="utf-8")

        body = MessageDelete(from_idx=1, source="messages", restore_checkpoint=True)
        with patch(
            "uniclaw.webui.api.get_or_load_session", new_callable=AsyncMock, return_value=config
        ), patch(
            "uniclaw.webui.api.SessionManager.save_session", new_callable=AsyncMock
        ):
            resp = await delete_messages("sid", body)

        # 回退成功,消息被删
        assert resp["ok"] is True
        assert resp["checkpoint"]["ok"] is True
        assert resp["deleted"] == 2
        assert len(session._messages) == 1
        # 工作区回退到消息1 时刻:即消息1 轮 checkpoint 的快照(v0 之后的消息0 轮)
        # 消息1 的 checkpoint 快照保存的是该轮之前的状态 = v0(a.txt 初始内容)
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v0"

    async def test_restore_fail_no_delete(self, tmp_path):
        """回退失败(找不到 checkpoint)→ 不删除消息,返回 ok=false。"""
        config = _make_config(tmp_path)
        session = config.current_agent.session
        # 消息的 created_at 无对应 checkpoint
        times = _add_msgs(session, 2)
        # 不创建任何 checkpoint

        body = MessageDelete(from_idx=0, source="messages", restore_checkpoint=True)
        with patch(
            "uniclaw.webui.api.get_or_load_session", new_callable=AsyncMock, return_value=config
        ):
            resp = await delete_messages("sid", body)

        assert resp["ok"] is False
        assert resp["deleted"] == 0
        assert resp["checkpoint"]["ok"] is False
        # 消息未删
        assert len(session._messages) == 2

    async def test_no_root_dir_fails(self, tmp_path):
        """会话无工作目录 → 回退失败,不删消息。"""
        config = _make_config(tmp_path)
        session = config.current_agent.session
        session.root_dir = None
        _add_msgs(session, 2)

        body = MessageDelete(from_idx=0, source="messages", restore_checkpoint=True)
        with patch(
            "uniclaw.webui.api.get_or_load_session", new_callable=AsyncMock, return_value=config
        ):
            resp = await delete_messages("sid", body)

        assert resp["ok"] is False
        assert resp["deleted"] == 0
        assert resp["checkpoint"]["ok"] is False
        assert "工作目录" in resp["checkpoint"]["message"]
        assert len(session._messages) == 2

    async def test_anchor_turn_can_restore(self, tmp_path):
        """无文件改动的 turn(锚点):删除该消息也能回退。"""
        config = _make_config(tmp_path)
        session = config.current_agent.session
        times = _add_msgs(session, 3)
        await _create_checkpoints(tmp_path, times)
        # 中间 turn(消息1)不改文件 → 落锚点:清掉 a.txt 差异前先记录锚点
        # 先改动再回到原状模拟"无差异 turn"
        (tmp_path / "a.txt").write_text("v0", encoding="utf-8")
        anchor_ts = int(time.time() * 1000) + 999
        from uniclaw.utils.checkpoint import _file_create_anchor

        await _file_create_anchor(tmp_path, "没改文件", created_at=anchor_ts)
        # agent 后续改文件
        (tmp_path / "a.txt").write_text("AGENT改", encoding="utf-8")

        # 删除锚点时刻的消息
        body = MessageDelete(from_idx=1, source="messages", restore_checkpoint=True)
        with patch(
            "uniclaw.webui.api.get_or_load_session", new_callable=AsyncMock, return_value=config
        ), patch(
            "uniclaw.webui.api.SessionManager.save_session", new_callable=AsyncMock
        ):
            resp = await delete_messages("sid", body)

        # 锚点回退应成功(恢复到最近快照 v0)
        assert resp["checkpoint"]["ok"] is True
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v0"

    async def test_history_source_restore(self, tmp_path):
        """source=history 时同样按该列表索引定位并回退。"""
        config = _make_config(tmp_path)
        session = config.current_agent.session
        times = _add_msgs(session, 3)
        await _create_checkpoints(tmp_path, times)
        (tmp_path / "a.txt").write_text("AGENT改", encoding="utf-8")

        body = MessageDelete(from_idx=1, source="history", restore_checkpoint=True)
        with patch(
            "uniclaw.webui.api.get_or_load_session", new_callable=AsyncMock, return_value=config
        ), patch(
            "uniclaw.webui.api.SessionManager.save_session", new_callable=AsyncMock
        ):
            resp = await delete_messages("sid", body)

        assert resp["ok"] is True
        assert resp["checkpoint"]["ok"] is True
        assert len(session._messages) == 1
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v0"