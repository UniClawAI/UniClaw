import argparse
import asyncio
import os
import threading
import warnings

# jieba 0.42.1 使用了非 raw 字符串的正则表达式,在 Python 3.12+ 触发 SyntaxWarning
warnings.filterwarnings("ignore", category=SyntaxWarning)
from uniclaw.config import is_first_launch, run_setup_wizard


def main():
    parser = argparse.ArgumentParser(description="UniClaw - AI Agent")
    parser.add_argument(
        "--mode",
        choices=["console", "webui"],
        default="console",
        help="启动模式: console(控制台, 默认), webui(Web界面)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="WebUI 模式的端口号 (默认: 8080)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="WebUI 模式的监听地址 (默认: 127.0.0.1,局域网访问用 0.0.0.0)",
    )
    parser.add_argument(
        "--no-ssl",
        action="store_true",
        help="禁用 HTTPS (使用 HTTP)",
    )
    parser.add_argument(
        "--domain",
        type=str,
        default="",
        help="域名 (如 uniclaw.example.com)",
    )
    args = parser.parse_args()

    # 后台预加载 tiktoken 编码器,避免首次调用时同步下载阻塞事件循环
    def _preload_tiktoken():
        try:
            import tiktoken

            tiktoken.get_encoding("cl100k_base")
            tiktoken.get_encoding("o200k_base")
        except Exception:
            pass

    # 后台预加载 OpenAI SDK 模块,避免首次 API 调用时同步 import 阻塞事件循环
    def _preload_openai():
        try:
            import openai.resources.audio  # noqa: F401
            import openai.types.audio  # noqa: F401
        except Exception:
            pass

    # 后台预加载 MCP 模块,避免首次使用 MCP 工具时同步 import 阻塞事件循环
    def _preload_mcp():
        try:
            import mcp  # noqa: F401
        except Exception:
            pass

    threading.Thread(target=_preload_tiktoken, daemon=True).start()
    threading.Thread(target=_preload_openai, daemon=True).start()
    threading.Thread(target=_preload_mcp, daemon=True).start()

    if args.mode == "webui":
        from uniclaw.webui.launcher import launch
    else:
        from uniclaw.console.launcher import launch

    if args.mode == "webui":
        ssl = not args.no_ssl  # 默认启用 SSL
        asyncio.run(launch(host=args.host, port=args.port, ssl=ssl, domain=args.domain))
    else:
        # 首次启动引导(console 模式)
        if is_first_launch():
            asyncio.run(run_setup_wizard())
        asyncio.run(launch())


if __name__ == "__main__":
    main()
