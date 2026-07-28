from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class JsonStateStore:
    def __init__(self, path: str | Path = "~/.ilink-bot/credentials.json"):
        self.path = Path(path).expanduser()
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = self._empty()
        self.data.setdefault("schema_version", 1)
        self.data.setdefault("contexts", {})
        self.data.setdefault("sync_buf", "")
        self.data.setdefault("login_info", {})
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_session(
        self,
        *,
        bot_token: str,
        base_url: str | None = None,
        login_response: dict[str, Any] | None = None,
    ) -> None:
        self.bot_token = bot_token
        if base_url:
            self.base_url = base_url
        self.data["login_info"] = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "base_url": self.base_url,
            "source": "qr_login",
            "login_response": login_response,
        }
        raw_user = (login_response or {}).get("user_info") or (
            login_response or {}
        ).get("userInfo")
        if isinstance(raw_user, dict):
            self.data["login_info"]["user_info"] = raw_user
        self.save()

    def session_info(self) -> dict[str, Any]:
        return {
            "logged_in": bool(self.bot_token),
            "credential_path": str(self.path),
            "base_url": self.base_url,
            "saved_at": self.data.get("login_info", {}).get("saved_at"),
            "context_count": len(self.data.get("contexts", {})),
        }

    @property
    def bot_token(self) -> str | None:
        return self.data.get("bot_token")

    @bot_token.setter
    def bot_token(self, value: str | None) -> None:
        if value:
            self.data["bot_token"] = value
        else:
            self.data.pop("bot_token", None)

    @property
    def base_url(self) -> str | None:
        return self.data.get("base_url")

    @base_url.setter
    def base_url(self, value: str | None) -> None:
        if value:
            self.data["base_url"] = value

    @property
    def sync_buf(self) -> str:
        return str(self.data.get("sync_buf") or "")

    @sync_buf.setter
    def sync_buf(self, value: str) -> None:
        self.data["sync_buf"] = value or ""

    def get_context(self, user_id: str) -> str | None:
        return self.data["contexts"].get(user_id)

    def set_context(self, user_id: str, context_token: str) -> None:
        if user_id and context_token:
            self.data["contexts"][user_id] = context_token

    def clear_session(self) -> None:
        base_url = self.base_url
        self.data = self._empty()
        if base_url:
            self.base_url = base_url
        self.save()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schema_version": 1, "contexts": {}, "sync_buf": "", "login_info": {}}
