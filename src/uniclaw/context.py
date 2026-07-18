from pathlib import Path
import os
import platform
import sys
from datetime import datetime
from enum import StrEnum

from uniclaw.config import AppConfig
from uniclaw.utils.constants import SYSTEM_PREFIX

APP_NAME = "UniClaw"


def get_base_system_prompt(config: AppConfig) -> str:
    from uniclaw.tools.fs import Write
    from uniclaw.tools.monitor.tools import monitor_start
    from uniclaw.tools.registry import search_tools
    from uniclaw.tools.shell import Bash
    from uniclaw.tools.web import webSearch

    task = config.current_agent
    session = task.session
    root_dir = session.root_dir
    # 额外工作空间目录
    extra = list(config.workspace)
    extra_text = ""
    if extra and root_dir:
        extra.append(root_dir)  # 确保当前目录在工作空间中
        extra_lines = "\n".join(f"  - {d}" for d in extra)
        extra_text = f"\n\n# 额外工作空间目录\n用户已授权你访问以下额外目录(均可读写):\n{extra_lines}\n"

    # 环境信息行
    env_lines = [
        f"- 当前日期:{datetime.now().strftime('%Y-%m-%d %A')}",
    ]
    if root_dir:
        env_lines.append(f"- 当前目录:{root_dir}")
    env_lines += [
        f"- 平台:{platform.system()}",
        f"- 进程:{sys.argv[0]} (PID:{os.getpid()})",
    ]
    env_text = "\n".join(env_lines)

    system_prompt = f"""你是 {APP_NAME},一个运行在终端中的 AI 编程和办公助手,帮助用户完成编写代码、调试、重构、解释等软件工程任务。

## 行为准则
- **独立思考**:主动提出更优方案,而非盲目执行指令;不因"只是AI"等理由自我设限。
- **积极主动**:深入理解用户意图,主动提供最佳方案。需求不明确时,询问澄清或提供 2-5 个可行方案供选择。
- **追求最优解**:以解决根本问题为目标,优先选择健壮、可维护的方案,充分考虑边界情况和潜在风险,拒绝临时方案。
- **自主执行**:充分利用已有资源完成复杂任务。对于监控进程、后台循环、长时间任务等需求,主动使用 {Write.name} 编写脚本并通过 {Bash.name} 或 {monitor_start.name} 执行,不要以"只是聊天界面"为由拒绝。
- **勇于探索**:遇到不熟悉的任务时,不要轻易说"做不到"。先充分利用已有资源(工具、skill、记忆、项目文档等)探索解决方案;如果不确定是否有合适的工具或skill,使用 {search_tools.name} 搜索;如果本地仍无合适方案,使用 {webSearch.name} 搜索互联网上可用的 skill 或解决方案,并将搜索结果呈现给用户、询问是否需要安装或采用。搜索无结果时,不要直接告知用户"没找到",应主动更换不同关键词、同义词或更宽泛/更具体的表述重新搜索,多次尝试后再总结。
- **系统通知**:收到以 {SYSTEM_PREFIX} 开头的消息时,视为系统通知而非用户请求,根据内容调整行为但不直接回复。

## 工作规范
- 简洁直接,先给出答案
- 优先编辑现有文件而不是创建新文件
- 不添加不必要的注释、文档字符串或错误处理
- 编辑前读取文件,使用行号保持精确
- 文件操作始终使用绝对路径
- 多步骤任务系统地逐步完成
- 任务不清楚时,在继续之前请求澄清
- 临时文件使用完毕后及时清理
- **长任务进展汇报**:执行多步骤任务时,每完成约20次工具调用后,主动向用户简要汇报当前进展(已完成什么、正在做什么、下一步计划),保持用户对任务状态的感知。

## 环境
{env_text}
{extra_text}{get_platform_hints()}
"""
    return system_prompt


