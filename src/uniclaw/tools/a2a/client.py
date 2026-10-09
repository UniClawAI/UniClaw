from __future__ import annotations

import uuid
from typing import Any

import httpx

# Agent Card 是准静态配置,按 URL 跨实例缓存 —— 工具层每次调用都新建
# A2AClient,实例级缓存等于没缓存(每次仍拉卡)。clear_card_cache 供刷新。
_CARD_CACHE: dict[str, dict] = {}


def clear_card_cache(url: str | None = None) -> None:
    """清除 Agent Card 缓存(不传 url 清全部)。"""
    if url is None:
        _CARD_CACHE.clear()
    else:
        _CARD_CACHE.pop(url.rstrip("/"), None)


class A2AClient:
    """Small interoperable A2A JSON-RPC client (Agent Card discovery + tasks)."""

    def __init__(self, url: str, token: str = "", timeout: float = 120.0):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.timeout = timeout
        self._endpoint = ""
        self._card: dict | None = None  # Agent Card 缓存(同实例不重复拉取)

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
        if self._card is not None:
            return self._card
        if self.url in _CARD_CACHE:
            self._card = _CARD_CACHE[self.url]
            return self._card
        # A2A peers are commonly on localhost or a LAN.  Do not route those
        # requests through an ambient corporate/system HTTP proxy.
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, trust_env=False, verify=False) as client:
            errors = []
            for url in self._card_candidates():
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    self._card = response.json()
                    _CARD_CACHE[self.url] = self._card
                    return self._card
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(str(exc))
        raise RuntimeError("无法读取 A2A Agent Card: " + "; ".join(errors))

    async def send_message(self, text: str, context_id: str = "", blocking: bool = True) -> dict:
        """发送 message/send 请求。

        blocking=False 按 A2A 协议请求非阻塞语义(尽快返回 submitted 任务),
        便于 a2a_submit_task 的"异步提交"名副其实;服务器忽略该字段时
        行为退化为同步返回,由调用方兜底处理。
        """
        endpoint = await self._endpoint_url()
        params = {"message": {"kind": "message", "messageId": str(uuid.uuid4()), "role": "user", "parts": [{"kind": "text", "text": text}]}}
        if context_id:
            params["contextId"] = context_id
        if not blocking:
            params["blocking"] = False
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send", "params": params}
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, trust_env=False, verify=False) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            body = response.json()
        if "error" in body:
            raise RuntimeError(body["error"].get("message", "A2A 请求失败"))
        return body.get("result", {})

    async def get_task(self, task_id: str) -> dict:
        result = await self._rpc("tasks/get", {"id": task_id})
        self._verify_task_id(result, task_id)
        return result

    async def cancel_task(self, task_id: str) -> dict:
        result = await self._rpc("tasks/cancel", {"id": task_id})
        self._verify_task_id(result, task_id)
        return result

    @staticmethod
    def _verify_task_id(result: dict, task_id: str) -> None:
        """校验响应属于所查询的任务 —— 提取不到或不一致都拒绝(防串号)。"""
        returned = task_id_of(result)
        if not returned:
            raise RuntimeError(
                f"A2A 响应缺少任务 ID(查询 {task_id}, 响应字段: "
                f"{sorted(result) if isinstance(result, dict) else type(result).__name__})"
            )
        if returned != task_id:
            raise RuntimeError(
                f"A2A 服务器返回了其他任务(查询 {task_id}, 返回 {returned}),拒绝透传"
            )

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


def task_id_of(result) -> str:
    """从 A2A 响应(Task/Message/包装结构)归一化提取任务 ID。

    兼容 id / taskId / task.id 等常见形态 —— 字段名不统一时旧逻辑
    提取为空会静默放行串号/输出 null。
    """
    if not isinstance(result, dict):
        return ""
    for key in ("id", "taskId"):
        val = result.get(key)
        if isinstance(val, str) and val:
            return val
    for inner_key in ("task", "result"):
        inner = result.get(inner_key)
        if isinstance(inner, dict):
            got = task_id_of(inner)
            if got:
                return got
    return ""


def task_state_of(result) -> str:
    """从 A2A 响应归一化提取任务状态(state)。

    兼容 status.state / 顶层 state / status 字符串 / task.* 嵌套。
    """
    if not isinstance(result, dict):
        return ""
    status = result.get("status")
    if isinstance(status, dict):
        state = status.get("state")
        if isinstance(state, str) and state:
            return state
    elif isinstance(status, str) and status:
        return status
    state = result.get("state")
    if isinstance(state, str) and state:
        return state
    for inner_key in ("task", "result"):
        inner = result.get(inner_key)
        if isinstance(inner, dict):
            got = task_state_of(inner)
            if got:
                return got
    return ""


def extract_a2a_text(result: dict) -> str:
    """Extract readable content from either an A2A Message or Task response."""
    messages = [result]
    status = result.get("status") if isinstance(result, dict) else None
    if isinstance(status, dict) and status.get("message"):
        messages.append(status["message"])
    messages.extend(result.get("artifacts", []) if isinstance(result, dict) else [])
    chunks = []
    for item in messages:
        if isinstance(item, str):
            if item:
                chunks.append(item)
            continue
        if not isinstance(item, dict):
            continue
        for part in item.get("parts", []):
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content") or ""
                if text:
                    chunks.append(str(text))
    return "\n".join(chunk for chunk in chunks if chunk) or str(result)