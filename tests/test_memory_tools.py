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


def test_memory_save_rejects_confidence_out_of_range(monkeypatch, tmp_path):
    """confidence 越界(如 1.5)必须拒绝保存,报 TOOL_ERROR 而非静默成功。"""
    monkeypatch.setattr(
        "uniclaw.tools.memory.memory.get_app_dir",
        lambda scope: tmp_path / scope,
    )
    mock_config = SimpleNamespace(
        current_agent=SimpleNamespace(),
        root_dir=tmp_path,
    )
    rt = ToolRuntime(config=mock_config)

    for bad in (1.5, -0.1, 2, 100):
        result = memory_save.func(
            name=f"conf-{bad}",
            description="d",
            content="c",
            type="feedback",
            scope="project",
            confidence=bad,
            tool_runtime=rt,
        )
        assert result.startswith("[TOOL_ERROR]"), f"confidence={bad} 应被拒绝: {result}"
        assert "0~1" in result
        assert str(bad) in result

    # 边界值 0 与 1 合法
    ok = memory_save.func(
        name="conf-edge",
        description="d",
        content="c",
        type="feedback",
        scope="project",
        confidence=0.5,
        tool_runtime=rt,
    )
    assert "[TOOL_ERROR]" not in ok
    assert "保存成功" in ok or "已保存" in ok
