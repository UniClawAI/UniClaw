"""Docker 沙箱管理器 — 封装所有 Docker 操作。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from uniclaw.utils.constants import TOOL_ERROR

from ..shell import smart_decode, STDERR_MARKER
from .models import ContainerInfo, ContainerStatus

logger = logging.getLogger(__name__)


class SandboxManager:
    """Docker 沙箱管理器(单例),管理容器和镜像的完整生命周期。"""

    _instance: "SandboxManager | None" = None

    def __init__(self):
        self._containers: dict[str, ContainerInfo] = {}  # name → ContainerInfo

    @classmethod
    def get_instance(cls) -> "SandboxManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 底层工具 ──────────────────────────────────────────────

    async def check_docker(self) -> str | None:
        """检查 Docker 是否可用,返回错误信息或 None。"""
        _, stderr, rc = await self._run(["docker", "info"], timeout=10)
        if rc != 0:
            return stderr.strip() or "Docker 服务未运行"
        return None

    async def _run(self, args: list[str], timeout: int = 60) -> tuple[str, str, int]:
        """执行 docker 命令,返回 (stdout, stderr, returncode)。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return (
                smart_decode(stdout_bytes),
                smart_decode(stderr_bytes),
                proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ("", f"命令超时({timeout}秒)", 1)
        except Exception as e:
            return ("", str(e), 1)

    # ── 容器操作 ──────────────────────────────────────────────

    async def create_container(
        self,
        image: str,
        network: bool = False,
        volumes: list[str] | None = None,
        ports: list[str] | None = None,
        memory: str = "256m",
        cpus: str = "1",
    ) -> str:
        """创建持久容器(后台 sleep infinity),返回结果信息。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        # 检查本地是否存在该镜像,不存在则拉取
        _, _, check_rc = await self._run(
            ["docker", "image", "inspect", image], timeout=10
        )
        if check_rc != 0:
            _, stderr, rc = await self._run(["docker", "pull", image], timeout=120)
            if rc != 0:
                return f"{TOOL_ERROR}: 拉取镜像 {image} 失败: {stderr.strip()}"

        # 生成唯一容器名
        name = f"uniclaw-sandbox-{uuid.uuid4().hex[:8]}"

        # 构建 docker create 命令
        cmd = ["docker", "create", "--name", name]
        # 安全参数(覆盖默认 memory/cpus)
        cmd += ["--memory", memory, "--cpus", cpus]
        cmd += ["--security-opt", "no-new-privileges"]
        if not network:
            cmd += ["--network", "none"]
        # 端口映射
        for port in ports or []:
            cmd += ["-p", port]
        # 挂载卷
        for vol in volumes or []:
            cmd += ["-v", vol]
        # 容器保持运行: 用 sleep infinity
        cmd += [image, "sleep", "infinity"]

        stdout, stderr, rc = await self._run(cmd)
        if rc != 0:
            return f"{TOOL_ERROR}: 创建容器失败: {stderr.strip()}"

        # 启动容器
        stdout, stderr, rc = await self._run(["docker", "start", name])
        if rc != 0:
            return f"{TOOL_ERROR}: 启动容器失败: {stderr.strip()}"

        # 注册到管理器
        info = ContainerInfo(
            name=name,
            image=image,
            network=network,
            volumes=volumes or [],
            ports=ports or [],
            memory=memory,
            cpus=cpus,
            status=ContainerStatus.running,
        )
        self._containers[name] = info

        vol_info = f", 挂载: {', '.join(volumes)}" if volumes else ""
        port_info = f", 端口: {', '.join(ports)}" if ports else ""
        net_info = "启用" if network else "禁用"
        return (
            f"容器创建成功\n"
            f"  名称: {name}\n"
            f"  镜像: {image}\n"
            f"  网络: {net_info}\n"
            f"  内存限制: {memory}, CPU 限制: {cpus}{vol_info}{port_info}\n\n"
            f'使用 DockerExec(name="{name}", command="...") 执行命令'
        )

    async def exec_in_container(
        self,
        name: str,
        command: str,
        shell: str = "sh",
        timeout: int = 60,
    ) -> str:
        """在容器中执行命令,返回输出。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        info = self._containers.get(name)
        if not info:
            return f"{TOOL_ERROR}: 容器 '{name}' 不存在或未被管理。使用 DockerList 查看可用容器"

        if info.status != ContainerStatus.running:
            return f"{TOOL_ERROR}: 容器 '{name}' 已停止,请先使用 DockerStart 启动或 DockerRemove 删除"

        # docker exec
        cmd = ["docker", "exec", name, shell, "-c", command]
        stdout, stderr, rc = await self._run(cmd, timeout=timeout)
        if rc != 0 and not stdout:
            return (
                f"{TOOL_ERROR}: {stderr.strip()}"
                if stderr
                else f"{TOOL_ERROR}: 命令执行失败(exit {rc})"
            )

        out = stdout
        if stderr:
            out += ("\n" if out else "") + STDERR_MARKER + stderr
        return out.strip() or "(没有输出)"

    async def stop_container(self, name: str) -> str:
        """停止容器。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        info = self._containers.get(name)
        if not info:
            return f"{TOOL_ERROR}: 容器 '{name}' 不存在或未被管理"

        if info.status == ContainerStatus.stopped:
            return f"容器 '{name}' 已经是停止状态"

        stdout, stderr, rc = await self._run(
            ["docker", "stop", "-t", "5", name], timeout=15
        )
        if rc != 0:
            return f"{TOOL_ERROR}: 停止容器失败: {stderr.strip()}"

        info.status = ContainerStatus.stopped
        return f"容器 '{name}' 已停止"

    async def start_container(self, name: str) -> str:
        """启动已停止的容器。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        info = self._containers.get(name)
        if not info:
            return f"{TOOL_ERROR}: 容器 '{name}' 不存在或未被管理"

        if info.status == ContainerStatus.running:
            return f"容器 '{name}' 已经是运行状态"

        _, stderr, rc = await self._run(["docker", "start", name])
        if rc != 0:
            return f"{TOOL_ERROR}: 启动容器失败: {stderr.strip()}"

        info.status = ContainerStatus.running
        return f"容器 '{name}' 已启动"

    async def remove_container(self, name: str) -> str:
        """停止并删除容器,从管理器移除。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        info = self._containers.get(name)
        if not info:
            # 尝试直接删除(可能是外部创建的容器)
            _, stderr, rc = await self._run(["docker", "rm", "-f", name])
            if rc != 0:
                return f"{TOOL_ERROR}: 容器 '{name}' 不存在或删除失败: {stderr.strip()}"
            return f"容器 '{name}' 已删除"

        # 停止 + 删除
        await self._run(["docker", "stop", "-t", "5", name], timeout=15)
        _, stderr, rc = await self._run(["docker", "rm", "-f", name])
        if rc != 0:
            return f"{TOOL_ERROR}: 删除容器失败: {stderr.strip()}"

        del self._containers[name]
        return f"容器 '{name}' 已删除"

    async def list_containers(self) -> str:
        """列出所有管理的容器及状态。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        if not self._containers:
            return "当前没有管理中的容器。使用 DockerCreate 创建容器"

        # 用 docker inspect 检查实际状态
        lines = ["管理中的容器:"]
        stale = []
        for name, info in self._containers.items():
            stdout, _, rc = await self._run(
                ["docker", "inspect", "-f", "{{.State.Status}}", name]
            )
            actual = stdout.strip() if rc == 0 else "not_found"
            if actual == "not_found":
                stale.append(name)
                continue
            if actual == "running":
                info.status = ContainerStatus.running
            else:
                info.status = ContainerStatus.stopped

            net = "是" if info.network else "否"
            vol = ", ".join(info.volumes) if info.volumes else "无"
            port = ", ".join(info.ports) if info.ports else "无"
            lines.append(
                f"  [{info.status.value}] {name}\n"
                f"    镜像: {info.image} | 网络: {net} | 端口: {port} | 挂载: {vol}\n"
                f"    资源: 内存 {info.memory}, CPU {info.cpus}"
            )

        # 清理已消失的容器
        for name in stale:
            del self._containers[name]

        if len(lines) == 1:
            return "当前没有管理中的容器。使用 DockerCreate 创建容器"
        return "\n".join(lines)

    # ── 镜像操作 ──────────────────────────────────────────────

    async def pull_image(self, image: str) -> str:
        """拉取镜像。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        stdout, stderr, rc = await self._run(["docker", "pull", image], timeout=300)
        if rc != 0:
            return f"{TOOL_ERROR}: 拉取镜像 {image} 失败: {stderr.strip()}"
        return f"镜像 {image} 拉取成功\n{stdout.strip()}"

    async def list_images(self) -> str:
        """列出本地镜像。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        stdout, stderr, rc = await self._run(
            [
                "docker",
                "images",
                "--format",
                "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}",
            ]
        )
        if rc != 0:
            return f"{TOOL_ERROR}: 获取镜像列表失败: {stderr.strip()}"
        return stdout.strip() or "没有本地镜像"

    async def search_image(self, keyword: str, limit: int = 10) -> str:
        """搜索 Docker Hub 镜像。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        stdout, stderr, rc = await self._run(
            [
                "docker",
                "search",
                "--format",
                "table {{.Name}}\t{{.Description}}\t{{.StarCount}}\t{{.IsOfficial}}",
                keyword,
            ],
            timeout=30,
        )
        if rc != 0:
            return f"{TOOL_ERROR}: 搜索失败: {stderr.strip()}"

        lines = stdout.strip().split("\n")
        if len(lines) <= 1:
            return f"未找到与 '{keyword}' 相关的镜像"
        # 限制返回数量(+1 因为第一行是表头)
        header = lines[0]
        results = lines[1 : limit + 1]
        return f"{header}\n" + "\n".join(results)

    async def remove_image(self, image: str, force: bool = False) -> str:
        """删除镜像。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        cmd = ["docker", "rmi"]
        if force:
            cmd.append("-f")
        cmd.append(image)

        stdout, stderr, rc = await self._run(cmd)
        if rc != 0:
            return f"{TOOL_ERROR}: 删除镜像失败: {stderr.strip()}"
        return f"镜像 {image} 已删除\n{stdout.strip()}"

    async def build_image(
        self, path: str, tag: str, dockerfile: str = "Dockerfile"
    ) -> str:
        """从 Dockerfile 构建镜像。"""
        err = await self.check_docker()
        if err:
            return f"{TOOL_ERROR}: {err}"

        cmd = [
            "docker",
            "build",
            "-f",
            f"{path}/{dockerfile}",
            "-t",
            tag,
            path,
        ]

        stdout, stderr, rc = await self._run(cmd, timeout=600)
        if rc != 0:
            return f"{TOOL_ERROR}: 构建镜像失败:\n{stderr.strip()}"
        return f"镜像 {tag} 构建成功\n{stdout.strip()}"
