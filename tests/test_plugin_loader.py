import asyncio
import types
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio

from uniclaw.tools.base import tool
from uniclaw.tools.plugins.loader import PluginManager
from uniclaw.tools.registry import ToolRegistry


def _write_plugin(path: Path, name: str, value: str = "ok", shutdown: bool = False):
    cleanup = (
        "\nclosed = False\ndef shutdown():\n    global closed\n    closed = True\n"
        if shutdown
        else ""
    )
    path.write_text(
        f'''from uniclaw.tools.base import tool\n\n@tool\ndef {name}() -> str:\n    """plugin tool"""\n    return {value!r}\n\ndef get_tools():\n    return [{name}]\n{cleanup}''',
        encoding="utf-8",
    )


async def test_root_dir_is_normalized(tmp_path):
    first = PluginManager.get_instance()
    second = PluginManager.get_instance()
    assert first is second
    await PluginManager.clear_instance()


async def test_load_and_hot_reload(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    path = plugin_dir / "sample.py"
    _write_plugin(path, "sample_tool", "old")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    tools = await manager.discover_and_load()
    assert [item.name for item in tools] == ["sample_tool"]
    old_tool = tools[0]

    _write_plugin(path, "new_tool", "new")
    reloaded = await manager.check_and_reload()
    assert reloaded is not None
    assert [item.name for item in reloaded] == ["new_tool"]
    assert old_tool.func is not reloaded[0].func
    await PluginManager.clear_instance()


async def test_manager_is_singleton_and_ignores_root_dir(tmp_path):
    first = PluginManager.get_instance()
    second = PluginManager.get_instance()
    assert first is second
    await PluginManager.clear_instance()


async def test_user_plugin_directory_is_shared(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    _write_plugin(plugin_dir / "shared.py", "shared_tool")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    assert [item.name for item in await manager.discover_and_load()] == ["shared_tool"]
    await PluginManager.clear_instance()


async def test_project_plugin_directory_is_ignored(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project_dir = tmp_path / "project" / "plugins" / "tools"
    project_dir.mkdir(parents=True)
    _write_plugin(project_dir / "project_only.py", "project_only")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    assert await manager.discover_and_load() == []
    await PluginManager.clear_instance()


async def test_invalid_plugin_is_skipped(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "invalid.py").write_text("x = 1\n", encoding="utf-8")
    _write_plugin(plugin_dir / "valid.py", "valid_tool")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    assert [item.name for item in await manager.discover_and_load()] == ["valid_tool"]
    await PluginManager.clear_instance()


async def test_plugin_cleanup_hook(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    path = plugin_dir / "cleanup.py"
    _write_plugin(path, "cleanup_tool", shutdown=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    await manager.discover_and_load()
    module = manager.list_plugins()[0].module
    await manager.discover_and_load()
    assert module.closed is True
    await PluginManager.clear_instance()


async def test_refresh_registers_plugin_tools(tmp_path, monkeypatch):
    """refresh() 首次调用应把插件工具注册到 ToolRegistry,分类为"插件"。"""
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    _write_plugin(plugin_dir / "sample.py", "sample_tool")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    registry = ToolRegistry()
    with patch(
        "uniclaw.tools.registry.ToolRegistry.get_instance",
        return_value=registry,
    ):
        manager = PluginManager.get_instance()
        tools = await manager.refresh()
        assert [t.name for t in tools] == ["sample_tool"]

        entry = registry.get_all_entries().get("sample_tool")
        assert entry is not None
        assert entry.category == "插件"
        assert "sample_tool" not in registry.get_core_names()

        # 无变化时 refresh() 不重复注册,返回缓存工具
        again = await manager.refresh()
        assert [t.name for t in again] == ["sample_tool"]
    await PluginManager.clear_instance()


async def test_refresh_updates_same_name_tool(tmp_path, monkeypatch):
    """同名工具内容变化后,注册表必须拿到新的 Tool 对象。"""
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    path = plugin_dir / "sample.py"
    # value 长度不同,保证 _file_state 哈希变化触发重载
    _write_plugin(path, "same_tool", "old")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    registry = ToolRegistry()
    with patch(
        "uniclaw.tools.registry.ToolRegistry.get_instance",
        return_value=registry,
    ):
        manager = PluginManager.get_instance()
        await manager.refresh()
        original_func = registry.get_all_entries()["same_tool"].tool.func

        # 工具名不变,仅内容变化
        _write_plugin(path, "same_tool", "brand_new_value")
        tools = await manager.refresh()
        assert [t.name for t in tools] == ["same_tool"]
        entry = registry.get_all_entries()["same_tool"]
        assert entry.tool is tools[0]
        assert entry.tool.func is not original_func
    await PluginManager.clear_instance()


async def test_refresh_syncs_registry_on_reload(tmp_path, monkeypatch):
    """插件重载后 refresh() 应注销已移除的旧工具并注册新增工具。"""
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    path = plugin_dir / "sample.py"
    _write_plugin(path, "old_tool")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    registry = ToolRegistry()
    with patch(
        "uniclaw.tools.registry.ToolRegistry.get_instance",
        return_value=registry,
    ):
        manager = PluginManager.get_instance()
        await manager.refresh()
        assert "old_tool" in registry.get_all_entries()

        # 重命名工具:old_tool 应被注销,renamed_tool 应被注册
        # (工具名长度不同,保证 _file_state 的 mtime+size 哈希变化)
        _write_plugin(path, "renamed_tool")
        tools = await manager.refresh()
        assert [t.name for t in tools] == ["renamed_tool"]
        assert "old_tool" not in registry.get_all_entries()
        assert "renamed_tool" in registry.get_all_entries()
    await PluginManager.clear_instance()


async def test_init_registry_keeps_plugin_category(tmp_path, monkeypatch):
    """真实启动时序(init_registry 在 refresh 之后执行)不得把插件分类覆盖为 mcp。"""
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    _write_plugin(plugin_dir / "sample.py", "sample_tool")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    from uniclaw.tools.registry import init_registry

    registry = ToolRegistry()
    with patch(
        "uniclaw.tools.registry.ToolRegistry.get_instance",
        return_value=registry,
    ):
        manager = PluginManager.get_instance()
        tools = await manager.refresh()
        # 模拟 _ensure_registry:先由 get_all_tools().refresh() 注册插件,再 init_registry 全量注册
        init_registry(tools)
        entry = registry.get_all_entries().get("sample_tool")
        assert entry is not None
        assert entry.category == "插件"

        # 无文件变化时再次 refresh 也不改变分类
        await manager.refresh()
        assert registry.get_all_entries()["sample_tool"].category == "插件"
    await PluginManager.clear_instance()


async def test_plugin_conflicts_with_builtin_tool(tmp_path, monkeypatch):
    """插件工具与内置工具(如核心工具 Read)重名时应被跳过。"""
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "conflict.py").write_text(
        '''from uniclaw.tools.base import tool\n\n@tool\ndef Read(path: str = "x") -> str:\n    """collision with builtin"""\n    return "no"\n\ndef get_tools():\n    return [Read]\n''',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    tools = await manager.discover_and_load()
    assert [t.name for t in tools] == []
    assert manager.list_plugins() == []
    await PluginManager.clear_instance()


async def test_plugin_can_import_sibling_module(tmp_path, monkeypatch):
    """插件应能 import 同目录模块(多文件拆分),且热重载后仍可用。"""
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "helper.py").write_text(
        'def greet():\n    return "hi"\n', encoding="utf-8"
    )
    path = plugin_dir / "main.py"
    path.write_text(
        '''import helper\nfrom uniclaw.tools.base import tool\n\n@tool\ndef greet_tool() -> str:\n    """greet"""\n    return helper.greet()\n\ndef get_tools():\n    return [greet_tool]\n''',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    tools = await manager.discover_and_load()
    assert [t.name for t in tools] == ["greet_tool"]
    assert await tools[0]() == "hi"

    # 重载后辅助模块应重新导入,工具仍可调用
    # (value 变化触发 _file_state 变化)
    path.write_text(
        '''import helper\nfrom uniclaw.tools.base import tool\n\n@tool\ndef greet_tool2() -> str:\n    """greet again"""\n    return helper.greet()\n\ndef get_tools():\n    return [greet_tool2]\n''',
        encoding="utf-8",
    )
    reloaded = await manager.check_and_reload()
    assert reloaded is not None
    assert [t.name for t in reloaded] == ["greet_tool2"]
    assert await reloaded[0]() == "hi"
    await PluginManager.clear_instance()


async def test_concurrent_refresh_is_safe(tmp_path, monkeypatch):
    """并发调用 refresh() 不应破坏状态(加载/卸载由锁串行化)。"""
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    for i in range(3):
        _write_plugin(plugin_dir / f"p{i}.py", f"co_tool_{i}")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    results = await asyncio.gather(*[manager.refresh() for _ in range(8)])
    names = sorted({t.name for r in results for t in r})
    assert names == ["co_tool_0", "co_tool_1", "co_tool_2"]
    assert len(manager.list_plugins()) == 3
    await PluginManager.clear_instance()


async def test_abandoned_plugin_module_name_not_leaked(tmp_path, monkeypatch):
    """跨插件工具重名冲突导致插件被弃用时,_module_names 不应残留失效模块名。"""
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "a.py").write_text(
        '''from uniclaw.tools.base import tool\n@tool\ndef t1() -> str:\n    """t1"""\n    return "1"\ndef get_tools():\n    return [t1]\n''',
        encoding="utf-8",
    )
    (plugin_dir / "b.py").write_text(
        '''from uniclaw.tools.base import tool\n@tool\ndef t1() -> str:\n    """t1 dup"""\n    return "2"\ndef get_tools():\n    return [t1]\n''',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    tools = await manager.discover_and_load()
    # 按字母序 a 先加载, b 的工具名冲突被弃用
    assert [t.name for t in tools] == ["t1"]
    assert len(manager.list_plugins()) == 1
    # 只有 a 的模块名残留,b 的已被 discard
    for plugin in manager.list_plugins():
        assert plugin.path.name == "a.py"
    assert len(manager._module_names) == 1
    await PluginManager.clear_instance()


async def test_plugin_with_required_missing_property_is_skipped(tmp_path, monkeypatch):
    """required 引用了 properties 中不存在的属性时,插件工具应被跳过。

    手写 Tool 对象的参数 schema 可能绕过 @tool 装饰器的严格生成,
    strict 模式下 provider 会拒绝这类既缺属性又被 required 引用的 schema。
    """
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "bad.py").write_text(
        '''from uniclaw.tools.base import Tool


def func(name: str) -> str:
    """hand-written tool"""
    return "no"


def get_tools():
    return [
        Tool(
            name="bad_tool",
            description="hand-written schema",
            func=func,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["missing_field"],
            },
        )
    ]
''',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    tools = await manager.discover_and_load()
    assert [t.name for t in tools] == []
    assert manager.list_plugins() == []
    await PluginManager.clear_instance()


async def test_valid_plugin_with_strict_schema_loads(tmp_path, monkeypatch):
    """required 均存在于 properties 时,手工 schema 插件应正常加载。"""
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "good.py").write_text(
        """from uniclaw.tools.base import Tool


def func(name: str) -> str:
    return name


def get_tools():
    return [
        Tool(
            name="good_tool",
            description="well-formed schema",
            func=func,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        )
    ]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    manager = PluginManager.get_instance()
    tools = await manager.discover_and_load()
    assert [t.name for t in tools] == ["good_tool"]
    assert len(manager.list_plugins()) == 1
    await PluginManager.clear_instance()


async def test_unload_evicts_loaded_plugin_from_sessions(tmp_path, monkeypatch):
    """插件文件被删除/注销后,存活会话中已加载的插件工具应被驱逐。

    插件工具加载进会话后驻留在 name2tool,若不驱逐,插件被卸载后
    agent 仍能调用旧 Tool 对象。卸载时要把工具名压入各任务的
    pending_evicted,由主循环 apply() 统一清理。
    """
    from uniclaw.agent.multi_agent import MultiAgent
    from uniclaw.tools.registry import ExtendedToolManager

    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    path = plugin_dir / "sample.py"
    _write_plugin(path, "sample_tool")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    # 模拟一个已加载插件工具、正在运行的 agent 会话
    fake_task = types.SimpleNamespace(
        id="task-1",
        extended_mgr=ExtendedToolManager(),
    )
    fake_task.extended_mgr.loaded.append("sample_tool")
    fake_task.extended_mgr.energy["sample_tool"] = 100

    real_multi = MultiAgent.get_instance()
    with patch.object(real_multi, "id2AgentTask", {"task-1": fake_task}):
        manager = PluginManager.get_instance()
        await manager.discover_and_load()
        # 删除插件文件后再次刷新,触发 _unload_all → 通知会话驱逐
        path.unlink()
        tools = await manager.refresh()
        assert [t.name for t in tools] == []

    # 插件工具应被移入 pending_evicted,apply() 会从会话工具列表移除。
    # 用真实 Tool 对象模拟会话中已驻留的旧插件工具。
    from uniclaw.tools.base import Tool

    def _stub(name: str) -> str:
        return name

    old_tool = Tool(
        name="sample_tool",
        description="plugin tool",
        func=_stub,
        parameters={"type": "object", "properties": {}},
    )
    assert "sample_tool" in fake_task.extended_mgr.pending_evicted
    tool_list = [old_tool]
    name2tool = {"sample_tool": old_tool}
    fake_task.extended_mgr.apply(tool_list, name2tool)
    assert [t.name for t in tool_list] == []
    assert "sample_tool" not in name2tool
    await PluginManager.clear_instance()
