"""Jev 结构化决策模块及各子系统集成测试

覆盖 utils/jev.py 核心模块, 以及 skill/memory/security/rag 的 Jev 集成。
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.utils.jev import (
    BatchResult,
    ChoiceResult,
    JevAPIError,
    JevConfigError,
    NoulResult,
    ScoreResult,
    _classify_answer,
    _parse_choice,
    _parse_noul,
    _parse_score,
    is_available,
)


# ── 辅助工具 ──────────────────────────────────────────────


def _make_noul_answer(noul: float):
    """构造模拟的 NoulAnswer。"""
    return SimpleNamespace(noul=noul)


def _make_choice_answer(choice: str, probabilities: dict, confidence: float):
    """构造模拟的 ChoiceAnswer。"""
    return SimpleNamespace(
        choice=choice, probabilities=probabilities, confidence=confidence
    )


def _make_score_answer(score: float, probabilities: dict, confidence: float):
    """构造模拟的 ScoreAnswer。"""
    return SimpleNamespace(
        score=score, probabilities=probabilities, confidence=confidence
    )


def _make_response(answers: dict):
    """构造模拟的 system_one 响应。"""
    return SimpleNamespace(answers=answers)


def _make_config(
    root_dir=None,
    mini_model_name="gpt-4o-mini",
    embedding_model="text-embedding-3-small",
):
    """创建模拟的 AppConfig。"""
    return SimpleNamespace(
        root_dir=root_dir,
        mini_model_name=mini_model_name,
        embedding_model=embedding_model,
        workspace=[],
        spinner=SimpleNamespace(start=lambda *a, **k: None, stop=lambda **k: None),
        providers={
            "openai": SimpleNamespace(
                api_key="test-key",
                base_url="https://api.openai.com/v1",
            )
        },
        current_agent=SimpleNamespace(
            session=SimpleNamespace(id="test-session-id")
        ),
    )


# ── is_available 测试 ────────────────────────────────────


class TestIsAvailable:
    """is_available 函数测试"""

    def test_available_when_key_set(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
        assert is_available() is True

    def test_not_available_when_key_empty(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "")
        assert is_available() is False

    def test_not_available_when_key_unset(self, monkeypatch):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        assert is_available() is False

    def test_not_available_when_key_whitespace(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "   ")
        assert is_available() is False


# ── 解析函数测试 ──────────────────────────────────────────


class TestParseNoul:
    """_parse_noul 测试"""

    def test_parse_noul(self):
        answer = _make_noul_answer(0.75)
        result = _parse_noul(answer)
        assert isinstance(result, NoulResult)
        assert result.noul == 0.75

    def test_parse_noul_zero(self):
        answer = _make_noul_answer(0.0)
        result = _parse_noul(answer)
        assert result.noul == 0.0

    def test_parse_noul_one(self):
        answer = _make_noul_answer(1.0)
        result = _parse_noul(answer)
        assert result.noul == 1.0


class TestParseChoice:
    """_parse_choice 测试"""

    def test_parse_choice(self):
        answer = _make_choice_answer(
            "safe", {"safe": 0.8, "unsafe": 0.2}, 0.6
        )
        result = _parse_choice(answer)
        assert isinstance(result, ChoiceResult)
        assert result.choice == "safe"
        assert result.probabilities == {"safe": 0.8, "unsafe": 0.2}
        assert result.confidence == 0.6


class TestParseScore:
    """_parse_score 测试"""

    def test_parse_score(self):
        answer = _make_score_answer(75.0, {"low": 0.1, "high": 0.9}, 0.8)
        result = _parse_score(answer)
        assert isinstance(result, ScoreResult)
        assert result.score == 75.0
        assert result.confidence == 0.8


class TestClassifyAnswer:
    """_classify_answer 测试"""

    def test_classify_noul(self):
        answer = _make_noul_answer(0.6)
        result = _classify_answer("q1", answer)
        assert isinstance(result, NoulResult)

    def test_classify_choice(self):
        answer = _make_choice_answer("a", {"a": 1.0}, 1.0)
        result = _classify_answer("q2", answer)
        assert isinstance(result, ChoiceResult)

    def test_classify_score(self):
        answer = _make_score_answer(50.0, {"mid": 1.0}, 1.0)
        result = _classify_answer("q3", answer)
        assert isinstance(result, ScoreResult)

    def test_unknown_type_raises(self):
        answer = SimpleNamespace(unknown_field=42)
        with pytest.raises(JevAPIError, match="未知 answer 类型"):
            _classify_answer("q4", answer)


# ── 数据类测试 ────────────────────────────────────────────


class TestDataclasses:
    """返回值数据类测试"""

    def test_noul_result_frozen(self):
        result = NoulResult(noul=0.5)
        with pytest.raises(AttributeError):
            result.noul = 0.7

    def test_choice_result_frozen(self):
        result = ChoiceResult(
            choice="a", probabilities={"a": 1.0}, confidence=0.9
        )
        with pytest.raises(AttributeError):
            result.choice = "b"

    def test_score_result_frozen(self):
        result = ScoreResult(
            score=80.0, probabilities={"high": 1.0}, confidence=0.9
        )
        with pytest.raises(AttributeError):
            result.score = 90.0

    def test_batch_result(self):
        answers = {
            "q1": NoulResult(noul=0.8),
            "q2": ChoiceResult(
                choice="yes", probabilities={"yes": 0.9}, confidence=0.8
            ),
        }
        result = BatchResult(answers=answers)
        assert len(result.answers) == 2
        assert result.answers["q1"].noul == 0.8


# ── 异常测试 ──────────────────────────────────────────────


class TestExceptions:
    """异常类型测试"""

    def test_jev_config_error(self):
        err = JevConfigError("未配置")
        assert str(err) == "未配置"
        assert isinstance(err, Exception)

    def test_jev_api_error_with_status(self):
        err = JevAPIError("失败", status_code=429)
        assert str(err) == "失败"
        assert err.status_code == 429

    def test_jev_api_error_without_status(self):
        err = JevAPIError("超时")
        assert err.status_code is None


# ── RAG 重排序 Jev 集成测试 ──────────────────────────────


class TestRAGRerankViaJev:
    """RAG _rerank_via_jev 测试"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.rag.select_many")
    async def test_rerank_via_jev_scores(self, mock_select_many):
        """测试 Jev 返回 noul 作为相关性分数"""
        from uniclaw.tools.rag.rag import RAGManager

        mock_select_many.return_value = {
            "0": NoulResult(noul=0.9),
            "1": NoulResult(noul=0.3),
            "2": NoulResult(noul=0.7),
        }

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()

        candidates = [
            {"content": "Python 编程语言"},
            {"content": "天气预报"},
            {"content": "机器学习入门"},
        ]
        scores = await manager._rerank_via_jev("Python", candidates, "")

        assert len(scores) == 3
        assert scores[0] == 0.9
        assert scores[1] == 0.3
        assert scores[2] == 0.7

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.rag.select_many")
    async def test_rerank_via_jev_with_intent(self, mock_select_many):
        """测试带搜索意图时 state 包含 intent"""
        from uniclaw.tools.rag.rag import RAGManager

        mock_select_many.return_value = {
            "0": NoulResult(noul=0.8),
        }

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()

        candidates = [{"content": "Python 教程"}]
        await manager._rerank_via_jev("Python", candidates, "查找编程教程")

        call_kwargs = mock_select_many.call_args
        assert "查找编程教程" in call_kwargs.kwargs.get("state", call_kwargs[1].get("state", ""))

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.rag.select_many")
    async def test_rerank_via_jev_content_truncated(self, mock_select_many):
        """测试候选内容截断到 200 字符"""
        from uniclaw.tools.rag.rag import RAGManager

        long_content = "x" * 500
        mock_select_many.return_value = {"0": NoulResult(noul=0.5)}

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()

        candidates = [{"content": long_content}]
        await manager._rerank_via_jev("query", candidates, "")

        call_kwargs = mock_select_many.call_args
        options = call_kwargs.kwargs.get("options", call_kwargs[1].get("options", {}))
        assert len(options["0"]) == 200

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.rag.select_many")
    async def test_rerank_via_jev_missing_key_defaults_zero(self, mock_select_many):
        """测试 Jev 返回缺少某个 key 时默认为 0"""
        from uniclaw.tools.rag.rag import RAGManager

        mock_select_many.return_value = {
            "0": NoulResult(noul=0.8),
            # 缺少 "1"
        }

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()

        candidates = [
            {"content": "doc1"},
            {"content": "doc2"},
        ]
        scores = await manager._rerank_via_jev("query", candidates, "")

        assert scores[0] == 0.8
        assert scores[1] == 0.0