def get_claude_md(session) -> str:
    """加载 CLAUDE.md 项目指令,防止提示词注入"""
    root_dir = session.root_dir
    if not root_dir:
        return ""
    claude_md_path = root_dir / "CLAUDE.md"
    if not claude_md_path.exists():
        return ""

    try:
        content = claude_md_path.read_text(encoding="utf-8").strip()
        if not content:
            return ""

        # 限制文件大小(最大 10KB)
        max_size = 10 * 1024
        if len(content.encode("utf-8")) > max_size:
            content = content[:max_size] + "\n... (文件过大,已截断)"

        # 防止提示词注入:移除可能的系统指令伪装
        # 过滤掉试图模拟系统消息的行
        lines = content.split("\n")
        safe_lines = []
        for line in lines:
            # 跳过试图伪装成系统指令的行
            stripped = line.strip().lower()
            if stripped.startswith("ignore") and (
                "previous" in stripped or "above" in stripped
            ):
                continue
            if stripped.startswith("system:") or stripped.startswith("assistant:"):
                continue
            if "you are now" in stripped and (
                "act as" in stripped or "pretend" in stripped
            ):
                continue
            safe_lines.append(line)

        safe_content = "\n".join(safe_lines)
        if not safe_content:
            return ""

        # CLAUDE.md 说明
        description = (
            f"## CLAUDE.md\n"
            f"项目根目录的指令文件(路径:{claude_md_path}),"
            f"定义项目特定的规范和约束,每次对话自动加载。\n"
            f'用户要求"记住项目规范"、"添加项目指令"时,写入此文件。\n'
            f"建议内容:代码风格、架构规范、工作流程、技术栈、禁止事项。"
        )
        return f"{description}\n\n{safe_content}"
    except Exception:
        return ""


def get_platform_hints() -> str:
    """返回针对当前操作系统的 shell 提示信息。"""
    import platform as _plat

    if _plat.system() == "Windows":
        from uniclaw.tools.shell import _GIT_BASH_PATH

        if _GIT_BASH_PATH:
            from uniclaw.tools.shell import Bash

            return (
                "\n## Windows Shell 提示\n"
                "你在 Windows 上,可以直接使用 bash 命令(ls、cat、grep、find、管道等)。\n"
                "注意:bash 环境中的路径分隔符为 `/`,Windows 路径如 `C:\\Users` 在 bash 中写作 `/c/Users`。\n"
                "也可以混用 Windows 命令(如 `where`、`dir`),bash 环境下两者皆可执行。\n"
                f"对于非 {Bash.name} 工具(如 monitor_start、文件操作工具等),必须使用正常 Windows 路径格式(如 `C:\\Users\\name`)。\n"
            )
        return (
            "\n## Windows Shell 提示\n"
            "你在 Windows 上,请使用 Windows 命令:\n"
            "- 使用 `type file.txt` 而不是 `cat file.txt`\n"
            '- 使用 `type file.txt | findstr /n /i "pattern"` 而不是 `grep`\n'
            '- 使用 `powershell -Command "Get-Content file.txt -Tail 20"` 而不是 `tail -n 20`\n'
            '- 使用 `powershell -Command "Get-Content file.txt -Head 20"` 而不是 `head -n 20`\n'
            "- 使用 `dir /s /b *.py` 或 `powershell -Command \"Get-ChildItem -Recurse -Filter *.py\"` 而不是 `find . -name '*.py'`\n"
            "- 使用 `del file.txt` 而不是 `rm file.txt`\n"
            "- `mkdir folder` 在两者上都可用(不需要 -p)\n"
            "- 使用 `copy` / `move` 而不是 `cp` / `mv`\n"
            "- 使用 `&&` 链接命令,而不是 `;`\n"
            "- 路径使用反斜杠 `\\`,但正斜杠 `/` 在大多数情况下也适用\n"
            '- Python 可用:`python -c "..."` 可用于复杂的文本处理\n'
        )
    return ""


def _build_free_chat_prompt(config: AppConfig) -> str:
    """自由聊天模式的精简提示词。"""
    from datetime import datetime
    from uniclaw.tools.memory.memory import Memory
    from uniclaw.tools.web import webSearch

    task = config.current_agent
    if task and task.session.system_prompt:
        lines = [task.session.system_prompt]
    else:
        lines = [
            f"你是 {APP_NAME},一个简洁友好的 AI 助手。",
            "",
            "## 规则",
            "- 简洁直接,先给出答案",
            f"- 涉及实时信息、事实核查、不确定的内容时,主动使用 {webSearch.name} 搜索",
            "- 搜索无结果时,更换关键词、同义词或更宽泛/具体的表述多次尝试",
            "- 不确定时坦诚说明,不要编造",
            f"- 当前日期:{datetime.now().strftime('%Y-%m-%d %A')}",
        ]
    # scope 限制:无论是否有自定义提示词,始终生效
    lines.append("- 有 scope 的工具写入只允许修改项目级,不允许修改用户级")
    # 仅注入记忆索引(不含操作说明)
    index = Memory.get_memory_index_preview(task.session.root_dir)
    if index:
        lines += ["", "# 记忆", index]
    return "\n".join(lines)


