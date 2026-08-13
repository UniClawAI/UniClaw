import json
import platform
import threading
from datetime import datetime
from pathlib import Path

from uniclaw.config import AppConfig
from uniclaw.console.ui import warn
from uniclaw.tools.mcp.tools import mcp_list_servers
from uniclaw.tools.shell import Bash
from uniclaw.utils.message import MessageRole

# 无需权限提示即可安全运行的前缀
_SAFE_PREFIXES = (
    # 文件查看类
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "less",
    "more",
    # 路径与目录
    "pwd",
    "cd ",
    "dir ",
    # 输出与信息显示
    "echo",
    "printf",
    # 时间与日期
    "date",
    "time",
    # 命令查找与类型
    "which",
    "type",
    "where ",
    "command -v",
    # 环境变量
    "env",
    "printenv",
    "set",
    # 系统信息
    "uname",
    "hostname",
    "whoami",
    "id",
    "uptime",
    "w",
    # Git 只读操作
    "git log",
    "git status",
    "git diff",
    "git show",
    "git branch",
    "git remote",
    "git stash list",
    "git tag",
    "git reflog",
    "git blame",
    "git shortlog",
    "git describe",
    "git rev-parse",
    "git ls-files",
    "git ls-tree",
    # 文件搜索
    "find ",
    "grep ",
    "rg ",
    "ag ",
    "fd ",
    "locate ",
    # 编程语言解释器(仅限安全子命令)
    "python -m pytest",
    "python -m pip",
    "python -m uv",
    "python -m black",
    "python -m mypy",
    "python -m ruff",
    "python -m py_compile",
    "python3 -m pytest",
    "python3 -m pip",
    "python3 -m uv",
    "python3 -m black",
    "python3 -m mypy",
    "python3 -m ruff",
    "python3 -m py_compile",
    # Python 包管理(只读)
    "pip show",
    "pip list",
    "pip freeze",
    "pip check",
    "pip index versions",
    "uv pip list",
    "uv pip show",
    # Node.js 包管理(只读)
    "npm list",
    "npm view",
    "npm info",
    "yarn list",
    "yarn info",
    # Rust 包管理(只读)
    "cargo metadata",
    "cargo tree",
    "cargo search",
    "cargo doc --no-deps",
    # 磁盘与文件系统
    "df ",
    "du ",
    "mount",
    "lsblk",
    # 内存与进程
    "free ",
    "top -bn",
    "ps ",
    "htop",
    # 网络诊断(只读)
    "ping -c",
    "ping -n",
    "curl -I",
    "curl --head",
    "curl -s",
    "wget -S",
    "wget --spider",
    "nslookup",
    "dig",
    "host",
    "traceroute",
    "tracert",
    "netstat -",
    "ss -",
    "ip addr",
    "ip route",
    "ifconfig",
    # 端口检查
    "lsof -i",
    "netstat -tlnp",
    # Docker 只读操作
    "docker ps",
    "docker images",
    "docker version",
    "docker info",
    "docker inspect",
    "docker logs",
    "docker stats --no-stream",
    # 服务状态
    "systemctl status",
    "systemctl list-units",
    "service --status-all",
    # 硬件信息
    "lscpu",
    "lsmem",
    "lsusb",
    "lspci",
    # 日志查看
    "journalctl --no-pager",
    "dmesg",
    # Windows 特定命令
    "tasklist",
    "wmic ",
    "systeminfo",
    "driverquery",
)


_CHAIN_OPERATORS = (";", "&&", "||", "|", "`", "$(", "\n")


