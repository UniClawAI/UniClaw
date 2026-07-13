import asyncio
from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn, err

# 子命令列表
SUBCOMMANDS = ["consolidate"]


async def cmd_memory(args: str, config: AppConfig) -> bool:
    """记忆管理命令

    支持以下功能:
    - 无参数:列出所有记忆的详细信息
    - <关键词>:使用 AI 智能搜索相关记忆
    - consolidate:从当前对话中提取并保存记忆

    Args:
        args: 命令参数,可以是关键词或 "consolidate"
        task: 当前代理任务对象,包含消息历史
        config: 配置字典,包含系统配置信息

    Returns:
        bool: 始终返回 True 表示命令执行完成
    """
    from uniclaw.tools.memory.memory import Memory
    from uniclaw.tools.memory.context import ai_select_memories
    from uniclaw.tools.memory.consolidate import consolidate_session
    from uniclaw.context import Scope

    task = config.current_agent
    query = args.strip()

    # /memory consolidate — 从当前对话提取记忆
    if query == "consolidate":
        if len(task.session) == 0:
            await warn("当前没有对话消息", config)
            return True
        await info("正在分析对话并提取记忆...", config)
        memories = await consolidate_session(task.session, config)
        if not memories:
            await warn("未提取到值得保存的记忆", config)
            return True
        await ok(f"✓ 已提取并保存 {len(memories)} 条记忆:", config)
        for mem in memories:
            await info(f"  • [{mem.type}] {mem.name}: {mem.description}", config)
        return True

    # /memory — 列出所有记忆详情(用户级 + 项目级)
    root_dir = task.session.root_dir
    all_memories = (
        Memory.load_all_memories(root_dir) if root_dir else []
    ) + Memory.load_all_memories(Scope.USER)
    if not all_memories:
        await warn("暂无记忆", config)
        return True

    # /memory <关键词> — AI 搜索相关记忆
    if query:
        results = ai_select_memories(query, all_memories, max_results=5, config=config)
        if not results:
            await warn(f"未找到与「{query}」相关的记忆", config)
            return True
        lines = [f"\n找到 {len(results)} 条相关记忆:\n"]
        for r in results:
            lines.append(f"  [{r['type']}] {r['name']}")
            lines.append(f"    {r['description']}")
            lines.append(
                f"    置信度: {r['confidence']}  来源: {r['source']}  作用域: {r['scope']}"
            )
            if r.get("freshness_text"):
                lines.append(f"    {r['freshness_text']}")
            lines.append("")
        await info("\n".join(lines), config)
        return True

    # 无参数 — 列出全部记忆详情
    lines = [f"\n共 {len(all_memories)} 条记忆:\n"]
    for mem in all_memories:
        lines.append(f"  [{mem.type}] {mem.name}")
        lines.append(f"    {mem.description}")
        lines.append(
            f"    置信度: {mem.confidence}  来源: {mem.source}  作用域: {mem.scope}"
        )
        if mem.created:
            lines.append(f"    创建时间: {mem.created}")
        if mem.last_used_at:
            lines.append(f"    最后使用: {mem.last_used_at}")
        lines.append("")
    await info("\n".join(lines), config)
    return True
