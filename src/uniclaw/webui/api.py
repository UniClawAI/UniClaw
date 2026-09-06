"""REST API 路由。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from uniclaw.ilink_bot.manager import BotManager
from uniclaw.webui.models import (
    AsrRequest,
    CheckpointCreate,
    CheckpointRestore,
    ConfigUpdate,
    GitAiCommitMessage,
    GitCommit,
    GitStage,
    HookUpdate,
    MessageDelete,
    PermissionRuleDelete,
    PromptOptimize,
    SessionMove,
    SessionRename,
    SettingsUpdate,
    SubAgentCreate,
    UserPromptOptimize,
    WechatBotCreate,
)
from uniclaw.webui.ws import get_or_load_session, session_cache
from uniclaw.tools.session.session_manager import SessionManager
from uniclaw.utils.logger import get_logger

router = APIRouter(prefix="/api")


def _validate_path(base_dir: str, relative_path: str) -> Path:
    """验证并规范化路径,防止路径遍历攻击。

    Args:
        base_dir: 基础目录(项目根目录)
        relative_path: 相对路径

    Returns:
        规范化后的绝对路径

    Raises:
        HTTPException: 路径越界时抛出 403 错误
    """
    base = Path(base_dir).resolve()
    target = (base / relative_path).resolve()

    # 检查目标路径是否在基础目录内
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=403, detail=f"路径越界: {relative_path}")

    return target


# === 项目管理 ===


@router.get("/projects")
async def list_projects():
    """列出已有项目(从 session metadata 提取去重的 root_dir)。"""
    sessions = SessionManager.list_sessions(limit=10000)
    projects = {}
    for s in sessions:
        root_dir = s.get("root_dir", "")
        if not root_dir:
            continue
        if root_dir not in projects:
            projects[root_dir] = {
                "root_dir": root_dir,
                "session_count": 0,
                "last_active": s.get("end_time") or s.get("start_time", ""),
            }
        projects[root_dir]["session_count"] += 1
        end_time = s.get("end_time") or s.get("start_time", "")
        if end_time > projects[root_dir]["last_active"]:
            projects[root_dir]["last_active"] = end_time
    # 按最后活跃时间排序
    result = sorted(projects.values(), key=lambda x: x["last_active"], reverse=True)
    return result


@router.get("/dirs")
async def list_dirs(path: str = ""):
    """浏览服务端目录。"""
    if not path:
        # 返回根目录列表
        if os.name == "nt":
            import string

            drives = [
                f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")
            ]
            return [{"name": d, "path": d, "is_dir": True} for d in drives]
        else:
            return [{"name": "/", "path": "/", "is_dir": True}]

    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {path}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {path}")

    entries = []
    try:
        for item in sorted(p.iterdir()):
            entries.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                }
            )
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"无权限访问: {path}")
    return entries


# === 会话管理 ===


@router.get("/sessions")
async def list_sessions(root_dir: str = ""):
    """列出会话(文件 + 内存缓存中的活跃会话)。"""
    limit = 10000
    if root_dir:
        sessions = SessionManager.list_sessions(limit=limit, root_dir=root_dir)
    else:
        sessions = SessionManager.list_sessions(limit=limit)

    # 合并内存缓存中的活跃会话(缓存版本优先,因为可能有未保存的新消息)
    saved_map = {s["session_id"]: s for s in sessions}
    for sid, config in session_cache.items():
        session = config.current_agent.session
        item_root_dir = str(session.root_dir) if session.root_dir else ""
        if root_dir and item_root_dir != root_dir:
            continue
        cache_item = {
            "session_id": session.id,
            "title": session.title or session.id,
            "start_time": session.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": "",
            "message_count": len(session._messages),
            "root_dir": item_root_dir,
            "session_type": session.session_type,
            "file_path": "",
        }
        existing = saved_map.get(sid)
        if existing:
            # 缓存中的消息数更多 → 用缓存版本替换
            if cache_item["message_count"] > existing.get("message_count", 0):
                saved_map[sid] = cache_item
            # 更新 title(缓存中的 title 可能更准确)
            if session.title:
                saved_map[sid]["title"] = session.title
        else:
            saved_map[sid] = cache_item
    sessions = list(saved_map.values())

    # 按时间排序
    sessions.sort(
        key=lambda x: x.get("end_time") or x.get("start_time") or "",
        reverse=True,
    )
    return sessions[:limit]


@router.get("/sessions/search")
async def search_sessions(keyword: str):
    """搜索会话。"""
    return SessionManager.search_sessions(keyword)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """获取会话详情(优先从缓存读取,缓存中可能有未保存的新消息)。"""
    session = None
    # 优先从缓存获取(可能有未保存的新消息)
    if session_id in session_cache:
        config = session_cache[session_id]
        session = config.current_agent.session
    # 缓存未命中或缓存中没有消息,从文件加载
    if session is None or not session._messages:
        file_session = SessionManager.load_session(session_id)
        if file_session and (
            session is None or len(file_session._messages) > len(session._messages)
        ):
            session = file_session
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    # 手动构建响应(避免 to_dict 的 async/config 依赖)
    from datetime import datetime

    now = datetime.now()
    duration = max(0, int((now - session.start_time).total_seconds()))
    return {
        "session_id": session.id,
        "title": session.title or session.id,
        "root_dir": str(session.root_dir),
        "session_type": session.session_type,
        "start_time": session.start_time.isoformat(),
        "end_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_seconds": duration,
        "message_count": len(session._messages),
        "messages": session.to_messages(),
        "history": session.to_history_messages(),
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话。"""
    from uniclaw.webui.ws import get_or_load_session, notify_session_deleted

    try:
        config = await get_or_load_session(session_id)
    except ValueError:
        config = None
    root_dir = config.root_dir if config else None
    SessionManager.delete_session(session_id, config=config)
    session_cache.pop(session_id, None)
    # 通知前端会话已删除
    await notify_session_deleted(session_id, root_dir)
    return {"ok": True}


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, body: SessionRename):
    """重命名会话。"""
    SessionManager.update_title(session_id, body.title)
    # 同步更新内存缓存中的标题
    if session_id in session_cache:
        session_cache[session_id].current_agent.session.title = body.title
    return {"ok": True}


