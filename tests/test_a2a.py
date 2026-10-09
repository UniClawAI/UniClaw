from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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

# ── a2a 异步语义(汇总 #6/#19) ──────────────────────────────


class TestA2aSubmitSemantics:
    @pytest.mark.asyncio
    async def test_sync_response_keeps_result_text(self):
        """服务器同步返回 Message 时,结果文本不能丢,也不能给 null task_id。"""
        from unittest.mock import AsyncMock, MagicMock, patch, patch

        from uniclaw.tools.a2a.tools import a2a_submit_task

        sync_result = {
            "kind": "message",
            "contextId": "ctx-1",
            "parts": [{"kind": "text", "text": "远端已完成"}],
        }
        fake_client = MagicMock()
        fake_client.send_message = AsyncMock(return_value=sync_result)

        with patch(
            "uniclaw.tools.a2a.tools.A2AManager.get_instance"
        ) as mgr, patch(
            "uniclaw.tools.a2a.tools.A2AClient", return_value=fake_client
        ):
            mgr.return_value.get_agent = AsyncMock(
                return_value={"url": "http://x", "token": ""}
            )
            result = await a2a_submit_task(
                agent_name="x",
                message="hi",
                tool_runtime=MagicMock(config=MagicMock()),
            )

        data = __import__("json").loads(result)
        assert data["result"] == "远端已完成"
        assert data["state"] == "completed"
        assert "无需 a2a_get_task" in data["note"]

    @pytest.mark.asyncio
    async def test_get_task_id_mismatch_reported(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from uniclaw.tools.a2a.tools import a2a_get_task

        fake_client = MagicMock()
        fake_client.get_task = AsyncMock(
            return_value={"id": "other-id", "status": {"state": "completed"}}
        )
        with patch(
            "uniclaw.tools.a2a.tools.A2AManager.get_instance"
        ) as mgr, patch(
            "uniclaw.tools.a2a.tools.A2AClient", return_value=fake_client
        ):
            mgr.return_value.get_agent = AsyncMock(
                return_value={"url": "http://x", "token": ""}
            )
            result = await a2a_get_task(agent_name="x", task_id="want-id")

        assert "不一致" in result
        assert "want-id" in result

class TestA2aFieldNormalization:
    def test_task_id_variants(self):
        from uniclaw.tools.a2a.client import task_id_of

        assert task_id_of({"id": "t1"}) == "t1"
        assert task_id_of({"taskId": "t2"}) == "t2"
        assert task_id_of({"task": {"id": "t3"}}) == "t3"
        assert task_id_of({"id": "x", "task": {"taskId": "t4"}}) == "x"
        assert task_id_of({}) == ""
        assert task_id_of(None) == ""

    def test_task_state_variants(self):
        from uniclaw.tools.a2a.client import task_state_of

        assert task_state_of({"status": {"state": "working"}}) == "working"
        assert task_state_of({"state": "submitted"}) == "submitted"
        assert task_state_of({"status": "canceled"}) == "canceled"
        assert task_state_of({"task": {"status": {"state": "completed"}}}) == "completed"
        assert task_state_of({}) == ""

    def test_extract_text_from_dict_parts(self):
        """parts 元素是 dict 时取 text/content,不再输出 dict repr。"""
        from uniclaw.tools.a2a.client import extract_a2a_text

        out = extract_a2a_text(
            {"parts": [{"kind": "text", "text": "结果内容"}, {"content": "补充"}]}
        )
        assert "结果内容" in out
        assert "补充" in out
        assert "kind" not in out, "不得泄漏 dict repr"

    @pytest.mark.asyncio
    async def test_client_get_task_rejects_mismatched_id(self):
        """client 层校验: 返回其他任务/缺 id 都拒绝。"""
        from uniclaw.tools.a2a.client import A2AClient

        client = A2AClient("http://x", "")
        client._endpoint = "http://x/a2a"
        with patch.object(
            client,
            "_rpc",
            AsyncMock(return_value={"id": "wrong-task", "status": {"state": "working"}}),
        ):
            with pytest.raises(RuntimeError, match="其他任务"):
                await client.get_task("want-id")
        with patch.object(client, "_rpc", AsyncMock(return_value={"status": {"state": "working"}})):
            with pytest.raises(RuntimeError, match="缺少任务 ID"):
                await client.get_task("want-id")

    @pytest.mark.asyncio
    async def test_submit_working_not_marked_completed(self):
        """working 状态不得被标成 completed;task_id 归一化(taskId 形态)。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from uniclaw.tools.a2a.tools import a2a_submit_task

        result = {
            "taskId": "task-9",
            "contextId": "ctx",
            "status": {"state": "working"},
        }
        fake_client = MagicMock()
        fake_client.send_message = AsyncMock(return_value=result)
        with patch("uniclaw.tools.a2a.tools.A2AManager.get_instance") as mgr, patch(
            "uniclaw.tools.a2a.tools.A2AClient", return_value=fake_client
        ), patch("uniclaw.tools.a2a.tools._start_task_watcher"):
            mgr.return_value.get_agent = AsyncMock(
                return_value={"url": "http://x", "token": ""}
            )
            out = await a2a_submit_task(
                agent_name="x", message="hi", tool_runtime=MagicMock(config=MagicMock())
            )
        import json as _json

        data = _json.loads(out)
        assert data["task_id"] == "task-9"
        assert data["state"] == "working"

    @pytest.mark.asyncio
    async def test_remove_agent_and_list_refresh(self):
        """remove_agent 工具可用;list 强制重读手工编辑的配置。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from uniclaw.tools.a2a.manager import A2AManager
        from uniclaw.tools.a2a.tools import a2a_list_agents, a2a_remove_agent

        mgr = A2AManager()
        mgr._config = None
        mgr._config_path = MagicMock()
        mgr._config_path.exists = MagicMock(return_value=False)

        removed = await a2a_remove_agent("nobody")
        assert "未找到" in removed

        mgr._config = {"agents": {"x": {"url": "http://x", "token": ""}}}
        with patch.object(A2AManager, "get_instance", return_value=mgr):
            out = await a2a_remove_agent("x")
            assert "已移除" in out
            # 手工改配置后 list 重读: 清缓存模拟磁盘变化
            mgr._config = None
            mgr._config_path.exists = MagicMock(return_value=False)
            listing = await a2a_list_agents()
            assert "尚未配置" in listing

class TestA2aCardCache:
    @pytest.mark.asyncio
    async def test_card_cached_across_clients(self):
        """同 URL 的新客户端实例复用已拉取的 Agent Card(工具层每次新建实例)。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from uniclaw.tools.a2a.client import A2AClient, clear_card_cache

        clear_card_cache()
        card = {"url": "http://x/a2a", "name": "x"}
        fake_response = MagicMock()
        fake_response.json = MagicMock(return_value=card)
        fake_response.raise_for_status = MagicMock()
        fake_client = AsyncMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "uniclaw.tools.a2a.client.httpx.AsyncClient", return_value=fake_client
        ):
            c1 = A2AClient("http://x", "")
            await c1.get_card()
            c2 = A2AClient("http://x", "")
            await c2.get_card()
            c3 = A2AClient("http://x", "")
            await c3.get_card()

        assert fake_client.get.call_count == 1, (
            f"同 URL 应只拉一次 Agent Card,实际 {fake_client.get.call_count} 次"
        )
        clear_card_cache()