def is_safe_tool(name: str) -> bool:
    """判断是否为安全工具(自动批准,无需用户确认)

    包括只读类工具和记忆/技能管理等安全的管理工具。
    通过导入工具函数并使用 .name 属性获取名称,避免硬编码字符串导致的大小写错误。

    Args:
        name: 工具名称

    Returns:
        bool: 如果是安全工具返回True,否则返回False
    """
    # 从各个模块导入安全工具函数
    from uniclaw.tools.fs import Read, Glob, ConvertToMarkdown
    from uniclaw.tools.shell import Grep, search_files_with_everything
    from uniclaw.tools.media import ReadMedia
    from uniclaw.tools.sandbox.tools import (
        DockerCreate,
        DockerExec,
        DockerStart,
        DockerStop,
        DockerRemove,
        DockerList,
        DockerPull,
        DockerSearch,
        DockerImages,
        DockerRemoveImage,
        DockerBuild,
    )
    from uniclaw.tools.web import webFetch, webSearch
    from uniclaw.tools.search import platform_search
    from uniclaw.tools.memory.tools import (
        memory_save,
        memory_delete,
        memory_list,
        memory_search,
    )
    from uniclaw.tools.scheduler.tools import (
        schedule_create,
        schedule_list,
        schedule_remove,
        schedule_toggle,
    )
    from uniclaw.tools.skill.tools import skill_suggest, skill_read
    from uniclaw.tools.sleep import sleep_timer
    from uniclaw.tools.plan import enter_plan_mode, exit_plan_mode
    from uniclaw.tools.monitor.tools import (
        monitor_list,
        monitor_output,
        monitor_get_matched,
        monitor_update_pattern,
    )
    from uniclaw.tools.todolist import (
        todolist_create,
        todolist_update,
        todolist_clear,
        todolist_list,
        todolist_cancel,
    )
    from uniclaw.tools.ask import AskUserQuestion
    from uniclaw.tools.session.tools import session_list, session_detail
    from uniclaw.tools.session.recall import recall_history, get_history_range
    from uniclaw.tools.hooks.tools import hook_docs, hook_read
    from uniclaw.tools.multi_agent.tools import (
        subagent_list_tasks,
        subagent_check_result,
        subagent_list_definitions,
        subagent_close,
        subagent_get_definition,
        subagent_discuss,
        subagent_send_message,
    )
    from uniclaw.tools.notify import push_notification
    from uniclaw.tools.registry import search_tools
    from uniclaw.tools.knowledge.tools import (
        kg_add_entity,
        kg_add_relation,
        kg_add_alias,
        kg_update_entity,
        kg_delete_entity,
        kg_delete_relation,
        kg_get_entity,
        kg_search,
        kg_neighbors,
        kg_path,
        kg_stats,
        kg_export,
        kg_list,
        kg_extract,
        kg_clear,
        kg_merge_entities,
    )
    from uniclaw.tools.send_file import send_file
    from uniclaw.tools.wechat import (
        wechat_list_contacts,
        wechat_send_text,
        wechat_send_image,
        wechat_send_file,
    )
    from uniclaw.tools.computer_use import (
        cu_screenshot,
        cu_locate_on_screen,
        cu_get_elements,
        cu_find_element,
    )
    from uniclaw.tools.advisor import advisor_list, ask_advisor
    from uniclaw.tools.security.tools import read_llm_safe_prompt
    from uniclaw.tools.rag.tools import rag_search, rag_list_collections, rag_set_desc
    from uniclaw.tools.download.tools import (
        http_download,
        http_download_status,
        http_download_remove,
    )
    from uniclaw.tools.help import list_slash_commands, get_command_help
    from uniclaw.tools.tts.tools import text_to_speech
    from uniclaw.tools.monitor.tools import monitor_stop
    from uniclaw.tools.session.tools import session_update_title
    from uniclaw.tools.hooks.tools import hook_remove
    from uniclaw.tools.web_browse.tools import (
        browser_screenshot,
        browser_get_text,
        browser_get_html,
        browser_get_attribute,
        browser_get_elements,
        browser_get_url,
        browser_get_title,
        browser_toggle_mode,
        browser_get_value,
        browser_get_count,
        browser_get_box,
        browser_get_styles,
        browser_scroll_into_view,
        browser_focus,
        browser_wait,
        browser_scroll,
        browser_list_pages,
    )
    from uniclaw.tools.ipython.tools import (
        ipython_inspect,
        ipython_vars,
        ipython_history,
        ipython_list_kernels,
    )

    # 使用 .name 属性获取工具的实际名称,构建安全工具集合
    safe_tools = [
        # ── 文件系统 / Shell ──
        Read.name,
        ConvertToMarkdown.name,
        ReadMedia.name,
        Glob.name,
        Grep.name,
        DockerCreate.name,
        DockerExec.name,
        DockerStart.name,
        DockerStop.name,
        DockerRemove.name,
        DockerList.name,
        DockerPull.name,
        DockerSearch.name,
        DockerImages.name,
        DockerRemoveImage.name,
        DockerBuild.name,
        search_files_with_everything.name,
        # ── Web / 搜索 ──
        webFetch.name,
        webSearch.name,
        platform_search.name,
        search_tools.name,
        # ── 记忆 ──
        memory_save.name,
        memory_delete.name,
        memory_list.name,
        memory_search.name,
        # ── 定时任务 ──
        schedule_create.name,
        schedule_list.name,
        schedule_remove.name,
        schedule_toggle.name,
        # ── 技能 ──
        skill_suggest.name,
        skill_read.name,
        # ── Plan ──
        enter_plan_mode.name,
        exit_plan_mode.name,
        # ── 进程监控 ──
        monitor_list.name,
        monitor_stop.name,
        monitor_output.name,
        monitor_get_matched.name,
        monitor_update_pattern.name,
        # ── 任务清单 ──
        todolist_create.name,
        todolist_update.name,
        todolist_clear.name,
        todolist_list.name,
        todolist_cancel.name,
        # ── 多智能体 ──
        subagent_send_message.name,
        subagent_close.name,
        subagent_check_result.name,
        subagent_list_tasks.name,
        subagent_discuss.name,
        subagent_list_definitions.name,
        subagent_get_definition.name,
        # ── 会话管理 ──
        session_list.name,
        session_detail.name,
        session_update_title.name,
        recall_history.name,
        get_history_range.name,
        # ── Hooks ──
        hook_docs.name,
        hook_read.name,
        hook_remove.name,
        # ── MCP ──
        mcp_list_servers.name,
        # ── 安全 ──
        read_llm_safe_prompt.name,
        # ── Ask / 通知 ──
        AskUserQuestion.name,
        push_notification.name,
        send_file.name,
        sleep_timer.name,
        # ── 帮助 ──
        list_slash_commands.name,
        get_command_help.name,
        # ── TTS ──
        text_to_speech.name,
        # ── 微信(全部安全) ──
        wechat_list_contacts.name,
        wechat_send_text.name,
        wechat_send_image.name,
        wechat_send_file.name,
        # ── 知识图谱(全部安全) ──
        kg_add_entity.name,
        kg_add_relation.name,
        kg_add_alias.name,
        kg_update_entity.name,
        kg_delete_entity.name,
        kg_delete_relation.name,
        kg_get_entity.name,
        kg_search.name,
        kg_neighbors.name,
        kg_path.name,
        kg_stats.name,
        kg_export.name,
        kg_list.name,
        kg_extract.name,
        kg_clear.name,
        kg_merge_entities.name,
        # ── RAG ──
        rag_search.name,
        rag_list_collections.name,
        rag_set_desc.name,
        # ── HTTP 下载 ──
        http_download.name,
        http_download_status.name,
        http_download_remove.name,
        # ── Web Browse 只读 ──
        browser_screenshot.name,
        browser_get_text.name,
        browser_get_html.name,
        browser_get_attribute.name,
        browser_get_url.name,
        browser_get_title.name,
        browser_get_elements.name,
        browser_toggle_mode.name,
        browser_get_value.name,
        browser_get_count.name,
        browser_get_box.name,
        browser_get_styles.name,
        browser_scroll_into_view.name,
        browser_focus.name,
        browser_wait.name,
        browser_scroll.name,
        browser_list_pages.name,
        # ── Computer Use 只读 ──
        cu_screenshot.name,
        cu_locate_on_screen.name,
        cu_get_elements.name,
        cu_find_element.name,
        # ── Advisor 只读 ──
        advisor_list.name,
        ask_advisor.name,
        # ── IPython 只读 ──
        ipython_inspect.name,
        ipython_vars.name,
        ipython_history.name,
        ipython_list_kernels.name,
    ]

    return name in safe_tools


