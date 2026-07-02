"""TTS 工具 — 文本转语音。"""

from __future__ import annotations

from collections.abc import Callable, Awaitable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uniclaw.tools.session.session import StreamChunk


def _build_message(text: str, style: str) -> list[dict]:
    """构建 TTS 消息。"""
    message = []
    if style:
        message.append({"role": "user", "content": style})
    message.append({"role": "assistant", "content": text})
    return message


# ── 流式回调 ──────────────────────────────────────────────────


async def _stream_with_callback(chunks, on_chunk: Callable[[StreamChunk], Awaitable[None]] | None = None):
    """通用流式处理:累积 chunk 并调用回调。返回累积的 AIMessage。"""
    ai_message = None
    async for chunk in chunks:
        if ai_message is None:
            ai_message = chunk
        else:
            ai_message += chunk
        if on_chunk and chunk.audio:
            await on_chunk(chunk)
    return ai_message


async def _console_callback(chunk: StreamChunk):
    """控制台回调:流式播放音频。"""
    pcm, sr = chunk.audio_to_pcm()
    _console_player.write(pcm, sample_rate=sr)


async def _webui_callback(chunk: StreamChunk, session_id: str):
    """WebUI 回调:流式发送音频到前端。"""
    from uniclaw.webui.ws import _broadcast

    await _broadcast({
        "event": "audio_chunk",
        "session_id": session_id,
        "audio": chunk.audio,
    })


async def _wechat_callback(_chunk: StreamChunk):
    """微信回调:暂不支持流式播放,仅累积。"""
    pass


# 控制台播放器实例(延迟初始化)
_console_player = None


def _get_console_player():
    """获取或创建控制台播放器。"""
    global _console_player
    if _console_player is None:
        from uniclaw.tools.session import StreamPlayer
        _console_player = StreamPlayer()
        _console_player.start()
    return _console_player


def _get_on_chunk(config) -> Callable[[StreamChunk], Awaitable[None]] | None:
    """根据界面类型获取回调函数。"""
    if config.is_wechat:
        return _wechat_callback
    if config.is_webui:
        session_id = config.current_agent.session.id
        return lambda chunk: _webui_callback(chunk, session_id)
    _get_console_player()
    return _console_callback


async def _finish_stream(config):
    """流式结束后的清理。"""
    global _console_player

    if config.is_wechat:
        pass  # 微信模式暂无清理操作
    elif config.is_webui:
        from uniclaw.webui.ws import _broadcast
        session_id = config.current_agent.session.id
        await _broadcast({"event": "audio_end", "session_id": session_id})
    elif _console_player:
        _console_player.stop()
        _console_player = None


# ── 主函数 ────────────────────────────────────────────────────


async def tts(
    text: str,
    style: str = "",
    save_path: str = "",
    play: bool = False,
    config=None,
) -> str:
    """将文本转换为语音。

    Args:
        text: 要转换的文本内容。支持在文本中嵌入风格标签与音频标签进行精细控制。
               风格标签:在文本开头添加括号 (风格) 标签,如"(开心)你好世界""(唱歌)歌词"。
               支持基础情绪(开心/悲伤/愤怒)、复合情绪(怅然/欣慰/无奈)、整体语调(温柔/高冷/活泼)、
               音色定位(磁性/醇厚/清亮)、人设腔调(夹子音/御姐音)、方言(东北话/四川话/粤语)、角色扮演等。
               音频标签:在文本任意位置插入 [音频标签],如[笑声][呼吸][停顿],实现细粒度控制。
        style: 说话风格控制,支持自然语言描述,如"开心""悲伤""温柔""激动"等。
               支持复杂控制:多风格切换("播报→低语→嘶吼")、多情绪混合("压抑的愤怒""带着哽咽的笑意")、
               多粒度控制(段落级基调→句子级节奏→词级重音→字粒度哽咽拖音)。
               不填则使用默认风格。
        save_path: 保存路径(如 output.wav),不填则不保存文件。
        play: 是否播放语音,默认 false。控制台模式使用本地播放,WebUI 模式流式发送到前端。

    Returns:
        操作结果描述。
    """
    from uniclaw.provider.openai_provider import astream, achat

    if not text:
        return "错误:文本不能为空"

    audio = dict(config.audio)
    model = config.tts_model

    if not model:
        return "错误:未配置 TTS 模型,请先使用 /model 命令设置 TTS 模型"

    message = _build_message(text, style)
    result_parts = []

    if play:
        # 流式播放
        on_chunk = _get_on_chunk(config)
        chunks = astream(message, model_name=model, audio=audio or None, config=config)
        ai_message = await _stream_with_callback(chunks, on_chunk)
        await _finish_stream(config)
        result_parts.append("已流式发送到前端" if config.is_webui else "已播放")
    else:
        # 非流式:获取完整音频
        try:
            ai_message = await achat(
                message, model_name=model, audio=audio or None, config=config,
            )
        except Exception as e:
            return f"错误:TTS 调用失败 - {e}"

        if not ai_message.audio:
            return "错误:未获取到音频数据"

    # 保存文件
    if save_path and ai_message and ai_message.audio:
        path = ai_message.save_audio(save_path)
        result_parts.append(f"已保存到 {path}")

    if not result_parts:
        result_parts.append("已生成语音")

    return ",".join(result_parts)
