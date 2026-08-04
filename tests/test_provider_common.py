"""tests for src/uniclaw/provider/common.py"""

from types import SimpleNamespace

from uniclaw.provider.common import (
    build_extra_body,
    compare_urls,
    is_anthropic_api,
    is_google_api,
    is_multimodal_error,
    is_openrouter_api,
    parse_model_ref,
    safe_parse_args,
)


# ── compare_urls ─────────────────────────────────────────────────


class TestCompareUrls:
    def test_identical(self):
        assert compare_urls("https://api.example.com/v1", "https://api.example.com/v1")

    def test_trailing_slash(self):
        assert compare_urls("https://api.example.com/v1/", "https://api.example.com/v1")

    def test_case_insensitive_netloc(self):
        assert compare_urls("https://API.Example.COM/v1", "https://api.example.com/v1")

    def test_different_scheme(self):
        assert not compare_urls("http://api.example.com/v1", "https://api.example.com/v1")

    def test_different_path(self):
        assert not compare_urls("https://api.example.com/v1", "https://api.example.com/v2")

    def test_completely_different(self):
        assert not compare_urls("https://a.com", "https://b.com")


# ── is_google_api / is_openrouter_api / is_anthropic_api ────────


class TestApiDetection:
    def test_google_api(self):
        assert is_google_api("https://generativelanguage.googleapis.com/v1beta/openai/")

    def test_google_api_no_trailing_slash(self):
        assert is_google_api("https://generativelanguage.googleapis.com/v1beta/openai")

    def test_not_google_api(self):
        assert not is_google_api("https://api.openai.com/v1")

    def test_openrouter_api(self):
        assert is_openrouter_api("https://openrouter.ai/api/v1/")

    def test_not_openrouter_api(self):
        assert not is_openrouter_api("https://api.openai.com/v1")

    def test_anthropic_api(self):
        assert is_anthropic_api("https://api.anthropic.com")

    def test_not_anthropic_api(self):
        assert not is_anthropic_api("https://api.openai.com")


# ── parse_model_ref ──────────────────────────────────────────────


class TestParseModelRef:
    def test_known_provider(self):
        providers = {"mimo": SimpleNamespace()}
        name, model = parse_model_ref("mimo/mimo-v2.5", providers)
        assert name == "mimo"
        assert model == "mimo-v2.5"

    def test_unknown_provider(self):
        providers = {"mimo": SimpleNamespace()}
        name, model = parse_model_ref("openai/gpt-4o", providers)
        assert name is None
        assert model == "openai/gpt-4o"

    def test_no_slash(self):
        providers = {}
        name, model = parse_model_ref("gpt-4o", providers)
        assert name is None
        assert model == "gpt-4o"

    def test_multi_slash_known_first(self):
        providers = {"openrouter": SimpleNamespace()}
        name, model = parse_model_ref("openrouter/openai/gpt-4o", providers)
        assert name == "openrouter"
        assert model == "openai/gpt-4o"

    def test_multi_slash_unknown_first(self):
        providers = {"mimo": SimpleNamespace()}
        name, model = parse_model_ref("openrouter/openai/gpt-4o", providers)
        assert name is None
        assert model == "openrouter/openai/gpt-4o"

    def test_empty_providers(self):
        name, model = parse_model_ref("any/model", {})
        assert name is None
        assert model == "any/model"


# ── build_extra_body ─────────────────────────────────────────────


class TestBuildExtraBody:
    def test_google_returns_none(self):
        result = build_extra_body(
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            enable_thinking=True,
            thinking=True,
        )
        assert result is None

    def test_openrouter_thinking_disabled(self):
        result = build_extra_body(
            "https://openrouter.ai/api/v1/",
            enable_thinking=False,
            thinking=False,
        )
        assert result is not None
        assert result["reasoning"]["effort"] == "none"

    def test_openrouter_thinking_enabled(self):
        result = build_extra_body(
            "https://openrouter.ai/api/v1/",
            enable_thinking=True,
            thinking=True,
        )
        assert result is not None
        assert "reasoning" not in result
        assert result["thinking"]["type"] == "enabled"

    def test_standard_base(self):
        result = build_extra_body(
            "https://api.openai.com/v1",
            enable_thinking=True,
            thinking=False,
        )
        assert result is not None
        assert result["enable_thinking"] is True
        assert result["thinking"]["type"] == "disabled"


# ── safe_parse_args ──────────────────────────────────────────────


class TestSafeParseArgs:
    def test_valid_json(self):
        assert safe_parse_args('{"key": "value"}') == {"key": "value"}

    def test_empty_string(self):
        assert safe_parse_args("") == {}

    def test_none(self):
        assert safe_parse_args(None) == {}

    def test_invalid_json(self):
        assert safe_parse_args("{broken") == {}

    def test_non_dict_json(self):
        assert safe_parse_args("[1, 2, 3]") == {}

    def test_json_number(self):
        assert safe_parse_args("42") == {}

    def test_json_string(self):
        assert safe_parse_args('"hello"') == {}

    def test_nested_dict(self):
        data = '{"a": {"b": 1}}'
        assert safe_parse_args(data) == {"a": {"b": 1}}


# ── is_multimodal_error ──────────────────────────────────────────


class TestIsMultimodalError:
    def _make_error(self, status_code, message):
        e = Exception(message)
        e.status_code = status_code
        return e

    def test_image_error_400(self):
        e = self._make_error(400, "image_url is not supported")
        assert is_multimodal_error(e) is True

    def test_audio_error_404(self):
        e = self._make_error(404, "input_audio not supported")
        assert is_multimodal_error(e) is True

    def test_multimodal_keyword(self):
        e = self._make_error(400, "multimodal content not allowed")
        assert is_multimodal_error(e) is True

    def test_video_keyword(self):
        e = self._make_error(400, "video_url format error")
        assert is_multimodal_error(e) is True

    def test_wrong_status_code(self):
        e = self._make_error(500, "image_url error")
        assert is_multimodal_error(e) is False

    def test_no_keyword_in_message(self):
        e = self._make_error(400, "bad request format")
        assert is_multimodal_error(e) is False

    def test_status_via_status_attr(self):
        e = Exception("image not supported")
        e.status = 400
        # remove status_code if inherited
        if hasattr(e, "status_code"):
            delattr(e, "status_code")
        assert is_multimodal_error(e) is True

    def test_no_status_attr(self):
        e = Exception("image not supported")
        assert is_multimodal_error(e) is False