def is_safe_bash(cmd: str, root_dir: Path | None) -> bool:
    """如果命令是只读的且从不需要权限提示,则返回 True。

    拒绝包含 shell 链式操作符(;、&&、||、|、反引号、$(…))的命令
    — 这些可能在安全前缀后执行任意代码。
    """
    c = cmd.strip()

    # 先拒绝任何链接多个命令的危险操作符(最高优先级,不可被用户规则覆盖)
    if any(op in c for op in _CHAIN_OPERATORS):
        return False

    # 再检查用户自定义的持久化规则
    if check_saved_bash_rule(cmd, root_dir):
        return True

    # 最后检查系统内置的安全前缀白名单
    return any(c.startswith(p) for p in _SAFE_PREFIXES)


def bash_desc(cmd: str, config: AppConfig) -> str:
    """
    获取命令的描述

    使用 AI 分析命令行参数的功能和潜在安全风险。

    Args:
        cmd: 要分析的命令行字符串
        config: 配置对象,包含模型参数等信息

    Returns:
        AI 生成的命令描述和安全风险评估文本
    """
    from uniclaw.provider.fallback import chat
    from uniclaw.tools.session.session import Session

    # 构建提示词
    system_prompt = """你是一个命令行安全分析专家。请分析用户提供的 shell 命令,并返回以下信息:

1. **命令功能**:简要说明这个命令的作用和预期效果
2. **安全风险评估**:评估执行此命令可能带来的安全风险(如文件修改、系统配置更改、数据泄露等)
3. **风险等级**:请给出一个 0 到 100 的整数评分,0 表示非常安全,100 表示非常危险

请以简洁清晰的中文回答,控制在 200 字以内。

# 环境
- 平台:{platform}
""".format(
        platform=platform.system()
    )
    user_prompt = f"请分析以下命令:\n``bash\n{cmd}\n```"

    session = Session()
    session.add_user_message(content=user_prompt)

    wait_id = config.spinner.start("分析命令...")
    try:
        # 调用 LLM 进行分析
        response = chat(
            system_prompt,
            session,
            model_name=config.mini_model_name,
            enable_thinking=False,
            thinking=False,
            config=config,
        )
        return response.content
    except Exception as e:
        # 如果 AI 调用失败,返回错误信息
        return f"⚠️ 无法获取命令分析:{str(e)}"
    finally:
        config.spinner.stop(wait_id=wait_id)


