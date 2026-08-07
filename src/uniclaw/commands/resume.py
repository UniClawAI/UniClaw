"""会话恢复与管理命令"""

from uniclaw.agent import AgentTask
from uniclaw.config import AppConfig
from uniclaw.console.ui import err, info, warn
from uniclaw.tools.session.session import Session
from uniclaw.tools.session.session_manager import SessionManager
from uniclaw.utils.message import MessageRole

# 子命令列表
SUBCOMMANDS = ["list", "del", "search", "fork"]


def _format_item(index: int, item: dict) -> str:
    """格式化会话条目"""
    title = item.get("title") or "[无标题]"
    time = item.get("end_time") or item.get("start_time", "")
    msg_count = item.get("message_count", 0)
    sid = item.get("session_id", "")
    return f"  {index}. {title}  |  {time}  |  {msg_count} 条消息  |  {sid}"


async def cmd_resume(args: str, config: AppConfig) -> bool:
    """恢复和管理历史会话,支持列出、恢复、删除、搜索、分叉等操作。

    会话是 UniClaw 的核心概念之一,每次对话都会自动持久化到磁盘
    (.UniClaw/sessions/),用户可以随时中断并在后续恢复之前的对话上下文。

    子命令一览:
        /resume                        — 无参数,列出最近 10 个会话供交互式选择
        /resume <session_id>           — 恢复指定 ID 的会话,回放历史消息
        /resume list                   — 列出所有已保存的会话 (最多 50 个)
        /resume del <session_id>       — 删除指定会话 (需二次确认)
        /resume search <keyword>       — 按关键词搜索会话内容,显示匹配位置
        /resume fork [session_id] [idx]— 从指定会话的某条消息处创建分叉

    Args:
        args: 用户输入的子命令和参数,由空格分隔。例如 "list"、"del abc123"、
              "search 关键词"、"fork abc123 5"。空字符串表示无参数调用。
        config: 全局应用配置,包含当前 agent 任务、运行模式 (Console/WebUI) 等信息。

    Returns:
        bool: 始终返回 True,表示命令已处理完毕。

    行为说明:
        - Console 模式:恢复会话时直接替换 task.session 并回放历史消息到终端
        - WebUI 模式:恢复会话时通知前端切换,不修改当前 task 的 session;
          删除会话后若为当前会话,前端自动进入新建会话状态
        - fork 操作会基于原会话截取前 N 条消息创建新会话,原会话不受影响
        - 所有子命令解析失败时,回退到无参数的交互式选择逻辑
    """
    # 获取当前 agent 任务,后续恢复/fork 操作都需要它
    task = config.current_agent

    # 解析输入:将 args 拆为子命令名 (subcmd) 和剩余参数 (rest)
    # maxsplit=1 保证只拆第一个空格,rest 可能包含空格 (如 search 的关键词)
    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    # ── 子命令: list ──────────────────────────────────────────────
    # 列出所有已保存的会话 (上限 50 个),按时间倒序排列。
    # 每行显示序号、标题、时间、消息数和 session_id,方便用户选择恢复。
    if subcmd == "list":
        items = SessionManager.list_sessions(limit=50)
        if not items:
            await warn("没有可恢复的会话", config)
            return True
        lines = [f"\n可恢复的会话 (共 {len(items)} 个):\n"]
        for idx, item in enumerate(items, 1):
            lines.append(_format_item(idx, item))
        lines.append("\n用法: /resume <session_id>")
        await info("\n".join(lines), config)
        return True

    # ── 子命令: del / delete / rm ─────────────────────────────────
    # 删除指定会话。流程:校验参数 → 二次确认 → 执行删除 → 通知前端 (WebUI)。
    # 支持三个别名 (del/delete/rm),使用 rest 作为 session_id。
    if subcmd in ("del", "delete", "rm"):
        session_id = rest.strip()
        if not session_id:
            await err("用法: /resume del <session_id>", config)
            return True
        answer = ""
        try:
            from uniclaw.console.ui import get_input

            answer = await get_input(
                f"确定要删除会话 {session_id}?(y/n):", title="删除对话", config=config
            )
        except Exception as e:
            await err(f"获取删除确认输入失败: {e}", config)
            return True
        if answer.strip().lower() != "y":
            await warn("已取消删除", config)
            return True
        if SessionManager.delete_session(session_id):
            await warn(f"已删除会话: {session_id}", config)
            # WebUI 模式:通知前端会话已删除
            if config.is_webui:
                try:
                    from uniclaw.webui.ws import notify_session_deleted

                    await notify_session_deleted(session_id, config.root_dir)
                except Exception as e:
                    await err(f"通知前端会话删除失败: {e}", config)
        else:
            await err(f"删除失败或未找到会话: {session_id}", config)
        return True

    # ── 子命令: search ────────────────────────────────────────────
    # 按关键词搜索所有会话的消息内容。搜索结果除了显示会话基本信息外,
    # 还会标注匹配的消息序号,方便用户定位到具体内容。
    if subcmd == "search":
        keyword = rest.strip()
        if not keyword:
            await err("用法: /resume search <keyword>", config)
            return True
        try:
            results = SessionManager.search_sessions(keyword)
        except Exception as exc:
            await err(f"搜索失败: {exc}", config)
            return True
        if not results:
            await warn(f"未找到包含 {keyword!r} 的对话", config)
            return True
        lines = [f"找到 {len(results)} 条包含 {keyword!r} 的对话:\n"]
        for idx, item in enumerate(results, 1):
            lines.append(_format_item(idx, item))
            lines.append(
                "   匹配位置: " + "、".join(f"消息{i}" for i in item["matches"])
            )
        await info("\n".join(lines), config)
        return True

    # ── 子命令: fork ──────────────────────────────────────────────
    # 会话分叉:从原会话的某条消息处截断,创建一个新会话继续对话。
    # 分叉不影响原会话,适合"回到过去换个方向试试"的场景。
    # 实际逻辑委托给 _handle_fork(),参数解析和交互都在那边完成。
    if subcmd == "fork":
        await _handle_fork(rest.strip(), task, config)
        return True

    # ── 直接恢复: /resume <session_id> ────────────────────────────
    # subcmd 不为空且不匹配任何子命令时,将其视为 session_id 尝试恢复。
    # 这是最常见的用法:用户从 /resume list 或其他渠道拿到 session_id 后直接粘贴。
    if subcmd:
        session = SessionManager.load_session(subcmd)
        if not session:
            await err(f"未找到会话: {subcmd}", config)
            return True
        await _restore_session(session, task, config)
        return True

    # ── 无参数:交互式选择 ─────────────────────────────────────────
    # 最友好的入口:列出最近 10 个会话,用户输入序号或 session_id 即可恢复。
    # 支持两种选择方式:数字序号 (1/2/3...) 或直接输入完整 session_id。
    # 直接回车取消,不进行任何操作。
    items = SessionManager.list_sessions(limit=10)
    if not items:
        await warn("没有可恢复的会话", config)
        return True

    lines = ["最近会话:\n"]
    for idx, item in enumerate(items, 1):
        lines.append(_format_item(idx, item))
    lines.append("\n输入序号或 session_id 恢复(直接回车取消):")
    prompt_text = "\n".join(lines)

    try:
        from uniclaw.console.ui import get_input

        choice = await get_input(prompt_text, title="恢复会话", config=config)
    except Exception as e:
        await err(f"获取会话选择输入失败: {e}", config)
        return True
    if not choice:
        return True
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(items):
            session_id = items[idx]["session_id"]
            session = SessionManager.load_session(session_id)
            if session:
                await _restore_session(session, task, config)
                return True
        await err(f"无效序号: {choice}", config)
        return True

    # 按 session_id 恢复
    session = SessionManager.load_session(choice)
    if not session:
        await err(f"未找到会话: {choice}", config)
        return True
    await _restore_session(session, task, config)
    return True


