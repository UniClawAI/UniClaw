import httpx
from uniclaw.config import AppConfig, ProviderProfile, save_config
from uniclaw.console.ui import info, ok, warn, err


def fetch_openai_models_sync(base_url: str, api_key: str, proxy_url: str = "") -> list[str]:
    """同步版本: 通过 base_url 和 api_key 获取可用模型列表

    Args:
        base_url: API 基础 URL
        api_key: API 密钥
        proxy_url: 代理 URL

    Returns:
        list[str]: 模型 ID 列表
    """
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    url = f"{base}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    client_kwargs = {"headers": headers, "timeout": 10}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url
    resp = httpx.get(url, **client_kwargs)
    resp.raise_for_status()
    data = resp.json()
    return [m["id"] for m in data.get("data", [])]


async def fetch_openai_models(base_url: str, api_key: str, proxy_url: str = "") -> list[str]:
    """异步版本: 通过 base_url 和 api_key 获取可用模型列表

    Args:
        base_url: API 基础 URL
        api_key: API 密钥
        proxy_url: 代理 URL

    Returns:
        list[str]: 模型 ID 列表
    """
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    url = f"{base}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    client_kwargs = {}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url
    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return [m["id"] for m in data.get("data", [])]


def fetch_anthropic_models_sync(base_url: str, api_key: str, proxy_url: str = "") -> list[str]:
    """同步版本: 获取 Anthropic 可用模型列表。"""
    url = f"{base_url.rstrip('/')}/v1/models"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    client_kwargs = {"headers": headers, "timeout": 10}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url
    resp = httpx.get(url, **client_kwargs)
    resp.raise_for_status()
    data = resp.json()
    return [m["id"] for m in data.get("data", [])]


async def fetch_anthropic_models(base_url: str, api_key: str, proxy_url: str = "") -> list[str]:
    """异步版本: 获取 Anthropic 可用模型列表。"""
    url = f"{base_url.rstrip('/')}/v1/models"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    client_kwargs = {}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url
    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return [m["id"] for m in data.get("data", [])]


async def _fetch_provider_models(profile: ProviderProfile) -> list[str]:
    """获取指定 provider 的模型列表。"""
    if profile.protocol == "anthropic":
        return await fetch_anthropic_models(profile.base_url, profile.api_key, profile.proxy_url)
    else:
        return await fetch_openai_models(profile.base_url, profile.api_key, profile.proxy_url)


def _move_to_first(lst: list[str], item: str) -> list[str]:
    """将 item 移到列表第一个位置,去重。"""
    return [item] + [x for x in lst if x != item]


async def _apply_model(model_ref: str, config: AppConfig) -> None:
    """选择模型后提示设置角色(主模型/mini/多模态)。"""

    async def _notify_webui():
        """通知 WebUI 配置已变更。"""
        try:
            from uniclaw.webui.ws import _notify_config_changed
            session_id = config.current_agent.session.id
            await _notify_config_changed(session_id)
        except Exception:
            pass

    if config.is_wechat:
        config.model_name = _move_to_first(config.model_name, model_ref)
        save_config(config)
        await ok(f"✓ 已设为主模型: {model_ref}", config)
        return

    from uniclaw.console.ui import get_input

    choice = await get_input(
        f"\n已选择: {model_ref}\n"
        "  [1] 设为主模型\n"
        "  [2] 设为 mini 模型\n"
        "  [3] 设为多模态模型\n"
        "  [4] 设为顾问模型\n"
        "  [5] 设为 TTS 模型\n"
        "  [6] 设为 ASR 模型\n"
        "  [7] 设为图片生成模型\n"
        "选择 (1-7, 回车取消): ",
        config=config,
    )
    choice = choice.strip()
    if not choice:
        return

    if choice == "1":
        config.model_name = _move_to_first(config.model_name, model_ref)
        save_config(config)
        await ok(f"✓ 已设为主模型: {model_ref}", config)
        await _notify_webui()
    elif choice == "2":
        config.mini_model_name = _move_to_first(config.mini_model_name, model_ref)
        save_config(config)
        await ok(f"✓ 已设为 mini 模型: {model_ref}", config)
        await _notify_webui()
    elif choice == "3":
        config.multimodal_model_name = _move_to_first(config.multimodal_model_name, model_ref)
        save_config(config)
        await ok(f"✓ 已设为多模态模型: {model_ref}", config)
        await _notify_webui()
    elif choice == "4":
        config.large_model_name = _move_to_first(config.large_model_name, model_ref)
        save_config(config)
        await ok(f"✓ 已设为顾问模型: {model_ref}", config)
        await _notify_webui()
    elif choice == "5":
        config.tts_model = model_ref
        voice = await get_input("请输入语音名称 (回车跳过): ", config=config)
        if voice.strip():
            config.audio = {"voice": voice.strip()}
        save_config(config)
        await ok(f"✓ 已设为 TTS 模型: {model_ref}" + (f", 语音: {voice.strip()}" if voice.strip() else ""), config)
        await _notify_webui()
    elif choice == "6":
        config.asr_model = model_ref
        save_config(config)
        await ok(f"✓ 已设为 ASR 模型: {model_ref}", config)
        await _notify_webui()
    elif choice == "7":
        config.image_model = model_ref
        save_config(config)
        await ok(f"✓ 已设为图片生成模型: {model_ref}", config)
        await _notify_webui()


