import json

from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR

from uniclaw.context import Scope
from uniclaw.tools.hooks.hook_manager import (
    VALID_EVENTS,
    add_hook,
    get_hooks_path,
    remove_hook,
    load_all_hooks_configs,
)

# ── 系统提示词(静态常量,最大化 LLM 缓存命中) ──────────────

_HOOKS_SYSTEM_PROMPT = """\
# Hook 机制
Hook 是在特定事件触发时自动执行的 shell 命令,可通过 {read}、{add}、{remove} 工具管理。
**⚠️ PreToolUse hook 非零退出码会阻止工具调用,hook 命令有 bug 可能导致死锁。**
使用 hook 前请先通过 {skill_read} 加载 "hooks" 技能查看完整文档(事件列表、参数传递、环境变量等)。"""


def get_hooks_system_prompt() -> str:
    """返回 Hook 机制的系统提示词(静态内容,适合放在缓存前缀区域)。"""
    from uniclaw.tools.skill.tools import skill_read

    return _HOOKS_SYSTEM_PROMPT.format(
        read=hook_read.name,
        add=hook_add.name,
        remove=hook_remove.name,
        skill_read=skill_read.name,
    )


@tool
def hook_read(tool_runtime: ToolRuntime = None) -> str:
    """
    读取全部 hooks 配置。Hook 是在特定事件触发时自动执行的 shell 命令。
    输出先项目级后用户级,每个 hook 显示 id、名称、事件、匹配器和命令。
    使用前请先通过 skill_read 加载 "hooks" 技能查看完整文档。

    """
    config = tool_runtime.config
    if not config or not config.current_agent:
        raise ValueError("hook_read 需要 config 中的 current_agent 来获取 root_dir")
    root_dir = config.root_dir
    lines = []
    for scope, cfg in load_all_hooks_configs(root_dir):
        lines.append(f"=== {scope} 级 hooks ===")
        hooks = cfg.get("hooks", {})
        found = False
        for event, entries in hooks.items():
            for entry in entries:
                found = True
                entry_id = entry.get("id", "")
                name = entry.get("name", "")
                matcher = entry.get("matcher") or "*"
                commands = [h["command"] for h in entry.get("hooks", [])]
                label = f"[{entry_id}]"
                if name:
                    label += f" {name}"
                lines.append(f"  {label} event={event} matcher={matcher}")
                for cmd in commands:
                    lines.append(f"    -> {cmd}")
        if not found:
            lines.append("  (无)")
    return "\n".join(lines)


@tool
def hook_add(
    event: str,
    commands: str,
    name: str = "",
    matcher: str = "",
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    添加单条 hook。在特定事件触发时自动执行 shell 命令。
    使用前请先通过 skill_read 加载 "hooks" 技能查看完整文档(事件列表、参数传递、注意事项)。

    Args:
        event: 触发事件,详见 "hooks" 技能文档。
        commands: 要执行的 shell 命令,多个命令用换行分隔(如 "echo step1\\necho step2")。
        name: 可选的人类可读名称(如 "block-rm"),便于后续按名删除。
        matcher: 可选的工具名匹配器,支持通配符(*,?)和多模式(|或,分隔)。如 "Bash","Read|Write"。不指定则匹配所有。
        scope: 'project'(项目级)或 'user'(用户级)。

    Returns:
        str: 操作结果消息。
    """
    config = tool_runtime.config
    if not config or not config.current_agent:
        raise ValueError("hook_add 需要 config 中的 current_agent 来获取 root_dir")
    root_dir = config.root_dir
    cmd_list = [c.strip() for c in commands.strip().split("\n") if c.strip()]
    if scope == Scope.PROJECT and root_dir is None:
        return (
            f"{TOOL_ERROR}: 当前会话无工作目录,无法添加项目级 hook,请使用 scope='user'"
        )
    if event not in VALID_EVENTS:
        return f"{TOOL_ERROR}: 未知事件 '{event}',可选: {', '.join(sorted(VALID_EVENTS))}"
    if not cmd_list:
        return f"{TOOL_ERROR}: commands 不能为空,请提供至少一条 shell 命令"
    root = root_dir if scope == Scope.PROJECT else Scope.USER
    try:
        new_id = add_hook(
            event=event,
            commands=cmd_list,
            name=name or None,
            matcher=matcher or None,
            root=root,
        )
    except json.JSONDecodeError as e:
        return (
            f"{TOOL_ERROR}: hooks 配置文件 JSON 解析失败"
            f"(路径: {get_hooks_path(root)}): {e}"
        )
    except ValueError as e:
        return (
            f"{TOOL_ERROR}: hooks 配置校验失败"
            f"(路径: {get_hooks_path(root)}, 事件: '{event}'): {e}"
        )
    except OSError as e:
        return (
            f"{TOOL_ERROR}: 读写 hooks 配置失败"
            f"(路径: {get_hooks_path(root)}, 事件: '{event}'): {type(e).__name__}: {e}"
        )
    label = f"[{new_id}]"
    if name:
        label += f" {name}"
    return f"已添加 hook {label},事件={event},命令数={len(cmd_list)}"


@tool
def hook_remove(id_or_name: str, tool_runtime: ToolRuntime = None) -> str:
    """
    删除单条 hook。Hook 是在特定事件触发时自动执行的 shell 命令。
    根据 id 或 name 删除,自动搜索项目级和用户级配置。
    使用前请先通过 skill_read 加载 "hooks" 技能查看完整文档。

    Args:
        id_or_name: hook 的 id(如 "a3f8c2")或 name(如 "block-rm")。

    Returns:
        str: 操作结果消息。
    """
    config = tool_runtime.config
    if not config or not config.current_agent:
        raise ValueError("hook_remove 需要 config 中的 current_agent 来获取 root_dir")
    root_dir = config.root_dir
    try:
        removed = remove_hook(id_or_name, root_dir)
    except OSError as e:
        return (
            f"{TOOL_ERROR}: 写入 hooks 配置失败"
            f"(id/名称: '{id_or_name}'): {type(e).__name__}: {e}"
        )
    if removed:
        return f"已删除 hook: {id_or_name}"
    return f"{TOOL_ERROR}: 未找到 hook: {id_or_name}"


def get_tools() -> list:
    return [hook_read, hook_add, hook_remove]


def get_all_tools() -> list:
    return get_tools()
