from __future__ import annotations

import asyncio
import json
import os
import threading
import difflib
import time
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING
import uuid

from uniclaw.agent.types import AgentStatus, AgentTask
from uniclaw.tools.registry import search_tools
from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR
from uniclaw.provider import astream
from uniclaw.provider.error_classifier import is_network_or_provider_error
from uniclaw.tools.session.session import StreamChunk, UserMessage, maybe_time_notice
from uniclaw.tools import get_core_tools, get_tools
from uniclaw.utils.message import MessageRole, extract_text
from dataclasses import dataclass, field
from uniclaw.context import (
    build_system_prompt,
    get_base_system_prompt,
    get_env_system_prompt,
)
from uniclaw.utils.project_instruction import (
    SUBDIR_CLAUDE_MD_TAG,
    _resolve_project_instruction,
    get_subdir_project_instruction,
    is_subdir_claude_md_msg,
)
from uniclaw.config import Permissions, AppConfig
from uniclaw.tools.ask import AskUserQuestion

if TYPE_CHECKING:
    pass
from uniclaw.tools.fs import Edit, Write
from uniclaw.tools.base import (
    tc_name as _tc_name,
    tc_args as _tc_args,
    parse_tc_args,
    Tool,
    ToolRuntime,
    extract_explains,
)

# 死循环检测:连续相同工具调用次数阈值
LOOP_DETECTION_THRESHOLD = 5

# 触发子目录 CLAUDE.md 按需加载的工具 → 文件路径参数名
_CLAUDE_MD_PATH_ARGS = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "ConvertToMarkdown": "file_path",
    "Glob": "path",
    "Grep": "path",
}


def _find_nearest_subdir_claude_md(
    file_path: Path, roots: list[Path], exclude_self: set[Path] | None = None
) -> Path | None:
    """从文件父目录向上查找最近的含 CLAUDE.md 的目录。

    exclusion(默认空)里的根不返回其自身 CLAUDE.md —— 用于
    root_dir(它的 CLAUDE.md 已在 system prompt 中,不重复加载)。
    外部工作空间根不在排除集中:文件落在该根本身时,返回该根自己的
    CLAUDE.md(它没进任何 system prompt,应在此按需加载)。

    Args:
        file_path: 被操作的文件路径(已 resolve)。
        roots: 可信根列表,按路径部件降序(具体优先),首个包含 file_path 者生效。
        exclude_self: 不返回自身 CLAUDE.md 的根集合。

    Returns:
        Path | None: 最近的含 CLAUDE.md 的目录;无则 None。
    """
    exclude_self = exclude_self or set()
    root = next((r for r in roots if file_path.is_relative_to(r)), None)
    if root is None:
        return None
    # 起点:文件从父目录开始;目录(Glob/Grep 传目录本身)从自身开始,
    # 否则 root 自身的项目指令会被起点跳过、永远加载不到。
    cur = file_path if file_path.is_dir() else file_path.parent
    while True:
        if cur in exclude_self:
            return None  # 碰到被排除的根(如 root_dir):不加载其自身
        if _resolve_project_instruction(cur):
            return cur  # AGENTS.md 优先,其次 CLAUDE.md
        if cur == root:
            return None  # 已到该根本身,不再向上爬出归属根
        if cur.parent == cur:
            return None  # 已到文件系统根,防止无限上溯
        cur = cur.parent


def _claude_md_trusted_roots(config: AppConfig) -> list[Path]:
    """可信根列表 = session.root_dir + 额外工作空间目录(已 resolve)。

    外部目录经 /add_dir 显式加入工作空间后才参与按需加载 — 对齐
    Claude Code 的 --add-dir 语义:默认不加载外部项目 CLAUDE.md,
    显式授权后才加载,避免不可信目录的指令注入。

    Args:
        config: AppConfig,取 current_agent.session.root_dir 与 workspace。

    Returns:
        list[Path]: 可信根列表,按路径部件数降序(具体优先)。
    """
    roots: list[Path] = []
    # 与代码库一致:root_dir 走 config.root_dir 便捷属性, 底层再取 current_agent.session.root_dir
    if config.current_agent and config.current_agent.session.root_dir:
        roots.append(Path(config.current_agent.session.root_dir).resolve())
    for d in (config.workspace or []):
        if not d:
            continue
        try:
            p = Path(d).resolve()
        except Exception:
            continue
        if p not in roots:
            roots.append(p)
    # 长路径优先(更具体的根,如 /a/b 先于 /a),保证归属判定取最近的根
    return sorted(roots, key=lambda p: len(p.parts), reverse=True)


def _maybe_load_subdir_claude_md(
    task: AgentTask,
    results: list[tuple[dict[str, Any], Any]],
    config: AppConfig,
) -> list[str]:
    """工具操作子目录文件后,按需加载该子目录最近的 CLAUDE.md 注入上下文。

    只对 _CLAUDE_MD_PATH_ARGS 中声明的工具取路径参数;同一子目录在
    当前消息流(_messages, 不含 history)中已加载过则跳过 —— compact
    把注入消息压掉后,再次访问会自动重新加载(对齐 Claude Code)。

    可信根 = session.root_dir + /add_dir 显式加入的额外工作空间目录;
    可信根之外的路径(C: 根或未授权的目录)不加载。

    Args:
        task: 当前 AgentTask。
        results: _execute_tool_calls 的 (tool_call, content) 结果列表。
        config: AppConfig,用于取 session.root_dir 与 workspace。

    Returns:
        list[str]: 本次注入的消息文本列表(供调用方广播 UserEvent,
            保持实时 WebSocket 与历史回放一致)。
    """
    roots = _claude_md_trusted_roots(config)
    if not roots:
        return []
    # root_dir 自身 CLAUDE.md 已在 system prompt,不在此重复加载
    exclude_self: set[Path] = set()
    if task.session.root_dir:
        exclude_self.add(Path(task.session.root_dir).resolve())

    # 收集本轮访问到的子目录(去重,按文件路径升序保证注入顺序稳定)
    dirs: set[Path] = set()
    for tool_call, content in results:
        name = _tc_name(tool_call)
        arg_key = _CLAUDE_MD_PATH_ARGS.get(name)
        if not arg_key:
            continue
        # 工具执行失败/被用户拒绝(结果以 TOOL_ERROR 开头)时不触发 —
        # 否则会把用户拒绝访问或并不存在的目录的 CLAUDE.md 注入上下文。
        text = extract_text(content) if content else ""
        if text.startswith(TOOL_ERROR):
            continue
        args = _tc_args(tool_call)
        raw = args.get(arg_key)
        if not raw:
            continue
        try:
            p = Path(str(raw)).resolve()
        except Exception:
            continue
        subdir = _find_nearest_subdir_claude_md(p, roots, exclude_self)
        if subdir:
            dirs.add(subdir)

    if not dirs:
        return []

    # 去重:跳过已在当前消息流中注入过的子目录
    # 注入消息是我们 add_message(USER, ...) 产生的,只能是 UserMessage;
    # 只对 UserMessage 识别,避免 AI 回复/工具结果恰好以注入前缀开头误判。
    loaded: set[Path] = set()
    for msg in task.session._messages:
        if not isinstance(msg, UserMessage):
            continue
        p = is_subdir_claude_md_msg(msg.to_content())
        # 消息必须带注入标记,且其目录属于本轮访问到的子目录
        if p and p.resolve().parent in dirs:
            loaded.add(p.resolve().parent)
    pending = sorted(dirs - loaded)

    # 全部工具结果追加完之后统一注入,保持 Anthropic tool_use/tool_result 紧邻配对
    injected: list[str] = []
    for subdir in pending:
        resolved = get_subdir_project_instruction(subdir)
        if not resolved:
            continue
        content, inst_path = resolved
        msg = f"{SUBDIR_CLAUDE_MD_TAG} 已加载 {inst_path}\n{content}"
        task.session.add_message(MessageRole.USER, msg)
        injected.append(msg)
    return injected

# 输出被截断的停止原因(OpenAI "length" / Anthropic "max_tokens")
_TRUNCATED_FINISH_REASONS = {"length", "max_tokens"}


def _is_output_truncated(resp) -> bool:
    """LLM 输出是否因达到长度上限被截断(内容/工具参数可能只生成了一半)。"""
    return (getattr(resp, "finish_reason", "") or "") in _TRUNCATED_FINISH_REASONS


