"""静态文件加解密工具。

使用 Fernet 对称加密(AES-CBC + HMAC)保护前端静态文件,防止篡改。
加密后文件名不变,通过文件头魔数标记区分是否已加密。
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

# 加密目标扩展名
ENCRYPTABLE_EXTS = {".js", ".html"}

# 文件头魔数: 8 字节标记 + 1 字节版本号
_MAGIC = b"UCLAWENC"
_VERSION = b"\x01"
_HEADER = _MAGIC + _VERSION  # 9 bytes

# 固定密钥(分段存储,运行时拼接)
_KEY_PARTS = ["gh5qj47FyJY", "U7Cc8HuHIxZ", "0F0xu_cDZRP", "ns71VSOIgk="]
_KEY = "".join(_KEY_PARTS).encode()


def _get_fernet() -> Fernet:
    return Fernet(_KEY)


def is_encrypted(data: bytes) -> bool:
    """判断数据是否已加密(检查文件头魔数)。"""
    return data[: len(_MAGIC)] == _MAGIC


def encrypt_data(data: bytes) -> bytes:
    """加密数据。返回 魔数头 + Fernet token。"""
    if is_encrypted(data):
        return data  # 已加密,直接返回
    return _HEADER + _get_fernet().encrypt(data)


def decrypt_data(data: bytes) -> bytes:
    """解密数据。未加密的数据原样返回。失败抛出 InvalidToken。"""
    if not is_encrypted(data):
        return data  # 未加密,原样返回
    return _get_fernet().decrypt(data[len(_HEADER) :])


def encrypt_file(file_path) -> None:
    """加密单个文件(原地覆盖)。已加密的文件跳过。"""
    from pathlib import Path

    file_path = Path(file_path)
    data = file_path.read_bytes()
    if is_encrypted(data):
        return
    file_path.write_bytes(encrypt_data(data))


def decrypt_file(file_path) -> bytes:
    """解密文件并返回明文 bytes。未加密的文件原样返回。"""
    from pathlib import Path

    file_path = Path(file_path)
    data = file_path.read_bytes()
    return decrypt_data(data)
