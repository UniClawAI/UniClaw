"""WebUI 应用层测试 — 覆盖认证中间件、auth API 端点和路径防护。"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from uniclaw.webui import app as webui_app
from uniclaw.webui.api import _validate_path
from uniclaw.webui.app import (
    _AuthRequest,
    _ChangePwdRequest,
    _is_trusted_ip,
    auth_change_password,
    auth_login,
    auth_me,
    auth_middleware,
    auth_register,
    auth_status,
    _serve_html,
)


def _make_request(
    path: str = "/api/test",
    client_ip: str = "1.2.3.4",
    headers: dict | None = None,
    cookies: dict | None = None,
) -> Request:
    """构造 Starlette Request。"""
    hdrs = [(b"host", b"localhost")]
    if headers:
        # ASGI 规范要求 headers 键为小写(uvicorn 会自动转换)
        for k, v in headers.items():
            hdrs.append((k.lower().encode(), v.encode()))
    if cookies:
        ck = "; ".join(f"{k}={v}" for k, v in cookies.items())
        hdrs.append((b"cookie", ck.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": hdrs,
        "client": (client_ip, 12345),
        "server": ("localhost", 8000),
        "state": {},
    }
    return Request(scope)


class TestValidatePath:
    """_validate_path 路径遍历防护测试。"""

    def test_normal_path(self, tmp_path):
        """正常相对路径。"""
        result = _validate_path(str(tmp_path), "sub/file.txt")
        assert result == (tmp_path / "sub" / "file.txt").resolve()

    def test_same_dir(self, tmp_path):
        """同目录文件。"""
        result = _validate_path(str(tmp_path), ".")
        assert result == tmp_path.resolve()

    def test_parent_traversal(self, tmp_path):
        """向上越界抛出 403。"""
        with pytest.raises(HTTPException) as exc:
            _validate_path(str(tmp_path), "../outside")
        assert exc.value.status_code == 403

    def test_deep_traversal(self, tmp_path):
        """深层越界抛出 403。"""
        with pytest.raises(HTTPException) as exc:
            _validate_path(str(tmp_path), "../../../../etc/passwd")
        assert exc.value.status_code == 403

    def test_double_dot_inside(self, tmp_path):
        """内部含 .. 的路径。"""
        result = _validate_path(str(tmp_path), "a/../b.txt")
        assert result == (tmp_path / "b.txt").resolve()


class TestIsTrustedIp:
    """_is_trusted_ip 测试。"""

    @patch("uniclaw.config.get_config_path")
    def test_trusted(self, mock_path, tmp_path):
        """IP 在可信列表。"""
        cfg = tmp_path / "settings.json"
        cfg.write_text(json.dumps({"trusted_ips": ["192.168.1.5"]}), encoding="utf-8")
        mock_path.return_value = cfg
        assert _is_trusted_ip("192.168.1.5") is True

    @patch("uniclaw.config.get_config_path")
    def test_not_trusted(self, mock_path, tmp_path):
        """IP 不在可信列表。"""
        cfg = tmp_path / "settings.json"
        cfg.write_text(json.dumps({"trusted_ips": ["192.168.1.5"]}), encoding="utf-8")
        mock_path.return_value = cfg
        assert _is_trusted_ip("10.0.0.1") is False

    @patch("uniclaw.config.get_config_path")
    def test_no_config(self, mock_path, tmp_path):
        """无配置文件。"""
        mock_path.return_value = tmp_path / "missing.json"
        assert _is_trusted_ip("192.168.1.5") is False

    @patch("uniclaw.config.get_config_path")
    def test_empty_trusted(self, mock_path, tmp_path):
        """trusted_ips 为空。"""
        cfg = tmp_path / "settings.json"
        cfg.write_text(json.dumps({}), encoding="utf-8")
        mock_path.return_value = cfg
        assert _is_trusted_ip("192.168.1.5") is False

    @patch("uniclaw.config.get_config_path")
    def test_corrupt_config(self, mock_path, tmp_path):
        """配置文件损坏。"""
        cfg = tmp_path / "settings.json"
        cfg.write_text("not json", encoding="utf-8")
        mock_path.return_value = cfg
        assert _is_trusted_ip("192.168.1.5") is False


class TestAuthStatus:
    """auth_status 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.user_exists", return_value=True)
    async def test_has_user(self, mock_exists):
        """已有账号。"""
        resp = await auth_status()
        assert resp == {"has_user": True}

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.user_exists", return_value=False)
    async def test_no_user(self, mock_exists):
        """无账号。"""
        resp = await auth_status()
        assert resp == {"has_user": False}


