"""tools/session/notes.py 测试 — 会话笔记工具(原本 28% 覆盖)。

覆盖增删改查四个操作 + 重名检查 + 空笔记 + get_notes_system_prompt。
SessionNote 用真实数据类,会话用 MagicMock 隔离。
"""

import pytest
from unittest.mock import MagicMock

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.session.session import SessionNote
from uniclaw.tools.session.notes import (
    session_note_add,
    session_note_update,
    session_note_delete,
    session_note_list,
    session_note_get,
    get_tools,
    get_all_tools,
    get_notes_system_prompt,
)
from uniclaw.utils.constants import TOOL_ERROR


def _session(notes=None):
    """构造带 session_notes 列表的 session。"""
    s = MagicMock()
    s.session_notes = notes if notes is not None else []
    return s


def _rt(session):
    config = MagicMock()
    config.current_agent = MagicMock()
    config.current_agent.session = session
    return ToolRuntime(config=config)


def _note(name="n1", description="d1", content="c1"):
    return SessionNote(name=name, description=description, content=content)


class TestGetTools:
    def test_registered(self):
        assert get_tools() == [
            session_note_add,
            session_note_update,
            session_note_delete,
            session_note_list,
            session_note_get,
        ]
        assert get_all_tools() == get_tools()


class TestSessionNoteAdd:
    @pytest.mark.asyncio
    async def test_add_success(self):
        session = _session()
        result = await session_note_add.func(
            name="配置", description="数据库配置", content="host=localhost",
            tool_runtime=_rt(session),
        )
        assert "已添加笔记 '配置'" in result
        assert len(session.session_notes) == 1
        note = session.session_notes[0]
        assert note.name == "配置"
        assert note.content == "host=localhost"

    @pytest.mark.asyncio
    async def test_add_duplicate_name_rejected(self):
        session = _session([_note(name="dup")])
        result = await session_note_add.func(
            name="dup", description="x", content="y", tool_runtime=_rt(session),
        )
        assert TOOL_ERROR in result
        assert "已存在名为 'dup'" in result
        assert len(session.session_notes) == 1  # 未新增


class TestSessionNoteUpdate:
    @pytest.mark.asyncio
    async def test_update_description_and_content(self):
        note = _note(content="old", description="oldd")
        session = _session([note])
        result = await session_note_update.func(
            name="n1", description="新描述", content="新内容",
            tool_runtime=_rt(session),
        )
        assert "已更新笔记 'n1'" in result
        assert "描述" in result and "内容" in result
        assert note.description == "新描述"
        assert note.content == "新内容"

    @pytest.mark.asyncio
    async def test_update_only_description(self):
        note = _note(content="keep")
        session = _session([note])
        result = await session_note_update.func(
            name="n1", description="新描述", content="", tool_runtime=_rt(session),
        )
        assert "已更新笔记 'n1'" in result
        assert "描述" in result
        assert "内容" not in result  # 只更新描述
        assert note.content == "keep"  # 内容未变

    @pytest.mark.asyncio
    async def test_update_no_fields(self):
        session = _session([_note()])
        result = await session_note_update.func(
            name="n1", description="", content="", tool_runtime=_rt(session),
        )
        assert "未更新任何字段" in result

    @pytest.mark.asyncio
    async def test_update_not_found(self):
        session = _session([])
        result = await session_note_update.func(
            name="missing", description="x", content="y", tool_runtime=_rt(session),
        )
        assert TOOL_ERROR in result
        assert "未找到名为 'missing'" in result


class TestSessionNoteDelete:
    @pytest.mark.asyncio
    async def test_delete_success(self):
        notes = [_note(), _note(name="n2")]
        session = _session(notes)
        result = await session_note_delete.func(
            name="n1", tool_runtime=_rt(session),
        )
        assert "已删除笔记 'n1'" in result
        assert len(session.session_notes) == 1
        assert session.session_notes[0].name == "n2"

    @pytest.mark.asyncio
    async def test_delete_not_found(self):
        session = _session([])
        result = await session_note_delete.func(
            name="missing", tool_runtime=_rt(session),
        )
        assert TOOL_ERROR in result
        assert "未找到名为 'missing'" in result


class TestSessionNoteList:
    def test_empty(self):
        session = _session([])
        result = session_note_list.func(tool_runtime=_rt(session))
        assert "当前会话没有任何笔记" in result

    def test_lists_summaries(self):
        session = _session([_note(name="n1", description="desc1", content="secret content")])
        result = session_note_list.func(tool_runtime=_rt(session))
        assert "会话笔记(共 1 条)" in result
        assert "n1" in result
        assert "desc1" in result  # 显示摘要
        assert "secret content" not in result  # 不显示完整内容


class TestSessionNoteGet:
    def test_get_success(self):
        session = _session([_note(name="n1", description="d", content="full content")])
        result = session_note_get.func(name="n1", tool_runtime=_rt(session))
        assert "n1" in result
        assert "full content" in result

    def test_get_not_found(self):
        session = _session([])
        result = session_note_get.func(name="missing", tool_runtime=_rt(session))
        assert TOOL_ERROR in result
        assert "未找到名为 'missing'" in result


class TestNotesSystemPrompt:
    def test_mentions_tools(self):
        prompt = get_notes_system_prompt()
        assert "# 会话笔记" in prompt
        assert session_note_add.name in prompt
        assert "持久化" in prompt
        assert "压缩" in prompt