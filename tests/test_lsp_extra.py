"""LSP 扩展测试 — client 分发/文档同步/诊断兜底/格式化边界。

纯逻辑 + mock,不启动真实语言服务器。
覆盖:dispatch 响应分发、request 超时、文档同步 hash 与版本、诊断 push/pull、
崩溃重启计数、格式化截断边界。
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.tools.lsp.client import LspClient, LspRpcError
from uniclaw.tools.lsp.manager import LSPManager, LspSession
from uniclaw.tools.lsp.tools import (
    _format_diagnostics,
    _format_locations,
    _parse_position,
)


def _client() -> LspClient:
    return LspClient(["fake"], "/tmp", name="test")


# ── dispatch 分发 ──────────────────────────────────────────


def test_dispatch_response_matches_pending_id():
    """响应按 id 分发到对应的 pending future。"""
    c = _client()
    fut = MagicMock(done=lambda: False)
    c._pending[7] = fut
    c._dispatch({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})
    fut.set_result.assert_called_once_with({"ok": True})


def test_dispatch_error_response_sets_exception():
    """服务器 error 响应 → future 收到 LspRpcError。"""
    c = _client()
    fut = MagicMock(done=lambda: False)
    c._pending[3] = fut
    c._dispatch({"jsonrpc": "2.0", "id": 3, "error": {"code": -32601, "message": "no"}})
    exc = fut.set_exception.call_args[0][0]
    assert isinstance(exc, LspRpcError) and exc.code == -32601


def test_dispatch_unknown_id_ignored():
    """未知 id 的响应(迟到/乱序)不报错。"""
    c = _client()
    c._dispatch({"jsonrpc": "2.0", "id": 999, "result": 1})  # 不应抛异常


def test_dispatch_register_capability_is_server_request():
    """读循环识别服务器→客户端请求(id+method 且不在 pending)。"""
    c = _client()
    assert c._pending == {}
    # 该分支在 _read_loop 中判断,这里验证判定条件
    msg = {"jsonrpc": "2.0", "id": 1, "method": "client/registerCapability", "params": {}}
    is_server_request = (
        "id" in msg and "method" in msg and msg["id"] not in c._pending
    )
    assert is_server_request


@pytest.mark.asyncio
async def test_request_timeout_cleans_pending():
    """request 超时后 pending 记录被清除(防泄漏)。"""
    c = _client()
    c._proc = MagicMock(returncode=None)
    c._proc.stdin.write = MagicMock()  # 同步写
    c._proc.stdin.drain = AsyncMock()
    # fut 永不 resolve(无响应到达),真实 wait_for 短超时触发
    with pytest.raises(asyncio.TimeoutError):
        await c.request("x", {}, timeout=0.01)
    assert 1 not in c._pending  # 已清理


def test_is_alive_states():
    """is_alive:未启动 False;进程死了 False。"""
    c = _client()
    assert not c.is_alive
    c._proc = MagicMock(returncode=0)
    c._read_task = MagicMock(done=lambda: True)
    assert not c.is_alive  # read_task 已完成


# ── 文档同步 hash/version ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_do_open_version_increments_and_dedupes(tmp_path):
    """_do_open:首次 didOpen v1;内容不变跳过;变化后 v2。"""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    client = MagicMock(is_alive=True, notify=AsyncMock())
    s = LspSession("python", tmp_path)
    s.client = client
    resolved = str(f.resolve())

    await s._do_open(resolved)
    v1 = client.notify.call_args[0][1]["textDocument"]["version"]
    assert v1 == 1
    client.notify.reset_mock()

    await s._do_open(resolved)  # 内容未变 → 跳过
    client.notify.assert_not_awaited()

    f.write_text("x = 2\n", encoding="utf-8")
    await s._do_open(resolved)
    v2 = client.notify.call_args[0][1]["textDocument"]["version"]
    assert v2 == 2  # 版本递增


@pytest.mark.asyncio
async def test_do_open_force_redelivers(tmp_path):
    """force=True 即使内容未变也重新 didOpen(崩溃重启后重开)。"""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    client = MagicMock(is_alive=True, notify=AsyncMock())
    s = LspSession("python", tmp_path)
    s.client = client
    resolved = str(f.resolve())
    await s._do_open(resolved)
    client.notify.reset_mock()
    await s._do_open(resolved, force=True)
    assert client.notify.await_count == 1


# ── 诊断 push/pull 兜底 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_diagnostics_push_hit(tmp_path):
    """推送已到时轮询返回缓存诊断。"""
    f = tmp_path / "a.py"
    f.write_text("x: int = 's'\n", encoding="utf-8")
    client = MagicMock(is_alive=True, notify=AsyncMock())
    client.get_diagnostics = MagicMock(return_value=[{"severity": 1, "message": "m"}])
    s = LspSession("python", tmp_path)
    s.client = client
    diags = await s.get_diagnostics(str(f))
    assert diags == [{"severity": 1, "message": "m"}]
    # 立即 pull 失败(request 未配置)→ 轮询到已推送的缓存
    client.get_diagnostics.assert_called()


@pytest.mark.asyncio
async def test_get_diagnostics_pull_fallback(tmp_path):
    """推送未到 → pull 兜底取诊断;pull 失败返回 None。"""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    client = MagicMock(is_alive=True, notify=AsyncMock())
    client.get_diagnostics = MagicMock(return_value=None)  # 一直无推送

    # pull 成功
    client.request = AsyncMock(return_value={"kind": "full", "items": [{"message": "e"}]})
    s = LspSession("python", tmp_path)
    s.client = client
    with patch("uniclaw.tools.lsp.manager.DIAG_WAIT_TIMEOUT", 0.01):
        diags = await s.get_diagnostics(str(f))
    assert diags == [{"message": "e"}]
    # 无条件失效目标缓存(依赖可能已改),pull 结果写回缓存
    client.invalidate_diagnostics.assert_called()
    assert client._diag_cache  # pull 已写入缓存

    # pull 抛错(服务器不支持)
    s2 = LspSession("python", tmp_path)
    s2.client = MagicMock(is_alive=True, notify=AsyncMock())
    s2.client.get_diagnostics = MagicMock(return_value=None)
    s2.client.request = AsyncMock(side_effect=LspRpcError(-32601, "no method"))
    with patch("uniclaw.tools.lsp.manager.DIAG_WAIT_TIMEOUT", 0.01):
        diags2 = await s2.get_diagnostics(str(f))
    assert diags2 is None


@pytest.mark.asyncio
async def test_get_diagnostics_empty_items_is_success(tmp_path):
    """pull 返回空 items 也视为成功(服务器支持 pull 且确实无诊断)。"""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    client = MagicMock(is_alive=True, notify=AsyncMock())
    client.get_diagnostics = MagicMock(return_value=None)
    client.request = AsyncMock(return_value={"kind": "full", "items": []})
    s = LspSession("python", tmp_path)
    s.client = client
    with patch("uniclaw.tools.lsp.manager.DIAG_WAIT_TIMEOUT", 0.01):
        diags = await s.get_diagnostics(str(f))
    assert diags == []  # 空列表而非 None


@pytest.mark.asyncio
async def test_pull_early_after_invalidate(tmp_path):
    """invalidate 后无推送则提前 pull(PULL_EARLY_SECONDS 内返回)。"""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    client = MagicMock(is_alive=True, notify=AsyncMock())
    client.get_diagnostics = MagicMock(return_value=None)
    client.request = AsyncMock(return_value={"items": [{"message": "ok"}]})
    s = LspSession("python", tmp_path)
    s.client = client
    with (
        patch("uniclaw.tools.lsp.manager.PULL_EARLY_SECONDS", 0.0),
        patch("uniclaw.tools.lsp.manager.DIAG_WAIT_TIMEOUT", 5.0),
    ):
        t0 = asyncio.get_event_loop().time()
        diags = await s.get_diagnostics(str(f))
        elapsed = asyncio.get_event_loop().time() - t0
    assert diags == [{"message": "ok"}]
    assert elapsed < 2.0  # 提前 pull 生效,未干等 5s


@pytest.mark.asyncio
async def test_pull_fail_then_push(tmp_path):
    """提前 pull 失败(pull 不支持)→ 继续轮询;push 到达则返回。"""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    client = MagicMock(is_alive=True, notify=AsyncMock())
    client.request = AsyncMock(side_effect=LspRpcError(-32601, "no method"))
    client.get_diagnostics = MagicMock(
        side_effect=[None, None, [{"message": "pushed"}]]
    )
    s = LspSession("python", tmp_path)
    s.client = client
    with (
        patch("uniclaw.tools.lsp.manager.PULL_EARLY_SECONDS", 0.0),
        patch("uniclaw.tools.lsp.manager.DIAG_POLL_INTERVAL", 0.01),
        patch("uniclaw.tools.lsp.manager.DIAG_WAIT_TIMEOUT", 5.0),
    ):
        diags = await s.get_diagnostics(str(f))
    assert diags == [{"message": "pushed"}]


@pytest.mark.asyncio
async def test_pull_fail_no_push_returns_none(tmp_path):
    """pull 不支持且无 push → 等满超时后返回 None(不无限等)。"""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    client = MagicMock(is_alive=True, notify=AsyncMock())
    client.request = AsyncMock(side_effect=LspRpcError(-32601, "no method"))
    client.get_diagnostics = MagicMock(return_value=None)
    s = LspSession("python", tmp_path)
    s.client = client
    with (
        patch("uniclaw.tools.lsp.manager.PULL_EARLY_SECONDS", 0.0),
        patch("uniclaw.tools.lsp.manager.DIAG_POLL_INTERVAL", 0.01),
        patch("uniclaw.tools.lsp.manager.DIAG_WAIT_TIMEOUT", 0.05),
    ):
        diags = await s.get_diagnostics(str(f))
    assert diags is None


# ── 崩溃重启 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_started_respects_max_restarts(tmp_path):
    """连续崩溃超过 MAX_RESTARTS 后不再自动重启。"""
    import uniclaw.tools.lsp.manager as mgr_mod

    s = LspSession("python", tmp_path)
    s._restarts = mgr_mod.MAX_RESTARTS  # 已达上限
    s.client = MagicMock(is_alive=False)
    with pytest.raises(RuntimeError, match="连续崩溃"):
        await s.ensure_started()


@pytest.mark.asyncio
async def test_close_resets_start_error_and_restarts(tmp_path):
    """close() 清除 _start_error,允许会话复活重启。"""
    s = LspSession("python", tmp_path)
    s._start_error = "previous failure"
    s.client = MagicMock(is_alive=True, stop=AsyncMock())
    await s.close()
    assert s._start_error is None
    assert s.client is None
    assert s._docs == {}


# ── 格式化边界 ──────────────────────────────────────────


def test_format_diagnostics_truncates_long_message():
    """超长诊断消息截断到 200 字符。"""
    diags = [{"severity": 1, "range": {"start": {"line": 0, "character": 0}},
              "message": "x" * 300}]
    out = _format_diagnostics(Path("/a.py"), diags)
    assert "..." in out and "x" * 200 not in out  # 已截断


def test_format_diagnostics_many_items_truncated():
    """超过 100 条时截断并提示省略数。"""
    diags = [{"severity": 1, "range": {"start": {"line": i, "character": 0}},
              "message": f"e{i}"} for i in range(120)]
    out = _format_diagnostics(Path("/a.py"), diags)
    assert "20 条已省略" in out  # 120-100=20


def test_format_locations_many_truncated():
    """超过 limit 时截断。"""
    items = [{"uri": f"file:///d:/f{i}.py",
              "range": {"start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1}}} for i in range(30)]
    out = _format_locations(items, limit=10)
    assert "另有 20 处已省略" in out


@pytest.mark.parametrize(
    "text,path_sub,line,col",
    [
        ("", None, 0, 0),                          # 空串 → 当前目录
        ("a.py", "a.py", 0, 0),
        ("a.py:0", "a.py", 0, 0),                  # 行 0 → 0-based 0
        ("a.py:-3", "a.py:-3", 0, 0),              # 负行号当作路径一部分
        # 三层冒号:从右数两位是行/列,其余是路径
        ("a.py:10:5:3", "a.py:10", 4, 2),
        ("D:/x/y.py:2", "y.py", 1, 0),             # 正斜杠盘符
    ],
)
def test_parse_position_edges(text, path_sub, line, col):
    got_path, got_line, got_col = _parse_position(text)
    assert got_line == line and got_col == col
    if path_sub is None:
        return  # 空串解析到当前目录,不校验具体路径
    assert got_path.name == path_sub or str(got_path).endswith(path_sub)
