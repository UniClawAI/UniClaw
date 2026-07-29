"""微信消息处理模块,将 iLink Bot 消息桥接到 UniClaw Agent。"""

import asyncio
import base64
import io
import mimetypes
import re
from contextlib import redirect_stdout

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

from uniclaw.agent import (
    AgentTask,
    AgentStatus,
    MultiAgent,
    TextChunkEvent,
    AssistantEvent,
    ToolEvent,
    EndEvent,
    PermissionRequestEvent,
    ThinkingStartEvent,
    ThinkingChunkEvent,
    ToolPreparingEvent,
    ToolStartEvent,
    UserEvent,
    SlashCommandEvent,
    ShellCommandEvent,
    CheckpointStartEvent,
    CheckpointEndEvent,
)
from uniclaw.commands import handle_slash
from uniclaw.config import Permissions, RunMode, load_config, AppConfig
from uniclaw.tools.shell import Bash
from uniclaw.ilink_bot import IlinkBotClient, IncomingMessage
from uniclaw.ilink_bot.media import download_media, detect_ext
from uniclaw.context import build_system_prompt
from uniclaw.console.ui import C, clr, info, ok, warn, err

# 每个用户独立的配置(含 session 和 agent)
_user_configs: dict[str, AppConfig] = {}

# 待处理的输入请求 {user_id: asyncio.Future}
_pending_wechat_inputs: dict[str, asyncio.Future] = {}


def _get_user_config(user_id: str) -> AppConfig:
    """获取或创建用户的 AppConfig(含独立的 session 和 agent)。"""
    if user_id not in _user_configs:
        from uniclaw.tools.session.session_manager import SessionManager

        # 尝试加载已有会话,找不到则新建
        session = SessionManager.load_session(user_id)
        if session is None:
            session = user_id
        else:
            from uniclaw.tools.session.session import SessionType

            session.session_type = SessionType.WECHAT

        config = load_config(
            session=session, run_mode=RunMode.WEBUI, session_type=SessionType.WECHAT
        )
        config.current_agent.name = f"wechat-{user_id}"
        config.current_agent.event_queue = asyncio.Queue()
        _user_configs[user_id] = config
    return _user_configs[user_id]


async def _build_user_message(
    msg: IncomingMessage, bot: IlinkBotClient, config: AppConfig
) -> str | list:
    """构造用户消息,包含文本和图片的多模态内容。"""
    text = msg.text.strip()
    if not msg.images:
        return text or "(图片)"

    content_blocks: list[dict] = []
    for img in msg.images:
        try:
            data = download_media(img, bot=bot)
            ext = detect_ext(data, "image")
            mime = mimetypes.types_map.get(ext, "image/jpeg")
            b64 = base64.b64encode(data).decode()
            content_blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )
        except Exception as e:
            await warn(f"图片下载失败: {e}", config)

    if text:
        content_blocks.append({"type": "text", "text": text})
    elif not content_blocks:
        return "(图片下载失败)"

    return content_blocks


def _format_tool_call(name: str, args: dict) -> str:
    """格式化工具调用为简洁的 'name arg' 形式。"""
    if not args:
        return name
    first_val = next(iter(args.values()), None)
    if first_val is None:
        return name
    s = str(first_val)
    if len(s) > 40:
        s = s[:37] + "..."
    return f"{name} {s}"


