"""会话管理工具测试 — 覆盖 session_* 工具和 recall_history / get_history_range。"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from uniclaw.tools.session.tools import (
    session_list,
    session_detail,
    session_delete,
    session_update_title,
    get_tools,
    get_all_tools,
)
from uniclaw.tools.session.recall import (
    recall_history,
    get_history_range,
    get_recall_system_prompt,
    get_tools as recall_get_tools,
)
from uniclaw.tools.session.session import Session, UserMessage, AIMessage


def _make_config(session: Session = None) -> MagicMock:
    """构造带 session 的 mock config。"""
    config = MagicMock()
    config.is_webui = False
    config.root_dir = None
    if session is not None:
        agent = MagicMock()
        agent.session = session
        config.current_agent = agent
    return config


def _make_nonempty_session(title: str = "会话") -> Session:
    """构造消息非空的 Session(Session.__len__ 使空消息为 falsy)。"""
    s = Session(title=title)
    s._messages.append(UserMessage(content="初始消息"))
    s.history.append(UserMessage(content="初始消息"))
    return s


def _make_session() -> Session:
    """构造带已归档历史的 Session。"""
    s = Session(title="测试会话")
    s.history = [
        UserMessage(content="第一条:数据库迁移"),
        AIMessage(content="我建议使用 migration 工具"),
        UserMessage(content="数据库连接报错"),
        AIMessage(content="已经修复"),
        UserMessage(content="下一步"),
    ]
    s._messages = [
        UserMessage(content="[之前的对话摘要] 早期内容已被压缩..."),
        AIMessage(content="已经修复"),
        UserMessage(content="下一步"),
    ]
    s._compact_count = 1
    return s


def _make_compacted_session() -> Session:
    """构造真实 compact(split=6) 之后的 Session 状态。

    compact() 后:_messages = [摘要对(2 条)] + 原消息[6:],history 保持全量不变。
    归档部分 = history[0:6] = #0~#5,当前上下文 = #6~#19。
    """
    s = Session()
    for i in range(10):
        s.add_user_message(content=f"用户问题 {i}: 数据库迁移")
        s.add_assistant_message(content=f"回答 {i}", model_name="gpt-4o", usage={})
    s._messages = [
        UserMessage(content="[之前的对话摘要]\n数据库迁移"),
        AIMessage(content="已阅读之前的对话摘要,继续当前任务。", model_name=""),
    ] + s._messages[6:]
    s._compact_count = 2
    return s


class TestToolRegistration:
    """工具注册测试。"""

    def test_session_tools(self):
        """session 管理工具 4 个。"""
        names = [t.name for t in get_tools()]
        assert len(names) == 4
        assert "session_list" in names
        assert "session_detail" in names
        assert "session_delete" in names
        assert "session_update_title" in names

    def test_recall_tools(self):
        """recall 工具 2 个。"""
        names = [t.name for t in recall_get_tools()]
        assert len(names) == 2
        assert "recall_history" in names
        assert "get_history_range" in names

    def test_get_all_tools_same(self):
        """get_all_tools 与 get_tools 相同。"""
        assert len(get_all_tools()) == len(get_tools())


class TestSessionList:
    """session_list 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.session.tools.SessionManager.list_sessions")
    async def test_empty(self, mock_list):
        """无会话返回提示。"""
        mock_list.return_value = []
        result = await session_list()
        assert "没有找到任何会话历史" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.session.tools.SessionManager.list_sessions")
    async def test_with_sessions(self, mock_list):
        """有会话时返回列表。"""
        mock_list.return_value = [
            {
                "session_id": "s1",
                "title": "测试会话",
                "message_count": 10,
                "start_time": "2026-01-01",
            }
        ]
        result = await session_list()
        assert "测试会话" in result
        assert "s1" in result
        assert "10" in result


