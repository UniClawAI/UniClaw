"""知识图谱工具 — 实体/关系的 CRUD、查询、可视化。"""

from __future__ import annotations

import functools
import inspect
import json
import sqlite3
from pathlib import Path
from uniclaw.config import AppConfig
from uniclaw.context import Scope, get_app_dir
from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR

from .graph import KnowledgeGraph


def _as_scope(scope) -> Scope:
    """把 scope 归一化为 Scope 枚举。

    工具注解写的是 Scope,但 LLM 传的是字符串("project"/"user")——
    Python 不做运行时强制转换,直接 `scope.value` 会 AttributeError。
    """
    return scope if isinstance(scope, Scope) else Scope(scope)


def _get_db_path(config: AppConfig, scope: Scope = Scope.PROJECT) -> Path:
    """解析知识图谱数据库路径。scope: "user" 用户级 / "project" 项目级(默认)。"""
    scope = _as_scope(scope)
    resolved = (
        config.root_dir if scope == Scope.PROJECT and config.root_dir else Scope.USER
    )
    return get_app_dir(resolved) / "knowledge.db"


def _get_graph(config: AppConfig, scope: Scope = Scope.PROJECT) -> KnowledgeGraph:
    """从 config 获取知识图谱实例。scope: "user" 用户级 / "project" 项目级(默认)。"""
    return KnowledgeGraph(_get_db_path(config, scope))


def _kg_error(
    func_name: str, sig: inspect.Signature, exc: BaseException, args, kwargs
) -> str:
    """生成带上下文的 TOOL_ERROR: 工具名 + 关键参数 + scope。

    不带 db 路径 —— 那是内部实现细节,对调用方(模型)无用。
    """
    ctx: list[str] = []
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    scope = bound.arguments.get("scope") or Scope.PROJECT
    for key, val in bound.arguments.items():
        if key in ("tool_runtime", "scope") or val is None:
            continue
        if isinstance(val, str) and val == "":
            continue
        text = val if isinstance(val, str) else repr(val)
        if len(text) > 80:
            text = text[:77] + "..."
        ctx.append(f"{key}={text}")
    parts = [f"工具={func_name}", f"scope={scope}"]
    parts.extend(ctx)
    # 不带 db 路径 —— 那是内部实现细节,对调用方(模型)无用
    return (
        f"{TOOL_ERROR}: 知识图谱操作失败({', '.join(parts)}): "
        f"{type(exc).__name__}: {exc}"
    )


def _kg_tool(func):
    """为同步 kg_* 工具统一兜住预期的外部失败(sqlite3.Error/OSError)。

    内层函数保持 try/finally: graph.close();本装饰器在其外层捕获
    sqlite3.Error(锁冲突/建库失败)与 OSError(导出写文件失败),返回带上下文的
    TOOL_ERROR 文本。bug 级异常(KeyError/TypeError 等)不捕获,交由 Tool.__call__ 兜底。
    """
    sig = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (sqlite3.Error, OSError) as e:
            return _kg_error(func.__name__, sig, e, args, kwargs)

    return wrapper