def _is_loop_shutdown_noise(e: BaseException) -> bool:
    """进程/事件循环关闭期的噪音错误(非缺陷)。

    关闭/重启时收尾工作(检查点、未完的 LLM 请求)会撞上已关闭的
    default executor 与被取消的 IOCP 定时器,刷 ERROR 堆栈风暴只增噪。
    属生命周期现象,降级为一行提示。
    """
    text = f"{type(e).__name__} {e}".lower()
    return (
        "executor shutdown" in text
        or "invalid state" in text
        or "winerror 995" in text
        or "由于线程退出或应用程序请求" in str(e)
    )
from uniclaw.tools.multi_agent.sub_agent import AgentDefinition
from uniclaw.tools.multi_agent.tools import (
    subagent_check_result,
    subagent_send_to_child,
    subagent_send_to_parent,
    subagent_close,
)
from uniclaw.tools.shell import Bash
from uniclaw.utils.checkpoint import create_checkpoint
from uniclaw.utils.wakeup import wake_agent
from uniclaw.utils.git import (
    create_worktree,
    get_git_root,
    remove_worktree,
)
from uniclaw.utils.truncation import truncate_text_by_lines
from uniclaw.utils.format import format_args_for_display
from uniclaw.tools.hooks.hook_manager import HookError, HookEvent, run_hooks
import traceback

from uniclaw.utils.wrapper import error_catch
from uniclaw.console.ui import err, info, warn

DEDUP_TOOLS = frozenset({"Read", "Glob", "Grep", "webFetch"})
DEDUP_MIN_CHARS = 500  # 结果超过此长度才去重


class ReturnEvent:

    def __init__(self, default_content=None):
        self.content = default_content
        self.return_event = asyncio.Event()


@dataclass
class UserEvent:
    content: str | list[dict[str, Any]]


@dataclass
class TextChunkEvent:
    content: str


@dataclass
class ThinkingChunkEvent:
    def __init__(self, content):
        self.content = content


@dataclass
class ThinkingStartEvent:
    pass


@dataclass
class AssistantEvent:
    content: str
    tool_calls: list
    in_tokens: int = 0
    out_tokens: int = 0
    model_name: str = ""
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    cache_discount: float = 0.0
    created_at: float = 0.0
    # 停止原因(OpenAI: finish_reason / Anthropic: stop_reason),用于前端展示与截断检测
    finish_reason: str = ""


@dataclass
class ToolPreparingEvent:
    """LLM 流式输出中检测到工具调用名称,工具尚未执行。"""

    name: str
    args: dict = field(default_factory=dict)


@dataclass
class ToolStartEvent:
    name: str
    args: dict
    tool_call_id: str = ""
    explain: str = ""


@dataclass
class ToolEvent:
    name: str
    content: str
    tool_call_id: str
    args: dict = None


@dataclass
class ToolStreamEvent:
    """工具执行过程中的流式输出事件(纯 UI 显示用,不参与 LLM 交互)。"""

    name: str
    content: str
    tool_call_id: str = ""


@dataclass
class CheckpointStartEvent:
    """检查点创建开始事件,用于 UI 显示旋转器。"""


@dataclass
class CheckpointEndEvent:
    """检查点创建结束事件,用于 UI 停止旋转器。"""


@dataclass
class EndEvent:
    depth: int


@dataclass
class InterruptedEvent:
    message: str = "已中断,等待您的补充指令..."


class PermissionRequestEvent(ReturnEvent):
    def __init__(
        self,
        description: str,
        tool_call: dict = None,
        explanation: str = "",
        agent_name: str = "",
    ):
        super().__init__("无可用的 UI 响应,自动拒绝权限请求")
        self.description: str = description
        self.tool_call: dict = tool_call or {}
        self.explanation: str = explanation
        self.agent_name: str = agent_name


class SlashCommandEvent(ReturnEvent):
    """用户在 agent 运行期间输入了 /command,交由 UI 处理。"""

    def __init__(self, command: str):
        super().__init__()
        self.command: str = command


class ShellCommandEvent(ReturnEvent):
    """用户在 agent 运行期间输入了 !cmd,交由 UI 执行并将结果返回。"""

    def __init__(self, command: str, source: str = "chat"):
        super().__init__()
        self.command: str = command
        self.source: str = source


async def _check_permission(tc: dict, config: AppConfig) -> tuple[bool, str]:
    """检查工具调用是否需要用户权限确认。

    根据配置的权限模式和工具类型,判断是否自动批准该工具调用。
    某些安全操作或特定模式下的操作可以自动放行,其他操作需要用户手动确认。

    Args:
        tc (dict): 工具调用字典,包含以下键:
            - name (str): 工具名称,如 "Read", "Write", "Bash" 等
            - args (dict): 工具参数,不同工具有不同的参数字段
        config (AppConfig): 应用配置对象

    Returns:
        tuple[bool, str]: (是否自动批准, LLM解释文本)
            - 第一个元素:True 表示自动批准,False 表示需要用户确认
            - 第二个元素:LLM 生成的安全分析解释(仅 AUTO 模式下 LLM 判定不安全时有值)

    Note:
        - 计划模式切换工具始终自动批准
        - ACCEPT_ALL 模式下所有操作自动批准
        - MANUAL 模式下所有操作都需要用户确认
        - 只读类工具和记忆/技能列表工具自动批准
        - PLAN 模式下,写入计划目录的 Write 操作自动批准
        - Bash 命令通过安全检查后自动批准
        - 写入当前工作目录下文件的 Write 操作自动批准
        - AUTO 模式下,以上快速路径都未命中时,调用 LLM 检测安全性
        - 其他情况默认需要用户确认
    """
    perm_mode = config.permission_mode
    name = _tc_name(tc)

    if perm_mode == Permissions.ACCEPT_ALL:
        return (True, "")
    if perm_mode == Permissions.MANUAL:
        return (False, "")  # 始终询问

    # 安全工具自动批准(只读类工具和管理工具,computer use 启用时包含写入工具)
    from uniclaw.tools.security import is_safe_tool

    if is_safe_tool(name):
        return (True, "")

    # 活跃 skill 声明的工具自动放行
    from uniclaw.tools.skill.tools import get_active_skill_tools

    if name in get_active_skill_tools():
        return (True, "")

    # PLAN 模式下的特殊处理
    if perm_mode == Permissions.PLAN:

        # Write 工具:写入计划目录自动放行
        if name in (Write.name, Edit.name):
            from pathlib import Path

            file_path = _tc_args(tc).get("file_path", "")
            try:
                abs_file = Path(file_path).resolve()
                from uniclaw.tools.plan import get_plans_dir

                if abs_file.is_relative_to(get_plans_dir(config).resolve()):
                    return (True, "")
            except (ValueError, OSError):
                pass

    # Bash 命令安全检查(安全则直接放行,不安全则继续走后续流程包括 LLM 检测)
    if name == Bash.name:
        from uniclaw.tools.security import is_safe_bash

        args = _tc_args(tc)
        command = args.get("command", "").strip()
        if is_safe_bash(command, config.root_dir):
            return (True, "")

    # 其他工具的持久化规则检查
    from uniclaw.tools.security import check_saved_tool_rule

    if check_saved_tool_rule(name, config.root_dir):
        return (True, "")

    # Write 工具:如果写入的是可写目录下的文件,则自动放行
    if name in (Write.name, Edit.name):
        from pathlib import Path

        file_path = _tc_args(tc).get("file_path", "")
        writable_dirs = config.writable_dirs

        if writable_dirs:
            try:
                abs_file = Path(file_path).resolve()
                for d in writable_dirs:
                    abs_dir = Path(d).resolve()
                    if abs_file.is_relative_to(abs_dir):
                        return (True, "")
            except (ValueError, OSError):
                pass

    # 所有快速路径都未命中,调用 LLM 检测安全性
    from uniclaw.tools.security import llm_safe_check

    is_safe, explanation = await llm_safe_check(tc, config)
    if is_safe:
        return (True, "")
    return (False, explanation)


def _tool_in_full_catalog(name: str) -> bool:
    """工具是否在全量清单(get_all_tools 对应的注册表)里。

    在全量清单里但当前 name2tool 找不到,说明是"存在但当前环境未启用/被裁剪"
    (如 monitor_screen 需要 Windows+pywinpty),措辞应是"未启用"而非"不存在"。
    注册表尚未初始化时 entries 为空,自然返回 False(回退到"工具不存在")。
    """
    from uniclaw.tools.registry import ToolRegistry

    return name in ToolRegistry.get_instance().get_all_entries()


