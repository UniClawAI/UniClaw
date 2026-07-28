# 自动压缩触发阈值:当消息 token 超过 context limit 的此比例时触发压缩
AUTOCOMPACT_THRESHOLD = 0.7

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


def get_pressure_level(current_tokens: int, model: str | None) -> int:
    """根据当前 token 用量返回压力等级。

    Args:
        current_tokens: 当前估算的 token 数
        model: 模型名称

    Returns:
        0/1/2 — 对应的压力等级, -1 表示未超过最低阈值(不需要压缩)
    """
    limit = get_context_limit(model)
    ratio = current_tokens / limit if limit > 0 else 0
    for threshold, level in PRESSURE_LEVELS:
        if ratio >= threshold:
            return level
    return -1


MODEL_CONTEXT_LIMITS = {
    # OpenAI GPT 系列
    "gpt-3.5-turbo": 16385,
    "gpt-4": 8192,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 1000000,
    "gpt-4.1-mini": 1000000,
    "gpt-4.1-nano": 1000000,
    "gpt-5": 128000,
    "gpt-5.4": 128000,
    # OpenAI 推理系列
    "o1": 200000,
    "o1-mini": 128000,
    "o1-pro": 200000,
    "o3": 200000,
    "o3-mini": 200000,
    "o4-mini": 200000,
    # Claude 系列
    "claude-3-5-sonnet": 200000,
    "claude-3-5-haiku": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-opus-4-7": 200000,
    "claude-haiku-4-5-20251001": 200000,
    # Google Gemini 系列
    "gemini-1.5-pro": 2000000,
    "gemini-1.5-flash": 1000000,
    "gemini-2.0-flash": 1000000,
    "gemini-2.5-pro": 1000000,
    "gemini-2.5-flash": 1000000,
    # Meta Llama 系列
    "llama-3.1": 128000,
    "llama-3.3": 128000,
    "llama-4-scout": 10000000,
    "llama-4-maverick": 1000000,
    # DeepSeek 系列
    "deepseek-chat": 128000,
    "deepseek-v3": 128000,
    "deepseek-reasoner": 128000,
    "deepseek-r1": 128000,
    # 小米 MiMo 系列
    "mimo-v2.5-pro": 1000000,
    "mimo-7b": 32768,
    # 阿里 Qwen 系列
    "qwen-turbo": 128000,
    "qwen-plus": 131072,
    "qwen-max": 131072,
    "qwen-2.5": 128000,
    "qwen-3": 128000,
    "qwq-32b": 128000,
}


def get_context_limit(model: str | None = None) -> int:
    if not model:
        return 128000
    # 去掉 provider 前缀,如 "openai/gpt-4o" -> "gpt-4o"
    if "/" in model:
        parts = model.split("/")
        short_name = parts[len(parts) - 1]
    else:
        short_name = model
    # 先精确匹配
    if short_name in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[short_name]
    # 再前缀匹配(按 key 长度降序,确保最长前缀优先匹配)
    sorted_keys = sorted(MODEL_CONTEXT_LIMITS.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if short_name.startswith(key):
            return MODEL_CONTEXT_LIMITS[key]
    return 128000
