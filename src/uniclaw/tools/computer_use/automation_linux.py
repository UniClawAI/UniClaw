"""Linux 后端 — 基于 xdotool 的桌面 UI 自动化。

全部走 utils.process.run_command(异步):xdotool 调用若用同步
subprocess.run 会阻塞事件循环最长 10 秒,等于把整个 agent 卡住。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.process import run_command, smart_decode

logger = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH = 5
MAX_ELEMENTS = 200

_CMD_TIMEOUT = 10


class _CmdError(RuntimeError):
    """shell 命令执行失败(非 bug: 命令返回非零或无法执行)。"""


async def _run(cmd: str) -> str:
    """执行 shell 命令并返回 stdout,失败时返回空串。

    用于探测/查询类命令(which、getactivewindow、search、getwindowgeometry)。
    这些命令的"无结果"与"失败"都以空串表达,调用方按需判断。
    """
    try:
        r = await run_command(cmd, timeout=_CMD_TIMEOUT, decode=smart_decode)
    except Exception as e:
        logger.debug("执行命令失败 '%s': %s", cmd, e)
        return ""
    return r.stdout.strip()


async def _run_action(cmd: str) -> str:
    """执行动作类 shell 命令,失败时抛 _CmdError。

    点击/输入/聚焦这类命令的失败绝不能静默 —— 一旦吞掉,调用方就会向 LLM
    报"已点击""已输入"的假成功,LLM 无从知道操作实际没生效。
    调用方应把 _CmdError 转成带上下文的 TOOL_ERROR。
    """
    try:
        r = await run_command(cmd, timeout=_CMD_TIMEOUT, decode=smart_decode)
    except Exception as e:
        raise _CmdError(f"命令执行异常({cmd}): {e}") from e
    if r.timed_out:
        raise _CmdError(f"命令超时 {_CMD_TIMEOUT} 秒({cmd})")
    if r.code != 0:
        detail = (r.stderr or r.stdout or "").strip()
        raise _CmdError(f"命令退出码 {r.code}({cmd}): {detail or '无错误输出'}")
    return r.stdout.strip()


async def _get_active_window() -> str:
    """获取当前活动窗口 ID。"""
    return await _run("xdotool getactivewindow")


async def _get_window_name(wid: str) -> str:
    """获取窗口名称。"""
    return await _run(f"xdotool getwindowname {wid}")


async def _get_window_pid(wid: str) -> str:
    """获取窗口 PID。"""
    return await _run(f"xdotool getwindowpid {wid}")


async def get_interactive_elements() -> str:
    """获取当前活动窗口信息。"""
    try:
        # 检查 xdotool 是否可用
        if not await _run("which xdotool"):
            return f"{TOOL_ERROR}: 需要安装 xdotool: sudo apt install xdotool"

        wid = await _get_active_window()
        if not wid:
            return f"{TOOL_ERROR}: 无法获取活动窗口"

        window_name = await _get_window_name(wid)
        window_pid = await _get_window_pid(wid)

        # 获取窗口几何信息
        geometry = await _run(f"xdotool getwindowgeometry --shell {wid}")
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


async def find_element(
    name: Optional[str] = None,
) -> str:
    """按标题查找窗口。"""
    try:
        if not await _run("which xdotool"):
            return f"{TOOL_ERROR}: 需要安装 xdotool"

        if name:
            wid = await _run(f"xdotool search --name '{name}'")
            if wid:
                wid = wid.splitlines()[0]
                wname = await _get_window_name(wid)
                geometry = await _run(f"xdotool getwindowgeometry --shell {wid}")
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
        if not await _run("which xdotool"):
            return f"{TOOL_ERROR}: 需要安装 xdotool"

        if action == "click":
            if x is not None and y is not None:
                await _run_action(f"xdotool mousemove {x} {y} click 1")
                return f"已点击: ({x}, {y})"
            return f"{TOOL_ERROR}: 需要提供 x, y 坐标"

        elif action == "invoke":
            if x is not None and y is not None:
                await _run_action(f"xdotool mousemove {x} {y} click 1")
                return f"已点击(invoke): ({x}, {y})"
            return f"{TOOL_ERROR}: 需要提供 x, y 坐标"

        elif action == "focus":
            if name:
                wid = await _run(f"xdotool search --name '{name}'")
                if wid:
                    await _run_action(f"xdotool windowactivate {wid.splitlines()[0]}")
                    return f"已聚焦窗口: {name}"
            if x is not None and y is not None:
                await _run_action(f"xdotool mousemove {x} {y} click 1")
                return f"已点击(focus): ({x}, {y})"
            return f"{TOOL_ERROR}: 需要提供窗口名称或坐标"

        elif action == "type":
            if not type_text:
                return f"{TOOL_ERROR}: action='type' 时必须提供 type_text"
            if x is not None and y is not None:
                await _run_action(f"xdotool mousemove {x} {y} click 1")
                await asyncio.sleep(0.1)
            # xdotool type 不支持中文,用 xdotool key 逐字符
            await _run_action(f"xdotool type -- '{type_text}'")
            return f"已输入文本: {type_text}"

        else:
            return f"{TOOL_ERROR}: 不支持的操作 '{action}'"

    except _CmdError as e:
        return f"{TOOL_ERROR}: 操作 '{action}' 未生效: {e}"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


# ── 工具定义 ──────────────────────────────────────────────────────


@tool
async def cu_get_elements() -> str:
    """获取当前活动窗口信息(Linux xdotool)。

    返回当前活动窗口的标题、ID、位置和大小。
    xdotool 不支持 UI 树遍历,建议配合 cu_screenshot 截图后用坐标操作。

    Returns:
        窗口信息文本。
    """
    return await get_interactive_elements()


@tool
async def cu_find_element(
    name: Optional[str] = None,
) -> str:
    """在 Linux 桌面中按标题查找窗口。

    Args:
        name: 窗口标题(子串匹配)。

    Returns:
        窗口信息文本。
    """
    return await find_element(name)


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
