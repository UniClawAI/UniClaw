"""顾问模型工具测试 — 覆盖 advisor_list、_strip_provider、_match_model 和工具注册。"""

import pytest

from uniclaw.tools.base import ToolRuntime
from unittest.mock import MagicMock

from uniclaw.tools.advisor import (
    _strip_provider,
    _match_model,
    advisor_list,
    get_tools,
    get_all_tools,
)


class TestStripProvider:
    """_strip_provider 前缀去除测试。"""

    def test_with_provider(self):
        """有提供商前缀时去除。"""
        assert _strip_provider("openai/gpt-4o") == "gpt-4o"

    def test_without_provider(self):
        """无前缀时原样返回。"""
        assert _strip_provider("gpt-4o") == "gpt-4o"

    def test_multiple_slashes(self):
        """多个斜杠时只分割第一个。"""
        assert _strip_provider("org/model/version") == "model/version"

    def test_empty_string(self):
        """空字符串返回空。"""
        assert _strip_provider("") == ""

    def test_slash_only(self):
        """只有斜杠。"""
        assert _strip_provider("/") == ""


class TestMatchModel:
    """_match_model 模型匹配测试。"""

    def test_exact_match(self):
        """精确全名匹配。"""
        candidates = ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"]
        assert _match_model("openai/gpt-4o", candidates) == "openai/gpt-4o"

    def test_match_without_provider(self):
        """省略前缀匹配。"""
        candidates = ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"]
        assert _match_model("gpt-4o", candidates) == "openai/gpt-4o"

    def test_no_match(self):
        """不匹配返回 None。"""
        candidates = ["openai/gpt-4o"]
        assert _match_model("gpt-4o-mini", candidates) is None

    def test_empty_candidates(self):
        """空候选列表返回 None。"""
        assert _match_model("gpt-4o", []) is None

    def test_multiple_candidates(self):
        """多个候选时返回第一个匹配。"""
        candidates = ["a/model", "b/model"]
        assert _match_model("model", candidates) == "a/model"


class TestAdvisorList:
    """advisor_list 测试。"""

    @pytest.mark.asyncio
    async def test_no_config(self):
        """无配置返回提示。"""
        result = await advisor_list(tool_runtime=ToolRuntime(config=None))
        assert "未配置" in result

    @pytest.mark.asyncio
    async def test_no_large_model(self):
        """未配置顾问模型返回提示。"""
        config = MagicMock()
        config.large_model_name = []
        result = await advisor_list(tool_runtime=ToolRuntime(config=config))
        assert "未配置" in result

    @pytest.mark.asyncio
    async def test_with_models(self):
        """有顾问模型时返回列表。"""
        config = MagicMock()
        config.large_model_name = ["openai/o3", "anthropic/claude-opus-4-20250514"]
        result = await advisor_list(tool_runtime=ToolRuntime(config=config))
        assert "2 个顾问模型" in result
        assert "o3" in result
        assert "claude-opus-4-20250514" in result


class TestAdvisorRegistration:
    """工具注册测试。"""

    def test_get_tools_with_config(self):
        """有配置时返回 3 个工具。"""
        config = MagicMock()
        config.large_model_name = ["openai/o3"]
        result = get_tools(config=config)
        assert len(result) == 3

    def test_get_tools_without_config(self):
        """无配置时返回空列表。"""
        assert get_tools(config=None) == []
        assert get_tools() == []

    def test_get_tools_empty_large_model(self):
        """large_model_name 为空时返回空列表。"""
        config = MagicMock()
        config.large_model_name = []
        assert get_tools(config=config) == []

    def test_get_all_tools_always_returns_three(self):
        """get_all_tools 无条件返回 3 个工具。"""
        assert len(get_all_tools()) == 3
