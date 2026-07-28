import asyncio
import os
import shutil
import time
import tempfile
from enum import StrEnum
from pathlib import Path
from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR

from .shell import smart_decode, STDERR_MARKER


class SandboxLanguage(StrEnum):
    """沙箱支持的编程语言。"""

    python = "python"
    py = "py"
    javascript = "javascript"
    js = "js"
    shell = "shell"
    bash = "bash"

LANG_CONFIG = {
    "python": {
        "ext": ".py",
        "image": "python:3-slim",
        "cmd": ["python", "/code/code.py"],
    },
    "py": {"ext": ".py", "image": "python:3-slim", "cmd": ["python", "/code/code.py"]},
    "javascript": {
        "ext": ".js",
        "image": "node:slim",
        "cmd": ["node", "/code/code.js"],
    },
    "js": {"ext": ".js", "image": "node:slim", "cmd": ["node", "/code/code.js"]},
    "shell": {"ext": ".sh", "image": "alpine", "cmd": ["sh", "/code/code.sh"]},
    "bash": {"ext": ".sh", "image": "bash", "cmd": ["bash", "/code/code.sh"]},
}

# Docker 安全参数(基础)
_DOCKER_SECURITY = [
    "--rm",
    "--memory",
    "256m",
    "--cpus",
    "1",
    "--security-opt",
    "no-new-privileges",
]


async def _check_docker() -> str | None:
    """检查 Docker 是否可用,返回错误信息或 None"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            return smart_decode(stderr_bytes).strip() or "Docker 服务未运行"
    except FileNotFoundError:
        return "未找到 docker 命令,请安装 Docker"
    except asyncio.TimeoutError:
        return "Docker 响应超时,请检查 Docker Desktop 状态"
    except Exception as e:
        return str(e)
    return None


async def _pull_image(image: str) -> str | None:
    """拉取镜像,返回错误信息或 None"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "pull",
            image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            return f"拉取镜像 {image} 失败: {smart_decode(stderr_bytes).strip()}"
    except asyncio.TimeoutError:
        return f"拉取镜像 {image} 超时"
    return None


@tool
async def RunCode(
    language: SandboxLanguage, code: str, timeout: int = 30, network: bool = False
) -> str:
    """
    在 Docker 沙箱中安全运行代码片段并返回输出。

    代码在隔离的 Docker 容器中执行,具有以下安全限制:
    - 默认禁止网络访问(network=true 可开启)
    - 内存限制 256MB,CPU 限制 1 核
    - 禁止提权
    - 超时自动终止

    支持语言: python/py, javascript/js, shell/bash

    Args:
        language: 编程语言
        code: 要执行的代码字符串
        timeout: 超时秒数,默认 30
        network: 是否启用网络访问,默认 False。需要网络时(如 HTTP 请求)设为 True

    Returns:
        str: 代码执行的输出结果
    """
    lang = language.lower().strip()
    if lang not in LANG_CONFIG:
        return f"{TOOL_ERROR}: 不支持的语言 '{language}',支持: {', '.join(SandboxLanguage)}"

    if not code.strip():
        return f"{TOOL_ERROR}: 代码不能为空"

    cfg = LANG_CONFIG[lang]

    # 拉取镜像(如需要)
    pull_err = await _pull_image(cfg["image"])
    if pull_err:
        return f"{TOOL_ERROR}: {pull_err}"

    # 写入临时代码文件
    tmp_dir = tempfile.mkdtemp(prefix="sandbox_")
    code_file = Path(tmp_dir) / f"code{cfg['ext']}"
    code_file.write_text(code, encoding="utf-8")

    # 构建 docker run 命令
    cmd = ["docker", "run", *_DOCKER_SECURITY]
    if not network:
        cmd += ["--network", "none"]
    cmd += [
        "--stop-timeout",
        str(timeout),
        "-v",
        f"{Path(tmp_dir).as_posix()}:/code:ro",
        cfg["image"],
        *cfg["cmd"],
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout + 5,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"{TOOL_ERROR}: 执行超时({timeout} 秒),容器已终止"
        except Exception:
            proc.kill()
            await proc.wait()
            raise

        stdout = smart_decode(stdout_bytes)
        stderr = smart_decode(stderr_bytes)

        out = stdout
        if stderr:
            out += ("\n" if out else "") + STDERR_MARKER + stderr
        return out.strip() or "(没有输出)"

    except Exception as e:
        return f"{TOOL_ERROR}: {e}"
    finally:
        # 清理临时文件和目录
        shutil.rmtree(tmp_dir, ignore_errors=True)


# Docker 检测结果缓存(3分钟过期)
_docker_cache: dict = {"result": None, "time": 0}
_docker_cache_ttl = 60 * 30


async def get_tools(config=None) -> list:
    """获取沙箱工具列表(根据 Docker 可用性动态返回,带缓存)"""
    now = time.monotonic()
    if (
        _docker_cache["result"] is not None
        and now - _docker_cache["time"] < _docker_cache_ttl
    ):
        return _docker_cache["result"]

    from uniclaw.console.ui import warn

    _docker_err = await _check_docker()
    if _docker_err:

        await warn(f"[sandbox] Docker 不可用: {_docker_err},RunCode 工具已禁用。", config)
        result = []
    else:
        result = [RunCode]

    _docker_cache["result"] = result
    _docker_cache["time"] = now
    return result


def get_all_tools() -> list:
    """获取所有沙箱工具(无条件返回)"""
    return [RunCode]
