"""上下文压缩逻辑测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.compaction import get_context_limit, get_pressure_level, PRESSURE_LEVELS

# ── get_context_limit 测试 ──────────────────────────────────


def test_get_context_limit_known_models():
    """验证已知模型的 context limit。"""
    assert get_context_limit("gpt-4o") == 128000
    assert get_context_limit("gpt-4.1") == 1000000
    assert get_context_limit(None) == 128000
    assert get_context_limit("openai/gpt-4o") == 128000


def test_get_context_limit_prefix_match():
    """前缀匹配应生效。"""
    assert get_context_limit("gpt-4o-mini-custom") == 128000


def test_get_context_limit_unknown_defaults():
    """未知模型返回默认值。"""
    assert get_context_limit("totally-unknown-model") == 128000


# ── get_pressure_level 测试 ─────────────────────────────────


def test_pressure_level_below_50():
    """40% → level -1 (不需要压缩)。"""
    assert get_pressure_level(51200, "gpt-4o") == -1


def test_pressure_level_at_50():
    """55% → level 0 (轻度压缩)。"""
    assert get_pressure_level(70400, "gpt-4o") == 0


def test_pressure_level_at_70():
    """75% → level 1 (中度压缩)。"""
    assert get_pressure_level(96000, "gpt-4o") == 1


def test_pressure_level_at_85():
    """90% → level 2 (重度压缩)。"""
    assert get_pressure_level(115200, "gpt-4o") == 2


def test_pressure_level_exact_threshold():
    """恰好在阈值上应触发对应等级。"""
    limit = get_context_limit("gpt-4o")
    assert get_pressure_level(int(limit * 0.50), "gpt-4o") == 0
    assert get_pressure_level(int(limit * 0.70), "gpt-4o") == 1
    assert get_pressure_level(int(limit * 0.85), "gpt-4o") == 2


def test_pressure_level_zero_tokens():
    """0 token → level -1。"""
    assert get_pressure_level(0, "gpt-4o") == -1


# ── Session.maybe_compact 测试 ──────────────────────────────


def _make_session(estimate_side_effects):
    """创建模拟 Session,estimate_tokens 按顺序返回指定值。"""
    from uniclaw.tools.session.session import Session

    session = Session.__new__(Session)
    session._messages = []
    session.dedup_cache = set()
    session.estimate_tokens = MagicMock(side_effect=estimate_side_effects)
    session.snip_old_tool_results = MagicMock()
    session.compact = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_below_threshold_no_action():
    """token 数低于 50% 阈值时不做任何压缩。"""
    from uniclaw.tools.session.session import Session

    session = _make_session([10000])
    config = SimpleNamespace(model_name="gpt-4o")
    result = await session.maybe_compact(config)
    assert result is False
    session.snip_old_tool_results.assert_not_called()
    session.compact.assert_not_called()


@pytest.mark.asyncio
async def test_level0_snip_only():
    """token 数在 50-70% 时,只做 snip 不做 LLM 压缩。"""
    # gpt-4o limit=128000, 50%=64000, 70%=89600
    # 70000 > 64000 → level 0; snip 后 60000 < 64000 → 停在 level 0
    session = _make_session([70000, 60000])
    config = SimpleNamespace(model_name="gpt-4o")
    result = await session.maybe_compact(config)
    assert result is True
    session.snip_old_tool_results.assert_called_once()
    session.compact.assert_not_called()


@pytest.mark.asyncio
async def test_level1_full_compact():
    """token 数在 70-85% 时,snip + LLM 摘要。"""
    # 96000 > 89600 → level 1; snip 后仍 > 64000 → compact
    session = _make_session([96000, 90000, 80000])
    config = SimpleNamespace(model_name="gpt-4o")
    result = await session.maybe_compact(config)
    assert result is True
    session.snip_old_tool_results.assert_called_once()
    session.compact.assert_called_once()


@pytest.mark.asyncio
async def test_level2_aggressive_compact():
    """token 数超过 85% 时,snip + 两次 LLM 摘要。"""
    # 116000 > 108800 → level 2
    # snip 后仍 > 64000 → compact(0.3)
    # compact(0.3) 后仍 > 89600 → compact(0.15)
    session = _make_session([116000, 110000, 95000, 80000])
    config = SimpleNamespace(model_name="gpt-4o")
    result = await session.maybe_compact(config)
    assert result is True
    session.snip_old_tool_results.assert_called_once()
    assert session.compact.call_count == 2


@pytest.mark.asyncio
async def test_unknown_model_uses_default_limit():
    """未知模型使用默认 128000 limit。"""
    session = _make_session([50000])
    config = SimpleNamespace(model_name="unknown-model")
    result = await session.maybe_compact(config)
    assert result is False
