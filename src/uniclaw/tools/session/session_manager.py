from __future__ import annotations
import shutil
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
    def _resolve_path(cls, raw: str) -> Path:
        """将 metadata 中的 file_path 解析为绝对路径。

        兼容旧版绝对路径和新版相对路径 (相对于 sessions 目录).
        """
        p = Path(raw)
        if p.is_absolute():
            return p
        return cls._default_dir() / p

    @classmethod
    def load_session(cls, session_id: str) -> Session | None:
        """加载会话,返回 Session 对象。"""
        meta = cls._load_metadata().get(session_id)
        if not meta:
            return None
        path = cls._resolve_path(meta.get("file_path", ""))
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Session.from_data(data)
        except (OSError, json.JSONDecodeError):
            return None

    @classmethod
    def list_sessions(
        cls,
        limit: int = 0,
        root_dir: str | None = None,
        include_wechat: bool = False,
        archived: bool = False,
    ) -> list[dict]:
        """列出会话,默认排除已归档的会话(归档 = 移出视野)。

        Args:
            limit: 返回条数上限,0 表示不限。
            root_dir: 按会话工作目录过滤。
            include_wechat: 是否包含微信会话。
            archived: True 只列归档会话,False 只列未归档会话。
                需要全部时无法用此参数表达,直接读 _load_metadata()。

        Returns:
            list[dict]: 排序后的会话 metadata 列表。
        """
        items = list(cls._load_metadata().values())
        if not include_wechat:
            items = [
                item
                for item in items
                if item.get("session_type", SessionType.CONSOLE) != SessionType.WECHAT
            ]
        if root_dir:
            items = [item for item in items if item.get("root_dir") == root_dir]
        items = [
            item
            for item in items
            if bool(item.get("archived", False)) == archived
        ]
        items.sort(
            key=lambda item: item.get("end_time") or item.get("start_time") or "",
            reverse=True,
        )
        if limit > 0:
            return items[:limit]
        return items

    @classmethod
    def delete_session(cls, session_id: str, config: AppConfig | None = None) -> bool:
        metadata = cls._load_metadata()
        meta = metadata.pop(session_id, None)
        # 判断是否为自由聊天并获取 root_dir:config 优先,meta 兜底
        is_free_chat = (config and config.is_free_chat) or (
            meta and meta.get("session_type") == SessionType.FREE_CHAT
        )
        root_dir = (config.root_dir if config else None) or (
            Path(meta["root_dir"]) if meta and meta.get("root_dir") else None
        )
        # 清理自由聊天的独立工作目录
        if is_free_chat and root_dir and root_dir.is_dir():
            shutil.rmtree(root_dir, ignore_errors=True)
        if not meta:
            return False
        path = cls._resolve_path(meta.get("file_path", ""))
        try:
            if path.exists():
                path.unlink()
            cls._save_metadata(metadata)
            cls._cleanup_mcp_persistent(session_id)
            return True
        except OSError:
            return False

    @classmethod
    def _cleanup_mcp_persistent(cls, session_id: str) -> None:
        """清理被删除会话的 MCP 持久连接(fire-and-forget,不阻塞删除)。"""
        try:
            import asyncio

            from uniclaw.tools.mcp import MCPManager

            loop = asyncio.get_running_loop()
            loop.create_task(
                MCPManager.get_instance().close_session_persistent_sessions(session_id)
            )
        except RuntimeError:
            # 无运行中的事件循环(如单测同步调用),跳过清理
            pass
        except Exception:
            pass

    @classmethod
    def search_sessions(cls, keyword: str) -> list:
        pattern = re.compile(keyword, re.IGNORECASE)
        results = []
        for meta in cls.list_sessions(limit=0, archived=False):
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
        path = cls._resolve_path(meta.get("file_path", ""))
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
        path = cls._resolve_path(meta.get("file_path", ""))
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
    def _set_archived(cls, session_id: str, archived: bool) -> bool:
        """设置会话的归档标记。会话文件本身不动,仅改 metadata。"""
        metadata = cls._load_metadata()
        meta = metadata.get(session_id)
        if not meta:
            return False
        meta["archived"] = archived
        metadata[session_id] = meta
        cls._save_metadata(metadata)
        return True

    @classmethod
    def archive_session(cls, session_id: str) -> bool:
        """归档会话:从常规列表隐藏,进归档区。

        Args:
            session_id: 要归档的会话 ID。

        Returns:
            bool: 会话存在且归档成功时为 True。
        """
        return cls._set_archived(session_id, True)

    @classmethod
    def unarchive_session(cls, session_id: str) -> bool:
        """取消归档会话:从归档区恢复到常规列表。

        Args:
            session_id: 要恢复的会话 ID。

        Returns:
            bool: 会话存在且恢复成功时为 True。
        """
        return cls._set_archived(session_id, False)

    @classmethod
    async def fork_session(
        cls, session_id: str, message_idx: int, config: AppConfig
    ) -> Session:
        """从指定会话的消息处分叉,创建新会话。

        Args:
            session_id: 原会话 ID
            message_idx: 分叉点消息索引(0-based),包含该消息及之前的消息
            config: 配置字典

        Returns:
            Session: 新会话。

        Raises:
            ValueError: 会话不存在、会话文件损坏或 message_idx 越界。
            RuntimeError: 序列化新会话失败。
        """
        meta = cls._load_metadata().get(session_id)
        if not meta:
            raise ValueError(f"未找到会话: {session_id}")
        path = cls._resolve_path(meta.get("file_path", ""))
        if not path.exists():
            raise ValueError(f"会话文件不存在: {path}")
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"会话文件读取或解析失败: {path}") from e

        messages = original.get("messages", [])
        if message_idx < 0 or message_idx >= len(messages):
            raise ValueError(
                f"无效的消息序号 {message_idx}(共 {len(messages)} 条消息)"
            )

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
            raise RuntimeError("分叉会话序列化失败")
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
        if task.session.session_type == SessionType.A2A:
            return ""
        data = await task.session.to_dict(config)
        metadata = cls._load_metadata()
        existing_meta = metadata.get(task.id, None)
        if existing_meta:
            file_path = cls._resolve_path(existing_meta["file_path"])
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
        resolved = file_path.resolve()
        try:
            rel = str(resolved.relative_to(cls._default_dir().resolve()))
        except ValueError:
            # 不在 sessions 目录下的路径 (理论上不会发生), 降级为绝对路径
            rel = str(resolved)
        metadata = cls._load_metadata()
        existing = metadata.get(data["session_id"], {})
        # 保留归档标记:会话归档后若仍在运行并保存,不应被覆盖回未归档状态
        archived = existing.get("archived", False)
        metadata[data["session_id"]] = {
            "session_id": data["session_id"],
            "title": data.get("title"),
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "message_count": data.get("message_count", 0),
            "root_dir": data.get("root_dir") or None,
            "session_type": data.get("session_type", SessionType.CONSOLE),
            "file_path": rel,
            "archived": archived,
        }
        cls._save_metadata(metadata)