async def _restore_session(session: Session, task: AgentTask, config: AppConfig = None):
    """将会话切换到目标 session,是所有恢复操作的最终执行者。

    Console 和 WebUI 模式下的恢复行为完全不同:
    - Console 模式:直接替换 task.session,清空终端后回放历史消息 (让用户看到之前的对话)
    - WebUI 模式:不修改 task.session,而是通过 WebSocket 通知前端切换到目标会话,
      前端自行从 session_cache 加载并渲染 (前端维护自己的会话状态)

    Args:
        session: 要恢复的目标会话对象。
        task: 当前的 agent 任务,Console 模式下会替换其 session 属性。
        config: 全局配置,用于判断运行模式和错误处理。可选,但实际调用时都会传入。
    """
    old_session_id = task.session.id if task.session else ""

    # WebUI 模式:通知前端切换会话 (不修改 task,前端自行管理状态)
    if config and config.is_webui:
        try:
            from uniclaw.webui.ws import notify_session_switched

            await notify_session_switched(session.id, old_session_id)
        except Exception as e:
            await err(f"通知前端会话切换失败: {e}", config)
    else:
        # Console 模式:替换 session,清屏后回放历史消息
        task.session = session
        try:
            from uniclaw.console.run import TUIApp

            tui = TUIApp.get_instance()
            if tui:
                tui.clear()
                messages = task.session.to_history_messages()
                tui.replay_messages(messages)
        except Exception as e:
            await err(f"回放历史消息失败: {e}", config)


