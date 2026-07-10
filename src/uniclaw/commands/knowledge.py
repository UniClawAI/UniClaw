"""知识图谱命令 — /kg stats, search, list, export, clear。"""

from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn, err

# 子命令列表
SUBCOMMANDS = ["stats", "search", "list", "export", "clear"]


async def cmd_knowledge(args: str, config: AppConfig) -> bool:
    """知识图谱管理命令

    支持以下功能:
    - 无参数:显示图谱统计信息
    - stats:显示图谱统计信息
    - search <关键词>:搜索实体
    - list [类型]:列出实体
    - export html|json|markdown:导出图谱
    - clear:清空图谱(需确认)

    Args:
        args: 命令参数
        config: 配置对象

    Returns:
        bool: 始终返回 True 表示命令执行完成
    """
    from uniclaw.tools.knowledge.graph import KnowledgeGraph
    from uniclaw.context import get_app_dir

    query = args.strip()
    db_path = get_app_dir(config.root_dir) / "knowledge.db"

    if not db_path.exists():
        await warn("知识图谱为空,请先使用 kg_add_entity 添加实体", config)
        return True

    graph = KnowledgeGraph(db_path)

    try:
        # /kg 或 /kg stats — 统计信息
        if not query or query == "stats":
            stats = graph.get_stats()
            lines = [
                "知识图谱统计:",
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
            return True

        parts = query.split(None, 1)
        subcmd = parts[0].lower()
        subargs = parts[1] if len(parts) > 1 else ""

        # /kg search <关键词>
        if subcmd == "search":
            if not subargs:
                await warn("用法: /kg search <关键词>", config)
                return True
            results = graph.search_entities(subargs, limit=20)
            if not results:
                await warn(f"未找到匹配 '{subargs}' 的实体", config)
                return True
            lines = [f"找到 {len(results)} 个匹配实体:"]
            for e in results:
                aliases = e.get("aliases", [])
                alias_str = f" (别名: {', '.join(aliases)})" if aliases else ""
                desc = f" — {e['description']}" if e.get("description") else ""
                lines.append(f"  [{e['type']}] {e['name']}{alias_str}{desc}")
            await ok("\n".join(lines), config)
            return True

        # /kg list [类型]
        if subcmd == "list":
            entity_type = subargs if subargs else ""
            entities = graph.list_entities(entity_type=entity_type, limit=50)
            if not entities:
                await warn("知识图谱为空", config)
                return True
            type_label = f" (type={entity_type})" if entity_type else ""
            lines = [f"共 {len(entities)} 个实体{type_label}:"]
            for e in entities:
                aliases = e.get("aliases", [])
                alias_str = f" (别名: {', '.join(aliases)})" if aliases else ""
                desc = f" — {e['description']}" if e.get("description") else ""
                lines.append(f"  [{e['type']}] {e['name']}{alias_str}{desc}")
            await ok("\n".join(lines), config)
            return True

        # /kg export html|json|markdown
        if subcmd == "export":
            fmt = subargs.lower() if subargs else "markdown"
            if fmt == "html":
                output_path = get_app_dir(config.root_dir) / "knowledge_graph.html"
                graph.visualize(output_path)
                await ok(f"HTML 可视化已生成: {output_path}", config)
                await info("请在浏览器中打开查看", config)
            elif fmt == "json":
                import json

                data = graph.export_json()
                output_path = get_app_dir(config.root_dir) / "knowledge_graph.json"
                output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                await ok(f"JSON 已导出: {output_path}", config)
            elif fmt == "markdown":
                md = graph.export_markdown()
                output_path = get_app_dir(config.root_dir) / "knowledge_graph.md"
                output_path.write_text(md, encoding="utf-8")
                await ok(f"Markdown 已导出: {output_path}", config)
            else:
                await warn(f"不支持的格式: {fmt}。可选: html, json, markdown", config)
            return True

        # /kg clear — 清空图谱
        if subcmd == "clear":
            stats = graph.get_stats()
            await warn(
                f"即将清空知识图谱 ({stats['entities']} 个实体, {stats['relations']} 条关系)。此操作不可逆!",
                config,
            )
            from uniclaw.console.ui import get_input

            confirm = await get_input("确认清空? (yes/no): ", title="确认", config=config)
            if confirm.lower() not in ("yes", "y"):
                await info("已取消", config)
                return True

            graph.conn.execute("DELETE FROM relations")
            graph.conn.execute("DELETE FROM entity_aliases")
            graph.conn.execute("DELETE FROM entities")
            graph.conn.commit()
            await ok("知识图谱已清空", config)
            return True

        # 未知子命令
        await warn(f"未知子命令: {subcmd}", config)
        await info("可用子命令: stats, search, list, export, clear", config)
        return True

    finally:
        graph.close()
