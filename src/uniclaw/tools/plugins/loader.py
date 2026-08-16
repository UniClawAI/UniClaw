"""工具插件加载器 — 从可信外部目录动态加载 ``@tool`` 工具。

插件目录:
- 用户级: ``~/.UniClaw/plugins/tools/``

插件是可执行 Python 代码,加载前请确认目录和文件来源可信。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uniclaw.tools.base import Tool

logger = logging.getLogger(__name__)
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 内置工具名(核心 + 扩展)的进程级缓存,用于插件与内置工具的重名检测
_BUILTIN_TOOL_NAMES: set[str] | None = None


def _builtin_tool_names() -> set[str]:
    """获取内置工具名集合;获取失败(依赖缺失等)时降级为空集,不阻塞插件加载。"""
    global _BUILTIN_TOOL_NAMES
    if _BUILTIN_TOOL_NAMES is None:
        try:
            from uniclaw.tools.registry import get_builtin_tool_names

            _BUILTIN_TOOL_NAMES = get_builtin_tool_names()
        except Exception:
            _BUILTIN_TOOL_NAMES = set()
    return _BUILTIN_TOOL_NAMES


@dataclass
class Plugin:
    """已加载的插件信息。"""

    name: str
    path: Path
    module: Any
    tools: list[Tool] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    mtime: int = 0
    module_name: str = ""


class PluginManager:
    """单例用户级插件管理器,负责插件发现、热重载和生命周期管理。"""

    _instance: "PluginManager | None" = None

    def __init__(self):
        self._plugins: dict[str, Plugin] = {}
        self._skipped: dict[str, int] = {}
        self._module_names: set[str] = set()
        self._loaded_files: dict[str, int] = {}
        self._registry_names: set[str] = set()  # 已注册到 ToolRegistry 的插件工具名
        self._lock = asyncio.Lock()  # 串行化加载/卸载/注册,防止多任务并发竞态
        self._plugin_dirs: set[str] = set()  # 为插件加入 sys.path 的目录,卸载时清理

    @classmethod
    def get_instance(cls) -> "PluginManager":
        """获取唯一的用户级插件管理器。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    async def clear_instance(cls):
        """异步卸载并清除单例。"""
        if cls._instance is not None:
            instance = cls._instance
            async with instance._lock:
                await instance._unload_all()
            cls._instance = None

    @staticmethod
    def get_plugin_directory() -> Path:
        """返回用户级插件目录。"""
        return (Path.home() / ".UniClaw" / "plugins" / "tools").resolve()

    async def discover_and_load(self) -> list[Tool]:
        """全量异步卸载旧插件并重新发现、加载用户级插件。

        加载完成后同步注册到 ToolRegistry,保证注册表始终与插件工具一致。
        """
        async with self._lock:
            return await self._discover_and_load_unlocked()

    async def check_and_reload(self) -> list[Tool] | None:
        """检测用户级插件文件变化,必要时异步重载。"""
        async with self._lock:
            return await self._recheck_unlocked()

    async def refresh(self) -> list[Tool]:
        """检测插件变化,必要时重载并同步注册表,返回当前插件工具列表。

        无变化时直接返回缓存的插件工具,避免重复注册。

        Returns:
            当前插件工具的 Tool 列表(已去重,可直接并入工具集合)。
        """
        async with self._lock:
            await self._recheck_unlocked()
            return self.get_tools()

    async def _recheck_unlocked(self) -> list[Tool] | None:
        """锁内实现:文件变化时重载,无变化返回 None。"""
        current = self._scan_files()
        if current == self._loaded_files:
            return None
        logger.info("检测到用户级插件变化,重新加载...")
        return await self._discover_and_load_unlocked()

    async def _discover_and_load_unlocked(self) -> list[Tool]:
        """锁内实现:全量卸载旧插件并重新发现、加载用户级插件。"""
        await self._unload_all()
        self._skipped.clear()
        self._loaded_files = self._scan_files()
        tools = await self._load_all()
        self._sync_registry()
        return tools

    def _sync_registry(self) -> None:
        """将当前插件工具集全量同步到 ToolRegistry。

        先注销上一轮全部插件工具,再重新注册当前全部插件工具。
        同名工具内容可能已变化,因此不能按名字跳过注册,必须
        注销旧对象后写入新 Tool 对象。
        """
        from uniclaw.tools.registry import ToolRegistry, PLUGIN_CATEGORY

        registry = ToolRegistry.get_instance()
        if self._registry_names:
            registry.unregister(*self._registry_names)
        tools = self.get_tools()
        for tool in tools:
            registry.register(tool, [], PLUGIN_CATEGORY, is_core=False)
        self._registry_names = {tool.name for tool in tools}

    def _scan_files(self) -> dict[str, int]:
        directory = self.get_plugin_directory()
        if not directory.is_dir():
            return {}
        return {
            self._file_key(path): self._file_state(path)
            for path in sorted(directory.glob("*.py"))
            if not path.name.startswith("_")
        }

    def get_tools(self) -> list[Tool]:
        """返回当前管理器的插件工具,并过滤重复工具名。"""
        tools: list[Tool] = []
        names: set[str] = set()
        for plugin in self._plugins.values():
            for tool in plugin.tools:
                if tool.name in names:
                    logger.warning(
                        "插件工具名冲突,跳过 %s (%s)", tool.name, plugin.path
                    )
                    continue
                names.add(tool.name)
                tools.append(tool)
        return tools

    def list_plugins(self) -> list[Plugin]:
        """返回当前已加载插件。"""
        return list(self._plugins.values())

    def _file_key(self, path: Path) -> str:
        return str(path.resolve()).casefold()

    @staticmethod
    def _file_state(path: Path) -> int:
        try:
            stat = path.stat()
            return (stat.st_mtime_ns << 16) ^ stat.st_size
        except OSError:
            return -1

    def _module_name(self, path: Path) -> str:
        digest = hashlib.sha256(self._file_key(path).encode()).hexdigest()[:20]
        return f"uniclaw_plugin_{digest}_{path.stem}"

    async def _unload_all(self):
        """异步调用清理钩子并移除当前 manager 创建的模块。

        同时把已注册的插件工具从 ToolRegistry 注销,保持注册表一致,
        并通知已运行会话把旧插件工具从可用工具集合中移除。
        """
        removed_names: set[str] = set()
        if self._registry_names:
            removed_names = set(self._registry_names)
            from uniclaw.tools.registry import ToolRegistry

            registry = ToolRegistry.get_instance()
            for name in self._registry_names:
                registry.unregister(name)
            self._registry_names.clear()
        for plugin in list(self._plugins.values()):
            await self._cleanup_module(plugin.module, plugin.path)
            if plugin.module_name in sys.modules:
                del sys.modules[plugin.module_name]
        self._plugins.clear()
        self._skipped.clear()
        self._module_names.clear()
        self._loaded_files.clear()
        self._drop_plugin_dir_modules()
        if removed_names:
            self._notify_sessions_remove_tools(removed_names)

    def _notify_sessions_remove_tools(self, names: set[str]) -> None:
        """通知所有存活 agent 会话把被卸载的插件工具标记为待清理。

        插件工具经 search_tools / restore_session 加载进会话后,即使插件
        文件被删除或修改,旧 Tool 对象仍驻留在会话的 name2tool 中并可被
        继续调用。通过 ExtendedToolManager.evict() 把工具名压入
        pending_evicted,agent 主循环的 apply() 会从工具列表与 name2tool
        中一并移除。

        Args:
            names: 被注销的插件工具名集合。
        """
        if not names:
            return
        try:
            from uniclaw.agent.multi_agent import MultiAgent

            tasks = list(MultiAgent.get_instance().id2AgentTask.values())
        except Exception as exc:
            logger.warning("通知会话移除插件工具失败: %s", exc)
            return
        for task in tasks:
            mgr = task.extended_mgr
            if mgr is None:
                continue
            try:
                mgr.evict(list(names))
            except Exception:
                logger.debug(
                    "会话 %s 清理插件工具失败,忽略",
                    getattr(task, "id", ""),
                    exc_info=True,
                )

    async def _cleanup_module(self, module: Any, path: Path):
        """等待插件的同步或异步清理钩子完成。"""
        hook = getattr(module, "shutdown", None) or getattr(module, "close", None)
        if not callable(hook):
            return
        try:
            result = hook()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.warning("清理插件 %s 失败: %s", path, exc)

    def _drop_plugin_dir_modules(self):
        """卸载插件目录下导入的辅助模块,并从 sys.path 移除插件目录。

        插件通过 sys.path 注入才能 import 同目录模块(如 helper.py),
        这些辅助模块会驻留在 sys.modules,若不清理,重载后仍命中旧缓存。
        """
        if not self._plugin_dirs:
            return
        dirs = [Path(d) for d in self._plugin_dirs]
        for name in list(sys.modules):
            module = sys.modules[name]
            origin = getattr(module, "__file__", None)
            if not origin:
                continue
            try:
                origin_path = Path(origin).resolve()
            except OSError:
                continue
            for d in dirs:
                try:
                    if origin_path.is_relative_to(d):
                        sys.modules.pop(name, None)
                        break
                except ValueError:
                    continue
        for d in self._plugin_dirs:
            if d in sys.path:
                sys.path.remove(d)
        self._plugin_dirs.clear()

    async def _load_all(self) -> list[Tool]:
        tools: list[Tool] = []
        tool_names: set[str] = set()
        builtin_names = _builtin_tool_names()
        directory = self.get_plugin_directory()
        if not directory.is_dir():
            return tools
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            plugin = self._load_plugin(path)
            file_key = self._file_key(path)
            if plugin is None:
                self._skipped[file_key] = self._file_state(path)
                continue
            accepted: list[Tool] = []
            for tool in plugin.tools:
                if tool.name in builtin_names:
                    logger.warning(
                        "插件 %s 的工具 %s 与内置工具重名,跳过", path, tool.name
                    )
                elif tool.name in tool_names:
                    logger.warning("插件工具名冲突,跳过 %s (%s)", tool.name, path)
                else:
                    tool_names.add(tool.name)
                    accepted.append(tool)
            plugin.tools = accepted
            if not accepted:
                await self._cleanup_module(plugin.module, path)
                sys.modules.pop(plugin.module_name, None)
                self._module_names.discard(plugin.module_name)
                continue
            self._plugins[file_key] = plugin
            tools.extend(accepted)
        return tools

    def _valid_tool(self, tool: Tool, path: Path, seen: set[str]) -> bool:
        if (
            not isinstance(tool.name, str)
            or not tool.name
            or not _TOOL_NAME_RE.fullmatch(tool.name)
        ):
            logger.warning("插件 %s 包含非法工具名 %r,跳过", path, tool.name)
            return False
        if tool.name in seen:
            logger.warning("插件 %s 内工具名重复 %s,跳过", path, tool.name)
            return False
        if (
            not callable(tool.func)
            or not isinstance(tool.description, str)
            or not tool.description.strip()
        ):
            logger.warning("插件 %s 的工具 %s 元数据无效,跳过", path, tool.name)
            return False
        schema = tool.parameters
        if (
            not isinstance(schema, dict)
            or schema.get("type") != "object"
            or not isinstance(schema.get("properties", {}), dict)
        ):
            logger.warning("插件 %s 的工具 %s 参数 schema 无效,跳过", path, tool.name)
            return False
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(
            isinstance(name, str) for name in required
        ):
            logger.warning("插件 %s 的工具 %s required 无效,跳过", path, tool.name)
            return False
        # required 中引用的属性必须存在于 properties 中,否则 strict 模式下
        # provider 会在运行时拒绝该工具 schema,导致整轮 LLM 调用失败。
        properties = schema.get("properties", {})
        if not all(name in properties for name in required):
            logger.warning(
                "插件 %s 的工具 %s required 引用了不存在的属性,跳过", path, tool.name
            )
            return False
        return True

    def _load_plugin(self, path: Path) -> Plugin | None:
        """动态导入单个插件文件并提取 ``get_tools()`` 返回的工具。"""
        path = path.resolve()
        module_name = self._module_name(path)
        # 插件目录加入 sys.path,使插件可以 import 同目录模块(拆分多文件)。
        # 追加到末尾,不抢占标准库/已安装包的解析优先级;
        # 目录在 _unload_all 时统一移除,避免污染进程 sys.path。
        plugin_dir = str(path.parent)
        if plugin_dir not in sys.path:
            sys.path.append(plugin_dir)
        self._plugin_dirs.add(plugin_dir)
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if spec is None or spec.loader is None:
                logger.warning("无法创建模块 spec: %s", path)
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            self._module_names.add(module_name)
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.warning("加载插件 %s 失败: %s", path, exc)
            sys.modules.pop(module_name, None)
            self._module_names.discard(module_name)
            return None

        get_fn = getattr(module, "get_tools", None)
        if not callable(get_fn):
            logger.warning("插件 %s 缺少 get_tools() 函数,跳过", path)
            sys.modules.pop(module_name, None)
            self._module_names.discard(module_name)
            return None
        try:
            tools = get_fn()
        except Exception as exc:
            logger.warning("插件 %s 的 get_tools() 执行失败: %s", path, exc)
            sys.modules.pop(module_name, None)
            self._module_names.discard(module_name)
            return None
        if not isinstance(tools, (list, tuple)) or not tools:
            logger.warning("插件 %s 的 get_tools() 必须返回非空列表", path)
            sys.modules.pop(module_name, None)
            self._module_names.discard(module_name)
            return None

        valid_tools: list[Tool] = []
        seen: set[str] = set()
        for tool in tools:
            if isinstance(tool, Tool) and self._valid_tool(tool, path, seen):
                seen.add(tool.name)
                valid_tools.append(tool)
            elif not isinstance(tool, Tool):
                logger.warning("插件 %s 返回了非 Tool 对象: %s", path, type(tool))
        if not valid_tools:
            sys.modules.pop(module_name, None)
            self._module_names.discard(module_name)
            return None

        meta = getattr(module, "PLUGIN_META", {})
        return Plugin(
            name=path.stem,
            path=path,
            module=module,
            tools=valid_tools,
            meta=meta if isinstance(meta, dict) else {},
            mtime=self._file_state(path),
            module_name=module_name,
        )