async def _handle_fork(args: str, task: AgentTask, config: AppConfig):
    """处理 /resume fork 子命令,从历史会话的指定位置创建分叉。

    分叉 (fork) 是一种"时光倒流"机制:将原会话的前 N 条消息复制到新会话,
    用户可以在新会话中从那个节点开始,尝试不同的对话方向,而不影响原会话。

    用法:
        /resume fork                    — 分叉当前会话,交互式选择分叉点
        /resume fork <idx>              — 分叉当前会话到第 idx 条消息
        /resume fork <session_id>       — 分叉指定历史会话,交互式选择分叉点
        /resume fork <session_id> <idx> — 分叉指定历史会话到第 idx 条消息

    Args:
        args: fork 子命令后的剩余参数,可能为空或包含 session_id 和/或消息序号。
        task: 当前 agent 任务,用于获取当前会话 ID (无 session_id 参数时使用)。
        config: 全局配置。
    """
    # 参数解析:args 可能是 "", "123", "abc", "abc 123" 等形式
    parts = args.split() if args else []
    session_id = None
    message_idx = None

    if len(parts) == 0:
        # 无参数:对当前会话进行分叉,稍后交互式选择分叉点
        session_id = task.session.id
    elif len(parts) == 1:
        arg = parts[0]
        if arg.isdigit():
            # 纯数字:当前会话 + 直接指定分叉点 (跳过交互式选择)
            session_id = task.session.id
            message_idx = int(arg)
        else:
            # 非数字:视为 session_id,稍后交互式选择分叉点
            session_id = arg
    else:
        # 两个参数:第一个是 session_id,第二个是消息序号
        session_id = parts[0]
        if parts[1].isdigit():
            message_idx = int(parts[1])
        else:
            await err(
                f"无效的消息序号: {parts[1]},用法: /resume fork <session_id> <序号>",
                config,
            )
            return

    # 加载目标会话并校验有效性
    session = SessionManager.load_session(session_id)
    if not session:
        await err(f"未找到会话: {session_id}", config)
        return

    if len(session) == 0:
        await err("会话没有消息,无法分叉", config)
        return

    # 如果没指定分叉点,显示交互式消息列表让用户选择
    if message_idx is None:
        message_idx = await _pick_fork_point(session, config)
        if message_idx is None:
            return  # 用户取消了选择

    # 执行分叉:SessionManager 会截取原会话的前 message_idx 条消息创建新会话
    forked = await SessionManager.fork_session(session_id, message_idx, config)
    if not forked:
        await err(
            f"分叉失败: 无效的消息序号 {message_idx}(共 {len(session)} 条消息)", config
        )
        return

    # 分叉成功:恢复到新会话并通知用户
    await _restore_session(forked, task, config)
    await info(
        f"已从会话 {session_id} 的第 {message_idx + 1} 条消息处分叉\n新会话: {forked.id}",
        config,
    )


async def _pick_fork_point(session: Session, config: AppConfig) -> int | None:
    """交互式消息选择器,让用户从会话历史中选择分叉点。

    列出目标会话的所有消息 (角色 + 内容摘要),用户输入序号选定分叉点。
    分叉点意味着:保留该序号及之前的所有消息,丢弃之后的消息。

    Args:
        session: 要展示消息列表的会话对象。
        config: 全局配置。

    Returns:
        int | None: 用户选择的消息索引 (0-based),取消或输入无效时返回 None。
    """
    lines = ["会话消息:\n"]
    for idx, msg in enumerate(session):
        role = msg.role
        # 简化内容显示
        text = msg.to_content().replace("\n", " ").strip()[:60]
        if not text:
            text = "(无文本内容)"
        lines.append(f"  {idx + 1}. [{role}] {text}")

    lines.append(f"\n输入分叉点序号 (1-{len(session)}),直接回车取消:")

    try:
        from uniclaw.console.ui import get_input

        choice = await get_input("\n".join(lines), title="选择分叉点", config=config)
    except Exception as e:
        await err(f"获取分叉点选择输入失败: {e}", config)
        return None
    if not choice:
        return None
    if not choice.isdigit():
        await err(f"无效输入: {choice},请输入数字序号", config)
        return None

    idx = int(choice) - 1
    if idx < 0 or idx >= len(session):
        await err(f"序号超出范围: {choice}(共 {len(session)} 条消息)", config)
        return None
    return idx
