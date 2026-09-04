"""memory_search BM25 搜索测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.memory.memory import Memory
from uniclaw.tools.memory.tools import memory_search, memory_save
from uniclaw.utils.tokenize import tokenize as _tokenize


def _make_config(tmp_path):
    """创建模拟 config。"""
    return SimpleNamespace(
        current_agent=SimpleNamespace(),
        root_dir=tmp_path,
        mini_model_name="test-mini",
        spinner=SimpleNamespace(start=lambda *a, **k: None, stop=lambda **k: None),
    )


def _seed_memories(tmp_path, mock_config):
    """在 tmp_path 下创建几条测试记忆。"""
    memories = [
        ("python-style", "Python 代码风格规范", "使用 4 空格缩进,行宽 88 字符", "user"),
        ("git-workflow", "Git 工作流", "使用 rebase 而非 merge 保持历史整洁", "user"),
        ("api-endpoint", "API 端点配置", "生产环境使用 /v2/api 路由", "project"),
        ("数据库连接", "数据库连接池配置", "最大连接数 20,超时 30 秒", "project"),
    ]
    for name, desc, content, scope in memories:
        memory_save.func(
            name=name,
            description=desc,
            content=content,
            scope=scope,
            tool_runtime=ToolRuntime(config=mock_config),
        )


# ── 分词测试 ──────────────────────────────────────────────


def test_tokenize_mixed():
    """中英文混合分词。"""
    tokens = _tokenize("Python 代码风格")
    assert "python" in tokens
    # jieba 应该切出"代码"和"风格"
    assert "代码" in tokens
    assert "风格" in tokens


def test_tokenize_english():
    """纯英文分词。"""
    tokens = _tokenize("Git workflow rebase")
    assert "git" in tokens
    assert "workflow" in tokens
    assert "rebase" in tokens


def test_tokenize_chinese():
    """纯中文分词。"""
    tokens = _tokenize("数据库连接池配置")
    assert "数据库" in tokens or "数据" in tokens
    assert "连接" in tokens or "连接池" in tokens


# ── BM25 搜索测试 ────────────────────────────────────────


def test_bm25_partial_match(monkeypatch, tmp_path):
    """BM25 能匹配非精确子串的查询。"""
    monkeypatch.setattr(
        "uniclaw.tools.memory.memory.get_app_dir",
        lambda scope: tmp_path / scope,
    )
    mock_config = _make_config(tmp_path)
    _seed_memories(tmp_path, mock_config)

    # "Python 风格" 不是任何记忆的精确子串,但 BM25 应该能匹配到 "python-style"
    with patch(
        "uniclaw.tools.memory.tools.ai_select_memories",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = asyncio.run(
            memory_search.func(query="Python 风格", max_results=5, tool_runtime=ToolRuntime(config=mock_config))
        )

    assert "python-style" in result


def test_bm25_chinese_query(monkeypatch, tmp_path):
    """中文查询通过 jieba 分词匹配。"""
    monkeypatch.setattr(
        "uniclaw.tools.memory.memory.get_app_dir",
        lambda scope: tmp_path / scope,
    )
    mock_config = _make_config(tmp_path)
    _seed_memories(tmp_path, mock_config)

    # "数据库" 应该匹配到 "数据库连接"
    with patch(
        "uniclaw.tools.memory.tools.ai_select_memories",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = asyncio.run(
            memory_search.func(query="数据库连接", max_results=5, tool_runtime=ToolRuntime(config=mock_config))
        )

    assert "数据库连接" in result


def test_bm25_no_match(monkeypatch, tmp_path):
    """无关查询不返回结果。"""
    monkeypatch.setattr(
        "uniclaw.tools.memory.memory.get_app_dir",
        lambda scope: tmp_path / scope,
    )
    mock_config = _make_config(tmp_path)
    _seed_memories(tmp_path, mock_config)

    with patch(
        "uniclaw.tools.memory.tools.ai_select_memories",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = asyncio.run(
            memory_search.func(query="量子计算", max_results=5, tool_runtime=ToolRuntime(config=mock_config))
        )

    assert "未找到" in result


def test_bm25_ranking_prefers_high_confidence(monkeypatch, tmp_path):
    """高置信度记忆在 BM25 分数相近时排名更靠前。"""
    monkeypatch.setattr(
        "uniclaw.tools.memory.memory.get_app_dir",
        lambda scope: tmp_path / scope,
    )
    mock_config = _make_config(tmp_path)

    # 创建两条内容相似但置信度不同的记忆
    memory_save.func(
        name="test-low-conf",
        description="代码风格",
        content="代码规范和风格指南",
        scope="user",
        confidence=0.3,
        tool_runtime=ToolRuntime(config=mock_config),
    )
    memory_save.func(
        name="test-high-conf",
        description="代码风格",
        content="代码规范和风格指南",
        scope="user",
        confidence=1.0,
        tool_runtime=ToolRuntime(config=mock_config),
    )

    with patch(
        "uniclaw.tools.memory.tools.ai_select_memories",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = asyncio.run(
            memory_search.func(query="代码风格", max_results=5, tool_runtime=ToolRuntime(config=mock_config))
        )

    # 高置信度应该排在前面
    high_pos = result.find("test-high-conf")
    low_pos = result.find("test-low-conf")
    assert high_pos < low_pos


def test_bm25_ai_fallback(monkeypatch, tmp_path):
    """BM25 结果不足时调用 AI 搜索补充。"""
    monkeypatch.setattr(
        "uniclaw.tools.memory.memory.get_app_dir",
        lambda scope: tmp_path / scope,
    )
    mock_config = _make_config(tmp_path)
    _seed_memories(tmp_path, mock_config)

    # BM25 找不到"量子计算",AI 应该被调用
    mock_ai = AsyncMock(return_value=[])
    with patch("uniclaw.tools.memory.tools.ai_select_memories", mock_ai):
        asyncio.run(
            memory_search.func(query="量子计算", max_results=5, tool_runtime=ToolRuntime(config=mock_config))
        )

    mock_ai.assert_called_once()
