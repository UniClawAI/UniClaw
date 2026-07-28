"""静态文件加密脚本。

用法:
    uv run python -m uniclaw.webui.encrypt_static          # 加密(原地覆盖)
    uv run python -m uniclaw.webui.encrypt_static --decrypt # 解密(恢复明文)

加密后文件名不变,通过文件头魔数标记区分是否已加密。
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from uniclaw.webui.crypto import (
    ENCRYPTABLE_EXTS,
    decrypt_data,
    encrypt_data,
    is_encrypted,
)

# Windows 控制台 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# 静态文件目录(默认 dist 构建输出)
# 向上查找 pyproject.toml 定位项目根目录
def _find_project_root() -> Path:
    """向上查找 pyproject.toml 定位项目根目录。"""
    p = Path(__file__).resolve().parent
    for _ in range(10):
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    # fallback: 4 层父目录
    return Path(__file__).resolve().parents[4]


_PROJECT_ROOT = _find_project_root()
STATIC_DIR = _PROJECT_ROOT / "dist" / "src" / "uniclaw" / "webui" / "static"


def collect_files(directory: Path) -> list[Path]:
    """递归收集可加密的文件。"""
    files: list[Path] = []
    for p in sorted(directory.rglob("*")):
        if p.is_file() and p.suffix in ENCRYPTABLE_EXTS:
            files.append(p)
    return files


def cmd_encrypt() -> None:
    """加密静态文件(原地覆盖)。"""
    files = collect_files(STATIC_DIR)

    if not files:
        print("没有找到可加密的文件。")
        return

    print(f"找到 {len(files)} 个文件:")
    skipped = 0
    for f in files:
        rel = f.relative_to(STATIC_DIR)
        data = f.read_bytes()
        if is_encrypted(data):
            skipped += 1
            continue
        encrypted = encrypt_data(data)
        f.write_bytes(encrypted)
        print(f"  [OK] {rel}")

    if skipped:
        print(f"\n加密完成({skipped} 个已加密的文件跳过)。")
    else:
        print("\n加密完成。")


def cmd_decrypt() -> None:
    """解密静态文件(恢复明文)。"""
    files = collect_files(STATIC_DIR)

    if not files:
        print("没有找到可解密的文件。")
        return

    print(f"找到 {len(files)} 个文件:")
    skipped = 0
    for f in files:
        rel = f.relative_to(STATIC_DIR)
        data = f.read_bytes()
        if not is_encrypted(data):
            skipped += 1
            continue
        decrypted = decrypt_data(data)
        f.write_bytes(decrypted)
        print(f"  [OK] {rel}")

    if skipped:
        print(f"\n解密完成({skipped} 个未加密的文件跳过)。")
    else:
        print("\n解密完成。")


def main() -> None:
    parser = argparse.ArgumentParser(description="UniClaw 静态文件加密工具")
    parser.add_argument(
        "--decrypt",
        action="store_true",
        help="解密模式(恢复明文)",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="指定静态文件目录(默认: webui/static)",
    )
    args = parser.parse_args()

    global STATIC_DIR
    if args.dir:
        STATIC_DIR = Path(args.dir).resolve()

    if not STATIC_DIR.exists():
        print(f"错误: 目录不存在 {STATIC_DIR}", file=sys.stderr)
        sys.exit(1)

    if args.decrypt:
        cmd_decrypt()
    else:
        cmd_encrypt()


if __name__ == "__main__":
    main()
