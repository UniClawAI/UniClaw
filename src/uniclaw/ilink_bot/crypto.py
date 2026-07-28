from __future__ import annotations

import base64
import hashlib
import os
import re

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HEX_16_BYTES = re.compile(r"^[0-9a-fA-F]{32}$")
BLOCK_SIZE = 16


def generate_aes_key() -> bytes:
    return os.urandom(16)


def decode_aes_key(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        if len(value) == 16:
            return value
        value = value.decode("ascii")

    if HEX_16_BYTES.match(value):
        return bytes.fromhex(value)

    decoded = base64.b64decode(value)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32 and HEX_16_BYTES.match(
        decoded.decode("ascii", errors="ignore")
    ):
        return bytes.fromhex(decoded.decode("ascii"))
    raise ValueError("AES key must decode to exactly 16 bytes")


def encode_aes_key(key: bytes) -> str:
    """Encode key as base64(hex) for CDNMedia.aes_key."""
    if len(key) != 16:
        raise ValueError("AES key must be 16 bytes")
    return base64.b64encode(key.hex().encode("utf-8")).decode("ascii")


def encrypt_aes_ecb(data: bytes, key: bytes | str) -> bytes:
    raw_key = decode_aes_key(key) if isinstance(key, str) else key
    encryptor = Cipher(algorithms.AES(raw_key), modes.ECB()).encryptor()
    return encryptor.update(_pkcs7_pad(data)) + encryptor.finalize()


def decrypt_aes_ecb(data: bytes, key: bytes | str) -> bytes:
    raw_key = decode_aes_key(key) if isinstance(key, str) else key
    decryptor = Cipher(algorithms.AES(raw_key), modes.ECB()).decryptor()
    return _pkcs7_unpad(decryptor.update(data) + decryptor.finalize())


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def encrypted_size(raw_size: int) -> int:
    pad = BLOCK_SIZE - (raw_size % BLOCK_SIZE)
    if pad == 0:
        pad = BLOCK_SIZE
    return raw_size + pad


def _pkcs7_pad(data: bytes) -> bytes:
    pad = BLOCK_SIZE - (len(data) % BLOCK_SIZE)
    if pad == 0:
        pad = BLOCK_SIZE
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % BLOCK_SIZE != 0:
        raise ValueError("Invalid PKCS7 payload length")
    pad = data[-1]
    if pad < 1 or pad > BLOCK_SIZE or data[-pad:] != bytes([pad]) * pad:
        raise ValueError("Invalid PKCS7 padding")
    return data[:-pad]
