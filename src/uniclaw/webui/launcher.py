"""WebUI 模式启动入口。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import uvicorn

from uniclaw.utils.logger import get_logger
import ipaddress
import socket

logger = get_logger("webui", Path.cwd())


def _make_streams_encoding_safe() -> None:
    """Windows 控制台默认 GBK 编码,print 含 emoji 等增补平面字符时会抛
    UnicodeEncodeError 并中断流程(如重启路径)。统一改为替换模式。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


_make_streams_encoding_safe()

# 当前 uvicorn server 实例与启动参数(restart_agent 工具用于优雅关闭和重建进程)
_server_instance: uvicorn.Server | None = None
_LAUNCH_KWARGS: dict = {}
# serve() 是否已返回(True 表示关闭流程已完成,看门狗据此放行)
_serve_done = True

# 优雅关闭宽限期(秒):超过后强制退出,防止个别连接卡死关闭流程
_SHUTDOWN_GRACE_SECONDS = 15.0


def request_shutdown() -> None:
    """请求 uvicorn 优雅关闭。

    serve() 返回后由 launch() 检查重启标志并拉起新进程。
    供 tools/restart.py 等外部模块触发进程重启。
    同时启动看门狗:宽限期内未完成关闭则强制退出,防止个别
    WebSocket 连接或收尾任务把关闭流程无限期挂住。
    """
    global _serve_done

    if _server_instance is None:
        return
    _server_instance.should_exit = True
    _serve_done = False
    asyncio.create_task(_force_exit_watchdog())


async def _force_exit_watchdog() -> None:
    """优雅关闭看门狗:宽限期后仍未完成则置 force_exit 强制退出。"""
    await asyncio.sleep(_SHUTDOWN_GRACE_SECONDS)
    if _serve_done or _server_instance is None:
        return
    logger.warning("[restart] 优雅关闭超时,强制退出服务")
    print("\n  [restart] 优雅关闭超时,强制退出服务...")
    try:
        sys.stdout.flush()
    except Exception:
        pass
    _server_instance.force_exit = True


async def launch(
    host: str = "127.0.0.1", port: int = 8080, ssl: bool = False, domain: str = ""
):
    """启动 WebUI 模式。

    不在启动时创建 config — root_dir 由前端第一条消息指定。
    config 在 create_session 时按需创建。

    Args:
        host: 监听地址,默认 127.0.0.1(仅本地访问),0.0.0.0 允许局域网访问
        port: 端口号,默认 8080
        ssl: 是否启用 HTTPS(自动生成自签名证书)
        domain: 可选的域名,如 "uniclaw.example.com"
    """
    global _server_instance, _LAUNCH_KWARGS

    from uniclaw.tools.scheduler.scheduler import Scheduler

    await Scheduler.get_instance().start()

    logger = get_logger("webui", Path.cwd())

    # Windows ProactorEventLoop 在 HTTPS/WSS 连接关闭时会抛出
    # _ProactorBasePipeTransport._call_connection_lost() 异常
    # (ConnectionResetError: [WinError 10054]),通过自定义异常处理器过滤此无害错误
    loop = asyncio.get_running_loop()
    _default_handler = loop.get_exception_handler() or loop.default_exception_handler

    def _suppress_connection_reset(loop, context):
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError):
            return
        _default_handler(context)

    loop.set_exception_handler(_suppress_connection_reset)

    # SSL 配置
    ssl_keyfile = None
    ssl_certfile = None
    protocol = "http"

    if ssl:
        try:
            ssl_keyfile, ssl_certfile = _ensure_ssl_certs(domain)
            protocol = "https"
            logger.info(f"已启用 HTTPS 模式,证书: {ssl_certfile}")
            print(f"  SSL 证书: {ssl_certfile}")
        except Exception as e:
            logger.warning(f"SSL 证书生成失败,回退到 HTTP 模式: {e}")
            print(f"  ⚠️ SSL 证书生成失败,使用 HTTP 模式: {e}")
            ssl = False

    logger.info(f"启动 WebUI 模式,地址: {host}:{port}")

    print(f"\n  UniClaw WebUI 已启动")
    if host in ("0.0.0.0", "::"):
        # 显示本机 IP 地址方便局域网访问
        local_ip = _get_local_ip(is_ipv6=_is_ipv6(host))
        print(f"  本地访问: {protocol}://localhost:{port}")
        if local_ip:
            ip_display = f"[{local_ip}]" if _is_ipv6(local_ip) else local_ip
            print(f"  局域网访问: {protocol}://{ip_display}:{port}")
        else:
            print(f"  局域网访问: {protocol}://<本机IP>:{port}")
    else:
        display_host = f"[{host}]" if _is_ipv6(host) else host
        print(f"  请在浏览器中打开: {protocol}://{display_host}:{port}")
    print()

    config = uvicorn.Config(
        "uniclaw.webui.app:app",
        host=host,
        port=port,
        log_level="info",
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
        ws_ping_interval=30,  # 每 30 秒发送 ping,及时检测死连接
        ws_ping_timeout=10,  # 10 秒无 pong 响应则关闭连接
        timeout_graceful_shutdown=10,  # 关闭时等待连接的最长时间(秒),防止个别连接拖住重启
    )
    global _serve_done

    # 重启恢复:检测到旧进程留下的重启标志时,服务就绪后恢复会话并唤醒 agent
    from uniclaw.tools.restart import resumer

    resumer.schedule_pending_restart_resume(host, port)

    server = uvicorn.Server(config)
    _server_instance = server
    _LAUNCH_KWARGS = {"host": host, "port": port, "ssl": ssl, "domain": domain}
    try:
        await server.serve()
    finally:
        _serve_done = True

    # serve() 结束(优雅关闭完成,端口已释放):若存在重启标志则以相同参数拉起新进程
    from uniclaw.tools.restart import relauncher

    await relauncher.relaunch_if_pending(_LAUNCH_KWARGS)


def _ensure_ssl_certs(domain: str = "") -> tuple[str, str]:
    """确保 SSL 证书存在,不存在则自动生成自签名证书。"""
    from uniclaw.utils.ssl_cert import get_or_create_certs

    return get_or_create_certs(domain)


def _is_ipv6(host: str) -> bool:
    """判断地址是否为 IPv6。"""
    try:
        addr = ipaddress.ip_address(host)
        return addr.version == 6
    except ValueError:
        return False


def _get_local_ip(is_ipv6: bool = False) -> str:
    """获取本机局域网 IP 地址。

    Args:
        is_ipv6: True 时使用 IPv6 协议族,否则使用 IPv4。
    """
    if is_ipv6:
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
                s.connect(("2001:4860:4860::8888", 80))
                return s.getsockname()[0].split("%")[0]  # 去掉 scope ID
        except Exception as e:
            logger.debug("获取本机 IPv6 地址失败: %s", e)
            return ""
    else:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception as e:
            logger.debug("获取本机 IPv4 地址失败: %s", e)
            return ""
