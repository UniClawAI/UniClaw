import base64
import mimetypes
import asyncio
import threading
from pathlib import Path

from uniclaw.agent import MultiAgent
from uniclaw.utils.constants import SYSTEM_PREFIX
from uniclaw.utils.debug import heartbeat
from uniclaw.commands import handle_slash, COMMANDS, COMMAND_SUBCOMMANDS
from uniclaw.tools.fs import Edit, Write
from uniclaw.tools.media import IMAGE_EXTENSIONS, AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from uniclaw.utils.logger import get_logger

_COMMANDS_LIST = list(COMMANDS.keys())
from uniclaw.compaction import get_context_limit
from uniclaw.config import Permissions, AppConfig
from uniclaw.console.ui import C, ok
from uniclaw.console.output_renderer import OutputRenderer
from uniclaw.console.dialog import DialogManager
from uniclaw.console.session_panel import SessionPanel
from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.shell import Bash
from uniclaw.agent import (
    AgentTask,
    ThinkingStartEvent,
    ThinkingChunkEvent,
    TextChunkEvent,
    AssistantEvent,
    ToolPreparingEvent,
    ToolStartEvent,
    ToolEvent,
    EndEvent,
    PermissionRequestEvent,
    UserEvent,
    InterruptedEvent,
    SlashCommandEvent,
    ShellCommandEvent,
    CheckpointStartEvent,
    CheckpointEndEvent,
)
from uniclaw.utils.message import MessageRole, extract_text

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion, ConditionalCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.filters import Condition
from uniclaw.utils.format import format_args_for_display


class MouseScrollableFormattedTextControl(FormattedTextControl):
    def __init__(self, *args, on_scroll_up=None, on_scroll_down=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_scroll_up = on_scroll_up
        self._on_scroll_down = on_scroll_down

    def mouse_handler(self, mouse_event):
        if mouse_event.event_type == MouseEventType.SCROLL_UP and self._on_scroll_up:
            self._on_scroll_up()
            return None
        if (
            mouse_event.event_type == MouseEventType.SCROLL_DOWN
            and self._on_scroll_down
        ):
            self._on_scroll_down()
            return None
        return super().mouse_handler(mouse_event)


# ── 常量 ──────────────────────────────────────────────────────

_PERMISSION_CYCLE = [
    Permissions.AUTO,
    Permissions.MANUAL,
    Permissions.ACCEPT_ALL,
    Permissions.PLAN,
]


# ── 独立工具 ──────────────────────────────────────────────────


class _CommandCompleter(Completer):
    def __init__(self, get_task=None):
        self._get_task = get_task
        self._skills_cache: list = []
        self._skills_cache_time: float = 0

    def _load_skills(self):
        """加载 skills,带 5 秒缓存避免频繁磁盘 IO。"""
        import time

        now = time.time()
        if now - self._skills_cache_time < 5:
            return self._skills_cache
        task = self._get_task() if self._get_task else None
        root_dir = task.session.root_dir if task else None
        try:
            from uniclaw.tools.skill.loader import load_skills

            self._skills_cache = load_skills(root_dir)
        except Exception as e:
            get_logger("run", root_dir).debug("Failed to load skills: %s", e)
            self._skills_cache = []
        self._skills_cache_time = now
        return self._skills_cache

    def get_completions(self, document, _complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        # 解析命令和参数
        parts = text[1:].split(None, 1)
        cmd = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        # 如果没有输入空格,补全命令名
        if " " not in text[1:]:
            # 补全内置命令
            for c in _COMMANDS_LIST:
                if c.startswith(cmd):
                    yield Completion(f"/{c}", start_position=-len(text))
            # 补全 skill 触发器
            for skill in self._load_skills():
                for trigger in skill.triggers:
                    trigger_name = trigger.lstrip("/")
                    if trigger_name.startswith(cmd) and trigger_name != cmd:
                        desc = skill.description or skill.name
                        yield Completion(
                            f"/{trigger_name}",
                            start_position=-len(text),
                            display_meta=desc,
                        )
        else:
            # 如果输入了空格,补全子命令
            if cmd in COMMAND_SUBCOMMANDS:
                for subcmd in COMMAND_SUBCOMMANDS[cmd]:
                    if subcmd.startswith(args):
                        yield Completion(
                            f"/{cmd} {subcmd}",
                            start_position=-len(text),
                        )


class _FileCompleter(Completer):
    """@文件名补全器"""

    def __init__(self, get_task):
        self._get_task = get_task

    def get_completions(self, document, _complete_event):
        text = document.text_before_cursor

        # 查找最后一个 @ 符号的位置
        at_index = text.rfind("@")
        if at_index == -1:
            return

        # 获取 @ 后面的文本作为文件名前缀
        prefix = text[at_index + 1 :]

        task = self._get_task()
        if not task:
            return

        root_dir = task.session.root_dir
        if root_dir is None:
            return

        # 支持路径分隔符,处理子目录
        if "/" in prefix or "\\" in prefix:
            # 分离目录和文件名部分
            parts = prefix.replace("\\", "/").rsplit("/", 1)
            dir_part = parts[0]
            file_prefix = parts[1] if len(parts) > 1 else ""
            search_dir = root_dir / dir_part
        else:
            file_prefix = prefix
            search_dir = root_dir

        # 搜索匹配的文件
        try:
            for item in sorted(search_dir.iterdir()):
                if item.name.startswith(file_prefix) and not item.name.startswith("."):
                    # 构建补全文本
                    if "/" in prefix or "\\" in prefix:
                        completion_text = f"{dir_part}/{item.name}"
                    else:
                        completion_text = item.name

                    yield Completion(
                        text=completion_text,
                        start_position=-len(prefix),
                        display=item.name,
                        display_meta=(
                            "目录"
                            if item.is_dir()
                            else f"{item.stat().st_size:,} bytes"
                        ),
                    )
        except (OSError, PermissionError):
            pass


class _UniClawCompleter(Completer):
    """UniClaw 综合补全器"""

    def __init__(self, get_task):
        self._command_completer = _CommandCompleter(get_task)
        self._file_completer = _FileCompleter(get_task)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # 优先处理 / 命令补全
        if text.startswith("/"):
            yield from self._command_completer.get_completions(document, complete_event)
            return

        # 处理 @ 文件补全
        if "@" in text:
            yield from self._file_completer.get_completions(document, complete_event)


def _build_user_message(text: str):
    """检测用户输入中的图片/音频路径,构造多模态内容或纯文本。"""
    parts = text.split()
    content_blocks = []
    has_media = False

    for part in parts:
        p = Path(part)
        if p.exists() and p.suffix.lower() in IMAGE_EXTENSIONS:
            try:
                mime = mimetypes.guess_type(str(p))[0] or "image/png"
                data = base64.b64encode(p.read_bytes()).decode()
                content_blocks.append({"type": "text", "text": part})
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{data}"},
                    }
                )
                has_media = True
            except Exception as e:
                get_logger("console.run", Path.cwd()).warning(
                    "Failed to encode image %s: %s", part, e
                )
                content_blocks.append({"type": "text", "text": part})
        elif p.exists() and p.suffix.lower() in AUDIO_EXTENSIONS:
            try:
                mime = mimetypes.guess_type(str(p))[0] or "audio/mpeg"
                data = base64.b64encode(p.read_bytes()).decode()
                content_blocks.append({"type": "text", "text": part})
                content_blocks.append(
                    {
                        "type": "input_audio",
                        "input_audio": {"data": f"data:{mime};base64,{data}"},
                    }
                )
                has_media = True
            except Exception as e:
                get_logger("console.run", Path.cwd()).warning(
                    "Failed to encode audio %s: %s", part, e
                )
                content_blocks.append({"type": "text", "text": part})
        elif p.exists() and p.suffix.lower() in VIDEO_EXTENSIONS:
            try:
                mime = mimetypes.guess_type(str(p))[0] or "video/mp4"
                data = base64.b64encode(p.read_bytes()).decode()
                content_blocks.append({"type": "text", "text": part})
                content_blocks.append(
                    {
                        "type": "video_url",
                        "video_url": {"url": f"data:{mime};base64,{data}"},
                        "fps": 2,
                        "media_resolution": "default",
                    }
                )
                has_media = True
            except Exception as e:
                get_logger("console.run", Path.cwd()).warning(
                    "Failed to encode video %s: %s", part, e
                )
                content_blocks.append({"type": "text", "text": part})
        else:
            content_blocks.append({"type": "text", "text": part})

    if not has_media:
        return text

    merged = []
    for block in content_blocks:
        if (
            block["type"] == "text"
            and merged
            and merged[len(merged) - 1]["type"] == "text"
        ):
            merged[len(merged) - 1]["text"] += " " + block["text"]
        else:
            merged.append(block)

    return merged