# ── LLM 安全检测 ────────────────────────────────────────────

_tool_desc_map: dict[str, str] | None = None


async def _get_tool_desc(name) -> str | None:
    """构建工具名 -> 工具描述的映射,用于 LLM 安全检测。"""
    global _tool_desc_map
    if _tool_desc_map is not None:
        desc = _tool_desc_map.get(name, None)
        if desc:
            return desc
        _tool_desc_map = None
    from uniclaw.tools import get_all_tools

    _tool_desc_map = {}
    for tool in await get_all_tools():
        _tool_desc_map[tool.name] = tool.description or ""
    return _tool_desc_map.get(name, None)


async def llm_safe_check(tc: dict, config: AppConfig) -> tuple[bool, str]:
    """使用 LLM 进行工具调用安全审核(权限检查机制)。

    这是 UniClaw 的核心安全机制。当 AI 尝试执行一个不在白名单中的工具时,
    本函数会调用llm对该工具调用进行智能分析,判断是否安全。

    工作流程:
    1. 检查工具是否在 is_safe_tool() 白名单中
    2. 如果在白名单中:直接放行,无需检查
    3. 如果不在白名单中:
       a. 构建安全分析提示词(包含内置规则 + 用户自定义的注入策略)
       b. 调用llm分析该工具调用的功能和安全风险
       c. llm返回 {"is_safe": true/false, "explanation": "风险评估"}
       d. 如果 is_safe=true,自动执行:否则需要用户确认

    安全策略注入机制:
    - 注入提示词来自 read_llm_safe_prompt(),可通过 write/edit/clear 工具动态调整
    - 例如:管理员可以通过 edit_llm_safe_prompt 告诉 AI "允许所有 git 命令" 或 "禁止删除文件"
    - 这样 AI 在后续的安全审核中会遵循这些策略

    Args:
        tc (dict): 工具调用对象
            - name: 工具名称(如 "Edit", "Bash" 等)
            - args: 工具参数字典(如 {"command": "ls -la"})
        config (AppConfig): 应用配置对象
            - mini_model_name: 用于安全分析的小模型名称(如 gpt-4-mini)

    Returns:
        tuple[bool, str]: (是否安全, 风险说明)
            - (True, explanation): 安全的工具调用,可自动执行
            - (False, explanation): 有安全风险,需要用户确认
            - (False, ""): LLM 调用失败,降级到需要用户确认
    """
    from uniclaw.provider.fallback import achat
    from uniclaw.tools.security.tools import _load_llm_safe_prompt
    from uniclaw.tools.base import tc_name, tc_args
    from uniclaw.tools.session.session import Session

    name = tc_name(tc)
    args = tc_args(tc)
    tool_desc = await _get_tool_desc(name)

    # 获取当前工作空间
    root_dir = config.root_dir
    extra = list(config.workspace)
    extra_text = ""
    if extra:
        if root_dir:
            extra.append(root_dir)
        extra_lines = "\n".join(f"  - {d}" for d in extra)
        extra_text = f"- 当前空间目录:\n{extra_lines}\n"

    system_prompt = f"""你是一个工具调用安全分析专家。分析以下工具调用是否可以安全地自动执行(无需用户确认)。

# 分析方法
- **必须结合工具的函数描述和具体参数值进行综合判断**,不能仅凭工具名称或类型下结论
- 同一工具在不同参数下安全级别可能截然不同
- 对于 {Bash.name} 命令,必须逐个拆解命令、参数、管道和重定向,分析其完整语义
- 对于其他工具,必须检查参数值是否涉及敏感路径、敏感数据或高风险操作

# 当前环境
- 平台:{platform.system()}
- 当前目录:{root_dir or '未设置'}
{extra_text}

安全的调用(is_safe=true):
- 只读操作(读取文件、搜索、列出内容)
- 无害操作(获取公开信息、搜索、时区查询)
- 不会修改系统状态或文件
- 不会泄露敏感数据
- 常规的功能性操作,不涉及安全风险
- 安装或启动软件,只要安装的内容和启动的程序本身是安全的(如通过 npm/pip/brew/apt/cargo 等包管理器安装主流软件包,或启动常见开发工具和服务)

不安全的调用(is_safe=false):
- 修改、删除、覆盖文件
- 执行危险的 shell 命令
- 访问凭据或密钥
- 向非公开端点发送请求
- 修改系统配置
- 可能造成不可逆变更

explanation 要求:
- 如果是 Bash/Shell 命令:拆解命令各部分,解释每个参数和管道的作用,说明整体功能和潜在风险
- 如果是其他工具:说明工具的功能和具体操作内容
- 判定为不安全时,必须清楚说明有哪些危害
- 风险评分:请给出一个 0 到 100 的整数评分,0 表示非常安全,100 表示非常危险

只返回 JSON,不要用 markdown 包裹:
{{"is_safe": true/false,  "explanation": "简要中文解释"}}"""
    injected_prompt = _load_llm_safe_prompt(root_dir)
    if injected_prompt:
        system_prompt += f"\n\n# ⚠️ 用户自定义安全策略(最高优先级)\n以下是由用户主动配置的安全审核规则,必须严格遵守。当用户策略与默认规则冲突时,以用户策略为准:\n{injected_prompt}"

    if name == Bash.name:
        command = args.get("command", "")
        user_prompt = f"工具: {Bash.name} (Shell 命令)\n命令: {command}"
    else:
        user_prompt = f"工具: {name}\n工具描述: {tool_desc}\n参数:\n{json.dumps(args, indent=2, ensure_ascii=False)}"

    session = Session()
    session.add_user_message(content=user_prompt)
    wait_id = config.spinner.start(f"Checking {name} safety...")
    try:
        from uniclaw.utils.format import parse_json_from_llm

        response = await achat(
            system_prompt,
            session,
            model_name=config.mini_model_name,
            temperature=0,
            max_tokens=5000,
            enable_thinking=False,
            thinking=False,
            response_format={"type": "json_object"},
            config=config,
        )
        result = parse_json_from_llm(response.content)
        # 验证返回的 JSON 结构
        if not result or not isinstance(result, dict):
            return (False, "Invalid response format")
        # 只接受预期的字段
        is_safe = result.get("is_safe")
        explanation = result.get("explanation", "")
        if not isinstance(is_safe, bool):
            return (False, "Invalid is_safe field")
        if not isinstance(explanation, str):
            explanation = str(explanation)
        return (bool(is_safe), explanation)
    except Exception as e:
        await warn(f"LLM 安全检查失败: {e}", config)
        return (False, "")
    finally:
        config.spinner.stop(wait_id=wait_id)


