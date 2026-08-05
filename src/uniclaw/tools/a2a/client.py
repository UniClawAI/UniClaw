from __future__ import annotations

import uuid
import httpx


class A2AClient:
    """Small interoperable A2A JSON-RPC client (Agent Card discovery + tasks)."""

    def __init__(self, url: str, token: str = "", timeout: float = 120.0):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.timeout = timeout
        self._endpoint = ""

    def _card_candidates(self) -> list[str]:
        marker = "/.well-known/"
        if marker in self.url:
            return [self.url]

        base = self.url[:-4] if self.url.endswith("/a2a") else self.url
        return [
            base + "/.well-known/agent-card.json",
            base + "/.well-known/agent.json",
        ]

    async def get_card(self) -> dict:
        # A2A peers are commonly on localhost or a LAN.  Do not route those
        # requests through an ambient corporate/system HTTP proxy.
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, trust_env=False, verify=False) as client:
            errors = []
            for url in self._card_candidates():
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(str(exc))
        raise RuntimeError("无法读取 A2A Agent Card: " + "; ".join(errors))

    async def send_message(self, text: str, context_id: str = "") -> dict:
        endpoint = await self._endpoint_url()
        params = {"message": {"kind": "message", "messageId": str(uuid.uuid4()), "role": "user", "parts": [{"kind": "text", "text": text}]}}
        if context_id:
            params["contextId"] = context_id
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send", "params": params}
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, trust_env=False, verify=False) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            body = response.json()
        if "error" in body:
            raise RuntimeError(body["error"].get("message", "A2A 请求失败"))
        return body.get("result", {})

    async def get_task(self, task_id: str) -> dict:
        return await self._rpc("tasks/get", {"id": task_id})

    async def cancel_task(self, task_id: str) -> dict:
        return await self._rpc("tasks/cancel", {"id": task_id})

    async def _rpc(self, method: str, params: dict) -> dict:
        endpoint = await self._endpoint_url()
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, trust_env=False, verify=False) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            body = response.json()
        if "error" in body:
            raise RuntimeError(body["error"].get("message", "A2A 请求失败"))
        return body.get("result", {})

    async def _endpoint_url(self) -> str:
        if not self._endpoint:
            card = await self.get_card()
            self._endpoint = card.get("url") or card.get("supportedInterfaces", [{}])[0].get("url") or ""
        if not self._endpoint:
            raise RuntimeError("Agent Card 未声明可调用 URL")
        return self._endpoint


def extract_a2a_text(result: dict) -> str:
    """Extract readable content from either an A2A Message or Task response."""
    messages = [result]
    status = result.get("status") if isinstance(result, dict) else None
    if isinstance(status, dict) and status.get("message"):
        messages.append(status["message"])
    messages.extend(result.get("artifacts", []) if isinstance(result, dict) else [])
    chunks = []
    for item in messages:
        for part in item.get("parts", []) if isinstance(item, dict) else []:
            if part.get("kind") == "text" or "text" in part:
                chunks.append(str(part.get("text", "")))
    return "\n".join(chunk for chunk in chunks if chunk) or str(result)
