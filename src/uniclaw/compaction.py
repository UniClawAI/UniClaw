"""上下文压缩模块 — 根据 token 用量自动压缩消息历史。

从 OpenRouter API 获取模型上下文长度,失败时使用默认值 128000。
"""

import logging

logger = logging.getLogger("compaction")

# 默认上下文长度(当 API 获取失败时使用)
DEFAULT_CONTEXT_LIMIT = 128000

# ── 三级压力阈值 ──────────────────────────────────────────────
# 每个等级对应不同的压缩策略:
#   level 0 (50%) — 轻度:仅微压缩(清空旧工具结果)
#   level 1 (70%) — 中度:微压缩 + LLM 摘要
#   level 2 (85%) — 重度:微压缩 + 更激进的 LLM 摘要
PRESSURE_LEVELS: list[tuple[float, int]] = [
    (0.85, 2),
    (0.70, 1),
    (0.50, 0),
]

# 自动压缩边界: 取 level 1(首次引入 LLM 摘要,开始有信息损失)的阈值。
# 不参与压缩触发逻辑(那由 PRESSURE_LEVELS 驱动),仅供上下文报告预留缓冲。
AUTOCOMPACT_THRESHOLD: float = {lv: t for t, lv in PRESSURE_LEVELS}[1]





async def get_pressure_level(current_tokens: int, model: str | None) -> int:
    """根据当前 token 用量返回压力等级。

    Args:
        current_tokens: 当前估算的 token 数
        model: 模型名称

    Returns:
        0/1/2 — 对应的压力等级, -1 表示未超过最低阈值(不需要压缩)
    """
    limit = await get_context_limit(model)
    ratio = current_tokens / limit if limit > 0 else 0
    for threshold, level in PRESSURE_LEVELS:
        if ratio >= threshold:
            return level
    return -1
async def get_context_limit(model: str | None = None) -> int:
    """获取模型的上下文长度限制。

    从 OpenRouter API 获取,失败时使用默认值。

    Args:
        model: 模型名称(支持完整 ID 或短名称)

    Returns:
        上下文长度(tokens)
    """
    if not model:
        return DEFAULT_CONTEXT_LIMIT

    # 尝试从 OpenRouter API 获取
    try:
        from uniclaw.utils.model_info import get_model_info_provider

        provider = get_model_info_provider()
        info = await provider.get_model_info(model)
        if info and info.context_length > 0:
            return info.context_length
    except Exception as e:
        logger.debug("从 OpenRouter 获取上下文长度失败: %s", e)

    return DEFAULT_CONTEXT_LIMIT