# ── 持久化权限规则 ──────────────────────────────────────────

_RULES_LOCK = threading.Lock()

_COMPOUND_PREFIXES = {
    "git",
    "npm",
    "yarn",
    "pip",
    "uv",
    "cargo",
    "docker",
    "systemctl",
    "npx",
}


def _rules_path(root_dir: Path) -> Path:
    from uniclaw.context import get_app_dir

    return get_app_dir(root_dir) / "permission_rules.json"


def _load_rules(root_dir: Path | None) -> list:
    if root_dir is None:
        return []
    path = _rules_path(root_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("rules", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save_rules(rules: list, root_dir: Path):
    path = _rules_path(root_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"rules": rules}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def extract_bash_prefix(command: str) -> str:
    parts = command.strip().split()
    if not parts:
        return ""
    if parts[0] in _COMPOUND_PREFIXES and len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"
    return parts[0]


def add_permission_rule(rule_type: str, pattern: str, root_dir: Path | None):
    if root_dir is None:
        return
    with _RULES_LOCK:
        rules = _load_rules(root_dir)
        if any(r["type"] == rule_type and r["pattern"] == pattern for r in rules):
            return
        rules.append(
            {
                "type": rule_type,
                "pattern": pattern,
                "created": datetime.now().isoformat(timespec="seconds"),
            }
        )
        _save_rules(rules, root_dir)


def remove_permission_rule(rule_type: str, pattern: str, root_dir: Path | None) -> bool:
    if root_dir is None:
        return False
    with _RULES_LOCK:
        rules = _load_rules(root_dir)
        new_rules = [
            r for r in rules if not (r["type"] == rule_type and r["pattern"] == pattern)
        ]
        if len(new_rules) == len(rules):
            return False
        _save_rules(new_rules, root_dir)
        return True


def list_permission_rules(root_dir: Path | None) -> list:
    return _load_rules(root_dir)


def check_saved_bash_rule(command: str, root_dir: Path | None) -> bool:
    """检查Bash命令是否匹配用户定义的持久化规则

    Args:
        command: Bash命令字符串
        root_dir: 根目录

    Returns:
        bool: 如果命令匹配已保存的bash规则则返回True
    """
    rules = _load_rules(root_dir)
    command = command.strip()
    return any(r["type"] == "bash" and command.startswith(r["pattern"]) for r in rules)


def check_saved_tool_rule(tool_name: str, root_dir: Path | None) -> bool:
    """检查工具名称是否匹配用户定义的持久化规则

    Args:
        tool_name: 工具名称(如 "Write", "Read", "Edit" 等)
        root_dir: 根目录

    Returns:
        bool: 如果工具名称匹配已保存的tool规则则返回True
    """
    rules = _load_rules(root_dir)
    return any(r["type"] == "tool" and r["pattern"] == tool_name for r in rules)
