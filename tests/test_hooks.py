import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.hooks import HookError, HookEvent, run_hooks, add_hook
from uniclaw.tools.hooks import hook_read, hook_add


def _make_task(root_dir: Path):
    """创建一个模拟的 AgentTask 用于测试。"""
    task = MagicMock()
    task.session.root_dir = root_dir
    task.session.id = "test_session"
    task.id = "test_task"
    task.name = "test"
    return task


def _python_append_command(path):
    script = (
        "import json, pathlib, sys; "
        f"p=pathlib.Path({str(path)!r}); "
        "p.write_text((p.read_text(encoding='utf-8') if p.exists() else '') + "
        "json.load(sys.stdin)['event'] + '\\n', encoding='utf-8')"
    )
    return f'"{sys.executable}" -c "{script}"'


@pytest.mark.asyncio
async def test_claude_style_hook_runs_matching_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "hook.log"
    add_hook(
        event="PreToolUse",
        commands=[_python_append_command(out)],
        matcher="Bash",
        root=tmp_path,
    )

    task = _make_task(tmp_path)
    await run_hooks(
        HookEvent.PRE_TOOL_USE,
        {"tool_name": "Bash", "args": {"command": "echo hi"}},
        config=None,
        task=task,
    )
    await run_hooks(
        HookEvent.PRE_TOOL_USE,
        {"tool_name": "Read", "args": {"file_path": "a.py"}},
        config=None,
        task=task,
    )

    assert out.read_text(encoding="utf-8") == "PreToolUse\n"


@pytest.mark.asyncio
async def test_pre_tool_hook_nonzero_blocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    add_hook(
        event="PreToolUse",
        commands=[
            f'"{sys.executable}" -c "import sys; sys.stderr.write(\'blocked\'); sys.exit(2)"'
        ],
        matcher="Bash",
        root=tmp_path,
    )

    task = _make_task(tmp_path)
    with pytest.raises(HookError) as exc:
        await run_hooks(
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"},
            config=None,
            task=task,
        )

    assert "blocked" in str(exc.value)


def test_hook_add_and_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_config = SimpleNamespace(
        current_agent=SimpleNamespace(),
        root_dir=tmp_path,
    )

    result = hook_add.func(
        event="SessionStart",
        commands="echo hi",
        name="test-hook",
        tool_runtime=ToolRuntime(config=mock_config),
    )
    assert "已添加 hook" in result

    read_output = hook_read.func(tool_runtime=ToolRuntime(config=mock_config))
    assert "SessionStart" in read_output
    assert "echo hi" in read_output
    assert "test-hook" in read_output


def test_invalid_event_rejected(tmp_path):
    with pytest.raises(ValueError, match="未知的hooks事件"):
        add_hook(event="Nope", commands=["echo hi"])
