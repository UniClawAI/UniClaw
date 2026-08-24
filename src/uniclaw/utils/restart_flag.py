"""进程重启标志文件的读写。

重启流程中,旧进程在退出前写入标志文件(session_id),
新进程启动时读取并删除,据此恢复会话并用固定提示词唤醒 agent 继续工作。
"""

import json
import os
import time
from pathlib import Path

from uniclaw.context import Scope, get_app_dir
from uniclaw.utils.constants import SYSTEM_PREFIX

# 标志文件的有效期:超过该时间的标志视为陈旧,不再触发唤醒
MAX_AGE_SECONDS = 600

# 重启完成后唤醒 agent 的固定提示词(消费方:tools/restart/resumer.py)
WAKE_MESSAGE = (
    f"{SYSTEM_PREFIX}(restart_agent) 进程已完成重启,会话已从磁盘恢复。"
    f"请继续之前的工作。"
)


def flag_path() -> Path:
    """返回重启标志文件的路径(用户级目录)。"""
    return get_app_dir(Scope.USER) / "pending_restart.json"


def write_pending_restart(session_id: str) -> Path:
    """写入重启标志文件。

    Args:
        session_id: 重启后要恢复的会话 ID。

    Returns:
        标志文件路径。
    """
    path = flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": session_id,
        "pid": os.getpid(),
        "timestamp": time.time(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_pending_restart(max_age: float = MAX_AGE_SECONDS) -> dict | None:
    """读取重启标志文件。

    Args:
        max_age: 标志最大有效期(秒),超过视为陈旧并删除文件后返回 None
            (陈旧标志已无消费价值,留着只会误导后续排查)。

    Returns:
        标志内容 dict,不存在或陈旧返回 None。
    """
    path = flag_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        clear_pending_restart()
        return None
    timestamp = data.get("timestamp", 0)
    if not isinstance(timestamp, (int, float)) or time.time() - timestamp > max_age:
        clear_pending_restart()
        return None
    return data


def clear_pending_restart() -> None:
    """删除重启标志文件(存在才删,不存在静默跳过)。"""
    try:
        flag_path().unlink(missing_ok=True)
    except OSError:
        pass