# ── RAG 重排序 LLM 测试 ──────────────────────────────────


class TestRAGRerankViaLLM:
    """RAG _rerank_via_llm 测试"""

    @pytest.mark.asyncio
    @patch("uniclaw.provider.fallback.achat")
    async def test_rerank_via_llm_success(self, mock_achat):
        """测试 LLM 返回合法分数"""
        from uniclaw.tools.rag.rag import RAGManager

        mock_response = MagicMock()
        mock_response.content = '{"scores": [{"index": 0, "score": 90}, {"index": 1, "score": 30}]}'
        mock_achat.return_value = mock_response

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()

        candidates = [
            {"content": "Python 教程"},
            {"content": "天气预报"},
        ]
        scores = await manager._rerank_via_llm("Python", candidates, "")

        assert scores is not None
        assert len(scores) == 2
        assert abs(scores[0] - 0.9) < 0.01
        assert abs(scores[1] - 0.3) < 0.01

    @pytest.mark.asyncio
    @patch("uniclaw.provider.fallback.achat")
    async def test_rerank_via_llm_returns_none_on_error(self, mock_achat):
        """测试 LLM 调用异常时返回 None"""
        from uniclaw.tools.rag.rag import RAGManager

        mock_achat.side_effect = Exception("API 错误")

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()

        candidates = [{"content": "doc"}]
        scores = await manager._rerank_via_llm("query", candidates, "")

        assert scores is None

    @pytest.mark.asyncio
    @patch("uniclaw.provider.fallback.achat")
    async def test_rerank_via_llm_returns_none_on_mismatch(self, mock_achat):
        """测试 LLM 返回数量不匹配时返回 None"""
        from uniclaw.tools.rag.rag import RAGManager

        mock_response = MagicMock()
        mock_response.content = '{"scores": [{"index": 0, "score": 90}]}'
        mock_achat.return_value = mock_response

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()

        candidates = [
            {"content": "doc1"},
            {"content": "doc2"},
        ]
        scores = await manager._rerank_via_llm("query", candidates, "")

        assert scores is None


