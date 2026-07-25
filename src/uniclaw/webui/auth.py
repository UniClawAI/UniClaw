"""WebUI 认证模块:单账号登录 + JWT。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import jwt

from uniclaw.utils.logger import get_logger
from uniclaw.context import get_app_dir, Scope

# ── 常量 ──────────────────────────────────────────────────────────────────

_DB_DIR = get_app_dir(Scope.USER) / "auth"
_DB_PATH = _DB_DIR / "auth.db"
_JWT_SECRET: str | None = None  # 首次使用时从环境或随机生成
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_HOURS = 24
_TOKEN_HEADER = "Authorization"
_TOKEN_PREFIX = "Bearer "

logger = get_logger("webui.auth", Path.cwd())


# ── 登录限流 ──────────────────────────────────────────────────────────────


@dataclass
class _IpRecord:
    """单个 IP 的登录失败记录。"""

    fail_count: int = 0
    expire_time: float = 0.0


class LoginRateLimiter:
    """基于 IP 的登录失败限流器。

    策略:首次失败后等待 1 秒,每次递增 1.5 倍(1s, 1.5s, 2.25s, 3.375s, ...)。
    成功登录后清除该 IP 的失败记录。
    """

    _BASE_DELAY: float = 1.0
    _MULTIPLIER: float = 1.5

    def __init__(self) -> None:
        self._records: dict[str, _IpRecord] = {}

    def check(self, ip: str) -> Optional[float]:
        """检查 IP 是否被限流。

        Returns:
            None 表示放行,否则返回需要等待的秒数。
        """
        self.cleanup()
        rec = self._records.get(ip)
        if not rec:
            return None
        remaining = rec.expire_time - time.monotonic()
        if remaining > 0:
            return round(remaining, 2)
        return None

    def record_failure(self, ip: str) -> None:
        """记录一次登录失败。"""
        rec = self._records.get(ip)
        if rec is None:
            rec = _IpRecord()
            self._records[ip] = rec
        rec.fail_count += 1
        delay = self._BASE_DELAY * (self._MULTIPLIER ** (rec.fail_count - 1))
        rec.expire_time = time.monotonic() + delay
        logger.warning(f"IP {ip} 登录失败 {rec.fail_count} 次,下次需等待 {delay:.1f}s")

    def record_success(self, ip: str) -> None:
        """登录成功,清除该 IP 的失败记录。"""
        self._records.pop(ip, None)

    def cleanup(self) -> None:
        """清理所有等待期已过的 IP 记录。"""
        now = time.monotonic()
        expired = [ip for ip, rec in self._records.items() if now >= rec.expire_time]
        for ip in expired:
            del self._records[ip]


# 全局单例
login_rate_limiter = LoginRateLimiter()


# ── 数据结构 ──────────────────────────────────────────────────────────────


@dataclass
class User:
    id: int
    username: str
    created_at: str


# ── 数据库 ────────────────────────────────────────────────────────────────


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接(自动建表)。"""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    return conn


def user_exists() -> bool:
    """是否已有账号。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) FROM user").fetchone()
        return row[0] > 0
    finally:
        conn.close()


def create_user(username: str, password: str) -> User:
    """创建账号(仅允许一个)。"""
    if user_exists():
        raise ValueError("账号已存在")
    salt = os.urandom(32).hex()
    pw_hash = _hash_password(password, salt)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO user (id, username, password_hash, salt) VALUES (1, ?, ?, ?)",
            (username, pw_hash, salt),
        )
        conn.commit()
        logger.info(f"WebUI 账号已创建: {username}")
        return User(id=1, username=username, created_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    finally:
        conn.close()


def verify_user(username: str, password: str) -> bool:
    """验证凭据。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT username, password_hash, salt FROM user LIMIT 1"
        ).fetchone()
        if not row:
            return False
        db_user, db_hash, salt = row
        if db_user != username:
            return False
        return _hash_password(password, salt) == db_hash
    finally:
        conn.close()


def get_user() -> Optional[User]:
    """获取当前用户信息。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, username, created_at FROM user LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return User(id=row[0], username=row[1], created_at=row[2])
    finally:
        conn.close()


def change_password(new_password: str) -> None:
    """修改密码。"""
    salt = os.urandom(32).hex()
    pw_hash = _hash_password(new_password, salt)
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE user SET password_hash = ?, salt = ? WHERE id = (SELECT id FROM user LIMIT 1)",
            (pw_hash, salt),
        )
        conn.commit()
        logger.info("WebUI 密码已修改")
    finally:
        conn.close()


# ── 密码哈希 ──────────────────────────────────────────────────────────────


def _hash_password(password: str, salt: str) -> str:
    """PBKDF2-SHA256 哈希密码。"""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=100_000,
    ).hex()


# ── JWT ───────────────────────────────────────────────────────────────────


def _get_jwt_secret() -> str:
    """获取 JWT 密钥(首次随机生成,持久化到文件)。"""
    global _JWT_SECRET
    if _JWT_SECRET:
        return _JWT_SECRET

    secret_file = _DB_DIR / ".jwt_secret"
    if secret_file.exists():
        _JWT_SECRET = secret_file.read_text(encoding="utf-8").strip()
    else:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        _JWT_SECRET = os.urandom(32).hex()
        secret_file.write_text(_JWT_SECRET, encoding="utf-8")
    return _JWT_SECRET


def create_token(username: str) -> str:
    """签发 JWT。"""
    payload = {
        "sub": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + _JWT_EXPIRE_HOURS * 3600,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=_JWT_ALGORITHM)


def verify_token(token: str) -> Optional[str]:
    """验证 JWT,返回 username 或 None。"""
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[_JWT_ALGORITHM])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ── 导出符号 ──────────────────────────────────────────────────────────────

__all__ = [
    "User",
    "user_exists",
    "create_user",
    "verify_user",
    "get_user",
    "change_password",
    "create_token",
    "verify_token",
    "login_rate_limiter",
    "_DB_PATH",
    "_JWT_ALGORITHM",
    "_JWT_EXPIRE_HOURS",
    "_TOKEN_HEADER",
    "_TOKEN_PREFIX",
]
