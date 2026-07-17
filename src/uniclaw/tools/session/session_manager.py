from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from uniclaw.context import Scope, get_app_dir
from uniclaw.tools.session.session import Session, SessionType

if TYPE_CHECKING:
    from uniclaw.config import AppConfig


class SessionManager:
    @staticmethod
    def _default_dir() -> Path:
        return get_app_dir(Scope.USER) / "sessions"

    @classmethod
    def metadata_file(cls) -> Path:
        return cls._default_dir() / "metadata.json"

    @classmethod
    def load_session(cls, session_id: str) -> Session | None:
        """加载会话,返回 Session 对象。"""
        meta = cls._load_metadata().get(session_id)
        if not meta:
            return None
        path = Path(meta.get("file_path", ""))
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Session.from_data(data)
        except (OSError, json.JSONDecodeError):
            return None

    @classmethod
    def list_sessions(cls, limit: int = 0, root_dir: str | None = None, include_wechat: bool = False) -> list[dict]:
        items = list(cls._load_metadata().values())
        if not include_wechat:
            items = [item for item in items if item.get("session_type", SessionType.CONSOLE) != SessionType.WECHAT]
        if root_dir:
            items = [item for item in items if item.get("root_dir") == root_dir]
        items.sort(
            key=lambda item: item.get("end_time") or item.get("start_time") or "",
            reverse=True,
        )
        if limit > 0:
            return items[:limit]
        return items

    @classmethod
    def delete_session(cls, session_id: str) -> bool:
        metadata = cls._load_metadata()
        meta = metadata.pop(session_id, None)
        if not meta:
            return False
        path = Path(meta.get("file_path", ""))
        try:
            if path.exists():
                path.unlink()
            cls._save_metadata(metadata)
            return True
        except OSError:
            return False

    @classmethod
    def search_sessions(cls, keyword: str) -> list:
        pattern = re.compile(keyword, re.IGNORECASE)
        results = []
        for meta in cls.list_sessions(limit=0):
            session = cls.load_session(meta["session_id"])
            if not session:
                continue
            matches: list[int] = []
            for idx, msg in enumerate(session.to_openai_messages(), 1):
                text = json.dumps(msg, ensure_ascii=False, default=str)
                if pattern.search(text):
                    matches.append(idx)
            if matches:
                item = dict(meta)
                item["matches"] = matches
                results.append(item)
        return results

    @classmethod
    def update_title(cls, session_id: str, title: str) -> bool:
        meta = cls._load_metadata().get(session_id)
        if not meta:
            return False
        path = Path(meta.get("file_path", ""))
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        data["title"] = title
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        meta["title"] = title
        metadata = cls._load_metadata()
        metadata[session_id] = meta
        cls._save_metadata(metadata)
        return True

    @classmethod
    def update_root_dir(cls, session_id: str, root_dir: str) -> bool:
        """更新会话的 root_dir(移动到其他项目)。"""
        metadata = cls._load_metadata()
        meta = metadata.get(session_id)
        if not meta:
            return False
        # 更新 session 文件中的 root_dir
        path = Path(meta.get("file_path", ""))
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["root_dir"] = root_dir
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except (OSError, json.JSONDecodeError):
                return False
        meta["root_dir"] = root_dir
        metadata[session_id] = meta
        cls._save_metadata(metadata)
        return True

    @classmethod
    async def fork_session(cls, session_id: str, message_idx: int, config: AppConfig) -> Session | None:
        """从指定会话的消息处分叉,创建新会话。

        Args:
            session_id: 原会话 ID
            message_idx: 分叉点消息索引(0-based),包含该消息及之前的消息
            config: 配置字典

        Returns:
            新会话 Session,失败返回 None
        """
        meta = cls._load_metadata().get(session_id)
        if not meta:
            return None
        path = Path(meta.get("file_path", ""))
        if not path.exists():
            return None
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        messages = original.get("messages", [])
        if message_idx < 0 or message_idx >= len(messages):
            return None

        title = (original.get("title") or "") + "分叉"

        root_dir = original.get("root_dir")
        if root_dir == "None":
            root_dir = None
        forked = Session(
            title=title,
            root_dir=Path(root_dir) if root_dir else None,
            system_prompt=original.get("system_prompt"),
        )
        for msg in messages[: message_idx + 1]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            extra = {k: v for k, v in msg.items() if k not in ("role", "content")}
            forked.add_message(role, content, **extra)

        data = await forked.to_dict(config)
        if data is None:
            return None
        data["metadata"] = original.get("metadata", {})

        task_dir = cls._default_dir()
        task_dir.mkdir(parents=True, exist_ok=True)
        file_path = task_dir / f"{forked.id}.json"
        file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cls._upsert_metadata(data, file_path)
        return forked

    @classmethod
    async def save_session(cls, config: AppConfig) -> str:
        task = config.current_agent
        data = await task.session.to_dict(config)
        metadata = cls._load_metadata()
        existing_meta = metadata.get(task.id, None)
        if existing_meta:
            file_path = Path(existing_meta["file_path"])
        else:
            file_path = cls._default_dir() / f"{task.id}.json"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cls._upsert_metadata(data, file_path)
        return str(file_path)

    @classmethod
    def _load_metadata(cls) -> dict:
        if not cls.metadata_file().exists():
            return {}
        try:
            data = json.loads(cls.metadata_file().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        for item in data.values():
            if isinstance(item, dict) and item.get("root_dir") == "None":
                item["root_dir"] = None
        return data

    @classmethod
    def _save_metadata(cls, metadata: dict):
        cls.metadata_file().parent.mkdir(parents=True, exist_ok=True)
        cls.metadata_file().write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def _upsert_metadata(cls, data: dict, file_path: Path):
        metadata = cls._load_metadata()
        metadata[data["session_id"]] = {
            "session_id": data["session_id"],
            "title": data.get("title"),
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "message_count": data.get("message_count", 0),
            "root_dir": data.get("root_dir") or None,
            "session_type": data.get("session_type", SessionType.CONSOLE),
            "file_path": str(file_path.resolve()),
        }
        cls._save_metadata(metadata)
