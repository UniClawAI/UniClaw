from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from uniclaw.agent import AgentTask
    from uniclaw.config import AppConfig

from uniclaw.context import Scope
from enum import StrEnum
from uniclaw.utils.cache import ttl_cache
from uniclaw.utils.logger import get_logger

HOOKS_DIR_NAME = ".uniclaw"
HOOKS_FILE_NAME = "hooks.json"
DEFAULT_HOOK_TIMEOUT_SECONDS = 30


class HookEvent(StrEnum):
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PRE_ASSISTANT = "PreAssistant"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_RESPONSE = "PermissionResponse"


BLOCKING_EVENTS = {HookEvent.PRE_TOOL_USE, HookEvent.PERMISSION_REQUEST}
VALID_EVENTS = {
    HookEvent.SESSION_START,
    HookEvent.SESSION_END,
    HookEvent.PRE_TOOL_USE,
    HookEvent.POST_TOOL_USE,
    HookEvent.PRE_ASSISTANT,
    HookEvent.PERMISSION_REQUEST,
    HookEvent.PERMISSION_RESPONSE,
}


@dataclass
class HookResult:
    event: str
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int

    @property
    def blocked(self) -> bool:
        return self.event in BLOCKING_EVENTS and self.returncode != 0


class HookError(Exception):
    pass


def _generate_id() -> str:
    return uuid.uuid4().hex[:8]


def get_hooks_path(root: Scope | Path = Scope.USER) -> Path:
    from uniclaw.context import get_app_dir

    return get_app_dir(root) / HOOKS_FILE_NAME


def default_hooks_config() -> dict[str, Any]:
    return {
        "hooks": {
            HookEvent.SESSION_START: [],
            HookEvent.SESSION_END: [],
            HookEvent.PRE_TOOL_USE: [],
            HookEvent.POST_TOOL_USE: [],
            HookEvent.PRE_ASSISTANT: [],
            HookEvent.PERMISSION_REQUEST: [],
            HookEvent.PERMISSION_RESPONSE: [],
        }
    }


def _validate_entry(entry: Any, seen_ids: set[str]) -> None:
    if not isinstance(entry, dict):
        raise ValueError("hooks条目必须是对象")

    entry_id = entry.get("id")
    if not entry_id:
        entry_id = _generate_id()
        entry["id"] = entry_id
    if entry_id in seen_ids:
        raise ValueError(f"重复的hook id: {entry_id}")
    seen_ids.add(entry_id)

    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        raise ValueError("hooks条目必须包含 'hooks' 列表")
    for hook in hooks:
        if not isinstance(hook, dict):
            raise ValueError("嵌套hooks必须是对象")
        hook_type = hook.get("type", "command")
        if hook_type != "command":
            raise ValueError(f"不支持的hooks类型: {hook_type}")
        command = hook.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("命令hooks必须包含非空命令")


def validate_hooks_config(data: Any) -> None:
    if not isinstance(data, dict):
        raise ValueError("hooks配置必须是 JSON 对象")
    hooks = data.get("hooks")
    if hooks is None:
        raise ValueError("hooks配置必须包含顶级 'hooks' 对象")
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' 必须是对象")
    seen_ids: set[str] = set()
    for event, entries in hooks.items():
        if event not in VALID_EVENTS:
            raise ValueError(f"未知的hooks事件: {event}")
        if not isinstance(entries, list):
            raise ValueError(f"hooks.{event} 必须是列表")
        for entry in entries:
            _validate_entry(entry, seen_ids)


def load_hooks_config(root: Scope | Path = Scope.USER) -> dict[str, Any]:
    path = get_hooks_path(root)
    if not path.exists():
        return default_hooks_config()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    validate_hooks_config(data)
    return data


@ttl_cache(ttl_seconds=60)
def load_all_hooks_configs(root_dir: Path | None) -> list[tuple[str, dict[str, Any]]]:
    """加载项目级和用户级hooks配置,返回 [(scope, config), ...],项目级在前。"""
    configs = []
    for root in (root_dir, Scope.USER):
        if root is None:
            continue
        try:
            cfg = load_hooks_config(root)
            label = "project" if isinstance(root, Path) else root.value
            configs.append((label, cfg))
        except Exception:
            get_logger("hooks", root_dir).debug(
                "加载%s级hooks配置失败或不存在",
                "project" if isinstance(root, Path) else root.value,
            )
    return configs


def add_hook(
    event: str,
    commands: list[str],
    name: str | None = None,
    matcher: str | None = None,
    root: Scope | Path = Scope.USER,
) -> str:
    if event not in VALID_EVENTS:
        raise ValueError(f"未知的hooks事件: {event}")
    if not commands:
        raise ValueError("commands 不能为空")

    config = load_hooks_config(root)
    hooks = config.setdefault("hooks", {})
    entries = hooks.setdefault(event, [])

    new_id = _generate_id()

    entry: dict[str, Any] = {
        "id": new_id,
        "hooks": [{"type": "command", "command": cmd} for cmd in commands],
    }
    if name:
        entry["name"] = name
    if matcher:
        entry["matcher"] = matcher

    entries.append(entry)

    path = get_hooks_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    load_all_hooks_configs.cache_clear()
    return new_id


