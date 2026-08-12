"""
Docker 沙箱工具全面测试
需要 Docker 环境才能运行
"""

import asyncio
import pytest

from uniclaw.tools.sandbox.tools import (
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
    get_tools,
    get_all_tools,
)
from uniclaw.tools.sandbox.manager import SandboxManager
from uniclaw.tools.sandbox.models import ContainerInfo, ContainerStatus


def _is_docker_available() -> bool:
    """同步检查 Docker 是否可用"""
    try:
        manager = SandboxManager.get_instance()
        return asyncio.run(manager.check_docker()) is None
    except Exception:
        return False


def _extract_container_name(result: str) -> str:
    """从 DockerCreate 返回结果中提取容器名"""
    for line in result.split("\n"):
        if line.strip().startswith("名称:"):
            return line.split(": ")[1].strip()
    raise ValueError(f"无法从结果中提取容器名: {result}")


# 跳过整个模块如果 Docker 不可用
pytestmark = [
    pytest.mark.skipif(
        not _is_docker_available(),
        reason="Docker 不可用",
    ),
    pytest.mark.slow,
]


# ════════════════════════════════════════════════════════════════
# DockerCreate - 容器创建
# ════════════════════════════════════════════════════════════════


class TestDockerCreate:
    """容器创建测试"""

    def test_create_basic(self):
        """基本创建"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        assert "创建成功" in result
        assert "uniclaw-sandbox-" in result
        name = _extract_container_name(result)
        asyncio.run(DockerRemove.func(name=name))

    def test_create_with_network(self):
        """启用网络"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim", network=True))
        assert "网络: 启用" in result
        name = _extract_container_name(result)
        asyncio.run(DockerRemove.func(name=name))

    def test_create_without_network(self):
        """禁用网络(默认)"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim", network=False))
        assert "网络: 禁用" in result
        name = _extract_container_name(result)
        asyncio.run(DockerRemove.func(name=name))

    def test_create_with_volume(self, tmp_path):
        """目录挂载"""
        vol = f"{tmp_path.as_posix()}:/data"
        result = asyncio.run(DockerCreate.func(image="python:3-slim", volumes=[vol]))
        assert "创建成功" in result
        assert vol in result
        name = _extract_container_name(result)
        asyncio.run(DockerRemove.func(name=name))

    def test_create_with_multiple_volumes(self, tmp_path):
        """多目录挂载"""
        vol1 = f"{tmp_path.as_posix()}:/data1"
        vol2 = f"{tmp_path.as_posix()}:/data2"
        result = asyncio.run(
            DockerCreate.func(image="python:3-slim", volumes=[vol1, vol2])
        )
        assert "创建成功" in result
        name = _extract_container_name(result)
        asyncio.run(DockerRemove.func(name=name))

    def test_create_with_ports(self):
        """端口映射"""
        result = asyncio.run(
            DockerCreate.func(image="python:3-slim", ports=["18080:80"])
        )
        assert "创建成功" in result
        assert "18080:80" in result
        name = _extract_container_name(result)
        asyncio.run(DockerRemove.func(name=name))

    def test_create_with_multiple_ports(self):
        """多端口映射"""
        result = asyncio.run(
            DockerCreate.func(image="python:3-slim", ports=["18080:80", "13000:3000"])
        )
        assert "创建成功" in result
        name = _extract_container_name(result)
        asyncio.run(DockerRemove.func(name=name))

    def test_create_custom_resource(self):
        """自定义资源限制"""
        result = asyncio.run(
            DockerCreate.func(image="python:3-slim", memory="512m", cpus="2")
        )
        assert "内存限制: 512m" in result
        assert "CPU 限制: 2" in result
        name = _extract_container_name(result)
        asyncio.run(DockerRemove.func(name=name))

    def test_create_alpine(self):
        """创建 alpine 容器"""
        result = asyncio.run(DockerCreate.func(image="alpine"))
        assert "创建成功" in result
        name = _extract_container_name(result)
        asyncio.run(DockerRemove.func(name=name))

    def test_create_empty_image(self):
        """空镜像名"""
        result = asyncio.run(DockerCreate.func(image=""))
        assert "ERROR" in result

    def test_create_invalid_volume_format(self):
        """无效挂载格式"""
        result = asyncio.run(
            DockerCreate.func(image="python:3-slim", volumes=["invalid_format"])
        )
        assert "ERROR" in result
        assert "格式" in result

    def test_create_invalid_port_format(self):
        """无效端口格式"""
        result = asyncio.run(
            DockerCreate.func(image="python:3-slim", ports=["invalid"])
        )
        assert "ERROR" in result
        assert "格式" in result


# ════════════════════════════════════════════════════════════════
# DockerExec - 命令执行
# ════════════════════════════════════════════════════════════════


class TestDockerExec:
    """命令执行测试"""

    def test_exec_echo(self):
        """基本命令执行"""
        result = asyncio.run(DockerCreate.func(image="alpine"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(DockerExec.func(name=name, command="echo hello"))
            assert output.strip() == "hello"
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_exec_python(self):
        """Python 命令执行"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(
                DockerExec.func(name=name, command="python -c 'print(1+1)'")
            )
            assert "2" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_exec_multiline_python(self):
        """多行 Python 代码"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            code = "for i in range(3): print(i)"
            output = asyncio.run(
                DockerExec.func(name=name, command=code, shell="python")
            )
            assert "0" in output
            assert "1" in output
            assert "2" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_exec_multiple_commands_same_container(self):
        """同一容器多次执行命令"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            # 写文件
            asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"with open('/tmp/test.txt', 'w') as f: f.write('step1')\"",
                )
            )
            # 读文件 - 验证状态保持
            output = asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"print(open('/tmp/test.txt').read())\"",
                )
            )
            assert "step1" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_exec_install_package(self):
        """安装 Python 包并使用"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim", network=True))
        name = _extract_container_name(result)
        try:
            # 安装
            asyncio.run(
                DockerExec.func(name=name, command="pip install -q pyjokes", timeout=60)
            )
            # 使用
            output = asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c 'import pyjokes; print(pyjokes.get_joke())'",
                )
            )
            assert len(output.strip()) > 0
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_exec_with_bash_shell(self):
        """使用 bash shell"""
        result = asyncio.run(DockerCreate.func(image="bash"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(
                DockerExec.func(name=name, command="echo $((2+3))", shell="bash")
            )
            assert "5" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_exec_stderr_output(self):
        """stderr 输出"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c 'import sys; sys.stderr.write(\"err msg\")'",
                )
            )
            assert "err msg" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_exec_syntax_error(self):
        """语法错误"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(
                DockerExec.func(name=name, command="def foo(", shell="python")
            )
            assert "Error" in output or "SyntaxError" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_exec_runtime_error(self):
        """运行时错误"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(
                DockerExec.func(
                    name=name,
                    command="raise ValueError('test error')",
                    shell="python",
                )
            )
            assert "ValueError" in output
            assert "test error" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_exec_nonexistent_container(self):
        """不存在的容器"""
        output = asyncio.run(
            DockerExec.func(name="nonexistent-xyz", command="echo test")
        )
        assert "ERROR" in output or "不存在" in output

    def test_exec_empty_name(self):
        """空容器名"""
        output = asyncio.run(DockerExec.func(name="", command="echo test"))
        assert "ERROR" in output

    def test_exec_empty_command(self):
        """空命令"""
        result = asyncio.run(DockerCreate.func(image="alpine"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(DockerExec.func(name=name, command=""))
            assert "ERROR" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))


