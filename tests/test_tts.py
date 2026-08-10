"""TTS 语音合成测试 — 覆盖 _build_message 和工具注册。"""

import pytest
from unittest.mock import MagicMock

from uniclaw.tools.tts.tts import _build_message
from uniclaw.tools.tts.tools import get_tools, get_all_tools


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
