"""
IPython 工具定义 - 提供持久化 Python 交互式执行环境的工具。

支持代码执行、变量管理、历史记录、魔术命令等 IPython 完整功能。
"""

import time

from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR
from .kernel import IPythonKernelManager


@tool
async def ipython_start(
    kernel_id: str = "default", kernel_name: str = "python3"
) -> str:
    """
    启动一个 IPython 内核,用于执行 Python 代码。

    内核启动后变量会持久保存,后续调用 ipython_execute 时可继续使用。
    支持同时运行多个内核(通过 kernel_id 区分)。
    用完后请调用 ipython_stop 关闭内核以释放资源。

    Args:
        kernel_id: 内核标识符,默认为 "default"。可启动多个内核并用不同 ID 区分。
        kernel_name: Jupyter 内核名称,默认为 "python3"。

    Returns:
        str: 启动结果消息。
    """
    mgr = IPythonKernelManager.get_instance()
    try:
        info = await mgr.start_kernel(kernel_id, kernel_name)
        return f"IPython 内核 '{kernel_id}' 已启动 (kernel_name={kernel_name})。变量将在多次执行间持久保存。"
    except RuntimeError as e:
        return f"{TOOL_ERROR}{e}"


@tool
async def ipython_execute(
    code: str, kernel_id: str = "default", timeout: int = 30
) -> str:
    """
    在 IPython 内核中执行 Python 代码。

    支持所有 Python 语法和 IPython 魔术命令(如 %timeit, %matplotlib, %pip 等)。
    变量在多次调用间持久保存。如果内核未启动,会自动启动默认内核。

    Args:
        code: 要执行的 Python 代码。支持多行代码、魔术命令(以 % 或 %% 开头)。
        kernel_id: 内核标识符,默认为 "default"。
        timeout: 执行超时秒数,默认 30 秒。

    Returns:
        str: 执行结果,包含输出文本和错误信息(如有)。
    """
    mgr = IPythonKernelManager.get_instance()

    # 自动启动默认内核
    if kernel_id not in mgr.kernels:
        try:
            await mgr.start_kernel(kernel_id)
        except RuntimeError as e:
            return f"{TOOL_ERROR}自动启动内核失败: {e}"

    try:
        result = await mgr.execute(code, kernel_id, timeout)
        return result.format()
    except RuntimeError as e:
        return f"{TOOL_ERROR}{e}"


@tool
async def ipython_inspect(name: str, kernel_id: str = "default") -> str:
    """
    检查 IPython 内核中变量的详细信息。

    显示变量的类型、值、长度、形状(如有)和文档字符串(如有)。
    适用于检查数据结构、函数签名、类实例等。

    Args:
        name: 要检查的变量名或表达式。
        kernel_id: 内核标识符,默认为 "default"。

    Returns:
        str: 变量的详细信息。
    """
    mgr = IPythonKernelManager.get_instance()
    try:
        info = await mgr.inspect_variable(name, kernel_id)
        return info.format()
    except RuntimeError as e:
        return f"{TOOL_ERROR}{e}"


@tool
async def ipython_vars(
    kernel_id: str = "default", filter_type: str | None = None
) -> str:
    """
    列出 IPython 内核中当前所有用户定义的变量。

    显示变量名、类型和值的预览。可用于快速了解当前内核状态。

    Args:
        kernel_id: 内核标识符,默认为 "default"。
        filter_type: 按类型名过滤(可选),如 "int", "str", "DataFrame"。

    Returns:
        str: 变量列表。
    """
    mgr = IPythonKernelManager.get_instance()
    try:
        vars_list = await mgr.list_variables(kernel_id, filter_type)
    except RuntimeError as e:
        return f"{TOOL_ERROR}{e}"

    if not vars_list:
        return "内核中没有用户定义的变量。"

    lines = [f"内核 '{kernel_id}' 中的变量 ({len(vars_list)} 个):"]
    for v in vars_list:
        lines.append(f"  {v.name}: {v.type_name} = {v.repr_str}")
    return "\n".join(lines)


@tool
async def ipython_history(
    kernel_id: str = "default", last_n: int = 20
) -> str:
    """
    获取 IPython 内核的代码执行历史。

    Args:
        kernel_id: 内核标识符,默认为 "default"。
        last_n: 返回最近 N 条记录,默认 20。

    Returns:
        str: 执行历史列表。
    """
    mgr = IPythonKernelManager.get_instance()
    try:
        history = await mgr.get_history(kernel_id, last_n)
    except RuntimeError as e:
        return f"{TOOL_ERROR}{e}"

    if not history:
        return f"内核 '{kernel_id}' 暂无执行历史。"

    lines = [f"内核 '{kernel_id}' 最近 {len(history)} 条执行记录:"]
    for i, code in enumerate(history, 1):
        preview = code if len(code) <= 200 else code[:200] + "..."
        lines.append(f"  [{i}] {preview}")
    return "\n".join(lines)


@tool
async def ipython_stop(kernel_id: str = "default") -> str:
    """
    停止一个运行中的 IPython 内核并释放资源。

    停止后该内核中的所有变量将丢失。

    Args:
        kernel_id: 要停止的内核标识符,默认为 "default"。

    Returns:
        str: 停止结果消息。
    """
    mgr = IPythonKernelManager.get_instance()
    try:
        return await mgr.stop_kernel(kernel_id)
    except RuntimeError as e:
        return f"{TOOL_ERROR}{e}"


@tool
async def ipython_list_kernels() -> str:
    """
    列出所有正在运行的 IPython 内核。

    Returns:
        str: 内核列表,包含 ID、创建时间和执行次数。
    """
    mgr = IPythonKernelManager.get_instance()
    kernels = mgr.list_kernels()

    if not kernels:
        return "当前没有运行中的 IPython 内核。"

    lines = ["运行中的 IPython 内核:"]
    for k in kernels:
        created = time.strftime("%H:%M:%S", time.localtime(k["created_at"]))
        lines.append(
            f"  - {k['kernel_id']}: 创建于 {created}, "
            f"已执行 {k['execution_count']} 次, "
            f"历史记录 {k['history_length']} 条"
        )
    return "\n".join(lines)


# ============================================================
# 工具注册
# ============================================================


def get_tools(config=None) -> list:
    """获取 IPython 工具列表。"""
    return [
        ipython_start,
        ipython_execute,
        ipython_inspect,
        ipython_vars,
        ipython_history,
        ipython_stop,
        ipython_list_kernels,
    ]


def get_all_tools() -> list:
    """获取所有 IPython 工具。"""
    return get_tools()