def _permission_desc(tc: dict) -> str:
    """生成权限请求的美观描述信息

    Args:
        tc: 工具调用字典,包含工具名称和参数

    Returns:
        格式化的权限请求描述字符串
    """
    name = _tc_name(tc)
    inp = _tc_args(tc)

    # Bash 命令执行
    if name == Bash.name:
        command = inp.get("command", "")
        return f"🖥️  运行 Shell 命令:\n   {command}"

    # 文件写入操作
    if name == Write.name:
        file_path = inp.get("file_path", "")
        return f"📝 写入文件:\n   {file_path}"

    # 文件编辑操作
    if name == Edit.name:
        file_path = inp.get("file_path", "")
        old_string = inp.get("old_string", "")
        new_string = inp.get("new_string", "")
        replace_all = inp.get("replace_all", False)
        diff = _edit_permission_diff(file_path, old_string, new_string)
        suffix = "\n   replace_all=true" if replace_all else ""
        return f"✏️  编辑文件:\n   {file_path}{suffix}\n\n{diff}"

    # 其他工具调用
    formatted_args = format_args_for_display(inp, 500, ",\n")
    return f"🔧 调用工具: {name}({formatted_args})"


def _edit_permission_diff(file_path: str, old_string: str, new_string: str) -> str:
    """Build a compact preview diff for an Edit permission prompt."""

    def _diff_lines(value: str) -> list[str]:
        lines = str(value).splitlines(keepends=True)
        if not lines and value:
            lines = [str(value)]
        return [line if line.endswith(("\n", "\r")) else f"{line}\n" for line in lines]

    old_lines = _diff_lines(old_string)
    new_lines = _diff_lines(new_string)

    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{Path(file_path).name}",
            tofile=f"b/{Path(file_path).name}",
            n=3,
        )
    )
    if not diff_lines:
        return "拟修改内容无差异。"

    max_lines = 160
    if len(diff_lines) > max_lines:
        hidden = len(diff_lines) - max_lines
        diff_lines = diff_lines[:max_lines]
        diff_lines.append(f"... ({hidden} more diff lines hidden)\n")
    return "拟修改 diff:\n" + "".join(diff_lines)