# ── RAG 重排序路由测试 ───────────────────────────────────


class TestRAGRerankRouting:
    """RAG _rerank 路由逻辑测试"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.rag.is_available", return_value=True)
    @patch("uniclaw.tools.rag.rag.select_many")
    async def test_jev_success_skips_llm(self, mock_select_many, mock_available):
        """测试 Jev 成功时不调用 LLM"""
        from uniclaw.tools.rag.rag import RAGManager

        mock_select_many.return_value = {
            "0": NoulResult(noul=0.9),
            "1": NoulResult(noul=0.3),
        }

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()
        manager._rerank_via_llm = AsyncMock()

        candidates = [
            {"content": "doc1", "retrieval_channels": ["vector"], "distance": 0.1},
            {"content": "doc2", "retrieval_channels": ["vector"], "distance": 0.5},
        ]
        await manager._rerank("query", candidates, 2)

        manager._rerank_via_llm.assert_not_called()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.rag.is_available", return_value=True)
    @patch("uniclaw.tools.rag.rag.select_many", side_effect=JevAPIError("失败"))
    async def test_jev_failure_falls_back_to_llm(
        self, mock_select_many, mock_available
    ):
        """测试 Jev 失败时回退到 LLM"""
        from uniclaw.tools.rag.rag import RAGManager

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()
        manager._rerank_via_llm = AsyncMock(return_value=[0.8, 0.2])

        candidates = [
            {"content": "doc1", "retrieval_channels": ["vector"], "distance": 0.1},
            {"content": "doc2", "retrieval_channels": ["vector"], "distance": 0.5},
        ]
        await manager._rerank("query", candidates, 2)

        manager._rerank_via_llm.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.rag.is_available", return_value=False)
    async def test_jev_unavailable_skips_to_llm(self, mock_available):
        """测试 Jev 不可用时直接走 LLM"""
        from uniclaw.tools.rag.rag import RAGManager

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()
        manager._rerank_via_llm = AsyncMock(return_value=[0.8, 0.2])

        candidates = [
            {"content": "doc1", "retrieval_channels": ["vector"], "distance": 0.1},
            {"content": "doc2", "retrieval_channels": ["vector"], "distance": 0.5},
        ]
        await manager._rerank("query", candidates, 2)

        manager._rerank_via_llm.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.rag.is_available", return_value=True)
    @patch("uniclaw.tools.rag.rag.select_many")
    async def test_mixed_sorting_with_jev_scores(
        self, mock_select_many, mock_available
    ):
        """测试 Jev 分数与余弦相似度混合排序"""
        from uniclaw.tools.rag.rag import RAGManager

        mock_select_many.return_value = {
            "0": NoulResult(noul=0.3),
            "1": NoulResult(noul=0.9),
        }

        manager = RAGManager.__new__(RAGManager)
        manager.config = _make_config()

        # doc1 余弦相似度高但 Jev 分低, doc2 相反
        candidates = [
            {"content": "doc1", "retrieval_channels": ["vector"], "distance": 0.1},
            {"content": "doc2", "retrieval_channels": ["vector"], "distance": 0.5},
        ]
        result = await manager._rerank("query", candidates, 2)

        # doc2: 0.6 * 0.9 + 0.4 * 0.5 = 0.74
        # doc1: 0.6 * 0.3 + 0.4 * 0.9 = 0.54
        assert result[0]["content"] == "doc2"
        assert result[1]["content"] == "doc1"


# ── Skill 推荐 Jev 集成测试 ──────────────────────────────


def _make_skill(name: str, description: str = ""):
    """构造模拟的 SkillDef。"""
    return SimpleNamespace(
        name=name,
        description=description,
        triggers=[name],
        argument_hint="",
        when_to_use="",
        tools=[],
        file_path=f"/tmp/{name}/skill.md",
        prompt="",
    )


class TestSkillSuggestViaJev:
    """skill _suggest_via_jev 测试"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.skill.tools.select_many")
    async def test_suggest_via_jev_filters_by_threshold(self, mock_select_many):
        """测试 noul > 0.5 才被选中"""
        from uniclaw.tools.skill.tools import _suggest_via_jev

        skill1 = _make_skill("git", "版本控制")
        skill2 = _make_skill("docker", "容器化")
        skill3 = _make_skill("python", "编程语言")

        mock_select_many.return_value = {
            "git": NoulResult(noul=0.8),
            "docker": NoulResult(noul=0.4),
            "python": NoulResult(noul=0.6),
        }

        result = await _suggest_via_jev("管理代码版本", [skill1, skill2, skill3], 10)

        names = [s.name for s in result]
        assert "git" in names
        assert "python" in names
        assert "docker" not in names

    @pytest.mark.asyncio
    @patch("uniclaw.tools.skill.tools.select_many")
    async def test_suggest_via_jev_respects_max_results(self, mock_select_many):
        """测试 max_results 限制返回数量"""
        from uniclaw.tools.skill.tools import _suggest_via_jev

        skills = [_make_skill(f"skill{i}", "desc") for i in range(5)]
        mock_select_many.return_value = {
            f"skill{i}": NoulResult(noul=0.9) for i in range(5)
        }

        result = await _suggest_via_jev("task", skills, 2)
        assert len(result) == 2

    @pytest.mark.asyncio
    @patch("uniclaw.tools.skill.tools.select_many")
    async def test_suggest_via_jev_sorted_by_noul(self, mock_select_many):
        """测试结果按 noul 降序排列"""
        from uniclaw.tools.skill.tools import _suggest_via_jev

        skills = [_make_skill("a"), _make_skill("b"), _make_skill("c")]
        mock_select_many.return_value = {
            "a": NoulResult(noul=0.6),
            "b": NoulResult(noul=0.9),
            "c": NoulResult(noul=0.7),
        }

        result = await _suggest_via_jev("task", skills, 10)
        assert [s.name for s in result] == ["b", "c", "a"]