# ════════════════════════════════════════════════════════════════
# DockerExec - 超时
# ════════════════════════════════════════════════════════════════


class TestDockerExecTimeout:
    """超时测试"""

    def test_timeout(self):
        """命令超时"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(
                DockerExec.func(
                    name=name,
                    command="import time; time.sleep(60)",
                    shell="python",
                    timeout=3,
                )
            )
            assert "超时" in output or "ERROR" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_quick_command_no_timeout(self):
        """快速命令不应超时"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(
                DockerExec.func(
                    name=name, command="print('fast')", shell="python", timeout=10
                )
            )
            assert "fast" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))


# ════════════════════════════════════════════════════════════════
# DockerStop / DockerRemove - 容器停止与删除
# ════════════════════════════════════════════════════════════════


class TestDockerStopAndRemove:
    """容器停止与删除测试"""

    def test_stop_running_container(self):
        """停止运行中的容器"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        output = asyncio.run(DockerStop.func(name=name))
        assert "已停止" in output
        asyncio.run(DockerRemove.func(name=name))

    def test_stop_already_stopped(self):
        """停止已停止的容器"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        asyncio.run(DockerStop.func(name=name))
        output = asyncio.run(DockerStop.func(name=name))
        assert "已经" in output or "停止状态" in output
        asyncio.run(DockerRemove.func(name=name))

    def test_remove_running_container(self):
        """删除运行中的容器"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        output = asyncio.run(DockerRemove.func(name=name))
        assert "已删除" in output

    def test_remove_nonexistent_container(self):
        """删除不存在的容器"""
        output = asyncio.run(DockerRemove.func(name="nonexistent-xyz"))
        # 可能返回错误或成功(容器不存在时 docker rm 也可能成功)
        assert isinstance(output, str)

    def test_remove_empty_name(self):
        """空容器名"""
        output = asyncio.run(DockerRemove.func(name=""))
        assert "ERROR" in output

    def test_stop_empty_name(self):
        """空容器名"""
        output = asyncio.run(DockerStop.func(name=""))
        assert "ERROR" in output

    def test_exec_after_stop(self):
        """停止后执行命令应失败"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        asyncio.run(DockerStop.func(name=name))
        output = asyncio.run(DockerExec.func(name=name, command="echo test"))
        assert "ERROR" in output or "停止" in output
        asyncio.run(DockerRemove.func(name=name))


