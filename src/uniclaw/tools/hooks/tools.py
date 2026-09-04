import sys

from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR

from uniclaw.context import Scope
from uniclaw.tools.hooks.hook_manager import (
    HookEvent,
    add_hook,
    remove_hook,
    load_all_hooks_configs,
)

# ── 系统提示词(静态常量,最大化 LLM 缓存命中) ──────────────

_HOOKS_SYSTEM_PROMPT = """\
# Hook 机制
Hook 是在特定事件触发时自动执行的 shell 命令,可通过 {read}、{add}、{remove} 工具管理。
**⚠️ PreToolUse hook 非零退出码会阻止工具调用,hook 命令有 bug 可能导致死锁。**
使用 hook 前请先调用 {docs} 查看完整文档(事件列表、参数传递、环境变量等)。"""


def get_hooks_system_prompt() -> str:
    """返回 Hook 机制的系统提示词(静态内容,适合放在缓存前缀区域)。"""
    return _HOOKS_SYSTEM_PROMPT.format(
        read=hook_read.name,
        add=hook_add.name,
        remove=hook_remove.name,
        docs=hook_docs.name,
    )


# 事件定义: (事件枚举, 简介, stdin 特有字段)
_HOOK_EVENTS = [
    (HookEvent.SESSION_START, "会话启动时触发(仅主代理)", "user_message, depth"),
    (HookEvent.SESSION_END, "会话结束时触发(仅主代理)", "status, depth"),
    (
        HookEvent.PRE_TOOL_USE,
        "工具调用前触发(**非零退出码会阻止工具调用**)",
        "tool_name, tool_call, args",
    ),
    (HookEvent.POST_TOOL_USE, "工具调用后触发", "tool_name, tool_call, args, result"),
    (
        HookEvent.PRE_ASSISTANT,
        "助手回复前触发(流式响应完成后)",
        "content, tool_calls, in_tokens, out_tokens, model_name",
    ),
    (
        HookEvent.PERMISSION_REQUEST,
        "权限请求时触发(**非零退出码会拒绝权限**)",
        "tool_name, tool_call, args, description, explanation",
    ),
    (
        HookEvent.PERMISSION_RESPONSE,
        "权限响应后触发",
        "tool_name, tool_call, args, permitted, response",
    ),
]