@router.post("/sessions/{session_id}/move")
async def move_session(session_id: str, body: SessionMove):
    """移动会话到其他项目。"""
    ok = SessionManager.update_root_dir(session_id, body.root_dir)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 更新缓存中的 root_dir
    if session_id in session_cache:
        session_cache[session_id].current_agent.session.root_dir = Path(body.root_dir)
    return {"ok": True}


@router.post("/sessions/{session_id}/title/generate")
async def generate_title(session_id: str):
    """AI 生成标题。"""
    try:
        config = await get_or_load_session(session_id)
        session = config.current_agent.session
        title = await session.generate_title(config)
        SessionManager.update_title(session_id, title)
        return {"title": title}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}/messages")
async def delete_messages(session_id: str, body: MessageDelete):
    """删除会话末尾指定数量的消息。"""
    try:
        config = await get_or_load_session(session_id)
        session = config.current_agent.session
        # 根据 from_idx 计算要删除的数量
        source_list = (
            session._messages if body.source == "messages" else session.history
        )
        count = len(source_list) - body.from_idx
        deleted = session.delete_messages(count, source=body.source)
        # 保存到文件
        await SessionManager.save_session(config)
        return {"ok": True, "deleted": deleted}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        get_logger("webui", Path.cwd()).error(f"删除消息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === 配置 ===


@router.get("/config")
async def get_config(session_id: str):
    """获取会话配置。"""
    try:
        config = await get_or_load_session(session_id)
        result = {
            "model_name": config.model_name,
            "mini_model_name": config.mini_model_name,
            "permission_mode": (
                config.permission_mode.value
                if hasattr(config.permission_mode, "value")
                else str(config.permission_mode)
            ),
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "root_dir": config.root_dir,
            "voice_available": bool(config.tts_model and config.audio),
            "voice_mode": config.voice_mode,
            "asr_available": bool(config.asr_model),
            "image_available": bool(config.image_model),
            "computer_use_enabled": config.computer_use_enabled,
            "explain_mode": (
                True
                if config.explain_mode is True
                else (
                    sorted(config.explain_mode)
                    if isinstance(config.explain_mode, set)
                    else False
                )
            ),
        }
        # todolist 信息
        todo = config.current_agent.todolist
        if todo and not todo.is_empty():
            result["todolist"] = {
                "items": [
                    {"content": it.content, "status": it.status.value}
                    for it in todo.items
                ],
                "brief": todo.get_brief(),
            }
        else:
            result["todolist"] = None
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/config")
async def update_config(body: ConfigUpdate):
    """更新配置(模型切换请使用 /model 命令)。"""
    try:
        config = await get_or_load_session(body.session_id)
        if body.permission_mode is not None:
            from uniclaw.config import Permissions

            config.permission_mode = Permissions(body.permission_mode)
        if body.computer_use_enabled is not None:
            config.computer_use_enabled = body.computer_use_enabled
        if body.explain_mode is not None:
            if isinstance(body.explain_mode, list):
                config.explain_mode = set(body.explain_mode)
            else:
                config.explain_mode = body.explain_mode
        if body.temperature is not None:
            config.temperature = body.temperature
        if body.max_tokens is not None:
            config.max_tokens = body.max_tokens
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/optimize-prompt")
async def optimize_prompt(body: PromptOptimize):
    """优化系统提示词。"""
    if not body.prompt.strip():
        return {"optimized": ""}

    from uniclaw.tools.session.session import Session, SessionType
    from uniclaw.provider.fallback import achat
    from uniclaw.config import load_config

    config = load_config()
    session = Session()
    session.add_user_message(body.prompt)
    try:
        resp = await achat(
            "你是一个提示词优化专家。用户会给你一段系统提示词,请优化它,使其更清晰、更结构化、更有效。"
            "保持用户的原始意图不变,只改进表达。直接输出优化后的提示词,不要解释。",
            session,
            model_name=config.mini_model_name,
            enable_thinking=False,
            thinking=False,
            config=config,
        )
        return {"optimized": resp.content.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize-user-prompt")
async def optimize_user_prompt(body: UserPromptOptimize):
    """优化用户输入的提示词(带会话上下文)。"""
    if not body.prompt.strip():
        return {"optimized": ""}

    try:
        # 获取会话配置和历史消息
        config = await get_or_load_session(body.session_id)
        session = config.current_agent.session

        # 使用 to_str() 获取会话上下文
        context_summary = session.to_str(include_tools=False)

        # 构建优化提示词
        system_prompt = (
            "你是一个提示词优化专家。用户会给你一段用户输入的提示词,请根据会话上下文优化它,使其更清晰、更具体、更有效。"
            "保持用户的原始意图不变,只改进表达。如果会话上下文有助于理解用户意图,请参考上下文进行优化。"
            "直接输出优化后的提示词,不要解释。"
        )

        # 构建用户消息
        user_message = body.prompt
        if context_summary:
            user_message = (
                f"会话上下文:\n{context_summary}\n\n用户提示词:\n{body.prompt}"
            )

        from uniclaw.tools.session.session import Session, SessionType
        from uniclaw.provider.fallback import achat

        # 创建临时会话用于优化
        optimize_session = Session()
        optimize_session.add_user_message(user_message)

        resp = await achat(
            system_prompt,
            optimize_session,
            model_name=config.mini_model_name,
            enable_thinking=False,
            thinking=False,
            config=config,
        )
        return {"optimized": resp.content.strip()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _mask_key(key: str) -> str:
    """脱敏 API key:保留前 4 + 后 4 字符,中间用 **** 替代。"""
    if not key or len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


def _read_settings_raw() -> tuple[Path, dict]:
    """读取 settings.json 原始数据(不做归一化)。"""
    from uniclaw.config import get_config_path

    path = get_config_path()
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return path, data


@router.get("/settings")
async def get_settings():
    """读取全局 settings.json(API key 脱敏)。"""
    path, data = _read_settings_raw()

    # 脱敏 providers 中的 api_key
    providers = data.get("providers", {})
    masked_providers = {}
    for name, p in providers.items():
        masked_providers[name] = {
            **p,
            "api_key": _mask_key(p.get("api_key", "")),
        }

    def _norm_model(v):
        """归一化 model 字段为 list。"""
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v else []
        return list(v) if isinstance(v, list) else []

    # 判断是项目级还是用户级配置
    try:
        path.relative_to(Path.cwd())
        config_level = "project"
    except ValueError:
        config_level = "user"

    return {
        "config_path": str(path),
        "config_level": config_level,
        "model_name": _norm_model(data.get("model_name")),
        "mini_model_name": _norm_model(data.get("mini_model_name")),
        "multimodal_model_name": _norm_model(data.get("multimodal_model_name")),
        "large_model_name": _norm_model(data.get("large_model_name")),
        "tts_model": data.get("tts_model", "") or "",
        "asr_model": data.get("asr_model", "") or "",
        "image_model": data.get("image_model", "") or "",
        "embedding_model": data.get("embedding_model", "") or "",
        "audio": data.get("audio") or None,
        "temperature": data.get("temperature"),
        "max_tokens": data.get("max_tokens"),
        "top_p": data.get("top_p"),
        "proxy_url": data.get("proxy_url", "") or "",
        "GITHUB_TOKEN": _mask_key(data.get("GITHUB_TOKEN", "")),
        "EXA_API_KEY": _mask_key(data.get("EXA_API_KEY", "")),
        "max_agent_depth": data.get("max_agent_depth", 2),
        "permission_timeout": data.get("permission_timeout", 300),
        "permission_mode": data.get("permission_mode", "auto"),
        "trusted_ips": data.get("trusted_ips", []) or [],
        "providers": masked_providers,
    }


@router.put("/settings")
async def update_settings(body: SettingsUpdate):
    """保存 settings.json。session_id 非空时更新会话级配置(内存),否则更新全局配置(磁盘)。"""

    # === 会话级配置更新 ===
    if body.session_id:
        await _update_session_settings(body)

    # === 全局配置更新 ===
    # 读取原始配置,用于恢复未修改的脱敏 key
    path, original = _read_settings_raw()
    original_providers = original.get("providers", {})
    original_github = original.get("GITHUB_TOKEN", "")
    original_exa = original.get("EXA_API_KEY", "")

    # 恢复脱敏的 API key(通过 masked key 的前4后4字符匹配原始 key)
    masked_to_original: dict[str, str] = {}
    for p in original_providers.values():
        orig_key = p.get("api_key", "")
        if orig_key:
            masked_to_original[_mask_key(orig_key)] = orig_key
    providers = {}
    for name, p in body.providers.items():
        api_key = p.api_key
        if "****" in api_key:
            api_key = masked_to_original.get(api_key, api_key)
        providers[name] = {
            "name": p.name or name,
            "protocol": p.protocol,
            "api_key": api_key,
            "base_url": p.base_url,
        }
        if p.proxy_url:
            providers[name]["proxy_url"] = p.proxy_url

    # 恢复脱敏的 token
    github_token = body.GITHUB_TOKEN
    if "****" in github_token:
        github_token = original_github
    exa_key = body.EXA_API_KEY
    if "****" in exa_key:
        exa_key = original_exa

    # 验证模型名的 provider 前缀
    provider_names = set(providers.keys())
    for field_name in (
        "model_name",
        "mini_model_name",
        "multimodal_model_name",
        "large_model_name",
    ):
        models = getattr(body, field_name)
        for m in models:
            if "/" not in m:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name} 格式错误:'{m}' 必须是 'provider/model' 格式",
                )
            prefix = m.split("/", 1)[0]
            if prefix not in provider_names:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name} 中的 '{m}' 引用了不存在的 provider '{prefix}'",
                )

    cleaned = {
        "model_name": body.model_name,
        "mini_model_name": body.mini_model_name,
        "multimodal_model_name": body.multimodal_model_name,
        "large_model_name": body.large_model_name,
        "tts_model": body.tts_model,
        "asr_model": body.asr_model,
        "image_model": body.image_model,
        "embedding_model": body.embedding_model,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "top_p": body.top_p,
        "proxy_url": body.proxy_url,
        "GITHUB_TOKEN": github_token,
        "EXA_API_KEY": exa_key,
        "max_agent_depth": body.max_agent_depth,
        "permission_timeout": body.permission_timeout,
        "permission_mode": body.permission_mode,
        "trusted_ips": body.trusted_ips,
        "providers": providers,
    }

    # audio 配置:直接透传 dict
    cleaned["audio"] = body.audio if body.audio else None

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True, "config_path": str(path)}


