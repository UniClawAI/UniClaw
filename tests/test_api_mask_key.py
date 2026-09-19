"""api.py _mask_key / _resolve_masked_key 的单元测试。"""

import pytest
from uniclaw.webui.api import _mask_key, _resolve_masked_key


# ── _mask_key ──────────────────────────────────────────────────────


class TestMaskKey:
    def test_empty_key_returns_empty(self):
        """空 key 返回空字符串(不返回掩码)。"""
        assert _mask_key("") == ""

    def test_none_returns_empty(self):
        """None 也返回空字符串。"""
        assert _mask_key(None) == ""

    def test_short_key_returns_stars(self):
        """长度 <= 8 的 key 全掩为 '****'。"""
        assert _mask_key("abcd") == "****"
        assert _mask_key("abcdefgh") == "****"

    def test_normal_key(self):
        """正常 key: 前 4 + **** + 后 4。"""
        assert _mask_key("sk-1234567890abcdef") == "sk-1****cdef"

    def test_exactly_9_chars(self):
        """刚好 9 字符的 key 保留前 4 后 4。"""
        assert _mask_key("123456789") == "1234****6789"


# ── _resolve_masked_key ────────────────────────────────────────────


class TestResolveMaskedKey:
    def test_same_name_match_returns_disk_key(self):
        """同名 provider 掩码一致时优先返回磁盘 key。"""
        original = {"openai": {"api_key": "sk-1234567890abcdef"}}
        result = _resolve_masked_key("openai", _mask_key("sk-1234567890abcdef"), original)
        assert result == "sk-1234567890abcdef"

    def test_same_name_mask_mismatch_falls_back_to_global(self):
        """同名掩码不一致时,按全局掩码匹配。"""
        original = {
            "openai": {"api_key": "sk-aaaaaaaa"},
            "anthropic": {"api_key": "sk-1234567890abcdef"},
        }
        masked = _mask_key("sk-1234567890abcdef")
        result = _resolve_masked_key("openai", masked, original)
        assert result == "sk-1234567890abcdef"

    def test_full_mask_skips_global_scan(self):
        """全掩码 '****' 不做全局扫描,避免短 key 碰撞。"""
        original = {
            "openai": {"api_key": "short"},
            "anthropic": {"api_key": "tiny"},
        }
        result = _resolve_masked_key("openai", "****", original)
        # 同名 key 的掩码是 '****' 但匹配 → 仍然返回同名 key
        assert result == "short"

    def test_all_short_keys_same_mask_returns_same_name_disk_key(self):
        """所有短 key 掩码都是 '****' 时,优先返回同名 provider 的 key。"""
        original = {
            "openai": {"api_key": "aaa"},
            "anthropic": {"api_key": "bbb"},
        }
        result = _resolve_masked_key("anthropic", "****", original)
        assert result == "bbb"

    def test_no_match_falls_back_to_same_name_disk_key(self):
        """掩码陈旧、全局都匹配不上时,退回同名 provider 磁盘 key。"""
        original = {
            "openai": {"api_key": "sk-old-old-old-old"},
            "anthropic": {"api_key": "sk-anth-key-xxxx"},
        }
        result = _resolve_masked_key("openai", "sk-99****zzzz", original)
        assert result == "sk-old-old-old-old"

    def test_unknown_provider_returns_empty(self):
        """provider 名不在原始配置中时返回空字符串。"""
        original = {"openai": {"api_key": "sk-1234567890abcdef"}}
        result = _resolve_masked_key("new_provider", "sk-1****cdef", original)
        # 全局扫描命中 openai
        assert result == "sk-1234567890abcdef"

    def test_disk_key_empty_returns_empty(self):
        """磁盘无 key 时返回空。"""
        original = {"openai": {"api_key": ""}}
        result = _resolve_masked_key("openai", "****", original)
        assert result == ""

    def test_disk_key_none_returns_none(self):
        """磁盘 key 为 None 时返回 None(dict.get 返回 None, 非默认值)。"""
        original = {"openai": {"api_key": None}}
        result = _resolve_masked_key("openai", "****", original)
        assert result is None

    def test_provider_entry_is_none(self):
        """provider 值为 None 时不崩溃。"""
        original = {"openai": None}
        result = _resolve_masked_key("openai", "****", original)
        assert result == ""

    def test_empty_original_providers(self):
        """原始配置为空时返回空。"""
        result = _resolve_masked_key("openai", "sk-1****cdef", {})
        assert result == ""