# ════════════════════════════════════════════════════════════════
# DockerStart - 容器启动
# ════════════════════════════════════════════════════════════════


class TestDockerStart:
    """容器启动测试"""

    def test_start_stopped_container(self):
        """启动已停止的容器"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            asyncio.run(DockerStop.func(name=name))
            output = asyncio.run(DockerStart.func(name=name))
            assert "已启动" in output
            # 验证可以执行命令
            exec_output = asyncio.run(
                DockerExec.func(name=name, command="python -c 'print(42)'")
            )
            assert "42" in exec_output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_start_already_running(self):
        """启动已在运行的容器"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(DockerStart.func(name=name))
            assert "已经" in output or "运行状态" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_start_empty_name(self):
        """空容器名"""
        output = asyncio.run(DockerStart.func(name=""))
        assert "ERROR" in output

    def test_start_nonexistent_container(self):
        """启动不存在的容器"""
        output = asyncio.run(DockerStart.func(name="nonexistent-xyz"))
        assert "ERROR" in output or "不存在" in output

    def test_stop_start_cycle(self):
        """停止 → 启动 → 停止 → 启动 循环"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            for _ in range(2):
                asyncio.run(DockerStop.func(name=name))
                start_output = asyncio.run(DockerStart.func(name=name))
                assert "已启动" in start_output
                exec_output = asyncio.run(
                    DockerExec.func(name=name, command="echo alive")
                )
                assert "alive" in exec_output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_state_preserved_after_restart(self):
        """重启后状态保持"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            # 写文件
            asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"with open('/tmp/data.txt', 'w') as f: f.write('preserved')\"",
                )
            )
            # 停止 → 启动
            asyncio.run(DockerStop.func(name=name))
            asyncio.run(DockerStart.func(name=name))
            # 读文件
            output = asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"print(open('/tmp/data.txt').read())\"",
                )
            )
            assert "preserved" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))


# ════════════════════════════════════════════════════════════════
# DockerList - 容器列表
# ════════════════════════════════════════════════════════════════


class TestDockerList:
    """容器列表测试"""

    def test_list_empty(self):
        """无容器时列表"""
        output = asyncio.run(DockerList.func())
        assert "没有" in output or "容器" in output

    def test_list_single_container(self):
        """单个容器"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(DockerList.func())
            assert name in output
            assert "running" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_list_multiple_containers(self):
        """多个容器"""
        result1 = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name1 = _extract_container_name(result1)
        result2 = asyncio.run(DockerCreate.func(image="alpine"))
        name2 = _extract_container_name(result2)
        try:
            output = asyncio.run(DockerList.func())
            assert name1 in output
            assert name2 in output
        finally:
            asyncio.run(DockerRemove.func(name=name1))
            asyncio.run(DockerRemove.func(name=name2))

    def test_list_shows_image_info(self):
        """列表显示镜像信息"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(DockerList.func())
            assert "python:3-slim" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_list_shows_volumes(self):
        """列表显示挂载信息"""
        result = asyncio.run(
            DockerCreate.func(image="python:3-slim", volumes=["/tmp:/data"])
        )
        name = _extract_container_name(result)
        try:
            output = asyncio.run(DockerList.func())
            assert "/tmp:/data" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_list_shows_ports(self):
        """列表显示端口映射"""
        result = asyncio.run(
            DockerCreate.func(image="python:3-slim", ports=["18080:80"])
        )
        name = _extract_container_name(result)
        try:
            output = asyncio.run(DockerList.func())
            assert "18080:80" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))