async def _update_session_settings(body: SettingsUpdate) -> dict:
    """更新会话级配置(仅修改内存中的 AppConfig,不写磁盘)。"""
    session_id = body.session_id

    # 获取会话 config(优先缓存,否则从磁盘加载)
    try:
        config = await get_or_load_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

    # 更新 providers(在模型名验证之前,确保新 provider 可用于验证)
    if body.providers:
        from uniclaw.config import ProviderProfile

        # 恢复脱敏的 API key(会话级也需要检测,防止脱敏 key 覆盖原始值)
        _, original = _read_settings_raw()
        original_providers = original.get("providers", {})
        masked_to_original: dict[str, str] = {}
        for p in original_providers.values():
            orig_key = p.get("api_key", "")
            if orig_key:
                masked_to_original[_mask_key(orig_key)] = orig_key

        resolved_providers = {}
        for name, p in body.providers.items():
            api_key = p.api_key
            if "****" in api_key:
                api_key = masked_to_original.get(api_key, "")
            resolved_providers[name] = ProviderProfile(
                name=p.name or name,
                protocol=p.protocol,
                api_key=api_key,
                base_url=p.base_url,
                proxy_url=p.proxy_url,
            )
        config.providers = resolved_providers

    # 验证模型名的 provider 前缀(使用更新后的 providers)
    provider_names = set(config.providers.keys())
    for field_name in (
        "model_name",
        "mini_model_name",
        "multimodal_model_name",
        "large_model_name",
    ):
        models = getattr(body, field_name)
        for m in models:
            if "/" not in m:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name} 格式错误:'{m}' 必须是 'provider/model' 格式",
                )
            prefix = m.split("/", 1)[0]
            if prefix not in provider_names:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name} 中的 '{m}' 引用了不存在的 provider '{prefix}'",
                )

    # 更新会话级字段
    config.model_name = body.model_name
    config.mini_model_name = body.mini_model_name
    config.multimodal_model_name = body.multimodal_model_name
    config.large_model_name = body.large_model_name
    config.tts_model = body.tts_model
    config.asr_model = body.asr_model
    config.image_model = body.image_model
    config.embedding_model = body.embedding_model
    config.audio = body.audio if body.audio else None
    config.temperature = body.temperature
    config.max_tokens = body.max_tokens
    config.top_p = body.top_p
    config.proxy_url = body.proxy_url
    config.max_agent_depth = body.max_agent_depth
    config.permission_timeout = body.permission_timeout
    from uniclaw.config import Permissions

    config.permission_mode = Permissions(body.permission_mode)

    return {"ok": True, "session_id": session_id}


