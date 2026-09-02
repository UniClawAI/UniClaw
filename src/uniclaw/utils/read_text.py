"""文本文件读取工具

提供统一的文本文件读取函数,包含二进制检测和编码探测。
二进制检测策略: 文件内容包含 NUL 字节(\\x00)即视为二进制。
"""

import codecs
from pathlib import Path

# UTF BOM → 标准编码名映射
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

# 编码中天然含 NUL 字节的编码(UTF-16/32)
_NUL_SAFE_ENCODINGS = frozenset({"utf-16", "utf-32"})

# 最长 BOM 长度(UTF-32, 4 字节):
# 凑够最长 BOM 再判断,避免把 UTF-32 LE 的 BOM 前缀(FF FE)误判为 UTF-16 LE
_MAX_BOM_LEN = max(len(bom) for bom, _ in _BOMS)


def _detect_bom(data: bytes) -> str | None:
    """根据文件头 BOM 推断编码,无 BOM 时返回 None。

    BOM 前缀互不重叠,startswith 依次匹配即可,
    UTF-32 LE 的 FF FE 00 00 不会被 UTF-16 LE 的 FF FE 误命中。
    """
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return encoding
    return None


def read_text_file(path: Path | str) -> str | None:
    """读取文本文件,自动检测编码。

    递增分块读取(128 字节起,指数增长,上限 100MB),边读边扫描:
    先按 BOM 识别编码(UTF-16/32 含大量 NUL,需豁免二进制检测);
    无 BOM 时检测 NUL 字节,发现即判为二进制并提前返回 None。

    Detection order:
        1. BOM 检测: 命中则按对应编码解码(UTF-8/UTF-16/UTF-32,含大小端)
        2. NUL 检测: 无 BOM 的文件含 NUL 字节(\\x00) → 判为二进制,返回 None
        3. 编码探测: 依次尝试 UTF-8 → GBK → Latin-1 解码
        4. 兜底: UTF-8 errors=replace 解码

    Args:
        path: 文件路径

    Returns:
        str: 解码后的文本内容
        None: 文件为二进制或无法读取
    """
    try:
        f = Path(path).open("rb")
    except (PermissionError, OSError, FileNotFoundError):
        return None

    try:
        # 递增分块读取: 128 → 256 → 512 ... 上限 100MB
        chunk_size = 128
        max_chunk = 100 << 20  # 100MB
        data = bytearray()
        encoding: str | None = None
        bom_checked = False

        with f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                data += chunk

                # BOM 检测需在 NUL 检测之前:
                # UTF-16/UTF-32 文件含大量 NUL,但应按 BOM 编码解码而非判为二进制
                # 凑够最长 BOM 长度后仅判断一次
                if not bom_checked and len(data) >= _MAX_BOM_LEN:
                    bom_checked = True
                    encoding = _detect_bom(bytes(data[: _MAX_BOM_LEN]))

                # UTF-16/UTF-32 编码天然含 NUL 字节,跳过 NUL 检测
                if encoding not in _NUL_SAFE_ENCODINGS and b"\x00" in chunk:
                    return None

                # 分块大小始终递增,不受编码分支影响
                chunk_size = min(chunk_size * 2, max_chunk)
    except (PermissionError, OSError):
        return None

    data = bytes(data)

    # 空文件: 无有效内容,视为不可读
    if not data:
        return None

    # 1. BOM 编码解码
    if encoding is not None:
        return data.decode(encoding)

    # 2. 编码探测: 依次尝试常见编码
    for candidate in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(candidate)
        except UnicodeDecodeError:
            continue

    # 3. 兜底: UTF-8 replace 模式
    return data.decode("utf-8", errors="replace")