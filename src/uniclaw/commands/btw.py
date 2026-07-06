import uuid

from uniclaw.config import AppConfig
from uniclaw.console.ui import info, err
from uniclaw.utils.message import MessageRole


async def cmd_btw(args: str, config: AppConfig) -> bool:
    """在不打断当前对话的情况下提问侧问题

    开一个独立的 LLM 调用回答问题,不影响当前会话的消息历史。
    自动携带最近对话上下文,让回答更贴合当前工作场景。

    用法: /btw <问题>
    示例: /btw 什么是 Python GIL?
    """
    task = config.current_agent
    question = args.strip()
    if not question:
        await err("用法: /btw <问题>\n示例: /btw 什么是 Python GIL?", config)
        return True

    from uniclaw.provider import achat
    from uniclaw.tools.session.session import Session
    # 构建带上下文的消息
    context = task.session.build_context_summary(max_messages=10, max_chars=2000)
    system_content = "你是一个有帮助的助手。请简洁明了地回答,如果问题涉及代码给出关键示例即可。"
    if context:
        system_content += f"\n\n以下是用户当前对话的最近上下文,供你参考:\n---\n{context}\n---"

    _session = Session()
    _session.add_user_message(content=question)

    # 获取 TUI 实例用于显示
    from uniclaw.console.run import TUIApp

    tui = TUIApp.get_instance()

    wait_id = config.spinner.start("💡 思考侧问题...")
    try:
        response = await achat(
            system_content,
            _session,
            temperature=None,
            max_tokens=None,
            enable_thinking=False,
            thinking=False,
            config=config,
        )

        answer = response.content if response.content else "(无回答)"

        if tui:
            # 用特殊样式显示侧问题结果
            tui.print("")
            tui.print(f"💡 侧问题: {question}", style="fg:yellow")
            tui.print("─" * 40, style="fg:gray")
            tui.print(answer)
            tui.print("─" * 40, style="fg:gray")
            tui.print("")
        else:
            # 非 TUI 模式直接打印
            await info(f"\n💡 侧问题: {question}", config)
            await info("─" * 40, config)
            await info(answer, config)
            await info("─" * 40, config)
            await info("", config)

    except Exception as e:
        await err(f"侧问题回答失败: {e}", config)
    finally:
        config.spinner.stop(wait_id=wait_id)

    return True
