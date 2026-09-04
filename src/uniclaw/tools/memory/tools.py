import math
import time
from pathlib import Path

from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.console.ui import warn
from uniclaw.tools.memory.context import ai_select_memories, memory_freshness_text
from .memory import Memory, MemorySource, MemoryType, Scope


@tool
def memory_save(
    name: str,
    description: str,
    content: str,
    scope: Scope,
    type: MemoryType = MemoryType.user,
    source: MemorySource = MemorySource.user,
    confidence: float = 1,
    force: bool = False,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    保存记忆到存储系统。

    该函数创建一个新的记忆对象,将其持久化存储,并返回保存成功的消息。
    支持多种记忆类型、来源和作用域,可用于记录用户偏好、项目信息、反馈等内容。

    如果同名记忆已存在且内容不同,默认不会覆盖,而是返回已有记忆和新记忆的内容对比,
    由调用方决定如何处理:
    - 合并/覆盖:使用 force=True 调用 memory_save,直接用新记忆替换旧记忆
    - 改名保存:直接用不同名称调用 memory_save(旧记忆保留)

    Args:
        name: 记忆的名称,用于唯一标识该记忆条目。建议尽可能详细以避免重名冲突
              示例格式:"分类-子分类-具体描述"
              - "项目配置-数据库连接池大小"
              - "技术栈-Python版本要求"
              - "开发规范-API命名规则"
        description: 记忆的描述信息,简要说明记忆的用途或内容
        content: 记忆的具体内容,包含需要保存的核心信息
        scope: 记忆的作用域,决定记忆的可见范围,可选值包括:
               - "user": 用户级别,对所有项目可见
               - "project": 项目级别,仅对当前项目可见
        type: 记忆的类型,可选值包括:
              - "user": 用户相关记忆
              - "feedback": 反馈信息
              - "project": 项目相关信息
              - "reference": 参考资料
              默认为 "user"
        source: 记忆的来源,标识记忆的创建者,可选值包括:
                - "user": 由用户创建
                - "model": 由模型生成
                - "tool": 由工具生成
                默认为 "user"
        confidence: 记忆的置信度,取值范围为 0-1,表示记忆的可靠程度
                   默认为 1(最高置信度)
        force: 是否强制保存。为 True 且同名记忆已存在时,用新记忆替换旧记忆,
               并在返回结果中包含旧记忆内容。默认 False。

    Returns:
        str: 保存成功时返回确认消息；同名记忆已存在且内容相同时返回提示；
             同名但内容不同时返回新旧记忆对比及处理建议

    Example:
        >>> result = memory_save(
        ...     name="用户偏好",
        ...     description="用户喜欢的沟通方式",
        ...     content="用户偏好简洁直接的回复风格",
        ...     scope="user",
        ...     type="user"
        ... )
        >>> print(result)
        记忆 '用户偏好' 已保存。
    """
    config = tool_runtime.config
    # user scope 不需要 root_dir；project scope 需要 root_dir(无 root_dir 时 fallback 到用户级)
    if not name.strip():
        raise ValueError("记忆名称 name 不能为空")
    if not content.strip():
        raise ValueError("记忆内容 content 不能为空")

    memory_scope: Scope | Path = (
        config.root_dir if scope == Scope.PROJECT and config.root_dir else Scope.USER
    )
    memory = Memory(
        name=name,
        description=description,
        content=content,
        type=type,
        source=source,
        scope=memory_scope,
        confidence=confidence,
    )

    result = memory.save_memory(force=force)

    if result["status"] == "identical":
        return result["message"]

    if result["status"] == "replaced":
        old = result["existing"]
        return (
            f"记忆 '{name}' 已强制替换。\n\n"
            f"旧记忆:\n"
            f"  描述:{old['description']}\n"
            f"  内容:{old['content']}\n"
            f"  类型:{old['type']}  作用域:{old['scope']}  "
            f"来源:{old['source']}  置信度:{old['confidence']}\n\n"
            f"新记忆:\n"
            f"  描述:{description}\n"
            f"  内容:{content}\n"
            f"  类型:{type}  作用域:{scope}  来源:{source}  置信度:{confidence}"
        )

    if result["status"] == "conflict":
        old = result["existing"]
        return (
            f"冲突:记忆 '{name}' 已存在且内容不同,请决定如何处理。\n\n"
            f"已有记忆:\n"
            f"  描述:{old['description']}\n"
            f"  内容:{old['content']}\n"
            f"  类型:{old['type']}  置信度:{old['confidence']}\n\n"
            f"新记忆:\n"
            f"  描述:{description}\n"
            f"  内容:{content}\n"
            f"  类型:{type}  置信度:{confidence}\n\n"
            f"处理方式:\n"
            f"1. 合并/覆盖:使用 force=True 调用 {memory_save.name},直接用新记忆替换旧记忆\n"
            f"2. 改名保存:直接用不同名称调用 {memory_save.name}(保留旧记忆)"
        )

    return result["message"]


@tool
async def memory_delete(name: str, scope: Scope, tool_runtime: ToolRuntime = None) -> str:
    """
    按名称删除持久化记忆条目。

    该函数根据指定的名称和作用域定位并删除对应的记忆文件,然后重建索引以保持数据一致性。
    删除操作是永久性的,无法恢复,请谨慎使用。

    Args:
        name: 要删除的记忆条目的名称,用于唯一标识目标记忆
        scope: 记忆的作用域,决定在哪个范围内查找和删除记忆,可选值包括:
               - "user": 用户级别作用域
               - "project": 项目级别作用域

    Returns:
        str: 返回删除成功的消息,格式为 "记忆已删除: '{记忆名称}' (作用域: {作用域})"

    Example:
        >>> result = memory_delete(
        ...     name="用户偏好",
        ...     scope="user"
        ... )
        >>> print(result)
        记忆已删除: '用户偏好' (作用域: user)
    """
    config = tool_runtime.config
    # user scope 不需要 root_dir；project scope 需要从 config 获取 root_dir(无 root_dir 时 fallback 到用户级)
    memory_scope: Scope | Path = (
        config.root_dir if scope == Scope.PROJECT and config.root_dir else Scope.USER
    )
    # 获取记忆文件路径并删除对应的记忆文件
    memory_path = Memory.get_memory_path(memory_scope, name)
    memory_path.unlink()

    # 重建索引以保持数据一致性
    Memory.rebuild_index(memory_scope)

    # 同步 FTS5 索引
    try:
        from .fts import remove_memory

        remove_memory(memory_path)
    except Exception as e:
        await warn(f"FTS 索引删除失败: {e}", config)

    return f"记忆已删除: '{name}' (作用域: {scope})"


@tool
def memory_list(scope: Scope, tool_runtime: ToolRuntime = None):
    """
    列出指定作用域下的所有记忆。

    该函数从存储系统中加载并格式化展示记忆列表,支持按作用域筛选或显示全部记忆。
    每个记忆条目会显示其类型、作用域、名称、置信度、来源等元数据信息。

    Args:
        scope: 记忆的作用域筛选条件,可选值包括:
               - "user": 仅显示用户级别记忆
               - "project": 仅显示项目级别记忆
               - "all": 显示所有作用域的记忆(用户 + 项目)

    Returns:
        str: 格式化后的记忆列表字符串,包含以下信息:
             - 记忆总数统计
             - 每条记忆的详细信息(类型、作用域、名称、置信度、来源)
             - 记忆的描述信息(如果存在)
             如果没有记忆,返回相应的提示信息
    """
    config = tool_runtime.config
    # 根据scope参数确定要查询的作用域范围
    root_dir = config.root_dir
    if scope == Scope.PROJECT:
        memories = (
            Memory.load_all_memories(scope=root_dir)
            if root_dir
            else Memory.load_all_memories(scope=Scope.USER)
        )
    elif scope == Scope.ALL:
        memories = (
            Memory.load_all_memories(scope=root_dir)
            + Memory.load_all_memories(scope=Scope.USER)
            if root_dir
            else Memory.load_all_memories(scope=Scope.USER)
        )
    else:
        memories = Memory.load_all_memories(scope=Scope.USER)
    # 处理无记忆的情况,返回友好的提示信息
    if not memories:
        return "未存储任何记忆。" if scope == Scope.ALL else f"未存储{scope}记忆。"

    # 构建记忆列表的格式化输出
    lines = [f"共 {len(memories)} 条记忆:"]
    for memory in memories:
        # 构建置信度和来源的元数据标签
        conf_tag = f" conf:{memory.confidence:.0%}" if memory.confidence < 1.0 else ""
        src_tag = (
            f" src:{memory.source}" if memory.source and memory.source != "user" else ""
        )
        meta = f"{conf_tag}{src_tag}".strip()
        tag = f"[{memory.type:9s}|{memory.scope_name:7s}]"
        lines.append(f"  {tag} {memory.name}{(' — ' + meta) if meta else ''}")

        # 如果记忆有描述信息,则追加显示
        if memory.description:
            lines.append(f"    {memory.description}")
    return "\n".join(lines)


@tool
async def memory_search(query: str, max_results: int, tool_runtime: ToolRuntime = None) -> str:
    """
    搜索与查询相关的记忆条目。

    该函数通过 SQLite FTS5 全文检索和 AI 语义筛选相结合的方式,从记忆库中查找与用户查询最相关的记忆。
    搜索结果按 BM25 相关性 × 0.5 + 置信度 × 近期性 × 0.5 综合排序,并更新记忆的最近使用时间。

    Args:
        query (str): 搜索查询字符串,通过 FTS5 unicode61 分词器在记忆的名称、描述和内容中进行 BM25 匹配
        max_results (int): 最大返回结果数量
    Returns:
        str: 格式化的搜索结果字符串,包含找到的记忆条目信息。如果未找到匹配的记忆,返回提示信息

    Note:
        - 搜索过程:FTS5 BM25 搜索 → AI 语义补充(FTS5 不足时) → 综合排序
        - BM25 分数归一化到 [0, 1],与置信度 × 近期性各占 50% 权重
        - 近期性评分采用指数衰减模型,半衰期约为21天
        - 返回的记忆条目会自动更新最后使用时间
    """
    config = tool_runtime.config
    root_dir = config.root_dir

    # 收集所有记忆目录(传给 fts_search 定位数据库文件)
    user_memory_dir = Memory.get_memory_dir(Scope.USER)
    memory_dirs = [user_memory_dir]
    if root_dir:
        project_memory_dir = Memory.get_memory_dir(root_dir)
        memory_dirs.append(project_memory_dir)
    memory_dirs = [d for d in memory_dirs if d.exists()]

    # 加载所有记忆(用于 AI fallback 和综合排序)
    memories = Memory.load_all_memories(scope=Scope.USER)
    if root_dir:
        memories += Memory.load_all_memories(scope=root_dir)
    if not memories:
        return "未找到匹配的记忆。"

    # Phase 1: FTS5 BM25 搜索
    from .fts import fts_search

    fts_hits = fts_search(query, memory_dirs, max_results=max_results)

    # 将 FTS 结果映射回 Memory 对象
    path_to_memory = {str(m.filename.resolve()): m for m in memories}
    keyword_results = []
    if fts_hits:
        max_score = fts_hits[0]["score"] if fts_hits[0]["score"] > 0 else 1.0
        for hit in fts_hits:
            memory = path_to_memory.get(hit["path"])
            if not memory:
                continue
            mtime_s = Path(memory.filename).stat().st_mtime
            bm25_norm = hit["score"] / max_score if max_score > 0 else 0.0
            keyword_results.append(
                {
                    "name": memory.name,
                    "description": memory.description,
                    "type": memory.type,
                    "scope": memory.scope_name,
                    "content": memory.content,
                    "filename": memory.filename,
                    "mtime_s": mtime_s,
                    "freshness_text": memory_freshness_text(mtime_s),
                    "confidence": memory.confidence,
                    "source": memory.source,
                    "memory": memory,
                    "bm25_score": bm25_norm,
                    "snippet": hit.get("snippet", ""),
                }
            )

    # Phase 2: AI 语义搜索(FTS5 结果不足时补充)
    ai_results = []
    if len(keyword_results) < max_results:
        ai_results = await ai_select_memories(
            query, memories, max_results, config=config
        )

    # 合并两种搜索结果,按文件名去重
    seen = set()
    results = []
    for r in keyword_results + ai_results:
        if r["filename"] not in seen:
            seen.add(r["filename"])
            results.append(r)

    if not results:
        return "未找到匹配的记忆。"

    # 按 BM25 × 0.5 + 置信度 × 近期性 × 0.5 综合排序
    now = time.time()
    for r in results:
        age_days = max(0, (now - r["mtime_s"]) / 86400)
        recency = math.exp(-age_days / 30)  # 半衰期 ≈ 21 天
        bm25_part = r.get("bm25_score", 0.0)
        cr_part = r.get("confidence", 1.0) * recency
        r["_rank"] = bm25_part * 0.5 + cr_part * 0.5
    results.sort(key=lambda r: r["_rank"], reverse=True)
    results = results[:max_results]

    # 为返回的记忆条目更新 last_used_at
    for r in results:
        if r.get("filename"):
            r["memory"].touch_last_used()

    # 格式化输出结果
    lines = [f"Found {len(results)} relevant memory/memories for '{query}':", ""]
    for r in results:
        freshness = f"  ⚠ {r['freshness_text']}" if r["freshness_text"] else ""
        conf = r.get("confidence", 1.0)
        src = r.get("source", "user")
        meta_tag = ""
        if conf < 1.0 or src != "user":
            meta_tag = f"  [conf:{conf:.0%} src:{src}]"
        snippet_text = ""
        if r.get("snippet"):
            snippet_text = f"\n  匹配: {r['snippet']}"
        lines.append(
            f"[{r['type']}/{r['scope']}] {r['name']}{meta_tag}\n"
            f"  {r['description']}\n"
            f"  {r['content'][:200]}{'...' if len(r['content']) > 200 else ''}"
            f"{snippet_text}{freshness}"
        )
    return "\n\n".join(lines)


def get_tools() -> list:
    """获取记忆工具列表"""
    return [memory_save, memory_delete, memory_list, memory_search]


def get_all_tools() -> list:
    """获取所有记忆工具(无条件返回)"""
    return get_tools()