@router.post("/models")
async def list_models(body: dict):
    """从各 provider 获取可用模型列表(使用前端传入的 providers)。

    body 格式: {"providers": {name: {protocol, api_key, base_url, proxy_url}, ...}}
    每个 provider 只使用它自己的 proxy_url,全局 proxy_url 不用于任何模型。
    api_key 中的 **** 脱敏值会从 settings.json 恢复。
    返回普通模型和 embedding 模型,前端可根据 embedding 标记区分。
    """
    import asyncio
    from uniclaw.commands.model import fetch_openai_models, fetch_anthropic_models
    from uniclaw.utils.model_info import get_model_info_provider

    _, original = _read_settings_raw()
    original_providers = original.get("providers", {})

    raw_providers = body.get("providers", {})
    # 恢复脱敏的 api_key(通过 masked key 的前4后4字符匹配原始 key)
    masked_to_original: dict[str, str] = {}
    for p in original_providers.values():
        orig_key = p.get("api_key", "")
        if orig_key:
            masked_to_original[_mask_key(orig_key)] = orig_key
    providers: dict[str, dict] = {}
    for name, p in raw_providers.items():
        api_key = p.get("api_key", "")
        if "****" in api_key:
            api_key = masked_to_original.get(api_key, api_key)
        providers[name] = {**p, "api_key": api_key}

    all_models: list[dict] = []
    embedding_models: list[dict] = []
    providers_info: dict[str, dict] = {}

    async def _fetch(name: str, p: dict) -> tuple[list[dict], list[dict]]:
        protocol = (p.get("protocol") or "openai").lower()
        base_url = p.get("base_url") or ""
        api_key = p.get("api_key") or ""
        proxy = p.get("proxy_url") or ""
        if not base_url or not api_key:
            return [], []
        try:
            if protocol == "anthropic":
                ids = await fetch_anthropic_models(base_url, api_key, proxy)
                return [{"id": mid, "provider": name} for mid in sorted(ids)], []
            else:
                tasks = [
                    fetch_openai_models(base_url, api_key, proxy),
                    fetch_openai_models(base_url, api_key, proxy, "embeddings"),
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                normal = results[0] if not isinstance(results[0], Exception) else []
                embed = results[1] if not isinstance(results[1], Exception) else []
                normal.sort()
                embed.sort()
                return (
                    [{"id": mid, "provider": name} for mid in normal],
                    [{"id": mid, "provider": name} for mid in embed],
                )
        except Exception as e:
            get_logger("webui", Path.cwd()).warning(
                f"获取 provider {name} 模型列表失败: {e}"
            )
            return [], []

    tasks = [_fetch(name, p) for name, p in providers.items()]
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, tuple):
                normal, embed = result
                all_models.extend(normal)
                embedding_models.extend(embed)

    # 标记无法获取模型列表的 provider 为 allow_custom
    fetched_providers = {m["provider"] for m in all_models}
    for name in providers:
        providers_info[name] = {"allow_custom": name not in fetched_providers}

    # 获取模型视觉能力信息
    model_info_provider = get_model_info_provider()

    # 为每个模型添加视觉能力标记 (True/False/None 表示未知)
    for m in all_models:
        full_id = f"{m['provider']}/{m['id']}"
        info = await model_info_provider.get_model_info(full_id, fetch_details=False)
        if info:
            m["supports_vision"] = info.supports_vision
            m["supports_video"] = info.supports_video
            m["supports_audio"] = info.supports_audio
            m["supports_tools"] = info.supports_tools
            m["price"] = info.pricing if info.pricing else None
        else:
            m["supports_vision"] = None
            m["supports_video"] = None
            m["supports_audio"] = None
            m["supports_tools"] = None
            m["price"] = None

    return {
        "models": all_models,
        "embedding_models": embedding_models,
        "providers_info": providers_info,
    }


