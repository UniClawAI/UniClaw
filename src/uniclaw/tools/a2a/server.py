"""A2A routes mounted directly on the existing WebUI FastAPI application."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from uniclaw.tools.session.session import SessionType

from .manager import A2AManager


def _message(text: str, role: str = "agent") -> dict:
    return {"kind": "message", "messageId": str(uuid.uuid4()), "role": role, "parts": [{"kind": "text", "text": text}]}


def mount_a2a_routes(app: FastAPI) -> None:
    """Mount A2A endpoints once. Availability is toggled by /a2a start|stop."""
    tasks: dict[str, dict] = {}
    contexts: dict[str, object] = {}
    context_locks: dict[str, asyncio.Lock] = {}
    running: dict[str, asyncio.Task] = {}

    def context_or_404(authorization: str | None) -> dict:
        context = A2AManager.get_instance().service_status()
        if not context:
            raise HTTPException(status_code=404, detail="A2A service is disabled")
        if authorization != f"Bearer {context['token']}":
            raise HTTPException(status_code=401, detail="Invalid A2A bearer token")
        return context

    def card_for(request: Request) -> dict:
        endpoint = str(request.base_url).rstrip("/") + "/a2a"
        return {
            "protocolVersion": "1.0",
            "name": "UniClaw",
            "description": "A UniClaw agent that can work in its local workspace and control its local computer when enabled.",
            "url": endpoint,
            "preferredTransport": "JSONRPC",
            "supportedInterfaces": [{"protocolBinding": "JSONRPC", "url": endpoint}],
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [{"id": "uniclaw", "name": "UniClaw local agent", "description": "Completes tasks using configured local UniClaw tools."}],
        }

    async def run_task(task: dict, text: str, context: dict) -> None:
        try:
            from uniclaw.agent import MultiAgent
            from uniclaw.config import RunMode, load_config
            from uniclaw.spinner import NoopSpinner

            context_id = task["contextId"]
            lock = context_locks.setdefault(context_id, asyncio.Lock())
            async with lock:
                if task["status"].get("state") == "canceled":
                    return
                config = contexts.get(context_id)
                if config is None:
                    config = load_config(root_dir=context["root_dir"], spinner=NoopSpinner(), run_mode=RunMode.WEBUI, session_type=SessionType.A2A)
                    config.permission_mode = context["permission_mode"]
                    contexts[context_id] = config
                await MultiAgent.get_instance().run(text, config=config)
                if task["status"].get("state") == "canceled":
                    return
                result = config.current_agent.result or config.current_agent.session.get_assistant_messages() or "任务已完成。"
                task["status"] = {"state": "completed", "message": _message(result), "timestamp": datetime.now(timezone.utc).isoformat()}
                task["artifacts"] = [{"artifactId": str(uuid.uuid4()), "parts": [{"kind": "text", "text": result}]}]
        except Exception as exc:
            if task["status"].get("state") != "canceled":
                task["status"] = {"state": "failed", "message": _message(f"A2A task failed: {exc}"), "timestamp": datetime.now(timezone.utc).isoformat()}
        finally:
            running.pop(task["id"], None)

    @app.get("/.well-known/agent-card.json")
    @app.get("/.well-known/agent.json")
    async def agent_card(request: Request, authorization: Annotated[str | None, Header()] = None):
        context_or_404(authorization)
        return card_for(request)

    @app.post("/a2a")
    async def rpc(request: Request, authorization: Annotated[str | None, Header()] = None):
        context = context_or_404(authorization)
        body = await request.json()
        request_id = body.get("id")
        if body.get("jsonrpc") != "2.0":
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Invalid JSON-RPC request"}})
        method, params = body.get("method"), body.get("params", {})
        if method == "tasks/get":
            task = tasks.get(params.get("id") or params.get("taskId"))
            if not task:
                return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "Task not found"}})
            return {"jsonrpc": "2.0", "id": request_id, "result": task}
        if method == "tasks/cancel":
            task = tasks.get(params.get("id") or params.get("taskId"))
            if not task:
                return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "Task not found"}})
            task["status"] = {"state": "canceled", "message": _message("Task canceled"), "timestamp": datetime.now(timezone.utc).isoformat()}
            config = contexts.get(task["contextId"])
            if config:
                config.current_agent.cancel_event.set()
            runner = running.get(task["id"])
            if runner and not config:
                runner.cancel()
            return {"jsonrpc": "2.0", "id": request_id, "result": task}
        if method not in ("message/send", "SendMessage"):
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
        text = "\n".join(str(p.get("text", "")) for p in params.get("message", {}).get("parts", []) if p.get("kind") == "text" or "text" in p).strip()
        if not text:
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "A text message is required"}})
        task_id = str(uuid.uuid4())
        task = {"kind": "task", "id": task_id, "contextId": params.get("contextId") or str(uuid.uuid4()), "status": {"state": "working", "timestamp": datetime.now(timezone.utc).isoformat()}}
        tasks[task_id] = task
        runner = asyncio.create_task(run_task(task, text, context))
        running[task_id] = runner
        return {"jsonrpc": "2.0", "id": request_id, "result": task}