# ── Memory 搜索 Jev 集成测试 ─────────────────────────────


def _make_memory(name: str, description: str = "", tmp_path: Path | None = None):
    """构造模拟的 Memory 对象。"""
    if tmp_path:
        filename = str(tmp_path / f"{name}.md")
        Path(filename).write_text(f"# {name}", encoding="utf-8")
    else:
        filename = f"{name}.md"
    return SimpleNamespace(
        name=name,
        type="user",
        description=description,
        scope_name="user",
        content="",
        filename=filename,
        confidence=0.9,
        source="user",
    )


class TestMemorySelectViaJev:
    """memory _select_memories_via_jev 测试"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.memory.context.select_many")
    async def test_select_memories_filters_by_threshold(self, mock_select_many, tmp_path):
        """测试 noul > 0.5 才被选中"""
        from uniclaw.tools.memory.context import _select_memories_via_jev

        m1 = _make_memory("m1", "用户偏好", tmp_path)
        m2 = _make_memory("m2", "项目配置", tmp_path)

        mock_select_many.return_value = {
            "m1": NoulResult(noul=0.8),
            "m2": NoulResult(noul=0.3),
        }

        result = await _select_memories_via_jev("用户偏好", [m1, m2], 10)

        names = [r["name"] for r in result]
        assert "m1" in names
        assert "m2" not in names

    @pytest.mark.asyncio
    @patch("uniclaw.tools.memory.context.select_many")
    async def test_select_memories_respects_max_results(self, mock_select_many, tmp_path):
        """测试 max_results 限制返回数量"""
        from uniclaw.tools.memory.context import _select_memories_via_jev

        memories = [_make_memory(f"m{i}", "desc", tmp_path) for i in range(5)]
        mock_select_many.return_value = {
            f"m{i}": NoulResult(noul=0.9) for i in range(5)
        }

        result = await _select_memories_via_jev("query", memories, 2)
        assert len(result) == 2


# ── Skill 推荐路由测试 ───────────────────────────────────


class TestSkillSuggestRouting:
    """skill _suggest_skills 路由逻辑测试"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.skill.tools.is_available", return_value=True)
    @patch("uniclaw.tools.skill.tools.select_many")
    async def test_jev_success_skips_llm(self, mock_select_many, mock_available):
        """测试 Jev 成功时不调用 LLM"""
        from uniclaw.tools.skill.tools import _suggest_skills

        skill = _make_skill("git", "版本控制")
        mock_select_many.return_value = {"git": NoulResult(noul=0.9)}

        config = _make_config()
        result = await _suggest_skills("版本控制", [skill], 10, config)

        assert len(result) == 1
        assert result[0].name == "git"

    @pytest.mark.asyncio
    @patch("uniclaw.tools.skill.tools.is_available", return_value=True)
    @patch(
        "uniclaw.tools.skill.tools.select_many",
        side_effect=JevAPIError("失败"),
    )
    async def test_jev_failure_falls_back_to_llm(
        self, mock_select_many, mock_available
    ):
        """测试 Jev 失败时回退到 LLM"""
        from uniclaw.tools.skill.tools import _suggest_skills

        skill = _make_skill("git", "版本控制")
        config = _make_config()

        with patch(
            "uniclaw.tools.skill.tools._suggest_via_llm",
            new_callable=AsyncMock,
            return_value=[skill],
        ) as mock_llm:
            result = await _suggest_skills("版本控制", [skill], 10, config)
            mock_llm.assert_called_once()
            assert len(result) == 1