# ════════════════════════════════════════════════════════════════
# DockerVolumes - 目录挂载数据共享
# ════════════════════════════════════════════════════════════════


class TestDockerVolumes:
    """目录挂载数据共享测试"""

    def test_write_from_container_read_from_host(self, tmp_path):
        """容器写入,宿主机读取"""
        vol = f"{tmp_path.as_posix()}:/data"
        result = asyncio.run(DockerCreate.func(image="python:3-slim", volumes=[vol]))
        name = _extract_container_name(result)
        try:
            asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"with open('/data/out.txt', 'w') as f: f.write('hello from container')\"",
                )
            )
            content = (tmp_path / "out.txt").read_text()
            assert content == "hello from container"
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_write_from_host_read_from_container(self, tmp_path):
        """宿主机写入,容器读取"""
        (tmp_path / "input.txt").write_text("hello from host")
        vol = f"{tmp_path.as_posix()}:/data"
        result = asyncio.run(DockerCreate.func(image="python:3-slim", volumes=[vol]))
        name = _extract_container_name(result)
        try:
            output = asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"print(open('/data/input.txt').read())\"",
                )
            )
            assert "hello from host" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_bidirectional_data_flow(self, tmp_path):
        """双向数据流"""
        vol = f"{tmp_path.as_posix()}:/data"
        result = asyncio.run(DockerCreate.func(image="python:3-slim", volumes=[vol]))
        name = _extract_container_name(result)
        try:
            # 宿主机 → 容器
            (tmp_path / "in.txt").write_text("step1")
            output1 = asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"print(open('/data/in.txt').read())\"",
                )
            )
            assert "step1" in output1

            # 容器 → 宿主机
            asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"with open('/data/out.txt', 'w') as f: f.write('step2')\"",
                )
            )
            assert (tmp_path / "out.txt").read_text() == "step2"
        finally:
            asyncio.run(DockerRemove.func(name=name))


# ════════════════════════════════════════════════════════════════
# DockerImages - 镜像列表
# ════════════════════════════════════════════════════════════════


class TestDockerImages:
    """镜像列表测试"""

    def test_list_images(self):
        """列出镜像"""
        output = asyncio.run(DockerImages.func())
        assert isinstance(output, str)
        # 至少应该有 python 或 alpine 镜像
        assert len(output) > 0

    def test_list_after_pull(self):
        """拉取后列表"""
        asyncio.run(DockerPull.func(image="alpine"))
        output = asyncio.run(DockerImages.func())
        assert "alpine" in output


# ════════════════════════════════════════════════════════════════
# DockerPull - 镜像拉取
# ════════════════════════════════════════════════════════════════


class TestDockerPull:
    """镜像拉取测试"""

    def test_pull_alpine(self):
        """拉取 alpine"""
        output = asyncio.run(DockerPull.func(image="alpine"))
        assert "成功" in output or "alpine" in output

    def test_pull_empty_image(self):
        """空镜像名"""
        output = asyncio.run(DockerPull.func(image=""))
        assert "ERROR" in output

    def test_pull_invalid_image(self):
        """无效镜像名"""
        output = asyncio.run(
            DockerPull.func(image="this_image_definitely_does_not_exist_xyz")
        )
        assert "ERROR" in output or "失败" in output


# ════════════════════════════════════════════════════════════════
# DockerSearch - 镜像搜索
# ════════════════════════════════════════════════════════════════


