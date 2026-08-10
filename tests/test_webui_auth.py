"""WebUI 认证模块测试 — 覆盖登录限流、密码哈希、JWT 和账号管理。"""

import pytest
from pathlib import Path
from unittest.mock import patch

from uniclaw.webui import auth


@pytest.fixture
def isolated_db(tmp_path):
    """将认证数据库隔离到临时目录。"""
    with patch("uniclaw.webui.auth._DB_DIR", tmp_path / "auth"), patch(
        "uniclaw.webui.auth._DB_PATH", tmp_path / "auth" / "auth.db"
    ):
        yield


@pytest.fixture
def fixed_jwt():
    """固定 JWT 密钥(≥32 字节避免警告)。"""
    with patch("uniclaw.webui.auth._JWT_SECRET", "t" * 32):
        yield


class TestHashPassword:
    """密码哈希测试。"""

    def test_deterministic(self):
        """相同输入相同哈希。"""
        h1 = auth._hash_password("secret", "salt123")
        h2 = auth._hash_password("secret", "salt123")
        assert h1 == h2

    def test_different_salt(self):
        """不同 salt 不同哈希。"""
        h1 = auth._hash_password("secret", "salt1")
        h2 = auth._hash_password("secret", "salt2")
        assert h1 != h2

    def test_different_password(self):
        """不同密码不同哈希。"""
        h1 = auth._hash_password("secret1", "salt")
        h2 = auth._hash_password("secret2", "salt")
        assert h1 != h2

    def test_hex_length(self):
        """输出为 64 位十六进制。"""
        h = auth._hash_password("secret", "salt")
        assert len(h) == 64


class TestJWT:
    """JWT 签发与验证测试。"""

    def test_roundtrip(self, fixed_jwt):
        """签发后可验证出用户名。"""
        token = auth.create_token("admin")
        assert auth.verify_token(token) == "admin"

    def test_invalid_token(self, fixed_jwt):
        """无效 token 返回 None。"""
        assert auth.verify_token("not-a-token") is None

    def test_tampered_token(self, fixed_jwt):
        """被篡改的 token 返回 None。"""
        token = auth.create_token("admin")
        tampered = token[:-4] + "xxxx"
        assert auth.verify_token(tampered) is None

    def test_empty_token(self, fixed_jwt):
        """空 token 返回 None。"""
        assert auth.verify_token("") is None

    def test_expired_token(self, fixed_jwt):
        """过期 token 返回 None。"""
        import jwt as pyjwt
        import time

        payload = {"sub": "admin", "iat": int(time.time()) - 100000, "exp": 1}
        token = pyjwt.encode(payload, "t" * 32, algorithm="HS256")
        assert auth.verify_token(token) is None

    def test_secret_persisted(self, tmp_path, monkeypatch):
        """密钥生成后持久化到文件。"""
        secret_file = tmp_path / ".jwt_secret"
        with patch("uniclaw.webui.auth._JWT_SECRET", None), patch(
            "uniclaw.webui.auth._DB_DIR", tmp_path
        ):
            s1 = auth._get_jwt_secret()
            s2 = auth._get_jwt_secret()
        assert s1 == s2
        assert secret_file.exists()
        assert secret_file.read_text(encoding="utf-8").strip() == s1


