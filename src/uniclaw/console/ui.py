from __future__ import annotations

import inspect
import uuid
import sys
from enum import StrEnum
import threading
import time
from typing import TYPE_CHECKING

from uniclaw.spinner import BaseSpinner
from uniclaw.utils.logger import get_logger

if TYPE_CHECKING:
    from uniclaw.config import AppConfig

_PT_STYLE_MAP = {
    "\033[30m": "fg:black",
    "\033[31m": "fg:red",
    "\033[32m": "fg:green",
    "\033[33m": "fg:yellow",
    "\033[34m": "fg:blue",
    "\033[35m": "fg:magenta",
    "\033[36m": "fg:cyan",
    "\033[37m": "fg:white",
    "\033[91m": "fg:brightred",
    "\033[92m": "fg:brightgreen",
    "\033[93m": "fg:brightyellow",
    "\033[94m": "fg:brightblue",
    "\033[95m": "fg:brightmagenta",
    "\033[96m": "fg:brightcyan",
    "\033[97m": "fg:brightwhite",
    "\033[1m": "bold",
    "\033[2m": "dim",
    "\033[3m": "italic",
    "\033[4m": "underline",
    "\033[7m": "reverse",
    "\033[90m": "fg:gray",
}


class C(StrEnum):
    """终端颜色代码枚举

    定义了常用的 ANSI 转义序列颜色代码,用于在终端中输出彩色文本。
    每个枚举值对应一个 ANSI 颜色代码字符串。

    Attributes:
        CYAN: 青色 (ANSI 36)
        GREEN: 绿色 (ANSI 32)
        YELLOW: 黄色 (ANSI 33)
        RED: 红色 (ANSI 31)
        BLUE: 蓝色 (ANSI 34)
        MAGENTA: 洋红色 (ANSI 35)
        WHITE: 白色 (ANSI 37)
        BOLD: 加粗样式 (ANSI 1)
        DIM: 暗淡样式 (ANSI 2)
        GRAY: 灰色 (ANSI 90)
        RESET: 重置所有样式 (ANSI 0)
        BLACK: 黑色 (ANSI 30)
        LIGHT_RED: 亮红色 (ANSI 91)
        LIGHT_GREEN: 亮绿色 (ANSI 92)
        LIGHT_YELLOW: 亮黄色 (ANSI 93)
        LIGHT_BLUE: 亮蓝色 (ANSI 94)
        LIGHT_MAGENTA: 亮洋红色 (ANSI 95)
        LIGHT_CYAN: 亮青色 (ANSI 96)
        LIGHT_WHITE: 亮白色 (ANSI 97)
        UNDERLINE: 下划线样式 (ANSI 4)
        ITALIC: 斜体样式 (ANSI 3)
        REVERSE: 反显样式 (ANSI 7)
    """

    # 基础颜色
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # 亮色
    LIGHT_RED = "\033[91m"
    LIGHT_GREEN = "\033[92m"
    LIGHT_YELLOW = "\033[93m"
    LIGHT_BLUE = "\033[94m"
    LIGHT_MAGENTA = "\033[95m"
    LIGHT_CYAN = "\033[96m"
    LIGHT_WHITE = "\033[97m"

    # 样式修饰
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    REVERSE = "\033[7m"
    GRAY = "\033[90m"

    # 重置
    RESET = "\033[0m"

    @property
    def pt_style(self) -> str:
        """对应的 prompt_toolkit 样式字符串"""
        return _PT_STYLE_MAP.get(self.value, "")


def clr(text: str, *keys: C) -> str:
    """为文本添加 ANSI 颜色(终端模式)。"""
    return "".join(k for k in keys) + str(text) + C.RESET


def tui_clr(text: str, *keys: C) -> list[tuple[str, str]]:
    """为文本添加 prompt_toolkit 片段(TUI 模式)。"""
    style = " ".join(k.pt_style for k in keys if k.pt_style)
    return [(style, str(text))]


def _get_tui():
    from uniclaw.console.run import TUIApp

    return TUIApp.get_instance()


def _get_callback(config: AppConfig | None):
    """从 config 中获取输出回调(沿 parent_config 链找到 root)。"""
    if config is None:
        return None
    root = config.root_config
    if root is None:
        root = config
    return root.output_callback


_WECHAT_PREFIX = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "err": "❌"}


def _wechat_reply(msg: str, config: AppConfig | None, level: str = "info") -> bool:
    """尝试通过微信发送消息,成功返回 True。"""
    if config is None:
        return False
    bot = getattr(config, "wechat_ctx", None)
    if not bot:
        return False
    prefix = _WECHAT_PREFIX.get(level, "ℹ️")
    try:
        bot.reply_text(f"{prefix} {msg}")
    except Exception as e:
        get_logger("console.ui", config.root_dir).debug("微信消息回复失败: %s", e)
    return True


