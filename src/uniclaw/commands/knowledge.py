"""知识图谱命令 — /kg stats, search, list, export, clear。"""

from __future__ import annotations
import json
from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn

# 子命令列表
SUBCOMMANDS = ["stats", "search", "list", "export", "clear"]


def _get_graphs(config: AppConfig, scope: str = ""):
    """根据 scope 获取图谱实例列表。scope 为空时返回两层。"""
    from uniclaw.tools.knowledge.graph import KnowledgeGraph
    from uniclaw.context import Scope, get_app_dir

    scopes = []
    if scope == "user":
        scopes.append(("用户级", Scope.USER))
    elif scope == "project":
        if config.root_dir:
            scopes.append(("项目级", config.root_dir))
        else:
            scopes.append(("用户级", Scope.USER))
    else:
        scopes.append(("用户级", Scope.USER))
        if config.root_dir:
            scopes.append(("项目级", config.root_dir))

    result = []
    for label, s in scopes:
        db_path = get_app_dir(s) / "knowledge.db"
        result.append((label, db_path, KnowledgeGraph(db_path)))
    return result


async def cmd_knowledge(args: str, config: AppConfig) -> bool:
    """知识图谱管理命令

    支持以下功能:
    - 无参数:显示图谱统计信息(用户级+项目级)
    - stats:显示图谱统计信息
    - search <关键词>:搜索实体
    - list [类型]:列出实体
    - export html|json|markdown [user|project]:导出图谱
    - clear [user|project]:清空图谱(需确认)

    Args:
        args: 命令参数
        config: 配置对象

    Returns:
        bool: 始终返回 True 表示命令执行完成
    """
    query = args.strip()

    if not query or query == "stats":
        graphs = _get_graphs(config)
        try:
            await _cmd_stats(graphs, config)
        finally:
            for _, _, g in graphs:
                g.close()
        return True

    parts = query.split(None, 1)
    subcmd = parts[0].lower()
    subargs = parts[1] if len(parts) > 1 else ""

    if subcmd == "search":
        if not subargs:
            await warn("用法: /kg search <关键词>", config)
            return True
        graphs = _get_graphs(config)
        try:
            await _cmd_search(graphs, subargs, config)
        finally:
            for _, _, g in graphs:
                g.close()
        return True

    if subcmd == "list":
        graphs = _get_graphs(config)
        try:
            await _cmd_list(graphs, subargs, config)
        finally:
            for _, _, g in graphs:
                g.close()
        return True

    if subcmd == "export":
        export_parts = subargs.split(None, 1)
        fmt = export_parts[0].lower() if export_parts else "markdown"
        scope = export_parts[1].lower() if len(export_parts) > 1 else ""
        graphs = _get_graphs(config, scope)
        try:
            await _cmd_export(graphs, fmt, config)
        finally:
            for _, _, g in graphs:
                g.close()
        return True

    if subcmd == "clear":
        scope = subargs.strip().lower()
        graphs = _get_graphs(config, scope)
        try:
            await _cmd_clear(graphs, config)
        finally:
            for _, _, g in graphs:
                g.close()
        return True

    await warn(f"未知子命令: {subcmd}", config)
    await info("可用子命令: stats, search, list, export, clear", config)
    return True


async def _cmd_stats(graphs, config: AppConfig):
    all_empty = True
    for label, db_path, graph in graphs:
        if not db_path.exists():
            await info(f"[{label}] 知识图谱为空", config)
            continue
        stats = graph.get_stats()
        if stats["entities"] == 0:
            await info(f"[{label}] 知识图谱为空", config)
            continue
        all_empty = False
        lines = [
            f"[{label}] 知识图谱统计:",
            f"  实体: {stats['entities']}",
            f"  关系: {stats['relations']}",
            f"  别名: {stats['aliases']}",
        ]
        if stats["entity_types"]:
            lines.append("  实体类型:")
            for t, cnt in stats["entity_types"].items():
                lines.append(f"    {t}: {cnt}")
        if stats["relation_types"]:
            lines.append("  关系类型:")
            for t, cnt in stats["relation_types"].items():
                lines.append(f"    {t}: {cnt}")
        await info("\n".join(lines), config)
    if all_empty:
        await warn("知识图谱为空,请先使用 kg_add_entity 添加实体", config)


