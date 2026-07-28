"""
SQLite FTS5 全文检索索引。

使用 external-content 模式:memory_fts 存元数据 + body,
memory_fts_idx 是 FTS5 虚表,通过触发器自动同步。
"""

import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from uniclaw.utils import frontmatter

DB_FILENAME = "fts.db"
SCORE_FLOOR_RATIO = 0.15  # 相对分数下限:topScore × ratio


# ── 建库 ──────────────────────────────────────────────────


def _get_db_path(memory_dir: Path) -> Path:
    return memory_dir / DB_FILENAME


def _connect(memory_dir: Path) -> sqlite3.Connection:
    """打开(或创建)FTS5 数据库,确保表结构就绪。"""
    db_path = _get_db_path(memory_dir)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """建表 + FTS5 虚表 + 触发器(幂等)。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_fts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            scope TEXT NOT NULL,
            type TEXT NOT NULL,
            body TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            last_indexed_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS memory_fts_scope_idx ON memory_fts (scope);
        CREATE INDEX IF NOT EXISTS memory_fts_type_idx ON memory_fts (type);
    """)

    # FTS5 虚表(external content 模式)
    # 用 "IF NOT EXISTS" 避免重复创建报错
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts_idx USING fts5(
                body,
                content='memory_fts',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 1'
            )
        """)
    except sqlite3.OperationalError:
        pass  # 已存在

    # 触发器:INSERT / DELETE / UPDATE 自动同步 FTS5
    # DELETE 和 UPDATE 必须用 'delete' magic command,否则 token 会腐烂
    for name, sql in [
        (
            "memory_fts_ai",
            """CREATE TRIGGER IF NOT EXISTS memory_fts_ai AFTER INSERT ON memory_fts BEGIN
                INSERT INTO memory_fts_idx(rowid, body) VALUES (NEW.id, NEW.body);
            END""",
        ),
        (
            "memory_fts_ad",
            """CREATE TRIGGER IF NOT EXISTS memory_fts_ad AFTER DELETE ON memory_fts BEGIN
                INSERT INTO memory_fts_idx(memory_fts_idx, rowid, body) VALUES('delete', OLD.id, OLD.body);
            END""",
        ),
        (
            "memory_fts_au",
            """CREATE TRIGGER IF NOT EXISTS memory_fts_au AFTER UPDATE ON memory_fts BEGIN
                INSERT INTO memory_fts_idx(memory_fts_idx, rowid, body) VALUES('delete', OLD.id, OLD.body);
                INSERT INTO memory_fts_idx(rowid, body) VALUES (NEW.id, NEW.body);
            END""",
        ),
    ]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # 已存在

    conn.commit()


# ── Fingerprint ───────────────────────────────────────────


def _fingerprint(path: Path) -> str:
    """ "<file_size>-<mtime_ms>",轻量变更检测。"""
    stat = path.stat()
    return f"{stat.st_size}-{int(stat.st_mtime * 1000)}"


# ── 查询构建 ──────────────────────────────────────────────


def _build_query(query: str) -> Optional[str]:
    """将用户查询转为 FTS5 MATCH 表达式。

    每个 token 加双引号(防止特殊字符干扰),OR-join。
    返回 None 表示无有效 token。
    """
    tokens = re.findall(r"[\w]+", query, re.UNICODE)
    if not tokens:
        return None
    # 双引号包裹每个 token,OR 连接
    quoted = [f'"{t}"' for t in tokens]
    return " OR ".join(quoted)


# ── Reconcile ─────────────────────────────────────────────


def reconcile(memory_dirs: list[Path]) -> dict:
    """扫描磁盘记忆文件,增量同步到 FTS5 索引。

    Returns:
        {"indexed": int, "pruned": int, "skipped": int}
    """
    stats = {"indexed": 0, "pruned": 0, "skipped": 0}

    for memory_dir in memory_dirs:
        if not memory_dir.exists():
            continue

        conn = _connect(memory_dir)
        try:
            # 收集磁盘上的 .md 文件(排除 index 文件)
            disk_files: dict[str, Path] = {}
            for fp in memory_dir.glob("*.md"):
                if fp.name == "memory.md":
                    continue
                disk_files[str(fp.resolve())] = fp

            # 加载 DB 中已有的 path → fingerprint
            existing = {}
            for row in conn.execute("SELECT path, fingerprint FROM memory_fts"):
                existing[row[0]] = row[1]

            # Prune: DB 中有但磁盘上没有的
            for db_path in existing:
                if db_path not in disk_files:
                    conn.execute("DELETE FROM memory_fts WHERE path = ?", (db_path,))
                    stats["pruned"] += 1

            # Index: 磁盘上有但 DB 中没有的,或 fingerprint 变了的
            now = int(time.time())
            for path_str, fp in disk_files.items():
                fp_str = _fingerprint(fp)
                if existing.get(path_str) == fp_str:
                    stats["skipped"] += 1
                    continue

                # 读取文件,提取 body
                body = _read_body(fp)

                # UPSERT
                conn.execute(
                    """INSERT INTO memory_fts (path, scope, type, body, fingerprint, last_indexed_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(path) DO UPDATE SET
                           scope=excluded.scope,
                           type=excluded.type,
                           body=excluded.body,
                           fingerprint=excluded.fingerprint,
                           last_indexed_at=excluded.last_indexed_at""",
                    (
                        path_str,
                        _scope_of(fp, memory_dir),
                        _type_of(fp),
                        body,
                        fp_str,
                        now,
                    ),
                )
                stats["indexed"] += 1

            conn.commit()
        finally:
            conn.close()

    return stats


def _read_body(fp: Path) -> str:
    """读取记忆文件,返回 name + description + content 拼接。"""
    try:
        text = fp.read_text(encoding="utf-8")
        metadata, content = frontmatter.parse_frontmatter(text)
        name = metadata.get("name", "")
        desc = metadata.get("description", "")
        return f"{name} {desc} {content}"
    except Exception as e:
        logger.debug("解析 frontmatter 失败,使用原始文本: %s", e)
        return fp.read_text(encoding="utf-8")


def _scope_of(fp: Path, memory_dir: Optional[Path] = None) -> str:
    """从路径判断 scope:.UniClaw/memory 在项目下 → project,否则 → user。

    如果提供了 memory_dir,直接用它来判断(更可靠,尤其在测试环境下)。
    """
    # 优先用 memory_dir 判断(reconcile 时已知)
    if memory_dir is not None:
        # 检查 memory_dir 是否包含 .UniClaw 且父目录不是 home
        parts = memory_dir.resolve().parts
        for i, p in enumerate(parts):
            if p == ".UniClaw":
                parent = Path(*parts[:i]) if i > 0 else Path("/")
                try:
                    if parent.resolve() == Path.home().resolve():
                        return "user"
                except Exception as e:
                    logger.debug("解析路径失败: %s", e)
                return "project"
        return "user"

    # fallback: 从文件路径推断
    parts = fp.parts
    for i, p in enumerate(parts):
        if p == ".UniClaw" and i + 1 < len(parts) and parts[i + 1] == "memory":
            parent = Path(*parts[:i]) if i > 0 else Path("/")
            try:
                if parent.resolve() == Path.home().resolve():
                    return "user"
            except Exception as e:
                logger.debug("解析路径失败: %s", e)
            return "project"
    return "user"


def _type_of(fp: Path) -> str:
    """从 frontmatter 提取 type,默认 'user'。"""
    try:
        text = fp.read_text(encoding="utf-8")
        metadata, _ = frontmatter.parse_frontmatter(text)
        return metadata.get("type", "user")
    except Exception as e:
        logger.debug("从 frontmatter 提取 type 失败: %s", e)
        return "user"


# ── 单条同步 ──────────────────────────────────────────────


def index_memory(memory) -> None:
    """将单条记忆同步到 FTS5 索引(save_memory 后调用)。"""
    from .memory import Memory

    memory_dir = memory.filename.parent
    conn = _connect(memory_dir)
    try:
        body = f"{memory.name} {memory.description} {memory.content}"
        fp_str = _fingerprint(memory.filename)
        now = int(time.time())
        path_str = str(memory.filename.resolve())
        conn.execute(
            """INSERT INTO memory_fts (path, scope, type, body, fingerprint, last_indexed_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   scope=excluded.scope,
                   type=excluded.type,
                   body=excluded.body,
                   fingerprint=excluded.fingerprint,
                   last_indexed_at=excluded.last_indexed_at""",
            (path_str, memory.scope_name, memory.type, body, fp_str, now),
        )
        conn.commit()
    finally:
        conn.close()


def remove_memory(path: Path) -> None:
    """从 FTS5 索引中删除单条记忆(delete_memory 后调用)。"""
    memory_dir = path.parent
    if not _get_db_path(memory_dir).exists():
        return
    conn = _connect(memory_dir)
    try:
        path_str = str(path.resolve())
        conn.execute("DELETE FROM memory_fts WHERE path = ?", (path_str,))
        conn.commit()
    finally:
        conn.close()


# ── 搜索 ──────────────────────────────────────────────────


def fts_search(
    query: str,
    memory_dirs: list[Path],
    max_results: int = 5,
) -> list[dict]:
    """FTS5 BM25 搜索。

    Returns:
        [{"path": str, "scope": str, "type": str, "snippet": str, "score": float}, ...]
        score 已取反(higher = better),已应用相对分数下限。
    """
    fts_query = _build_query(query)
    if not fts_query:
        return []

    # 先 reconcile 确保索引最新
    reconcile(memory_dirs)

    all_results: list[dict] = []
    fetch_limit = min(max_results * 3, 50)  # 过取 3x 用于分数下限过滤

    for memory_dir in memory_dirs:
        db_path = _get_db_path(memory_dir)
        if not db_path.exists():
            continue

        conn = _connect(memory_dir)
        try:
            rows = conn.execute(
                """SELECT
                       memory_fts.path,
                       memory_fts.scope,
                       memory_fts.type,
                       snippet(memory_fts_idx, 0, '<<', '>>', '...', 32) AS snippet,
                       bm25(memory_fts_idx) AS score
                   FROM memory_fts_idx
                   JOIN memory_fts ON memory_fts.id = memory_fts_idx.rowid
                   WHERE memory_fts_idx MATCH ?
                   ORDER BY score
                   LIMIT ?""",
                (fts_query, fetch_limit),
            ).fetchall()

            for row in rows:
                # FTS5 bm25: lower = better(内部为负数),取反使 higher = better
                all_results.append(
                    {
                        "path": row[0],
                        "scope": row[1],
                        "type": row[2],
                        "snippet": row[3],
                        "score": -row[4],
                    }
                )
        finally:
            conn.close()

    if not all_results:
        return []

    # 按 score 降序排列
    all_results.sort(key=lambda r: r["score"], reverse=True)

    # 相对分数下限:保留 topScore × ratio 以上的结果
    top_score = all_results[0]["score"]
    if top_score > 0:
        cutoff = top_score * SCORE_FLOOR_RATIO
        filtered = [
            r for r in all_results if r["score"] >= cutoff or r is all_results[0]
        ]
    else:
        filtered = all_results

    return filtered[:max_results]