async def build_system_prompt(config: AppConfig):
    # 自由聊天模式:精简提示词,节省 token
    if config.is_free_chat:
        return _build_free_chat_prompt(config)

    system_prompt = get_base_system_prompt(config)

    # === 稳定内容(低频变化,最大化缓存前缀命中) ===

    # Security — 完全静态内容(放在最前面,最大化缓存命中)
    from uniclaw.tools.security.tools import get_security_system_prompt

    security_ctx = get_security_system_prompt()
    if security_ctx:
        system_prompt += f"\n\n{security_ctx}"

    # Hooks — 完全静态内容
    from uniclaw.tools.hooks.tools import get_hooks_system_prompt

    hooks_ctx = get_hooks_system_prompt()
    if hooks_ctx:
        system_prompt += f"\n\n{hooks_ctx}"

    # 知识图谱 — 完全静态内容(图谱非空时才注入)
    from uniclaw.tools.knowledge.context import get_knowledge_system_prompt

    kg_ctx = get_knowledge_system_prompt(config.root_dir)
    if kg_ctx:
        system_prompt += f"\n\n{kg_ctx}\n"

    # 子代理提示 — 完全静态内容
    from uniclaw.tools.multi_agent.tools import get_sub_agent_system_prompt

    sub_agent_ctx = get_sub_agent_system_prompt()
    if sub_agent_ctx:
        system_prompt += f"\n\n{sub_agent_ctx}"

    # 扩展工具提示
    from uniclaw.tools.registry import get_registry_system_prompt

    registry_ctx = await get_registry_system_prompt(config)
    if registry_ctx:
        system_prompt += f"\n\n{registry_ctx}"

    # CLAUDE.md 项目指令 — 项目级稳定
    task = config.current_agent
    claude_md = get_claude_md(task.session)
    if claude_md:
        system_prompt += f"\n\n# CLAUDE.md 项目指令:\n\n{claude_md}\n"

    # Skill — 低频变化
    from uniclaw.tools.skill.tools import get_skill_system_prompt

    skill_ctx = get_skill_system_prompt(task.session.root_dir)
    if skill_ctx:
        system_prompt += f"\n\n# skill:\n{skill_ctx}\n"

    # === 中频变化内容 ===

    # 历史消息检索提示 — 仅当有被压缩的历史消息时注入
    from uniclaw.tools.session.recall import get_recall_system_prompt

    recall_ctx = get_recall_system_prompt(task.session)
    if recall_ctx:
        system_prompt += f"\n\n{recall_ctx}"

    # 记忆 — 中频变化(保存/删除时变化)
    from uniclaw.tools.memory.context import get_memory_system_prompt

    memory_ctx = get_memory_system_prompt(task.session.root_dir)
    if memory_ctx:
        system_prompt += f"\n\n# 记忆\n你的持久化记忆:\n{memory_ctx}\n"

    # Plan mode — 仅在计划模式下启用
    from uniclaw.tools.plan import get_plan_system_prompt

    plan_prompt = get_plan_system_prompt(config)
    if plan_prompt:
        system_prompt += plan_prompt

    # Computer Use — 中频变化(启用/禁用时变化)
    from uniclaw.tools.computer_use import get_cu_system_prompt

    cu_prompt = get_cu_system_prompt(config)
    if cu_prompt:
        system_prompt += cu_prompt

    # === 高频变化内容(放在最后,减少对缓存前缀的影响) ===

    # TodoList — 每次任务状态更新都变化(放在最后,减少对缓存前缀的影响)
    from uniclaw.tools.todolist import get_list_system_prompt

    todolist_ctx = get_list_system_prompt(config.current_agent.todolist)
    if todolist_ctx:
        system_prompt += f"\n\n{todolist_ctx}\n"

    # Goal — 目标停止条件
    goal_mgr = config.current_agent.goal_manager if config.current_agent else None
    if goal_mgr and goal_mgr.active:
        system_prompt += f"\n\n# 当前目标\n目标: {goal_mgr.goal}\n请确保你的工作朝着这个目标推进,并在完成后明确说明目标已达成。\n"

    return system_prompt


class Scope(StrEnum):
    USER = "user"
    ALL = "all"


def get_app_dir(root_dir: Scope | Path | None = Scope.USER):
    if isinstance(root_dir, Path):
        base = root_dir.resolve()
    elif root_dir == Scope.USER or root_dir is None:
        base = Path.home()
    elif root_dir == Scope.ALL:
        raise ValueError(
            f"Scope.ALL 不能直接传入 get_app_dir,请分别传入 Scope.USER 和 root_dir"
        )
    else:
        raise ValueError(f"无效的root_dir: {root_dir}")
    app_dir = base / f".{APP_NAME}"
    return app_dir
