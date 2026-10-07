"""TTS 语音合成测试 — 覆盖 _build_message、长文本分段、音频拼接和工具注册。"""

import base64

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from uniclaw.tools.tts.tts import (
    TTS_SEGMENT_MAX_CHARS,
    _build_message,
    split_text_for_tts,
    tts,
)
from uniclaw.tools.tts.tools import get_tools, get_all_tools
from uniclaw.utils.audio import (
    concat_audio_b64,
    concat_audio_segments,
    ensure_wav,
    extract_pcm,
    pcm_to_wav,
)


def _wav(pcm: bytes, sample_rate: int = 24000, channels: int = 1, bits: int = 16) -> bytes:
    return pcm_to_wav(pcm, sample_rate=sample_rate, channels=channels, bits=bits)


def _wav_duration(wav_bytes: bytes) -> float:
    """按 PCM 字节数与采样参数计算时长。"""
    pcm, sr, ch, bits = extract_pcm(wav_bytes)
    frame_size = max(1, ch * bits // 8)
    return len(pcm) / frame_size / sr


class TestBuildMessage:
    """_build_message 消息构建测试。"""

    def test_without_style(self):
        """无风格时只有 assistant 消息。"""
        msg = _build_message("你好世界", "")
        assert len(msg) == 1
        assert msg[0]["role"] == "assistant"
        assert msg[0]["content"] == "你好世界"

    def test_with_style(self):
        """有风格时先 user 后 assistant。"""
        msg = _build_message("你好世界", "开心")
        assert len(msg) == 2
        assert msg[0]["role"] == "user"
        assert msg[0]["content"] == "开心"
        assert msg[1]["role"] == "assistant"
        assert msg[1]["content"] == "你好世界"

    def test_empty_text(self):
        """空文本。"""
        msg = _build_message("", "")
        assert len(msg) == 1
        assert msg[0]["content"] == ""

    def test_style_whitespace(self):
        """风格为空白字符串。"""
        msg = _build_message("hello", "   ")
        # 空白字符串也是 truthy,会作为 style
        assert len(msg) == 2


class TestTTSRegistration:
    """TTS 工具注册测试。"""

    def test_get_tools_with_config(self):
        """有配置时返回 1 个工具。"""
        config = MagicMock()
        config.tts_model = "tts-1"
        config.audio = {"type": "audio"}
        result = get_tools(config=config)
        assert len(result) == 1

    def test_get_tools_without_config(self):
        """无配置时返回空列表。"""
        assert get_tools(config=None) == []

    def test_get_tools_no_tts_model(self):
        """无 tts_model 时返回空列表。"""
        config = MagicMock()
        config.tts_model = ""
        config.audio = {"type": "audio"}
        assert get_tools(config=config) == []

    def test_get_tools_no_audio(self):
        """无 audio 时返回空列表。"""
        config = MagicMock()
        config.tts_model = "tts-1"
        config.audio = None
        assert get_tools(config=config) == []

    def test_get_all_tools_always_returns_one(self):
        """get_all_tools 无条件返回 1 个工具。"""
        assert len(get_all_tools()) == 1

    def test_tools_have_descriptions(self):
        """所有工具都有描述。"""
        for t in get_all_tools():
            assert t.description, f"{t.name} 缺少描述"


# ── split_text_for_tts 长文本分段 ─────────────────────────────


class TestSplitTextForTTS:
    """长文本分段策略测试。"""

    def test_short_text_single_segment(self):
        """不超过段长上限时整段返回,不切分。"""
        text = "你好世界。"
        assert split_text_for_tts(text) == [text]
        assert TTS_SEGMENT_MAX_CHARS == 200

    def test_empty_text(self):
        """空文本返回空列表。"""
        assert split_text_for_tts("") == []
        assert split_text_for_tts("   \n  ") == []

    def test_split_at_sentence_end(self):
        """切分点落在句末标点之后,不劈开句子。"""
        sent = "这是一个测试句子。"  # 9 字
        text = sent * 30  # 270 字 > 200
        segments = split_text_for_tts(text)
        assert len(segments) >= 2
        for seg in segments:
            assert len(seg) <= TTS_SEGMENT_MAX_CHARS
            # 每段都以句末标点结尾(切分点在句末)
            assert seg[-1] in "。！？!?；;."
        # 拼回原文(段间无内容丢失)
        assert "".join(segments) == text

    def test_merge_short_sentences(self):
        """短句合并到段长上限附近,不一句一段。"""
        sent = "短句。"  # 3 字
        text = sent * 50  # 150 字 <= 200 → 单段
        assert split_text_for_tts(text) == [text]
        text2 = sent * 80  # 240 字 → 2 段
        segments = split_text_for_tts(text2)
        assert 2 <= len(segments) <= 4
        assert all(len(s) <= TTS_SEGMENT_MAX_CHARS for s in segments)

    def test_skip_empty_and_punct_only(self):
        """空段/纯标点段跳过。"""
        text = "。。。\n\n" + "内容句子。" * 30 + "\n\n。。。"
        segments = split_text_for_tts(text)
        assert segments
        for seg in segments:
            assert any(ch.isalnum() for ch in seg)

    def test_oversized_sentence_secondary_split(self):
        """单句超长时按子句标点切,不丢内容。"""
        clause = "这是很长的一个子句,"
        text = clause * 40  # 一个超长"句",中间只有逗号
        segments = split_text_for_tts(text)
        assert len(segments) >= 2
        assert all(len(s) <= TTS_SEGMENT_MAX_CHARS for s in segments)
        assert "".join(segments) == text

    def test_oversized_no_punct_hard_split(self):
        """无任何标点的超长文本硬切,保证段长上限。"""
        text = "字" * 450
        segments = split_text_for_tts(text)
        assert all(len(s) <= TTS_SEGMENT_MAX_CHARS for s in segments)
        assert "".join(segments) == text

    def test_decimal_point_not_split(self):
        """数字中的小数点不切断(段末不会出现裸 "3.")。"""
        text = "圆周率是 3.14159,不是 3.14。" * 20
        segments = split_text_for_tts(text)
        for seg in segments:
            assert not seg.rstrip().endswith("3."), f"小数点被误判为句末: {seg!r}"
        assert "".join(segments) == text

    def test_paragraph_breaks(self):
        """空行分段落,段落独立成段不跨段合并。"""
        para = "这是第一段的内容。"
        text = "\n\n".join([para * 15, para * 15])
        segments = split_text_for_tts(text)
        assert len(segments) >= 2

    def test_custom_max_len(self):
        """max_len 可调。"""
        text = "句子内容啊。" * 10  # 50 字
        segments = split_text_for_tts(text, max_len=20)
        assert len(segments) >= 3
        assert all(len(s) <= 20 for s in segments)


# ── 音频拼接 ──────────────────────────────────────────────────


class TestAudioConcat:
    """extract_pcm / concat_audio_segments 测试。"""

    def test_extract_wav(self):
        pcm = b"\x01\x02\x03\x04" * 100
        wav = _wav(pcm, sample_rate=16000)
        got_pcm, sr, ch, bits = extract_pcm(wav)
        assert got_pcm == pcm
        assert sr == 16000
        assert ch == 1
        assert bits == 16

    def test_extract_raw_pcm16(self):
        raw = b"\x01\x02" * 50
        got_pcm, sr, ch, bits = extract_pcm(raw)
        assert got_pcm == raw
        assert sr == 24000

    def test_ensure_wav_wraps_raw(self):
        raw = b"\x01\x02" * 50
        wav = ensure_wav(raw)
        assert wav[:4] == b"RIFF"
        assert extract_pcm(wav)[0] == raw

    def test_ensure_wav_keeps_wav(self):
        wav = _wav(b"\x01\x02" * 10)
        assert ensure_wav(wav) == wav

    def test_concat_two_segments_duration_adds(self):
        """拼接后时长 = 各段时长之和(同采样率)。"""
        pcm_a = b"\x00\x01" * 24000  # 1 秒 @24kHz 16bit
        pcm_b = b"\x00\x01" * 48000  # 2 秒
        combined = concat_audio_segments([_wav(pcm_a), _wav(pcm_b)])
        assert combined[:4] == b"RIFF"
        assert _wav_duration(combined) == pytest.approx(3.0, abs=0.01)
        # PCM 按序拼接
        assert extract_pcm(combined)[0] == pcm_a + pcm_b

    def test_concat_mixed_wav_and_raw(self):
        pcm_a = b"\x00\x01" * 100
        raw_b = b"\x00\x02" * 100
        combined = concat_audio_segments([_wav(pcm_a), raw_b])
        assert extract_pcm(combined)[0] == pcm_a + raw_b

    def test_concat_mismatched_params_raises(self):
        """段间采样率不一致报 ValueError(调用方转 TOOL_ERROR)。"""
        with pytest.raises(ValueError, match="格式不一致"):
            concat_audio_segments(
                [_wav(b"\x00" * 10, sample_rate=24000), _wav(b"\x00" * 10, sample_rate=16000)]
            )

    def test_concat_empty_raises(self):
        with pytest.raises(ValueError):
            concat_audio_segments([])

    def test_concat_audio_b64_single_wraps_raw(self):
        """单段裸 PCM16 也会包上 WAV 头,保证 .wav 可播放。"""
        raw = b"\x01\x02" * 50
        out = base64.b64decode(concat_audio_b64([base64.b64encode(raw).decode()]))
        assert out[:4] == b"RIFF"

    def test_concat_audio_b64_multi(self):
        pcm_a = b"\x00\x01" * 24000
        pcm_b = b"\x00\x01" * 24000
        parts = [
            base64.b64encode(_wav(pcm_a)).decode(),
            base64.b64encode(_wav(pcm_b)).decode(),
        ]
        out = base64.b64decode(concat_audio_b64(parts))
        assert _wav_duration(out) == pytest.approx(2.0, abs=0.01)


# ── tts() 分段合成路径 ────────────────────────────────────────


def _fake_config():
    config = MagicMock()
    config.tts_model = "tts-1"
    config.audio = {"voice": "alloy", "format": "wav"}
    config.is_wechat = False
    config.is_webui = False
    return config


def _fake_achat_message(pcm_len: int = 2400):
    """构造返回 WAV 的假 AIMessage。"""
    from uniclaw.tools.session.session import AIMessage

    wav = _wav(b"\x00\x01" * pcm_len)
    return AIMessage(content="", audio=base64.b64encode(wav).decode())


class TestTTSSegmentedSynthesis:
    """长文本走分段合成路径。"""

    @pytest.mark.asyncio
    async def test_long_text_uses_multiple_calls(self):
        """长文本触发多次 TTS 调用,次数 = 分段数。"""
        sent = "这是测试句子内容。"  # 9 字
        text = sent * 30  # 270 字 → 分段
        segments = split_text_for_tts(text)
        assert len(segments) >= 2

        mock_achat = AsyncMock(side_effect=[_fake_achat_message() for _ in segments])
        with patch("uniclaw.provider.openai_provider.achat", mock_achat):
            result = await tts(text=text, config=_fake_config())

        assert mock_achat.await_count == len(segments)
        assert "分" in result and "段合成" in result
        # 每段送的文本都是完整句子(切分点在句末)
        for call in mock_achat.await_args_list:
            msg = call.args[0]
            seg_text = msg[-1]["content"]
            assert seg_text[-1] in "。！？!?；;."

    @pytest.mark.asyncio
    async def test_short_text_single_call(self):
        """短文本只调用一次,不走分段。"""
        mock_achat = AsyncMock(return_value=_fake_achat_message())
        with patch("uniclaw.provider.openai_provider.achat", mock_achat):
            result = await tts(text="你好世界。", config=_fake_config())
        assert mock_achat.await_count == 1
        assert "分" not in result

    @pytest.mark.asyncio
    async def test_save_path_concatenated_duration(self):
        """拼接后保存的音频时长 ≈ 各段之和,扩展名按魔数纠正。"""
        sent = "测试句子内容哦。"  # 8 字
        text = sent * 40  # 320 字 → 2+ 段
        segments = split_text_for_tts(text)
        assert len(segments) >= 2
        # 每段 1.0 秒 @24kHz (b"\x00\x01" * 24000 → 48000 字节 / 2 字节采样 / 24000Hz)
        pcm_len = 24000
        mock_achat = AsyncMock(
            side_effect=[_fake_achat_message(pcm_len=pcm_len) for _ in segments]
        )
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            target = str(Path(td) / "out.mp3")  # 故意传 .mp3,应被纠正为 .wav
            with patch("uniclaw.provider.openai_provider.achat", mock_achat):
                result = await tts(
                    text=text, save_path=target, config=_fake_config()
                )
            out_file = Path(td) / "out.wav"
            assert out_file.exists(), result
            assert not (Path(td) / "out.mp3").exists()
            wav_bytes = out_file.read_bytes()
            assert wav_bytes[:4] == b"RIFF"
            expected_sec = len(segments) * (pcm_len / 24000)
            assert _wav_duration(wav_bytes) == pytest.approx(expected_sec, abs=0.05)

    @pytest.mark.asyncio
    async def test_segment_failure_returns_error_with_context(self):
        """某段失败时返回 TOOL_ERROR 并标注段号,不假成功。"""
        text = "测试句子内容哦。" * 40
        segments = split_text_for_tts(text)
        assert len(segments) >= 2

        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("服务超时")
            return _fake_achat_message()

        mock_achat = AsyncMock(side_effect=_side_effect)
        with patch("uniclaw.provider.openai_provider.achat", mock_achat):
            result = await tts(text=text, config=_fake_config())
        from uniclaw.utils.constants import TOOL_ERROR

        assert TOOL_ERROR in result
        assert "段 2" in result
        assert "服务超时" in result

    @pytest.mark.asyncio
    async def test_empty_audio_segment_returns_error(self):
        """某段未返回音频时返回 TOOL_ERROR,不静默降质。"""
        from uniclaw.tools.session.session import AIMessage
        from uniclaw.utils.constants import TOOL_ERROR

        text = "测试句子内容哦。" * 40
        segments = split_text_for_tts(text)
        assert len(segments) >= 2

        msgs = [_fake_achat_message() for _ in segments]
        msgs[1] = AIMessage(content="", audio=None)
        mock_achat = AsyncMock(side_effect=msgs)
        with patch("uniclaw.provider.openai_provider.achat", mock_achat):
            result = await tts(text=text, config=_fake_config())
        assert TOOL_ERROR in result
        assert "未获取到音频数据" in result

    @pytest.mark.asyncio
    async def test_text_without_speakable_content(self):
        """纯标点文本报错,不假成功。"""
        from uniclaw.utils.constants import TOOL_ERROR

        mock_achat = AsyncMock()
        with patch("uniclaw.provider.openai_provider.achat", mock_achat):
            result = await tts(text="。。。", config=_fake_config())
        assert TOOL_ERROR in result
        assert mock_achat.await_count == 0
