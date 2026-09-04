from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.security import (
    clear_llm_safe_prompt,
    edit_llm_safe_prompt,
    llm_safe_check,
    read_llm_safe_prompt,
    write_llm_safe_prompt,
)
from uniclaw.tools.security.tools import _save_llm_safe_prompt
from uniclaw.spinner import BaseSpinner


class _MockSpinner(BaseSpinner):
    """测试用的假旋转器,不产生任何副作用。"""

    def start(self, text="waiting...", wait_id=None):
        return "mock_wait_id"

    def stop(self, wait_id=""):
        pass

    def is_active(self):
        return False

    def get_display(self):
        return ""


def _make_config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        current_agent=SimpleNamespace(),
        root_dir=tmp_path,
        mini_model_name="gpt-3.5-mini",
        model_name="gpt-3.5-mini",
        OPENAI_BASE_URL="",
        OPENAI_API_KEY="",
        multimodal_model_name=None,
        workspace=[],
        proxy_url="",
        spinner=_MockSpinner(),
    )


def test_llm_safe_prompt_tools_persist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = _make_config(tmp_path)

    assert (
        read_llm_safe_prompt.func(tool_runtime=ToolRuntime(config=config))
        == "当前未设置 llm_safe_check 注入提示词。"
    )

    assert (
        write_llm_safe_prompt.func("允许 git push", tool_runtime=ToolRuntime(config=config))
        == "已保存 llm_safe_check 注入提示词。"
    )
    assert read_llm_safe_prompt.func(tool_runtime=ToolRuntime(config=config)) == "允许 git push"

    assert "已编辑" in edit_llm_safe_prompt.func(
        "允许 git push", "允许 docker ps", tool_runtime=ToolRuntime(config=config)
    )
    assert read_llm_safe_prompt.func(tool_runtime=ToolRuntime(config=config)) == "允许 docker ps"

    assert (
        clear_llm_safe_prompt.func(tool_runtime=ToolRuntime(config=config))
        == "已清除 llm_safe_check 注入提示词。"
    )
    assert (
        read_llm_safe_prompt.func(tool_runtime=ToolRuntime(config=config))
        == "当前未设置 llm_safe_check 注入提示词。"
    )


async def test_llm_safe_check_uses_injected_system_prompt(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    captured = {}

    async def fake_achat(system_prompt, session, **kwargs):
        captured["system_prompt"] = system_prompt
        return SimpleNamespace(content='{"is_safe": true, "explanation": "OK"}')

    monkeypatch.setattr("uniclaw.provider.fallback.achat", fake_achat)
    monkeypatch.setattr(
        "uniclaw.tools.security.security._get_tool_desc", AsyncMock(return_value=None)
    )

    _save_llm_safe_prompt("允许 git push 操作", root_dir=tmp_path)

    tc = {"name": "DummyTool", "args": {"param": "value"}}
    config = _make_config(tmp_path)

    is_safe, explanation = await llm_safe_check(tc, config)

    assert is_safe is True
    assert explanation == "OK"
    assert "允许 git push 操作" in captured["system_prompt"]


async def test_llm_safe_check_uses_config_prompt(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    captured = {}

    async def fake_achat(system_prompt, session, **kwargs):
        captured["system_prompt"] = system_prompt
        return SimpleNamespace(content='{"is_safe": true, "explanation": "OK"}')

    monkeypatch.setattr("uniclaw.provider.fallback.achat", fake_achat)
    monkeypatch.setattr(
        "uniclaw.tools.security.security._get_tool_desc", AsyncMock(return_value=None)
    )

    _save_llm_safe_prompt("允许 docker logs 操作", root_dir=tmp_path)

    tc = {"name": "DummyTool", "args": {"param": "value"}}
    config = _make_config(tmp_path)

    is_safe, explanation = await llm_safe_check(tc, config)

    assert is_safe is True
    assert explanation == "OK"
    assert "允许 docker logs 操作" in captured["system_prompt"]