async def token_usage_rate(task: AgentTask, config: AppConfig) -> float:
    model = config.model_name[0] if config.model_name else ""
    used = task.session.estimate_tokens(model)
    limit = await get_context_limit(model)
    pct = used / limit * 100 if limit else 0
    return pct


async def ask_permission_interactive(
    desc: str,
    config: AppConfig,
    tool_call: dict = None,
    explanation: str = "",
    agent_name: str = "",
):
    tui: TUIApp | None = TUIApp.get_instance()
    if not tui:
        return "无 TUI 实例"

    if explanation:
        # LLM 已提供安全分析,适用于所有工具
        desc = f"{desc}\n\n{explanation}"
    elif tool_call and tool_call.get("name") == Bash.name:
        # 降级:LLM 未提供解释时,使用 bash_desc 仅分析 Bash 命令
        from uniclaw.tools.security import bash_desc

        command = tool_call.get("args", {}).get("command", "")
        if command:
            wait_id = config.spinner.start("Analyzing...")
            bash_info = bash_desc(command, config)
            config.spinner.stop(wait_id=wait_id)
            if bash_info:
                desc = f"{desc}\n\n{bash_info}"

    if tool_call and tool_call.get("name") == Bash.name:
        from uniclaw.tools.security import extract_bash_prefix

        _cmd = tool_call.get("args", {}).get("command", "")
        _pattern = extract_bash_prefix(_cmd)
        _allow_label = f"始终允许 '{_pattern}'"
    elif tool_call:
        _allow_label = f"始终允许 '{tool_call.get('name', '')}'"
    else:
        _allow_label = "全部接受"

    agent_label = f" [子代理: {agent_name}]" if agent_name else ""
    prompt_text = f"⚠️{agent_label} 需要您的授权:\n{desc}\n\ny 同意 | a {_allow_label} | 其他输入为拒绝理由"
    title = f"权限确认 - {agent_name}" if agent_name else "权限确认"
    text = (await tui.tui_input(prompt_text, title=title)).strip()

    if text.lower() == "a":
        from uniclaw.tools.security import save_always_allow_rule

        tool_name = tool_call.get("name", "") if tool_call else ""
        tool_args = tool_call.get("args", {}) if tool_call else {}
        saved = save_always_allow_rule(tool_name, tool_args, config.root_dir)
        if saved:
            await ok(f"✅ 已保存规则: 始终允许 {saved}", config)
        return True

    if text.lower() == "y":
        return True
    return text if text else "用户拒绝执行"