async def _collect_response(
    config: AppConfig,
    client: IlinkBotClient | None = None,
) -> str:
    """从事件队列中收集 Agent 的文本回复。

    Args:
        config: 用户的 AppConfig(含 current_agent)
        client: iLink Bot 客户端,用于实时发送工具调用通知
    """
    task = config.current_agent
    spinner = config.spinner
    spinner_wait_id: str | None = None
    parts: list[str] = []
    current_name = ""
    current_args: dict = {}
    thinking_stream = False
    text_stream = False

    from uniclaw.utils.wakeup import mark_draining

    mark_draining(task, True)
    try:
        while True:
            _agent_task, event = await task.event_queue.get()
            if spinner_wait_id:
                spinner.stop(spinner_wait_id)
                spinner_wait_id = None
            if isinstance(event, ThinkingStartEvent):
                spinner_wait_id = spinner.start("Thinking...")
            elif isinstance(event, ThinkingChunkEvent):
                if not thinking_stream:
                    print(clr("  [思考中]", C.DIM))
                thinking_stream = True
                print(clr(event.content, C.DIM), end="")
            elif isinstance(event, TextChunkEvent):
                if not text_stream:
                    print(clr("  [回复]", C.WHITE))
                text_stream = True
                parts.append(event.content)
                print(clr(event.content, C.WHITE), end="")
            elif isinstance(event, AssistantEvent):
                thinking_stream = False
                text_stream = False
                print()
                if event.tool_calls:
                    print(clr(f"  工具调用 x{len(event.tool_calls)}", C.CYAN))
                    for tc in event.tool_calls:
                        fn = tc.get("function", {})
                        print(
                            clr(
                                f"    - {fn.get('name', '?')}({fn.get('arguments', '')})",
                                C.CYAN,
                            )
                        )
                print(
                    clr(
                        f"  模型:{event.model_name}  输入:{event.in_tokens} 输出:{event.out_tokens}",
                        C.DIM,
                    )
                )
            elif isinstance(event, ToolPreparingEvent):
                label = _format_tool_call(event.name, event.args)
                spinner_wait_id = spinner.start(f"准备调用 '{label}'...")
            elif isinstance(event, CheckpointStartEvent):
                spinner_wait_id = spinner.start("创建检查点...")
            elif isinstance(event, CheckpointEndEvent):
                if spinner_wait_id:
                    spinner.stop(spinner_wait_id)
                    spinner_wait_id = None
            elif isinstance(event, ToolStartEvent):
                current_name = event.name
                current_args = event.args
                label = _format_tool_call(event.name, event.args)
                spinner_wait_id = spinner.start(f"工具 '{label}' 执行中...")
                if client:
                    try:
                        client.reply_text(f"🔧 {label}")
                    except Exception as e:
                        await warn(f"发送工具调用通知失败: {e}", config)
            elif isinstance(event, ToolEvent):
                print(
                    clr(
                        f"  [工具] {_format_tool_call(current_name, current_args)}",
                        C.GREEN,
                    )
                )
                print(clr(f"    {event.content}", C.DIM))
            elif isinstance(event, UserEvent):
                # 显示用户输入消息(微信模式下通常不需要显示,但保留用于调试)
                pass
            elif isinstance(event, PermissionRequestEvent):
                agent_label = (
                    f" [子代理: {event.agent_name}]" if event.agent_name else ""
                )
                prompt = f"🔧{agent_label} {event.description}"
                if event.explanation:
                    prompt += f"\n{event.explanation}"
                prompt += "\n\n输入 y 同意,其他内容为拒绝:"
                reply = await wechat_input(prompt, title="权限请求", config=config)
                reply = reply.strip()
                if reply.lower() == "y":
                    event.content = True
                else:
                    event.content = reply
                event.return_event.set()
            elif isinstance(event, ShellCommandEvent):
                await info(f"[微信] 用户执行Shell命令: {event.command}", config)
                result = await Bash(event.command, config=config)
                output = _ANSI_RE.sub("", result).strip()
                print(clr(f"  $ {event.command}", C.CYAN))
                print(clr(output or "(无输出)", C.DIM))
                event.content = output
                event.return_event.set()
            elif isinstance(event, SlashCommandEvent):
                await info(f"[微信] 用户执行斜杠命令: {event.command}", config)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    result = await handle_slash(event.command, config)
                output = _ANSI_RE.sub("", buf.getvalue()).strip()
                if output:
                    print(clr(output, C.WHITE))
                event.content = result if isinstance(result, str) else ""
                event.return_event.set()
            elif isinstance(event, EndEvent):
                if event.depth == 0:
                    break
    finally:
        mark_draining(task, False)
    print()
    return "".join(parts)