@router.post("/asr")
async def transcribe_audio(body: AsrRequest):
    """语音识别:将音频转为文字。"""
    from uniclaw.utils.audio import asr as asr_func
    from uniclaw.config import load_config

    # 使用任意 session 的 config,或新建默认 config
    config = None
    for _, cached_config in session_cache.items():
        config = cached_config
        break
    if config is None:
        config = load_config()

    if not config.asr_model:
        raise HTTPException(
            status_code=400, detail="asr_model 未配置,请通过 /model 命令设置 ASR 模型"
        )

    import base64
    import tempfile

    # 将 base64 数据写入临时文件
    audio_bytes = base64.b64decode(body.audio)
    ext = f".{body.format}" if body.format else ".webm"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        text = await asr_func(tmp_path, config)
        return {"text": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.get("/context")
async def get_context_usage(session_id: str):
    """获取上下文使用情况。"""
    try:
        config = await get_or_load_session(session_id)
        from uniclaw.commands.context_usage import analyze_context, _pct

        report = await analyze_context(config)
        return {
            "model": report.model,
            "limit": report.limit,
            "used_tokens": report.used_tokens,
            "system_prompt_tokens": report.system_prompt_tokens,
            "tool_tokens": report.tool_tokens,
            "message_tokens": report.message_tokens,
            "autocompact_tokens": report.autocompact_tokens,
            "free_tokens": report.free_tokens,
            "percentage": round(_pct(report.used_tokens, report.limit), 1),
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        get_logger("webui", Path.cwd()).error(f"获取上下文使用情况失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === 文件浏览 ===


@router.get("/files")
async def list_files(root_dir: str, path: str = "", recursive: bool = False):
    """列出项目文件。"""
    target = _validate_path(root_dir, path) if path else Path(root_dir).resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {target}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {target}")

    base = Path(root_dir).resolve()
    entries = []
    try:
        if recursive:
            all_items = sorted(target.rglob("*"))
            files = [i for i in all_items if i.is_file()]
            dirs = [i for i in all_items if i.is_dir()]
            # 按 .gitignore 规则过滤文件(含隐藏文件)
            from uniclaw.utils.gitignore import get_not_ignored_files, is_ignored_by_gitignore
            visible_files = set(get_not_ignored_files(files))
            visible_dirs = [d for d in dirs if not is_ignored_by_gitignore([d])]
            for item in visible_dirs + sorted(visible_files):
                entries.append(
                    {
                        "name": item.name,
                        "path": str(item.relative_to(base)),
                        "is_dir": item.is_dir(),
                        "size": item.stat().st_size if item.is_file() else 0,
                    }
                )
                if len(entries) >= 200:  # 限制数量
                    break
        else:
            for item in sorted(target.iterdir()):
                if item.name.startswith("."):
                    continue
                entries.append(
                    {
                        "name": item.name,
                        "path": str(item.relative_to(base)),
                        "is_dir": item.is_dir(),
                        "size": item.stat().st_size if item.is_file() else 0,
                    }
                )
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"无权限访问: {target}")
    return entries


@router.get("/files/content")
async def get_file_content(root_dir: str, path: str):
    """读取文件内容。"""
    file_path = _validate_path(root_dir, path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"不是文件: {file_path}")
    # 限制文件大小 (100MB)
    if file_path.stat().st_size > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大(>100MB)")
    try:
        content = file_path.read_text(encoding="utf-8")
        return {"content": content, "path": path}
    except UnicodeDecodeError:
        return {"content": "[二进制文件]", "path": path}


# === Checkpoint ===


@router.get("/checkpoints")
async def list_checkpoints(root_dir: str):
    """列出 checkpoint。"""
    from uniclaw.utils.checkpoint import list_checkpoints as cp_list

    # 验证 root_dir 合法性
    _validate_path(root_dir, "")
    result = await cp_list(Path(root_dir))
    return {"output": result}


@router.post("/checkpoints")
async def create_checkpoint(body: CheckpointCreate):
    """创建 checkpoint。"""
    from uniclaw.utils.checkpoint import create_checkpoint

    # 验证 root_dir 合法性
    _validate_path(body.root_dir, "")
    result = await create_checkpoint(Path(body.root_dir), body.message)
    return {"result": result}


@router.post("/checkpoints/{idx}/restore")
async def restore_checkpoint(idx: int, body: CheckpointRestore):
    """恢复 checkpoint。"""
    from uniclaw.utils.checkpoint import apply_checkpoint

    # 验证 root_dir 合法性
    _validate_path(body.root_dir, "")
    result = await apply_checkpoint(Path(body.root_dir), idx)
    return {"result": result}


@router.get("/checkpoints/diff-current")
async def diff_current(root_dir: str):
    """查看当前工作区与仓库的差异(未提交变更)。"""
    from uniclaw.utils.checkpoint import diff_current as cp_diff_current

    _validate_path(root_dir, "")
    result = await cp_diff_current(Path(root_dir))
    return {"output": result}


@router.get("/checkpoints/diff-between")
async def diff_between(from_idx: int, to_idx: int, root_dir: str):
    """比较两个 checkpoint 之间的差异。"""
    from uniclaw.utils.checkpoint import diff_between as cp_diff_between

    _validate_path(root_dir, "")
    result = await cp_diff_between(Path(root_dir), from_idx, to_idx)
    return {"output": result}


@router.get("/checkpoints/{idx}/diff")
async def diff_checkpoint(idx: int, root_dir: str):
    """查看 checkpoint diff。"""
    from uniclaw.utils.checkpoint import diff_checkpoint as cp_diff

    # 验证 root_dir 合法性
    _validate_path(root_dir, "")
    result = await cp_diff(Path(root_dir), idx)
    return {"output": result}


# === Git ===


@router.get("/git/status")
async def git_status(root_dir: str):
    """Git status。"""
    import subprocess

    # 验证 root_dir 合法性
    _validate_path(root_dir, "")
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {"output": result.stdout}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Git 命令执行超时")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Git 未安装或不在 PATH 中")
    except Exception as e:
        get_logger("webui", Path.cwd()).error(f"Git status 失败: {e}")
        raise HTTPException(status_code=500, detail="Git 命令执行失败")


@router.post("/git/commit")
async def git_commit(body: GitCommit):
    """Git commit。"""
    import subprocess

    # 验证 root_dir 合法性
    _validate_path(body.root_dir, "")
    try:
        # 暂存文件
        if body.files:
            subprocess.run(["git", "add"] + body.files, cwd=body.root_dir, check=True)
        # 提交
        result = subprocess.run(
            ["git", "commit", "-m", body.message],
            cwd=body.root_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {"output": result.stdout + result.stderr}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Git 命令执行超时")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=f"Git 命令执行失败: {e.stderr}")
    except Exception as e:
        get_logger("webui", Path.cwd()).error(f"Git commit 失败: {e}")
        raise HTTPException(status_code=500, detail="Git 提交失败")


@router.post("/git/stage")
async def git_stage(body: GitStage):
    """Git add。"""
    import subprocess

    # 验证 root_dir 合法性
    _validate_path(body.root_dir, "")
    try:
        result = subprocess.run(
            ["git", "add"] + body.files,
            cwd=body.root_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {"output": result.stdout + result.stderr}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Git 命令执行超时")
    except Exception as e:
        get_logger("webui", Path.cwd()).error(f"Git stage 失败: {e}")
        raise HTTPException(status_code=500, detail="Git 暂存失败")


@router.post("/git/unstage")
async def git_unstage(body: GitStage):
    """Git reset。"""
    import subprocess

    # 验证 root_dir 合法性
    _validate_path(body.root_dir, "")
    try:
        result = subprocess.run(
            ["git", "reset"] + body.files,
            cwd=body.root_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {"output": result.stdout + result.stderr}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Git 命令执行超时")
    except Exception as e:
        get_logger("webui", Path.cwd()).error(f"Git unstage 失败: {e}")
        raise HTTPException(status_code=500, detail="Git 取消暂存失败")


@router.post("/git/ai-commit-message")
async def git_ai_commit_message(body: GitAiCommitMessage):
    """AI 生成 commit message。"""
    from uniclaw.utils.git import git_generate_commit_message

    _validate_path(body.root_dir, "")
    log = get_logger("webui", Path.cwd())

    # 获取 config:优先从 session_cache 复用,否则新建
    config = None
    for _, cached_config in session_cache.items():
        cached_root = str(cached_config.current_agent.session.root_dir)
        if cached_root == body.root_dir:
            config = cached_config
            break

    if config is None:
        from uniclaw.config import load_config
        from uniclaw.webui.spinner import WebSpinner

        config = load_config(root_dir=Path(body.root_dir), spinner=WebSpinner())

    result = await git_generate_commit_message(Path(body.root_dir), config)

    if "error" in result:
        log.warning(f"AI commit message: {result['error']}")

    return result


# === 权限 ===


@router.get("/permissions/rules")
async def list_permission_rules(root_dir: str):
    """列出权限规则。"""
    from uniclaw.tools.security.security import list_permission_rules as list_rules

    # 验证 root_dir 合法性
    _validate_path(root_dir, "")
    return list_rules(Path(root_dir))


@router.delete("/permissions/rules")
async def delete_permission_rule(body: PermissionRuleDelete):
    """删除权限规则。"""
    from uniclaw.tools.security.security import remove_permission_rule

    # 验证 root_dir 合法性
    _validate_path(body.root_dir, "")
    remove_permission_rule(body.rule_type, body.pattern, body.root_dir)
    return {"ok": True}


# === Hooks ===


@router.get("/hooks")
async def list_hooks(root_dir: str):
    """列出 hooks 配置。"""
    from uniclaw.tools.hooks.hook_manager import load_hooks_config

    # 验证 root_dir 合法性
    _validate_path(root_dir, "")
    return load_hooks_config(Path(root_dir))


@router.put("/hooks")
async def update_hooks(body: HookUpdate):
    """更新 hooks 配置。"""
    from uniclaw.tools.hooks.hook_manager import get_hooks_path, load_all_hooks_configs

    # 验证 root_dir 合法性
    _validate_path(body.root_dir, "")
    try:
        path = get_hooks_path(Path(body.root_dir))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(body.hooks, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        load_all_hooks_configs.cache_clear()
        return {"ok": True}
    except Exception as e:
        get_logger("webui", Path.cwd()).error(f"更新 hooks 失败: {e}")
        raise HTTPException(status_code=500, detail="更新 hooks 配置失败")


@router.get("/hooks/events")
async def list_hook_events():
    """列出可用的 HookEvent 类型。"""
    from uniclaw.tools.hooks.hook_manager import HookEvent

    return [{"name": e.name, "value": e.value} for e in HookEvent]


# === 命令 ===


@router.get("/commands")
async def list_commands(root_dir: str = ""):
    """列出可用命令。"""
    from uniclaw.commands import COMMANDS, COMMAND_SUBCOMMANDS

    commands = []
    for name, handler in COMMANDS.items():
        commands.append(
            {
                "name": name,
                "description": (handler.__doc__ or "").strip().split("\n")[0],
            }
        )
    # 展开别名:/cp → /checkpoint 的子命令
    subcommands = dict(COMMAND_SUBCOMMANDS)
    for name, handler in COMMANDS.items():
        if name not in subcommands:
            for primary, primary_handler in COMMANDS.items():
                if primary in subcommands and primary_handler is handler:
                    subcommands[name] = subcommands[primary]
                    break

    # 加载 skill 触发器
    from uniclaw.tools.skill.loader import load_skills

    try:
        skills = load_skills(Path(root_dir) if root_dir else None)
        existing = {c["name"] for c in commands}
        for skill in skills:
            for trigger in skill.triggers:
                trigger_name = trigger.lstrip("/")
                if trigger_name in existing:
                    continue
                existing.add(trigger_name)
                commands.append(
                    {
                        "name": trigger_name,
                        "description": skill.description or skill.name,
                        "is_skill": True,
                    }
                )
    except Exception as e:
        get_logger("webui", Path.cwd()).warning(
            f"加载技能失败: {e}"
        )  # skill 加载失败不影响命令列表

    return {"commands": commands, "subcommands": subcommands}


# === 技能 ===


@router.get("/skills")
async def list_skills(root_dir: str = ""):
    """列出可用技能。"""
    from uniclaw.tools.skill.loader import load_skills

    # 验证 root_dir 合法性(如果提供)
    if root_dir:
        _validate_path(root_dir, "")
    skills = load_skills(Path(root_dir) if root_dir else Path.cwd())
    return [
        {"name": s.name, "description": s.description, "triggers": s.triggers}
        for s in skills
    ]


# === 后台进程管理 ===


@router.get("/monitors")
async def list_monitors():
    """列出后台进程。"""
    from uniclaw.tools.monitor.manager import MonitorManager

    manager = MonitorManager.get_instance()
    result = []
    for mid, mon in manager._monitors.items():
        result.append(
            {
                "id": mid,
                "command": mon.command,
                "description": mon.description,
                "status": (
                    mon.status.value
                    if hasattr(mon.status, "value")
                    else str(mon.status)
                ),
                "pid": mon.process.pid if mon.process else None,
            }
        )
    return result


@router.post("/monitors/{monitor_id}/stop")
async def stop_monitor(monitor_id: str):
    """停止后台进程。"""
    from uniclaw.tools.monitor.manager import MonitorManager

    manager = MonitorManager.get_instance()
    try:
        result = await manager.stop_monitor(monitor_id)
        return {"output": result}
    except Exception as e:
        get_logger("webui", Path.cwd()).error(f"停止进程失败: {e}")
        raise HTTPException(status_code=500, detail="停止进程失败")


# === 微信 Bot 管理 ===


@router.get("/wechat/bots")
async def list_wechat_bots():
    """列出已注册的微信 Bot 及其状态。"""
    manager = BotManager()
    return [
        {
            "name": bot.name,
            "is_logged_in": bot.is_logged_in,
            "credential_path": str(bot.credential_path),
        }
        for bot in manager.bots
    ]


@router.post("/wechat/bots")
async def create_wechat_bot(body: WechatBotCreate):
    """创建微信 Bot 并获取 QR URL。"""
    manager = BotManager()

    bot_name = body.name
    if not bot_name:
        # 自动生成名称
        import uuid

        bot_name = f"bot-{uuid.uuid4().hex[:8]}"

    # 检查是否已存在
    existing_bot = manager.get(bot_name)
    if existing_bot:
        # 如果已存在且已登录,返回错误
        if existing_bot.is_logged_in:
            raise HTTPException(
                status_code=400, detail=f"Bot '{bot_name}' 已存在且已登录"
            )
        # 如果已存在但未登录,删除后重新创建
        manager.remove_bot(bot_name)

    # 创建 Bot
    bot = manager.add_bot(bot_name)

    # 获取 QR URL
    try:
        qr_data = bot._get_qrcode()
        from uniclaw.ilink_bot.client import _pick

        qrcode = _pick(qr_data, "qrcode")
        qr_url = _pick(qr_data, "qrcode_img_content") or qrcode

        if not qr_url:
            raise HTTPException(status_code=500, detail="获取二维码失败")

        return {
            "bot_name": bot_name,
            "qrcode_url": qr_url,
            "qrcode": qrcode,  # 用于后续轮询登录状态
        }
    except HTTPException:
        raise
    except Exception as e:
        # 清理已创建的 Bot
        manager.remove_bot(bot_name)
        raise HTTPException(status_code=500, detail=f"获取二维码失败: {e}")


@router.post("/wechat/bots/{name}/login")
async def login_wechat_bot(name: str, qrcode: str = ""):
    """触发微信 Bot 登录流程(阻塞等待用户扫码)。"""
    import asyncio

    manager = BotManager()

    bot = manager.get(name)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot '{name}' 不存在")

    if bot.is_logged_in:
        manager._active[name] = bot
        return {"success": True, "bot_name": name, "message": "已登录"}

    if not qrcode:
        raise HTTPException(status_code=400, detail="缺少 qrcode 参数")

    # 定义状态回调,通过 WS 广播给前端
    async def on_status(status_name: str, _data: object):
        from uniclaw.webui.ws import _broadcast

        await _broadcast(
            {
                "event": "wechat_login_status",
                "bot_name": name,
                "status": status_name,
            }
        )

    try:
        result = await bot.poll_login(qrcode, on_status=on_status, poll_interval=1.0)
        if bot.is_logged_in:
            manager._active[name] = bot
            # 如果 start() 未在运行则启动(start 内部循环会自动为新 bot 创建轮询任务)
            if not manager.is_running:
                asyncio.create_task(manager.start())
            return {"success": True, "bot_name": name}
        else:
            return {"success": False, "bot_name": name, "error": "登录失败"}
    except Exception as e:
        return {"success": False, "bot_name": name, "error": str(e)}


@router.post("/wechat/bots/{name}/qrcode")
async def get_wechat_bot_qrcode(name: str):
    """获取微信 Bot 的 QR URL(用于重新登录)。"""
    manager = BotManager()

    bot = manager.get(name)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot '{name}' 不存在")

    # 清除旧的登录状态
    if bot.is_logged_in:
        bot.logout()
    if name in manager._active:
        del manager._active[name]

    # 获取新的 QR URL
    try:
        qr_data = bot._get_qrcode()
        from uniclaw.ilink_bot.client import _pick

        qrcode = _pick(qr_data, "qrcode")
        qr_url = _pick(qr_data, "qrcode_img_content") or qrcode

        if not qr_url:
            raise HTTPException(status_code=500, detail="获取二维码失败")

        return {
            "bot_name": name,
            "qrcode_url": qr_url,
            "qrcode": qrcode,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取二维码失败: {e}")


@router.delete("/wechat/bots/{name}")
async def delete_wechat_bot(name: str):
    """删除微信 Bot。"""
    manager = BotManager()
    if not manager.get(name):
        raise HTTPException(status_code=404, detail=f"Bot '{name}' 不存在")
    manager.remove_bot(name)
    return {"ok": True}


# === 文件下载 ===


@router.get("/files/download")
async def download_file(file_id: str):
    """通过临时 ID 下载文件。

    Args:
        file_id: send_file 工具生成的临时下载 ID
    """
    from fastapi.responses import FileResponse
    from uniclaw.tools.send_file import get_download

    download = get_download(file_id)
    if not download:
        raise HTTPException(status_code=404, detail="下载链接不存在或已过期")

    file_path = download["path"]
    file_name = download["name"]

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    # 限制文件大小 (1GB)
    if file_path.stat().st_size > 1024 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大(>1GB)")

    return FileResponse(
        path=str(file_path),
        filename=file_name,
        media_type="application/octet-stream",
    )


# === 子代理 ===


@router.post("/sub-agents")
async def create_sub_agent(body: SubAgentCreate):
    """创建子代理并等待结果返回。"""
    import uuid

    from uniclaw.agent import MultiAgent
    from uniclaw.config import create_sub_agent_config
    from uniclaw.tools.multi_agent.sub_agent import load_agent_definitions

    root_dir = Path(body.root_dir) if body.root_dir else None
    name = body.name or f"sub-{uuid.uuid4().hex[:8]}"

    # 创建配置
    from uniclaw.config import RunMode

    config = create_sub_agent_config(
        root_dir, name, body.prompt, run_mode=RunMode.WEBUI
    )

    # 加载 agent 定义
    agent_def = None
    for d in load_agent_definitions(root_dir):
        if d.name == body.subagent_type:
            agent_def = d
            break

    # 启动子代理并等待结果
    mgr = MultiAgent.get_instance()
    task = await mgr.start_sub_agent(body.prompt, config, agent_def=agent_def)

    from uniclaw.agent import AgentStatus

    if task.status == AgentStatus.FAILED:
        return {
            "task_id": task.id,
            "name": task.name,
            "status": "failed",
            "result": f"启动子代理失败: {task.result}",
        }
    await mgr.wait(task.id, timeout=300)

    return {
        "task_id": task.id,
        "name": task.name,
        "status": task.status,
        "result": task.result,
    }
