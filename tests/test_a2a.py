from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from uniclaw.tools.a2a.manager import A2AManager
from uniclaw.tools.a2a.client import A2AClient
from uniclaw.tools.a2a import tools as a2a_tools
from fastapi import FastAPI
from uniclaw.tools.a2a.server import mount_a2a_routes


@pytest.mark.asyncio
async def test_manager_persists_remote_agents(tmp_path):
    manager = A2AManager()
    manager._config_path = tmp_path / "a2a.json"
    await manager.add_agent("office-pc", "http://192.168.1.20:8765", "secret")

    assert await manager.get_agent("office-pc") == {
        "name": "office-pc",
        "url": "http://192.168.1.20:8765",
        "token": "secret",
    }
    assert await manager.remove_agent("office-pc") is True
    assert await manager.get_agent("office-pc") is None


def test_agent_card_candidates_support_endpoint_and_legacy_card():
    assert A2AClient("http://computer:8080/a2a")._card_candidates() == [
        "http://computer:8080/.well-known/agent-card.json",
        "http://computer:8080/.well-known/agent.json",
    ]
    assert A2AClient("http://computer:8080/.well-known/agent.json")._card_candidates() == [
        "http://computer:8080/.well-known/agent.json"
    ]


def test_agent_card_and_json_rpc_authentication():
    manager = A2AManager.get_instance()
    manager.disable_service()
    manager.enable_service(SimpleNamespace(root_dir=None, permission_mode="auto", workspace=[], writable_dirs=[]), "secret")
    app = FastAPI()
    mount_a2a_routes(app)
    client = TestClient(app)

    assert client.get("/.well-known/agent-card.json").status_code == 401
    card = client.get("/.well-known/agent-card.json", headers={"Authorization": "Bearer secret"})
    assert card.status_code == 200
    assert card.json()["url"] == "http://testserver/a2a"

    response = client.post("/a2a", headers={"Authorization": "Bearer secret"}, json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {"id": "missing"}})
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_completed_background_task_wakes_originating_agent(monkeypatch):
    received = []

    async def fake_wake(message, config):
        received.append(message)
        return True

    class CompletedClient:
        async def get_task(self, task_id):
            return {"id": task_id, "status": {"state": "completed"}, "artifacts": [{"parts": [{"kind": "text", "text": "done"}]}]}

    monkeypatch.setattr("uniclaw.utils.wakeup.wake_agent", fake_wake)
    config = SimpleNamespace(current_agent=SimpleNamespace(id="session-1"))
    await a2a_tools._watch_a2a_task("office-pc", "task-1", "context-1", CompletedClient(), config)

    assert "task_id: task-1" in received[0]
    assert "done" in received[0]
