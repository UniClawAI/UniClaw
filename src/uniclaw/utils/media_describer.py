from uniclaw.config import AppConfig
from uniclaw.utils.media_cache import (
    compute_hash,
    get_cached_description,
    save_description,
)

_MEDIA_PROMPTS = {
    "image": "请详细描述这张图片的内容,包括主要对象、场景、颜色、文字等信息。描述应该简洁但完整,让没有看到图片的人能够理解图片内容。",
    "audio": "请详细描述这段音频的内容,包括语音内容(如有)、音乐类型(如有)、环境声音等。描述应该简洁但完整。",
    "video": "请详细描述这段视频的内容,包括画面场景、人物动作、对话内容(如有)等。描述应该简洁但完整。",
}


def _build_content_block(media_url: str, media_type: str) -> dict:
    if media_type == "image":
        return {"type": "image_url", "image_url": {"url": media_url}}
    elif media_type == "audio":
        return {"type": "input_audio", "input_audio": {"data": media_url}}
    elif media_type == "video":
        return {
            "type": "video_url",
            "video_url": {"url": media_url},
            "fps": 2,
            "media_resolution": "default",
        }
    return {"type": "text", "text": f"[{media_type}]"}


async def describe_media(
    media_url: str, media_type: str, model_name: str, config: AppConfig
) -> str:
    content_hash = compute_hash(media_url)
    cached = get_cached_description(content_hash)
    if cached:
        return cached

    from uniclaw.provider.fallback import achat
    from uniclaw.tools.session.session import Session

    prompt = _MEDIA_PROMPTS.get(media_type, "请描述这个媒体文件的内容。")
    content_block = _build_content_block(media_url, media_type)
    _session = Session()
    _session.add_user_message(content=[{"type": "text", "text": prompt}, content_block])

    ai_message = await achat(
        "你是一个媒体描述助手。",
        _session,
        model_name=model_name,
        config=config,
    )
    description = ai_message.content or f"[{media_type} 描述生成失败]"

    save_description(content_hash, description, model_name, media_type)
    return description
