"""tools/session/session.py 纯函数测试。

只测 token 估算、字符计数等不依赖 LLM/IO 的逻辑:
- _count_str_chars 递归字符计数
- _estimate_visual_tokens / _estimate_audio_tokens 多模态块估算
- MultimodalBlock 序列化往返
- Message.to_openai_message / to_anthropic_message / estimate_tokens
- Session.get_assistant_messages
"""

import base64
import io

import pytest
from PIL import Image

from uniclaw.tools.session.session import (
    AIMessage,
    MultimodalBlock,
    MultimodalType,
    Session,
    UserMessage,
    _count_str_chars,
    _estimate_audio_tokens,
    _estimate_visual_tokens,
)

# ── _count_str_chars ─────────────────────────────────────────


class TestCountStrChars:
    def test_plain_string(self):
        assert _count_str_chars("hello") == 5

    def test_nested_dict(self):
        assert _count_str_chars({"a": "ab", "b": {"c": "cde"}}) == 5

    def test_nested_list(self):
        assert _count_str_chars(["ab", ["cde", "f"]]) == 6

    def test_mixed_structure(self):
        obj = {"a": ["x", {"b": "yz"}], "c": 42}
        assert _count_str_chars(obj) == 3

    def test_non_string_values_ignored(self):
        assert _count_str_chars({"n": 123, "b": True, "z": None}) == 0

    def test_empty(self):
        assert _count_str_chars({}) == 0
        assert _count_str_chars([]) == 0


# ── _estimate_visual_tokens ──────────────────────────────────


def _data_url(width: int, height: int) -> str:
    img = Image.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


class TestEstimateVisualTokens:
    def test_non_visual_block(self):
        assert _estimate_visual_tokens({"type": "text", "text": "hi"}) == 0

    def test_remote_url_fixed_cost(self):
        """非 data: URL 返回固定 85 tokens。"""
        block = {"type": "image_url", "image_url": {"url": "https://x.com/a.png"}}
        assert _estimate_visual_tokens(block) == 85

    def test_data_url_tile_cost(self):
        """data: URL 按图片尺寸分块计费: 85 + tiles*170。"""
        # 512x512 恰好 1 块
        block = {"type": "image_url", "image_url": {"url": _data_url(512, 512)}}
        assert _estimate_visual_tokens(block) == 85 + 1 * 170

    def test_data_url_multi_tile(self):
        # 1024x512 → 2x1 = 2 块
        block = {"type": "image_url", "image_url": {"url": _data_url(1024, 512)}}
        assert _estimate_visual_tokens(block) == 85 + 2 * 170

    def test_partial_tile_rounds_up(self):
        # 513x513 → 2x2 = 4 块
        block = {"type": "image_url", "image_url": {"url": _data_url(513, 513)}}
        assert _estimate_visual_tokens(block) == 85 + 4 * 170

    def test_video_url_same_as_image(self):
        block = {"type": "video_url", "video_url": {"url": "https://x.com/v.mp4"}}
        assert _estimate_visual_tokens(block) == 85

    def test_corrupt_data_falls_back(self):
        """非法 base64 容错返回 500。"""
        block = {"type": "image_url", "image_url": {"url": "data:image/png;base64,!!!"}}
        assert _estimate_visual_tokens(block) == 500


class TestEstimateAudioTokens:
    def test_non_audio_block(self):
        assert _estimate_audio_tokens({"type": "text", "text": "hi"}) == 0

    def test_empty_data_min_duration(self):
        assert _estimate_audio_tokens({"type": "input_audio", "input_audio": {}}) == 100

    def test_duration_scaling(self):
        """data 长度 * 3/4 / 24000 = 秒数, 每秒 10 tokens, 下限 50。"""
        # 24000 * 4/3 字节 ≈ 1 秒 → 10 tokens
        data = "A" * 32000
        block = {"type": "input_audio", "input_audio": {"data": data}}
        result = _estimate_audio_tokens(block)
        assert result == max(50, int((32000 * 3 / 4 / 24000) * 10))


# ── MultimodalBlock ──────────────────────────────────────────


class TestMultimodalBlock:
    def test_text_roundtrip(self):
        block = MultimodalBlock(type=MultimodalType.text, text="hello")
        data = block.to_dict()
        assert data == {"type": "text", "text": "hello"}
        restored = MultimodalBlock.from_dict(data)
        assert restored.type == MultimodalType.text
        assert restored.text == "hello"

    def test_image_roundtrip(self):
        block = MultimodalBlock(
            type=MultimodalType.image_url,
            image_url={"url": "https://x.com/a.png"},
        )
        restored = MultimodalBlock.from_dict(block.to_dict())
        assert restored.image_url == {"url": "https://x.com/a.png"}


# ── Message 转换与估算 ────────────────────────────────────────


class TestMessageConversion:
    def test_user_message_openai_format(self):
        msg = UserMessage(content="hi")
        assert msg.to_openai_message() == {"role": "user", "content": "hi"}

    def test_ai_message_openai_format(self):
        msg = AIMessage(content="hello")
        result = msg.to_openai_message()
        assert result["role"] == "assistant"
        assert result["content"] == "hello"

    def test_ai_message_reasoning_content_always_present(self):
        """reasoning_content 为空时以单空格占位(OpenAI 兼容)。"""
        result = AIMessage(content="hi").to_openai_message()
        assert result["reasoning_content"] == " "
        result2 = AIMessage(content="hi", reasoning_content="思考").to_openai_message()
        assert result2["reasoning_content"] == "思考"

    def test_user_message_anthropic_format(self):
        msg = UserMessage(content="hi")
        assert msg.to_anthropic_message() == {"role": "user", "content": "hi"}

    def test_estimate_tokens_text(self):
        """纯文本估算 = (count_tokens + 4) * 1.05。"""
        msg = UserMessage(content="hello world")
        assert msg.estimate_tokens() > 0

    def test_estimate_tokens_multimodal_list(self):
        """list 内容需是 MultimodalBlock 才能参与估算(dict 不行)。"""
        msg = UserMessage(
            content=[
                MultimodalBlock(type=MultimodalType.text, text="描述图片"),
                MultimodalBlock(
                    type=MultimodalType.image_url,
                    image_url={"url": "https://x.com/a.png"},
                ),
            ]
        )
        # 图片固定 85 + 文本 + 框架开销
        assert msg.estimate_tokens() > 85

    def test_estimate_tokens_zero_content(self):
        msg = UserMessage(content="")
        # 空内容仍有每条消息 4 tokens 的框架开销
        assert msg.estimate_tokens() == int((0 + 4) * 1.05)


# ── Session.get_assistant_messages ───────────────────────────


class TestGetAssistantMessages:
    def _fill(self, session):
        session.add_user_message(content="问题")
        session._messages.append(AIMessage(content="回答一"))
        session._messages.append(AIMessage(content="回答二"))

    def test_joined_with_separator(self):
        session = Session()
        self._fill(session)
        assert session.get_assistant_messages() == "回答一\n回答二"

    def test_none_separator_returns_list(self):
        session = Session()
        self._fill(session)
        assert session.get_assistant_messages(separator=None) == ["回答一", "回答二"]

    def test_skips_user_and_empty_ai(self):
        session = Session()
        session.add_user_message(content="用户消息")
        session._messages.append(AIMessage(content=""))
        session._messages.append(AIMessage(content="有效回答"))
        assert session.get_assistant_messages(separator=None) == ["有效回答"]

    def test_empty_session(self):
        session = Session()
        assert session.get_assistant_messages() == ""
        assert session.get_assistant_messages(separator=None) == []
