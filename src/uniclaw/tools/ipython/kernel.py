"""
IPython 内核管理 - 管理 IPython 内核生命周期和代码执行。

基于 jupyter_client 实现内核的启动、停止、代码执行、变量检查等功能。
"""

import ast
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# ANSI 转义序列(颜色/样式),traceback 里的要剥掉才可读
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

# inspect_variable 的 repr/str 预览上限(字符)。截断在内核侧完成,
# 避免 20 万元素 list 的全量 repr 穿过 IPC 把上下文/内存打爆。
INSPECT_TEXT_LIMIT = 2000


def _kernel_alive(info: "KernelInfo") -> bool:
    """内核进程是否存活。

    无法判定时(无 kernel_manager / is_alive 抛错)按存活处理,
    避免把慢执行误判成死内核。
    """
    km = info.kernel_manager
    if km is None:
        return True
    checker = getattr(km, "is_alive", None)
    if not callable(checker):
        return True
    try:
        return bool(checker())
    except Exception:
        return True


def _dead_kernel_error(kernel_id: str) -> str:
    """死内核的统一报错文案(带重启指引)。"""
    return (
        f"IPython 内核已终止(kernel_id={kernel_id!r}),请用 ipython_start 重新启动"
    )


def _unwrap_str_repr(text: str) -> str:
    """还原 IPython displayhook 对字符串结果的 repr 转义。

    `%lsmagic` 等魔术命令"返回"字符串而不是 print,IPython 会用 repr
    展示执行结果 —— 换行变成字面 `\\n`。识别为带转义的字符串字面量时
    还原真实内容;不含转义的短 repr(如 `'hi'`)保持原样。
    """
    if not text or text[0] not in "'\"":
        return text
    if (
        "\\n" not in text
        and "\\t" not in text
        and "\\'" not in text
        and '\\"' not in text
    ):
        return text
    try:
        val = ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return text
    return val if isinstance(val, str) else text


class IopubMsgType(StrEnum):
    """Jupyter iopub 通道的消息类型(见 Jupyter messaging spec)。

    只列本模块 `_collect()` 会处理的几种,其余类型静默跳过。
    用 StrEnum 是因为 `msg["header"]["msg_type"]` 拿到的是裸字符串,
    StrEnum 与 str 相等,比较时可直接用成员名,拼写由类型检查兜住。
    """

    EXECUTE_INPUT = "execute_input"
    STREAM = "stream"
    EXECUTE_RESULT = "execute_result"
    DISPLAY_DATA = "display_data"
    ERROR = "error"
    STATUS = "status"

# ============================================================
# 数据结构
# ============================================================


@dataclass
class KernelInfo:
    """内核连接信息。"""

    kernel_id: str
    kernel_manager: Any  # jupyter_client.KernelManager
    client: Any  # jupyter_client.BlockingKernelClient
    created_at: float = field(default_factory=time.time)
    execution_count: int = 0
    history: list[str] = field(default_factory=list)
    # 同一内核的代码执行必须串行,否则并发读取 iopub 队列会错乱
    exec_lock: Any = field(default_factory=asyncio.Lock)


@dataclass
class ExecutionResult:
    """代码执行结果。"""

    success: bool
    output: str
    error: str | None = None
    rich_outputs: list[dict] = field(default_factory=list)
    execution_count: int = 0

    def format(self) -> str:
        """格式化为可读文本。"""
        parts = []
        parts.append(f"[In {self.execution_count}]")

        if self.output:
            parts.append(self.output)

        if self.rich_outputs:
            for out in self.rich_outputs:
                mime = out["type"]
                data = out["data"]
                if mime.startswith("image/"):
                    parts.append(f"[{mime} 图像数据, {len(data)} 字符]")
                elif mime == "text/html":
                    parts.append(f"[HTML 输出]")
                elif mime == "text/latex":
                    parts.append(f"[LaTeX: {data}]")
                else:
                    parts.append(f"[{mime}]")

        if self.error:
            parts.append(f"错误:\n{self.error}")

        return "\n".join(parts)


@dataclass
class VariableInfo:
    """变量详细信息。"""

    name: str
    type_name: str
    repr_str: str
    str_val: str
    doc: str | None = None
    length: int | None = None
    shape: str | None = None
    dtype: str | None = None
    summary: str | None = None

    def format(self) -> str:
        """格式化为可读文本。"""
        parts = [f"变量: {self.name}"]
        parts.append(f"类型: {self.type_name}")
        parts.append(f"值: {self.repr_str}")
        if self.summary:
            parts.append(f"摘要: {self.summary}")
        if self.length is not None:
            parts.append(f"长度: {self.length}")
        if self.shape:
            parts.append(f"形状: {self.shape}")
        if self.dtype:
            parts.append(f"数据类型: {self.dtype}")
        if self.doc:
            doc_preview = self.doc[:1000]
            if len(self.doc) > 1000:
                doc_preview += "..."
            parts.append(f"文档: {doc_preview}")
        return "\n".join(parts)


