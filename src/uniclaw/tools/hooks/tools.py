import sys

from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR

from uniclaw.config import AppConfig
from uniclaw.context import Scope
from uniclaw.tools.hooks.hook_manager import (
    add_hook,
    remove_hook,
    load_all_hooks_configs,
)

# ── 系统提示词(静态常量,最大化 LLM 缓存命中) ──────────────

_HOOKS_SYSTEM_PROMPT = """\
# Hook 机制
Hook 是在特定事件触发时自动执行的 shell 命令,可用于工具调用前后的自动化检查、阻止危险操作、会话生命周期管理等。\
你可以通过 {read}、{add}、{remove} 工具管理 hook。\
可用事件:SessionStart、SessionEnd、PreToolUse(非零退出码阻止调用)、PostToolUse、PreAssistant、PermissionRequest(非零退出码拒绝)、PermissionResponse。

## 参数传递
Hook 命令可通过两种方式获取上下文信息:

**环境变量**(所有事件通用):
- `UNICLAW_HOOK_EVENT`: 触发事件名(如 PreToolUse)
- `UNICLAW_HOOK_CWD`: 当前工作目录
- `UNICLAW_HOOK_SCOPE`: 作用域(project 或 user)
- `UNICLAW_HOOK_TOOL`: 工具名(工具相关事件,其他事件为空)

**stdin JSON**(命令通过标准输入读取 JSON 对象):
通用字段:event、cwd、session_id、task_id、task_name。
各事件特有字段:
- SessionStart: user_message, depth
- SessionEnd: status, depth
- PreAssistant: content, tool_calls, in_tokens, out_tokens, model_name
- PreToolUse / PostToolUse: tool_name, tool_call, args; PostToolUse 额外含 result
- PermissionRequest: tool_name, tool_call, args, description, explanation
- PermissionResponse: tool_name, tool_call, args, permitted, response

示例: `jq -r '.tool_name'` 读取工具名, `jq -r '.args.command'` 读取 Bash 命令参数。

## ⚠️ PreToolUse 阻塞风险
PreToolUse hook 命令失败(非零退出码)会阻止对应工具调用。如果 hook 命令本身有问题,会导致所有工具都无法使用(死锁)。{windows_note}"""

_WINDOWS_ENCODING_NOTE = """

**Windows 编码注意**: Windows 上 cmd 默认非 UTF-8 编码,读取 stdin JSON 中的中文会乱码。需在命令前加 `chcp 65001 >nul` 切换代码页,保存文件时用 UTF-8 无 BOM 编码。
"""


def get_hooks_system_prompt() -> str:
    """返回 Hook 机制的系统提示词(静态内容,适合放在缓存前缀区域)。"""
    windows_note = _WINDOWS_ENCODING_NOTE if sys.platform == "win32" else ""
    return _HOOKS_SYSTEM_PROMPT.format(
        read=hook_read.name,
        add=hook_add.name,
        remove=hook_remove.name,
        windows_note=windows_note,
    )


@tool
def hook_read(config: AppConfig = None) -> str:
    """
    读取全部 hooks 配置。Hook 是在特定事件触发时自动执行的 shell 命令。
    输出先项目级后用户级,每个 hook 显示 id、名称、事件、匹配器和命令。

    注意:config 参数由系统框架自动注入,请勿手动传入。
    """
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
    scope: str = "project",
    config: AppConfig = None,
) -> str:
    """
    添加单条 hook。Hook 是在特定事件触发时自动执行的 shell 命令,可用于:
    - 工具调用前/后的自动化检查或日志记录
    - 阻止危险操作(PreToolUse 非零退出码会阻止工具执行)
    - 会话生命周期的初始化或清理
    支持多个命令按顺序执行。

    event: 触发事件,可选值:
        - SessionStart: 会话启动时触发
        - SessionEnd: 会话结束时触发
        - PreToolUse: 工具调用前触发(可阻止调用,非零退出码会阻止)
        - PostToolUse: 工具调用后触发
        - PreAssistant: 助手回复前触发
        - PermissionRequest: 权限请求时触发(可阻止,非零退出码拒绝权限)
        - PermissionResponse: 权限响应后触发
    commands: 要执行的 shell 命令,多个命令用换行分隔(如 "echo step1\\necho step2")。
    name: 可选的人类可读名称(如 "block-rm"),便于后续按名删除。
    matcher: 可选的工具名匹配器,支持通配符(*,?)和多模式(|或,分隔)。如 "Bash","Read|Write","*Tool*"。不指定则匹配所有。
    scope: 'project'(项目级)或 'user'(用户级)。

    注意:config 参数由系统框架自动注入,请勿手动传入。
    """
    if not config or not config.current_agent:
        raise ValueError("hook_add 需要 config 中的 current_agent 来获取 root_dir")
    root_dir = config.root_dir
    cmd_list = [c.strip() for c in commands.strip().split("\n") if c.strip()]
    if scope == "project" and root_dir is None:
        return (
            f"{TOOL_ERROR}: 当前会话无工作目录,无法添加项目级 hook,请使用 scope='user'"
        )
    root = root_dir if scope == "project" else Scope.USER
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
def hook_remove(id_or_name: str, config: AppConfig = None) -> str:
    """
    删除单条 hook。Hook 是在特定事件触发时自动执行的 shell 命令。
    根据 id 或 name 删除,自动搜索项目级和用户级配置。

    id_or_name: hook 的 id(如 "a3f8c2")或 name(如 "block-rm")。

    注意:config 参数由系统框架自动注入,请勿手动传入。
    """
    if not config or not config.current_agent:
        raise ValueError("hook_remove 需要 config 中的 current_agent 来获取 root_dir")
    root_dir = config.root_dir
    removed = remove_hook(id_or_name, root_dir)
    if removed:
        return f"已删除 hook: {id_or_name}"
    return f"{TOOL_ERROR}: 未找到 hook: {id_or_name}"


def get_tools() -> list:
    return [hook_read, hook_add, hook_remove]


def get_all_tools() -> list:
    return [hook_read, hook_add, hook_remove]
