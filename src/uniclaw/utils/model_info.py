"""OpenRouter 模型信息获取模块 — 通过 API 和页面抓取获取模型详细信息。

功能:
- 通过 OpenRouter API 获取所有模型元数据
- 通过 httpx 抓取模型页面获取补充信息
- 提供模型能力查询(多模态、工具调用、推理等)
- 提供价格信息(包括缓存价格)
- 提供 Artificial Analysis 指数

缓存策略: 内存缓存,当天有效,隔天失效重新获取。
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger("model_info")

# OpenRouter API 端点
_MODELS_API_URL = "https://openrouter.ai/api/v1/models"
_MODEL_PAGE_BASE_URL = "https://openrouter.ai/"


@dataclass
class ModelInfo:
    """模型信息数据类。"""

    id: str  # 模型ID (如 "openai/gpt-4o")
    name: str  # 显示名称
    description: str = ""  # 完整描述
    context_length: int = 0  # 上下文长度
    max_completion_tokens: int | None = None  # 最大输出 token 数
    input_modalities: list[str] = field(default_factory=list)  # 输入模态
    output_modalities: list[str] = field(default_factory=list)  # 输出模态
    supports_tools: bool = False  # 是否支持工具调用
    supports_vision: bool = False  # 是否支持图片输入
    supports_video: bool = False  # 是否支持视频输入
    supports_audio: bool = False  # 是否支持音频输入
    supports_reasoning: bool = False  # 是否支持推理
    pricing: dict = field(default_factory=dict)  # 价格信息
    supported_parameters: list[str] = field(default_factory=list)  # 支持的参数列表
    reasoning_info: dict | None = None  # 推理能力详情
    knowledge_cutoff: str | None = None  # 知识截止日期
    created: int = 0  # 创建时间戳
    # Hugging Face 信息
    hugging_face_id: str | None = None  # Hugging Face 模型 ID
    canonical_slug: str = ""  # 规范化的 slug
    # 默认参数
    default_parameters: dict = field(default_factory=dict)  # 默认参数 (temperature, top_p, top_k)
    # Artificial Analysis 指数
    intelligence_index: float | None = None  # 综合智能指数
    coding_index: float | None = None  # 编码能力指数
    agentic_index: float | None = None  # 代理能力指数
    # Design Arena 评分
    design_arena_scores: list[dict] = field(default_factory=list)  # Design Arena ELO 评分和排名
    # 推理能力详情
    supported_efforts: list[str] = field(default_factory=list)  # 支持的推理努力级别
    default_effort: str | None = None  # 默认推理努力级别
    reasoning_mandatory: bool = False  # 推理是否必须启用
    # 详细信息获取状态
    detailed_info_fetched: bool = False  # 是否已获取页面详细信息
    # 原始数据(用于调试和扩展)
    raw_data: dict = field(default_factory=dict, repr=False)

    @property
    def page_url(self) -> str:
        """获取模型的 OpenRouter 页面 URL。"""
        return f"{_MODEL_PAGE_BASE_URL}{self.id}"

    @property
    def short_name(self) -> str:
        """获取模型短名称(去掉 provider 前缀)。"""
        return self.id.split("/", 1)[1] if "/" in self.id else self.id

    @property
    def provider(self) -> str:
        """获取提供商名称。"""
        return self.id.split("/", 1)[0] if "/" in self.id else ""

    def to_dict(self) -> dict:
        """转换为字典(用于序列化)。"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "context_length": self.context_length,
            "max_completion_tokens": self.max_completion_tokens,
            "input_modalities": self.input_modalities,
            "output_modalities": self.output_modalities,
            "supports_tools": self.supports_tools,
            "supports_vision": self.supports_vision,
            "supports_video": self.supports_video,
            "supports_audio": self.supports_audio,
            "supports_reasoning": self.supports_reasoning,
            "pricing": self.pricing,
            "supported_parameters": self.supported_parameters,
            "reasoning_info": self.reasoning_info,
            "knowledge_cutoff": self.knowledge_cutoff,
            "created": self.created,
            "hugging_face_id": self.hugging_face_id,
            "canonical_slug": self.canonical_slug,
            "default_parameters": self.default_parameters,
            "intelligence_index": self.intelligence_index,
            "coding_index": self.coding_index,
            "agentic_index": self.agentic_index,
            "design_arena_scores": self.design_arena_scores,
            "supported_efforts": self.supported_efforts,
            "default_effort": self.default_effort,
            "reasoning_mandatory": self.reasoning_mandatory,
            "detailed_info_fetched": self.detailed_info_fetched,
            "page_url": self.page_url,
        }


