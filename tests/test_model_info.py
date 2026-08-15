"""model_info 模块的测试。"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from uniclaw.utils.model_info import ModelInfo, ModelInfoProvider, get_model_info_provider


# ── 测试数据 ──────────────────────────────────────────────

SAMPLE_MODEL_DATA = {
    "id": "openai/gpt-4o",
    "name": "OpenAI: GPT-4o",
    "description": "GPT-4o is a large multimodal model.",
    "context_length": 128000,
    "architecture": {
        "modality": "text+image->text",
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "tokenizer": "GPT",
    },
    "pricing": {
        "prompt": "0.000005",
        "completion": "0.000015",
        "image": "0.000005",
        "input_cache_read": "0.0000005",
        "input_cache_write": "0.0000025",
    },
    "top_provider": {
        "context_length": 128000,
        "max_completion_tokens": 16384,
        "is_moderated": True,
    },
    "supported_parameters": [
        "temperature",
        "tools",
        "tool_choice",
        "response_format",
        "seed",
    ],
    "reasoning": None,
    "benchmarks": {
        "artificial_analysis": {
            "intelligence_index": 72,
            "coding_index": 78.5,
            "agentic_index": 65.3,
        }
    },
    "knowledge_cutoff": "2024-10",
    "created": 1700000000,
}

SAMPLE_REASONING_MODEL_DATA = {
    "id": "anthropic/claude-3.5-sonnet",
    "name": "Anthropic: Claude 3.5 Sonnet",
    "description": "Claude 3.5 Sonnet is a powerful model.",
    "context_length": 200000,
    "architecture": {
        "modality": "text+image->text",
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
    },
    "pricing": {
        "prompt": "0.000003",
        "completion": "0.000015",
        "image": "0.000003",
        "input_cache_read": "0.0000003",
        "input_cache_write": "0.0000015",
    },
    "top_provider": {
        "max_completion_tokens": 8192,
    },
    "supported_parameters": [
        "temperature",
        "tools",
        "reasoning",
        "reasoning_effort",
    ],
    "reasoning": {
        "mandatory": False,
        "default_enabled": False,
    },
    "benchmarks": {},
    "created": 1700000000,
}

SAMPLE_VIDEO_MODEL_DATA = {
    "id": "google/gemini-2.0-flash",
    "name": "Google: Gemini 2.0 Flash",
    "description": "Gemini 2.0 Flash supports video input.",
    "context_length": 1048576,
    "architecture": {
        "modality": "text+image+video+audio->text",
        "input_modalities": ["text", "image", "video", "audio"],
        "output_modalities": ["text"],
    },
    "pricing": {
        "prompt": "0.0000001",
        "completion": "0.0000004",
    },
    "top_provider": {},
    "supported_parameters": ["tools", "temperature"],
    "reasoning": None,
    "benchmarks": {},
    "created": 1700000000,
}

SAMPLE_API_RESPONSE = {
    "data": [
        SAMPLE_MODEL_DATA,
        SAMPLE_REASONING_MODEL_DATA,
        SAMPLE_VIDEO_MODEL_DATA,
    ]
}


# ── ModelInfo 测试 ────────────────────────────────────────


class TestModelInfo:
    """ModelInfo 数据类测试。"""

    def test_basic_properties(self):
        """测试基本属性。"""
        info = ModelInfo(id="openai/gpt-4o", name="GPT-4o")
        assert info.page_url == "https://openrouter.ai/openai/gpt-4o"
        assert info.short_name == "gpt-4o"
        assert info.provider == "openai"

    def test_short_name_no_slash(self):
        """测试没有斜杠的模型 ID。"""
        info = ModelInfo(id="gpt-4o", name="GPT-4o")
        assert info.short_name == "gpt-4o"
        assert info.provider == ""

    def test_to_dict(self):
        """测试转换为字典。"""
        info = ModelInfo(
            id="openai/gpt-4o",
            name="GPT-4o",
            supports_tools=True,
            supports_vision=True,
        )
        d = info.to_dict()
        assert d["id"] == "openai/gpt-4o"
        assert d["supports_tools"] is True
        assert d["supports_vision"] is True
        assert d["detailed_info_fetched"] is False
        assert "page_url" in d

    def test_to_dict_with_detailed_info(self):
        """测试转换为字典（已获取详细信息）。"""
        info = ModelInfo(
            id="openai/gpt-4o",
            name="GPT-4o",
            supports_tools=True,
            detailed_info_fetched=True,
        )
        d = info.to_dict()
        assert d["detailed_info_fetched"] is True


# ── ModelInfoProvider 测试 ────────────────────────────────


class TestModelInfoProvider:
    """ModelInfoProvider 测试。"""

    @pytest.fixture
    def provider(self):
        """创建一个新的 provider 实例。"""
        return ModelInfoProvider()

    @pytest.fixture
    def mock_api_response(self):
        """模拟 API 响应。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_API_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    async def test_fetch_all_models(self, provider, mock_api_response):
        """测试获取所有模型。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            await provider._ensure_cache()

            assert len(provider._cache) == 3
            assert "openai/gpt-4o" in provider._cache
            assert "anthropic/claude-3.5-sonnet" in provider._cache
            assert "google/gemini-2.0-flash" in provider._cache

    async def test_parse_model_basic_info(self, provider):
        """测试解析模型基本信息。"""
        info = provider._parse_model_data(SAMPLE_MODEL_DATA)
        assert info is not None
        assert info.id == "openai/gpt-4o"
        assert info.name == "OpenAI: GPT-4o"
        assert info.context_length == 128000
        assert info.max_completion_tokens == 16384

    async def test_parse_model_modalities(self, provider):
        """测试解析模型模态信息。"""
        info = provider._parse_model_data(SAMPLE_MODEL_DATA)
        assert info.supports_vision is True
        assert info.supports_video is False
        assert info.supports_audio is False
        assert "image" in info.input_modalities

    async def test_parse_model_tools(self, provider):
        """测试解析模型工具支持。"""
        info = provider._parse_model_data(SAMPLE_MODEL_DATA)
        assert info.supports_tools is True

    async def test_parse_model_pricing(self, provider):
        """测试解析模型价格信息。"""
        info = provider._parse_model_data(SAMPLE_MODEL_DATA)
        assert info.pricing["prompt"] == 0.000005
        assert info.pricing["completion"] == 0.000015
        assert info.pricing["input_cache_read"] == 0.0000005
        assert info.pricing["input_cache_write"] == 0.0000025

    async def test_parse_model_benchmarks(self, provider):
        """测试解析模型 benchmarks。"""
        info = provider._parse_model_data(SAMPLE_MODEL_DATA)
        assert info.intelligence_index == 72
        assert info.coding_index == 78.5
        assert info.agentic_index == 65.3

    async def test_parse_model_reasoning(self, provider):
        """测试解析模型推理能力。"""
        info = provider._parse_model_data(SAMPLE_REASONING_MODEL_DATA)
        assert info.supports_reasoning is True
        assert info.reasoning_info is not None

    async def test_parse_video_model(self, provider):
        """测试解析支持视频的模型。"""
        info = provider._parse_model_data(SAMPLE_VIDEO_MODEL_DATA)
        assert info.supports_vision is True
        assert info.supports_video is True
        assert info.supports_audio is True

    async def test_resolve_model_id_full(self, provider, mock_api_response):
        """测试解析完整模型 ID。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            await provider._ensure_cache()
            result = provider._resolve_model_id("openai/gpt-4o")
            assert result == "openai/gpt-4o"

    async def test_resolve_model_id_short(self, provider, mock_api_response):
        """测试解析短名称。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            await provider._ensure_cache()
            result = provider._resolve_model_id("gpt-4o")
            assert result == "openai/gpt-4o"

    async def test_resolve_model_id_fuzzy(self, provider, mock_api_response):
        """测试模糊匹配。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            await provider._ensure_cache()
            result = provider._resolve_model_id("claude-3.5-sonnet")
            assert result == "anthropic/claude-3.5-sonnet"

    async def test_resolve_model_id_not_found(self, provider, mock_api_response):
        """测试未找到模型。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            await provider._ensure_cache()
            result = provider._resolve_model_id("nonexistent-model")
            assert result is None

    async def test_get_model_info(self, provider, mock_api_response):
        """测试获取模型信息。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            info = await provider.get_model_info("gpt-4o")
            assert info is not None
            assert info.id == "openai/gpt-4o"
            assert info.supports_tools is True

    async def test_get_model_info_not_found(self, provider, mock_api_response):
        """测试获取不存在的模型信息。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            info = await provider.get_model_info("nonexistent")
            assert info is None

    async def test_detailed_info_fetched_default(self, provider):
        """测试 detailed_info_fetched 默认值。"""
        info = provider._parse_model_data(SAMPLE_MODEL_DATA)
        assert info is not None
        assert info.detailed_info_fetched is False

    async def test_get_model_info_auto_fetch_details(self, provider, mock_api_response):
        """测试 get_model_info 自动获取详细信息。"""
        mock_page_info = {
            "model_id": "openai/gpt-4o",
            "og_description": "GPT-4o is a very detailed description from page.",
            "meta_description": "Short description",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            # 模拟 fetch_model_page 返回
            with patch.object(provider, "fetch_model_page", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = mock_page_info

                info = await provider.get_model_info("gpt-4o")
                assert info is not None
                assert info.detailed_info_fetched is True
                # 描述应该被更新为更长的页面描述
                assert info.description == "GPT-4o is a very detailed description from page."

    async def test_get_model_info_already_fetched(self, provider, mock_api_response):
        """测试 get_model_info 已获取详细信息时不再重复获取。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            with patch.object(provider, "fetch_model_page", new_callable=AsyncMock) as mock_fetch:
                # 第一次获取
                mock_fetch.return_value = {"og_description": "Test"}
                info1 = await provider.get_model_info("gpt-4o")
                assert info1 is not None
                assert info1.detailed_info_fetched is True
                assert mock_fetch.call_count == 1

                # 第二次获取，不应该再调用 fetch_model_page
                info2 = await provider.get_model_info("gpt-4o")
                assert info2 is not None
                assert mock_fetch.call_count == 1  # 仍然只调用了一次

    async def test_update_from_page_info(self, provider):
        """测试从页面信息更新模型数据。"""
        info = provider._parse_model_data(SAMPLE_MODEL_DATA)
        assert info is not None

        # 原始描述较短
        original_desc = info.description
        assert len(original_desc) < 100

        page_info = {
            "og_description": "This is a much longer and more detailed description from the page that should replace the short one.",
            "pricing": {
                "prompt": "0.000010",
                "input_cache_read": "0.000001",
            },
        }

        provider._update_from_page_info(info, page_info)

        # 描述应该被更新
        assert info.description == page_info["og_description"]
        # 价格应该被更新
        assert info.pricing["prompt"] == 0.000010
        assert info.pricing["input_cache_read"] == 0.000001
        # 未在 page_info 中的价格应该保持原值
        assert info.pricing["completion"] == 0.000015

    async def test_search_models(self, provider, mock_api_response):
        """测试搜索模型。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            results = await provider.search_models("claude")
            assert len(results) == 1
            assert results[0].id == "anthropic/claude-3.5-sonnet"

    async def test_list_models_with_tools(self, provider, mock_api_response):
        """测试列出支持工具的模型。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            results = await provider.list_models_with_tools()
            assert len(results) == 3  # 所有测试模型都支持工具

    async def test_list_models_with_vision(self, provider, mock_api_response):
        """测试列出支持图片的模型。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            results = await provider.list_models_with_vision()
            assert len(results) == 3  # 所有测试模型都支持图片

    async def test_list_models_with_video(self, provider, mock_api_response):
        """测试列出支持视频的模型。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            results = await provider.list_models_with_video()
            assert len(results) == 1
            assert results[0].id == "google/gemini-2.0-flash"

    async def test_list_models_with_reasoning(self, provider, mock_api_response):
        """测试列出支持推理的模型。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            results = await provider.list_models_with_reasoning()
            assert len(results) == 1
            assert results[0].id == "anthropic/claude-3.5-sonnet"

    async def test_list_models_by_provider(self, provider, mock_api_response):
        """测试按提供商列出模型。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_api_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            results = await provider.list_models_by_provider("openai")
            assert len(results) == 1
            assert results[0].id == "openai/gpt-4o"

    async def test_cache_invalidation(self, provider):
        """测试缓存失效逻辑。"""
        # 设置昨天的缓存
        provider._cache_date = "2020-01-01"
        provider._cache = {"test": MagicMock()}
        assert provider._is_cache_valid() is False

    async def test_clear_cache(self, provider):
        """测试清除缓存。"""
        provider._cache = {"test": MagicMock()}
        provider._short_name_index = {"test": "test"}
        provider._page_cache = {"test": MagicMock()}
        provider._cache_date = "2020-01-01"

        provider.clear_cache()
        assert len(provider._cache) == 0
        assert len(provider._short_name_index) == 0
        assert len(provider._page_cache) == 0
        assert provider._cache_date == ""


