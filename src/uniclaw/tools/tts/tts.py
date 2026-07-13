"""TTS 工具 — 文本转语音。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Awaitable
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.logger import get_logger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uniclaw.tools.session.session import StreamChunk
    from uniclaw.config import AppConfig


def _build_message(text: str, style: str) -> list[dict]:
    """构建 TTS 消息。"""
    message = []
    if style:
        message.append({"role": "user", "content": style})
    message.append({"role": "assistant", "content": text})
    return message


# ── 流式回调 ──────────────────────────────────────────────────


async def _stream_with_callback(
    chunks, on_chunk: Callable[[StreamChunk], Awaitable[None]] | None = None
):
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


async def _webui_callback(chunk: StreamChunk, session_id: str, stream_id: str):
    """WebUI 回调:流式发送音频到前端。"""
    from uniclaw.webui.ws import _broadcast

    await _broadcast(
        {
            "event": "audio_chunk",
            "session_id": session_id,
            "stream_id": stream_id,
            "audio": chunk.audio,
        }
    )


# 控制台播放器实例(延迟初始化)
_console_player = None


def _get_console_player():
    """获取或创建控制台播放器。"""
    global _console_player
    if _console_player is None:
        from uniclaw.tools.tts.player import StreamPlayer

        _console_player = StreamPlayer()
        _console_player.start()
    return _console_player


def _pcm_to_wav(
    pcm: bytes, sample_rate: int = 24000, channels: int = 1, bits: int = 16
) -> bytes:
    """给 PCM16 原始数据加上 WAV 文件头。"""
    import struct

    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_size,
    )
    return header + pcm


async def _send_wechat_voice(_chunk: StreamChunk, config) -> None:
    """将累积的音频作为文件发送到微信。"""
    import base64
    import os
    import tempfile

    ctx = getattr(config, "wechat_ctx", None)
    if not ctx:
        return
    bot = ctx
    try:
        pcm_bytes = base64.b64decode(_chunk.audio)
        wav_bytes = _pcm_to_wav(pcm_bytes)
        fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        try:
            os.write(fd, wav_bytes)
        finally:
            os.close(fd)
        try:
            bot.reply_file(tmp_path, file_name="tts.wav")
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        try:
            bot.reply_text(f"[TTS] {e}")
        except Exception:
            pass


def _get_on_chunk(
    config, stream_id: str = ""
) -> Callable[[StreamChunk], Awaitable[None]] | None:
    """根据界面类型获取回调函数。微信模式无需回调,由 _stream_with_callback 统一累积。"""
    if config.is_wechat:
        return None  # 不需要逐 chunk 回调,流式结束后通过 ai_message 统一发送
    if config.is_webui:
        session_id = config.current_agent.session.id
        return lambda chunk: _webui_callback(chunk, session_id, stream_id)
    _get_console_player()
    return _console_callback


async def _finish_stream(config, ai_message=None, stream_id: str = ""):
    """流式结束后的清理与发送。"""
    global _console_player

    if config.is_wechat:
        if ai_message and ai_message.audio:
            await _send_wechat_voice(ai_message, config)
    elif config.is_webui:
        from uniclaw.webui.ws import _broadcast

        session_id = config.current_agent.session.id
        await _broadcast(
            {"event": "audio_end", "session_id": session_id, "stream_id": stream_id}
        )
    elif _console_player:
        _console_player.stop()
        _console_player = None


# ── 语音模式: 流式 TTS 队列 ───────────────────────────────────

# 句子结束标点
_SENTENCE_ENDS = set(".!?。！？\n;；")

# per-session 状态
_tts_queues: dict[str, "TTSQueue"] = {}
_tts_accumulators: dict[str, str] = {}


class TTSQueue:
    """per-session TTS 队列,顺序处理语音段,保证音频不重叠。"""

    def __init__(self, session_id: str, config: AppConfig):
        self._session_id = session_id
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._config = config
        self._task: asyncio.Task | None = None

    async def _process(self):
        while True:
            try:
                text = await asyncio.wait_for(self._queue.get(), timeout=300)
            except asyncio.TimeoutError:
                break  # 5 分钟无新段,自动退出
            if text is None:
                break
            try:
                await tts(text=text, play=True, config=self._config)
            except Exception as e:
                get_logger("tts").warning(f"TTS 队列处理失败: {e}")
        # 自然退出: 清理 per-session 状态
        _tts_queues.pop(self._session_id, None)
        _tts_accumulators.pop(self._session_id, None)

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._process())

    async def put(self, text: str):
        await self._queue.put(text)

    async def stop(self):
        await self._queue.put(None)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass


def get_tts_queue(session_id: str, config: AppConfig) -> TTSQueue:
    """获取或创建 per-session TTS 队列。"""
    if session_id not in _tts_queues:
        q = TTSQueue(session_id, config)
        _tts_queues[session_id] = q
    q = _tts_queues[session_id]
    q.start()
    return q


def tts_enqueue(session_id: str, chunk_text: str, config: AppConfig):
    """流式文本入队:积累文本,从后往前找最后一个句子边界切分。"""
    acc = _tts_accumulators.get(session_id, "") + chunk_text

    if len(acc) >= 30:
        # 从后往前找最后一个句子边界
        for i in range(len(acc) - 1, 20, -1):
            if acc[i] in _SENTENCE_ENDS:
                text = acc[: i + 1].strip()
                remainder = acc[i + 1 :].lstrip()
                if text:
                    _tts_accumulators[session_id] = remainder
                    queue = get_tts_queue(session_id, config)
                    asyncio.create_task(queue.put(text))
                    return

    _tts_accumulators[session_id] = acc


async def tts_flush(session_id: str, config: AppConfig):
    """flush 剩余积累文本到 TTS 队列(AssistantEvent 时调用)。"""
    acc = _tts_accumulators.pop(session_id, "").strip()
    if acc:
        queue = get_tts_queue(session_id, config)
        await queue.put(acc)


async def tts_cleanup(session_id: str):
    """清理 per-session TTS 队列(EndEvent 时调用)。"""
    _tts_accumulators.pop(session_id, None)
    queue = _tts_queues.pop(session_id, None)
    if queue:
        await queue.stop()


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
        return f"{TOOL_ERROR}:文本不能为空"

    audio = dict(config.audio)
    model = config.tts_model

    if not model:
        return f"{TOOL_ERROR}:未配置 TTS 模型,请先使用 /model 命令设置 TTS 模型"

    message = _build_message(text, style)
    result_parts = []

    if play:
        # 流式播放
        import uuid

        stream_id = uuid.uuid4().hex[:8]
        on_chunk = _get_on_chunk(config, stream_id)
        chunks = astream(message, model_name=model, audio=audio or None, config=config)
        ai_message = await _stream_with_callback(chunks, on_chunk)
        await _finish_stream(config, ai_message, stream_id)
        result_parts.append("已播放")
    else:
        # 非流式:获取完整音频
        try:
            ai_message = await achat(
                message,
                model_name=model,
                audio=audio or None,
                config=config,
            )
        except Exception as e:
            return f"{TOOL_ERROR}:TTS 调用失败 - {e}"

        if not ai_message.audio:
            return f"{TOOL_ERROR}:未获取到音频数据"

    # 保存文件
    if save_path and ai_message and ai_message.audio:
        path = ai_message.save_audio(save_path)
        result_parts.append(f"已保存到 {path}")

    if not result_parts:
        result_parts.append("已生成语音")

    return ",".join(result_parts)