class TestDockerSearch:
    """镜像搜索测试"""

    def test_search_python(self):
        """搜索 python"""
        output = asyncio.run(DockerSearch.func(keyword="python"))
        assert "python" in output.lower()

    def test_search_nginx(self):
        """搜索 nginx"""
        output = asyncio.run(DockerSearch.func(keyword="nginx"))
        assert "nginx" in output.lower()

    def test_search_with_limit(self):
        """限制结果数量"""
        output = asyncio.run(DockerSearch.func(keyword="python", limit=3))
        lines = [l for l in output.strip().split("\n") if l.strip()]
        # 表头 + 最多3条结果
        assert len(lines) <= 4

    def test_search_empty_keyword(self):
        """空关键词"""
        output = asyncio.run(DockerSearch.func(keyword=""))
        assert "ERROR" in output

    def test_search_nonexistent(self):
        """搜索不存在的镜像"""
        output = asyncio.run(
            DockerSearch.func(keyword="this_keyword_should_not_match_any_image_xyz")
        )
        assert "未找到" in output or "没有" in output or len(output) > 0


# ════════════════════════════════════════════════════════════════
# DockerRemoveImage - 镜像删除
# ════════════════════════════════════════════════════════════════


class TestDockerRemoveImage:
    """镜像删除测试"""

    def test_remove_image(self):
        """删除镜像"""
        # 先拉取一个小型镜像
        asyncio.run(DockerPull.func(image="alpine"))
        output = asyncio.run(DockerRemoveImage.func(image="alpine"))
        assert "已删除" in output or "alpine" in output

    def test_remove_empty_image(self):
        """空镜像名"""
        output = asyncio.run(DockerRemoveImage.func(image=""))
        assert "ERROR" in output

    def test_remove_nonexistent_image(self):
        """删除不存在的镜像"""
        output = asyncio.run(
            DockerRemoveImage.func(image="nonexistent_image_xyz:latest")
        )
        assert "ERROR" in output or "失败" in output


# ════════════════════════════════════════════════════════════════
# DockerBuild - 镜像构建
# ════════════════════════════════════════════════════════════════


class TestDockerBuild:
    """镜像构建测试"""

    def test_build_simple_dockerfile(self, tmp_path):
        """构建简单 Dockerfile"""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM python:3-slim\nRUN echo 'built'")
        output = asyncio.run(
            DockerBuild.func(path=tmp_path.as_posix(), tag="test-build:v1")
        )
        assert "成功" in output or "test-build:v1" in output
        # 清理
        asyncio.run(DockerRemoveImage.func(image="test-build:v1", force=True))

    def test_build_empty_path(self):
        """空路径"""
        output = asyncio.run(DockerBuild.func(path="", tag="test:v1"))
        assert "ERROR" in output

    def test_build_empty_tag(self, tmp_path):
        """空标签"""
        output = asyncio.run(DockerBuild.func(path=tmp_path.as_posix(), tag=""))
        assert "ERROR" in output

    def test_build_and_use(self, tmp_path):
        """构建镜像后创建容器使用"""
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM alpine\nRUN touch /tmp/built_marker")
        asyncio.run(DockerBuild.func(path=tmp_path.as_posix(), tag="test-use:v1"))
        try:
            result = asyncio.run(DockerCreate.func(image="test-use:v1"))
            name = _extract_container_name(result)
            output = asyncio.run(
                DockerExec.func(name=name, command="ls /tmp/built_marker")
            )
            assert "built_marker" in output
            asyncio.run(DockerRemove.func(name=name))
        finally:
            asyncio.run(DockerRemoveImage.func(image="test-use:v1", force=True))


# ════════════════════════════════════════════════════════════════
# 综合场景测试
# ════════════════════════════════════════════════════════════════