# ── TUIApp ────────────────────────────────────────────────────


class TUIApp:
    """prompt_toolkit 全屏 TUI 应用封装(单例模式)。"""

    _instance: "TUIApp | None" = None

    def __new__(cls, config: AppConfig | None = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config: AppConfig | None = None):
        # 防止重复初始化
        if self._initialized:
            return

        if config is None:
            raise ValueError("首次创建 TUIApp 实例时必须提供 config 参数")

        self.config = config
        self._initialized = True

        # 输出管理(委托给 OutputRenderer)
        self.output = OutputRenderer(config, tui_ref=self)

        # 滚动
        self.command_history: list[str] = []
        self.history_index: int | None = None
        self.history_pending_text: str = ""
        # 会话面板(委托给 SessionPanel)
        self.session_panel = SessionPanel(tui_ref=self)
        self.active_task: AgentTask | None = None

        # 对话框(委托给 DialogManager)
        self.dialog = DialogManager(tui_ref=self)

        # ESC中断:跟踪当前运行的agent任务
        self.current_task: AgentTask | None = None

        # token 使用率
        self._token_pct: float = 0

        # prompt_toolkit 引用
        self.app: Application | None = None
        self.main_input_buffer: Buffer | None = None
        self.main_input_win: Window | None = None

        # 事件循环引用(用于线程安全的焦点切换)
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def get_instance(cls) -> "TUIApp | None":
        """获取 TUIApp 单例实例。"""
        return cls._instance

    def _run_on_ui_thread(self, callback, wait: bool = False, timeout: float = 1.0):
        """Run a small UI mutation on the prompt_toolkit event loop."""
        if not self.app:
            return
        loop = self._loop
        if loop and loop.is_running():
            if threading.current_thread() is threading.main_thread():
                callback()
                return
            done = threading.Event()

            def _runner():
                try:
                    callback()
                finally:
                    done.set()

            loop.call_soon_threadsafe(_runner)
            if wait:
                done.wait(timeout)
        else:
            callback()

    def _focus_window(self, window: Window | None):
        if not window or not self.app:
            return
        try:
            self.app.layout.focus(window)
        except Exception as e:
            if self.current_task:
                get_logger("run", self.current_task.session.root_dir).debug(
                    "Failed to focus prompt_toolkit window: %s", e, exc_info=True
                )
        self.app.invalidate()

    def _schedule_focus(self, window: Window | None, wait: bool = False):
        """Schedule focus on a prompt_toolkit window."""
        self._run_on_ui_thread(lambda: self._focus_window(window), wait=wait)

    # ── 输出管理(委托给 OutputRenderer)────────────────────

    def clear(self):
        self.output.clear()

    def print(
        self,
        text: str | list[tuple[str, str]],
        style: str = "",
        *,
        verbose: bool = False,
        normal: bool = False,
    ):
        self.output.print(text, style, verbose=verbose, normal=normal)

    def print_verbose(self, text: str | list[tuple[str, str]], style: str = "fg:gray"):
        self.output.print_verbose(text, style)

    def print_normal(self, text: str | list[tuple[str, str]], style: str = ""):
        self.output.print_normal(text, style)

    def print_styled(self, text: str | list[tuple[str, str]], style: str):
        self.output.print_styled(text, style)

    def replay_messages(self, messages: list[dict]):
        """用 drain_events 的风格渲染历史消息到输出区域。"""
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == MessageRole.SYSTEM:
                self.print_normal(f"{SYSTEM_PREFIX} {content[:200]}", "fg:gray")
            elif role == MessageRole.USER:
                # 用户消息:处理多模态内容
                content = extract_text(content, separator="\n")
                if content:
                    self.print(f"\n👤 {content}", style="fg:white")
            elif role == MessageRole.ASSISTANT:
                # 助手消息:文本内容
                content = extract_text(content, separator="\n")
                if content:
                    self.print(f"\n{content}")
                # 用量和模型信息
                usage = msg.get("usage", {})
                in_tokens = (
                    usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
                )
                out_tokens = (
                    usage.get("output_tokens", 0) if isinstance(usage, dict) else 0
                )
                model_name = msg.get("model_name", "")
                if in_tokens or out_tokens:
                    self.print_verbose(f"   Token: {in_tokens}→{out_tokens}")
                if model_name:
                    self.print_verbose(f"   模型: {model_name}")
                # 工具调用
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "unknown")
                    args_str = fn.get("arguments", "{}")
                    try:
                        import json

                        args = (
                            json.loads(args_str)
                            if isinstance(args_str, str)
                            else args_str
                        )
                    except Exception as e:
                        get_logger("run", self.config.root_dir).debug(
                            "Failed to parse tool args JSON for %s: %s", name, e
                        )
                        args = {}
                    args_display = format_args_for_display(args)
                    if args_display:
                        self.print_verbose(f"🔧 {name}({args_display})")
                    else:
                        self.print_verbose(f"🔧 {name}")
            elif role == MessageRole.TOOL:
                # 工具结果:显示名称和预览
                tool_name = msg.get("name", "")
                if isinstance(content, str):
                    preview = content.split("\n", 1)[0]
                    if len(preview) > 100 or len(content) > len(preview):
                        preview = preview[:100] + "..."
                    self.print_normal(f"🔧 {tool_name}: {preview}", "fg:gray")
        self.app.invalidate()

    # ── 属性委托(测试兼容)──────────────────────────────────

    @property
    def output_lines(self):
        return self.output.output_lines

    @output_lines.setter
    def output_lines(self, v):
        self.output.output_lines = v

    @property
    def verbose_indices(self):
        return self.output.verbose_indices

    @verbose_indices.setter
    def verbose_indices(self, v):
        self.output.verbose_indices = v

    @property
    def normal_indices(self):
        return self.output.normal_indices

    @normal_indices.setter
    def normal_indices(self, v):
        self.output.normal_indices = v

    @property
    def scroll_offset(self):
        return self.output.scroll_offset

    @scroll_offset.setter
    def scroll_offset(self, v):
        self.output.scroll_offset = v

    @property
    def _sep_height(self):
        return self.output._sep_height

    @_sep_height.setter
    def _sep_height(self, v):
        self.output._sep_height = v

    @property
    def _todo_chrome(self):
        return self.output._todo_chrome

    @_todo_chrome.setter
    def _todo_chrome(self, v):
        self.output._todo_chrome = v

    @property
    def _chrome_height(self):
        return self.output._chrome_height

    @_chrome_height.setter
    def _chrome_height(self, v):
        self.output._chrome_height = v

    # ── 会话面板(委托给 SessionPanel)──────────────────

    def refresh_session_items(self):
        self.session_panel.refresh()

    def _scroll_sessions(self, delta: int):
        self.session_panel.scroll(delta)

    def _ensure_selected_session_visible(self):
        self.session_panel.ensure_selected_visible()

    def _get_session_text(self):
        return self.session_panel.get_text()

    def load_selected_session(self):
        self.session_panel.load_selected()

    # 属性委托(测试兼容)
    @property
    def session_items(self):
        return self.session_panel.items

    @session_items.setter
    def session_items(self, v):
        self.session_panel.items = v

    @property
    def session_selected_index(self):
        return self.session_panel.selected_index

    @session_selected_index.setter
    def session_selected_index(self, v):
        self.session_panel.selected_index = v

    @property
    def session_scroll_offset(self):
        return self.session_panel.scroll_offset

    @session_scroll_offset.setter
    def session_scroll_offset(self, v):
        self.session_panel.scroll_offset = v

    @property
    def session_panel_focused(self):
        return self.session_panel.focused

    @session_panel_focused.setter
    def session_panel_focused(self, v):
        self.session_panel.focused = v

    @property
    def session_panel_visible(self):
        return self.session_panel.visible

    @session_panel_visible.setter
    def session_panel_visible(self, v):
        self.session_panel.visible = v

    # ── 对话框(委托给 DialogManager)────────────────────────

    async def tui_input(self, prompt: str, title: str = "输入") -> str:
        return await self.dialog.tui_input(
            prompt, title, self.config, self.main_input_buffer, self.main_input_win
        )

    async def tui_multi_input(
        self, questions: list[dict], title: str = "请选择"
    ) -> str:
        return await self.dialog.tui_multi_input(
            questions, title, self.config, self.main_input_buffer, self.main_input_win
        )

    # 静态方法别名(兼容外部调用)
    ansi_fragments = DialogManager.ansi_fragments
    diff_fragments = DialogManager.diff_fragments
    prompt_fragments = DialogManager.prompt_fragments

    # ── 输出渲染 ──────────────────────────────────────────────

    # ── 渲染方法(委托给 OutputRenderer)────────────────────

    def _main_output_width(self) -> int:
        return self.output.main_output_width()

    def _get_output_text(self):
        return self.output.get_output_text()

    def _count_visible_lines(self) -> int:
        return self.output.count_visible_lines()

    # 类方法别名(测试兼容)
    _count_fragments_lines = OutputRenderer.count_fragments_lines
    _split_fragments_lines = OutputRenderer.split_fragments_lines
    _char_display_width = OutputRenderer.char_display_width
    _wrap_fragment_line = OutputRenderer.wrap_fragment_line
    _wrap_fragment_lines = OutputRenderer.wrap_fragment_lines

    # ── 构建 Application ──────────────────────────────────────

    def build_app(self, on_submit) -> Application:
        """构建 prompt_toolkit Application: 上方滚动输出 + 下方固定输入框。"""
        config = self.config

        def _scroll_main_output_up():
            self.session_panel_focused = False
            self.scroll_offset += 1
            if self.app:
                self.app.invalidate()

        def _scroll_main_output_down():
            self.session_panel_focused = False
            self.scroll_offset = max(0, self.scroll_offset - 1)
            if self.app:
                self.app.invalidate()

        output_control = MouseScrollableFormattedTextControl(
            text=self._get_output_text,
            on_scroll_up=_scroll_main_output_up,
            on_scroll_down=_scroll_main_output_down,
        )

        output_window = Window(
            content=output_control,
            always_hide_cursor=True,
            wrap_lines=False,
        )

        def _get_prompt():
            pct = self._token_pct
            rd = self.config.current_agent.session.root_dir
            if rd is None:
                raise RuntimeError("Console 模式下 root_dir 不能为 None")
            return HTML(f"<b>[{rd.name}] {pct:.0f}% </b>»")

        def _accept_input(buf):
            text = buf.text
            buf.reset()
            if self.dialog.active and self.dialog.event is not None:
                self.dialog.result = text
                self.dialog.event.set()
                return True
            if text.strip():
                if not self.command_history or self.command_history[-1] != text:
                    self.command_history.append(text)
                self.history_index = None
                self.history_pending_text = ""
                on_submit(text)
            return True

        input_buffer = Buffer(
            completer=ConditionalCompleter(
                _UniClawCompleter(lambda: self.current_task),
                filter=Condition(lambda: not self.dialog.active),
            ),
            accept_handler=_accept_input,
            complete_while_typing=True,
            multiline=False,
        )
        self.main_input_buffer = input_buffer

        input_window = Window(
            content=BufferControl(buffer=input_buffer),
            height=2,
            dont_extend_height=False,
            get_line_prefix=lambda _n, _w: _get_prompt(),
        )
        self.main_input_win = input_window

        def _get_status_bar():
            mode = config.permission_mode
            label = mode.value if isinstance(mode, Permissions) else str(mode)
            parts = [
                f" <ansigreen>permission: {label}</ansigreen>",
                f"  <ansidim>(Shift+Tab 切换)</ansidim>",
            ]
            if config.computer_use_enabled:
                parts.append("  <ansiyellow>ComputerUse: ON</ansiyellow>")
                parts.append("  <ansidim>(Ctrl+U 紧急停止)</ansidim>")
            return HTML("".join(parts))

        status_bar = Window(
            content=FormattedTextControl(text=_get_status_bar),
            height=1,
            dont_extend_height=True,
            style="class:statusbar",
        )

        # 布局元素高度 → chrome 计算
        _sep_h = 1
        _input_h = 2
        _status_h = 1
        _frame_border_h = 1  # Frame 上下边框各 1 行
        _todo_chrome = 3  # todo 窗口自身的 chrome(标题栏 + 上下边框)
        self._todo_chrome = _todo_chrome
        self._sep_height = _sep_h
        self._chrome_height = _sep_h + _input_h + _sep_h + _status_h
        self.dialog.chrome_height = (
            _frame_border_h + _sep_h + _input_h + _frame_border_h + _status_h
        )

        # todolist 显示区域
        def _get_todo_text():
            todo = self.config.current_agent.todolist
            if todo is None or todo.is_empty():
                return [("", "")]
            from uniclaw.console.ui import tui_clr, C

            return tui_clr(todo.get_list(), C.CYAN)

        def _todo_height():
            todo = self.config.current_agent.todolist
            if todo is None or todo.is_empty():
                return 0
            return len(todo.items) + _todo_chrome

        todo_window = Window(
            content=FormattedTextControl(text=_get_todo_text),
            height=_todo_height,
            dont_extend_height=True,
            style="class:todolist",
        )

        def _is_todo_empty():
            todo = self.config.current_agent.todolist
            return todo is None or todo.is_empty()

        _is_todo_empty = Condition(_is_todo_empty)

        main_content = HSplit(
            [
                output_window,
                todo_window,
                Window(height=_sep_h, char="─", style="class:separator"),
                ConditionalContainer(
                    content=input_window,
                    filter=Condition(lambda: not self.dialog.active),
                ),
                Window(height=_sep_h, char="─", style="class:separator"),
                status_bar,
            ]
        )
        # 会话面板(委托给 SessionPanel)
        conv_frame, conv_sep = self.session_panel.build_layout()

        body_content = VSplit(
            [
                conv_frame,
                conv_sep,
                main_content,
            ]
        )

        # 对话框(委托给 DialogManager)
        dialog_float, dialog_input_win = self.dialog.build_float(
            input_buffer, input_window
        )
        self.dialog.buffer = input_buffer

        body = FloatContainer(
            content=body_content,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=8, scroll_offset=1),
                ),
                dialog_float,
            ],
        )

        # ── 快捷键 ────────────────────────────────────────────

        bindings = KeyBindings()

        @bindings.add("s-tab", filter=Condition(lambda: not self.dialog.active))
        def _toggle_permission(event):
            cur = config.permission_mode
            if isinstance(cur, str):
                cur = Permissions(cur)
            idx = _PERMISSION_CYCLE.index(cur) if cur in _PERMISSION_CYCLE else 0
            config.permission_mode = _PERMISSION_CYCLE[
                (idx + 1) % len(_PERMISSION_CYCLE)
            ]
            event.app.invalidate()

        @bindings.add("f2")
        def _toggle_verbose(event):
            config.verbose = not config.verbose
            self.scroll_offset = 0
            event.app.invalidate()

        @bindings.add("f3")
        def _toggle_session_panel(event):
            self.session_panel_visible = not self.session_panel_visible
            if not self.session_panel_visible and self.session_panel_focused:
                self.session_panel_focused = False
            event.app.invalidate()

        @bindings.add(
            "escape",
            filter=Condition(
                lambda: not (
                    self.dialog.active
                    and self.dialog.multi_mode
                    and self.dialog.other_active
                )
            ),
        )
        def _clear_input(event):
            if self.dialog.active and self.dialog.event is not None:
                self.dialog.result = "User cancelled permission request"
                input_buffer.reset()
                self.dialog.event.set()
            elif input_buffer.text:
                input_buffer.text = ""
            else:
                if self.current_task is not None:
                    self._loop.call_soon_threadsafe(self.current_task.cancel_event.set)
            self.history_index = None
            self.history_pending_text = ""

        _no_completion = Condition(lambda: not input_buffer.complete_state)
        _is_normal = Condition(lambda: not self.dialog.active)
        _main_focused = Condition(
            lambda: not self.session_panel_focused and not self.dialog.active
        )
        _dialog_not_focused = Condition(
            lambda: self.dialog.active
            and not self.dialog.multi_mode
            and self.app is not None
            and self.app.layout.current_window is not dialog_input_win
        )
        _main_not_focused = Condition(
            lambda: not self.dialog.active
            and self.app is not None
            and self.app.layout.current_window is not input_window
        )

        def _key_data(event) -> str:
            key_press = event.key_sequence[-1] if event.key_sequence else None
            return key_press.data if key_press is not None else ""

        def _insert_key_or_submit(event, buffer: Buffer, submit):
            data = _key_data(event)
            if data in ("\r", "\n"):
                submit()
            elif data in ("\x08", "\x7f"):
                buffer.delete_before_cursor(1)
            elif data and data.isprintable():
                buffer.insert_text(data)
            event.app.invalidate()

        @bindings.add("<any>", filter=_dialog_not_focused, eager=True)
        def _redirect_dialog_input(event):
            self._focus_window(dialog_input_win)

            def _submit_dialog():
                self.dialog.result = input_buffer.text
                input_buffer.reset()
                if self.dialog.event:
                    self.dialog.event.set()

            _insert_key_or_submit(event, input_buffer, _submit_dialog)

        @bindings.add("<any>", filter=_main_not_focused, eager=True)
        def _redirect_main_input(event):
            self._focus_window(input_window)
            _insert_key_or_submit(
                event, input_buffer, lambda: _accept_input(input_buffer)
            )

        @bindings.add("c-up", filter=_no_completion & _is_normal, eager=True)
        def _history_previous(event):
            if not self.command_history:
                return
            if self.history_index is None:
                self.history_pending_text = input_buffer.text
                self.history_index = len(self.command_history) - 1
            else:
                self.history_index = max(0, self.history_index - 1)
            input_buffer.text = self.command_history[self.history_index]
            input_buffer.cursor_position = len(input_buffer.text)

        @bindings.add("c-down", filter=_no_completion & _is_normal, eager=True)
        def _history_next(event):
            if self.history_index is None:
                return
            if self.history_index >= len(self.command_history) - 1:
                input_buffer.text = self.history_pending_text
                self.history_index = None
                self.history_pending_text = ""
            else:
                self.history_index += 1
                input_buffer.text = self.command_history[self.history_index]
            input_buffer.cursor_position = len(input_buffer.text)

        # 会话面板快捷键(委托给 SessionPanel)
        self.session_panel.bind_keys(bindings, _no_completion)

        @bindings.add("up", filter=_no_completion & _main_focused, eager=True)
        def _scroll_up(event):
            self.scroll_offset += 1
            event.app.invalidate()

        @bindings.add("down", filter=_no_completion & _main_focused, eager=True)
        def _scroll_down(event):
            self.scroll_offset = max(0, self.scroll_offset - 1)
            event.app.invalidate()

        # 对话框滚动快捷键(委托给 DialogManager)
        self.dialog.bind_keys(bindings, input_buffer)

        # @bindings.add("tab")
        # def _complete(event):
        #     buffer = event.app.current_buffer
        #     if buffer.complete_state:
        #         buffer.complete_next()
        #     else:
        #         buffer.start_completion(select_first=False)

        app = Application(
            layout=Layout(body, focused_element=input_window),
            key_bindings=bindings,
            full_screen=True,
            mouse_support=Condition(
                lambda: self.session_panel_focused or self.dialog.active
            ),
            enable_page_navigation_bindings=False,
        )

        config.spinner.set_invalidate_callback(app.invalidate)

        async def _spinner_task():
            while True:
                await asyncio.sleep(0.1)
                config.spinner.update_frame()

        app.create_background_task(_spinner_task())
        return app

    # ── 事件处理 ──────────────────────────────────────────────

    async def drain_events(self, agent_task: AgentTask):
        """从事件队列读取并更新输出区域,直到 EndEvent(depth=0)。"""
        from uniclaw.console.ui import C, tui_clr

        thinking_stream = False
        text_stream = False
        event_queue = agent_task.event_queue

        while True:
            try:
                queued_task, event = await asyncio.wait_for(
                    event_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                if agent_task.future is not None and agent_task.future.done():
                    self.config.spinner.stop(wait_id=agent_task.id)
                    exc = agent_task.future.exception()
                    if exc is not None:
                        import traceback

                        error_traceback = traceback.format_exc()
                        get_logger("run", agent_task.session.root_dir).error(
                            error_traceback
                        )
                        self.print(f"\n❌ Agent 线程异常退出: {exc}")
                    else:
                        self.print("\n⚠️ Agent 已结束,但没有收到结束事件。")
                    break
                continue

            # 检查事件是否来自主 agent,如果不是则显示 agent 标识
            is_main_agent = queued_task is agent_task
            agent_prefix = "" if is_main_agent else f"[{queued_task.name}] "

            if isinstance(event, ThinkingStartEvent):
                self.config.spinner.start(
                    f"{agent_prefix}Thinking...", wait_id=queued_task.id
                )
            elif isinstance(event, ThinkingChunkEvent):
                if not thinking_stream:
                    self.print_verbose(f"{agent_prefix}💭 [Thinking]")
                    self.print_verbose("")
                    thinking_stream = True
                think = self.output_lines[-1]
                think[0] = (
                    think[0][0],
                    think[0][1] + event.content,
                )
                verbose = self.config.verbose
                if verbose:
                    self.config.spinner.stop(wait_id=queued_task.id)
                else:
                    self.config.spinner.start(
                        f"{agent_prefix}Thinking...", wait_id=queued_task.id
                    )
                self.app.invalidate()
            elif isinstance(event, TextChunkEvent) and event.content:
                self.config.spinner.stop(wait_id=queued_task.id)
                if not text_stream:
                    self.print(agent_prefix)
                thinking_stream = False
                text_stream = True
                self.output_lines[-1].append(("", event.content))
                self.app.invalidate()
                # 语音模式: 流式积累文本,按句子边界触发 TTS
                if (
                    self.config.voice_mode
                    and self.config.tts_model
                    and self.config.audio
                ):
                    from uniclaw.tools.tts.tts import tts_enqueue

                    tts_enqueue(queued_task.session.id, event.content, self.config)
            elif isinstance(event, AssistantEvent):
                self.config.spinner.stop(wait_id=queued_task.id)
                thinking_stream = False
                text_stream = False
                self.print_verbose(
                    f"{agent_prefix}   Token: {event.in_tokens}→{event.out_tokens}"
                )
                if event.tool_calls:
                    self.print_verbose(
                        f"{agent_prefix}   工具调用数量: {len(event.tool_calls)}"
                    )
                    for i, tc in enumerate(event.tool_calls, 1):
                        fn = tc.get("function", {})
                        name = fn.get("name", "unknown")
                        self.print_verbose(f"{agent_prefix}   工具 {i}: {name}")
                        args_str = fn.get("arguments", "")
                        if args_str:
                            self.print_verbose(f"{agent_prefix}      参数: {args_str}")
                self.print_verbose(f"{agent_prefix}   模型: {event.model_name}")
                # 语音模式: flush 剩余文本到 TTS 队列
                if (
                    self.config.voice_mode
                    and event.content
                    and self.config.tts_model
                    and self.config.audio
                ):
                    from uniclaw.tools.tts.tts import tts_flush

                    await tts_flush(queued_task.session.id, self.config)
            elif isinstance(event, ToolPreparingEvent):
                args_display = format_args_for_display(event.args, max_length=10)
                self.config.spinner.start(
                    f"{agent_prefix}🔄 '{event.name}({args_display})'...",
                    wait_id=queued_task.id,
                )
            elif isinstance(event, CheckpointStartEvent):
                self.config.spinner.start(
                    f"{agent_prefix}📸 创建检查点...",
                    wait_id=f"checkpoint_{queued_task.id}",
                )
            elif isinstance(event, CheckpointEndEvent):
                self.config.spinner.stop(wait_id=f"checkpoint_{queued_task.id}")
            elif isinstance(event, ToolStartEvent):
                self.config.spinner.stop(wait_id=queued_task.id)
                args_display = format_args_for_display(event.args)
                explain_hint = f" 💡{event.explain}" if event.explain else ""
                wait_id = event.tool_call_id
                if args_display:
                    self.config.spinner.start(
                        f"{agent_prefix}🔧 运行工具 {explain_hint}'{event.name}({args_display})'...",
                        wait_id=wait_id,
                    )
                else:
                    self.config.spinner.start(
                        f"{agent_prefix}🔧 运行工具 {explain_hint}'{event.name}'...",
                        wait_id=wait_id,
                    )
            elif isinstance(event, ToolEvent):
                wait_id = event.tool_call_id
                self.config.spinner.stop(wait_id=wait_id)
                # 构建工具调用显示文本:工具名 + 参数
                args_display = format_args_for_display(event.args)
                if args_display:
                    self.print(f"{agent_prefix}🔧 {event.name}({args_display})")
                else:
                    self.print(f"{agent_prefix}🔧 {event.name}")
                preview = event.content.split("\n", 1)[0]
                if len(preview) > 100 or len(event.content) > len(preview):
                    preview = preview[:100] + "..."
                self.print_normal(preview, "fg:gray")
                if event.name in (Edit.name, Write.name) and "---" in event.content:
                    diff_fragments = TUIApp.diff_fragments(event.content)
                    self.print_verbose(diff_fragments)
                else:
                    self.print_verbose(event.content)
            elif isinstance(event, UserEvent):
                # 显示用户输入消息(list 内容只显示文本部分,避免输出 base64)
                display = (
                    extract_text(event.content)
                    if isinstance(event.content, list)
                    else event.content
                )
                self.print(f"\n{agent_prefix}👤 {display}", style="fg:white")
            elif isinstance(event, PermissionRequestEvent):
                self.config.spinner.stop(wait_id=queued_task.id)
                event.content = await ask_permission_interactive(
                    event.description,
                    self.config,
                    event.tool_call,
                    event.explanation,
                    agent_name=event.agent_name,
                )
                event.return_event.set()
                continue
            elif isinstance(event, ShellCommandEvent):
                self.config.spinner.stop(wait_id=queued_task.id)
                self.print(f"  $ {event.command}")
                out = await Bash(event.command, tool_runtime=ToolRuntime(config=self.config))
                self.print(out)
                event.content = out
                event.return_event.set()
                continue
            elif isinstance(event, SlashCommandEvent):
                self.config.spinner.stop(wait_id=queued_task.id)
                slash_result = await handle_slash(event.command, self.config)
                if isinstance(slash_result, str):
                    self.print(slash_result)
                    event.content = slash_result
                else:
                    event.content = ""
                self.refresh_session_items()
                self.app.invalidate()
                event.return_event.set()
                continue
            elif isinstance(event, InterruptedEvent):
                self.config.spinner.stop(wait_id=queued_task.id)
                self.print(tui_clr(f"\n{agent_prefix}⏹️  {event.message}", C.YELLOW))
            elif isinstance(event, EndEvent):
                self.config.spinner.stop(wait_id=queued_task.id)
                if event.depth == 0:
                    self.refresh_session_items()
                    # save_session 可能较慢(取标题),延迟再刷一次
                    asyncio.create_task(self._delayed_refresh(10))
                    break
            else:
                self.print(f"⚠️ 未知事件: {type(event)}")
        self.print(tui_clr("." * 60, C.GRAY))

    async def _delayed_refresh(self, seconds: int):
        """延迟刷新会话列表(save_session 完成后)。"""
        await asyncio.sleep(seconds)
        self.refresh_session_items()

    # ── 事件循环 ──────────────────────────────────────────────

    @heartbeat(threshold=1.0)
    async def _run_async(self, initial_output: list[str] | None = None):
        self._loop = asyncio.get_running_loop()

        task = self.config.current_agent
        task.event_queue = asyncio.Queue()
        self.active_task = task
        self.refresh_session_items()
        multi_agent = MultiAgent.get_instance()
        multi_agent.loop = self._loop  # 保存主事件循环引用,scheduler 等跨线程场景使用

        # 异步初始化 MCP 工具
        from uniclaw.tools.mcp import MCPManager

        await MCPManager.get_instance().refresh()

        def on_submit(text: str):
            self._loop.call_soon_threadsafe(task.user_queue.put_nowait, text)
            self.app.invalidate()

        self.app = self.build_app(on_submit)

        if initial_output:
            for line in initial_output:
                self.print(line)

        app_task = asyncio.create_task(self.app.run_async())

        try:
            while True:
                result = await task.user_queue.get()
                user_input = (result or "").strip()

                # 新消息到来时,清除上一个 skill 的工具白名单
                from uniclaw.tools.skill.tools import clear_active_skill_tools

                clear_active_skill_tools()

                if not user_input:
                    continue
                self.config.current_agent.cancel_event.clear()
                if user_input.startswith("!"):
                    shell_cmd = user_input[1:].strip()
                    if shell_cmd:
                        self.print(f"  $ {shell_cmd}")
                        out = await Bash(shell_cmd, tool_runtime=ToolRuntime(config=self.config))
                        self.print(out)
                        task.session.add_message(
                            MessageRole.USER,
                            f"{SYSTEM_PREFIX}(用户执行Shell命令)\n$ {shell_cmd}\n{out}",
                        )
                    continue
                if user_input.startswith("/"):
                    slash_result = await handle_slash(user_input, self.config)
                    if isinstance(slash_result, str):
                        user_input = slash_result
                    elif slash_result:
                        self.refresh_session_items()
                        self.app.invalidate()
                        continue
                    else:
                        continue

                user_message = _build_user_message(user_input)
                self.current_task = task
                try:
                    agent_task = multi_agent.start_agent(
                        user_message, config=self.config
                    )
                    await self.drain_events(task)
                except Exception as e:
                    import traceback

                    error_traceback = traceback.format_exc()
                    get_logger("run", task.session.root_dir).error(error_traceback)
                    self.print(f"\n❌ 错误: {e}")
                finally:
                    self.current_task = None

                self.print("")
                self._token_pct = await token_usage_rate(task, self.config)
                self.app.invalidate()
        finally:
            if not app_task.done():
                self.app.exit()
            await app_task


# ── 模块级便捷接口(供 commands/ 导入)──────────────────────


async def tui_input(prompt: str, title: str = "输入") -> str:
    """模块级异步便捷函数,委托给当前 TUIApp 实例。"""
    instance = TUIApp.get_instance()
    if instance:
        return await instance.tui_input(prompt, title=title)
    return ""


async def tui_multi_input(questions: list[dict], title: str = "请选择") -> str:
    """模块级异步便捷函数,多问题 Tab 对话框。"""
    instance = TUIApp.get_instance()
    if instance:
        return await instance.tui_multi_input(questions, title=title)
    return ""


async def repl_run(config: AppConfig, initial_output: list[str] | None = None):
    """启动 REPL(兼容 launcher.py 调用)。"""
    tui = TUIApp(config)
    await tui._run_async(initial_output)
