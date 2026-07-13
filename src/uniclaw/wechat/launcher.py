"""微信版 UniClaw 启动器,支持多微信账号。"""

import asyncio

from uniclaw.console.launcher import get_logo, get_welcome
from uniclaw.console.ui import info, ok, err
from uniclaw.ilink_bot import BotManager, AuthError
from uniclaw.utils.debug import heartbeat
from uniclaw.wechat.run import make_handler

_HELP = """
命令:
  add <名称>     添加并登录一个微信账号
  remove <名称>  移除一个微信账号
  list           查看所有账号状态
  stop           停止消息监听
  start          重新启动消息监听
  help           显示帮助
  exit           退出
""".strip()


@heartbeat(threshold=1.0)
async def _input_loop(manager: BotManager):
    """主命令循环,协程中运行 input 不阻塞事件循环。"""
    bot_task: asyncio.Task | None = None

    # 自动启动已登录的账号
    if any(b.is_logged_in for b in manager.bots):
        n = sum(1 for b in manager.bots if b.is_logged_in)
        await ok(f"自动启动 {n} 个账号...")
        bot_task = asyncio.create_task(manager.start())
    else:
        await info("暂无已登录账号,使用 add <名称> 添加。")

    await info(_HELP)
    await info("")

    while True:
        try:
            line = await asyncio.to_thread(input, "wechat> ")
            line = line.strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not line:
            continue

        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "exit":
            manager.stop()
            break
        elif cmd == "help":
            await info(_HELP)
        elif cmd == "list":
            if not manager:
                await info("暂无账号。使用 add <名称> 添加。")
            for bot in manager.bots:
                status = "已登录" if bot.is_logged_in else "未登录"
                await info(f"  {status}")
        elif cmd == "add":
            if not arg:
                await err("用法: add <名称>")
                continue
            try:
                await manager.add_and_login(arg)
                await ok(f"账号 '{arg}' 添加成功！")
                if bot_task is None or bot_task.done():
                    await ok("自动启动消息监听...")
                    bot_task = asyncio.create_task(manager.start())
            except AuthError as e:
                await err(f"登录失败: {e}")
            except Exception as e:
                await err(f"添加失败: {e}")
        elif cmd == "remove":
            if not arg:
                await err("用法: remove <名称>")
                continue
            if manager.get(arg):
                manager.remove_bot(arg)
                await ok(f"已移除 '{arg}'")
            else:
                await err(f"未找到账号 '{arg}'")
        elif cmd == "stop":
            manager.stop()
            if bot_task and not bot_task.done():
                bot_task.cancel()
                bot_task = None
            await ok("已停止消息监听。")
        elif cmd == "start":
            if not any(b.is_logged_in for b in manager.bots):
                await err("没有已登录的账号,请先 add <名称> 登录。")
                continue
            if bot_task and not bot_task.done():
                await info("消息监听已在运行中。")
                continue
            manager._stop_event.clear()
            await ok("启动消息监听...")
            bot_task = asyncio.create_task(manager.start())
        else:
            await err(f"未知命令: {cmd},输入 help 查看帮助。")

    manager.stop()
    if bot_task and not bot_task.done():
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass


async def launch():
    import os
    from uniclaw.config import Permissions

    from uniclaw.tools.scheduler.scheduler import Scheduler

    await Scheduler.get_instance().start()

    manager = BotManager()
    handler = make_handler()
    manager.on_message(handler)

    lines = [f"数据目录: {manager.data_dir}", f"已注册 {len(manager)} 个账号"]
    for bot in manager.bots:
        status = "已登录" if bot.is_logged_in else "未登录"
        lines.append(f"  - {status}")
    await info("\n".join(lines))

    try:
        await _input_loop(manager)
    except KeyboardInterrupt:
        manager.stop()

    await ok("已退出。")