async def info(msg: str, config: AppConfig = None):
    cb = _get_callback(config)
    if cb:
        if inspect.iscoroutinefunction(cb):
            await cb(msg, "info")
        else:
            cb(msg, "info")
        return
    if _wechat_reply(msg, config, "info"):
        return
    tui = _get_tui()
    if tui:
        tui.print(tui_clr(msg, C.CYAN))
    else:
        print(clr(msg, C.CYAN))


def clear():
    tui = _get_tui()
    if tui:
        tui.clear()
    else:
        print("\033[2J\033[H", end="")
        sys.stdout.flush()


async def ok(msg: str, config: AppConfig = None):
    cb = _get_callback(config)
    if cb:
        if inspect.iscoroutinefunction(cb):
            await cb(msg, "ok")
        else:
            cb(msg, "ok")
        return
    if _wechat_reply(msg, config, "ok"):
        return
    tui = _get_tui()
    if tui:
        tui.print(tui_clr(msg, C.GREEN))
    else:
        print(clr(msg, C.GREEN))


async def warn(msg: str, config: AppConfig = None):
    cb = _get_callback(config)
    if cb:
        if inspect.iscoroutinefunction(cb):
            await cb(msg, "warn")
        else:
            cb(msg, "warn")
        return
    if _wechat_reply(msg, config, "warn"):
        return
    tui = _get_tui()
    if tui:
        tui.print(tui_clr(f"Warning: {msg}", C.YELLOW))
    else:
        print(clr(f"Warning: {msg}", C.YELLOW))


async def err(msg: str, config: AppConfig = None):
    cb = _get_callback(config)
    if cb:
        if inspect.iscoroutinefunction(cb):
            await cb(msg, "err")
        else:
            cb(msg, "err")
        return
    if _wechat_reply(msg, config, "err"):
        return
    tui = _get_tui()
    if tui:
        tui.print(tui_clr(f"Error: {msg}", C.RED))
    else:
        print(clr(f"Error: {msg}", C.RED), file=sys.stderr)


def colorize_diff(diff_text: str) -> str:
    """为 unified diff 文本着色"""
    lines = diff_text.split("\n")
    result = []
    for line in lines:
        if line.startswith("---") or line.startswith("+++"):
            result.append(clr(line, C.DIM))
        elif line.startswith("@@"):
            result.append(clr(line, C.CYAN))
        elif line.startswith("-"):
            result.append(clr(line, C.RED))
        elif line.startswith("+"):
            result.append(clr(line, C.GREEN))
        else:
            result.append(line)
    return "\n".join(result)


class Spinner:
    thread = None
    stop_flag = threading.Event()
    current_text = "waiting..."

    @classmethod
    def start(cls, text: str = "waiting..."):
        cls.current_text = text
        if cls.thread and cls.thread.is_alive():
            return
        cls.stop_flag.clear()
        cls.thread = threading.Thread(target=cls.run, daemon=True, name="Spinner")
        cls.thread.start()

    @classmethod
    def stop(cls):
        if cls.thread and cls.thread.is_alive():
            cls.stop_flag.set()
            cls.thread.join(timeout=1)
            print("\r", " " * 70, end="\r")
        cls.thread = None

    @classmethod
    def run(cls):
        chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        while not cls.stop_flag.is_set():
            for char in chars:
                print(clr(f"\r{char} {cls.current_text}", C.BLUE), end="", flush=True)
                cls.stop_flag.wait(0.1)
                if cls.stop_flag.is_set():
                    break


class TUISpinner(BaseSpinner):
    """TUI 模式下的旋转器,通过回调更新显示(堆栈版本,线程安全)"""

    def __init__(self):
        self._stack: list[tuple[str, float, str]] = []
        self._lock = threading.Lock()
        self._frame: int = 0
        self._chars: str = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._invalidate_callback = None

    def set_invalidate_callback(self, callback):
        """设置 invalidate 回调,用于通知 TUI 刷新显示"""
        self._invalidate_callback = callback

    def start(self, text: str = "waiting...", wait_id: str | None = None):
        if wait_id is None:
            wait_id = f"TUISpinner_{uuid.uuid4().hex[:8]}"

        with self._lock:
            # 在_stack中寻找和自己id一样的那一层
            for i, (stack_text, stack_timestamp, stack_wait_id) in enumerate(
                self._stack
            ):
                if stack_wait_id == wait_id:
                    # 找到了相同的id
                    if stack_text == text:
                        # text一样,不做处理
                        return wait_id
                    else:
                        # text不一样,更新时间戳
                        self._stack[i] = (text, time.time(), wait_id)
                        self._frame = 0
                        if self._invalidate_callback:
                            self._invalidate_callback()
                        return wait_id

            # 没有找到一样的id,append新的
            self._stack.append((text, time.time(), wait_id))
            self._frame = 0
            if self._invalidate_callback:
                self._invalidate_callback()
        return wait_id

    def stop(self, wait_id: str):
        with self._lock:
            if not self._stack:
                return

            # 删除和自己id一样的那一项
            for i, (stack_text, stack_timestamp, stack_wait_id) in enumerate(
                self._stack
            ):
                if stack_wait_id == wait_id:
                    self._stack.pop(i)
                    if self._invalidate_callback:
                        self._invalidate_callback()
                    return

            # 如果没有一样的id,不做处理
            if self._invalidate_callback:
                self._invalidate_callback()

    def is_active(self) -> bool:
        with self._lock:
            return len(self._stack) > 0

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """格式化时间 duration"""
        if seconds < 1:
            return f"{seconds * 1000:.0f}ms"
        elif seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = seconds % 60
            return f"{minutes}m{secs:.0f}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h{minutes}m"

    def get_display(self) -> str:
        """获取当前旋转器显示文本(显示所有堆栈层级及其等待时间)"""
        with self._lock:
            if not self._stack:
                return ""

            char = self._chars[self._frame % len(self._chars)]

            # 构建每个层级的显示文本
            level_displays = []
            for text, timestamp, wait_id in self._stack:
                elapsed = time.time() - timestamp
                duration = self._format_duration(elapsed)
                level_displays.append(f"{text} [{duration}]")

            # 用 " > " 连接所有层级
            all_text = " > ".join(level_displays)
            return f"  {char} {all_text}"

    def update_frame(self):
        """更新帧并通知 TUI 刷新"""
        with self._lock:
            if self._stack:
                self._frame += 1
                if self._invalidate_callback:
                    self._invalidate_callback()


