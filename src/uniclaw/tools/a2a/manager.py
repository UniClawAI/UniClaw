from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from uniclaw.context import Scope, get_app_dir

if TYPE_CHECKING:
    from uniclaw.config import AppConfig


class A2AManager:
    """Persists remote A2A agents and controls the WebUI-hosted A2A endpoint."""

    _instance: "A2AManager | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._a2a_dir: Path = get_app_dir(Scope.USER) / "a2a"
        self._config_path: Path = self._a2a_dir / "a2a.json"
        self._config: dict | None = None
        self._service_context: dict | None = None
        self._service_loaded: bool = False

    @classmethod
    def get_instance(cls) -> "A2AManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def load_config(self) -> dict:
        if self._config is not None:
            return self._config
        try:
            if self._config_path.exists():
                self._config = json.loads(self._config_path.read_text(encoding="utf-8"))
            if not isinstance(self._config, dict) or not isinstance(
                self._config.get("agents"), dict
            ):
                self._config = {"agents": {}}
        except (OSError, json.JSONDecodeError):
            self._config = {"agents": {}}
        return self._config

    async def save_config(self) -> None:
        await self.load_config()
        assert self._config is not None
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def list_agents(self) -> list[dict]:
        await self.load_config()
        return [{"name": name, **data} for name, data in self._config["agents"].items()]

    async def get_agent(self, name: str) -> dict | None:
        await self.load_config()
        data = self._config["agents"].get(name)
        return {"name": name, **data} if data else None

    async def add_agent(self, name: str, url: str, token: str = "") -> None:
        await self.load_config()
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError("名称只能包含字母、数字、连字符和下划线")
        if not url.startswith(("http://", "https://")):
            raise ValueError("A2A URL 必须以 http:// 或 https:// 开头")
        self._config["agents"][name] = {"url": url.rstrip("/"), "token": token}
        await self.save_config()

    async def remove_agent(self, name: str) -> bool:
        await self.load_config()
        if name not in self._config["agents"]:
            return False
        del self._config["agents"][name]
        await self.save_config()
        return True

    def enable_service(self, config: AppConfig, token: str = "") -> dict:
        """Enable A2A routes already mounted on the WebUI application."""
        if not token:
            token = secrets.token_urlsafe(32)
        self._a2a_dir.mkdir(parents=True, exist_ok=True)
        service_root = self._a2a_dir / "workspace"
        service_root.mkdir(parents=True, exist_ok=True)
        self._service_context = {
            "token": token,
            "root_dir": service_root,
            "permission_mode": config.permission_mode,
        }
        self._persist_service()
        return dict(self._service_context)

    def disable_service(self) -> bool:
        if not self._service_context:
            return False
        self._service_context = None
        self._persist_service()
        return True

    def service_status(self) -> dict | None:
        if not self._service_loaded:
            self._restore_service()
        return dict(self._service_context) if self._service_context else None

    def _persist_service(self) -> None:
        """Write service state to a2a.json alongside agents config."""
        try:
            self._a2a_dir.mkdir(parents=True, exist_ok=True)
            data = {}
            if self._config_path.exists():
                data = json.loads(self._config_path.read_text(encoding="utf-8"))
            if self._service_context:
                data["service"] = {
                    "token": self._service_context["token"],
                    "permission_mode": self._service_context["permission_mode"],
                }
            else:
                data.pop("service", None)
            self._config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _restore_service(self) -> None:
        """Load persisted service state from a2a.json on first access."""
        self._service_loaded = True
        try:
            if not self._config_path.exists():
                return
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            svc = data.get("service")
            if not isinstance(svc, dict) or not svc.get("token"):
                return
            service_root = self._a2a_dir / "workspace"
            service_root.mkdir(parents=True, exist_ok=True)
            self._service_context = {
                "token": svc["token"],
                "root_dir": service_root,
                "permission_mode": svc.get("permission_mode", "auto"),
            }
        except (OSError, json.JSONDecodeError):
            pass