# ── 全局单例测试 ──────────────────────────────────────────


class TestGlobalProvider:
    """全局 provider 单例测试。"""

    def test_singleton(self):
        """测试单例模式。"""
        p1 = get_model_info_provider()
        p2 = get_model_info_provider()
        assert p1 is p2


# ── Compaction 集成测试 ────────────────────────────────────


class TestCompactionIntegration:
    """测试 compaction 模块与 model_info 的集成。"""

    async def test_get_context_limit_default(self):
        """测试获取上下文长度（默认值）。"""
        from uniclaw.compaction import get_context_limit, DEFAULT_CONTEXT_LIMIT

        # 测试无模型的默认值
        limit = await get_context_limit(None)
        assert limit == DEFAULT_CONTEXT_LIMIT

        # 测试未知模型（API 获取失败时使用默认值）
        limit = await get_context_limit("unknown-model-xyz")
        assert limit == DEFAULT_CONTEXT_LIMIT

    async def test_get_context_limit_with_api(self):
        """测试获取上下文长度（模拟 API 调用）。"""
        from uniclaw.compaction import get_context_limit
        from unittest.mock import AsyncMock, patch, MagicMock

        # 模拟 model_info 返回
        mock_info = MagicMock()
        mock_info.context_length = 200000

        with patch("uniclaw.utils.model_info.get_model_info_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.get_model_info = AsyncMock(return_value=mock_info)
            mock_get_provider.return_value = mock_provider

            limit = await get_context_limit("gpt-4o")
            assert limit == 200000

    async def test_get_pressure_level(self):
        """测试获取压力等级。"""
        from uniclaw.compaction import get_pressure_level, DEFAULT_CONTEXT_LIMIT

        # 测试低压力 (10k/128k < 50%)
        level = await get_pressure_level(10000, "gpt-4o")
        assert level == -1

        # 测试 level 0 压力 (70k/128k ≈ 55% > 50%)
        level = await get_pressure_level(70000, "gpt-4o")
        assert level == 0

        # 测试 level 1 压力 (100k/128k ≈ 78% > 70%)
        level = await get_pressure_level(100000, "gpt-4o")
        assert level == 1

        # 测试 level 2 压力 (120k/128k ≈ 94% > 85%)
        level = await get_pressure_level(120000, "gpt-4o")
        assert level == 2