async def get_input(prompt: str, title: str = "输入", config: AppConfig = None) -> str:
    """通用异步输入函数,自动选择 TUI / WebUI / stdin。

    Args:
        prompt: 提示文本
        title: 弹窗标题
        config: 配置对象

    Returns:
        用户输入的字符串,取消则返回空字符串。
    """
    from uniclaw.config import DisplayMode

    mode = config.display_mode if config else DisplayMode.CONSOLE

    if mode == DisplayMode.WECHAT:
        from uniclaw.wechat.run import wechat_input

        return await wechat_input(prompt, title=title, config=config)

    if mode == DisplayMode.WEBUI:
        from uniclaw.webui.ws import web_input

        return await web_input(prompt, title=title, config=config)

    # console 模式:优先 TUI,否则 stdin
    tui = _get_tui()
    if tui:
        return await tui.tui_input(prompt, title=title)

    try:
        print(prompt)
        return input()
    except (EOFError, KeyboardInterrupt):
        return ""


async def get_multi_input(
    questions: list[dict], title: str = "请选择", config: AppConfig = None
) -> str:
    """多问题 Tab 输入函数,自动选择 TUI / WebUI / stdin。

    Args:
        questions: 问题列表,每项含 question 和 options
        title: 弹窗标题
        config: 配置对象

    Returns:
        JSON 格式的答案字符串,取消或超时则返回提示文本。
    """
    import json

    from uniclaw.config import DisplayMode

    mode = config.display_mode if config else DisplayMode.CONSOLE

    if mode == DisplayMode.WECHAT:
        from uniclaw.wechat.run import wechat_multi_input

        result = await wechat_multi_input(
            questions=questions, title=title, config=config
        )
        return result or "已经超时,用户这会可能不在"

    if mode == DisplayMode.WEBUI:
        from uniclaw.webui.ws import web_multi_input

        result = await web_multi_input(questions=questions, title=title, config=config)
        return result or "已经超时,用户这会可能不在"

    # console 模式:优先 TUI
    tui = _get_tui()
    if tui:
        return (await tui.tui_multi_input(questions, title=title)) or "已经超时,用户这会可能不在"

    # stdin 降级:逐题提问
    print(f"\n💬 {title}\n")
    answers = {}
    try:
        for q in questions:
            question_text = q.get("question", "")
            options = q.get("options", [])
            is_multi = q.get("multi", False)
            print(f"  {question_text}")
            for j, opt in enumerate(options):
                print(f"    {j + 1}. {opt}")
            if is_multi:
                print("  (可多选,输入编号用逗号或空格分隔)")
                ans = input("  > ").strip()
                # 解析多选
                import re
                parts = re.split(r'[,，\s]+', ans)
                selected = []
                has_number = False
                for part in parts:
                    part = part.strip()
                    if part.isdigit() and 1 <= int(part) <= len(options):
                        selected.append(options[int(part) - 1])
                        has_number = True
                if has_number:
                    answers[question_text] = selected if len(selected) > 1 else selected[0]
                else:
                    answers[question_text] = ans if ans else ""
            else:
                ans = input("  > ").strip()
                if ans.isdigit() and 1 <= int(ans) <= len(options):
                    answers[question_text] = options[int(ans) - 1]
                else:
                    answers[question_text] = ans if ans else ""
            print()
    except (EOFError, KeyboardInterrupt):
        return ""
    return json.dumps(answers, ensure_ascii=False)
