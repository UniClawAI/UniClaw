"""文档加载器 — 支持多种格式的文件读取。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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


def load_directory(path: Path, recursive: bool = True) -> list[Document]:
    """加载目录下的所有支持格式文件。

    Args:
        path: 目录路径
        recursive: 是否递归子目录

    Returns:
        文档列表
    """
    docs = []
    pattern = "**/*" if recursive else "*"
    for file_path in sorted(path.glob(pattern)):
        if not file_path.is_file():
            continue
        # 跳过隐藏文件和隐藏目录
        if any(part.startswith(".") for part in file_path.relative_to(path).parts):
            continue
        if file_path.suffix.lower() in TEXT_EXTENSIONS or file_path.suffix.lower() == ".pdf":
            try:
                docs.extend(load_file(file_path))
            except (ValueError, Exception):
                continue  # 跳过无法读取的文件
    return docs


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
