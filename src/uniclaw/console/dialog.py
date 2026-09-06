import asyncio
import json
import re
import shutil
import threading

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Float
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.widgets import Frame
from prompt_toolkit.filters import Condition

from uniclaw.console.output_renderer import OutputRenderer, _get_display_width
from uniclaw.config import AppConfig

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class MultiQuestionTextControl(FormattedTextControl):
    """FormattedTextControl 子类,渲染多问题 Tab 对话框并支持鼠标点击选项。"""

    def __init__(self, dialog_manager):
        self._dm = dialog_manager
        super().__init__(text=self._get_fragments)

    def _get_fragments(self):
        dm = self._dm
        if not dm.multi_mode or not dm.questions:
            return [("", "")]
        from prompt_toolkit.mouse_events import MouseEventType

        fragments: list[tuple[str, str] | tuple[str, str, callable]] = []
        q = dm.questions[dm.current_tab]
        options = q.get("options", [])
        sel = dm.selections.get(dm.current_tab)
        other_idx = dm._get_other_idx()
        is_multi = dm._is_multi()

        # 多选时 sel 是列表,单选时是数字
        selected_set = set(sel) if is_multi and isinstance(sel, list) else ({sel} if sel is not None else set())

        def _click(idx):
            def _h(mouse_event):
                if mouse_event.event_type == MouseEventType.MOUSE_UP:
                    dm._select_option(idx)

            return _h

        # ── 竖排 Tab ──
        for i, qq in enumerate(dm.questions):
            q_text = qq.get("question", f"Q{i + 1}")
            click_tab = _click_tab(dm, i)
            is_q_multi = dm._is_multi(i)
            # Tab 显示提示:多选标记 [多选]
            multi_tag = " [多选]" if is_q_multi else ""
            if i == dm.current_tab:
                fragments.append(("", "  ◉ ", click_tab))
                fragments.append(("fg:ansigreen bold", q_text + multi_tag, click_tab))
            else:
                fragments.append(("", "  ○ ", click_tab))
                fragments.append(("dim", q_text + multi_tag, click_tab))
            fragments.append(("", "\n"))

        fragments.append(("class:separator", "─" * 50 + "\n"))

        # ── 问题标题 ──
        multi_hint = " (可多选)" if is_multi else ""
        fragments.append(("bold", f" {q.get('question', '')}{multi_hint}\n"))
        fragments.append(("", "\n"))

        # ── 选项(LLM 的"其他"跳过,由系统统一渲染) ──
        for j, opt in enumerate(options):
            if j == other_idx and other_idx < len(options):
                continue
            h = _click(j)
            is_selected = j in selected_set
            if is_multi:
                icon = "☑" if is_selected else "☐"
                style = "fg:ansigreen bold" if is_selected else ""
            else:
                icon = "●" if is_selected else "○"
                style = "fg:ansigreen bold" if is_selected else ""
            if is_selected:
                fragments.append(("", "  ", h))
                fragments.append((style, icon, h))
                fragments.append(("", f" {j + 1}. {opt}\n", h))
            else:
                fragments.append(("", "  ", h))
                fragments.append(("", icon, h))
                fragments.append(("", f" {j + 1}. {opt}\n", h))

        # ── "其他"选项 ──
        other_text = dm.other_texts.get(dm.current_tab, "")
        click_h = _click(other_idx)
        is_other_selected = other_idx in selected_set
        if is_other_selected and dm.other_active:
            fragments.append(("", "  "))
            if is_multi:
                fragments.append(("fg:ansiyellow bold", "☑"))
            else:
                fragments.append(("fg:ansiyellow bold", "●"))
            fragments.append(("", f" {other_idx + 1}. 其他: "))
            fragments.append(("fg:ansiyellow", f"[{other_text}█]\n"))
        elif is_other_selected:
            d = f" [{other_text}]" if other_text else ""
            fragments.append(("", "  ", click_h))
            if is_multi:
                fragments.append(("fg:ansiyellow bold", "☑", click_h))
            else:
                fragments.append(("fg:ansiyellow bold", "●", click_h))
            fragments.append(("", f" {other_idx + 1}. 其他:{d}\n", click_h))
        else:
            d = f" [{other_text}]" if other_text else ""
            fragments.append(("", "  ", click_h))
            if is_multi:
                fragments.append(("", "☐", click_h))
            else:
                fragments.append(("", "○", click_h))
            fragments.append(("", f" {other_idx + 1}. 其他:{d}\n", click_h))

        # ── 已选摘要 ──
        if selected_set:
            fragments.append(("", "\n"))
            selected_labels = []
            for idx in sorted(selected_set):
                if idx == other_idx:
                    ot = dm.other_texts.get(dm.current_tab, "").strip()
                    selected_labels.append(f"其他:{ot}" if ot else "其他")
                elif idx < len(options):
                    selected_labels.append(f"{idx + 1}. {options[idx]}")
            if selected_labels:
                fragments.append(("fg:ansigreen", f"  ◉ 已选: {', '.join(selected_labels)}\n"))

        fragments.append(("", "\n"))
        if is_multi:
            fragments.append(("dim", "  Tab 切换 | 数字键选中/取消 | Enter 提交\n"))
        else:
            fragments.append(("dim", "  Tab 切换 | 数字键选择 | Enter 提交\n"))
        return fragments


