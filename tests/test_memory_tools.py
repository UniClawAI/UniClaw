from types import SimpleNamespace

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.memory.memory import Memory
from uniclaw.tools.memory.tools import memory_save


def test_memory_save_force_replaces_existing_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "uniclaw.tools.memory.memory.get_app_dir",
        lambda scope: tmp_path / scope,
    )

    # 创建模拟 config，提供 current_agent 以便 memory_save 使用 root_dir
    mock_config = SimpleNamespace(
        current_agent=SimpleNamespace(),
        root_dir=tmp_path,
    )
    rt = ToolRuntime(config=mock_config)

    first = memory_save.func(
        name="api-endpoint",
        description="old endpoint",
        content="Use /v1/old.",
        type="feedback",
        scope="project",
        tool_runtime=rt,
    )
    conflict = memory_save.func(
        name="api-endpoint",
        description="new endpoint",
        content="Use /v2/new.",
        type="feedback",
        scope="project",
        tool_runtime=rt,
    )
    replaced = memory_save.func(
        name="api-endpoint",
        description="new endpoint",
        content="Use /v2/new.",
        type="feedback",
        scope="project",
        force=True,
        tool_runtime=rt,
    )

    loaded = Memory.load_memory(Memory.get_memory_path(tmp_path, "api-endpoint"))

    assert "保存成功" in first
    assert "冲突" in conflict
    assert "Use /v1/old." in conflict
    assert "已强制替换" in replaced
    assert "Use /v1/old." in replaced
    assert loaded.description == "new endpoint"
    assert loaded.content == "Use /v2/new."
