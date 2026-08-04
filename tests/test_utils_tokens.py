"""tests for src/uniclaw/utils/tokens.py — token 计数与截取。"""

from unittest.mock import patch

from uniclaw.utils.tokens import MODEL_ENCODINGS, count_tokens, get_encoder, slice_by_tokens


# ── MODEL_ENCODINGS ──────────────────────────────────────────────


class TestModelEncodings:
    def test_gpt4o_mapping(self):
        assert MODEL_ENCODINGS["gpt-4o"] == "o200k_base"

    def test_gpt4_mapping(self):
        assert MODEL_ENCODINGS["gpt-4"] == "cl100k_base"

    def test_gpt35_mapping(self):
        assert MODEL_ENCODINGS["gpt-3.5-turbo"] == "cl100k_base"

    def test_all_values_are_strings(self):
        for key, val in MODEL_ENCODINGS.items():
            assert isinstance(key, str)
            assert isinstance(val, str)


# ── get_encoder ──────────────────────────────────────────────────


class TestGetEncoder:
    def test_none_model_returns_default(self):
        enc = get_encoder(None)
        # tiktoken may or may not be installed
        if enc is not None:
            assert hasattr(enc, "encode")

    def test_known_model(self):
        enc = get_encoder("gpt-4o")
        if enc is not None:
            assert hasattr(enc, "encode")

    def test_provider_prefix_stripped(self):
        enc = get_encoder("openai/gpt-4o")
        if enc is not None:
            assert hasattr(enc, "encode")

    def test_unknown_model_fallback(self):
        enc = get_encoder("totally-unknown-model-xyz")
        if enc is not None:
            assert hasattr(enc, "encode")

    def test_caching(self):
        enc1 = get_encoder("gpt-4o")
        enc2 = get_encoder("gpt-4o")
        if enc1 is not None:
            assert enc1 is enc2


# ── count_tokens ─────────────────────────────────────────────────


class TestCountTokens:
    def test_empty_string(self):
        assert count_tokens("") == 0

    def test_short_ascii(self):
        result = count_tokens("hello world")
        assert result > 0
        assert isinstance(result, int)

    def test_cjk_text(self):
        result = count_tokens("你好世界")
        assert result > 0

    def test_longer_text(self):
        text = "hello " * 100
        result = count_tokens(text)
        assert result > 10

    def test_fallback_no_tiktoken(self):
        with patch("uniclaw.utils.tokens.get_encoder", return_value=None):
            result = count_tokens("hello world")
            # fallback: int(len("hello world") / 2.8) = int(11/2.8) = 3
            assert result == int(11 / 2.8)

    def test_fallback_empty(self):
        with patch("uniclaw.utils.tokens.get_encoder", return_value=None):
            assert count_tokens("") == 0


# ── slice_by_tokens ──────────────────────────────────────────────


class TestSliceByTokens:
    def test_short_text_unchanged(self):
        text = "hello"
        result = slice_by_tokens(text, 1000)
        assert result == text

    def test_truncate_from_start(self):
        text = "word " * 200
        result = slice_by_tokens(text, 10, from_end=False)
        assert len(result) < len(text)

    def test_truncate_from_end(self):
        text = "word " * 200
        result = slice_by_tokens(text, 10, from_end=True)
        assert len(result) < len(text)

    def test_fallback_from_start(self):
        with patch("uniclaw.utils.tokens.get_encoder", return_value=None):
            text = "a" * 1000
            result = slice_by_tokens(text, 10, from_end=False)
            assert len(result) == int(10 * 2.8)

    def test_fallback_from_end(self):
        with patch("uniclaw.utils.tokens.get_encoder", return_value=None):
            text = "a" * 1000
            result = slice_by_tokens(text, 10, from_end=True)
            assert len(result) == int(10 * 2.8)
            assert result == text[-int(10 * 2.8) :]