@tool
def hook_docs() -> str:
    """
    查看 Hook 机制的完整文档。使用 hook_read/hook_add/hook_remove 前请先阅读本文档。
    返回配置格式、作用域、事件详解、matcher 规则、命令执行环境、阻塞机制、安全注意事项等。
    """
    windows_note = ""
    if sys.platform == "win32":
        windows_note = (
            "\n\n**Windows 编码注意**: Windows 上 cmd 默认非 UTF-8 编码,"
            "读取 stdin JSON 中的中文会乱码。"
            "需在命令前加 `chcp 65001 >nul` 切换代码页,保存文件时用 UTF-8 无 BOM 编码。"
        )
    event_list = "\n".join(f"- `{name}`: {desc}" for name, desc, _ in _HOOK_EVENTS)
    stdin_fields = "\n".join(
        f"- `{name}`: {fields}" for name, _, fields in _HOOK_EVENTS
    )
    return (
        "# Hook 完整文档\n"
        "\n"
        "Hook 是在特定事件触发时自动执行的 shell 命令,可用于:工具调用前后检查、"
        "阻止危险操作、日志审计、会话生命周期管理等。\n"
        f"通过 {hook_read.name}、{hook_add.name}、{hook_remove.name} 管理 hook。\n"
        "\n"
        "## 配置文件\n"
        "\n"
        f"配置由 {hook_add.name}/{hook_remove.name} 管理,分为项目级和用户级,两级同时生效。\n"
        f"请勿直接编辑配置文件,始终通过工具操作。通过 {hook_read.name} 查看当前配置。\n"
        "\n"
        "## 作用域\n"
        "\n"
        f"- `project`: 存储在当前项目目录下,通过 {hook_add.name} 的 `scope='project'` 添加(默认)\n"
        f"- `user`: 存储在用户主目录下,通过 {hook_add.name} 的 `scope='user'` 添加\n"
        "两级配置同时加载、同时执行,项目级先执行。\n"
        "\n"
        "## 可用事件\n"
        "\n"
        f"{event_list}\n"
        "\n"
        "其中 `PreToolUse` 和 `PermissionRequest` 是**阻塞事件**:命令返回非零退出码时会阻止操作。\n"
        "其余事件仅记录,不影响执行流程。\n"
        "\n"
        "### 各事件 stdin JSON 特有字段\n"
        "\n"
        f"{stdin_fields}\n"
        "\n"
        "## Matcher 匹配规则\n"
        "\n"
        "matcher 用于限定 hook 只对特定工具生效,支持:\n"
        "- **通配符**: `*`(任意字符)、`?`(单个字符),如 `*Tool*`、`Bash?`\n"
        "- **多模式**: `|` 或 `,` 分隔,如 `Bash|Read|Write`\n"
        "- **默认**: 不指定或为 `*` 时匹配所有工具\n"
        "匹配使用 `fnmatchcase`(区分大小写)。\n"
        "\n"
        "## 命令执行环境\n"
        "\n"
        "每条 hook 命令以独立子进程运行:\n"
        "- **工作目录**: 当前会话的 root_dir\n"
        "- **超时**: 默认 30 秒,可在 hooks.json 中通过 `timeout` 字段自定义(秒)\n"
        "- **stdin**: JSON 对象,包含通用字段 + 事件特有字段\n"
        "- **stdout/stderr**: 正常捕获,失败时写入日志\n"
        "\n"
        "### 环境变量(所有事件通用)\n"
        "\n"
        "- `UNICLAW_HOOK_EVENT`: 触发事件名(如 `PreToolUse`)\n"
        "- `UNICLAW_HOOK_CWD`: 当前工作目录\n"
        "- `UNICLAW_HOOK_SCOPE`: 作用域(`project` 或 `user`)\n"
        "- `UNICLAW_HOOK_TOOL`: 工具名(仅工具相关事件,其他事件为空)\n"
        "\n"
        "注意:包含 KEY/SECRET/TOKEN/PASSWORD/CREDENTIAL 的环境变量会被自动过滤,不会传递给 hook 命令。\n"
        "\n"
        "### stdin JSON 通用字段\n"
        "\n"
        "- `event`: 触发事件名\n"
        "- `cwd`: 当前工作目录\n"
        "- `session_id`: 会话 ID\n"
        "- `task_id`: 任务 ID\n"
        "- `task_name`: 任务名称\n"
        "\n"
        "示例: `jq -r '.tool_name'` 读取工具名, `jq -r '.args.command'` 读取 Bash 命令参数。\n"
        "\n"
        "## ⚠️ 阻塞机制\n"
        "\n"
        "**PreToolUse** 和 **PermissionRequest** 是阻塞事件:\n"
        "- 命令返回退出码 0 → 放行\n"
        "- 命令返回非零退出码 → 阻止操作,将 stderr/stdout 内容作为拒绝原因返回给 LLM\n"
        "- 命令超时(退出码 124) → 同样阻止\n"
        "\n"
        "**风险**:如果 hook 命令本身有 bug(如语法错误导致总是非零退出),会阻止所有匹配的工具调用,"
        "可能导致死锁。建议先用非阻塞事件测试命令正确性,再应用到 PreToolUse。"
        f"{windows_note}"
    )


@tool
def hook_read(tool_runtime: ToolRuntime = None) -> str:
    """
    读取全部 hooks 配置。Hook 是在特定事件触发时自动执行的 shell 命令。
    输出先项目级后用户级,每个 hook 显示 id、名称、事件、匹配器和命令。
    使用前请先调用 hook_docs 查看完整文档。

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
    使用前请先调用 hook_docs 查看完整文档(事件列表、参数传递、注意事项)。

    Args:
        event: 触发事件,详见 hook_docs。
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
    root = root_dir if scope == Scope.PROJECT else Scope.USER
    new_id = add_hook(
        event=event,
        commands=cmd_list,
        name=name or None,
        matcher=matcher or None,
        root=root,
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
    使用前请先调用 hook_docs 查看完整文档。

    Args:
        id_or_name: hook 的 id(如 "a3f8c2")或 name(如 "block-rm")。

    Returns:
        str: 操作结果消息。
    """
    config = tool_runtime.config
    if not config or not config.current_agent:
        raise ValueError("hook_remove 需要 config 中的 current_agent 来获取 root_dir")
    root_dir = config.root_dir
    removed = remove_hook(id_or_name, root_dir)
    if removed:
        return f"已删除 hook: {id_or_name}"
    return f"{TOOL_ERROR}: 未找到 hook: {id_or_name}"


def get_tools() -> list:
    return [hook_docs, hook_read, hook_add, hook_remove]


def get_all_tools() -> list:
    return get_tools()
