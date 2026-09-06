"""文档加载器 — 支持多种格式的文件读取。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from uniclaw.utils.gitignore import get_not_ignored_files

from uniclaw.utils.read_text import read_text_file

# 支持的文本文件扩展名
TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".json", ".yaml", ".yml", ".csv",
    ".html", ".htm", ".xml", ".css", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".rb",
    ".sh", ".bash", ".zsh", ".fish", ".bat", ".cmd", ".ps1",
    ".toml", ".ini", ".cfg", ".conf", ".env", ".gitignore",
    ".dockerfile", ".makefile", ".sql", ".r", ".lua", ".swift",
    ".kt", ".scala", ".hs", ".ex", ".exs", ".erl", ".clj",
    ".vue", ".svelte", ".astro",
}


@dataclass
class Document:
    """加载后的文档对象。"""
    content: str
    metadata: dict = field(default_factory=dict)


def load_file(path: Path) -> list[Document]:
    """加载单个文件,返回文档列表(通常为单个文档)。

    Args:
        path: 文件路径

    Returns:
        文档列表

    Raises:
        ValueError: 不支持的文件格式
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(path)
    if suffix in TEXT_EXTENSIONS:
        return _load_text(path)
    raise ValueError(f"不支持的文件格式: {suffix}")


def load_directory(path: Path, recursive: bool = True) -> tuple[list[Document], int]:
    """加载目录下的所有支持格式文件。

    逐级向上查找 .gitignore 过滤文件和目录,无任何 .gitignore 时使用
    兜底规则(隐藏文件 + __pycache__/node_modules 等常见垃圾目录)。

    Args:
        path: 目录路径
        recursive: 是否递归子目录

    Returns:
        (文档列表, 跳过的无法读取文件数)
    """
    docs = []
    skipped = 0
    pattern = "**/*" if recursive else "*"
    candidates = [f for f in sorted(path.glob(pattern)) if f.is_file()]
    # 批量按 .gitignore 规则过滤(覆盖隐藏文件和依赖/构建目录)
    candidates = get_not_ignored_files(candidates)
    for file_path in candidates:
        if file_path.suffix.lower() in TEXT_EXTENSIONS or file_path.suffix.lower() == ".pdf":
            try:
                docs.extend(load_file(file_path))
            except Exception:
                skipped += 1  # 跳过无法读取的文件,计数不静默丢弃
    return docs, skipped


def _load_text(path: Path) -> list[Document]:
    """加载文本文件。"""
    content = read_text_file(path)
    if content is None:
        return []
    return [Document(
        content=content,
        metadata={"source": str(path), "filename": path.name, "suffix": path.suffix},
    )]


def _load_pdf(path: Path) -> list[Document]:
    """加载 PDF 文件。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("需要安装 pypdf 依赖: uv add pypdf")

    reader = PdfReader(str(path))
    docs = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text and text.strip():
            docs.append(Document(
                content=text.strip(),
                metadata={
                    "source": str(path),
                    "filename": path.name,
                    "suffix": ".pdf",
                    "page": i + 1,
                },
            ))
    return docs