@dataclass
class VariableSummary:
    """变量摘要。"""

    name: str
    type_name: str
    repr_str: str


# ============================================================
# 内核管理器
# ============================================================


class IPythonKernelManager:
    """IPython 内核管理器(单例)。"""

    _instance: "IPythonKernelManager | None" = None

    def __init__(self):
        self.kernels: dict[str, KernelInfo] = {}
        # get-or-create 必须串行: 否则并行的首次 execute 会各自启动一个内核,
        # 后者覆盖 self.kernels[kernel_id],前者成为孤儿 —— 表现为并行两次都拿到
        # [In 1](各自计数从 0 开始),且其中一个内核的 history 永久丢失
        self._start_lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "IPythonKernelManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def ensure_kernel(
        self, kernel_id: str = "default", kernel_name: str = "python3"
    ) -> KernelInfo:
        """获取内核,不存在则启动 —— 并发调用只会启动一个。

        自动启动场景(ipython_execute 未显式 start 时)必须走这里,
        直接 start_kernel 的"先查再建"在并发下是 TOCTOU 竞态。
        """
        async with self._start_lock:
            info = self.kernels.get(kernel_id)
            if info is not None:
                return info
            return await self.start_kernel(kernel_id, kernel_name)

    async def start_kernel(
        self, kernel_id: str = "default", kernel_name: str = "python3"
    ) -> KernelInfo:
        """启动一个 IPython 内核。

        Args:
            kernel_id: 内核标识符,用于后续操作指定内核。
            kernel_name: Jupyter 内核名称,默认为 python3。

        Returns:
            KernelInfo: 内核连接信息。

        Raises:
            RuntimeError: 启动失败时抛出。
        """
        if kernel_id in self.kernels:
            existing = self.kernels[kernel_id]
            if _kernel_alive(existing):
                raise RuntimeError(
                    f"内核 '{kernel_id}' 已存在,请先停止或使用其他 ID"
                )
            # 死内核占着 ID 会挡住"用 ipython_start 重新启动",清掉后继续启动
            self.kernels.pop(kernel_id, None)
            try:
                existing.client.stop_channels()
            except Exception:
                pass

        from jupyter_client.manager import KernelManager

        def _start():
            km = KernelManager(kernel_name=kernel_name)
            km.start_kernel()
            kc = km.client()
            kc.start_channels()
            try:
                kc.wait_for_ready(timeout=60)
            except Exception as e:
                # 诊断:检查内核进程是否真的死了
                alive = False
                ret = None
                try:
                    if km.provisioner and km.provisioner.process is not None:
                        ret = km.provisioner.process.poll()
                        alive = ret is None
                except Exception:
                    pass
                logger.error(
                    "内核 ready 等待失败: %s (alive=%s, returncode=%s, "
                    "has_kernel=%s)",
                    e,
                    alive,
                    ret,
                    km.has_kernel,
                )
                # 写入诊断文件,便于排查
                try:
                    diag_path = os.path.join(
                        os.environ.get("TEMP", "/tmp"), "uniclaw_ipython_diag.txt"
                    )
                    with open(diag_path, "a", encoding="utf-8") as f:
                        f.write(
                            f"[{time.strftime('%H:%M:%S')}] {e} "
                            f"(alive={alive}, returncode={ret}, "
                            f"has_kernel={km.has_kernel}, pid={km.pid if hasattr(km, 'pid') else None})\n"
                        )
                except Exception as diag_e:
                    logger.error("写入诊断文件失败: %s", diag_e)
                # 进程仍存活(如内核响应慢)则重试一次
                if alive:
                    logger.warning("内核进程仍存活,重试 wait_for_ready...")
                    kc.wait_for_ready(timeout=60)
                else:
                    raise
            return km, kc

        try:
            km, kc = await asyncio.to_thread(_start)
        except Exception as e:
            raise RuntimeError(f"启动内核失败: {e}") from e

        info = KernelInfo(
            kernel_id=kernel_id,
            kernel_manager=km,
            client=kc,
        )
        self.kernels[kernel_id] = info
        return info

    def get_kernel(self, kernel_id: str = "default") -> KernelInfo:
        """获取已存在的内核信息。

        Args:
            kernel_id: 内核标识符。

        Returns:
            KernelInfo: 内核信息。

        Raises:
            RuntimeError: 内核不存在时抛出。
        """
        if kernel_id not in self.kernels:
            available = list(self.kernels.keys())
            if available:
                raise RuntimeError(
                    f"内核 '{kernel_id}' 不存在。可用内核: {available}"
                )
            from .tools import ipython_start
            raise RuntimeError(
                f"内核 '{kernel_id}' 不存在。当前无运行中的内核,请先调用 {ipython_start.name}"
            )
        return self.kernels[kernel_id]

    def is_alive(self, kernel_id: str = "default") -> bool:
        """内核是否存活。不存在时返回 False。"""
        info = self.kernels.get(kernel_id)
        if info is None:
            return False
        return _kernel_alive(info)

    async def stop_kernel(self, kernel_id: str = "default") -> str:
        """停止并移除一个内核。

        Args:
            kernel_id: 内核标识符。

        Returns:
            str: 停止结果消息。
        """
        info = self.get_kernel(kernel_id)

        def _stop():
            try:
                info.client.stop_channels()
            except Exception:
                pass
            try:
                info.kernel_manager.shutdown_kernel(now=True)
            except Exception:
                pass

        try:
            await asyncio.to_thread(_stop)
        finally:
            del self.kernels[kernel_id]

        return f"内核 '{kernel_id}' 已停止"

    async def execute(
        self, code: str, kernel_id: str = "default", timeout: int = 30,
        record_history: bool = True,
    ) -> ExecutionResult:
        """在指定内核中执行代码。

        Args:
            code: 要执行的 Python 代码。
            kernel_id: 内核标识符。
            timeout: 执行超时秒数。
            record_history: 是否记录到执行历史。工具自身的探查代码
                (如 list_variables / inspect_variable 的内部脚本)应传 False,
                避免混入 ipython_history 的用户历史。

        Returns:
            ExecutionResult: 执行结果。

        Raises:
            RuntimeError: 内核不存在或已终止时抛出。
        """
        info = self.get_kernel(kernel_id)

        if not _kernel_alive(info):
            raise RuntimeError(_dead_kernel_error(kernel_id))

        # 同一内核串行执行,避免并发读取 iopub 队列导致消息错乱
        async with info.exec_lock:
            # 计数与历史追加放在锁内: 并发时两次 execute 若在锁外各自
            # +=  再回读共享变量,会拿到同一个编号(如都返回 [In 3])
            if record_history:
                info.history.append(code)
            info.execution_count += 1
            # 先记下本次分配值作兜底;内核返回 execute_input 时以它为准
            local_count = info.execution_count
            try:
                msg_id = info.client.execute(code)
            except Exception as e:
                # 通道已关(内核死亡)或提交失败
                if not _kernel_alive(info):
                    raise RuntimeError(_dead_kernel_error(kernel_id)) from e
                raise RuntimeError(f"提交执行失败: {e}") from e

            def _collect():
                stdout_parts = []
                stderr_parts = []
                rich_outputs = []
                error_info = None
                kernel_count = None
                completed = False

                while True:
                    try:
                        msg = info.client.get_iopub_msg(timeout=timeout)
                    except Exception:
                        break

                    # 只处理属于本次执行的消息,跳过残留的其他执行消息
                    parent = msg.get("parent_header", {})
                    parent_id = parent.get("msg_id")
                    if parent_id is not None and parent_id != msg_id:
                        continue

                    msg_type = msg["header"]["msg_type"]
                    content = msg["content"]

                    if msg_type == IopubMsgType.EXECUTE_INPUT:
                        # 内核实际分配的 [In N] 编号,并发下比共享计数器可靠
                        kc = content.get("execution_count")
                        if isinstance(kc, int):
                            kernel_count = kc

                    elif msg_type == IopubMsgType.STREAM:
                        text = content.get("text", "")
                        if content.get("name") == "stdout":
                            stdout_parts.append(text)
                        else:
                            stderr_parts.append(text)

                    elif msg_type == IopubMsgType.EXECUTE_RESULT:
                        kc = content.get("execution_count")
                        if isinstance(kc, int):
                            kernel_count = kc
                        data = content.get("data", {})
                        for mime, val in data.items():
                            if mime == "text/plain":
                                # 字符串结果被 displayhook 用 repr 展示,换行变字面 \n
                                stdout_parts.append(_unwrap_str_repr(val))
                            else:
                                rich_outputs.append({"type": mime, "data": val})

                    elif msg_type == IopubMsgType.DISPLAY_DATA:
                        data = content.get("data", {})
                        for mime, val in data.items():
                            if mime == "text/plain":
                                rich_outputs.append(
                                    {"type": mime, "data": _unwrap_str_repr(val)}
                                )
                            else:
                                rich_outputs.append({"type": mime, "data": val})

                    elif msg_type == IopubMsgType.ERROR:
                        ename = content.get("ename", "Error")
                        evalue = content.get("evalue", "")
                        traceback = content.get("traceback", [])
                        clean_tb = [_ANSI_ESCAPE.sub("", line) for line in traceback]
                        error_info = {
                            "ename": ename,
                            "evalue": evalue,
                            "traceback": clean_tb,
                        }

                    elif msg_type == IopubMsgType.STATUS:
                        if content.get("execution_state") == "idle":
                            if parent.get("msg_id") == msg_id:
                                completed = True
                                break

                return (
                    stdout_parts,
                    stderr_parts,
                    rich_outputs,
                    error_info,
                    kernel_count,
                    completed,
                )

            try:
                (
                    stdout_parts,
                    stderr_parts,
                    rich_outputs,
                    error_info,
                    kernel_count,
                    completed,
                ) = await asyncio.to_thread(_collect)
            except Exception as e:
                if not _kernel_alive(info):
                    raise RuntimeError(_dead_kernel_error(kernel_id)) from e
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"执行超时或通信失败: {e}",
                    rich_outputs=[],
                    execution_count=local_count,
                )

            # 收满 idle 之前进程挂掉(如 os._exit)→ 死内核;
            # 仅超时且进程仍存活 → 慢执行,不误判为死亡
            if not completed and not _kernel_alive(info):
                raise RuntimeError(_dead_kernel_error(kernel_id))

        # 优先用内核实际分配的 execution_count(即 [In N] 的 N),
        # 否则用本次在锁内记下的 local_count — 二者都与"本条执行"绑定
        exec_count = kernel_count if isinstance(kernel_count, int) else local_count

        stdout = "".join(stdout_parts).rstrip()
        stderr = "".join(stderr_parts).rstrip()

        if error_info:
            tb = "\n".join(error_info["traceback"])
            # ipykernel 给每个 cell 生成 murmur2 哈希临时文件名(如 3524816064.py),
            # 语法错误的 evalue 会带上它,无语义。替换为可读标识。
            tmp_name_pat = re.compile(r"\b\d+\.py\b")
            evalue = tmp_name_pat.sub("<input>", error_info["evalue"])
            tb = tmp_name_pat.sub("<input>", tb)
            error_text = f"{error_info['ename']}: {evalue}\n{tb}"
            return ExecutionResult(
                success=False,
                output=stdout,
                error=error_text,
                rich_outputs=rich_outputs,
                execution_count=exec_count,
            )

        return ExecutionResult(
            success=True,
            output=stdout,
            error=stderr if stderr else None,
            rich_outputs=rich_outputs,
            execution_count=exec_count,
        )

    async def inspect_variable(
        self, name: str, kernel_id: str = "default"
    ) -> VariableInfo:
        """检查变量的类型、值和详细信息。

        Args:
            name: 变量名。
            kernel_id: 内核标识符。

        Returns:
            VariableInfo: 变量信息。大容器的 repr/str 在内核侧截断到
            INSPECT_TEXT_LIMIT 字符,并附带摘要(如 `list, 长度 200000`)。
        """
        # 用 json.dumps 安全嵌入 name 字符串字面量,避免表达式含引号时语法错误
        name_literal = json.dumps(name, ensure_ascii=False)
        limit = INSPECT_TEXT_LIMIT
        code = f"""
import json as _json
_obj = {name}
def _trunc(_s, _limit={limit}):
    if _s is None:
        return None
    if len(_s) <= _limit:
        return _s
    return _s[:_limit] + '…(截断, 共 ' + str(len(_s)) + ' 字符)'
def _summary(_o):
    _t = type(_o).__name__
    try:
        _n = len(_o)
    except Exception:
        return None
    if isinstance(_o, dict):
        return _t + ', ' + str(_n) + ' 项'
    if isinstance(_o, str):
        return _t + ', ' + str(_n) + ' 字符'
    return _t + ', 长度 ' + str(_n)
try:
    _r = _trunc(repr(_obj))
except Exception:
    _r = '<repr 失败>'
try:
    _s = _trunc(str(_obj))
except Exception:
    _s = '<str 失败>'
_info = {{
    "name": {name_literal},
    "type": type(_obj).__name__,
    "summary": _summary(_obj),
    "repr": _r,
    "str": _s,
}}
try:
    _info["doc"] = _obj.__doc__[:500] if _obj.__doc__ else None
except:
    _info["doc"] = None
try:
    _info["len"] = len(_obj)
except:
    _info["len"] = None
try:
    _info["shape"] = str(_obj.shape)
except:
    _info["shape"] = None
try:
    _info["dtype"] = str(_obj.dtype)
except:
    _info["dtype"] = None
print(_json.dumps(_info, ensure_ascii=False, default=str))
"""
        result = await self.execute(code, kernel_id, timeout=10, record_history=False)
        if not result.success:
            error = (result.error or "").strip()
            # 变量未定义: 内核报 NameError,单独抛出明确提示,避免误导为内核问题
            if "NameError" in error:
                raise RuntimeError(f"变量 '{name}' 未定义")
            raise RuntimeError(f"检查变量失败: {error or '内核无输出'}")

        try:
            data = json.loads(result.output)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"解析变量信息失败: {result.output or '内核无输出(内核可能已终止)'}"
            )

        return VariableInfo(
            name=data["name"],
            type_name=data["type"],
            repr_str=data["repr"],
            str_val=data["str"],
            doc=data.get("doc"),
            length=data.get("len"),
            shape=data.get("shape"),
            dtype=data.get("dtype"),
            summary=data.get("summary"),
        )

    async def list_variables(
        self, kernel_id: str = "default", filter_type: str | None = None
    ) -> list[VariableSummary]:
        """列出内核中的所有用户变量。

        过滤掉内建(builtins 模块属性)和 IPython 自动注入的变量
        (_/In/Out/get_ipython/exit/quit/open 等),只列用户真正定义的。

        Args:
            kernel_id: 内核标识符。
            filter_type: 按类型名过滤(可选)。

        Returns:
            list[VariableSummary]: 变量摘要列表。
        """
        code = """
import json as _json
import builtins as _builtins
_ip = get_ipython()
_ns = _ip.user_ns
_hidden = set(_ip.user_ns_hidden.keys())
_builtins_names = set(dir(_builtins))
_vars = []
for _k, _v in list(_ns.items()):
    if _k.startswith('_'):
        continue
    if _k in _hidden:
        continue
    if _k in _builtins_names and _v is getattr(_builtins, _k, None):
        continue
    try:
        _r = repr(_v)
        if len(_r) > 200:
            _r = _r[:200] + '...'
    except:
        _r = '<无法显示>'
    _vars.append({
        "name": _k,
        "type": type(_v).__name__,
        "repr": _r,
    })
print(_json.dumps(_vars, ensure_ascii=False, default=str))
"""
        result = await self.execute(code, kernel_id, timeout=10, record_history=False)
        if not result.success:
            raise RuntimeError(f"列出变量失败: {result.error or '内核无输出'}")

        try:
            vars_list = json.loads(result.output)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"解析变量列表失败: {result.output or '内核无输出(内核可能已终止)'}"
            )

        summaries = [
            VariableSummary(name=v["name"], type_name=v["type"], repr_str=v["repr"])
            for v in vars_list
        ]

        if filter_type:
            summaries = [
                v for v in summaries if v.type_name.lower() == filter_type.lower()
            ]

        return summaries

    async def get_history(
        self, kernel_id: str = "default", last_n: int = 20
    ) -> list[str]:
        """获取执行历史。

        Args:
            kernel_id: 内核标识符。
            last_n: 返回最近 N 条记录。

        Returns:
            list[str]: 历史代码列表。
        """
        info = self.get_kernel(kernel_id)
        return info.history[-last_n:]

    def list_kernels(self) -> list[dict]:
        """列出所有运行中的内核。

        Returns:
            list[dict]: 内核信息列表。
        """
        result = []
        for kid, info in self.kernels.items():
            result.append(
                {
                    "kernel_id": kid,
                    "created_at": info.created_at,
                    "execution_count": info.execution_count,
                    "history_length": len(info.history),
                }
            )
        return result

    async def stop_all(self):
        """停止所有内核(用于清理)。"""
        kernel_ids = list(self.kernels.keys())
        for kid in kernel_ids:
            try:
                await self.stop_kernel(kid)
            except Exception as e:
                logger.warning(f"停止内核 {kid} 失败: {e}")