def make_handler():
    """创建消息处理函数,注册到 BotManager。

    Returns:
        ManagerHandler 签名的处理函数 (bot, msg)
    """

    multi_agent = MultiAgent.get_instance()

    async def handler(bot: IlinkBotClient, msg: IncomingMessage):
        user_id = msg.user_id
        text = msg.text.strip()
        if not text and not msg.images:
            return

        config = _get_user_config(user_id)
        task = config.current_agent

        # 挂载 bot 供 wechat_input / send_file / tts 使用
        config.wechat_ctx = bot

        # 如果有待处理的输入请求,优先响应
        if user_id in _pending_wechat_inputs:
            fut = _pending_wechat_inputs[user_id]
            if not fut.done():
                fut.set_result(text)
                return

        print(f"[微信] 收到消息 [{user_id}]: {text or '(图片)'}")

        # /命令处理
        if text.startswith("/"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                result = await handle_slash(text, config)
            output = _ANSI_RE.sub("", buf.getvalue()).strip()
            if isinstance(result, str) and result:
                # 结果作为用户消息,交给 AI 处理
                if task.status == AgentStatus.RUNNING:
                    task.user_queue.put_nowait(result)
                    print(f"[微信] 用户 {user_id} 的 agent 正在运行,消息已排队")
                    await info("⏳ 已排队,将在当前任务处理间隙自动补充。", config)
                    return
                # 启动 agent
                try:
                    bot.send_typing()
                except Exception as e:
                    await warn(f"发送打字状态失败: {e}", config)
                try:
                    system_prompt = await build_system_prompt(config)
                    multi_agent.start_agent(
                        result, config=config, system_prompt=system_prompt
                    )
                    reply = await _collect_response(config, client=bot)
                    if reply:
                        bot.reply_text(reply)
                except Exception as e:
                    bot.reply_text(f"❌ 执行出错: {e}")
            elif output:
                bot.reply_text(output.replace("\n", "\n\n"))
            elif result:
                print("命令已执行。")
            else:
                bot.reply_text("命令没找到")
            return

        # !命令处理 - 直接执行 shell 命令
        if text.startswith("!"):
            shell_cmd = text[1:].strip()
            if shell_cmd:
                await info(f"[微信] 执行命令: {shell_cmd}", config)
                result = await Bash(shell_cmd, config=config)
                output = _ANSI_RE.sub("", result).strip()
                bot.reply_text(output.replace("\n", "\n\n") or "(无输出)")
            return

        user_message = await _build_user_message(msg, bot, config)

        # 检查该用户是否有正在运行的 agent 任务
        task_name = f"wechat-{user_id}"
        for t in multi_agent.id2AgentTask.values():
            if t.name == task_name and t.status == AgentStatus.RUNNING:
                t.user_queue.put_nowait(
                    user_message if isinstance(user_message, str) else str(user_message)
                )
                print(f"[微信] 用户 {user_id} 的 agent 正在运行,消息已排队")
                await info("⏳ 已排队,将在当前任务处理间隙自动补充。", config)
                return

        try:
            bot.send_typing()
        except Exception as e:
            await warn(f"发送打字状态失败: {e}", config)

        try:
            system_prompt = await build_system_prompt(config)

            multi_agent.start_agent(
                user_message,
                config=config,
                system_prompt=system_prompt,
            )
            reply = await _collect_response(config, client=bot)

            if not reply:
                reply = "(Agent 未产生回复)"

            # 微信需要 \n+空格 才能正确换行
            bot.reply_text(reply.replace("\n", "\n "))
            print(f"[微信] 已回复 [{user_id}]: {reply[:50]}...")

        except Exception as e:
            await err(f"[微信] 处理消息失败: {e}", config)
            try:
                bot.reply_text(f"处理出错: {e}")
            except Exception as e2:
                await warn(f"发送错误回复失败: {e2}", config)
        finally:
            try:
                bot.stop_typing()
            except Exception as e:
                await warn(f"停止打字状态失败: {e}", config)

    return handler


async def wechat_input(prompt: str, title: str = "输入", config=None) -> str:
    """微信模式的输入函数:发送问题给用户,等待回复。"""
    bot = getattr(config, "wechat_ctx", None)
    if not bot:
        return ""

    user_id = bot._current_user_id
    # 发送问题
    try:
        bot.reply_text(f"💬 {title}\n{prompt}")
    except Exception as e:
        await warn(f"发送输入提示失败: {e}", config)
        return ""

    # 等待用户回复
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _pending_wechat_inputs[user_id] = fut
    try:
        return await asyncio.wait_for(fut, timeout=300)
    except asyncio.TimeoutError:
        return ""
    finally:
        _pending_wechat_inputs.pop(user_id, None)


async def wechat_multi_input(
    questions: list[dict], title: str = "请选择", config=None
) -> str:
    """微信模式的多问题输入:逐题调用 wechat_input。"""
    import json

    answers = {}
    for q in questions:
        question_text = q.get("question", "")
        options = q.get("options", [])
        lines = [question_text]
        for j, opt in enumerate(options):
            opt_str = opt if isinstance(opt, str) else str(opt)
            lines.append(f"  {j + 1}. {opt_str}")
        lines.append("请输入编号或直接输入文字:")
        prompt = "\n".join(lines)

        reply = await wechat_input(prompt, title=title, config=config)
        reply = reply.strip()
        if reply.isdigit() and 1 <= int(reply) <= len(options):
            answers[question_text] = options[int(reply) - 1]
        else:
            answers[question_text] = reply if reply else ""

    return json.dumps(answers, ensure_ascii=False)