class TestSessionDetail:
    """session_detail 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.session.tools.SessionManager.load_session")
    async def test_not_found(self, mock_load):
        """未找到会话。"""
        mock_load.return_value = None
        result = await session_detail("nonexistent")
        assert "未找到会话ID" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.session.tools.SessionManager.load_session")
    async def test_found(self, mock_load):
        """找到会话返回详情。"""
        s = Session(title="详情会话")
        s._messages.append(UserMessage(content="你好"))
        s._messages.append(AIMessage(content="你好!有什么可以帮你?"))
        mock_load.return_value = s
        result = await session_detail("s1")
        assert "详情会话" in result
        assert "会话ID: s1" in result
        assert "你好" in result


class TestSessionDelete:
    """session_delete 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.session.tools.SessionManager.load_session")
    async def test_not_found(self, mock_load):
        """未找到会话。"""
        mock_load.return_value = None
        result = await session_delete("nonexistent", config=_make_config())
        assert "未找到会话ID" in result

    @pytest.mark.asyncio
    @patch(
        "uniclaw.tools.session.tools.SessionManager.delete_session", return_value=True
    )
    @patch("uniclaw.tools.session.tools.SessionManager.load_session")
    @patch("uniclaw.console.ui.ok", new_callable=AsyncMock)
    async def test_delete_success(self, mock_ok, mock_load, mock_delete):
        """删除成功。"""
        mock_load.return_value = _make_nonempty_session("待删会话")
        result = await session_delete("s1", config=_make_config())
        assert "成功删除会话" in result
        assert "待删会话" in result

    @pytest.mark.asyncio
    @patch(
        "uniclaw.tools.session.tools.SessionManager.delete_session", return_value=False
    )
    @patch("uniclaw.tools.session.tools.SessionManager.load_session")
    @patch("uniclaw.console.ui.err", new_callable=AsyncMock)
    async def test_delete_failure(self, mock_err, mock_load, mock_delete):
        """删除失败。"""
        mock_load.return_value = _make_nonempty_session("待删会话")
        result = await session_delete("s1", config=_make_config())
        assert "删除会话失败" in result


