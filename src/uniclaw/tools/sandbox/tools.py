"""Docker 沙箱工具 — 容器与镜像的完整生命周期管理。"""

import time

from uniclaw.config import AppConfig
from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR

# Docker 检测结果缓存(30 分钟过期)
_docker_cache: dict = {"result": None, "time": 0}
_docker_cache_ttl = 60 * 30


# ── 容器管理工具 ──────────────────────────────────────────────


@tool
async def DockerCreate(
    image: str,
    network: bool = False,
    volumes: list[str] | None = None,
    ports: list[str] | None = None,
    memory: str = "256m",
    cpus: str = "1",
) -> str:
    """
    创建持久 Docker 容器。容器在后台保持运行,可反复使用 DockerExec 执行命令。

    适合需要多次执行命令、安装依赖后继续使用的场景。
    通过 volumes 参数挂载目录,可在容器和宿主机之间共享数据。
    通过 ports 参数映射端口,可从宿主机访问容器内服务。

    Args:
        image: Docker 镜像名(如 "python:3-slim", "node:slim", "ubuntu:latest")
        network: 是否启用网络访问,默认 False。需要网络时(如 pip install)设为 True
        volumes: 目录挂载列表,格式为 ["宿主机路径:容器路径"]。
            例如 ["D:/output:/output", "/data:/data"],容器内 /output 对应宿主机 D:/output。
            留空或 None 表示不挂载。
        ports: 端口映射列表,格式为 ["主机端口:容器端口"]。
            例如 ["8080:80", "3000:3000"],将容器端口映射到宿主机。
            留空或 None 表示不映射。
        memory: 内存限制,默认 "256m"
        cpus: CPU 核数限制,默认 "1"

    Returns:
        str: 创建结果,包含容器名和使用说明
    """
    if not image.strip():
        return f"{TOOL_ERROR}: 镜像名不能为空"

    # 校验 volumes 格式
    if volumes:
        for vol in volumes:
            if ":" not in vol:
                return f"{TOOL_ERROR}: 挂载格式错误 '{vol}',应为 '宿主机路径:容器路径'"

    # 校验 ports 格式
    if ports:
        for port in ports:
            if ":" not in port:
                return f"{TOOL_ERROR}: 端口格式错误 '{port}',应为 '主机端口:容器端口'"

    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.create_container(
        image=image.strip(),
        network=network,
        volumes=volumes,
        ports=ports,
        memory=memory,
        cpus=cpus,
    )


@tool
async def DockerExec(
    name: str,
    command: str,
    shell: str = "sh",
    timeout: int = 60,
) -> str:
    """
    在已有的 Docker 容器中执行命令并返回输出。

    容器需先通过 DockerCreate 创建。可在同一容器中多次调用执行不同命令,
    容器内的文件系统状态会保持(安装的包、创建的文件等)。

    Args:
        name: 容器名称,通过 DockerCreate 返回值或 DockerList 获取
        command: 要执行的命令(如 "pip install requests", "python -c 'print(1)'")
        shell: 使用的 shell,默认 "sh"。可选 "bash"
        timeout: 超时秒数,默认 60

    Returns:
        str: 命令执行的输出结果
    """
    if not name.strip():
        return f"{TOOL_ERROR}: 容器名不能为空"
    if not command.strip():
        return f"{TOOL_ERROR}: 命令不能为空"

    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.exec_in_container(
        name=name.strip(),
        command=command.strip(),
        shell=shell.strip(),
        timeout=timeout,
    )


@tool
async def DockerStop(name: str) -> str:
    """
    停止运行中的 Docker 容器。停止后可使用 DockerStart 重新启动。

    Args:
        name: 容器名称,通过 DockerCreate 返回值或 DockerList 获取

    Returns:
        str: 操作结果
    """
    if not name.strip():
        return f"{TOOL_ERROR}: 容器名不能为空"

    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.stop_container(name.strip())


@tool
async def DockerStart(name: str) -> str:
    """
    启动已停止的 Docker 容器。容器内的文件系统状态会保持。

    Args:
        name: 容器名称,通过 DockerCreate 返回值或 DockerList 获取

    Returns:
        str: 操作结果
    """
    if not name.strip():
        return f"{TOOL_ERROR}: 容器名不能为空"

    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.start_container(name.strip())


