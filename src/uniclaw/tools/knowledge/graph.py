"""知识图谱引擎 — SQLite-backed knowledge graph with entity resolution."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeGraph:
    """SQLite-backed knowledge graph engine.

    存储位置: `.UniClaw/knowledge.db`
    支持: 实体/关系 CRUD、别名管理、FTS5 模糊搜索、路径查找、可视化导出。
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── 连接管理 ──────────────────────────────────────────────

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _init_db(self):
        """初始化数据库 schema。"""
        c = self.conn
        c.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'concept',
                description TEXT,
                properties TEXT,
                source TEXT DEFAULT 'manual',
                confidence REAL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(name, type)
            );

            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                target_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                relation TEXT NOT NULL,
                properties TEXT,
                weight REAL DEFAULT 1.0,
                source TEXT DEFAULT 'manual',
                confidence REAL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                UNIQUE(source_id, target_id, relation)
            );

            CREATE TABLE IF NOT EXISTS entity_aliases (
                entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                alias TEXT NOT NULL,
                PRIMARY KEY (entity_id, alias)
            );

            CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
            CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
            CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id);
            CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id);
            CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation);
            CREATE INDEX IF NOT EXISTS idx_aliases_alias ON entity_aliases(alias);
        """)

        # FTS5 虚拟表(单独创建,不放在 executescript 中以避免 content= 引用问题)
        try:
            c.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(name, description, content=entities, content_rowid=id)"
            )
        except sqlite3.OperationalError:
            pass  # 已存在

        # FTS5 同步触发器
        for trigger_sql in [
            """CREATE TRIGGER IF NOT EXISTS entities_ai AFTER INSERT ON entities BEGIN
                INSERT INTO entities_fts(rowid, name, description) VALUES (new.id, new.name, new.description);
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_ad AFTER DELETE ON entities BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, description) VALUES('delete', old.id, old.name, old.description);
            END""",
            """CREATE TRIGGER IF NOT EXISTS entities_au AFTER UPDATE ON entities BEGIN
                INSERT INTO entities_fts(entities_fts, rowid, name, description) VALUES('delete', old.id, old.name, old.description);
                INSERT INTO entities_fts(rowid, name, description) VALUES (new.id, new.name, new.description);
            END""",
        ]:
            try:
                c.execute(trigger_sql)
            except sqlite3.OperationalError:
                pass

        c.commit()

    # ── 实体 CRUD ─────────────────────────────────────────────

    def add_entity(
        self,
        name: str,
        entity_type: str = "concept",
        description: str = "",
        properties: dict | None = None,
        source: str = "manual",
        confidence: float = 1.0,
    ) -> dict:
        """添加实体,返回 {id, duplicate_warning}。"""
        now = _now()
        props_json = json.dumps(properties, ensure_ascii=False) if properties else None

        # 检查重复
        duplicates = self.check_duplicate(name, entity_type)

        try:
            c = self.conn.execute(
                "INSERT INTO entities (name, type, description, properties, source, confidence, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, entity_type, description, props_json, source, confidence, now, now),
            )
            self.conn.commit()
            entity_id = c.lastrowid
            warning = None
            if duplicates:
                dup_names = [d["name"] for d in duplicates[:3]]
                warning = f"发现相似实体: {', '.join(dup_names)}。如果是同一实体,请使用 kg_add_alias 添加别名。"
            return {"id": entity_id, "duplicate_warning": warning}
        except sqlite3.IntegrityError:
            # 已存在,返回已有实体
            row = self.conn.execute(
                "SELECT id FROM entities WHERE name=? AND type=?", (name, entity_type)
            ).fetchone()
            return {"id": row["id"], "duplicate_warning": f"实体 '{name}' (type={entity_type}) 已存在。"}

    def add_relation(
        self,
        source_name: str,
        target_name: str,
        relation: str,
        source_type: str = "",
        target_type: str = "",
        properties: dict | None = None,
        weight: float = 1.0,
        source: str = "manual",
        confidence: float = 1.0,
    ) -> dict:
        """添加关系。自动通过名称/别名查找实体。"""
        src = self._resolve_entity(source_name, source_type)
        tgt = self._resolve_entity(target_name, target_type)

        if not src:
            return {"error": f"源实体 '{source_name}' 不存在。请先使用 kg_add_entity 创建。"}
        if not tgt:
            return {"error": f"目标实体 '{target_name}' 不存在。请先使用 kg_add_entity 创建。"}

        now = _now()
        props_json = json.dumps(properties, ensure_ascii=False) if properties else None

        try:
            c = self.conn.execute(
                "INSERT INTO relations (source_id, target_id, relation, properties, weight, source, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (src["id"], tgt["id"], relation, props_json, weight, source, confidence, now),
            )
            self.conn.commit()
            return {"id": c.lastrowid, "source": src["name"], "target": tgt["name"], "relation": relation}
        except sqlite3.IntegrityError:
            return {"error": f"关系已存在: {source_name} --[{relation}]--> {target_name}"}

    def add_alias(self, name: str, entity_type: str, alias: str) -> dict:
        """为实体添加别名。"""
        entity = self._resolve_entity(name, entity_type)
        if not entity:
            return {"error": f"实体 '{name}' 不存在。"}

        # 检查别名是否已指向其他实体
        existing = self.conn.execute(
            "SELECT e.id, e.name FROM entity_aliases a JOIN entities e ON a.entity_id=e.id WHERE a.alias=?",
            (alias,),
        ).fetchone()
        if existing:
            return {"error": f"别名 '{alias}' 已指向实体 '{existing['name']}'。"}

        try:
            self.conn.execute(
                "INSERT INTO entity_aliases (entity_id, alias) VALUES (?, ?)",
                (entity["id"], alias),
            )
            self.conn.commit()
            return {"ok": True, "entity": entity["name"], "alias": alias}
        except sqlite3.IntegrityError:
            return {"error": f"别名 '{alias}' 已存在于实体 '{name}'。"}

    def update_entity(self, name: str, entity_type: str = "", **kwargs) -> dict:
        """更新实体属性。"""
        entity = self._resolve_entity(name, entity_type)
        if not entity:
            return {"error": f"实体 '{name}' 不存在。"}

        allowed = {"description", "properties", "confidence", "type"}
        updates = {}
        for k, v in kwargs.items():
            if k in allowed:
                if k == "properties":
                    v = json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v
                updates[k] = v

        if not updates:
            return {"error": "无有效更新字段。"}

        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [entity["id"]]
        self.conn.execute(f"UPDATE entities SET {set_clause} WHERE id=?", values)
        self.conn.commit()
        return {"ok": True, "entity": entity["name"], "updated": list(updates.keys())}

    def delete_entity(self, name: str, entity_type: str = "") -> dict:
        """删除实体(级联删除关系和别名)。"""
        entity = self._resolve_entity(name, entity_type)
        if not entity:
            return {"error": f"实体 '{name}' 不存在。"}

        self.conn.execute("DELETE FROM entities WHERE id=?", (entity["id"],))
        self.conn.commit()
        return {"ok": True, "deleted": entity["name"]}

    def delete_relation(self, source_name: str, target_name: str, relation: str) -> dict:
        """删除关系。"""
        src = self._resolve_entity(source_name)
        tgt = self._resolve_entity(target_name)
        if not src or not tgt:
            return {"error": "源实体或目标实体不存在。"}

        c = self.conn.execute(
            "DELETE FROM relations WHERE source_id=? AND target_id=? AND relation=?",
            (src["id"], tgt["id"], relation),
        )
        self.conn.commit()
        if c.rowcount == 0:
            return {"error": f"关系不存在: {source_name} --[{relation}]--> {target_name}"}
        return {"ok": True, "deleted": f"{source_name} --[{relation}]--> {target_name}"}

    def merge_entities(self, source_name: str, target_name: str, source_type: str = "", target_type: str = "") -> dict:
        """合并两个实体:将 source 的关系、别名、属性转移到 target,然后删除 source。

        处理规则:
        - 关系:转移时跳过自环(source→target 已有关系)和重复关系
        - 别名:source 的别名转移到 target,冲突时跳过
        - 属性:source 的属性合并到 target,不覆盖 target 已有字段
        - 描述:若 target 无描述而 source 有,继承 source 的描述
        """
        src = self._resolve_entity(source_name, source_type)
        if not src:
            return {"error": f"实体 '{source_name}' 不存在。"}

        tgt = self._resolve_entity(target_name, target_type)
        if not tgt:
            return {"error": f"实体 '{target_name}' 不存在。"}

        if src["id"] == tgt["id"]:
            return {"error": f"'{source_name}' 和 '{target_name}' 是同一个实体。"}

        now = _now()
        transferred_rels = 0
        skipped_rels = 0
        transferred_aliases = 0
        skipped_aliases = 0

        # ── 1. 转移关系 ──────────────────────────────────────
        # 出边:source → X 改为 target → X
        for row in self.conn.execute(
            "SELECT * FROM relations WHERE source_id=?", (src["id"],)
        ).fetchall():
            old = dict(row)
            new_target_id = old["target_id"] if old["target_id"] != src["id"] else tgt["id"]
            # 自环检查:合并后 target → target
            if new_target_id == tgt["id"]:
                skipped_rels += 1
                continue
            try:
                self.conn.execute(
                    "INSERT INTO relations (source_id, target_id, relation, properties, weight, source, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (tgt["id"], new_target_id, old["relation"], old["properties"], old["weight"], old["source"], old["confidence"], now),
                )
                transferred_rels += 1
            except sqlite3.IntegrityError:
                skipped_rels += 1  # 重复关系,跳过

        # 入边:X → source 改为 X → target
        for row in self.conn.execute(
            "SELECT * FROM relations WHERE target_id=?", (src["id"],)
        ).fetchall():
            old = dict(row)
            new_source_id = old["source_id"] if old["source_id"] != src["id"] else tgt["id"]
            # 自环检查
            if new_source_id == tgt["id"]:
                skipped_rels += 1
                continue
            try:
                self.conn.execute(
                    "INSERT INTO relations (source_id, target_id, relation, properties, weight, source, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_source_id, tgt["id"], old["relation"], old["properties"], old["weight"], old["source"], old["confidence"], now),
                )
                transferred_rels += 1
            except sqlite3.IntegrityError:
                skipped_rels += 1

        # ── 2. 转移别名 ──────────────────────────────────────
        # source 的名称本身也作为 target 的别名
        try:
            self.conn.execute(
                "INSERT INTO entity_aliases (entity_id, alias) VALUES (?, ?)",
                (tgt["id"], src["name"]),
            )
            transferred_aliases += 1
        except sqlite3.IntegrityError:
            skipped_aliases += 1

        for row in self.conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id=?", (src["id"],)
        ).fetchall():
            try:
                self.conn.execute(
                    "INSERT INTO entity_aliases (entity_id, alias) VALUES (?, ?)",
                    (tgt["id"], row["alias"]),
                )
                transferred_aliases += 1
            except sqlite3.IntegrityError:
                skipped_aliases += 1

        # ── 3. 合并属性 ──────────────────────────────────────
        merged_props = False
        src_props = json.loads(src["properties"]) if src.get("properties") else {}
        tgt_props = json.loads(tgt["properties"]) if tgt.get("properties") else {}
        if src_props:
            new_props = {**src_props, **tgt_props}  # target 优先
            if new_props != tgt_props:
                self.conn.execute(
                    "UPDATE entities SET properties=?, updated_at=? WHERE id=?",
                    (json.dumps(new_props, ensure_ascii=False), now, tgt["id"]),
                )
                merged_props = True

        # ── 4. 继承描述 ──────────────────────────────────────
        inherited_desc = False
        if not tgt.get("description") and src.get("description"):
            self.conn.execute(
                "UPDATE entities SET description=?, updated_at=? WHERE id=?",
                (src["description"], now, tgt["id"]),
            )
            inherited_desc = True

        self.conn.commit()

        # ── 5. 删除 source 实体(级联删除剩余关系和别名) ────
        self.conn.execute("DELETE FROM entities WHERE id=?", (src["id"],))
        self.conn.commit()

        return {
            "ok": True,
            "source": src["name"],
            "target": tgt["name"],
            "transferred_relations": transferred_rels,
            "skipped_relations": skipped_rels,
            "transferred_aliases": transferred_aliases,
            "skipped_aliases": skipped_aliases,
            "merged_properties": merged_props,
            "inherited_description": inherited_desc,
        }

    # ── 查询 ──────────────────────────────────────────────────

    def get_entity(self, name: str, entity_type: str = "") -> dict | None:
        """获取实体详情(含别名和关系)。"""
        entity = self._resolve_entity(name, entity_type)
        if not entity:
            return None

        # 获取别名
        aliases = [
            r["alias"]
            for r in self.conn.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id=?", (entity["id"],)
            ).fetchall()
        ]

        # 获取关系
        relations = self._get_entity_relations(entity["id"])

        result = dict(entity)
        if result.get("properties"):
            result["properties"] = json.loads(result["properties"])
        result["aliases"] = aliases
        result["relations"] = relations
        return result

    def search_entities(self, keyword: str, entity_type: str = "", limit: int = 20) -> list[dict]:
        """FTS5 模糊搜索实体。"""
        if entity_type:
            rows = self.conn.execute(
                """SELECT e.* FROM entities_fts f
                   JOIN entities e ON f.rowid = e.id
                   WHERE entities_fts MATCH ? AND e.type = ?
                   ORDER BY rank LIMIT ?""",
                (keyword, entity_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT e.* FROM entities_fts f
                   JOIN entities e ON f.rowid = e.id
                   WHERE entities_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (keyword, limit),
            ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            if d.get("properties"):
                d["properties"] = json.loads(d["properties"])
            d["aliases"] = [
                r["alias"]
                for r in self.conn.execute(
                    "SELECT alias FROM entity_aliases WHERE entity_id=?", (d["id"],)
                ).fetchall()
            ]
            results.append(d)
        return results

    def get_neighbors(self, name: str, depth: int = 1, entity_type: str = "", relation_type: str = "") -> list[dict]:
        """获取实体的邻居(支持多跳遍历)。"""
        entity = self._resolve_entity(name, entity_type)
        if not entity:
            return []

        visited = {entity["id"]}
        current_level = [entity["id"]]
        results = []

        for d in range(depth):
            next_level = []
            for eid in current_level:
                # 出边
                query = "SELECT r.relation, r.weight, e.* FROM relations r JOIN entities e ON r.target_id=e.id WHERE r.source_id=?"
                params = [eid]
                if relation_type:
                    query += " AND r.relation=?"
                    params.append(relation_type)

                for row in self.conn.execute(query, params).fetchall():
                    if row["id"] not in visited:
                        visited.add(row["id"])
                        next_level.append(row["id"])
                        d_row = dict(row)
                        if d_row.get("properties"):
                            d_row["properties"] = json.loads(d_row["properties"])
                        d_row["depth"] = d + 1
                        d_row["direction"] = "outgoing"
                        results.append(d_row)

                # 入边
                query = "SELECT r.relation, r.weight, e.* FROM relations r JOIN entities e ON r.source_id=e.id WHERE r.target_id=?"
                params = [eid]
                if relation_type:
                    query += " AND r.relation=?"
                    params.append(relation_type)

                for row in self.conn.execute(query, params).fetchall():
                    if row["id"] not in visited:
                        visited.add(row["id"])
                        next_level.append(row["id"])
                        d_row = dict(row)
                        if d_row.get("properties"):
                            d_row["properties"] = json.loads(d_row["properties"])
                        d_row["depth"] = d + 1
                        d_row["direction"] = "incoming"
                        results.append(d_row)

            current_level = next_level

        return results

    def find_path(self, source_name: str, target_name: str, max_depth: int = 5) -> list[list[dict]]:
        """BFS 查找两个实体之间的所有路径(最大深度内)。"""
        src = self._resolve_entity(source_name)
        tgt = self._resolve_entity(target_name)
        if not src or not tgt:
            return []

        # BFS
        paths = []
        queue = [[src["id"]]]

        while queue:
            path = queue.pop(0)
            if len(path) > max_depth + 1:
                continue

            current = path[-1]
            if current == tgt["id"] and len(path) > 1:
                # 转换为实体路径
                entity_path = []
                for i, eid in enumerate(path):
                    row = self.conn.execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()
                    d = dict(row) if row else {"id": eid, "name": "?"}
                    if d.get("properties"):
                        d["properties"] = json.loads(d["properties"])
                    if i > 0:
                        # 添加关系信息
                        rel = self.conn.execute(
                            "SELECT relation FROM relations WHERE source_id=? AND target_id=?",
                            (path[i - 1], eid),
                        ).fetchone()
                        if not rel:
                            rel = self.conn.execute(
                                "SELECT relation FROM relations WHERE source_id=? AND target_id=?",
                                (eid, path[i - 1]),
                            ).fetchone()
                        d["via_relation"] = rel["relation"] if rel else "?"
                    entity_path.append(d)
                paths.append(entity_path)
                if len(paths) >= 10:  # 最多返回 10 条路径
                    break
                continue

            # 扩展邻居
            for row in self.conn.execute(
                "SELECT target_id FROM relations WHERE source_id=? UNION SELECT source_id FROM relations WHERE target_id=?",
                (current, current),
            ).fetchall():
                neighbor = row["target_id"] if "target_id" in row.keys() else row[0]
                if neighbor not in path:
                    queue.append(path + [neighbor])

        return paths

    def check_duplicate(self, name: str, entity_type: str = "") -> list[dict]:
        """检查是否存在相似实体,返回候选列表。"""
        candidates = []

        # 1. 精确匹配别名
        alias_match = self.conn.execute(
            "SELECT e.* FROM entity_aliases a JOIN entities e ON a.entity_id=e.id WHERE a.alias=?",
            (name,),
        ).fetchone()
        if alias_match:
            d = dict(alias_match)
            if d.get("properties"):
                d["properties"] = json.loads(d["properties"])
            d["match_type"] = "alias"
            candidates.append(d)

        # 2. FTS5 模糊搜索
        try:
            fts_matches = self.conn.execute(
                "SELECT e.* FROM entities_fts f JOIN entities e ON f.rowid=e.id WHERE entities_fts MATCH ?",
                (name,),
            ).fetchall()
            for m in fts_matches:
                d = dict(m)
                if d.get("properties"):
                    d["properties"] = json.loads(d["properties"])
                if d["id"] not in [c["id"] for c in candidates]:
                    d["match_type"] = "fts"
                    candidates.append(d)
        except sqlite3.OperationalError:
            pass

        return candidates

    def list_entities(self, entity_type: str = "", limit: int = 100) -> list[dict]:
        """列出实体。"""
        if entity_type:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE type=? ORDER BY updated_at DESC LIMIT ?",
                (entity_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM entities ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            if d.get("properties"):
                d["properties"] = json.loads(d["properties"])
            d["aliases"] = [
                r["alias"]
                for r in self.conn.execute(
                    "SELECT alias FROM entity_aliases WHERE entity_id=?", (d["id"],)
                ).fetchall()
            ]
            results.append(d)
        return results

    def get_stats(self) -> dict:
        """获取图谱统计信息。"""
        entity_count = self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        relation_count = self.conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        alias_count = self.conn.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0]

        type_dist = {}
        for row in self.conn.execute("SELECT type, COUNT(*) as cnt FROM entities GROUP BY type ORDER BY cnt DESC").fetchall():
            type_dist[row["type"]] = row["cnt"]

        relation_dist = {}
        for row in self.conn.execute("SELECT relation, COUNT(*) as cnt FROM relations GROUP BY relation ORDER BY cnt DESC").fetchall():
            relation_dist[row["relation"]] = row["cnt"]

        return {
            "entities": entity_count,
            "relations": relation_count,
            "aliases": alias_count,
            "entity_types": type_dist,
            "relation_types": relation_dist,
        }

    # ── 导出 ──────────────────────────────────────────────────

    def export_json(self) -> dict:
        """导出图谱为 JSON。"""
        entities = self.list_entities(limit=10000)
        relations = []
        for row in self.conn.execute("SELECT * FROM relations").fetchall():
            d = dict(row)
            if d.get("properties"):
                d["properties"] = json.loads(d["properties"])
            # 添加实体名称
            src = self.conn.execute("SELECT name FROM entities WHERE id=?", (d["source_id"],)).fetchone()
            tgt = self.conn.execute("SELECT name FROM entities WHERE id=?", (d["target_id"],)).fetchone()
            d["source_name"] = src["name"] if src else "?"
            d["target_name"] = tgt["name"] if tgt else "?"
            relations.append(d)

        return {"entities": entities, "relations": relations}

    def export_markdown(self) -> str:
        """导出图谱为 Markdown。"""
        stats = self.get_stats()
        lines = [f"# 知识图谱", ""]
        lines.append(f"实体: {stats['entities']} | 关系: {stats['relations']} | 别名: {stats['aliases']}")
        lines.append("")

        # 实体列表
        lines.append("## 实体")
        for entity in self.list_entities(limit=10000):
            aliases = entity.get("aliases", [])
            alias_str = f" (别名: {', '.join(aliases)})" if aliases else ""
            lines.append(f"- **{entity['name']}** [{entity['type']}]{alias_str}")
            if entity.get("description"):
                lines.append(f"  {entity['description']}")
        lines.append("")

        # 关系列表
        lines.append("## 关系")
        for row in self.conn.execute(
            """SELECT s.name as src, r.relation, t.name as tgt
               FROM relations r
               JOIN entities s ON r.source_id=s.id
               JOIN entities t ON r.target_id=t.id"""
        ).fetchall():
            lines.append(f"- {row['src']} --[{row['relation']}]--> {row['tgt']}")

        return "\n".join(lines)

    # ── 可视化 ────────────────────────────────────────────────

    def visualize(self, output_path: Path, highlight_entities: list[str] | None = None) -> Path:
        """生成交互式 HTML 知识图谱可视化(ECharts 力导向图)。"""
        import json as _json
        import hashlib
        from collections import defaultdict

        # 预定义颜色和图标
        type_colors = {
            "person": "#5470C6",
            "place": "#91CC75",
            "concept": "#FAC858",
            "event": "#EE6666",
            "tool": "#73C0DE",
            "organization": "#3BA272",
            "document": "#9A60B4",
            "technology": "#FC8452",
        }
        type_icons = {
            "person": "👤", "place": "📍", "concept": "💡", "event": "📌",
            "tool": "🔧", "organization": "🏢", "document": "📄", "technology": "⚙️",
        }

        # 颜色池: 为未预定义的类型动态分配颜色
        _color_pool = [
            "#5470C6", "#91CC75", "#FAC858", "#EE6666", "#73C0DE",
            "#3BA272", "#9A60B4", "#FC8452", "#6E79D5", "#C6A35E",
            "#5AB8DB", "#D4896B", "#8B7EC8", "#59C4A0", "#E88DB5",
            "#A0A0E0", "#D4A037", "#6EB5E0", "#C47D9B", "#7DD3B0",
        ]

        def _auto_color(type_name: str) -> str:
            h = int(hashlib.md5(type_name.encode()).hexdigest()[:8], 16)
            return _color_pool[h % len(_color_pool)]

        highlight_set = set(highlight_entities or [])
        entities = self.list_entities(limit=5000)

        # 统计每个实体的关系数量(用于节点大小)
        rel_count: dict[str, int] = defaultdict(int)
        for row in self.conn.execute(
            "SELECT s.name as src, t.name as tgt FROM relations r JOIN entities s ON r.source_id=s.id JOIN entities t ON r.target_id=t.id"
        ).fetchall():
            rel_count[row["src"]] += 1
            rel_count[row["tgt"]] += 1
        max_rel = max(rel_count.values()) if rel_count else 1

        # 构建节点(大小按关系数量缩放,颜色由 category 控制)
        nodes = []
        type_set: set[str] = set()
        for e in entities:
            type_set.add(e["type"])
        type_index = {t: i for i, t in enumerate(sorted(type_set))}

        # 检测同名实体,为重复名称生成带类型后缀的唯一 ID
        from collections import Counter
        name_counts = Counter(e["name"] for e in entities)
        # name → {type → id} 映射,用于关系引用时查找
        name_type_id: dict[str, dict[str, str]] = defaultdict(dict)

        for e in entities:
            icon = type_icons.get(e["type"], "●")
            count = rel_count.get(e["name"], 0)
            size = 10 + (count / max_rel) * 25 if max_rel > 0 else 12  # 10~35
            tooltip_parts = [f"{icon} {e['name']}", f"类型: {e['type']}", f"关系数: {count}"]
            if e.get("description"):
                tooltip_parts.append(f"描述: {e['description']}")
            if e.get("aliases"):
                tooltip_parts.append(f"别名: {', '.join(e['aliases'])}")

            # 同名实体用 "name (type)" 作为唯一 ID
            node_id = f"{e['name']} ({e['type']})" if name_counts[e["name"]] > 1 else e["name"]
            name_type_id[e["name"]][e["type"]] = node_id

            node = {
                "id": node_id,
                "name": e["name"],
                "symbolSize": round(size),
                "value": count,
                "category": type_index[e["type"]],
                "label": {"show": True},
                "tooltip": "<br/>".join(tooltip_parts),
                "type": e["type"],
            }
            if e["name"] in highlight_set:
                node["itemStyle"] = {"borderWidth": 3, "shadowBlur": 10, "shadowColor": type_colors.get(e["type"]) or _auto_color(e["type"])}
            nodes.append(node)

        # 构建边(同一对节点的多条关系用不同曲率,数量越多间距越大)
        # 查询时同时获取类型,用于正确引用去重后的节点 ID
        # 分组时用排序节点对,确保 A→B 和 B→A 被归为同一组以正确计算曲率
        edge_groups: dict[tuple, list] = defaultdict(list)
        for row in self.conn.execute(
            "SELECT s.name as src, s.type as src_type, t.name as tgt, t.type as tgt_type, r.relation "
            "FROM relations r JOIN entities s ON r.source_id=s.id JOIN entities t ON r.target_id=t.id"
        ).fetchall():
            src_id = name_type_id.get(row["src"], {}).get(row["src_type"], row["src"])
            tgt_id = name_type_id.get(row["tgt"], {}).get(row["tgt_type"], row["tgt"])
            pair = tuple(sorted([src_id, tgt_id]))
            # 记录边方向是否与排序对一致(用于曲率符号修正)
            reversed_dir = (src_id, tgt_id) != pair
            edge_groups[pair].append({
                "source": src_id, "target": tgt_id,
                "relation": row["relation"], "reversed": reversed_dir,
            })

        links = []
        for _pair, edges in edge_groups.items():
            count = len(edges)
            # 曲率范围随关系数量扩大,让多条线充分分开
            spread = min(0.8 + (count - 2) * 0.2, 3.0) if count > 1 else 0
            for i, edge in enumerate(edges):
                curveness = 0.0
                if count > 1:
                    curveness = -spread + (i / (count - 1)) * spread * 2
                # ECharts 中反向边的曲率效果也会反转,需取反以确保不同边弯向不同侧
                if edge["reversed"]:
                    curveness = -curveness
                src, tgt, rel = edge["source"], edge["target"], edge["relation"]
                links.append({
                    "source": src,
                    "target": tgt,
                    "name": rel,
                    "label": {"show": True, "formatter": rel, "fontSize": 13, "color": "#222", "position": "middle"},
                    "lineStyle": {"curveness": curveness, "opacity": 0.7},
                    "tooltip": f"{src} → {tgt}: {rel}",
                })

        # 图例数据(带颜色,和节点颜色一致)
        categories = [{
            "name": t,
            "itemStyle": {"color": type_colors.get(t) or _auto_color(t)},
        } for t in sorted(type_set)]

        # 生成 HTML
        nodes_json = _json.dumps(nodes, ensure_ascii=False, indent=2)
        links_json = _json.dumps(links, ensure_ascii=False, indent=2)
        categories_json = _json.dumps(categories, ensure_ascii=False, indent=2)

        # 构建图例 HTML
        legend_items = []
        for t in sorted(type_set):
            color = type_colors.get(t) or _auto_color(t)
            icon = type_icons.get(t, "●")
            legend_items.append(
                f'<span class="legend-item" data-type="{t}" style="cursor:pointer;display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:4px;transition:background .15s;">'
                f'<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{color};"></span>'
                f'{icon} {t}</span>'
            )
        legend_html = " ".join(legend_items)

        html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>知识图谱</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8f9fa; }}
#header {{
    background: #fff; border-bottom: 1px solid #e0e0e0; padding: 10px 20px;
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
}}
#header h1 {{ font-size: 18px; color: #333; font-weight: 600; }}
#header .stats {{ font-size: 13px; color: #888; }}
#search {{
    padding: 5px 10px; border: 1px solid #ddd; border-radius: 6px;
    font-size: 13px; width: 200px; outline: none; transition: border-color .2s;
}}
#search:focus {{ border-color: #5470C6; }}
#search::placeholder {{ color: #bbb; }}
#legend {{
    display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
    font-size: 12px; color: #555;
}}
.legend-item:hover {{ background: #f0f0f0; }}
.legend-item.active {{ background: #e8f0fe; font-weight: 600; }}
.legend-item.dimmed {{ opacity: 0.35; }}
#chart {{ width: 100%; height: calc(100vh - 48px); }}
</style>
</head>
<body>
<div id="header">
    <h1>知识图谱</h1>
    <span class="stats">实体 {len(nodes)} · 关系 {len(links)}</span>
    <input id="search" type="text" placeholder="搜索实体...">
    <div id="legend">{legend_html}</div>
</div>
<div id="chart"></div>
<div id="load-error" style="display:none;padding:40px;text-align:center;color:#c00;font-size:15px;"></div>
<script src="https://unpkg.com/echarts@5/dist/echarts.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.1/echarts.min.js"></script>
<script>
if (typeof echarts === 'undefined') {{
    document.getElementById('load-error').textContent = '⚠️ ECharts 加载失败(unpkg),尝试备用CDN...';
    document.getElementById('load-error').style.display = 'block';
}} else {{
try {{
var chart = echarts.init(document.getElementById('chart'));
var nodesData = {nodes_json};
var linksData = {links_json};
var categories = {categories_json};

nodesData.forEach(function(n) {{ n.draggable = true; }});

// 构建类型→节点索引映射
var typeIndices = {{}};
nodesData.forEach(function(n, i) {{
    if (!typeIndices[n.type]) typeIndices[n.type] = [];
    typeIndices[n.type].push(i);
}});

// 隐藏类型集合
var hiddenTypes = {{}};

var option = {{
    tooltip: {{
        trigger: 'item',
        backgroundColor: 'rgba(255,255,255,0.95)',
        borderColor: '#e0e0e0',
        borderWidth: 1,
        textStyle: {{ color: '#333', fontSize: 13 }},
        extraCssText: 'max-width:320px; white-space:pre-wrap;',
        formatter: function(p) {{
            if (p.dataType === 'node') return p.data.tooltip;
            if (p.dataType === 'edge') return p.data.tooltip;
            return '';
        }}
    }},
    legend: {{ show: false }},
    animationDuration: 600,
    animationEasingUpdate: 'quinticInOut',
    series: [{{
        type: 'graph',
        layout: 'force',
        data: nodesData,
        links: linksData,
        categories: categories,
        roam: true,
        draggable: true,
        label: {{
            show: true,
            position: 'right',
            fontSize: 12,
            color: '#333',
        }},
        edgeLabel: {{
            fontSize: 13,
            color: '#222',
        }},
        force: {{
            repulsion: 500,
            gravity: 0.08,
            edgeLength: [150, 350],
            friction: 0.7,
            layoutAnimation: true,
        }},
        lineStyle: {{
            color: '#aaa',
            width: 1.5,
            opacity: 0.7,
        }},
        emphasis: {{
            focus: 'adjacency',
            itemStyle: {{ shadowBlur: 12, shadowColor: 'rgba(0,0,0,0.3)' }},
            lineStyle: {{ width: 3 }},
        }},
    }}]
}};
chart.setOption(option);
window.addEventListener('resize', function() {{ chart.resize(); }});

// 自定义图例交互
var legendItems = document.querySelectorAll('.legend-item');

function highlightType(typeName) {{
    nodesData.forEach(function(n) {{
        n.itemStyle = n.itemStyle || {{}};
        if (n.type === typeName) {{
            n.itemStyle.opacity = 1;
            n.label = n.label || {{}};
            n.label.show = true;
        }} else {{
            n.itemStyle.opacity = 0.15;
            n.label = n.label || {{}};
            n.label.show = false;
        }}
    }});
    linksData.forEach(function(l) {{
        var srcNode = nodesData.find(function(n) {{ return n.name === l.source; }});
        var tgtNode = nodesData.find(function(n) {{ return n.name === l.target; }});
        var match = (srcNode && srcNode.type === typeName) || (tgtNode && tgtNode.type === typeName);
        l.lineStyle = l.lineStyle || {{}};
        l.lineStyle.opacity = match ? 0.9 : 0.05;
    }});
    chart.setOption({{ series: [{{ data: nodesData, links: linksData }}] }});
}}

function clearHighlight() {{
    nodesData.forEach(function(n) {{
        n.itemStyle = n.itemStyle || {{}};
        n.itemStyle.opacity = hiddenTypes[n.type] ? 0 : 1;
        n.label = n.label || {{}};
        n.label.show = !hiddenTypes[n.type];
    }});
    linksData.forEach(function(l) {{
        var srcNode = nodesData.find(function(n) {{ return n.name === l.source; }});
        var tgtNode = nodesData.find(function(n) {{ return n.name === l.target; }});
        var hidden = (srcNode && hiddenTypes[srcNode.type]) || (tgtNode && hiddenTypes[tgtNode.type]);
        l.lineStyle = l.lineStyle || {{}};
        l.lineStyle.opacity = hidden ? 0 : 0.7;
    }});
    chart.setOption({{ series: [{{ data: nodesData, links: linksData }}] }});
}}

legendItems.forEach(function(item) {{
    // 悬停: 高亮对应类型
    item.addEventListener('mouseenter', function() {{
        var typeName = this.getAttribute('data-type');
        highlightType(typeName);
        legendItems.forEach(function(el) {{
            el.classList.toggle('dimmed', el.getAttribute('data-type') !== typeName);
        }});
    }});
    // 移出: 恢复
    item.addEventListener('mouseleave', function() {{
        clearHighlight();
        legendItems.forEach(function(el) {{
            el.classList.remove('dimmed');
        }});
    }});
    // 点击: 切换显示/隐藏
    item.addEventListener('click', function() {{
        var typeName = this.getAttribute('data-type');
        if (hiddenTypes[typeName]) {{
            delete hiddenTypes[typeName];
            this.classList.remove('active');
        }} else {{
            hiddenTypes[typeName] = true;
            this.classList.add('active');
        }}
        clearHighlight();
    }});
}});

// 搜索高亮
document.getElementById('search').addEventListener('input', function(e) {{
    var q = e.target.value.trim().toLowerCase();
    if (!q) {{
        nodesData.forEach(function(n) {{
            if (!n.itemStyle) n.itemStyle = {{}};
            n.itemStyle.opacity = hiddenTypes[n.type] ? 0 : 1;
            n.label = n.label || {{}};
            n.label.show = !hiddenTypes[n.type];
        }});
        linksData.forEach(function(l) {{ l.lineStyle.opacity = 0.7; }});
    }} else {{
        var matched = {{}};
        nodesData.forEach(function(n) {{
            if (n.name.toLowerCase().indexOf(q) >= 0) matched[n.name] = true;
        }});
        nodesData.forEach(function(n) {{
            var hit = matched[n.name];
            if (!n.itemStyle) n.itemStyle = {{}};
            n.itemStyle.opacity = (hit && !hiddenTypes[n.type]) ? 1 : 0.1;
            n.label = n.label || {{}};
            n.label.show = hit && !hiddenTypes[n.type];
        }});
        linksData.forEach(function(l) {{
            l.lineStyle.opacity = (matched[l.source] || matched[l.target]) ? 0.8 : 0.05;
        }});
    }}
    chart.setOption({{ series: [{{ data: nodesData, links: linksData }}] }});
}});
}} catch(e) {{
    document.getElementById('load-error').textContent = '⚠️ 图表渲染错误: ' + e.message;
    document.getElementById('load-error').style.display = 'block';
}}
}}
</script>
</body>
</html>"""

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        return output_path

    # ── 辅助方法 ──────────────────────────────────────────────

    def _resolve_entity(self, name: str, entity_type: str = "") -> dict | None:
        """通过名称或别名查找实体。"""
        # 1. 精确匹配名称
        if entity_type:
            row = self.conn.execute(
                "SELECT * FROM entities WHERE name=? AND type=?", (name, entity_type)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM entities WHERE name=?", (name,)
            ).fetchone()

        if row:
            return dict(row)

        # 2. 匹配别名
        row = self.conn.execute(
            "SELECT e.* FROM entity_aliases a JOIN entities e ON a.entity_id=e.id WHERE a.alias=?",
            (name,),
        ).fetchone()
        if row:
            return dict(row)

        return None

    def _get_entity_relations(self, entity_id: int) -> list[dict]:
        """获取实体的所有关系。"""
        relations = []

        # 出边
        for row in self.conn.execute(
            """SELECT r.relation, r.weight, r.properties, e.name as target_name, e.type as target_type
               FROM relations r JOIN entities e ON r.target_id=e.id
               WHERE r.source_id=?""",
            (entity_id,),
        ).fetchall():
            d = dict(row)
            d["direction"] = "outgoing"
            if d.get("properties"):
                d["properties"] = json.loads(d["properties"])
            relations.append(d)

        # 入边
        for row in self.conn.execute(
            """SELECT r.relation, r.weight, r.properties, e.name as source_name, e.type as source_type
               FROM relations r JOIN entities e ON r.source_id=e.id
               WHERE r.target_id=?""",
            (entity_id,),
        ).fetchall():
            d = dict(row)
            d["direction"] = "incoming"
            if d.get("properties"):
                d["properties"] = json.loads(d["properties"])
            relations.append(d)

        return relations