@tool
@_kg_tool
def kg_add_entity(
    name: str,
    type: str = "concept",
    description: str = "",
    properties: dict = None,
    confidence: float = 1.0,
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    添加单个实体到知识图谱。如果发现相似实体会返回重复警告。
    注意:如果需要从文本中批量提取实体和关系,请使用 kg_extract 工具。


    Args:
        name: 实体名称,如 "刘备"、"Python"、"UniClaw"
        type: 实体类型,可选值: person, place, concept, event, tool, organization, document, technology
        description: 实体描述
        properties: 实体属性(JSON 对象),如 {"age": "60", "era": "三国"}
        confidence: 置信度,0.0~1.0,默认 1.0
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    if not name.strip():
        return f"{TOOL_ERROR}: 实体名称 name 不能为空"
    graph = _get_graph(config, scope)
    try:
        result = graph.add_entity(
            name=name,
            entity_type=type,
            description=description,
            properties=properties,
            source="manual",
            confidence=confidence,
        )

        lines = []
        if result.get("duplicate_warning"):
            lines.append(f"⚠ {result['duplicate_warning']}")
            lines.append("")

        if "error" in result:
            return f"{TOOL_ERROR}: {result['error']}"

        # 重复添加时,如果实体已存在,只显示警告不显示"已添加"
        already_exists = result.get("duplicate_warning", "") and "已存在" in result.get(
            "duplicate_warning", ""
        )
        if already_exists:
            entity_id = result.get("id", "?")
            # 检查是否有实际的新内容需要更新 (type, description 等与原实体不同时可使用 update_entity)
            return f"⚠ 实体 '{name}' (type={type}) 已存在,已跳过 (ID={entity_id}, 如需更新请使用 {kg_update_entity.name})"

        lines.append(f"实体 '{name}' (type={type}) 已添加, ID={result['id']}")
        return "\n".join(lines)
    finally:
        graph.close()


@tool
@_kg_tool
def kg_add_relation(
    source: str,
    target: str,
    relation: str,
    source_type: str = "",
    target_type: str = "",
    weight: float = 1.0,
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    在知识图谱中添加单条实体间关系。
    注意:如果需要从文本中批量提取实体和关系,请使用 kg_extract 工具。


    Args:
        source: 源实体名称
        target: 目标实体名称
        relation: 关系类型,如 "is_a", "has", "uses", "related_to", "created_by", "belongs_to"
        source_type: 源实体类型(可选,用于消歧)
        target_type: 目标实体类型(可选,用于消歧)
        weight: 关系权重,默认 1.0
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        result = graph.add_relation(
            source_name=source,
            target_name=target,
            relation=relation,
            source_type=source_type,
            target_type=target_type,
            weight=weight,
            source="manual",
        )

        if "error" in result:
            return f"{TOOL_ERROR}: {result['error']}"

        return f"关系已添加: {result['source']} --[{relation}]--> {result['target']}"
    finally:
        graph.close()


@tool
@_kg_tool
def kg_add_alias(
    name: str,
    alias: str,
    type: str = "",
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    为知识图谱中的实体添加别名。用于实体消歧,如将"刘玄德"关联到"刘备"。


    Args:
        name: 实体名称(已有实体)
        alias: 别名
        type: 实体类型(可选,用于消歧)
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    # 前置校验: 空/纯空白别名入库只会得到无意义的空白别名项, 直接拒绝
    if not alias or not alias.strip():
        return f"{TOOL_ERROR}: alias 不能为空(收到: {alias!r})"
    graph = _get_graph(config, scope)
    try:
        result = graph.add_alias(name, type, alias)
        if "error" in result:
            return f"{TOOL_ERROR}: {result['error']}"
        return f"别名已添加: {result['entity']} → {result['alias']}"
    finally:
        graph.close()


@tool
@_kg_tool
def kg_update_entity(
    name: str,
    type: str = "",
    description: str = "",
    confidence: float = -1,
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    更新知识图谱中实体的属性。


    Args:
        name: 实体名称
        type: 实体类型(可选,用于消歧)
        description: 新描述(留空则不更新)
        confidence: 新置信度(负数则不更新)
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        kwargs = {}
        if description:
            kwargs["description"] = description
        if confidence >= 0:
            kwargs["confidence"] = confidence

        result = graph.update_entity(name, type, **kwargs)
        if "error" in result:
            return f"{TOOL_ERROR}: {result['error']}"
        return f"实体 '{result['entity']}' 已更新: {', '.join(result['updated'])}"
    finally:
        graph.close()


@tool
@_kg_tool
def kg_delete_entity(
    name: str,
    type: str = "",
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    删除知识图谱中的实体(级联删除其所有关系和别名)。


    Args:
        name: 实体名称
        type: 实体类型(可选,用于消歧)
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        result = graph.delete_entity(name, type)
        if "error" in result:
            return f"{TOOL_ERROR}: {result['error']}"
        return f"实体 '{result['deleted']}' 及其关系已删除。"
    finally:
        graph.close()


@tool
@_kg_tool
def kg_delete_relation(
    source: str,
    target: str,
    relation: str,
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    删除知识图谱中的关系。


    Args:
        source: 源实体名称
        target: 目标实体名称
        relation: 关系类型
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        result = graph.delete_relation(source, target, relation)
        if "error" in result:
            return f"{TOOL_ERROR}: {result['error']}"
        return f"关系已删除: {result['deleted']}"
    finally:
        graph.close()


@tool
@_kg_tool
def kg_merge_entities(
    source: str,
    target: str,
    source_type: str = "",
    target_type: str = "",
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    合并两个实体:将 source 的关系、别名、属性转移到 target,然后删除 source。

    用于两个实体表示同一含义时的去重操作。转移过程中会自动跳过重复关系和冲突别名。


    Args:
        source: 要被合并的实体名称(合并后会被删除)
        target: 保留的实体名称(关系转移到此实体)
        source_type: 源实体类型(可选,用于消歧)
        target_type: 目标实体类型(可选,用于消歧)
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        result = graph.merge_entities(source, target, source_type, target_type)
        if "error" in result:
            return f"{TOOL_ERROR}: {result['error']}"

        lines = [f"实体合并完成: '{result['source']}' → '{result['target']}'"]
        lines.append(
            f"  转移关系: {result['transferred_relations']} 条 (跳过重复: {result['skipped_relations']})"
        )
        lines.append(
            f"  转移别名: {result['transferred_aliases']} 个 (跳过冲突: {result['skipped_aliases']})"
        )
        if result["merged_properties"]:
            lines.append("  属性已合并")
        if result["inherited_description"]:
            lines.append("  描述已继承")
        return "\n".join(lines)
    finally:
        graph.close()


@tool
@_kg_tool
def kg_get_entity(
    name: str,
    type: str = "",
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    获取知识图谱中实体的详细信息,包括别名和关系。


    Args:
        name: 实体名称
        type: 实体类型(可选,用于消歧)
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        entity = graph.get_entity(name, type)
        if not entity:
            if type:
                # 名称存在但类型不符时,报"未找到"会让人以为实体没入库
                other = graph.get_entity(name)
                if other:
                    return (
                        f"{TOOL_ERROR}: 实体 '{name}' 存在,但类型是 "
                        f"'{other['type']}',不是 '{type}'。"
                        f"查看它请去掉 type 参数;同名多类型请用 {kg_search.name} 消歧"
                    )
            return f"未找到实体 '{name}'。"

        lines = [f"实体: {entity['name']} (type={entity['type']})"]
        if entity.get("description"):
            lines.append(f"描述: {entity['description']}")
        if entity.get("aliases"):
            lines.append(f"别名: {', '.join(entity['aliases'])}")
        if entity.get("properties"):
            props = entity["properties"]
            if isinstance(props, dict):
                props_str = ", ".join(f"{k}={v}" for k, v in props.items())
                lines.append(f"属性: {props_str}")
        lines.append(f"置信度: {entity.get('confidence', 1.0):.0%}")
        lines.append(f"来源: {entity.get('source', 'manual')}")

        if entity.get("relations"):
            lines.append("")
            lines.append("关系:")
            for rel in entity["relations"]:
                if rel["direction"] == "outgoing":
                    lines.append(
                        f"  → {rel['relation']} → {rel['target_name']} ({rel['target_type']})"
                    )
                else:
                    lines.append(
                        f"  ← {rel['relation']} ← {rel['source_name']} ({rel['source_type']})"
                    )

        return "\n".join(lines)
    finally:
        graph.close()


@tool
@_kg_tool
def kg_search(
    keyword: str,
    type: str = "",
    limit: int = 20,
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    在知识图谱中搜索实体(FTS5 全文搜索)。


    Args:
        keyword: 搜索关键词
        type: 实体类型过滤(可选)
        limit: 最大返回数量,默认 20
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    # 前置校验: 空关键词会被当成"列出所有",语义不明,直接拒绝
    if not keyword or not keyword.strip():
        return (
            f"{TOOL_ERROR}: keyword 不能为空(收到: {keyword!r});"
            f"请提供要搜索的关键词;如需列出全部实体请使用 {kg_list.name}"
        )
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        results = graph.search_entities(keyword, entity_type=type, limit=limit)
        if not results:
            return f"未找到匹配 '{keyword}' 的实体。"

        lines = [f"找到 {len(results)} 个匹配实体:"]
        for e in results:
            aliases = e.get("aliases", [])
            alias_str = f" (别名: {', '.join(aliases)})" if aliases else ""
            desc = f" — {e['description']}" if e.get("description") else ""
            lines.append(f"  [{e['type']}] {e['name']}{alias_str}{desc}")
        return "\n".join(lines)
    finally:
        graph.close()


@tool
@_kg_tool
def kg_neighbors(
    name: str,
    depth: int = 1,
    type: str = "",
    relation_type: str = "",
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    获取知识图谱中实体的邻居(关联实体),支持多跳遍历。


    Args:
        name: 实体名称
        depth: 遍历深度,默认 1(直接邻居),最大 3
        type: 实体类型(可选,用于消歧)
        relation_type: 关系类型过滤(可选)
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    scope = _as_scope(scope)
    # depth=0 会静默返回空邻居(误导成"没有关系");超上限静默截断也不透明
    if depth < 1:
        return (
            f"{TOOL_ERROR}: depth 必须 >= 1(收到: {depth});"
            f"查直接邻居用 depth=1,多跳用 2~3"
        )
    depth_note = ""
    if depth > 3:
        depth_note = " —— depth 超出上限 3,已按 3 处理"
        depth = 3
    graph = _get_graph(config, scope)
    try:
        # 先确认实体存在 —— 否则"实体不存在"与"实体存在但无关系"都会返回
        # "没有邻居",调用方无法区分(可能误以为实体已入库)
        if not graph.get_entity(name, entity_type=type):
            return (
                f"{TOOL_ERROR}: 实体 '{name}' 不存在"
                f"({'type=' + type if type else '未指定 type'}"
                f", scope={scope.value});"
                f"可先用 {kg_search.name} 查找或 {kg_add_entity.name} 创建"
            )
        neighbors = graph.get_neighbors(
            name, depth=depth, entity_type=type, relation_type=relation_type
        )
        if not neighbors:
            # relation_type 过滤后的空结果 ≠ 没有任何关系
            if relation_type:
                all_neighbors = graph.get_neighbors(
                    name, depth=depth, entity_type=type
                )
                if all_neighbors:
                    rel_types = sorted(
                        {n.get("relation") or "?" for n in all_neighbors}
                    )
                    return (
                        f"实体 '{name}' 有 {len(all_neighbors)} 个邻居,"
                        f"但没有 relation_type='{relation_type}' 的关系。"
                        f"该实体实际关系类型: {', '.join(rel_types)}"
                    )
            return f"实体 '{name}' 存在,但没有邻居(当前无任何关系)。"

        lines = [f"实体 '{name}' 的邻居 (depth={depth}){depth_note}:"]
        for n in neighbors:
            direction = "→" if n.get("direction") == "outgoing" else "←"
            rel = n.get("relation", "?")
            lines.append(
                f"  {direction} [{n['type']}] {n['name']} (via {rel}, depth={n.get('depth', 1)})"
            )
        return "\n".join(lines)
    finally:
        graph.close()


@tool
@_kg_tool
def kg_path(
    source: str,
    target: str,
    max_depth: int = 5,
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    查找知识图谱中两个实体之间的路径。


    Args:
        source: 源实体名称
        target: 目标实体名称
        max_depth: 最大搜索深度,默认 5
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        # 先区分"实体不存在"与"确实无路径" —— 否则目标缺失时只报
        # "未找到路径",调用方分不清是实体没入库还是图里真不连通
        missing = [
            f"'{n}'" for n in (source, target) if not graph.get_entity(n)
        ]
        if missing:
            return (
                f"{TOOL_ERROR}: 实体不存在: {', '.join(missing)}"
                f"(scope={_as_scope(scope).value});"
                f"可先用 {kg_search.name} 查找或 {kg_add_entity.name} 创建"
            )
        paths = graph.find_path(source, target, max_depth=max_depth)
        if not paths:
            return f"未找到 '{source}' 到 '{target}' 的路径(两个实体都存在,但图中不连通)。"

        lines = [f"找到 {len(paths)} 条路径:"]
        for i, path in enumerate(paths, 1):
            parts = []
            for j, node in enumerate(path):
                if j == 0:
                    parts.append(f"{node['name']}")
                else:
                    rel = node.get("via_relation", "?")
                    # 反向走边时按真实边方向标注(真实边 A--[rel]-->B,
                    # 从 B 走到 A 应显示 B <--[rel]-- A,不能读成 B 爱 A)
                    if node.get("via_direction") == "backward":
                        parts.append(f" <--[{rel}]-- {node['name']}(反向查看)")
                    else:
                        parts.append(f" --[{rel}]--> {node['name']}")
            lines.append(f"  路径 {i}: {''.join(parts)}")
        return "\n".join(lines)
    finally:
        graph.close()


@tool
@_kg_tool
def kg_stats(
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    获取知识图谱的统计信息。


    Args:
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        stats = graph.get_stats()
        lines = [
            "知识图谱统计:",
            f"  实体: {stats['entities']}",
            f"  关系: {stats['relations']}",
            f"  别名: {stats['aliases']}",
        ]
        if stats["entity_types"]:
            lines.append("  实体类型分布:")
            for t, cnt in stats["entity_types"].items():
                lines.append(f"    {t}: {cnt}")
        if stats["relation_types"]:
            lines.append("  关系类型分布:")
            for t, cnt in stats["relation_types"].items():
                lines.append(f"    {t}: {cnt}")
        return "\n".join(lines)
    finally:
        graph.close()


@tool
@_kg_tool
def kg_export(
    path: str,
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    导出知识图谱到文件。根据文件后缀自动选择格式。


    Args:
        path: 导出文件路径,支持后缀: .json, .md, .html
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """

    output = Path(path)
    ext = output.suffix.lower()
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        if ext == ".json":
            data = graph.export_json()
            content = json.dumps(data, ensure_ascii=False, indent=2)
        elif ext == ".md":
            content = graph.export_markdown()
        elif ext == ".html":
            stats = graph.get_stats()
            try:
                output.parent.mkdir(parents=True, exist_ok=True)
                graph.visualize(output)
            except Exception as e:
                # bug 级异常照常外抛(Tool.__call__ 兜底并落堆栈),但补上
                # 阶段/规模上下文 —— 不把内部异常原文直接甩给调用方
                raise RuntimeError(
                    f"HTML 可视化生成失败(path={output}, "
                    f"实体 {stats['entities']}, 关系 {stats['relations']}): "
                    f"{type(e).__name__}: {e}"
                ) from e
            return f"HTML 可视化已生成: {output}\n请在浏览器中打开查看。"
        else:
            return f"{TOOL_ERROR}: 不支持的文件后缀: {ext}。支持: .json, .md, .html"

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        return f"知识图谱已导出: {output}"
    finally:
        graph.close()


@tool
@_kg_tool
def kg_list(
    type: str = "",
    limit: int = 50,
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    列出知识图谱中的实体。


    Args:
        type: 实体类型过滤(可选),如 "person", "concept"
        limit: 最大返回数量,默认 50
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        stats = graph.get_stats()
        total = stats["entities"]
        entities = graph.list_entities(entity_type=type, limit=limit)
        if not entities:
            # "该类型没有实体"与"整个图谱为空"是两回事,不能混为一谈
            if total == 0:
                return "知识图谱为空。"
            scope_desc = f"类型 '{type}'" if type else "当前条件"
            return f"{scope_desc} 下没有实体(图谱共 {total} 个实体)。"

        matched_total = (
            stats.get("entity_types", {}).get(type, 0) if type else total
        )
        shown = len(entities)
        head = f"返回 {shown} 条"
        if shown < matched_total:
            head += (
                f"(共 {matched_total} 个匹配实体, limit={limit} 已截断;"
                f"调大 limit 可查看更多)"
            )
        else:
            head += f"(共 {matched_total} 个)"
        if type:
            head += f", 类型过滤: {type}"

        lines = [head + ":"]
        for e in entities:
            aliases = e.get("aliases", [])
            alias_str = f" (别名: {', '.join(aliases)})" if aliases else ""
            desc = f" — {e['description']}" if e.get("description") else ""
            lines.append(f"  [{e['type']}] {e['name']}{alias_str}{desc}")
        return "\n".join(lines)
    finally:
        graph.close()


def _subagent_failure(task, stage: str) -> str:
    """subagent 失败/无产出时的报错。

    必须带上 name/status/task_id —— task.result 常常是空字符串,
    只报 "subagent 执行失败: " 会让调用方完全无从诊断。
    """
    detail = (task.result or "").strip()
    if detail:
        body = detail[:800]
    else:
        body = (
            "(subagent 无任何输出;可查看 .UniClaw/logs/ 下的 agent 日志"
            "定位失败环节)"
        )
    return (
        f"{TOOL_ERROR}: subagent {stage}失败"
        f"(name={task.name}, task_id={task.id}, status={task.status}): {body}"
    )


@tool
async def kg_extract(
    text: str = "",
    path: str = "",
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    从文本或文件中自动提取实体和关系,并添加到知识图谱。

    启动专用 subagent 分析内容,识别其中的实体和关系。
    subagent 会自动读取文件、分块处理,返回结构化 JSON 后写入图谱。

    实体类型不限于 person/place/concept 等常用类型,subagent 会根据内容灵活定义。

    注意:本工具会派生 subagent,需在主会话调用。在子代理内调用会因
    嵌套深度限制失败(超过最大嵌套深度 depth=3, max_agent_depth=2)——
    这是平台限制,不是工具缺陷。

    Args:
        text: 要分析的文本内容(直接传入)
        path: 文件或目录路径(由 subagent 自行读取分析)
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    scope = _as_scope(scope)
    if not text and not path:
        return f"{TOOL_ERROR}: 请提供 text 或 path 参数。"

    from uniclaw.agent import MultiAgent
    from uniclaw.tools.multi_agent.sub_agent import load_agent_definitions
    from uniclaw.utils.format import parse_json_from_llm

    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        # 获取已有实体列表(用于避免重复)
        existing = graph.list_entities(limit=100)
        existing_names = [e["name"] for e in existing]

        # 构造 prompt
        existing_hint = (
            f"\n\n已有实体列表(避免重复): {', '.join(existing_names[:50])}"
            if existing_names
            else ""
        )

        if path:
            prompt = (
                f"请从以下路径读取文件并提取实体和关系:\n{path}\n\n"
                "**要求**:\n"
                "- 如果是文件: 使用 Read 完整读取整个文件内容,不要遗漏任何部分\n"
                "- 如果是目录: 先用 Glob 列出所有文件,然后逐个 Read 完整读取每个文件\n"
                "- 对于较长的文件,分多次 Read (通过 offset) 确保覆盖全文\n"
                "- 提取要详尽,不要遗漏重要的实体和关系"
                f"{existing_hint}"
            )
        else:
            prompt = f"请从以下文本中提取实体和关系:\n\n{text}" f"{existing_hint}"

        # 启动 subagent
        mgr = MultiAgent.get_instance()
        sub_config = config.create_sub_config(
            name="kg-extract", prompt=prompt, tool_call_id=tool_runtime.tool_call_id
        )
        agent_def = load_agent_definitions(config.root_dir).get("kg-extract")
        if not agent_def:
            return f"{TOOL_ERROR}: 未找到 'kg-extract' 子智能体定义。"

        from uniclaw.agent import AgentStatus

        try:
            task = await mgr.start_sub_agent(
                user_message=prompt,
                config=sub_config,
                system_prompt=None,
                agent_def=agent_def,
                inherit_events=True,  # 子智能体事件继承到父级队列,前端可显示执行过程
            )
        except (RuntimeError, OSError) as e:
            # 只兜 subagent 派生失败(超出嵌套深度、资源不足等外部原因);
            # KeyError/AttributeError/TypeError 等编程错误原样抛给 Tool.__call__
            src = path or f"text({len(text)} 字符)"
            return (
                f"{TOOL_ERROR}: subagent 启动失败"
                f"(scope={scope.value}, source={src}): "
                f"{type(e).__name__}: {e}"
            )

        if task.status == AgentStatus.FAILED:
            return _subagent_failure(task, "启动")

        # 等待完成(subagent 的 ToolStartEvent/ThinkingChunkEvent 等
        # 会通过 bridge_events 自动广播到前端,无需在此重复)
        await mgr.wait(task.id, timeout=300, config=config)

        # 不只看 FAILED —— 超时/取消/lost 也拿不到可用结果,必须一并回报
        if task.status != AgentStatus.COMPLETED:
            return _subagent_failure(task, "执行")

        result_text = (task.result or "").strip()
        if not result_text:
            return _subagent_failure(task, "执行")

        # 解析 JSON
        data = parse_json_from_llm(result_text)
        if not data:
            return f"{TOOL_ERROR}: subagent 返回的内容无法解析为 JSON:\n{result_text[:500]}"

        entities = data.get("entities", [])
        relations = data.get("relations", [])

        lines = [f"AI 提取结果:"]
        lines.append(f"  实体: {len(entities)} 个")
        lines.append(f"  关系: {len(relations)} 个")
        lines.append("")

        # 添加实体
        for e in entities:
            result = graph.add_entity(
                name=e["name"],
                entity_type=e.get("type", "concept"),
                description=e.get("description", ""),
                properties=e.get("properties"),
                source="auto",
                confidence=e.get("confidence", 0.8),
            )
            status = "已添加" if "id" in result else "失败"
            dw = result.get("duplicate_warning") or ""
            # "已存在" 仅用于 IntegrityError(同名同类型已存在,复用旧 ID);
            # "发现相似实体" 只是提示,实体本身是新建的,不能标成"已存在"
            if "已存在" in dw:
                status = "已存在"
            warning = f" ⚠ {dw}" if dw else ""
            lines.append(
                f"  实体 [{e.get('type', 'concept')}] {e['name']}: {status}{warning}"
            )

        # 添加关系
        lines.append("")
        for r in relations:
            result = graph.add_relation(
                source_name=r["source"],
                target_name=r["target"],
                relation=r["relation"],
                source="auto",
                confidence=r.get("confidence", 0.8),
            )
            if "error" in result:
                lines.append(
                    f"  关系 {r['source']} --[{r['relation']}]--> {r['target']}: {result['error']}"
                )
            else:
                lines.append(
                    f"  关系 {r['source']} --[{r['relation']}]--> {r['target']}: 已添加"
                )

        return "\n".join(lines)
    finally:
        graph.close()


@tool
@_kg_tool
def kg_clear(
    scope: Scope = Scope.PROJECT,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    清空知识图谱中的所有实体和关系。此操作不可逆!


    Args:
        scope: 作用域,"user" 为用户级(跨项目共享),"project" 为项目级(默认)
    """
    config = tool_runtime.config
    graph = _get_graph(config, scope)
    try:
        stats = graph.get_stats()
        graph.conn.execute("DELETE FROM relations")
        graph.conn.execute("DELETE FROM entity_aliases")
        graph.conn.execute("DELETE FROM entities")
        graph.conn.commit()
        return f"知识图谱已清空。删除了 {stats['entities']} 个实体和 {stats['relations']} 条关系。"
    finally:
        graph.close()


def get_tools() -> list:
    """获取知识图谱工具列表。"""
    return [
        kg_add_entity,
        kg_add_relation,
        kg_add_alias,
        kg_update_entity,
        kg_delete_entity,
        kg_delete_relation,
        kg_merge_entities,
        kg_get_entity,
        kg_search,
        kg_neighbors,
        kg_path,
        kg_stats,
        kg_export,
        kg_list,
        kg_extract,
        kg_clear,
    ]


def get_all_tools() -> list:
    """获取所有知识图谱工具。"""
    return get_tools()