@tool
async def DockerRemove(name: str) -> str:
    """
    停止并删除 Docker 容器,释放所有资源。

    容器内的未挂载数据将丢失,请确保已通过挂载目录备份重要数据。

    Args:
        name: 容器名称,通过 DockerCreate 返回值或 DockerList 获取

    Returns:
        str: 操作结果
    """
    if not name.strip():
        return f"{TOOL_ERROR}: 容器名不能为空"

    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.remove_container(name.strip())


@tool
async def DockerList() -> str:
    """
    列出所有由 DockerCreate 创建的容器及其状态。

    显示容器名、镜像、网络、挂载、资源限制等信息。
    自动检测容器实际运行状态,清理已消失的容器。

    Returns:
        str: 容器列表
    """
    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.list_containers()


# ── 镜像管理工具 ──────────────────────────────────────────────


@tool
async def DockerPull(image: str) -> str:
    """
    拉取 Docker 镜像到本地。如果镜像已存在则更新到最新版本。

    Args:
        image: 镜像名(如 "python:3-slim", "node:20-alpine")

    Returns:
        str: 拉取结果
    """
    if not image.strip():
        return f"{TOOL_ERROR}: 镜像名不能为空"

    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.pull_image(image.strip())


@tool
async def DockerImages() -> str:
    """
    列出本地所有 Docker 镜像。

    显示镜像名、标签、大小、创建时间等信息。

    Returns:
        str: 镜像列表
    """
    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.list_images()


@tool
async def DockerSearch(keyword: str, limit: int = 10) -> str:
    """
    在 Docker Hub 搜索镜像。

    Args:
        keyword: 搜索关键词(如 "python", "nginx", "redis")
        limit: 返回结果数量,默认 10

    Returns:
        str: 搜索结果,包含镜像名、描述、星数、是否官方
    """
    if not keyword.strip():
        return f"{TOOL_ERROR}: 搜索关键词不能为空"

    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.search_image(keyword.strip(), limit=limit)


@tool
async def DockerRemoveImage(image: str, force: bool = False) -> str:
    """
    删除本地 Docker 镜像。

    如果镜像被容器使用,需要先删除容器或设置 force=True 强制删除。

    Args:
        image: 镜像名(如 "python:3-slim")
        force: 是否强制删除(即使有容器引用),默认 False

    Returns:
        str: 删除结果
    """
    if not image.strip():
        return f"{TOOL_ERROR}: 镜像名不能为空"

    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.remove_image(image.strip(), force=force)


@tool
async def DockerBuild(
    path: str,
    tag: str,
    dockerfile: str = "Dockerfile",
) -> str:
    """
    从 Dockerfile 构建 Docker 镜像。

    Args:
        path: 构建上下文目录路径(包含 Dockerfile 的目录)
        tag: 镜像标签(如 "myapp:latest", "test:v1")
        dockerfile: Dockerfile 文件名,默认 "Dockerfile"

    Returns:
        str: 构建结果
    """
    if not path.strip():
        return f"{TOOL_ERROR}: 构建目录不能为空"
    if not tag.strip():
        return f"{TOOL_ERROR}: 镜像标签不能为空"

    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    return await manager.build_image(
        path=path.strip(),
        tag=tag.strip(),
        dockerfile=dockerfile.strip(),
    )


# ── 工具列表 ──────────────────────────────────────────────────

_ALL_TOOLS = [
    DockerCreate,
    DockerExec,
    DockerStart,
    DockerStop,
    DockerRemove,
    DockerList,
    DockerPull,
    DockerSearch,
    DockerImages,
    DockerRemoveImage,
    DockerBuild,
]


async def get_tools(config: AppConfig = None) -> list:
    """获取沙箱工具列表(根据 Docker 可用性动态返回,带缓存)。"""
    now = time.monotonic()
    if (
        _docker_cache["result"] is not None
        and now - _docker_cache["time"] < _docker_cache_ttl
    ):
        return _docker_cache["result"]

    from uniclaw.console.ui import warn

    from .manager import SandboxManager

    manager = SandboxManager.get_instance()
    docker_err = await manager.check_docker()
    if docker_err:
        await warn(
            f"[sandbox] Docker 不可用: {docker_err},沙箱工具已禁用。",
            config,
        )
        result = []
    else:
        result = list(_ALL_TOOLS)

    _docker_cache["result"] = result
    _docker_cache["time"] = now
    return result


def get_all_tools() -> list:
    """获取所有沙箱工具(无条件返回)。"""
    return list(_ALL_TOOLS)
