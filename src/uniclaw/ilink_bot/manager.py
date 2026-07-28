from __future__ import annotations

import asyncio
import json
import signal
from typing import Any, Callable

from .client import IlinkBotClient
from .exceptions import AuthError
from .models import IncomingMessage

ManagerHandler = Callable[[IlinkBotClient, IncomingMessage], Any]


class BotManager:
    _instance: BotManager | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        from uniclaw.wechat import get_wechat_dir

        self.data_dir = get_wechat_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / "bots.json"
        self._bots: dict[str, IlinkBotClient] = {}
        self._active: dict[str, IlinkBotClient] = {}
        self._handlers: list[ManagerHandler] = []
        self._stop_event = asyncio.Event()
        self._stop_event.set()  # 初始为已停止状态
        self._load_config()

    @property
    def is_running(self) -> bool:
        """是否正在运行消息轮询。"""
        return not self._stop_event.is_set()

    def on_message(self, handler: ManagerHandler) -> ManagerHandler:
        self._handlers.append(handler)
        return handler

    @property
    def bots(self) -> list[IlinkBotClient]:
        return list(self._bots.values())

    def __iter__(self):
        return iter(self._bots.values())

    def __len__(self):
        return len(self._bots)

    def get(self, name: str) -> IlinkBotClient | None:
        return self._bots.get(name)

    def add_bot(self, name: str, **kwargs) -> IlinkBotClient:
        if name in self._bots:
            return self._bots[name]
        cred_path = str(self.data_dir / f"{name}.json")
        bot = IlinkBotClient(cred_path=cred_path, **kwargs)
        self._bots[name] = bot
        self._save_config()
        return bot

    async def add_and_login(self, name: str, **kwargs) -> IlinkBotClient:
        bot = self.add_bot(name, **kwargs)
        await bot.login(**kwargs)
        if bot.is_logged_in:
            self._active[name] = bot
            print(f"[{name}] 已登录并激活 (共 {len(self._active)} 个机器人)")
        return bot

    def remove_bot(self, name: str) -> None:
        bot = self._bots.pop(name, None)
        if bot:
            bot.logout()
            self._active.pop(name, None)
            self._save_config()

    async def login(self, name: str, **kwargs) -> dict[str, Any]:
        bot = self._bots.get(name)
        if not bot:
            raise KeyError(f"Bot '{name}' not found")
        return await bot.login(**kwargs)

    def remove_inactive(self) -> list[str]:
        removed = []
        for name, bot in list(self._bots.items()):
            if not bot.is_logged_in:
                self._bots.pop(name)
                removed.append(name)
        if removed:
            self._save_config()
            print(f"已移除 {len(removed)} 个未激活的机器人: {', '.join(removed)}")
        return removed

    async def start(self, *, interval: float = 0.2) -> None:
        self._stop_event.clear()

        self._active = {n: b for n, b in self._bots.items() if b.is_logged_in}
        if not self._active:
            print("没有已登录的机器人。等待登录 <名称>...")

        print(f"正在启动 {len(self._active)} 个机器人...")
        for name, bot in self._active.items():
            print(f"  [{name}] {bot.base_url}")

        tasks: dict[str, asyncio.Task] = {}

        while not self._stop_event.is_set():
            # 为新 bot 创建 task
            for name in self._active:
                if name not in tasks or tasks[name].done():
                    bot = self._active[name]
                    tasks[name] = asyncio.create_task(
                        self._poll(name, bot, interval), name=name
                    )
            # 清理已移除 bot 的 task
            for name in list(tasks):
                if name not in self._active:
                    tasks[name].cancel()
                    del tasks[name]
            await asyncio.sleep(0.1)

        for t in tasks.values():
            t.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        print("所有机器人已停止。")

    def run(self, *, interval: float = 0.2) -> None:
        self._install_signal_handlers()
        asyncio.run(self.start(interval=interval))

    def stop(self) -> None:
        self._stop_event.set()

    async def _poll(self, name: str, bot: IlinkBotClient, interval: float) -> None:
        while True:
            try:
                messages = await asyncio.to_thread(bot.get_updates)
                for msg in messages:
                    await self._dispatch(bot, msg)
            except AuthError as e:
                print(f"[{name}] 认证过期,请重新登录: login {name}")
                self._active.pop(name, None)
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[{name}] 错误: {e}")
            await asyncio.sleep(interval)

    async def _dispatch(self, bot: IlinkBotClient, msg: IncomingMessage) -> None:
        for handler in self._handlers:
            asyncio.create_task(self._run_handler(handler, bot, msg))
        await bot._dispatch(msg)

    async def _run_handler(
        self, handler, bot: IlinkBotClient, msg: IncomingMessage
    ) -> None:
        try:
            await handler(bot, msg)
        except Exception as e:
            print(f"[管理器] 处理器错误: {e}")

    def _install_signal_handlers(self) -> None:
        def handler(signum, frame):
            print("\n正在停止...")
            self.stop()

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except (OSError, ValueError):
            pass

    def _load_config(self) -> None:
        if not self.config_path.exists():
            return
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        for name in data.get("bots", []):
            cred_path = str(self.data_dir / f"{name}.json")
            self._bots[name] = IlinkBotClient(cred_path=cred_path)

    def _save_config(self) -> None:
        data = {"bots": list(self._bots.keys())}
        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