async def cmd_model(args: str, config: AppConfig) -> bool:
    """选择当前使用的模型

    支持以下功能:
    - 无参数:列出所有 provider 的模型
    - <provider>:列出该 provider 的模型
    - <provider>/<model>:直接切换到指定 provider 的指定模型
    - <关键词>:在所有模型中模糊搜索

    Args:
        args: 模型名称或搜索关键词
        config: 配置对象

    Returns:
        bool: 始终返回 True 表示命令执行完成
    """
    if not config.providers:
        await warn("未配置任何 provider,请先运行配置向导", config)
        return True

    # 解析参数
    provider_name = None
    model_keyword = ""
    if args:
        args = args.strip()
        if "/" in args:
            provider_name, _, model_keyword = args.partition("/")
        else:
            model_keyword = args

    # 确定要搜索的 providers 范围
    if provider_name and provider_name in config.providers:
        # 指定了有效 provider,只搜索该 provider
        search_providers = {provider_name: config.providers[provider_name]}
    else:
        # 没指定 provider 或 provider 不存在,搜索全部
        if provider_name:
            model_keyword = args  # provider 不存在,整体作为关键词
        search_providers = config.providers

    # 并发获取所有 provider 的模型列表
    import asyncio

    async def _fetch(name: str, profile) -> list[str]:
        try:
            models = await _fetch_provider_models(profile)
            models.sort()
            return [f"{name}/{m}" for m in models]
        except Exception:
            return []

    tasks = [_fetch(name, p) for name, p in search_providers.items()]
    results = await asyncio.gather(*tasks)
    all_models = [m for group in results for m in group]

    if not all_models:
        await warn("未找到可用模型,请使用 /model <provider>/<模型名称> 直接指定", config)
        return True

    # 关键词搜索
    if model_keyword:
        keyword_lower = model_keyword.lower()

        # 精确匹配
        if model_keyword in all_models:
            await _apply_model(model_keyword, config)
            return True

        # 模糊搜索
        matched = [m for m in all_models if keyword_lower in m.lower()]
        if not matched:
            await err(f"未找到匹配的模型: {model_keyword}", config)
            return True
        if len(matched) == 1:
            await _apply_model(matched[0], config)
            return True
        all_models = matched
        await info(f"\n找到 {len(matched)} 个匹配的模型:", config)

    # 显示模型列表
    current_main = config.model_name[0] if config.model_name else ""
    current_mini = config.mini_model_name[0] if config.mini_model_name else ""
    current_mm = config.multimodal_model_name[0] if config.multimodal_model_name else ""
    current_large = config.large_model_name[0] if config.large_model_name else ""
    current_tts = config.tts_model
    current_asr = config.asr_model
    current_image = config.image_model

    title = provider_name if provider_name and provider_name in search_providers else "所有"
    prompt_list = [f"\n{title} 可用模型:"]
    if current_tts:
        voice = config.audio.get("voice", "") if config.audio else ""
        prompt_list.append(f"  当前 TTS: {current_tts}" + (f" (语音: {voice})" if voice and len(voice) < 20 else ""))
    if current_asr:
        prompt_list.append(f"  当前 ASR: {current_asr}")
    if current_image:
        prompt_list.append(f"  当前图片生成: {current_image}")
    for i, m in enumerate(all_models, 1):
        tags = []
        if m == current_main:
            tags.append("主模型")
        if m == current_mini:
            tags.append("mini")
        if m == current_mm:
            tags.append("多模态")
        if m == current_large:
            tags.append("顾问")
        if m == current_tts:
            tags.append("TTS")
        if m == current_asr:
            tags.append("ASR")
        if m == current_image:
            tags.append("图片生成")
        marker = f" ← {', '.join(tags)}" if tags else ""
        prompt_list.append(f"  [{i}] {m}{marker}")

    if config.is_wechat:
        await info("\n".join(prompt_list), config)
        await info("\n请使用 /model <provider>/<模型名称> 切换模型", config)
        return True

    from uniclaw.console.ui import get_input

    choice = await get_input(
        "\n".join(prompt_list) + "\n请输入模型编号 (回车取消): ",
        config=config,
    )
    choice = choice.strip()
    if not choice:
        return True

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(all_models):
            await _apply_model(all_models[idx], config)
    except ValueError:
        pass

    return True