def _click_tab(dm, tab_idx):
    """Tab 点击 handler。"""
    from prompt_toolkit.mouse_events import MouseEventType

    def _h(mouse_event):
        if mouse_event.event_type == MouseEventType.MOUSE_UP:
            dm.current_tab = tab_idx
            dm.other_active = False
            dm._invalidate()

    return _h


class DialogManager:
    """管理 TUI 对话框:状态、渲染、输入等待、快捷键。"""

    def __init__(self, tui_ref):
        self._tui = tui_ref
        self.active: bool = False
        self.title: str = "输入"
        self.prompt: str = ""
        self.prompt_fragments_list: list[tuple[str, str]] = []
        self.event: threading.Event | None = None
        self.result: str | None = None
        self.scroll_offset: int = 0
        self.chrome_height: int = 6
        self.content_width: int = 20
        self.buffer: Buffer | None = None
        self.input_win: Window | None = None

        # 多问题 Tab 模式状态
        self.multi_mode: bool = False
        self.questions: list[dict] = []
        self.current_tab: int = 0
        self.selections: dict[int, int | list[int] | None] = {}  # 单选:int|None, 多选:list[int]
        self.other_texts: dict[int, str] = {}
        self.other_active: bool = False

    # ── 静态/类方法 ──────────────────────────────────────────

    @staticmethod
    def ansi_fragments(text: str) -> list[tuple[str, str]]:
        """将 ANSI 着色文本转为 prompt_toolkit 片段。"""
        from prompt_toolkit.formatted_text import ANSI

        ansi_obj = ANSI(text)
        fragments = list(ansi_obj.__pt_formatted_text__())
        merged: list[tuple[str, str]] = []
        for style, txt in fragments:
            if merged and merged[-1][0] == style:
                merged[-1] = (style, merged[-1][1] + txt)
            else:
                merged.append((style, txt))
        return merged

    @staticmethod
    def diff_fragments(diff_text: str) -> list[tuple[str, str]]:
        """Render a unified diff with prompt_toolkit styles."""
        fragments: list[tuple[str, str]] = []
        for line in diff_text.splitlines(keepends=True):
            line_body = line[:-1] if line.endswith("\n") else line
            newline = "\n" if line.endswith("\n") else ""
            if line_body.startswith(("---", "+++")):
                style = "dim"
            elif line_body.startswith("@@"):
                style = "fg:cyan"
            elif line_body.startswith("-"):
                style = "fg:red"
            elif line_body.startswith("+"):
                style = "fg:green"
            else:
                style = ""
            fragments.append((style, line_body + newline))
        return fragments or [("", "")]

    @classmethod
    def prompt_fragments(cls, text: str) -> list[tuple[str, str]]:
        """Render prompt text, preserving ANSI colors and coloring embedded diffs."""
        if "\x1b[" in text:
            return cls.ansi_fragments(text)
        if "--- " in text and "+++ " in text:
            return cls.diff_fragments(text)
        return [("", text)]

    # ── 多问题模式 ──────────────────────────────────────────

    @staticmethod
    def _normalize_option(opt) -> str:
        """将选项规范化为字符串。LLM 可能传 str 或 dict(如 {"text":"..."} )。"""
        if isinstance(opt, str):
            return opt
        if isinstance(opt, dict):
            # 尝试常见字段名
            for key in ("text", "label", "value", "name", "option"):
                if key in opt:
                    return str(opt[key])
            return str(opt)
        return str(opt)

    def set_questions(self, questions: list[dict]):
        """初始化多问题状态。"""
        self.multi_mode = True
        # 规范化:确保每个 question 的 options 都是字符串列表
        normalized = []
        for q in questions:
            nq = dict(q)
            nq["options"] = [self._normalize_option(o) for o in q.get("options", [])]
            normalized.append(nq)
        self.questions = normalized
        self.current_tab = 0
        # 多选问题初始化为空列表,单选初始化为 None
        self.selections = {i: ([] if q.get("multi", False) else None) for i, q in enumerate(normalized)}
        self.other_texts = {i: "" for i in range(len(questions))}
        self.other_active = False

    def _get_other_idx(self) -> int:
        """获取"其他"选项在选项列表中的索引(模糊匹配包含"其他"即可)。"""
        q = self.questions[self.current_tab]
        options = q.get("options", [])
        if options:
            last = options[-1].strip().lower()
            if any(kw in last for kw in ("其他", "other", "其它", "别的")):
                return len(options) - 1
        return len(options)

    def _is_multi(self, tab_idx: int | None = None) -> bool:
        """检查指定 Tab 是否为多选模式。"""
        idx = tab_idx if tab_idx is not None else self.current_tab
        q = self.questions[idx] if 0 <= idx < len(self.questions) else None
        return q is not None and q.get("multi", False) is True

    def _select_option(self, idx: int):
        """选择当前 Tab 的某个选项。"""
        other_idx = self._get_other_idx()
        is_multi = self._is_multi()

        if is_multi:
            # 多选:切换选中状态
            sel = self.selections.get(self.current_tab)
            if not isinstance(sel, list):
                sel = []
            if idx in sel:
                sel.remove(idx)
                # 取消选中"其他"时停用编辑模式
                if idx == other_idx:
                    self.other_active = False
            else:
                sel.append(idx)
                # 选中"其他"时激活编辑模式
                if idx == other_idx:
                    self.other_active = True
            self.selections[self.current_tab] = sel
        else:
            # 单选:直接设置
            if idx == other_idx:
                self.selections[self.current_tab] = idx
                self.other_active = True
            else:
                self.selections[self.current_tab] = idx
                self.other_active = False
        self._invalidate()

    def _switch_tab(self, delta: int):
        """切换 Tab。"""
        if not self.multi_mode:
            return
        n = len(self.questions)
        self.current_tab = (self.current_tab + delta) % n
        self.other_active = False
        self._invalidate()

    def _invalidate(self):
        """触发 TUI 重绘。"""
        tui = self._tui
        if tui and tui.app:
            tui.app.invalidate()

    def _all_selected(self) -> bool:
        """检查所有问题是否都已选择。"""
        for i in range(len(self.questions)):
            sel = self.selections.get(i)
            if self._is_multi(i):
                # 多选:需要是非空列表
                if not isinstance(sel, list) or len(sel) == 0:
                    return False
            else:
                # 单选:需要有值
                if sel is None:
                    return False
        return True

    def _collect_answers(self) -> str:
        """收集所有答案并返回 JSON 字符串。"""
        answers = {}
        for i, q in enumerate(self.questions):
            sel = self.selections.get(i)
            options = q.get("options", [])
            label = q.get("question", f"Q{i + 1}")
            other_idx = self._get_other_idx_for(i)
            is_multi = self._is_multi(i)

            if is_multi and isinstance(sel, list):
                # 多选:返回选项列表
                selected_opts = []
                for idx in sel:
                    if idx == other_idx:
                        other_text = self.other_texts.get(i, "").strip()
                        selected_opts.append(f"其他:{other_text}" if other_text else "其他")
                    elif idx < len(options):
                        selected_opts.append(options[idx])
                answers[label] = selected_opts if len(selected_opts) > 1 else (selected_opts[0] if selected_opts else "")
            elif sel is not None and sel == other_idx:
                # 单选"其他"
                other_text = self.other_texts.get(i, "").strip()
                if other_text:
                    answers[label] = f"其他:{other_text}"
                else:
                    answers[label] = "其他"
            elif sel is not None and sel < len(options):
                # 单选普通选项
                answers[label] = options[sel]
            else:
                answers[label] = ""
        return json.dumps(answers, ensure_ascii=False)

    def _get_other_idx_for(self, i: int) -> int:
        """获取第 i 题的"其他"选项索引(模糊匹配)。"""
        options = self.questions[i].get("options", [])
        if options:
            last = options[-1].strip().lower()
            if any(kw in last for kw in ("其他", "other", "其它", "别的")):
                return len(options) - 1
        return len(options)

    # ── 对话框宽度与文本渲染 ──────────────────────────────────

    def get_dialog_width(self) -> int:
        console_w = shutil.get_terminal_size((80, 24)).columns
        return max(40, min(self.content_width, console_w * 2 // 3))

    def get_dialog_text(self):
        if not self.active or not self.prompt:
            return [("", "")]
        all_lines = OutputRenderer.wrap_fragment_lines(
            OutputRenderer.split_fragments_lines(self.prompt_fragments_list),
            self.get_dialog_width(),
        )
        rows = shutil.get_terminal_size((80, 24)).lines
        visible_rows = max(5, rows - self.chrome_height)
        total = len(all_lines)
        end = max(0, total - self.scroll_offset)
        start = max(0, end - visible_rows)
        visible = all_lines[start:end]
        fragments: list[tuple[str, str]] = []
        for i, line_fragments in enumerate(visible):
            if i > 0:
                fragments.append(("", "\n"))
            fragments.extend(line_fragments)
        return fragments or [("", "")]

    # ── 对话框 Float 构建 ────────────────────────────────────

    def build_float(self, input_buffer: Buffer, main_input_win: Window):
        """构建对话框 Float 层和 input_win,返回 (Float, dialog_input_win)。"""
        # 单问题模式用原有控件
        self._single_text_control = FormattedTextControl(text=self.get_dialog_text)
        dialog_text_win = Window(
            content=self._single_text_control,
            wrap_lines=False,
            width=self.get_dialog_width,
        )

        # 多问题模式控件 — FormattedTextControl 子类,支持鼠标点击
        self._multi_control = MultiQuestionTextControl(self)
        multi_text_win = Window(
            content=self._multi_control,
            wrap_lines=False,
            width=self.get_dialog_width,
        )

        _is_multi = Condition(lambda: self.multi_mode and self.active)
        _is_single = Condition(lambda: not self.multi_mode and self.active)

        # 输入框 — 多问题模式下显示提示文字而非输入框
        def _get_hint():
            if self._is_multi():
                return [("dim", "  数字键选中/取消 | Tab 切换 | Enter 提交")]
            return [("dim", "  数字键选择 | Tab 切换 | Enter 提交")]

        hint_control = FormattedTextControl(text=_get_hint)
        hint_win = Window(content=hint_control, height=1, width=self.get_dialog_width)

        dialog_input_win = Window(
            content=BufferControl(buffer=input_buffer),
            height=2,
            width=self.get_dialog_width,
            get_line_prefix=lambda _a, _b: HTML("<b>输入 > </b>"),
        )
        self.input_win = dialog_input_win

        dialog_float = Float(
            content=ConditionalContainer(
                content=Frame(
                    HSplit(
                        [
                            ConditionalContainer(
                                content=dialog_text_win, filter=_is_single
                            ),
                            ConditionalContainer(
                                content=multi_text_win, filter=_is_multi
                            ),
                            Window(height=1, char="─", style="class:separator"),
                            ConditionalContainer(
                                content=dialog_input_win, filter=_is_single
                            ),
                            ConditionalContainer(content=hint_win, filter=_is_multi),
                        ]
                    ),
                    title=lambda: self.title,
                ),
                filter=Condition(lambda: self.active),
            ),
            top=0,
            bottom=0,
        )

        return dialog_float, dialog_input_win

    # ── 对话框输入(单问题) ────────────────────────────────────

    async def tui_input(
        self,
        prompt: str,
        title: str,
        config: AppConfig,
        buffer: Buffer,
        main_input_win: Window,
    ) -> str:
        """显示多行提示并等待用户输入。异步版本,不阻塞事件循环。"""
        self.multi_mode = False
        tui = self._tui
        if not tui.app:
            return ""
        plain_prompt = _ANSI_RE.sub("", prompt)
        max_line = (
            max(plain_prompt.splitlines(), key=_get_display_width)
            if plain_prompt
            else ""
        )
        dialog_event = asyncio.Event()

        def _open_dialog():
            self.scroll_offset = 0
            self.prompt = prompt
            self.title = title
            self.prompt_fragments_list = self.prompt_fragments(prompt)
            self.content_width = _get_display_width(max_line) + 4
            self.event = dialog_event
            self.result = None
            if self.buffer is not None:
                self.buffer.reset()
            self.active = True
            tui._focus_window(self.input_win)

        await tui._loop.run_in_executor(
            None, lambda: tui._run_on_ui_thread(_open_dialog, wait=True)
        )
        timeout = config.permission_timeout

        # 倒计时线程
        countdown_done = threading.Event()
        countdown_paused = threading.Event()
        countdown_stopped = threading.Event()

        def _on_dialog_text_changed(_):
            if not countdown_stopped.is_set():
                countdown_paused.set()

        if self.buffer:
            self.buffer.on_text_changed += _on_dialog_text_changed

        def _countdown():
            remaining = timeout
            while remaining > 0 and not countdown_done.is_set():
                if countdown_paused.is_set():
                    self.title = title
                    tui.app.invalidate()
                    countdown_done.wait()
                    return
                minutes, seconds = divmod(remaining, 60)
                self.title = f"{title} ({minutes:02d}:{seconds:02d})"
                tui.app.invalidate()
                countdown_done.wait(1)
                remaining -= 1
            if not countdown_done.is_set():
                self.title = title
                tui.app.invalidate()

        if timeout > 0:
            countdown_thread = threading.Thread(target=_countdown, daemon=True)
            countdown_thread.start()

        # 异步等待用户输入,不阻塞事件循环
        try:
            if timeout > 0:
                await asyncio.wait_for(dialog_event.wait(), timeout=timeout)
                answered = True
            else:
                await dialog_event.wait()
                answered = True
        except TimeoutError:
            answered = False
        countdown_stopped.set()
        countdown_done.set()
        if self.buffer:
            self.buffer.on_text_changed -= _on_dialog_text_changed
        if not answered:
            self.result = "已经超时,用户这会可能不在"

        result = self.result or ""
        self.active = False
        self.event = None

        def _close_dialog():
            self.active = False
            self.prompt = ""
            self.prompt_fragments_list = []
            self.event = None
            if self.buffer is not None:
                self.buffer.reset()
            tui._focus_window(main_input_win)

        await tui._loop.run_in_executor(
            None, lambda: tui._run_on_ui_thread(_close_dialog, wait=True)
        )
        return result

    # ── 对话框输入(多问题 Tab) ────────────────────────────────

    async def tui_multi_input(
        self,
        questions: list[dict],
        title: str,
        config: AppConfig,
        buffer: Buffer,
        main_input_win: Window,
    ) -> str:
        """多问题 Tab 对话框,支持鼠标点击选择和"其他"编辑框。"""
        tui = self._tui
        if not tui.app:
            return ""

        self.set_questions(questions)
        dialog_event = asyncio.Event()

        # 计算内容宽度 — 使用终端宽度的 2/3
        console_w = shutil.get_terminal_size((80, 24)).columns
        max_w = console_w * 2 // 3

        def _open_dialog():
            self.prompt = ""
            self.title = title
            self.prompt_fragments_list = []
            self.content_width = max_w
            self.event = dialog_event
            self.result = None
            if self.buffer is not None:
                self.buffer.reset()
            self.active = True
            # 多问题模式不 focus 到输入框
            tui.app.invalidate()

        await tui._loop.run_in_executor(
            None, lambda: tui._run_on_ui_thread(_open_dialog, wait=True)
        )
        timeout = config.permission_timeout

        # 倒计时
        countdown_done = threading.Event()
        countdown_stopped = threading.Event()

        def _countdown():
            remaining = timeout
            while remaining > 0 and not countdown_done.is_set():
                minutes, seconds = divmod(remaining, 60)
                self.title = f"{title} ({minutes:02d}:{seconds:02d})"
                tui.app.invalidate()
                countdown_done.wait(1)
                remaining -= 1
            if not countdown_done.is_set():
                self.title = title
                tui.app.invalidate()

        if timeout > 0:
            threading.Thread(target=_countdown, daemon=True).start()

        try:
            if timeout > 0:
                await asyncio.wait_for(dialog_event.wait(), timeout=timeout)
            else:
                await dialog_event.wait()
        except TimeoutError:
            self.result = "已经超时,用户这会可能不在"
        countdown_stopped.set()
        countdown_done.set()

        result = self.result or ""
        self.active = False
        self.multi_mode = False
        self.event = None

        def _close_dialog():
            self.active = False
            self.multi_mode = False
            self.prompt = ""
            self.prompt_fragments_list = []
            self.event = None
            if self.buffer is not None:
                self.buffer.reset()
            tui._focus_window(main_input_win)

        await tui._loop.run_in_executor(
            None, lambda: tui._run_on_ui_thread(_close_dialog, wait=True)
        )
        return result

    # ── 快捷键 ────────────────────────────────────────────────

    def bind_keys(self, bindings: KeyBindings, input_buffer: Buffer):
        """注册对话框相关的快捷键到 bindings。"""
        _is_dialog = Condition(lambda: self.active)

        # 多问题模式的快捷键
        _is_multi = Condition(lambda: self.active and self.multi_mode)
        _is_other_active = Condition(
            lambda: self.active and self.multi_mode and self.other_active
        )
        _is_multi_not_other = Condition(
            lambda: self.active and self.multi_mode and not self.other_active
        )

        @bindings.add("tab", filter=_is_multi_not_other, eager=True)
        def _tab_next(event):
            self._switch_tab(1)

        @bindings.add("s-tab", filter=_is_multi_not_other, eager=True)
        def _tab_prev(event):
            self._switch_tab(-1)

        # 数字键选择选项(非"其他"编辑模式时)
        for digit in "123456789":

            @bindings.add(digit, filter=_is_multi_not_other, eager=True)
            def _select_by_key(event, d=digit):
                idx = int(d) - 1
                q = self.questions[self.current_tab]
                options = q.get("options", [])
                other_idx = self._get_other_idx()
                total = other_idx + 1  # 包含"其他"
                if 0 <= idx < total:
                    self._select_option(idx)

        # "其他"编辑模式:键盘输入写入 other_text
        @bindings.add("<any>", filter=_is_other_active, eager=True)
        def _other_input(event):
            key = event.key_sequence[-1] if event.key_sequence else None
            data = key.data if key is not None else ""
            if data in ("\r", "\n"):
                # Enter:确认当前"其他"文本,标记已选择
                self.other_active = False
                self._invalidate()
            elif data in ("\x08", "\x7f"):
                # Backspace
                t = self.other_texts.get(self.current_tab, "")
                self.other_texts[self.current_tab] = t[:-1]
                self._invalidate()
            elif data == "escape":
                # Escape:取消"其他"编辑
                self.other_active = False
                self._invalidate()
            elif data and data.isprintable():
                self.other_texts[self.current_tab] = (
                    self.other_texts.get(self.current_tab, "") + data
                )
                self._invalidate()

        # Enter 提交(非"其他"编辑模式时,所有题都有选择则提交)
        @bindings.add("enter", filter=_is_multi_not_other, eager=True)
        def _submit_multi(event):
            if self._all_selected():
                self.result = self._collect_answers()
                if self.event:
                    self.event.set()

        # 单问题模式原有快捷键
        _is_single = Condition(lambda: self.active and not self.multi_mode)

        @bindings.add(
            "up",
            filter=Condition(lambda: not input_buffer.complete_state) & _is_single,
            eager=True,
        )
        def _dialog_scroll_up(event):
            all_lines = OutputRenderer.wrap_fragment_lines(
                OutputRenderer.split_fragments_lines(self.prompt_fragments_list),
                self.get_dialog_width(),
            )
            rows = shutil.get_terminal_size((80, 24)).lines
            visible_rows = max(5, rows - self.chrome_height)
            max_offset = max(0, len(all_lines) - visible_rows)
            self.scroll_offset = min(self.scroll_offset + 1, max_offset)
            event.app.invalidate()

        @bindings.add(
            "down",
            filter=Condition(lambda: not input_buffer.complete_state) & _is_single,
            eager=True,
        )
        def _dialog_scroll_down(event):
            self.scroll_offset = max(0, self.scroll_offset - 1)
            event.app.invalidate()