class TestLoginRateLimiter:
    """登录限流测试。"""

    def test_no_record_allows(self):
        """无记录时放行。"""
        limiter = auth.LoginRateLimiter()
        assert limiter.check("1.2.3.4") is None

    def test_failure_blocks(self):
        """失败后检查返回等待秒数。"""
        limiter = auth.LoginRateLimiter()
        with patch(
            "uniclaw.webui.auth.time.monotonic",
            side_effect=[100.0, 100.5, 100.5],
        ):
            limiter.record_failure("1.2.3.4")
            remaining = limiter.check("1.2.3.4")
        assert remaining is not None
        assert remaining > 0

    def test_failure_expired_allows(self):
        """失败超过等待期后放行。"""
        limiter = auth.LoginRateLimiter()
        with patch(
            "uniclaw.webui.auth.time.monotonic",
            side_effect=[100.0, 100.5, 102.0],
        ):
            limiter.record_failure("1.2.3.4")
            remaining = limiter.check("1.2.3.4")
        assert remaining is None

    def test_success_clears_record(self):
        """成功登录清除失败记录。"""
        limiter = auth.LoginRateLimiter()
        with patch("uniclaw.webui.auth.time.monotonic", side_effect=[100.0, 100.1, 100.2]):
            limiter.record_failure("1.2.3.4")
            limiter.record_success("1.2.3.4")
            remaining = limiter.check("1.2.3.4")
        assert remaining is None

    def test_cleanup_removes_expired(self):
        """清理过期记录。"""
        limiter = auth.LoginRateLimiter()
        with patch("uniclaw.webui.auth.time.monotonic", side_effect=[100.0, 200.0]):
            limiter.record_failure("1.2.3.4")
            limiter.cleanup()
        assert "1.2.3.4" not in limiter._records

    def test_fail_count_increments(self):
        """失败计数递增且等待时间递增。"""
        limiter = auth.LoginRateLimiter()
        with patch(
            "uniclaw.webui.auth.time.monotonic",
            side_effect=[100.0, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6],
        ):
            limiter.record_failure("ip")
            limiter.record_failure("ip")
            r1 = limiter.check("ip")
            limiter.record_failure("ip")
            r2 = limiter.check("ip")
        assert r2 > r1

    def test_cleanup_keeps_active(self):
        """清理保留未过期的记录。"""
        limiter = auth.LoginRateLimiter()
        with patch("uniclaw.webui.auth.time.monotonic", side_effect=[100.0, 100.1, 100.5]):
            limiter.record_failure("ip")
            limiter.cleanup()
        assert "ip" in limiter._records


class TestUserDB:
    """账号管理测试。"""

    def test_user_exists_false_initially(self, isolated_db):
        """初始无账号。"""
        assert auth.user_exists() is False

    def test_create_user(self, isolated_db):
        """创建账号。"""
        user = auth.create_user("admin", "password123")
        assert user.id == 1
        assert user.username == "admin"
        assert auth.user_exists() is True

    def test_create_user_duplicate(self, isolated_db):
        """重复创建账号报错。"""
        auth.create_user("admin", "password123")
        with pytest.raises(ValueError):
            auth.create_user("admin2", "password456")

    def test_verify_user_correct(self, isolated_db):
        """正确密码验证通过。"""
        auth.create_user("admin", "password123")
        assert auth.verify_user("admin", "password123") is True

    def test_verify_user_wrong_password(self, isolated_db):
        """错误密码验证失败。"""
        auth.create_user("admin", "password123")
        assert auth.verify_user("admin", "wrong") is False

    def test_verify_user_wrong_username(self, isolated_db):
        """错误用户名验证失败。"""
        auth.create_user("admin", "password123")
        assert auth.verify_user("other", "password123") is False

    def test_verify_user_no_account(self, isolated_db):
        """无账号时验证失败。"""
        assert auth.verify_user("admin", "password123") is False

    def test_get_user(self, isolated_db):
        """获取用户信息。"""
        auth.create_user("admin", "password123")
        user = auth.get_user()
        assert user is not None
        assert user.username == "admin"
        assert user.id == 1

    def test_get_user_none(self, isolated_db):
        """无账号时返回 None。"""
        assert auth.get_user() is None

    def test_change_password(self, isolated_db):
        """修改密码后新密码生效。"""
        auth.create_user("admin", "oldpassword")
        auth.change_password("newpassword")
        assert auth.verify_user("admin", "oldpassword") is False
        assert auth.verify_user("admin", "newpassword") is True

    def test_create_user_hashes_password(self, isolated_db):
        """密码以哈希存储,不存明文。"""
        auth.create_user("admin", "password123")
        import sqlite3

        conn = sqlite3.connect(str(auth._DB_PATH))
        try:
            row = conn.execute(
                "SELECT password_hash, salt FROM user LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] != "password123"
        assert len(row[0]) == 64