class MultiAgent:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self.id2AgentTask: dict[str, AgentTask] = {}
            self.loop: asyncio.AbstractEventLoop | None = None  # 主事件循环引用
            self._initialized = True

    @classmethod
    def get_instance(cls):
        """获取 MultiAgent 单例实例"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = object.__new__(cls)
                    cls._instance.__init__()
        return cls._instance

    async def send_event_to_user(self, event, config: AppConfig):
        """将事件放入队列。对于有 return_event 的事件,等待 UI 处理后返回内容。
        普通事件:仅发送到 task 自己的队列;无队列则丢弃。
        阻塞事件(带 return_event):队列查找链 → task 自身 → root_config → TUI 当前会话。"""
        task = config.current_agent
        queue = task.event_queue
        if not queue and hasattr(event, "return_event"):
            # root_config: 祖子代理场景的父级队列
            if config.root_config:
                queue = config.root_config.current_agent.event_queue
            # TUI: 后台任务(如 scheduler)路由到当前活跃会话
            if not queue:
                try:
                    from uniclaw.console.run import TUIApp

                    tui = TUIApp.get_instance()
                    if tui and tui.config:
                        queue = tui.config.current_agent.event_queue
                except Exception as e:
                    await err(f"获取TUI实例失败: {e}", config, e)
        if queue:
            await queue.put((task, event))

        if hasattr(event, "return_event"):
            if queue:
                await event.return_event.wait()
            return event.content

    async def _notify_sub_agent_progress(
        self,
        task: AgentTask,
        config: AppConfig,
        content: str,
        tool_calls: list | None = None,
    ):
        """异步子代理执行中产出新输出时,以 info 消息把输出内容展示给用户(不唤醒父 agent)。

        输出格式: 文本内容在前,随后每个工具调用一行,参数换行对齐显示:
            content
            tool_name1(参数1=xx,
                       参数2=xx)
            tool_name2(参数1=xx,
                       参数2=xx)

        由 _process_response 在 resp 出结果且含文本输出或工具调用时通过 create_task 调用。

        Args:
            task: 子代理任务,取 task.name 作为展示名。
            config: 子代理配置,经 parent_config 路由 info 消息。
            content: 本轮 LLM 文本输出。
            tool_calls: 本轮 LLM 发出的工具调用列表,可为空。
        """
        parent_config = config.parent_config
        if parent_config is None:
            return
        parts = []
        if content:
            parts.append(truncate_text_by_lines(content, max_tokens=500).strip())
        else:
            parts.append("")
        for tc in tool_calls or []:
            name = _tc_name(tc)
            args = _tc_args(tc)
            if args:
                indent = " " * (len(name) + 1)
                args_str = format_args_for_display(args, 500, f",\n{indent}")
            else:
                args_str = ""
            parts.append(f"{name}({args_str})")
        msg = f"[子智能体:{task.name}] " + "\n".join(parts)
        await info(msg, parent_config)

    async def wait(self, task_id: str, timeout: float = None, config: AppConfig = None):
        """
        异步等待指定任务完成并返回任务对象。

        如果设置了 timeout,每次超时后会检查 messages 是否有新增:
        有新内容则继续等待,无新内容则返回。

        Args:
            task_id (str): 任务的唯一标识符。
            timeout (float, optional): 每轮等待的超时时间(秒)。
            config (AppConfig, optional): 当前会话配置,用于错误通知和日志关联。

        Returns:
            AgentTask or None: 返回对应的任务对象。
        """
        task = self.id2AgentTask.get(task_id)
        if task is None:
            return None
        if task.future is None:
            return task

        last_msg_count = len(task.session)
        while True:
            try:
                await asyncio.wait_for(asyncio.shield(task.future), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                await err(f"等待任务完成异常: {e}", config, e)
            # 任务已完成
            if task.status in (
                AgentStatus.COMPLETED,
                AgentStatus.FAILED,
                AgentStatus.CANCELLED,
            ):
                break
            # 有 timeout 时:检查 messages 是否有新增
            current_msg_count = len(task.session)
            if current_msg_count > last_msg_count:
                last_msg_count = current_msg_count
            else:
                break  # 无新内容,结束等待
        return task

    def start_agent(
        self,
        user_message: str | list[dict[str, Any]],
        config: AppConfig,
        system_prompt: Optional[str] = None,
    ) -> AgentTask:
        task = config.current_agent
        task.prompt = user_message
        task.status = AgentStatus.PENDING
        task.cancel_event.clear()  # 清除取消标志,防止新任务被立即中断
        self.id2AgentTask[task.id] = task
        task.future = asyncio.create_task(self.run(user_message, system_prompt, config))
        return task

    async def start_sub_agent(
        self,
        user_message: str,
        config: AppConfig,
        system_prompt: str | None = None,
        agent_def: Optional[AgentDefinition] = None,
        isolation: bool = False,
        inherit_events: bool = False,
        notify_parent: bool = False,
        keep_alive: bool = False,
    ) -> AgentTask:
        """启动子代理。config 应通过 create_sub_config() 预先创建。"""
        parent_task = config.parent_agent
        task = config.current_agent
        task.prompt = user_message
        task.status = AgentStatus.PENDING
        task.notify_parent = notify_parent  # run 主循环在 resp 出结果时读取并即时唤醒
        task.keep_alive = keep_alive  # 异步子代理驻留标志,sleep_timer 可用性据此判断
        root_dir = config.root_dir

        if (
            inherit_events
            and parent_task is not None
            and parent_task.event_queue is not None
        ):
            # 仅同步子 agent(wait=True)共享父队列和 cancel_event(ESC 可直接取消)。
            # 异步子 agent 不共享:thinking/text/tool 事件由 send_event_to_user
            # 静默丢弃,中途进度改由 _notify_sub_agent_progress 以 info 展示给用户;
            # 阻塞事件(权限请求)仍经 root_config 链路由到主队列。
            task.event_queue = parent_task.event_queue
            task.cancel_event = parent_task.cancel_event
        self.id2AgentTask[task.id] = task

        base_system_prompt = get_base_system_prompt(config)
        # 环境段(日期/目录/PID)单独追加在末尾,避免打断稳定前缀缓存
        base_system_prompt += f"\n\n{get_env_system_prompt(config)}"
        # None 表示不限制工具: 子代理走 search_tools 按需加载扩展工具
        allowed_tools = None
        if agent_def:
            if agent_def.model_name:
                config.model_name = (
                    [agent_def.model_name]
                    if isinstance(agent_def.model_name, str)
                    else agent_def.model_name
                )
            if agent_def.tools:
                from uniclaw.tools.registry import ToolRegistry

                # 声明了白名单: 解析为工具对象, _run 中据此硬限制
                allowed_tools = ToolRegistry.get_instance().resolve_tools(
                    agent_def.tools
                )
            if agent_def.system_prompt:
                base_system_prompt += f"\n\n{agent_def.system_prompt}"

        # 未声明白名单时 allowed_tools 保持 None: 子代理不限制, 走 search_tools 按需加载
        # 子代理展示可搜索的扩展工具
        from uniclaw.tools.registry import get_registry_system_prompt

        registry_ctx = await get_registry_system_prompt(config)
        if registry_ctx:
            base_system_prompt += f"\n\n{registry_ctx}"
        # 用户传递的系统提示词放在最后
        if system_prompt:
            base_system_prompt += f"\n\n{system_prompt}"
        system_prompt = base_system_prompt

        if isolation:
            if root_dir is None:
                task.status = AgentStatus.FAILED
                task.result = "isolation需要root_dir(当前为None)"
                return task
            git_root = await get_git_root(root_dir)
            if not git_root:
                task.status = AgentStatus.FAILED
                task.result = "isolation需要git仓库"
                return task
            try:
                worktree_path, worktree_branch = await create_worktree(git_root)
            except Exception as e:
                task.status = AgentStatus.FAILED
                task.result = f"isolation创建工作树失败: {e}"
                return task
            task.worktree_path = worktree_path
            task.worktree_branch = worktree_branch
            notice = (
                f"\n\n[注意:你正在一个隔离的 git worktree 中工作,位于 "
                f"{worktree_path}(分支:{worktree_branch})。"
                f"你的更改与主工作区 {git_root} 隔离。"
                f"在完成之前提交你的更改,以便可以审查/合并。]"
            )
            system_prompt = system_prompt + notice
            config.writable_dirs.insert(0, worktree_path)
            task.session.root_dir = Path(worktree_path)

        def _notify_message(reason: str) -> str:
            """构造发给父 agent 的 [child_agent] 通知消息。"""
            # 已关闭的任务会从列表移除: 再引导 check_result 只会换来
            # "任务不存在" —— 通知必须与真实状态一致
            if task.closed:
                return (
                    f"{SYSTEM_PREFIX}[child_agent]\n"
                    f"名称: {task.name}\n"
                    f"任务ID: {task.id}\n"
                    f"状态: {task.status}\n"
                    f"消息: {reason}\n"
                    f"- 该任务已被关闭并从任务列表移除,结果无法再读取,"
                    f"不要再调用 {subagent_check_result.name}\n"
                    f"- 需要继续这项工作时,重新发起子代理创建即可"
                )
            return (
                f"{SYSTEM_PREFIX}[child_agent]\n"
                f"名称: {task.name}\n"
                f"任务ID: {task.id}\n"
                f"状态: {task.status}\n"
                f"消息: {reason}\n"
                f'- 请调用 {subagent_check_result.name}(task_id="{task.id}") 来读取结果'
                f"(结果保留、可重复读取,不再需要时用"
                f' {subagent_close.name}(task_id="{task.id}") 回收)\n'
                f'- 使用 {subagent_send_to_child.name}(task_id="{task.id}", message="...") 发送消息\n'
                f'- 任务仍在运行需提前停止时,使用 {subagent_close.name}(task_id="{task.id}") 关闭'
            )

        async def _notify_parent(reason: str):
            """向父 agent 发送唤醒通知(尽力而为,失败仅记日志)。"""
            if (
                notify_parent
                and parent_task is not None
                and parent_task is not task
                and config.parent_config is not None
            ):
                try:
                    await wake_agent(_notify_message(reason), config.parent_config)
                except Exception as e:
                    await warn(f"通知父 agent 失败(子代理 {task.name}): {e}", config)

        async def _run_proc(user_message, system_prompt, config: AppConfig):
            try:
                task.user_queue.put_nowait(user_message)
                while not task.cancel_event.is_set():
                    try:
                        msg = await asyncio.wait_for(
                            task.user_queue.get(),
                            timeout=1.0 if keep_alive else None,
                        )
                    except asyncio.TimeoutError:
                        if keep_alive:
                            continue
                        break
                    await self.run(msg, system_prompt, config, allowed_tools)
                    if task.cancel_event.is_set():
                        task.result = "任务已取消。"
                        # 取消也通知父 agent,避免父 agent 永远等不到结果
                        await _notify_parent("此子智能体已被取消。")
                        return
                    # 失败原因(如 _run_init 的深度超限)不得被空 assistant 输出覆盖,
                    # 否则上层只看到 "(无输出 — 状态: failed)" 无从诊断
                    if task.status == AgentStatus.FAILED and task.result:
                        pass
                    else:
                        assistant_out = task.session.get_assistant_messages()
                        task.result = assistant_out if assistant_out else task.result
                    await _notify_parent("此子智能体有新的输出。")
                    if not keep_alive:
                        break
                if not task.result:
                    task.result = task.session.get_assistant_messages()
            except Exception as e:
                task.result = f"任务处理失败:{str(e)}"
                task.status = AgentStatus.FAILED
                await err(
                    f"子代理 {task.name} 执行失败: {e}\n{traceback.format_exc()}",
                    config,
                    e=e,
                )
                # 失败也通知父 agent,避免父 agent 永远等不到结果
                await _notify_parent(f"此子智能体执行失败:{e}")
            finally:
                # status 由 _run_cleanup 按回合结果置 COMPLETED/FAILED/CANCELLED,
                # 此处不覆盖 —— keep_alive 驻留是"存活"维度,不改写回合结果。
                # notify_parent 的子代理:在 _notify_parent(即 wake_agent)之后
                # 补发 EndEvent(depth=0)。wake_agent 会重启 bridge,新 bridge
                # 先消费子代理结果,再读到此 EndEvent 正常退出。
                # (同步子代理不走这里,其 EndEvent(depth=0) 由 _run_cleanup 处理)
                if (
                    notify_parent
                    and config.parent_config is not None
                    and not config.parent_config.has_running_subs()
                ):
                    _ptask = config.parent_config.current_agent
                    if _ptask and _ptask.status not in (
                        AgentStatus.RUNNING,
                        AgentStatus.PENDING,
                    ):
                        await self.send_event_to_user(
                            EndEvent(depth=0), config.parent_config
                        )
                if task.worktree_path:
                    try:
                        await remove_worktree(
                            task.worktree_path, task.worktree_branch, root_dir
                        )
                    except Exception as e:
                        # 清理失败不掩盖真实结果/异常
                        await warn(f"移除工作树失败(子代理 {task.name}): {e}", config)

        task.future = asyncio.create_task(
            _run_proc(user_message, system_prompt, config)
        )

        # 兜底回调:防止未来某条路径绕过 try/except,异常被静默吞掉直到 GC
        def _log_future_exception(t: asyncio.Task):
            if not t.cancelled() and t.exception() is not None:
                err_sync = (
                    f"子代理 {task.name} future 异常:\n"
                    f"{''.join(traceback.format_exception(t.exception()))}"
                )
                asyncio.get_running_loop().call_soon_threadsafe(
                    asyncio.create_task, err(err_sync, config)
                )

        task.future.add_done_callback(_log_future_exception)
        return task

    def list_tasks(self) -> list[AgentTask]:
        return list(self.id2AgentTask.values())

    def send_message(self, task_id: str, message: str) -> bool:
        task = self.id2AgentTask.get(task_id)
        if task is None:
            return False
        if task.closed:
            return False
        # 执行过程已真正结束(future 已完成或从未启动且已终态)不能再收消息;
        # keep_alive 子代理回合结束后 status=COMPLETED 但 future 存活(驻留),
        # 仍可接收消息 —— 这正是 keep_alive 的用途。
        if task.is_finished:
            return False
        task.user_queue.put_nowait(message)
        return True

    def close_agent(self, task: AgentTask) -> str:
        """关闭子代理: 标记 closed,执行过程仍存活则发取消信号。

        调用方(父 agent)负责把任务从自己的 sub_configs 移除。

        Returns:
            str: "already_closed" (此前已关闭) 或 "closed" (本次关闭)。
        """
        if task.closed:
            return "already_closed"
        task.closed = True
        # 执行过程仍存活(含 keep_alive 驻留: status=COMPLETED 但 future 未结束)
        # 必须发取消信号,否则 _run_proc 循环继续空转成僵尸。
        if not task.is_finished:
            task.cancel_event.set()
        return "closed"

    def detach_sub_config(self, owner_config: AppConfig, task: AgentTask) -> None:
        """把 task 对应的 sub_config 从 owner 的 sub_configs 移除,避免 list_tasks 残留。"""
        if owner_config is None:
            return
        owner_config.sub_configs = [
            sub for sub in owner_config.sub_configs if sub.current_agent is not task
        ]

    async def _run_init(self, user_message, config: AppConfig) -> bool:
        """初始化 run 环境:深度检查、钩子、消息。成功返回 True,失败返回 False。"""
        task = config.current_agent
        # max_agent_depth = 最大子代理嵌套层数(root depth=0 不计):
        # depth > max 才算超限,因此 max=2 允许子代理 depth=1 和孙代理 depth=2。
        # (旧判断 depth >= max 会把孙代理挡在默认配置外,嵌套链路直接 failed)
        if config.depth > config.max_agent_depth:
            task.status = AgentStatus.FAILED
            task.result = (
                f"错误:超过最大嵌套深度 (depth={config.depth}, "
                f"max_agent_depth={config.max_agent_depth})"
            )
            # 补发 EndEvent,否则 WebUI bridge 永不退出、事件无人消费
            await self.send_event_to_user(EndEvent(depth=config.depth), config)
            return False
        task.status = AgentStatus.RUNNING
        if not config.is_sub:
            await run_hooks(
                HookEvent.SESSION_START,
                {"user_message": extract_text(user_message), "depth": config.depth},
                config=config,
                task=task,
            )
        # 时间感知: 与上次真实用户发言间隔过久或跨天时,先注入当前时间告知。
        # 系统通知唤醒(监控/定时任务等)不算用户开口,不注入
        is_system_msg = extract_text(user_message).strip().startswith(SYSTEM_PREFIX)
        if not is_system_msg:
            notice = maybe_time_notice(task.session)
            if notice:
                task.session.add_message(MessageRole.USER, notice)
                await self.send_event_to_user(UserEvent(notice), config)
        task.session.add_message(MessageRole.USER, user_message)
        await self.send_event_to_user(UserEvent(user_message), config)
        return True

    async def _stream_response(
        self, task, system_message, config: AppConfig, tools, model_ref: str = ""
    ) -> StreamChunk | None:
        """异步流式调用 LLM,处理 thinking/text chunk。返回 resp,取消返回 None。

        Args:
            model_ref: 模型引用(带 provider 前缀),如 "openrouter/openai/gpt-4o"。
                       为空时使用 config 中的默认配置。
        """
        try:
            resp = None
            async for chunk in astream(
                system_message,
                task.session,
                model_name=model_ref,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                top_p=config.top_p,
                tools=tools,
                config=config,
            ):
                if task.cancel_event.is_set():
                    task.status = AgentStatus.CANCELLED
                    await self.send_event_to_user(InterruptedEvent(), config)
                    return None
                if resp is None:
                    resp = chunk
                else:
                    resp += chunk
                if chunk.reasoning_content:
                    await self.send_event_to_user(
                        ThinkingChunkEvent(chunk.reasoning_content), config
                    )
                if chunk.content:
                    await self.send_event_to_user(TextChunkEvent(chunk.content), config)
                if chunk.new_tool_call_name:
                    await self.send_event_to_user(
                        ToolPreparingEvent(
                            chunk.new_tool_call_name, chunk.new_tool_call_args
                        ),
                        config,
                    )
            if task.cancel_event.is_set():
                await self.send_event_to_user(InterruptedEvent(), config)
                return None
            return resp
        except Exception as e:
            # 网络错误/模型厂商 API 错误属预期内外部故障(fallback 层会 warn/err 提示),不写日志
            if not is_network_or_provider_error(e):
                if _is_loop_shutdown_noise(e):
                    # 进程/事件循环关闭中的收尾请求,不是缺陷: 不刷堆栈
                    await warn(f"LLM 流式响应中断(进程正在关闭,跳过): {e}", config)
                else:
                    await err(f"LLM 流式响应失败: {e}", config, e)
            raise  # 向上抛出异常,由调用方处理 fallback

    async def _process_response(self, resp, task, config: AppConfig):
        """处理 LLM 响应:构建消息、记录 usage、发送事件。返回 (tool_calls, tool_explains)。"""
        content = resp.content or ""
        tool_calls = resp.tool_calls
        reasoning = resp.reasoning_content or ""

        # 提取 _explain 参数并从 tool_calls 中移除(explain 模式,sub-agent 不受影响)
        if config.explain_mode and not config.is_sub and tool_calls:
            tool_calls, tool_explains = extract_explains(tool_calls)
        else:
            tool_explains = {}

        # 输出被截断(达到输出长度上限): 内容与工具参数可能只生成了一半。
        # 无论有没有 tool_calls 都要提示 —— 正文被截断同样不完整
        if _is_output_truncated(resp):
            await warn(
                f"模型输出被截断(finish_reason={resp.finish_reason}, 达到输出长度"
                f"上限), 本轮内容/工具参数可能不完整",
                config,
            )

        in_tokens = resp.usage.input_tokens if resp.usage else 0
        out_tokens = resp.usage.output_tokens if resp.usage else 0
        total_tokens = resp.usage.total_tokens if resp.usage else in_tokens + out_tokens
        cached_tokens = resp.usage.cached_tokens if resp.usage else 0
        cache_write_tokens = resp.usage.cache_write_tokens if resp.usage else 0
        cache_discount = resp.usage.cache_discount if resp.usage else 0.0
        actual_model = resp.model_name or (
            config.model_name[0] if config.model_name else ""
        )
        usage_dict = {
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cache_discount": cache_discount,
        }

        task.session.add_message(
            MessageRole.ASSISTANT,
            content,
            model_name=actual_model,
            usage=usage_dict,
            tool_calls=tool_calls,
            reasoning_content=reasoning or None,
            finish_reason=resp.finish_reason,
        )

        await run_hooks(
            HookEvent.PRE_ASSISTANT,
            {
                "content": resp.content,
                "tool_calls": resp.tool_calls,
                "in_tokens": in_tokens,
                "out_tokens": out_tokens,
                "model_name": actual_model,
            },
            config=config,
            task=task,
        )
        await self.send_event_to_user(
            AssistantEvent(
                content=resp.content,
                tool_calls=resp.tool_calls,
                in_tokens=in_tokens,
                out_tokens=out_tokens,
                model_name=actual_model,
                cached_tokens=cached_tokens,
                cache_write_tokens=cache_write_tokens,
                cache_discount=cache_discount,
                created_at=time.time(),
                finish_reason=resp.finish_reason,
            ),
            config,
        )
        # 异步子代理执行中有新输出 → info 展示输出给用户(create_task 不阻塞主循环)
        if task.notify_parent and (content or tool_calls):
            asyncio.create_task(
                self._notify_sub_agent_progress(task, config, content, tool_calls)
            )
        from uniclaw.utils.usage import record_usage

        await record_usage(
            in_tokens, out_tokens, len(resp.tool_calls), model=actual_model
        )
        return tool_calls, tool_explains

    async def _check_infinite_loop(
        self,
        tool_calls: list[dict],
        recent_tool_calls: list[tuple],
        task: AgentTask,
        config: AppConfig,
    ) -> bool:
        """死循环检测:连续 N 次同参数同工具、且结果也毫无变化才判定死循环。

        将本轮调用签名追加到 recent_tool_calls(滑动窗口,原地修改),
        元素为 (签名, 结果指纹);指纹在工具执行后由 _execute_tool_calls 回填。

        只看签名会误伤"每次都推进状态"的调用 —— 如连续 browser_back:
        每次都真的后退,最后一次撞到"无历史"边界提示,同样签名连发,
        但每步都在推进,不是死循环。因此判定还要求最近的结果指纹相同
        (原地打转、毫无进展)才拦截。

        检测到死循环时通知 UI 并以 tool 消息注入打破提示,调用方应跳过本次工具执行。

        Args:
            tool_calls: 本轮 LLM 发出的工具调用列表。
            recent_tool_calls: 最近记录 [(签名, 结果指纹)],原地更新。
            task: 当前 agent 任务。
            config: 应用配置。

        Returns:
            bool: True 表示检测到死循环且已注入打破提示。
        """
        current_calls = tuple(
            sorted(
                (_tc_name(tc), json.dumps(_tc_args(tc), sort_keys=True))
                for tc in tool_calls
            )
        )
        recent_tool_calls.append((current_calls, ""))  # fp 待执行后回填
        # 只保留最近 N 条记录
        del recent_tool_calls[:-LOOP_DETECTION_THRESHOLD]
        if (
            len(recent_tool_calls) < LOOP_DETECTION_THRESHOLD
            or len({sig for sig, _ in recent_tool_calls}) != 1
        ):
            return False

        # 同签名连发,但结果指纹在变化(每步都有进展)→ 不是死循环
        fps = {fp for _, fp in recent_tool_calls if fp}
        if len(fps) > 1:
            return False

        # 连续 N 次完全相同的工具调用且结果无进展,判定为死循环
        tool_names = [name for name, _ in current_calls]
        tool_name_str = ", ".join(tool_names)
        await self.send_event_to_user(
            TextChunkEvent(
                f"\n⚠️ 检测到死循环:工具 `{tool_name_str}` 连续调用 "
                f"{LOOP_DETECTION_THRESHOLD} 次且参数完全相同。\n"
            ),
            config,
        )
        # 给 AI 发消息打破死循环,让它改变策略。
        # 以 tool 消息回复本次 tool_calls,保证每个工具调用都有对应结果,
        # 避免部分模型校验"assistant.tool_calls 必须有配套 tool 消息"而报错
        loop_break_msg = (
            f"{TOOL_ERROR}: 你已经连续 {LOOP_DETECTION_THRESHOLD} 次使用相同的参数调用工具 `{tool_name_str}`,"
            f"这表明你可能陷入了死循环。请立即停止当前操作,换一种不同的方法或思路来完成任务。"
        )
        for tc in tool_calls:
            tc_name = _tc_name(tc)
            tc_id = tc.get("id", "")
            tc_args = dict(_tc_args(tc))
            # UI 实时显示:与正常工具流程一致,start/end 成对发送
            await self.send_event_to_user(
                ToolStartEvent(tc_name, tc_args, tool_call_id=tc_id),
                config,
            )
            await self.send_event_to_user(
                ToolEvent(
                    name=tc_name,
                    content=loop_break_msg,
                    tool_call_id=tc_id,
                    args=tc_args,
                ),
                config,
            )
            task.session.add_message(
                MessageRole.TOOL,
                loop_break_msg,
                name=tc_name,
                tool_call_id=tc_id,
                args=tc_args,
            )
        recent_tool_calls.clear()
        return True

    async def _execute_single_tool(
        self,
        tool_call,
        name2tool,
        config: AppConfig,
        explain: str | None = None,
        output_truncated: bool = False,
    ) -> tuple[dict, Any]:
        """执行单个工具调用(权限检查 + hooks + 执行 + UI 事件)。

        Args:
            output_truncated: 本轮 LLM 输出被截断 —— 参数解析失败时可断定是
                生成中断而非模型没传, 报错给出分块重试指引。

        返回 (tool_call, tool_resp_content)。
        """
        task = config.current_agent
        tool_resp_content = None
        tc_name = _tc_name(tool_call)
        tc_args, args_issue = parse_tc_args(tool_call)

        # 查找工具
        try:
            tool = name2tool[tc_name]
        except KeyError:
            # 先查"未启用/未配置"清单: 直接调用禁用工具时给出根因,
            # 而不是误导性的"工具不存在"(search_tools 路径已能显示根因)
            _reasons = getattr(config, "unavailable_tool_reasons", None) or {}
            _reason = _reasons.get(tc_name) if hasattr(_reasons, "get") else None
            if _reason:
                tool_resp_content = f"{TOOL_ERROR}: 工具 {tc_name} 未启用: {_reason}"
            elif task.allowed_tools_set and tc_name in task.allowed_tools_set:
                tool_resp_content = (
                    f"{TOOL_ERROR}: '{tc_name}' 是扩展工具,当前未加载。"
                    f'请先使用 {search_tools.name} 搜索 "{tc_name}" 来加载该工具,然后重试。'
                )
            elif _tool_in_full_catalog(tc_name):
                # 在全量清单(get_all_tools)里但当前环境未启用/被裁剪:
                # 措辞必须是"未启用",不能报"工具不存在"(会误导调用方去改工具名)
                tool_resp_content = (
                    f"{TOOL_ERROR}: 工具 {tc_name} 未启用: 当前环境未启用或未配置该工具"
                    f"(可调用 {search_tools.name} 查询具体原因)"
                )
            else:
                tool_resp_content = f"{TOOL_ERROR}: 工具不存在: {tc_name}"

        # 参数 JSON 非法(疑似截断/损坏): 不调用工具, 直接返回诊断信息。
        # 与"模型没传参数"(空 arguments, 走参数绑定校验报缺参)区分开,
        # 免得参数丢失被伪装成缺参错误, 两种情况报错文本一模一样。
        # 完整原始 arguments 在 tc_args 的 _raw_arguments 里, 随工具事件与
        # 会话 tool 消息保留, 丢失现场可还原
        if tool_resp_content is None and args_issue:
            await warn(f"[{tc_name}] {args_issue}", config)
            if output_truncated:
                # 有 finish_reason=length/max_tokens 佐证, 可以断定是生成被截断
                tool_resp_content = (
                    f"{TOOL_ERROR}: {tc_name} 参数解析失败: 模型输出被截断(达到输出"
                    f"长度上限), 工具参数只生成了一半, 不是漏传。\n"
                    f"{args_issue}\n"
                    f"请把内容拆小后分多次调用(如大文件先写骨架, 再用 Edit 分段追加),"
                    f"或调大 max_tokens 后重试"
                )
            else:
                tool_resp_content = (
                    f"{TOOL_ERROR}: {tc_name} 参数解析失败: {args_issue}\n"
                    f"参数在到达执行器前已损坏, 不是漏传。请重新调用并附上完整参数;"
                    f"若内容过长疑似被截断, 请分块传入"
                )

        # PRE_TOOL_USE hook
        if tool_resp_content is None:
            try:
                await run_hooks(
                    HookEvent.PRE_TOOL_USE,
                    {
                        "tool_name": tc_name,
                        "tool_call": tool_call,
                        "args": tc_args,
                    },
                    config=config,
                    task=task,
                )
            except HookError as e:
                tool_resp_content = f"{TOOL_ERROR}: Hook 阻止了工具调用: {e}"

        # 权限检查
        if tool_resp_content is None:
            permitted, llm_explanation = await _check_permission(tool_call, config)
            if not permitted:
                description = _permission_desc(tool_call)
                try:
                    await run_hooks(
                        HookEvent.PERMISSION_REQUEST,
                        {
                            "tool_name": tc_name,
                            "tool_call": tool_call,
                            "args": tc_args,
                            "description": description,
                            "explanation": llm_explanation,
                        },
                        config=config,
                        task=task,
                    )
                    req = PermissionRequestEvent(
                        description=description,
                        tool_call=tool_call,
                        explanation=llm_explanation,
                        agent_name=task.name,
                    )
                    permitted = await self.send_event_to_user(req, config)
                except HookError as e:
                    permitted = f"Hook blocked permission request: {e}"
                await run_hooks(
                    HookEvent.PERMISSION_RESPONSE,
                    {
                        "tool_name": tc_name,
                        "tool_call": tool_call,
                        "args": tc_args,
                        "permitted": permitted is True,
                        "response": permitted,
                    },
                    config=config,
                    task=task,
                )
            if permitted is True:
                tc_id = tool_call.get("id", "")
                await self.send_event_to_user(
                    ToolStartEvent(
                        tc_name,
                        dict(tc_args),
                        tool_call_id=tc_id,
                        explain=explain or "",
                    ),
                    config,
                )
                try:

                    async def _stream_writer(
                        tool_call_id: str, content: str, _tc_name=tc_name
                    ):
                        # 凭 tool_call_id 路由,前端按 session_id:tool_call_id 定位工具块
                        await self.send_event_to_user(
                            ToolStreamEvent(
                                name=_tc_name,
                                content=content,
                                tool_call_id=tool_call_id,
                            ),
                            config,
                        )

                    rt = ToolRuntime(
                        config=config, tool_call_id=tc_id, stream_writer=_stream_writer
                    )
                    tool_resp_content = await tool(**tc_args, tool_runtime=rt)
                    # 标记扩展工具已使用(LRU:移到最前,防止被淘汰),核心工具不参与能量管理
                    from uniclaw.tools.registry import CORE_TOOL_NAMES

                    if tc_name not in CORE_TOOL_NAMES:
                        task.extended_mgr.touch(tc_name)
                    if isinstance(tool_resp_content, str):
                        tool_resp_content = truncate_text_by_lines(tool_resp_content)
                    # 只读工具去重:结果与之前相同且较大时省略
                    dedup_msg = task.session.check_dedup(
                        tc_name, tc_args, tool_resp_content
                    )
                    if dedup_msg:
                        tool_resp_content = dedup_msg
                except Exception as e:
                    await err(f"{TOOL_ERROR}: [{tc_name}] {e}", config, e)
                    tool_resp_content = f"{TOOL_ERROR}: {e}"
            else:
                tool_resp_content = (
                    f"{TOOL_ERROR}: 用户拒绝: {permitted}"
                    if isinstance(permitted, str) and permitted.strip()
                    else f"{TOOL_ERROR}: 用户拒绝执行"
                )

        # POST_TOOL_USE hook
        await run_hooks(
            HookEvent.POST_TOOL_USE,
            {
                "tool_name": tc_name,
                "tool_call": tool_call,
                "args": tc_args,
                "result": extract_text(tool_resp_content),
            },
            config=config,
            task=task,
        )
        display_content = (
            tool_resp_content
            if isinstance(tool_resp_content, str)
            else extract_text(tool_resp_content, separator="\n")
        )
        await self.send_event_to_user(
            ToolEvent(
                name=tc_name,
                content=display_content,
                tool_call_id=tool_call.get("id", ""),
                args=tc_args,
            ),
            config,
        )
        return tool_call, tool_resp_content

    async def _execute_tool_calls(
        self,
        tool_calls,
        name2tool,
        config: AppConfig,
        tools: list = None,
        tool_explains: dict[str, str] | None = None,
        loop_records: list[tuple] | None = None,
        output_truncated: bool = False,
    ) -> bool:
        """并行执行工具调用列表。返回 True 表示被 cancel。

        loop_records 非空时,把本轮结果指纹回填进最后一条记录,
        供死循环检测区分"原地打转"与"每步都有进展"。
        output_truncated: 本轮 LLM 输出被截断, 透传给单工具执行生成明确报错。
        """
        task = config.current_agent
        if tool_explains is None:
            tool_explains = {}

        # 并行执行所有工具
        results = await asyncio.gather(
            *[
                self._execute_single_tool(
                    tc,
                    name2tool,
                    config,
                    tool_explains.get(tc.get("id", ""), ""),
                    output_truncated=output_truncated,
                )
                for tc in tool_calls
            ]
        )

        if loop_records:
            # 指纹取各工具结果文本前缀拼接: 有进展(结果变化)就不算死循环
            fp = "|".join(
                (
                    content
                    if isinstance(content, str)
                    else extract_text(content, separator="\n")
                )[:120]
                for _, content in results
            )
            sig, _ = loop_records[-1]
            loop_records[-1] = (sig, fp)

        # 按顺序处理结果: add_message + cancel 检查
        for tool_call, tool_resp_content in results:
            tc_name = _tc_name(tool_call)
            tc_id = tool_call.get("id", "")
            explain_text = tool_explains.get(tc_id, "")
            # 检查是否为多模态内容(如图片),需要特殊处理
            _mm_types = {"image_url", "input_audio", "video_url"}
            if isinstance(tool_resp_content, list) and any(
                isinstance(b, dict) and b.get("type") in _mm_types
                for b in tool_resp_content
            ):
                # 提取文本部分作为 tool 回复
                extracted = extract_text(tool_resp_content, separator="\n")
                task.session.add_message(
                    MessageRole.TOOL,
                    extracted or "(见下方多媒体内容)",
                    name=tc_name,
                    tool_call_id=tc_id,
                    args=_tc_args(tool_call),
                    explain=explain_text,
                )
                # 将多模态内容作为 user 消息,让 LLM 能看到图片/音频/视频
                task.session.add_message(MessageRole.USER, tool_resp_content)
                # 广播给前端,让流式输出期间也能显示图片
                await self.send_event_to_user(UserEvent(tool_resp_content), config)
            else:
                # TOOL 消息 content 必须是 str,非 str 内容需转换。
                # 走到这里的 list 是未知/兜底类型(不含 image/audio/video 块),
                # 用换行连接各 text 块, 避免被默认空格黏成一行。
                final_content = (
                    tool_resp_content
                    if isinstance(tool_resp_content, str)
                    else extract_text(tool_resp_content, separator="\n")
                )
                task.session.add_message(
                    MessageRole.TOOL,
                    final_content,
                    name=tc_name,
                    tool_call_id=tc_id,
                    args=_tc_args(tool_call),
                    explain=explain_text,
                )
        if task.cancel_event.is_set():
            task.status = AgentStatus.CANCELLED
            await self.send_event_to_user(InterruptedEvent(), config)
            return True
        # 按需加载子目录 CLAUDE.md(所有工具结果之后统一注入,保持 tool 配对)
        injected_msgs = _maybe_load_subdir_claude_md(task, results, config)
        # 广播注入消息,保证实时 WebSocket 与历史回放显示一致
        for msg in injected_msgs:
            await self.send_event_to_user(UserEvent(msg), config)
        # 加载待发现工具,清理被淘汰的扩展工具
        if tools is not None:
            task.extended_mgr.apply(tools, name2tool)
        return False

    async def _save_session(self, config: AppConfig):
        """保存会话。"""
        try:
            from uniclaw.tools.session.session_manager import SessionManager

            await SessionManager.save_session(config)
        except Exception as e:
            await warn("保存会话失败", config, e)

    async def _save_memory(self, config: AppConfig):
        """保存记忆(异步,不阻塞主流程)。"""
        try:
            from uniclaw.tools.memory.auto_review import review_and_save_if_due

            saved = await review_and_save_if_due(config)
            for memory in saved:
                await info(
                    f"已保存一条新记忆: {memory.name}\n{memory.description}", config
                )
        except Exception as e:
            await warn("保存记忆失败", config, e)

    async def _run_cleanup(self, task, config: AppConfig):
        """设置最终状态,触发 SESSION_END 钩子,保存会话和记忆,发送 EndEvent。"""
        if task.status == AgentStatus.RUNNING:
            # 本回合已产出最终输出、回合结束 → COMPLETED。
            # keep_alive 子代理驻留等下一条消息是"存活"维度(future 未结束,
            # 见 can_accept_messages),不能覆盖"本回合已完成"这个事实 ——
            # 否则通知/check_result/list_tasks 三处状态对不上,读取后也不回收。
            task.status = AgentStatus.COMPLETED
        # 子代理生命周期:完成后保留在父代理 sub_configs 里,父代理才能按
        # [child_agent] 通知引导的 ID 调 check_result 读到最终输出。
        # 回收时机:
        #   - 同步子代理(notify_parent=False):结果已由 subagent_create 内联返回,
        #     父代理拿到结果后不再需要它,立即回收
        #   - 异步子代理:check_result 是**纯读**不回收,由显式 subagent_close 回收
        #     (读操作不该有副作用,否则"读完一轮 → 再派活"的多轮协作走不通)
        parent_config = config.parent_config
        if parent_config and not task.notify_parent:
            try:
                parent_config.sub_configs.remove(config)
            except ValueError:
                pass
        if not config.is_sub:
            await run_hooks(
                HookEvent.SESSION_END,
                {"status": task.status, "depth": config.depth},
                config=config,
                task=task,
            )
            # 异步保存,不阻塞(完成后发 SessionSavedEvent)
            asyncio.create_task(self._save_session(config))
            asyncio.create_task(self._save_memory(config))
        # 主 agent: 有正在运行的 subagent 时不发 EndEvent,等它们完成
        # WAITING/PENDING 状态的子代理不等待(下一轮对话可能被唤醒)
        if not config.is_sub and config.has_running_subs():
            await info(
                f"主 agent 已结束,等待 {len(config.get_running_subs())} 个子代理完成...",
                config,
            )
            return
        # 子代理: 发送 depth>0 的 EndEvent,通知前端子代理完成
        # 主 agent: 发送 depth=0 的 EndEvent,通知 bridge 退出
        await self.send_event_to_user(EndEvent(depth=config.depth), config)
        # 子代理结束后,帮父 agent 检查:父 agent 已结束且无其他 RUNNING 的 subagent,
        # 补发 EndEvent(depth=0) 让 WebUI bridge 退出。
        # 父代理被取消/失败时无人发 depth=0 的 EndEvent(bridge 永不退出),
        # 所以除 RUNNING 外的终态都要补发。
        #
        # 但 notify_parent=True 的子代理跳过:因为 _run_proc 会在 _notify_parent
        # (wake_agent)之后再发 EndEvent(depth=0),确保 bridge 先消费完子代理结果
        # 再退出。如果这里提前发,bridge 会立即退出,wake_agent 创建的新 bridge
        # 启动时读到残留旧事件,导致前端显示过期内容。
        if (
            parent_config
            and not task.notify_parent
            and not parent_config.has_running_subs()
        ):
            parent_task = parent_config.current_agent
            if parent_task and parent_task.status not in (
                AgentStatus.RUNNING,
                AgentStatus.PENDING,
            ):
                await self.send_event_to_user(EndEvent(depth=0), parent_config)

    @error_catch("agent")
    async def run(
        self,
        user_message: str | list[dict[str, Any]],
        system_message: Optional[str] = None,
        config: AppConfig = None,
        allowed_tools: list[Tool] | None = None,
    ):
        task = config.current_agent
        try:
            await self._run(user_message, system_message, config, allowed_tools)
        except asyncio.CancelledError:
            task.status = AgentStatus.CANCELLED
            # 必须补发 EndEvent,否则 WebUI bridge 不退出,
            # 后续事件入队无人消费,前端推送从此静默
            await self.send_event_to_user(EndEvent(depth=config.depth), config)
            raise
        except Exception:
            # 异常时确保状态归位并发 EndEvent,否则 status 卡在 RUNNING:
            # bridge 不退出、前端一直显示运行中,后续消息只入队无人消费(被静默吞掉)
            if task.status in (AgentStatus.RUNNING, AgentStatus.PENDING):
                task.status = AgentStatus.FAILED
            await self.send_event_to_user(EndEvent(depth=config.depth), config)
            raise

    async def _run(
        self,
        user_message: str | list[dict[str, Any]],
        system_message: Optional[str] = None,
        config: AppConfig = None,
        allowed_tools: list[Tool] | None = None,
    ):
        task = config.current_agent
        if not await self._run_init(user_message, config):
            return

        # 自动创建 Git 检查点(使用用户消息的文本部分作为描述)
        await self.send_event_to_user(CheckpointStartEvent(), config)
        try:
            await create_checkpoint(
                task.session.root_dir, message=extract_text(user_message)
            )
        except Exception as e:
            if _is_loop_shutdown_noise(e):
                await warn(f"创建检查点已跳过(进程正在关闭): {e}", config=config)
            else:
                await err(f"创建检查点失败(已跳过): {e}", config=config, e=e)
        await self.send_event_to_user(CheckpointEndEvent(), config)
        if system_message is None:
            system_message = await build_system_prompt(config)
        # 使用核心工具(约 15 个)+ search_tools,扩展工具按需加载
        is_sub = config.is_sub
        tools = list(await get_core_tools(sub_agent=is_sub))
        if is_sub:
            # allowed_tools 非 None 时为白名单; 否则不限制(全集, 走 search_tools 按需加载)
            if allowed_tools:
                allowed_names = {t.name for t in allowed_tools}
                # subagent_send_to_parent 是子代理核心工具(get_core_tools 已加载),
                # 不受 agent_def 白名单过滤
                tools = [
                    t
                    for t in tools
                    if t.name in allowed_names
                    or t.name == subagent_send_to_parent.name
                ]
                # 专用代理显式声明的扩展工具直接加载,避免通过 search_tools 绕过白名单。
                loaded_names = {t.name for t in tools}
                tools.extend(t for t in allowed_tools if t.name not in loaded_names)
                ext_names = allowed_names
            else:
                ext_names = {t.name for t in await get_tools(config)}
            # 同步子代理(父代理阻塞等待最终结果)无法中途发消息,不加载该工具
            if not task.notify_parent:
                tools = [t for t in tools if t.name != subagent_send_to_parent.name]
            task.allowed_tools_set = {t.name for t in tools} | ext_names
        else:
            task.allowed_tools_set = {t.name for t in await get_tools(config)}

        name2tool = {tool.name: tool for tool in tools}
        # 恢复上次会话加载过的扩展工具
        mgr = task.extended_mgr
        if mgr.loaded:
            from uniclaw.tools.registry import ToolRegistry

            _entries = ToolRegistry.get_instance().get_all_entries()
            mgr.restore_session(
                list(mgr.loaded), _entries, name2tool, tools, task.allowed_tools_set
            )
        compact_task: asyncio.Task | None = None
        model_list = config.model_name if config.model_name else [""]
        # 死循环检测:记录最近的工具调用签名
        recent_tool_calls: list[tuple[str, str]] = []  # [(tool_name, args_json), ...]
        while True:
            while True:
                if task.cancel_event.is_set():
                    task.status = AgentStatus.CANCELLED
                    await self.send_event_to_user(InterruptedEvent(), config)
                    break

                await self.send_event_to_user(ThinkingStartEvent(), config)

                # Fallback: 依次尝试 model_list 中的每个模型
                resp = None
                errors: list[str] = []
                for i, model_ref in enumerate(model_list):
                    try:
                        resp = await self._stream_response(
                            task, system_message, config, tools, model_ref=model_ref
                        )
                        break  # 成功则跳出 fallback 循环
                    except Exception as e:
                        err_msg = f"{model_ref or '(默认)'}: {e}"
                        errors.append(err_msg)
                        if i < len(model_list) - 1:
                            # 还有 fallback 模型,通知用户并继续
                            await warn(f"模型 {err_msg},尝试 fallback 模型...", config)
                        else:
                            # 所有模型都失败了
                            detail = "\n  - ".join(errors)
                            await err(f"所有模型请求失败:\n  - {detail}", config)
                if resp is None:
                    break

                # LLM 推理期间压缩可能在后台运行,此处等待完成
                if compact_task is not None and not compact_task.done():
                    await compact_task
                compact_task = None

                tool_calls, tool_explains = await self._process_response(
                    resp, task, config
                )
                if not tool_calls:
                    content = await task.drain_user_queue(self, config)
                    if content:
                        continue
                    break

                # 死循环检测:连续相同工具调用则注入打破提示,跳过本次执行
                if await self._check_infinite_loop(
                    tool_calls, recent_tool_calls, task, config
                ):
                    continue

                if task.cancel_event.is_set():
                    task.status = AgentStatus.CANCELLED
                    await self.send_event_to_user(InterruptedEvent(), config)
                    break
                if await self._execute_tool_calls(
                    tool_calls,
                    name2tool,
                    config,
                    tools,
                    tool_explains,
                    loop_records=recent_tool_calls,
                    output_truncated=_is_output_truncated(resp),
                ):
                    break
                content = await task.drain_user_queue(self, config)

                # 工具执行完成后,启动后台压缩(与 LLM 推理并行)
                if compact_task is None or compact_task.done():
                    compact_task = asyncio.create_task(
                        task.session.maybe_compact(config)
                    )
                # ── 能量扣减:每轮工具调用结束,扩展工具能量-1,耗尽则卸载 ──
                evicted_energy = task.extended_mgr.drain_energy()
                if evicted_energy and tools is not None:
                    tools[:] = [t for t in tools if t.name not in set(evicted_energy)]
                    for name in evicted_energy:
                        name2tool.pop(name, None)
            # 内层循环结束,取消未完成的后台压缩任务
            if compact_task is not None and not compact_task.done():
                compact_task.cancel()
                try:
                    await compact_task
                except asyncio.CancelledError:
                    pass
            compact_task = None
            # ── goal check: 独立 judge 评估目标是否达成 ──
            goal_mgr = task.goal_manager
            if (
                goal_mgr
                and goal_mgr.active
                and not config.is_sub
                and not task.cancel_event.is_set()
            ):
                from uniclaw.tools.todolist.goal import GoalStatus, evaluate_goal

                conversation = task.session.get_recent_text(max_chars=8000)
                status, reason = await evaluate_goal(
                    goal_mgr.goal, conversation, config
                )
                # status = GoalStatus.NOT_ACHIEVED
                # reason = "test"
                if status == GoalStatus.ACHIEVED:
                    await info(f"目标已达成: {goal_mgr.goal} | 原因: {reason}", config)
                    goal_mgr.clear_goal()
                elif status == GoalStatus.WAITING:
                    await info(
                        f"等待后台任务: {goal_mgr.goal} | 原因: {reason}", config
                    )
                elif goal_mgr.check_reentry():
                    # NOT_ACHIEVED 且未超过重入次数
                    goal_mgr.increment_reentry()
                    msg = (
                        f"{SYSTEM_PREFIX}目标尚未达成,请继续工作。\n"
                        f"目标: {goal_mgr.goal}\n"
                        f"原因: {reason}"
                    )
                    task.user_queue.put_nowait(msg)
                    content = await task.drain_user_queue(self, config)
                    continue
                # else: 超过最大重入次数,允许退出
            # ── overseer check: TodoList 未完成项 ──
            todo = task.todolist
            incomplete = todo.get_incomplete() if todo else []
            if (
                todo
                and todo.overseer.active
                and not config.is_sub
                and not task.cancel_event.is_set()
                and incomplete
            ):
                msg = f"{SYSTEM_PREFIX}还有以下任务未完成,请继续:\n" + "\n".join(
                    f"- {item}" for item in incomplete
                )
                msg += f"\n\n请查看TodoList当前任务列表并继续完成剩余任务。如需与用户交流,请使用 {AskUserQuestion.name} 工具。"
                task.user_queue.put_nowait(msg)
                content = await task.drain_user_queue(self, config)
                continue
            else:
                break

        await self._run_cleanup(task, config)
        return
