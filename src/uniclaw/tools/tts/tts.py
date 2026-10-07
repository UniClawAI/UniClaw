"""TTS 工具 — 文本转语音。"""

from __future__ import annotations

import asyncio
import re
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


# ── 长文本分段 ────────────────────────────────────────────────

# 单段最大字数。实测整段长文本送 TTS 引擎会语速骤降/生成冗余
# (2115 字整段时长 1.9 倍于单遍,585 字整段慢 42%),分段合成再拼接恢复正常语速。
TTS_SEGMENT_MAX_CHARS = 200

# 句末标点:切分点只落在这些标点之后,不劈开句子
_PRIMARY_BREAKS = "。！？!?；;\n"
# 子句标点:单句超长时的二级切分点
_SECONDARY_BREAKS = "，,、：:…"

_SENTENCE_SPLIT_DOT_NEXT = "\"')」”’"


def _is_speakable(seg: str) -> bool:
    """段内是否含可朗读内容(跳过空段/纯标点段)。"""
    return any(ch.isalnum() for ch in seg)


def _split_sentences(text: str) -> list[str]:
    """按句末标点切分,标点保留在句尾。数字中的小数点不切断。"""
    sentences = []
    buf = []
    n = len(text)
    for i, ch in enumerate(text):
        buf.append(ch)
        if ch in _PRIMARY_BREAKS:
            sentences.append("".join(buf))
            buf = []
        elif ch == ".":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt == "" or nxt.isspace() or nxt in _SENTENCE_SPLIT_DOT_NEXT:
                sentences.append("".join(buf))
                buf = []
    if buf:
        sentences.append("".join(buf))
    return sentences


def _split_oversized(text: str, max_len: int) -> list[str]:
    """单句超长时:先按子句标点切并合并到 max_len,仍超长则硬切。"""
    pieces = []
    buf = []
    for ch in text:
        buf.append(ch)
        if ch in _SECONDARY_BREAKS:
            pieces.append("".join(buf))
            buf = []
    if buf:
        pieces.append("".join(buf))

    merged = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + len(piece) > max_len:
            merged.append(cur)
            cur = piece
        else:
            cur += piece
    if cur:
        merged.append(cur)

    out = []
    for seg in merged:
        while len(seg) > max_len:
            out.append(seg[:max_len])
            seg = seg[max_len:]
        if seg:
            out.append(seg)
    return out


def split_text_for_tts(text: str, max_len: int = TTS_SEGMENT_MAX_CHARS) -> list[str]:
    """按句末标点把长文本切分为多个 TTS 合成段。

    整段送 TTS 时引擎会语速骤降/生成冗余内容,分段合成再拼接可恢复正常语速。
    切分点在句末,不劈开句子;空段/纯标点段跳过。
    单句超长时退化为按子句标点切分,子句仍超长则硬切(保证段长上限)。

    Args:
        text: 要合成的完整文本。
        max_len: 单段最大字数,默认 TTS_SEGMENT_MAX_CHARS。

    Returns:
        list[str]: 合成段列表,按原文顺序。文本为空或无可朗读内容时返回空列表。
    """
    text = text.strip()
    if not text or not _is_speakable(text):
        return []
    if len(text) <= max_len:
        return [text]

    segments: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        # 超长句先拆到段长上限以内
        expanded: list[str] = []
        for sent in _split_sentences(para):
            if len(sent) <= max_len:
                expanded.append(sent)
            else:
                expanded.extend(_split_oversized(sent, max_len))
        # 贪心合并:段末落句末标点,段长不超过 max_len
        cur = ""
        for sent in expanded:
            if cur and len(cur) + len(sent) > max_len:
                if _is_speakable(cur):
                    segments.append(cur.strip())
                cur = sent
            else:
                cur += sent
        if cur and _is_speakable(cur):
            segments.append(cur.strip())
    return segments


# ── 流式回调 ──────────────────────────────────────────────────


async def _stream_with_callback(
    chunks, on_chunk: Callable[[StreamChunk], Awaitable[None]] | None = None
):
    """通用流式处理:累积 chunk 并调用回调。返回累积的 AIMessage。

    音频按字节拼接(StreamChunk 的字符串拼接会让 base64 中间的
    padding 截断后续数据,只剩第一块)。
    """
    import base64

    ai_message = None
    audio_raw_parts: list[bytes] = []
    async for chunk in chunks:
        if ai_message is None:
            ai_message = chunk
        else:
            ai_message += chunk
        if chunk.audio:
            audio_raw_parts.append(base64.b64decode(chunk.audio))
        if on_chunk and chunk.audio:
            await on_chunk(chunk)
    if ai_message is not None and audio_raw_parts:
        merged = b"".join(audio_raw_parts)
        ai_message.audio = base64.b64encode(merged).decode("ascii")
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