class TestAuthRegister:
    """auth_register 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.user_exists", return_value=True)
    async def test_user_exists(self, mock_exists):
        """已有账号返回 400。"""
        resp = await auth_register(_AuthRequest(username="u", password="password1"))
        assert resp.status_code == 400
        assert "已存在" in json.loads(resp.body)["detail"]

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.user_exists", return_value=False)
    async def test_short_password(self, mock_exists):
        """密码过短返回 400。"""
        resp = await auth_register(_AuthRequest(username="u", password="123"))
        assert resp.status_code == 400
        assert "至少 6 位" in json.loads(resp.body)["detail"]

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.create_token", return_value="token123")
    @patch("uniclaw.webui.app.auth.create_user")
    @patch("uniclaw.webui.app.auth.user_exists", return_value=False)
    async def test_success(self, mock_exists, mock_create_user, mock_token):
        """注册成功返回 token。"""
        resp = await auth_register(_AuthRequest(username="u", password="password1"))
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["token"] == "token123"
        assert body["username"] == "u"
        mock_create_user.assert_called_once_with("u", "password1")
        assert "set-cookie" in resp.headers

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.create_user", side_effect=Exception("boom"))
    @patch("uniclaw.webui.app.auth.user_exists", return_value=False)
    async def test_exception(self, mock_exists, mock_create_user):
        """创建异常返回 500。"""
        resp = await auth_register(_AuthRequest(username="u", password="password1"))
        assert resp.status_code == 500
        assert "boom" in json.loads(resp.body)["detail"]


class TestAuthLogin:
    """auth_login 测试。"""

    def _req(self):
        return _make_request(path="/api/auth/login", client_ip="1.2.3.4")

    @pytest.mark.asyncio
    async def test_rate_limited(self):
        """被限流返回 429。"""
        limiter = MagicMock()
        limiter.check.return_value = 5.0
        with patch("uniclaw.webui.app.auth.login_rate_limiter", limiter):
            resp = await auth_login(_AuthRequest(username="u", password="p"), self._req())
        assert resp.status_code == 429
        body = json.loads(resp.body)
        assert body["retry_after"] == 5.0

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.user_exists", return_value=False)
    async def test_no_account(self, mock_exists):
        """无账号返回 400。"""
        limiter = MagicMock()
        limiter.check.return_value = None
        with patch("uniclaw.webui.app.auth.login_rate_limiter", limiter):
            resp = await auth_login(_AuthRequest(username="u", password="p"), self._req())
        assert resp.status_code == 400
        assert "不存在" in json.loads(resp.body)["detail"]

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.verify_user", return_value=False)
    @patch("uniclaw.webui.app.auth.user_exists", return_value=True)
    async def test_wrong_password(self, mock_exists, mock_verify):
        """密码错误返回 401 并记录失败。"""
        limiter = MagicMock()
        limiter.check.return_value = None
        with patch("uniclaw.webui.app.auth.login_rate_limiter", limiter):
            resp = await auth_login(_AuthRequest(username="u", password="wrong"), self._req())
        assert resp.status_code == 401
        assert "错误" in json.loads(resp.body)["detail"]
        limiter.record_failure.assert_called_once_with("1.2.3.4")

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.create_token", return_value="token123")
    @patch("uniclaw.webui.app.auth.verify_user", return_value=True)
    @patch("uniclaw.webui.app.auth.user_exists", return_value=True)
    async def test_success(self, mock_exists, mock_verify, mock_token):
        """登录成功返回 token 并清除失败记录。"""
        limiter = MagicMock()
        limiter.check.return_value = None
        with patch("uniclaw.webui.app.auth.login_rate_limiter", limiter):
            resp = await auth_login(_AuthRequest(username="u", password="p"), self._req())
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["token"] == "token123"
        assert body["username"] == "u"
        limiter.record_success.assert_called_once_with("1.2.3.4")
        assert "HttpOnly" in resp.headers["set-cookie"]


class TestAuthMe:
    """auth_me 测试。"""

    def _req(self, token: str | None = None):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return _make_request(path="/api/auth/me", headers=headers)

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.get_user")
    @patch("uniclaw.webui.app.auth.verify_token", return_value="admin")
    async def test_valid_token(self, mock_verify, mock_user):
        """有效 token 返回用户信息。"""
        mock_user.return_value = MagicMock(username="admin", created_at="2026-01-01")
        resp = await auth_me(self._req(token="valid"))
        assert resp == {"username": "admin", "created_at": "2026-01-01"}

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.verify_token", return_value=None)
    @patch("uniclaw.webui.app.auth.get_user")
    async def test_no_token_not_trusted(self, mock_user, mock_verify):
        """无 token 且非可信 IP 返回 401。"""
        mock_user.return_value = MagicMock(username="admin", created_at="x")
        with patch.object(webui_app, "_is_trusted_ip", return_value=False):
            resp = await auth_me(self._req())
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.verify_token", return_value=None)
    @patch("uniclaw.webui.app.auth.get_user")
    async def test_trusted_ip(self, mock_user, mock_verify):
        """可信 IP 无需 token。"""
        mock_user.return_value = MagicMock(username="admin", created_at="2026-01-01")
        with patch.object(webui_app, "_is_trusted_ip", return_value=True):
            resp = await auth_me(self._req())
        assert resp == {"username": "admin", "created_at": "2026-01-01", "trusted_ip": True}

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.verify_token", return_value=None)
    @patch("uniclaw.webui.app.auth.get_user", return_value=None)
    async def test_trusted_ip_no_user(self, mock_user, mock_verify):
        """可信 IP 但无用户返回 404。"""
        with patch.object(webui_app, "_is_trusted_ip", return_value=True):
            resp = await auth_me(self._req())
        assert resp.status_code == 404
        body = json.loads(resp.body)
        assert body["trusted_ip"] is True


class TestAuthChangePassword:
    """auth_change_password 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.user_exists", return_value=False)
    async def test_no_account(self, mock_exists):
        """无账号返回 400。"""
        resp = await auth_change_password(
            _ChangePwdRequest(new_password="password1"), _make_request()
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.user_exists", return_value=True)
    async def test_short_password(self, mock_exists):
        """密码过短返回 400。"""
        resp = await auth_change_password(
            _ChangePwdRequest(new_password="123"), _make_request()
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @patch("uniclaw.webui.app.auth.change_password")
    @patch("uniclaw.webui.app.auth.user_exists", return_value=True)
    async def test_success(self, mock_exists, mock_change):
        """修改成功。"""
        resp = await auth_change_password(
            _ChangePwdRequest(new_password="newpassword"), _make_request()
        )
        assert resp == {"ok": True}
        mock_change.assert_called_once_with("newpassword")


class TestAuthMiddleware:
    """auth_middleware 测试。"""

    @pytest.mark.asyncio
    async def test_whitelist_path(self):
        """白名单路径放行。"""
        req = _make_request(path="/api/auth/status")
        call_next = AsyncMock(return_value=Response(content=b"ok"))
        result = await auth_middleware(req, call_next)
        assert result.status_code == 200
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_login_page_whitelist(self):
        """登录页放行。"""
        req = _make_request(path="/login.html")
        call_next = AsyncMock(return_value=Response(content=b"ok"))
        result = await auth_middleware(req, call_next)
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_trusted_ip(self):
        """可信 IP 放行。"""
        req = _make_request(path="/api/some", client_ip="192.168.1.5")
        call_next = AsyncMock(return_value=Response(content=b"ok"))
        with patch.object(webui_app, "_is_trusted_ip", return_value=True):
            result = await auth_middleware(req, call_next)
        assert result.status_code == 200
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_valid_token_header(self):
        """有效 token 放行。"""
        req = _make_request(path="/api/some", headers={"Authorization": "Bearer valid"})
        call_next = AsyncMock(return_value=Response(content=b"ok"))
        with patch("uniclaw.webui.app.auth.verify_token", return_value="admin"):
            result = await auth_middleware(req, call_next)
        assert result.status_code == 200
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_valid_token_cookie(self):
        """Cookie 中的有效 token 放行。"""
        req = _make_request(path="/api/some", cookies={"uniclaw_token": "valid"})
        call_next = AsyncMock(return_value=Response(content=b"ok"))
        with patch("uniclaw.webui.app.auth.verify_token", return_value="admin"):
            result = await auth_middleware(req, call_next)
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_api_unauthorized_401(self):
        """未登录访问 API 返回 401。"""
        req = _make_request(path="/api/some")
        call_next = AsyncMock(return_value=Response(content=b"ok"))
        with patch("uniclaw.webui.app.auth.verify_token", return_value=None), patch.object(
            webui_app, "_is_trusted_ip", return_value=False
        ):
            result = await auth_middleware(req, call_next)
        assert result.status_code == 401
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_page_redirect(self):
        """未登录访问页面重定向登录页。"""
        req = _make_request(path="/")
        call_next = AsyncMock(return_value=Response(content=b"ok"))
        with patch("uniclaw.webui.app.auth.verify_token", return_value=None), patch.object(
            webui_app, "_is_trusted_ip", return_value=False
        ):
            result = await auth_middleware(req, call_next)
        assert result.status_code == 302
        assert result.headers["location"] == "/login.html"


class TestServeHtml:
    """_serve_html 测试。"""

    @patch("uniclaw.webui.app.is_encrypted", return_value=False)
    def test_plain(self, mock_enc, tmp_path):
        """未加密文件。"""
        f = tmp_path / "index.html"
        f.write_text("<html>hello</html>", encoding="utf-8")
        resp = _serve_html(f)
        assert "text/html" in resp.media_type
        assert b"hello" in resp.body

    @patch("uniclaw.webui.app.decrypt_data", side_effect=lambda d: b"<html>dec</html>")
    @patch("uniclaw.webui.app.is_encrypted", return_value=True)
    def test_encrypted(self, mock_enc, mock_dec, tmp_path):
        """加密文件解密后返回。"""
        f = tmp_path / "index.html"
        f.write_bytes(b"\x00\x01encrypted")
        resp = _serve_html(f)
        assert b"dec" in resp.body
        mock_dec.assert_called_once()