# ── Memory 搜索路由测试 ──────────────────────────────────


class TestMemorySelectRouting:
    """memory ai_select_memories 路由逻辑测试"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.memory.context.is_available", return_value=True)
    @patch("uniclaw.tools.memory.context.select_many")
    async def test_jev_success_skips_llm(self, mock_select_many, mock_available, tmp_path):
        """测试 Jev 成功时不调用 LLM"""
        from uniclaw.tools.memory.context import ai_select_memories

        memory = _make_memory("m1", "偏好", tmp_path)
        mock_select_many.return_value = {"m1": NoulResult(noul=0.8)}

        config = _make_config()
        result = await ai_select_memories("偏好", [memory], 10, config)

        assert len(result) == 1

    @pytest.mark.asyncio
    @patch("uniclaw.tools.memory.context.is_available", return_value=True)
    @patch(
        "uniclaw.tools.memory.context.select_many",
        side_effect=JevConfigError("未配置"),
    )
    async def test_jev_failure_falls_back_to_llm(
        self, mock_select_many, mock_available
    ):
        """测试 Jev 失败时回退到 LLM"""
        from uniclaw.tools.memory.context import ai_select_memories

        memory = _make_memory("m1", "偏好")
        config = _make_config()

        with patch(
            "uniclaw.tools.memory.context._select_memories_via_llm",
            new_callable=AsyncMock,
            return_value=[{"name": "m1"}],
        ) as mock_llm:
            result = await ai_select_memories("偏好", [memory], 10, config)
            mock_llm.assert_called_once()


# ── Security Jev 集成测试 ────────────────────────────────


class TestSecurityJevPreCheck:
    """security llm_safe_check Jev 前置快筛测试"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.security.security.is_available", return_value=True)
    @patch("uniclaw.tools.security.security.yes_no")
    @patch("uniclaw.tools.security.security._get_tool_desc", return_value="读取文件")
    @patch("uniclaw.tools.security.tools._load_llm_safe_prompt", return_value="")
    @patch("uniclaw.tools.base.tc_name", return_value="Read")
    @patch("uniclaw.tools.base.tc_args", return_value={"path": "/tmp/test.txt"})
    async def test_jev_safe_skips_llm(
        self, mock_args, mock_name, mock_prompt, mock_desc, mock_yes_no, mock_available
    ):
        """测试 Jev 判断安全时直接放行"""
        from uniclaw.tools.security.security import llm_safe_check

        mock_yes_no.return_value = NoulResult(noul=0.9)

        config = _make_config()
        tc = {"function": {"name": "Read", "arguments": '{"path": "/tmp/test.txt"}'}}

        is_safe, explanation = await llm_safe_check(tc, config)

        assert is_safe is True
        assert "Jev" in explanation

    @pytest.mark.asyncio
    @patch("uniclaw.tools.security.security.is_available", return_value=True)
    @patch("uniclaw.tools.security.security.yes_no")
    @patch("uniclaw.tools.security.security._get_tool_desc", return_value="执行命令")
    @patch("uniclaw.tools.security.tools._load_llm_safe_prompt", return_value="")
    @patch("uniclaw.tools.base.tc_name", return_value="Bash")
    @patch("uniclaw.tools.base.tc_args", return_value={"command": "rm -rf /"})
    async def test_jev_unsafe_falls_through_to_llm(
        self, mock_args, mock_name, mock_prompt, mock_desc, mock_yes_no, mock_available
    ):
        """测试 Jev 判断不安全时继续走 LLM"""
        from uniclaw.tools.security.security import llm_safe_check

        mock_yes_no.return_value = NoulResult(noul=0.2)

        config = _make_config()
        tc = {"function": {"name": "Bash", "arguments": '{"command": "rm -rf /"}'}}

        with patch(
            "uniclaw.provider.fallback.achat",
            new_callable=AsyncMock,
        ) as mock_achat:
            mock_resp = MagicMock()
            mock_resp.content = '{"is_safe": false, "explanation": "危险命令"}'
            mock_achat.return_value = mock_resp

            is_safe, explanation = await llm_safe_check(tc, config)

        # Jev 判断不安全, 继续走 LLM
        assert is_safe is False
        assert "危险" in explanation

    @pytest.mark.asyncio
    @patch("uniclaw.tools.security.security.is_available", return_value=True)
    @patch(
        "uniclaw.tools.security.security.yes_no",
        side_effect=JevAPIError("API 失败"),
    )
    @patch("uniclaw.tools.security.security._get_tool_desc", return_value="操作")
    @patch("uniclaw.tools.security.tools._load_llm_safe_prompt", return_value="")
    @patch("uniclaw.tools.base.tc_name", return_value="Edit")
    @patch("uniclaw.tools.base.tc_args", return_value={"path": "f.txt", "content": "x"})
    async def test_jev_error_falls_through_to_llm(
        self, mock_args, mock_name, mock_prompt, mock_desc, mock_yes_no, mock_available
    ):
        """测试 Jev 异常时回退到 LLM"""
        from uniclaw.tools.security.security import llm_safe_check

        config = _make_config()
        tc = {"function": {"name": "Edit", "arguments": '{"path": "f.txt", "content": "x"}'}}

        with patch(
            "uniclaw.provider.fallback.achat",
            new_callable=AsyncMock,
        ) as mock_achat:
            mock_resp = MagicMock()
            mock_resp.content = '{"is_safe": true, "explanation": "安全"}'
            mock_achat.return_value = mock_resp

            is_safe, explanation = await llm_safe_check(tc, config)

        # Jev 失败后走 LLM, LLM 说安全
        assert is_safe is True

    @pytest.mark.asyncio
    @patch("uniclaw.tools.security.security.is_available", return_value=False)
    @patch("uniclaw.tools.security.security._get_tool_desc", return_value="操作")
    @patch("uniclaw.tools.security.tools._load_llm_safe_prompt", return_value="")
    @patch("uniclaw.tools.base.tc_name", return_value="Read")
    @patch("uniclaw.tools.base.tc_args", return_value={"path": "f.txt"})
    async def test_jev_unavailable_skips_to_llm(
        self, mock_args, mock_name, mock_prompt, mock_desc, mock_available
    ):
        """测试 Jev 不可用时直接走 LLM"""
        from uniclaw.tools.security.security import llm_safe_check

        config = _make_config()
        tc = {"function": {"name": "Read", "arguments": '{"path": "f.txt"}'}}

        with patch(
            "uniclaw.provider.fallback.achat",
            new_callable=AsyncMock,
        ) as mock_achat:
            mock_resp = MagicMock()
            mock_resp.content = '{"is_safe": true, "explanation": "只读安全"}'
            mock_achat.return_value = mock_resp

            is_safe, _ = await llm_safe_check(tc, config)

        assert is_safe is True
        mock_achat.assert_called_once()
