"""自由聊天模式精简提示词(_build_free_chat_prompt)的单元测试"""

from pathlib import Path
from types import SimpleNamespace

from uniclaw.agent import AgentTask
from uniclaw.context import _build_free_chat_prompt
from uniclaw.tools.session.session import Session


def _make_free_chat_config(
    root_dir: Path, system_prompt: str | None = None
) -> SimpleNamespace:
    session = Session(root_dir=root_dir, system_prompt=system_prompt)
    task = AgentTask(name="main", prompt="", session=session)
    return SimpleNamespace(
        current_agent=task,
        is_free_chat=True,
        workspace=set(),
        run_mode="console",
    )


def test_free_chat_includes_env_section(tmp_path):
    """基础分支应包含环境段(日期/root_dir),而不只是规则"""
    config = _make_free_chat_config(tmp_path)
    text = _build_free_chat_prompt(config)
    assert "## 环境" in text
    assert "当前目录:" in text
    assert str(tmp_path) in text
    assert "当前日期:" in text


def test_free_chat_with_custom_system_prompt_includes_env(tmp_path):
    """自定义 system_prompt 分支同样追加环境段"""
    config = _make_free_chat_config(tmp_path, system_prompt="custom system prompt")
    text = _build_free_chat_prompt(config)
    assert text.startswith("custom system prompt")
    assert "## 环境" in text
    assert "当前目录:" in text
    assert str(tmp_path) in text


def test_free_chat_date_only_once(tmp_path):
    """日期只在末尾环境段出现一次(基础分支中不再重复)"""
    config = _make_free_chat_config(tmp_path)
    text = _build_free_chat_prompt(config)
    assert text.count("当前日期:") == 1