class TestFullWorkflow:
    """完整工作流测试"""

    def test_create_exec_list_remove(self):
        """创建 → 执行 → 列表 → 删除"""
        # 创建
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)
        assert "创建成功" in result

        # 执行
        output = asyncio.run(
            DockerExec.func(name=name, command="python -c 'print(42)'")
        )
        assert "42" in output

        # 列表
        list_output = asyncio.run(DockerList.func())
        assert name in list_output

        # 删除
        remove_output = asyncio.run(DockerRemove.func(name=name))
        assert "已删除" in remove_output

    def test_create_stop_exec_remove(self):
        """创建 → 停止 → 尝试执行(失败) → 删除"""
        result = asyncio.run(DockerCreate.func(image="python:3-slim"))
        name = _extract_container_name(result)

        # 停止
        stop_output = asyncio.run(DockerStop.func(name=name))
        assert "已停止" in stop_output

        # 执行应失败
        exec_output = asyncio.run(DockerExec.func(name=name, command="echo test"))
        assert "ERROR" in exec_output or "停止" in exec_output

        # 删除
        asyncio.run(DockerRemove.func(name=name))

    def test_search_pull_create_exec(self):
        """搜索 → 拉取 → 创建 → 执行"""
        # 搜索确认存在
        search_output = asyncio.run(DockerSearch.func(keyword="alpine", limit=1))
        assert "alpine" in search_output.lower()

        # 拉取
        pull_output = asyncio.run(DockerPull.func(image="alpine"))
        assert "成功" in pull_output or "alpine" in pull_output

        # 创建
        result = asyncio.run(DockerCreate.func(image="alpine"))
        name = _extract_container_name(result)

        # 执行
        output = asyncio.run(DockerExec.func(name=name, command="cat /etc/os-release"))
        assert "Alpine" in output or "alpine" in output.lower()

        # 清理
        asyncio.run(DockerRemove.func(name=name))

    def test_volume_workflow(self, tmp_path):
        """挂载 → 写入 → 读取 → 清理"""
        vol = f"{tmp_path.as_posix()}:/workspace"
        result = asyncio.run(DockerCreate.func(image="python:3-slim", volumes=[vol]))
        name = _extract_container_name(result)
        try:
            # 写入数据
            asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"import json; json.dump({'key': 'value'}, open('/workspace/data.json', 'w'))\"",
                )
            )
            # 验证宿主机可读
            import json

            data = json.loads((tmp_path / "data.json").read_text())
            assert data["key"] == "value"

            # 容器内读取验证
            output = asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"import json; print(json.load(open('/workspace/data.json'))['key'])\"",
                )
            )
            assert "value" in output
        finally:
            asyncio.run(DockerRemove.func(name=name))

    def test_build_volume_workflow(self, tmp_path):
        """构建镜像 → 挂载 → 执行 → 清理"""
        # 准备 Dockerfile
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM python:3-slim\nRUN pip install -q pyjokes")

        # 准备输出目录
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        # 构建
        asyncio.run(DockerBuild.func(path=tmp_path.as_posix(), tag="test-workflow:v1"))

        try:
            # 创建容器并挂载输出目录
            vol = f"{out_dir.as_posix()}:/output"
            result = asyncio.run(
                DockerCreate.func(image="test-workflow:v1", volumes=[vol])
            )
            name = _extract_container_name(result)

            # 执行并输出到文件
            asyncio.run(
                DockerExec.func(
                    name=name,
                    command="python -c \"import pyjokes; open('/output/joke.txt', 'w').write(pyjokes.get_joke())\"",
                )
            )

            # 验证输出
            joke = (out_dir / "joke.txt").read_text()
            assert len(joke) > 0

            asyncio.run(DockerRemove.func(name=name))
        finally:
            asyncio.run(DockerRemoveImage.func(image="test-workflow:v1", force=True))


# ════════════════════════════════════════════════════════════════
# 工具注册表测试
# ════════════════════════════════════════════════════════════════


class TestToolRegistry:
    """工具注册表测试"""

    def test_get_all_tools_count(self):
        """工具数量"""
        tools = get_all_tools()
        assert len(tools) == 11

    def test_get_all_tools_names(self):
        """工具名称"""
        tools = get_all_tools()
        names = {t.name for t in tools}
        expected = {
            "DockerCreate",
            "DockerExec",
            "DockerStart",
            "DockerStop",
            "DockerRemove",
            "DockerList",
            "DockerPull",
            "DockerSearch",
            "DockerImages",
            "DockerRemoveImage",
            "DockerBuild",
        }
        assert names == expected

    def test_get_tools_returns_list(self):
        """get_tools 返回列表"""
        result = asyncio.run(get_tools())
        assert isinstance(result, list)


# ════════════════════════════════════════════════════════════════
# 模型测试
# ════════════════════════════════════════════════════════════════


class TestModels:
    """数据模型测试"""

    def test_container_info_defaults(self):
        """ContainerInfo 默认值"""
        info = ContainerInfo(name="test", image="alpine", network=False)
        assert info.status == ContainerStatus.running
        assert info.volumes == []
        assert info.ports == []
        assert info.memory == "256m"
        assert info.cpus == "1"

    def test_container_status_values(self):
        """ContainerStatus 枚举值"""
        assert ContainerStatus.running == "running"
        assert ContainerStatus.stopped == "stopped"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
