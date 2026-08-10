"""Docker 沙箱数据模型。"""

from dataclasses import dataclass, field
from enum import StrEnum


class ContainerStatus(StrEnum):
    """容器状态枚举。"""

    running = "running"
    stopped = "stopped"


@dataclass
class ContainerInfo:
    """运行中的容器信息。"""

    name: str  # uniclaw-sandbox-<uuid8>
    image: str
    network: bool
    volumes: list[str] = field(default_factory=list)  # ["host:container", ...]
    ports: list[str] = field(default_factory=list)  # ["host_port:container_port", ...]
    memory: str = "256m"
    cpus: str = "1"
    status: ContainerStatus = ContainerStatus.running
