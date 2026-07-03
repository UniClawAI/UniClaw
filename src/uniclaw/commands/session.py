import json
from datetime import datetime
from pathlib import Path
from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn, err
from uniclaw.tools.session.session import Session
from uniclaw.utils.usage import get_stats, UsageField, TOTAL
from uniclaw.utils.message import MessageRole


async def cmd_compact(args: str, config: AppConfig) -> bool:
    """手动压缩对话历史

    通过移除或摘要化旧消息来减少上下文长度,优化 Token 使用。
    支持可选的聚焦参数,保留与特定主题相关的消息。

    Args:
        args: 可选的聚焦关键词,用于保留相关消息
        task: 当前代理任务对象,包含消息历史
        config: 配置字典,包含 model_name 等配置

    Returns:
        bool: 始终返回 True 表示命令执行完成
    """
    task = config.current_agent
    focus = args.strip() if args else ""
    model_name = config.model_name[0] if config.model_name else ""
    before = task.session.estimate_tokens(model_name)
    await info("正在压缩对话历史...", config)
    await task.session.compact(config, focus=focus)
    after = task.session.estimate_tokens(model_name)
    saved = before - after
    await ok(
        f"✓ 对话已压缩: {before} → {after} tokens(节省 {saved} tokens){'(聚焦: ' + focus + ')' if focus else ''}",
        config,
    )
    return True


async def cmd_clear(_args: str, config: AppConfig) -> bool:
    """清除当前会话上下文和屏幕

    清空所有消息历史,重置会话 ID 和开始时间,并清屏。

    Args:
        _args: 未使用的参数
        task: 当前代理任务对象,其消息历史将被清空
        _config: 未使用的配置字典

    Returns:
        bool: 始终返回 True 表示命令执行完成
    """
    task = config.current_agent
    old_id = task.session.id if config.is_wechat else ""
    task.session = Session(root_dir=task.session.root_dir, id=old_id, session_type=task.session.session_type)

    if config.is_wechat:
        try:
            from uniclaw.tools.session.session_manager import SessionManager

            await SessionManager.save_session(config)
        except Exception as e:
            await err(str(e), config)
        await info("会话已清除,可以开始新的对话了。", config)
    else:
        from uniclaw.console.run import TUIApp

        tui = TUIApp.get_instance()
        if tui:
            tui.clear()
        else:
            # 非TUI模式,使用系统清屏命令
            import platform
            import subprocess

            command = "cls" if platform.system() == "Windows" else "clear"
            subprocess.call(command, shell=True)
    return True


async def cmd_export(args: str, config: AppConfig) -> bool:
    """导出当前对话消息到文件

    支持两种导出格式:
    - Markdown (.md): 人类可读的格式,包含消息内容和统计信息
    - JSON (.json): 结构化数据格式,便于程序处理

    如果未指定路径,默认导出到用户目录的 exports 文件夹,使用带时间戳的文件名。

    Args:
        args: 导出文件路径(可选),根据扩展名决定格式
        task: 当前代理任务对象,包含要导出的消息历史
        config: 配置字典

    Returns:
        bool: 导出成功返回 True,失败返回 False
    """
    task = config.current_agent
    from uniclaw.context import get_app_dir, Scope

    # 确定导出路径和格式
    if args.strip():
        # 用户提供了路径
        export_path = Path(args.strip())
        # 如果是相对路径,转换为绝对路径
        if not export_path.is_absolute():
            export_path = task.session.root_dir / export_path
        # 根据扩展名决定格式
        use_json = export_path.suffix.lower() == ".json"
    else:
        # 使用默认路径:get_app_dir()/"exports",默认使用 md 格式
        exports_dir = get_app_dir(Scope.USER) / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        # 生成带时间戳的文件名,默认使用 .md 格式
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = exports_dir / f"session_{timestamp}.md"
        use_json = False

    # 确保父目录存在
    export_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if use_json:
            # JSON 格式导出
            stats = get_stats()
            total = stats.get(TOTAL, {})
            export_data = {
                "exported_at": datetime.now().isoformat(),
                "message_count": len(task.session),
                "total_input_tokens": total.get(UsageField.INPUT_TOKENS, 0),
                "total_output_tokens": total.get(UsageField.OUTPUT_TOKENS, 0),
                "api_calls": total.get(UsageField.API_CALLS, 0),
                "messages": (await task.session.to_dict(config)).get("messages", []),
            }

            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
        else:
            # Markdown 格式导出
            stats = get_stats()
            total = stats.get(TOTAL, {})
            md_content = f"""# 对话导出

**导出时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**消息数量**: {len(task.session)}
**总输入 Token**: {total.get(UsageField.INPUT_TOKENS, 0)}
**总输出 Token**: {total.get(UsageField.OUTPUT_TOKENS, 0)}
**API 调用次数**: {total.get(UsageField.API_CALLS, 0)}

---

"""

            # 添加消息内容
            for i, msg in enumerate(task.session.to_openai_messages(), 1):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")

                # 根据角色设置标题
                if role == MessageRole.USER:
                    md_content += f"## 用户 (第 {i} 条)\n\n"
                elif role == MessageRole.ASSISTANT:
                    md_content += f"## 助手 (第 {i} 条)\n\n"
                elif role == MessageRole.SYSTEM:
                    md_content += f"## 系统 (第 {i} 条)\n\n"
                else:
                    md_content += f"## {role} (第 {i} 条)\n\n"

                # 添加内容
                if isinstance(content, str):
                    md_content += f"{content}\n\n"
                else:
                    # 如果内容是列表或其他类型,转换为字符串
                    md_content += f"```\n{content}\n```\n\n"

                md_content += "---\n\n"

            with open(export_path, "w", encoding="utf-8") as f:
                f.write(md_content)

        await ok(f"✓ 对话已导出: {export_path}", config)
        await info(f"导出格式: {'JSON' if use_json else 'Markdown'}", config)
        await info(f"消息数量: {len(task.session)}", config)
        await info(f"总输入 Token: {total.get(UsageField.INPUT_TOKENS, 0)}", config)
        await info(f"总输出 Token: {total.get(UsageField.OUTPUT_TOKENS, 0)}", config)
    except Exception as e:
        await err(f"导出失败: {e}", config)
        return False

    return True