async def _send_wechat_voice(_chunk: StreamChunk, config) -> None:
    """将累积的音频作为文件发送到微信。"""
    import base64
    import os
    import tempfile
    from uniclaw.utils.audio import ensure_wav

    ctx = getattr(config, "wechat_ctx", None)
    if not ctx:
        return
    bot = ctx
    try:
        raw_bytes = base64.b64decode(_chunk.audio)
        # 兼容 WAV 与裸 PCM16:是 WAV 原样发,裸 PCM 包上 WAV 头
        wav_bytes = ensure_wav(raw_bytes)
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
        except Exception as e2:
            get_logger("tts").debug("TTS 微信回复失败: %s", e2)


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
        await _console_player.stop()
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

    长文本会按句末标点切分为多个合成段(单段不超过 TTS_SEGMENT_MAX_CHARS 字),
    各段分别送 TTS 引擎后按序拼接 —— 整段长文本直接合成会语速骤降/内容冗余,
    分段合成是修复手段而非绕过。切分点在句末,空段/纯标点段跳过。

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
               实际保存格式为 WAV;扩展名与实际格式不符时自动纠正
               (如传 output.mp3 实际会存为 output.wav)。
        play: 是否播放语音,默认 false。控制台模式使用本地播放,WebUI 模式流式发送到前端。

    Returns:
        操作结果描述。
    """
    from uniclaw.provider.openai_provider import astream, achat
    from uniclaw.utils.audio import concat_audio_b64

    if not text:
        return f"{TOOL_ERROR}: 文本不能为空"

    audio = dict(config.audio) if config.audio else {}
    model = config.tts_model

    if not model:
        return f"{TOOL_ERROR}: 未配置 TTS 模型,请先使用 /model 命令设置 TTS 模型"

    segments = split_text_for_tts(text)
    if not segments:
        return f"{TOOL_ERROR}: 文本无可朗读内容"

    result_parts = []
    audio_parts: list[str] = []
    total = len(segments)

    if play:
        # 流式播放:各段依次推流,播放端自然按序衔接
        import uuid

        stream_id = uuid.uuid4().hex[:8]
        on_chunk = _get_on_chunk(config, stream_id)
        for i, seg in enumerate(segments):
            chunks = astream(
                _build_message(seg, style),
                model_name=model,
                audio=audio or None,
                config=config,
            )
            ai_message = await _stream_with_callback(chunks, on_chunk)
            if ai_message is None or not ai_message.audio:
                await _finish_stream(config, None, stream_id)
                return (
                    f"{TOOL_ERROR}: 未获取到音频数据 (段 {i + 1}/{total},"
                    f"{len(seg)} 字): {seg[:30]}"
                )
            audio_parts.append(ai_message.audio)
        result_parts.append("已播放")
    else:
        # 非流式:逐段获取完整音频
        for i, seg in enumerate(segments):
            try:
                ai_message = await achat(
                    _build_message(seg, style),
                    model_name=model,
                    audio=audio or None,
                    config=config,
                )
            except Exception as e:
                return (
                    f"{TOOL_ERROR}: TTS 调用失败 (段 {i + 1}/{total},"
                    f"{len(seg)} 字): {e}"
                )
            if not ai_message.audio:
                return (
                    f"{TOOL_ERROR}: 未获取到音频数据 (段 {i + 1}/{total},"
                    f"{len(seg)} 字): {seg[:30]}"
                )
            audio_parts.append(ai_message.audio)

    # 各段按序拼接为单个 WAV(段间格式参数不一致属外部服务异常,转 TOOL_ERROR)
    try:
        combined_audio = concat_audio_b64(audio_parts)
    except ValueError as e:
        return f"{TOOL_ERROR}: 音频段拼接失败 ({total} 段): {e}"

    if play:
        from uniclaw.tools.session.session import AIMessage

        await _finish_stream(config, AIMessage(content="", audio=combined_audio), stream_id)

    # 保存文件:走 save_audio 的魔数扩展名纠正(拼接结果是带头 WAV)
    if save_path:
        from uniclaw.tools.session.session import AIMessage

        path = AIMessage(content="", audio=combined_audio).save_audio(save_path)
        result_parts.append(f"已保存到 {path}")

    if total > 1:
        result_parts.append(f"分 {total} 段合成")
    if not result_parts:
        result_parts.append("已生成语音")

    return ",".join(result_parts)
