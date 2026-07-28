"""Linux 后端 — 基于 xdotool 的桌面 UI 自动化。"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Optional

from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR

logger = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH = 5
MAX_ELEMENTS = 200


def _run(cmd: str) -> str:
    """执行 shell 命令并返回 stdout。"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception as e:
        logger.debug("执行命令失败 '%s': %s", cmd, e)
        return ""


def _get_active_window() -> str:
    """获取当前活动窗口 ID。"""
    return _run("xdotool getactivewindow")


def _get_window_name(wid: str) -> str:
    """获取窗口名称。"""
    return _run(f"xdotool getwindowname {wid}")


def _get_window_pid(wid: str) -> str:
    """获取窗口 PID。"""
    return _run(f"xdotool getwindowpid {wid}")


def get_interactive_elements() -> str:
    """获取当前活动窗口信息。"""
    try:
        # 检查 xdotool 是否可用
        if not _run("which xdotool"):
            return f"{TOOL_ERROR}: 需要安装 xdotool: sudo apt install xdotool"

        wid = _get_active_window()
        if not wid:
            return f"{TOOL_ERROR}: 无法获取活动窗口"

        window_name = _get_window_name(wid)
        window_pid = _get_window_pid(wid)

        # 获取窗口几何信息
        geometry = _run(f"xdotool getwindowgeometry --shell {wid}")
        x = y = w = h = ""
        for line in geometry.splitlines():
            if line.startswith("X="):
                x = line.split("=")[1]
            elif line.startswith("Y="):
                y = line.split("=")[1]
            elif line.startswith("WIDTH="):
                w = line.split("=")[1]
            elif line.startswith("HEIGHT="):
                h = line.split("=")[1]

        lines = [
            f"窗口: {window_name}",
            f"窗口 ID: {wid}",
            f"PID: {window_pid}",
            f"位置: ({x}, {y})",
            f"大小: {w}x{h}",
            "",
            "⚠️ Linux 后端基于 xdotool,不支持 UI 树遍历。",
            "可用操作: 点击坐标、输入文本、按键。",
            "建议配合 cu_screenshot 截图后用坐标操作。",
        ]
        return "\n".join(lines)

    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


def find_element(
    name: Optional[str] = None,
) -> str:
    """按标题查找窗口。"""
    try:
        if not _run("which xdotool"):
            return f"{TOOL_ERROR}: 需要安装 xdotool"

        if name:
            wid = _run(f"xdotool search --name '{name}'")
            if wid:
                wid = wid.splitlines()[0]
                wname = _get_window_name(wid)
                geometry = _run(f"xdotool getwindowgeometry --shell {wid}")
                return f"找到窗口: {wname}\n窗口 ID: {wid}\n{geometry}"
            return f"{TOOL_ERROR}: 未找到名为 '{name}' 的窗口"

        return f"{TOOL_ERROR}: Linux 后端仅支持按窗口名称查找"

    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


async def interact(
    name: Optional[str] = None,
    action: str = "click",
    type_text: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
) -> str:
    """与桌面交互(通过 xdotool)。"""
    try:
        if not _run("which xdotool"):
            return f"{TOOL_ERROR}: 需要安装 xdotool"

        if action == "click":
            if x is not None and y is not None:
                _run(f"xdotool mousemove {x} {y} click 1")
                return f"已点击: ({x}, {y})"
            return f"{TOOL_ERROR}: 需要提供 x, y 坐标"

        elif action == "invoke":
            if x is not None and y is not None:
                _run(f"xdotool mousemove {x} {y} click 1")
                return f"已点击(invoke): ({x}, {y})"
            return f"{TOOL_ERROR}: 需要提供 x, y 坐标"

        elif action == "focus":
            if name:
                wid = _run(f"xdotool search --name '{name}'")
                if wid:
                    _run(f"xdotool windowactivate {wid.splitlines()[0]}")
                    return f"已聚焦窗口: {name}"
            if x is not None and y is not None:
                _run(f"xdotool mousemove {x} {y} click 1")
                return f"已点击(focus): ({x}, {y})"
            return f"{TOOL_ERROR}: 需要提供窗口名称或坐标"

        elif action == "type":
            if not type_text:
                return f"{TOOL_ERROR}: action='type' 时必须提供 type_text"
            if x is not None and y is not None:
                _run(f"xdotool mousemove {x} {y} click 1")
                await asyncio.sleep(0.1)
            # xdotool type 不支持中文,用 xdotool key 逐字符
            _run(f"xdotool type -- '{type_text}'")
            return f"已输入文本: {type_text}"

        else:
            return f"{TOOL_ERROR}: 不支持的操作 '{action}'"

    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


# ── 工具定义 ──────────────────────────────────────────────────────


@tool
def cu_get_elements() -> str:
    """获取当前活动窗口信息(Linux xdotool)。

    返回当前活动窗口的标题、ID、位置和大小。
    xdotool 不支持 UI 树遍历,建议配合 cu_screenshot 截图后用坐标操作。

    Returns:
        窗口信息文本。
    """
    return get_interactive_elements()


@tool
def cu_find_element(
    name: Optional[str] = None,
) -> str:
    """在 Linux 桌面中按标题查找窗口。

    Args:
        name: 窗口标题(子串匹配)。

    Returns:
        窗口信息文本。
    """
    return find_element(name)


@tool
async def cu_interact(
    name: Optional[str] = None,
    action: str = "click",
    type_text: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
) -> str:
    """与 Linux 桌面交互(通过 xdotool)。

    建议配合 cu_screenshot 截图确定坐标后再操作。

    Args:
        name: 窗口标题(仅 focus 操作支持)。
        action: click(点击)、invoke(调用)、focus(聚焦窗口)、type(输入文本)。
        type_text: 要输入的文本,仅 action="type" 时使用。
        x: 点击坐标 x。
        y: 点击坐标 y。

    Returns:
        操作结果消息。
    """
    return await interact(name=name, action=action, type_text=type_text, x=x, y=y)
