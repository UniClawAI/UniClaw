import logging
import shutil

from prompt_toolkit.filters import Condition

logger = logging.getLogger("run")
from prompt_toolkit.application import get_app
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame


class SessionPanel:
    """管理 TUI 会话列表面板:状态、滚动、渲染、快捷键。"""

    def __init__(self, tui_ref):
        self._tui = tui_ref
        self.items: list[dict] = []
        self.selected_index: int = 0
        self.scroll_offset: int = 0
        self.focused: bool = False
        self.visible: bool = True

    # ── 数据管理 ──────────────────────────────────────────────

    def refresh(self):
        try:
            from pathlib import Path
            from uniclaw.tools.session.session_manager import SessionManager

            current_root_dir = str(Path.cwd())
            self.items = SessionManager.list_sessions(
                limit=10000, root_dir=current_root_dir
            )
            if self.selected_index >= len(self.items):
                self.selected_index = max(0, len(self.items) - 1)
            self.clamp_scroll()
        except Exception as e:
            logger.debug("刷新会话列表失败: %s", e, exc_info=True)

    def load_selected(self):
        tui = self._tui
        if not tui.active_task or not self.items:
            return
        item = self.items[self.selected_index]
        session_id = item.get("session_id")
        if not session_id:
            return
        try:
            import asyncio
            from uniclaw.commands.resume import _restore_session

            from uniclaw.tools.session.session_manager import SessionManager

            data = SessionManager.load_session(session_id)
            if not data:
                tui.print(f"未找到会话: {session_id}")
                return
            # _restore_session 是异步函数,用 ensure_future 调用
            asyncio.ensure_future(_restore_session(data, tui.active_task))

        except Exception as exc:
            import logging

            logging.getLogger("run").error("加载会话失败", exc_info=True)
            tui.print(f"加载会话失败: {exc}")

    # ── 滚动逻辑 ──────────────────────────────────────────────

    def visible_item_count(self) -> int:
        rows = shutil.get_terminal_size((80, 24)).lines
        content_rows = max(1, rows - 2)
        return max(1, content_rows // 2)

    def max_scroll_offset(self) -> int:
        return max(0, len(self.items) - self.visible_item_count())

    def clamp_scroll(self):
        self.scroll_offset = max(0, min(self.scroll_offset, self.max_scroll_offset()))

    def ensure_selected_visible(self):
        visible_count = self.visible_item_count()
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + visible_count:
            self.scroll_offset = self.selected_index - visible_count + 1
        self.clamp_scroll()

    def scroll(self, delta: int):
        self.scroll_offset = max(
            0, min(self.scroll_offset + delta, self.max_scroll_offset())
        )
        visible_count = self.visible_item_count()
        if self.items:
            self.selected_index = max(
                self.scroll_offset,
                min(
                    self.selected_index,
                    self.scroll_offset + visible_count - 1,
                    len(self.items) - 1,
                ),
            )

    def scroll_up(self):
        self.focused = True
        self.scroll(-1)
        if self._tui.app:
            self._tui.app.invalidate()

    def scroll_down(self):
        self.focused = True
        self.scroll(1)
        if self._tui.app:
            self._tui.app.invalidate()

    # ── 渲染 ──────────────────────────────────────────────────

    def get_text(self):
        if not self.items:
            return [("fg:gray", "暂无会话")]

        self.ensure_selected_visible()
        fragments: list[tuple[str, str]] = []
        active_session_id = self._tui.active_task.id if self._tui.active_task else ""
        visible_count = self.visible_item_count()
        start = self.scroll_offset
        end = min(len(self.items), start + visible_count)
        for idx, item in enumerate(self.items[start:end], start):
            if idx > start:
                fragments.append(("", "\n"))
            selected = idx == self.selected_index
            active = item.get("session_id") == active_session_id
            title = item.get("title") or "[无标题]"
            if len(title) > 22:
                title = title[:21] + "..."
            marker = ">" if selected else " "
            active_mark = "*" if active else " "
            style = "reverse" if selected and self.focused else ""
            if active:
                style = (style + " fg:green").strip()
            fragments.append((style, f"{marker}{active_mark} {title}\n"))
            end_time = (item.get("end_time") or item.get("start_time") or "")[:16]
            count = item.get("message_count", 0)
            fragments.append(("fg:gray", f"   {end_time} | {count} 条消息"))
        return fragments

    # ── 布局构建 ──────────────────────────────────────────────

    def build_layout(self):
        """构建会话面板的布局组件,返回 ConditionalContainer 列表。"""
        from uniclaw.console.run import MouseScrollableFormattedTextControl

        def _session_height():
            rows = shutil.get_terminal_size((80, 24)).lines
            return max(1, rows - 2)

        session_window = Window(
            content=MouseScrollableFormattedTextControl(
                text=self.get_text,
                on_scroll_up=self.scroll_up,
                on_scroll_down=self.scroll_down,
            ),
            width=32,
            height=_session_height,
            wrap_lines=False,
            dont_extend_width=True,
            style="class:session-list",
        )

        def _session_frame_height():
            try:
                return get_app().output.get_size().rows
            except Exception as e:
                logger.debug("获取终端尺寸失败: %s", e)
                return 24

        frame = ConditionalContainer(
            content=HSplit(
                [
                    Frame(
                        session_window,
                        title="会话(F3|C+K)",
                        height=_session_frame_height,
                    ),
                ]
            ),
            filter=Condition(lambda: self.visible),
        )
        separator = ConditionalContainer(
            content=Window(width=1, char="|", style="class:separator"),
            filter=Condition(lambda: self.visible),
        )
        return frame, separator

    # ── 快捷键 ────────────────────────────────────────────────

    def bind_keys(self, bindings: KeyBindings, _no_completion: Condition):
        """注册会话面板相关的快捷键到 bindings。"""
        _session_focused = Condition(
            lambda: self.focused and not self._tui.dialog.active
        )

        @bindings.add(
            "c-k", filter=Condition(lambda: not self._tui.dialog.active), eager=True
        )
        def _toggle_session_focus(event):
            self.focused = not self.focused
            event.app.invalidate()

        @bindings.add("enter", filter=_session_focused, eager=True)
        def _load_session(event):
            self.load_selected()
            self.focused = False
            event.app.invalidate()

        @bindings.add("up", filter=_no_completion & _session_focused, eager=True)
        def _session_previous(event):
            if self.items:
                self.selected_index = max(0, self.selected_index - 1)
                self.ensure_selected_visible()
            event.app.invalidate()

        @bindings.add("down", filter=_no_completion & _session_focused, eager=True)
        def _session_next(event):
            if self.items:
                self.selected_index = min(len(self.items) - 1, self.selected_index + 1)
                self.ensure_selected_visible()
            event.app.invalidate()
