"""内置 Skill: Hook 机制文档

hook_docs 工具迁来的完整文档。静态内容,由 LLM 按需通过 skill_read 加载。
"""

from uniclaw.tools.skill.loader import SkillDef, register_builtin

_HOOKS_SKILL_PROMPT = r"""# Hook 完整文档

Hook 是在特定事件触发时自动执行的 shell 命令,可用于:工具调用前后检查、
阻止危险操作、日志审计、会话生命周期管理等。

通过 `hook_read`、`hook_add`、`hook_remove` 工具管理 hook。

## 配置文件

配置由 `hook_add`/`hook_remove` 管理,分为项目级和用户级,两级同时生效。
请勿直接编辑配置文件,始终通过工具操作。通过 `hook_read` 查看当前配置。

## 作用域

- `project`: 存储在当前项目目录下,通过 `hook_add` 的 `scope='project'` 添加(默认)
- `user`: 存储在用户主目录下,通过 `hook_add` 的 `scope='user'` 添加
两级配置同时加载、同时执行,项目级先执行。

## 可用事件

- `SessionStart`: 会话启动时触发(仅主代理),stdin 特有字段: user_message, depth
- `SessionEnd`: 会话结束时触发(仅主代理),stdin 特有字段: status, depth
- `PreToolUse`: 工具调用前触发(**非零退出码会阻止工具调用**),stdin 特有字段: tool_name, tool_call, args
- `PostToolUse`: 工具调用后触发,stdin 特有字段: tool_name, tool_call, args, result
- `PreAssistant`: 助手回复前触发(流式响应完成后),stdin 特有字段: content, tool_calls, in_tokens, out_tokens, model_name
- `PermissionRequest`: 权限请求时触发(**非零退出码会拒绝权限**),stdin 特有字段: tool_name, tool_call, args, description, explanation
- `PermissionResponse`: 权限响应后触发,stdin 特有字段: tool_name, tool_call, args, permitted, response

其中 `PreToolUse` 和 `PermissionRequest` 是**阻塞事件**:命令返回非零退出码时会阻止操作。
其余事件仅记录,不影响执行流程。

### 各事件 stdin JSON 特有字段

- `SessionStart`: user_message, depth
- `SessionEnd`: status, depth
- `PreToolUse`: tool_name, tool_call, args
- `PostToolUse`: tool_name, tool_call, args, result
- `PreAssistant`: content, tool_calls, in_tokens, out_tokens, model_name
- `PermissionRequest`: tool_name, tool_call, args, description, explanation
- `PermissionResponse`: tool_name, tool_call, args, permitted, response

## Matcher 匹配规则

matcher 用于限定 hook 只对特定工具生效,支持:
- **通配符**: `*`(任意字符)、`?`(单个字符),如 `*Tool*`、`Bash?`
- **多模式**: `|` 或 `,` 分隔,如 `Bash|Read|Write`
- **默认**: 不指定或为 `*` 时匹配所有工具
匹配使用 `fnmatchcase`(区分大小写)。

## 命令执行环境

每条 hook 命令以独立子进程运行:
- **工作目录**: 当前会话的 root_dir
- **超时**: 默认 30 秒,可在 hooks.json 中通过 `timeout` 字段自定义(秒)
- **stdin**: JSON 对象,包含通用字段 + 事件特有字段
- **stdout/stderr**: 正常捕获,失败时写入日志

### 环境变量(所有事件通用)

- `UNICLAW_HOOK_EVENT`: 触发事件名(如 `PreToolUse`)
- `UNICLAW_HOOK_CWD`: 当前工作目录
- `UNICLAW_HOOK_SCOPE`: 作用域(`project` 或 `user`)
- `UNICLAW_HOOK_TOOL`: 工具名(仅工具相关事件,其他事件为空)

注意:包含 KEY/SECRET/TOKEN/PASSWORD/CREDENTIAL 的环境变量会被自动过滤,不会传递给 hook 命令。

### stdin JSON 通用字段

- `event`: 触发事件名
- `cwd`: 当前工作目录
- `session_id`: 会话 ID
- `task_id`: 任务 ID
- `task_name`: 任务名称

示例: `jq -r '.tool_name'` 读取工具名, `jq -r '.args.command'` 读取 Bash 命令参数。

## ⚠️ 阻塞机制

**PreToolUse** 和 **PermissionRequest** 是阻塞事件:
- 命令返回退出码 0 → 放行
- 命令返回非零退出码 → 阻止操作,将 stderr/stdout 内容作为拒绝原因返回给 LLM
- 命令超时(退出码 124) → 同样阻止

**风险**:如果 hook 命令本身有 bug(如语法错误导致总是非零退出),会阻止所有匹配的工具调用,
可能导致死锁。建议先用非阻塞事件测试命令正确性,再应用到 PreToolUse。

## 编码注意

Windows 上 cmd 默认非 UTF-8 编码,读取 stdin JSON 中的中文会乱码。
当运行环境为 Windows 且 hook 命令需要解析中文参数时,在命令前加 `chcp 65001 >nul` 切换代码页,
保存文件时用 UTF-8 无 BOM 编码。
"""


def register():
    """注册 hooks 技能"""
    register_builtin(
        SkillDef(
            name="hooks",
            description="Hook 机制完整文档:事件列表、配置格式、作用域、matcher 规则、"
            "命令执行环境、阻塞机制、编码注意事项。"
            "当用户想了解/使用 Hook(钩子)机制,或准备调用 hook_add/hook_read/hook_remove 时,先加载本文档。",
            triggers=[
                "/hooks",
                "/hook",
                "/hooks-docs",
                "hook",
                "hooks",
                "/hook-docs",
            ],
            tools=[
                "hook_read",
                "hook_add",
                "hook_remove",
            ],
            prompt=_HOOKS_SKILL_PROMPT,
            file_path=__file__,
            source="builtin",
            when_to_use="用户想了解 Hook 机制的原理/用法、查看 hook 文档,或准备添加、读取、删除 hook 时",
            argument_hint="",
        )
    )