class TestSessionUpdateTitle:
    """session_update_title 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.session.tools.SessionManager.load_session")
    async def test_not_found(self, mock_load):
        """未找到会话。"""
        mock_load.return_value = None
        result = await session_update_title(
            "nonexistent", "新标题", config=_make_config()
        )
        assert "未找到会话ID" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.session.tools.SessionManager.update_title", return_value=True)
    @patch("uniclaw.tools.session.tools.SessionManager.load_session")
    @patch("uniclaw.console.ui.ok", new_callable=AsyncMock)
    async def test_update_success(self, mock_ok, mock_load, mock_update):
        """更新成功。"""
        mock_load.return_value = _make_nonempty_session("旧标题")
        result = await session_update_title("s1", "新标题", config=_make_config())
        assert "成功更新会话标题" in result
        assert "旧标题" in result
        assert "新标题" in result

    @pytest.mark.asyncio
    @patch(
        "uniclaw.tools.session.tools.SessionManager.update_title", return_value=False
    )
    @patch("uniclaw.tools.session.tools.SessionManager.load_session")
    @patch("uniclaw.console.ui.err", new_callable=AsyncMock)
    async def test_update_failure(self, mock_err, mock_load, mock_update):
        """更新失败。"""
        mock_load.return_value = _make_nonempty_session("旧标题")
        result = await session_update_title("s1", "新标题", config=_make_config())
        assert "更新会话标题失败" in result


class TestRecallHistory:
    """recall_history 测试。"""

    @pytest.mark.asyncio
    async def test_empty_keywords(self):
        """空关键词返回提示。"""
        result = await recall_history([], config=_make_config(_make_session()))
        assert "请提供至少一个搜索关键词" in result

    @pytest.mark.asyncio
    async def test_no_archived_messages(self):
        """无归档消息。"""
        s = Session()
        s.history = [UserMessage(content="只有一条")]
        s._messages = [UserMessage(content="只有一条")]
        result = await recall_history(["数据库"], config=_make_config(s))
        assert "没有被压缩的历史消息" in result

    @pytest.mark.asyncio
    async def test_match_found(self):
        """命中关键词。"""
        result = await recall_history(["数据库"], config=_make_config(_make_session()))
        assert "找到" in result
        assert "数据库" in result

    @pytest.mark.asyncio
    async def test_no_match(self):
        """无命中。"""
        result = await recall_history(["xyzabc"], config=_make_config(_make_session()))
        assert "未在历史消息中找到匹配关键词" in result

    @pytest.mark.asyncio
    async def test_multiple_keywords(self):
        """多关键词搜索。"""
        result = await recall_history(
            ["数据库", "migration"], config=_make_config(_make_session())
        )
        assert "找到" in result

    @pytest.mark.asyncio
    async def test_recall_searches_entire_archived(self):
        """真实 compact 后,归档边界正确:最后一条归档消息也能被检索到。

        回归: _count_recent_messages 曾把摘要对的助手消息误计为最近消息,
        导致归档边界错一位(#5 被排除在可搜索范围外)。
        """
        s = _make_compacted_session()
        result = await recall_history(["回答"], context_size=0, config=_make_config(s))
        assert "#5" in result


class TestGetHistoryRange:
    """get_history_range 测试。"""

    @pytest.mark.asyncio
    async def test_empty_history(self):
        """无历史消息。"""
        s = Session()
        result = await get_history_range(0, 10, config=_make_config(s))
        assert "没有历史消息" in result

    @pytest.mark.asyncio
    async def test_valid_range(self):
        """有效范围。"""
        s = Session()
        s.history = [UserMessage(content=f"消息{i}") for i in range(5)]
        result = await get_history_range(0, 3, config=_make_config(s))
        assert "消息0" in result
        assert "消息2" in result

    @pytest.mark.asyncio
    async def test_invalid_range(self):
        """start >= end。"""
        s = Session()
        s.history = [UserMessage(content="x") for _ in range(3)]
        result = await get_history_range(2, 1, config=_make_config(s))
        assert "无效范围" in result

    @pytest.mark.asyncio
    async def test_boundary_clamped(self):
        """越界被修正。"""
        s = Session()
        s.history = [UserMessage(content=f"m{i}") for i in range(3)]
        result = await get_history_range(0, 99, config=_make_config(s))
        assert "m0" in result

    @pytest.mark.asyncio
    async def test_archived_boundary_after_compact(self):
        """真实 compact 后,归档/当前上下文的 ○/● 边界正确。

        回归: split=6 时归档应为 #0~#5(○),当前上下文为 #6+(●)。
        旧代码把摘要助手消息误计为最近消息,边界错一位(#5 显示为 ●)。
        """
        s = _make_compacted_session()
        result = await get_history_range(0, 20, config=_make_config(s))
        lines = [
            ln
            for ln in result.splitlines()
            if ln.startswith("  ○") or ln.startswith("  ●")
        ]
        assert lines[0].startswith("  ○ #0")
        assert lines[5].startswith("  ○ #5")
        assert lines[6].startswith("  ● #6")


class TestCountRecentMessages:
    """_count_recent_messages 单元测试。"""

    def test_after_real_compact(self):
        """真实 compact 后,最近消息数为 len(_messages) - _compact_count。"""
        from uniclaw.tools.session.recall import _count_recent_messages

        s = _make_compacted_session()
        # _messages = 摘要对(2) + 最近(14) = 16,最近消息应为 14
        assert len(s._messages) == 16
        assert _count_recent_messages(s) == 14

    def test_legacy_prefix_scan_fallback(self):
        """头部非标准摘要格式时,回退前缀扫描仍可用。"""
        from uniclaw.tools.session.recall import _count_recent_messages

        s = Session()
        s._compact_count = 0
        s._messages = [
            UserMessage(content="[之前的对话摘要]\n旧摘要"),
            AIMessage(content="回复", model_name=""),
            UserMessage(content="新问题"),
        ]
        assert _count_recent_messages(s) == 2


class TestGetRecallSystemPrompt:
    """get_recall_system_prompt 测试。"""

    def test_no_archived_returns_empty(self):
        """无归档返回空。"""
        s = Session()
        s.history = [UserMessage(content="x")]
        s._messages = [UserMessage(content="x")]
        assert get_recall_system_prompt(s) == ""

    def test_with_archived(self):
        """有归档返回提示。"""
        s = _make_session()
        prompt = get_recall_system_prompt(s)
        assert "历史上下文" in prompt
        assert "recall_history" in prompt
        assert "get_history_range" in prompt