class ModelInfoProvider:
    """模型信息提供者,缓存机制：当天有效,隔天失效重新获取。

    使用示例::

        provider = ModelInfoProvider()
        info = await provider.get_model_info("gpt-4o")
        if info:
            print(f"支持工具: {info.supports_tools}")
            print(f"上下文长度: {info.context_length}")
    """

    def __init__(self):
        # 主缓存: {model_id: ModelInfo}
        self._cache: dict[str, ModelInfo] = {}
        # 短名称索引: {short_name: model_id}
        self._short_name_index: dict[str, str] = {}
        # 缓存日期
        self._cache_date: str = ""
        # 页面抓取缓存: {model_id: dict}
        self._page_cache: dict[str, dict] = {}

    def _is_cache_valid(self) -> bool:
        """检查缓存是否有效(当天)。"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self._cache_date == today and bool(self._cache)

    async def _ensure_cache(self) -> None:
        """确保缓存有效,如果失效则重新获取。"""
        if self._is_cache_valid():
            return
        await self._fetch_all_models()

    async def _fetch_all_models(self) -> None:
        """从 OpenRouter API 获取所有模型信息。"""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(_MODELS_API_URL, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            self._cache.clear()
            self._short_name_index.clear()

            for model_data in data.get("data", []):
                model_info = self._parse_model_data(model_data)
                if model_info:
                    self._cache[model_info.id] = model_info
                    # 建立短名称索引
                    short = model_info.short_name
                    if short not in self._short_name_index:
                        self._short_name_index[short] = model_info.id

            self._cache_date = datetime.now().strftime("%Y-%m-%d")
            logger.info("已缓存 %d 个模型信息", len(self._cache))

        except Exception as e:
            logger.error("获取 OpenRouter 模型列表失败: %s", e)
            raise

    def _parse_model_data(self, data: dict) -> ModelInfo | None:
        """解析模型数据为 ModelInfo 对象。"""
        try:
            model_id = data.get("id", "")
            if not model_id:
                return None

            # 解析架构信息
            architecture = data.get("architecture", {})
            input_modalities = architecture.get("input_modalities", [])
            output_modalities = architecture.get("output_modalities", [])

            # 解析支持的参数
            supported_parameters = data.get("supported_parameters", [])

            # 解析价格信息
            pricing_data = data.get("pricing", {})
            pricing = {
                "prompt": float(pricing_data.get("prompt", 0)),
                "completion": float(pricing_data.get("completion", 0)),
                "image": float(pricing_data.get("image", 0)),
                "audio": float(pricing_data.get("audio", 0)),
                "input_cache_read": float(pricing_data.get("input_cache_read", 0)),
                "input_cache_write": float(pricing_data.get("input_cache_write", 0)),
            }

            # 解析 top_provider 信息
            top_provider = data.get("top_provider", {})
            max_completion_tokens = top_provider.get("max_completion_tokens")

            # 解析推理信息
            reasoning_data = data.get("reasoning") or {}
            supports_reasoning = bool(reasoning_data) or "reasoning" in supported_parameters
            supported_efforts = reasoning_data.get("supported_efforts", [])
            default_effort = reasoning_data.get("default_effort")
            reasoning_mandatory = reasoning_data.get("mandatory", False)

            # 解析 benchmarks 信息
            benchmarks = data.get("benchmarks", {})
            artificial_analysis = benchmarks.get("artificial_analysis", {})
            design_arena = benchmarks.get("design_arena", [])

            return ModelInfo(
                id=model_id,
                name=data.get("name", ""),
                description=data.get("description", ""),
                context_length=data.get("context_length", 0),
                max_completion_tokens=max_completion_tokens,
                input_modalities=input_modalities,
                output_modalities=output_modalities,
                supports_tools="tools" in supported_parameters,
                supports_vision="image" in input_modalities,
                supports_video="video" in input_modalities,
                supports_audio="audio" in input_modalities,
                supports_reasoning=supports_reasoning,
                pricing=pricing,
                supported_parameters=supported_parameters,
                reasoning_info=reasoning_data if reasoning_data else None,
                knowledge_cutoff=data.get("knowledge_cutoff"),
                created=data.get("created", 0),
                hugging_face_id=data.get("hugging_face_id"),
                canonical_slug=data.get("canonical_slug", ""),
                default_parameters=data.get("default_parameters", {}),
                intelligence_index=artificial_analysis.get("intelligence_index"),
                coding_index=artificial_analysis.get("coding_index"),
                agentic_index=artificial_analysis.get("agentic_index"),
                design_arena_scores=design_arena,
                supported_efforts=supported_efforts,
                default_effort=default_effort,
                reasoning_mandatory=reasoning_mandatory,
                raw_data=data,
            )
        except Exception as e:
            logger.debug("解析模型数据失败: %s", e)
            return None

    def _resolve_model_id(self, model_name: str) -> str | None:
        """解析模型名称为完整的模型 ID。

        支持:
        - 完整 ID: "openai/gpt-4o"
        - 短名称: "gpt-4o"
        - 模糊匹配: 查找后缀匹配
        """
        if not model_name:
            return None

        model_lower = model_name.lower()

        # 精确匹配完整 ID
        if model_lower in self._cache:
            return model_lower

        # 精确匹配短名称
        if model_lower in self._short_name_index:
            return self._short_name_index[model_lower]

        # 模糊匹配: 遍历缓存查找后缀
        for mid in self._cache:
            if mid.lower().endswith("/" + model_lower):
                return mid

        return None

    async def get_model_info(self, model_name: str) -> ModelInfo | None:
        """通过模型名获取模型信息,支持短名称模糊匹配。

        首次获取时会自动抓取页面获取详细信息(描述、缓存价格等),
        后续调用直接返回缓存数据。

        Args:
            model_name: 模型名称,支持完整 ID ("openai/gpt-4o") 或短名称 ("gpt-4o")

        Returns:
            ModelInfo 对象,未找到返回 None
        """
        await self._ensure_cache()
        model_id = self._resolve_model_id(model_name)
        if not model_id:
            return None

        info = self._cache.get(model_id)
        if info and not info.detailed_info_fetched:
            # 自动获取页面详细信息
            await self._fetch_and_update_details(info)

        return info

    async def _fetch_and_update_details(self, info: ModelInfo) -> None:
        """获取并更新模型的详细信息。

        Args:
            info: 要更新的 ModelInfo 对象
        """
        try:
            page_info = await self.fetch_model_page(info.id)
            if page_info:
                self._update_from_page_info(info, page_info)
                info.detailed_info_fetched = True
                logger.debug("已获取模型详细信息: %s", info.id)
        except Exception as e:
            logger.debug("获取模型详细信息失败 %s: %s", info.id, e)
            # 即使失败也标记为已尝试,避免重复请求
            info.detailed_info_fetched = True

    def _update_from_page_info(self, info: ModelInfo, page_info: dict) -> None:
        """从页面信息更新模型数据。

        Args:
            info: 要更新的 ModelInfo 对象
            page_info: 页面抓取的信息字典
        """
        # 更新描述(如果页面提供了更详细的描述)
        if page_info.get("og_description"):
            # 如果 API 的描述为空或较短,使用页面描述
            if not info.description or len(info.description) < len(page_info["og_description"]):
                info.description = page_info["og_description"]
        elif page_info.get("meta_description"):
            if not info.description or len(info.description) < len(page_info["meta_description"]):
                info.description = page_info["meta_description"]

        # 更新价格信息(如果页面提供了更详细的价格)
        if page_info.get("pricing"):
            page_pricing = page_info["pricing"]
            # 合并价格信息,保留原有值
            for key in ["prompt", "completion", "image", "audio", "input_cache_read", "input_cache_write"]:
                if key in page_pricing and page_pricing[key]:
                    try:
                        info.pricing[key] = float(page_pricing[key])
                    except (ValueError, TypeError):
                        pass

    async def get_model_page_url(self, model_name: str) -> str | None:
        """获取模型的 OpenRouter 页面 URL。

        Args:
            model_name: 模型名称

        Returns:
            页面 URL,未找到返回 None
        """
        info = await self.get_model_info(model_name)
        return info.page_url if info else None

    async def fetch_model_page(self, model_name: str) -> dict | None:
        """使用 httpx 抓取模型页面获取详细信息。

        Args:
            model_name: 模型名称

        Returns:
            页面抓取的信息字典,失败返回 None
        """
        await self._ensure_cache()
        model_id = self._resolve_model_id(model_name)
        if not model_id:
            return None

        # 检查页面缓存
        if model_id in self._page_cache:
            return self._page_cache[model_id]

        try:
            url = f"{_MODEL_PAGE_BASE_URL}{model_id}"
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=15, follow_redirects=True)
            resp.raise_for_status()

            # 解析页面内容(简单的正则提取)
            html = resp.text
            page_info = self._extract_page_info(html, model_id)
            self._page_cache[model_id] = page_info
            return page_info

        except Exception as e:
            logger.debug("抓取模型页面失败 %s: %s", model_id, e)
            return None

    def _extract_page_info(self, html: str, model_id: str) -> dict:
        """从页面 HTML 中提取信息。"""
        info = {"model_id": model_id, "url": f"{_MODEL_PAGE_BASE_URL}{model_id}"}

        # 尝试提取描述(如果 API 的 description 不够详细)
        desc_match = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html)
        if desc_match:
            info["meta_description"] = desc_match.group(1)

        # 尝试提取 og:title
        og_title_match = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', html)
        if og_title_match:
            info["og_title"] = og_title_match.group(1)

        # 尝试提取 og:description
        og_desc_match = re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', html)
        if og_desc_match:
            info["og_description"] = og_desc_match.group(1)

        # 尝试提取价格信息(从 JSON-LD 或脚本中)
        pricing = self._extract_pricing_from_html(html)
        if pricing:
            info["pricing"] = pricing

        return info

    def _extract_pricing_from_html(self, html: str) -> dict:
        """从 HTML 中提取价格信息。"""
        pricing = {}

        # 尝试从 JSON-LD 结构化数据中提取
        json_ld_match = re.search(r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        if json_ld_match:
            try:
                import json
                json_ld = json.loads(json_ld_match.group(1))
                # 提取价格信息
                if isinstance(json_ld, dict):
                    offers = json_ld.get("offers", {})
                    if isinstance(offers, dict):
                        price = offers.get("price")
                        if price:
                            pricing["prompt"] = str(price)
            except (json.JSONDecodeError, KeyError):
                pass

        # 尝试从页面文本中提取价格(备用方案)
        # 匹配类似 "$0.000005 / token" 或 "$5 / 1M tokens" 的模式
        price_patterns = [
            r'\$(\d+\.?\d*)\s*/\s*token',
            r'\$(\d+\.?\d*)\s*/\s*1M?\s*tokens?',
            r'(\d+\.?\d*)\s*/\s*token',
        ]

        for pattern in price_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                try:
                    # 取第一个匹配作为输入价格
                    pricing["prompt"] = float(matches[0])
                    break
                except ValueError:
                    continue

        return pricing

    async def search_models(self, query: str) -> list[ModelInfo]:
        """按关键词搜索模型。

        搜索范围: 模型 ID、名称、描述。

        Args:
            query: 搜索关键词

        Returns:
            匹配的模型列表
        """
        await self._ensure_cache()
        query_lower = query.lower()
        results = []

        for model in self._cache.values():
            if (
                query_lower in model.id.lower()
                or query_lower in model.name.lower()
                or query_lower in model.description.lower()
            ):
                results.append(model)

        return results

    async def list_all_models(self) -> list[ModelInfo]:
        """列出所有可用模型。

        Returns:
            所有模型的列表
        """
        await self._ensure_cache()
        return list(self._cache.values())

    async def list_models_by_provider(self, provider: str) -> list[ModelInfo]:
        """列出指定提供商的所有模型。

        Args:
            provider: 提供商名称 (如 "openai", "anthropic", "google")

        Returns:
            该提供商的模型列表
        """
        await self._ensure_cache()
        provider_lower = provider.lower()
        return [m for m in self._cache.values() if m.provider.lower() == provider_lower]

    async def list_models_with_tools(self) -> list[ModelInfo]:
        """列出所有支持工具调用的模型。"""
        await self._ensure_cache()
        return [m for m in self._cache.values() if m.supports_tools]

    async def list_models_with_vision(self) -> list[ModelInfo]:
        """列出所有支持图片输入的模型。"""
        await self._ensure_cache()
        return [m for m in self._cache.values() if m.supports_vision]

    async def list_models_with_video(self) -> list[ModelInfo]:
        """列出所有支持视频输入的模型。"""
        await self._ensure_cache()
        return [m for m in self._cache.values() if m.supports_video]

    async def list_models_with_reasoning(self) -> list[ModelInfo]:
        """列出所有支持推理的模型。"""
        await self._ensure_cache()
        return [m for m in self._cache.values() if m.supports_reasoning]

    def clear_cache(self) -> None:
        """清除所有缓存。"""
        self._cache.clear()
        self._short_name_index.clear()
        self._page_cache.clear()
        self._cache_date = ""


# 全局单例
_provider: ModelInfoProvider | None = None


def get_model_info_provider() -> ModelInfoProvider:
    """获取全局 ModelInfoProvider 单例。"""
    global _provider
    if _provider is None:
        _provider = ModelInfoProvider()
    return _provider