def remove_hook(hook_id_or_name: str, root_dir: Path) -> bool:
    removed = False
    for label, config in load_all_hooks_configs(root_dir):
        hooks = config.get("hooks", {})
        scope_changed = False
        for event in hooks:
            entries = hooks[event]
            original_len = len(entries)
            hooks[event] = [
                e
                for e in entries
                if e.get("id") != hook_id_or_name and e.get("name") != hook_id_or_name
            ]
            if len(hooks[event]) < original_len:
                scope_changed = True
        if scope_changed:
            removed = True
            root = root_dir if label == "project" else Scope.USER
            path = get_hooks_path(root)
            path.write_text(
                json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    if removed:
        load_all_hooks_configs.cache_clear()
    return removed


def _matches(matcher: Any, payload: dict[str, Any]) -> bool:
    if matcher in (None, "", "*"):
        return True
    tool_name = str(
        payload.get("tool_name") or payload.get("tool", {}).get("name") or ""
    )
    if not tool_name:
        return True
    patterns = [
        p.strip() for p in str(matcher).replace("|", ",").split(",") if p.strip()
    ]
    return any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in patterns)


def _hook_input(
    event: HookEvent,
    payload: dict[str, Any],
    config: AppConfig | None,
    task: AgentTask,
) -> dict[str, Any]:
    root_dir = task.session.root_dir
    return {
        "event": event,
        "cwd": str(root_dir) if root_dir else "",
        "session_id": task.session.id,
        "task_id": getattr(task, "id", None),
        "task_name": getattr(task, "name", None),
        **payload,
    }


async def _run_entries(
    event: HookEvent,
    entries: list[dict[str, Any]],
    hook_input: dict[str, Any],
    input_text: str,
    root_dir: str | None,
    scope: str,
) -> list[HookResult]:
    """运行单个scope的hooks条目。"""
    # 过滤敏感环境变量
    if root_dir is None:
        root_dir = os.getcwd()
    sensitive_keywords = ["KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"]
    filtered_env = {
        k: v
        for k, v in os.environ.items()
        if not any(keyword in k.upper() for keyword in sensitive_keywords)
    }
    env = {
        **filtered_env,
        "UNICLAW_HOOK_CWD": str(root_dir),
        "UNICLAW_HOOK_SCOPE": scope,
        "UNICLAW_HOOK_TOOL": str(hook_input.get("tool_name") or ""),
    }
    results: list[HookResult] = []
    for entry in entries:
        if not _matches(entry.get("matcher"), hook_input):
            continue
        for hook in entry.get("hooks", []):
            command = hook.get("command")
            timeout = (
                hook.get("timeout")
                or entry.get("timeout")
                or DEFAULT_HOOK_TIMEOUT_SECONDS
            )
            try:
                timeout = int(timeout)
            except (TypeError, ValueError):
                timeout = DEFAULT_HOOK_TIMEOUT_SECONDS
            hook_env = {**env, "UNICLAW_HOOK_EVENT": event}
            start = time.monotonic()
            try:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=root_dir,
                    env=hook_env,
                )
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=input_text.encode("utf-8")),
                    timeout=timeout,
                )
                stdout = (
                    stdout_bytes.decode("utf-8", errors="replace")
                    if stdout_bytes
                    else ""
                )
                stderr = (
                    stderr_bytes.decode("utf-8", errors="replace")
                    if stderr_bytes
                    else ""
                )
                result = HookResult(
                    event=event,
                    command=command,
                    returncode=proc.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                result = HookResult(
                    event=event,
                    command=command,
                    returncode=124,
                    stdout="",
                    stderr=f"hooks超时,超过 {timeout} 秒",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            results.append(result)
            if result.returncode != 0:
                get_logger("hooks", Path(root_dir)).warning(
                    "[%s] hooks失败: event=%s command=%s code=%s stdout=%s stderr=%s",
                    scope,
                    event,
                    command,
                    result.returncode,
                    result.stdout,
                    result.stderr,
                )
            if result.blocked:
                raise HookError(_format_block_message(result))
    return results


async def run_hooks(
    event: HookEvent,
    payload: dict[str, Any],
    config: "AppConfig | None",
    task: AgentTask,
) -> list[HookResult]:
    if event not in VALID_EVENTS:
        raise ValueError(f"未知的hooks事件: {event}")
    payload = payload or {}
    root_dir = task.session.root_dir
    hook_input = _hook_input(event, payload, config, task)
    input_text = json.dumps(hook_input, ensure_ascii=False)

    all_configs = load_all_hooks_configs(root_dir)
    if not all_configs and event in BLOCKING_EVENTS:
        get_logger("hooks", root_dir).error("未找到任何hooks配置")
        return []

    results: list[HookResult] = []
    for scope, hook_config in all_configs:
        entries = hook_config.get("hooks", {}).get(event, [])
        if not entries:
            continue
        try:
            scope_results = await _run_entries(
                event, entries, hook_input, input_text, root_dir, scope
            )
            results.extend(scope_results)
        except HookError:
            raise
        except Exception as exc:
            get_logger("hooks", root_dir).error(
                "运行%s级hooks失败", scope, exc_info=True
            )
            if event in BLOCKING_EVENTS:
                raise HookError(f"运行{scope}级hooks失败: {exc}") from exc
    return results


def _format_block_message(result: HookResult) -> str:
    details = (
        result.stderr.strip() or result.stdout.strip() or f"退出码 {result.returncode}"
    )
    return f"hooks阻止了 {result.event}: {details}"