async def _cmd_search(graphs, keyword: str, config: AppConfig):
    total = 0
    for label, db_path, graph in graphs:
        if not db_path.exists():
            continue
        results = graph.search_entities(keyword, limit=20)
        if not results:
            continue
        total += len(results)
        lines = [f"[{label}] 找到 {len(results)} 个匹配实体:"]
        for e in results:
            aliases = e.get("aliases", [])
            alias_str = f" (别名: {', '.join(aliases)})" if aliases else ""
            desc = f" — {e['description']}" if e.get("description") else ""
            lines.append(f"  [{e['type']}] {e['name']}{alias_str}{desc}")
        await ok("\n".join(lines), config)
    if total == 0:
        await warn(f"未找到匹配 '{keyword}' 的实体", config)


async def _cmd_list(graphs, entity_type: str, config: AppConfig):
    total = 0
    for label, db_path, graph in graphs:
        if not db_path.exists():
            continue
        entities = graph.list_entities(entity_type=entity_type, limit=50)
        if not entities:
            continue
        total += len(entities)
        type_label = f" (type={entity_type})" if entity_type else ""
        lines = [f"[{label}] 共 {len(entities)} 个实体{type_label}:"]
        for e in entities:
            aliases = e.get("aliases", [])
            alias_str = f" (别名: {', '.join(aliases)})" if aliases else ""
            desc = f" — {e['description']}" if e.get("description") else ""
            lines.append(f"  [{e['type']}] {e['name']}{alias_str}{desc}")
        await ok("\n".join(lines), config)
    if total == 0:
        await warn("知识图谱为空", config)


async def _cmd_export(graphs, fmt: str, config: AppConfig):
    for label, db_path, graph in graphs:
        if not db_path.exists():
            await info(f"[{label}] 知识图谱为空,跳过", config)
            continue

        base_dir = db_path.parent

        if fmt == "html":
            output_path = base_dir / "knowledge_graph.html"
            graph.visualize(output_path)
            await ok(f"[{label}] HTML 可视化已生成: {output_path}", config)
        elif fmt == "json":

            data = graph.export_json()
            output_path = base_dir / "knowledge_graph.json"
            output_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await ok(f"[{label}] JSON 已导出: {output_path}", config)
        elif fmt == "markdown":
            md = graph.export_markdown()
            output_path = base_dir / "knowledge_graph.md"
            output_path.write_text(md, encoding="utf-8")
            await ok(f"[{label}] Markdown 已导出: {output_path}", config)
        else:
            await warn(f"不支持的格式: {fmt}。可选: html, json, markdown", config)
            return


async def _cmd_clear(graphs, config: AppConfig):
    from uniclaw.console.ui import get_input

    has_data = False
    for label, db_path, graph in graphs:
        if not db_path.exists():
            continue
        stats = graph.get_stats()
        if stats["entities"] == 0:
            continue
        has_data = True
        await warn(
            f"[{label}] 即将清空知识图谱 ({stats['entities']} 个实体, {stats['relations']} 条关系)。此操作不可逆!",
            config,
        )
        confirm = await get_input(
            f"确认清空 [{label}] 知识图谱? (yes/no): ", title="确认", config=config
        )
        if confirm.lower() not in ("yes", "y"):
            await info(f"[{label}] 已取消", config)
            continue

        graph.conn.execute("DELETE FROM relations")
        graph.conn.execute("DELETE FROM entity_aliases")
        graph.conn.execute("DELETE FROM entities")
        graph.conn.commit()
        await ok(f"[{label}] 知识图谱已清空", config)
    if not has_data:
        await warn("知识图谱为空", config)
