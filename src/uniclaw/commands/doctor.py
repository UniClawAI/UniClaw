"""环境诊断命令"""

import asyncio
import os
import shutil
import sys
from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn, err
from uniclaw.context import APP_NAME


async def _run_tool_version(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    return stdout.decode("utf-8", errors="replace").strip()


async def cmd_doctor(_args: str, config: AppConfig) -> bool:
    """环境诊断

    检查 UniClaw 运行环境的各项依赖和配置状态。
    """
    root_dir = config.current_agent.session.root_dir

    await info(f"\n🔍 {APP_NAME} 环境诊断报告\n", config)
    await info("─" * 50, config)

    pass_count = 0
    warn_count = 0
    fail_count = 0

    async def _check(coro):
        nonlocal pass_count, warn_count, fail_count
        try:
            result = await coro
            await ok(f"  ✅ {result}", config)
            pass_count += 1
        except FileNotFoundError as e:
            await warn(f"  ⚠️  {e}", config)
            warn_count += 1
        except Exception as e:
            await err(f"  ❌ {e}", config)
            fail_count += 1

    # Python
    v = sys.version_info
    await _check(_sync(f"Python {v.major}.{v.minor}.{v.micro}"))

    # 配置文件
    async def _cfg():
        from uniclaw.config import get_config_path

        path = get_config_path()
        if path.exists():
            return f"配置文件存在: {path}"
        raise FileNotFoundError(f"配置文件不存在: {path}")

    await _check(_cfg())

    # API 配置
    async def _api():
        if not config.providers:
            raise ValueError("未配置任何 provider,请运行 /model 配置")
        names = ", ".join(config.providers.keys())
        return f"Providers: {names}"

    await _check(_api())

    # 当前模型
    model_str = ", ".join(config.model_name) if config.model_name else "未配置"
    await _check(_sync(f"模型: {model_str}"))

    # Git
    async def _git():
        if not shutil.which("git"):
            raise FileNotFoundError("git 未安装")
        return await _run_tool_version("git", "--version")

    await _check(_git())

    # GitHub CLI
    async def _gh():
        if not shutil.which("gh"):
            raise FileNotFoundError("gh CLI 未安装(PR 管理功能不可用)")
        return (await _run_tool_version("gh", "--version")).split("\n")[0]

    await _check(_gh())

    # Docker
    async def _docker():
        if not shutil.which("docker"):
            raise FileNotFoundError("Docker 未安装(沙箱功能不可用)")
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            raise RuntimeError("Docker 未运行(沙箱功能不可用)")
        return "Docker 运行中"

    await _check(_docker())

    # ripgrep
    async def _rg():
        if not shutil.which("rg"):
            raise FileNotFoundError("ripgrep 未安装(Grep 将使用 Python 降级方案)")
        return (await _run_tool_version("rg", "--version")).split("\n")[0]

    await _check(_rg())

    # 工作目录
    async def _ws():
        if root_dir is None:
            return "工作目录: 未设置(微信模式下使用进程目录)"
        if not root_dir.exists():
            raise FileNotFoundError(f"工作目录不存在: {root_dir}")
        if not os.access(root_dir, os.W_OK):
            raise PermissionError(f"工作目录不可写: {root_dir}")
        return f"工作目录: {root_dir}"

    await _check(_ws())

    # 项目目录
    async def _app():
        from uniclaw.context import get_app_dir, Scope

        d = get_app_dir(root_dir) if root_dir else get_app_dir(Scope.USER)
        if d.exists():
            return f"项目目录: {d}"
        return f"项目目录不存在(首次使用时自动创建): {d}"

    await _check(_app())

    # 技能系统
    async def _skills():
        from uniclaw.tools.skill.loader import load_skills, get_builtin_skills

        all_skills = load_skills(root_dir)
        builtin = get_builtin_skills()
        user = [s for s in all_skills if s.source == "user"]
        project = [s for s in all_skills if s.source == "project"]
        return f"技能: {len(builtin)} 内置, {len(user)} 用户, {len(project)} 项目"

    await _check(_skills())

    # MCP 服务
    async def _mcp():
        from uniclaw.tools.mcp import MCPManager

        mgr = MCPManager.get_instance()
        servers = mgr.get_servers() if hasattr(mgr, "get_servers") else []
        if not servers:
            return "MCP: 无已配置的服务器"
        online = sum(1 for s in servers if getattr(s, "status", None) == "connected")
        return f"MCP: {online}/{len(servers)} 在线"

    await _check(_mcp())

    await info("─" * 50, config)
    total = pass_count + warn_count + fail_count
    await info(
        f"\n  总计: {pass_count} 通过, {warn_count} 警告, {fail_count} 失败 / {total} 项\n",
        config,
    )

    return True


async def _sync(value: str) -> str:
    """包装同步值为协程,统一 _check 接口。"""
    return value
