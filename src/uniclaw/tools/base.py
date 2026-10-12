"""自定义 @tool 装饰器 — 生成 OpenAI function calling schema,自动排除注入参数。

ToolRuntime: 工具运行时上下文 dataclass,含 config / tool_call_id / stream_writer 三个字段。
工具函数声明 `tool_runtime: ToolRuntime = None` 形参即可拿到运行时上下文,由调用方(agent 主循环等)构造传入。
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
import re
import time
import types
import typing
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    get_args,
    get_origin,
    get_type_hints,
)

if TYPE_CHECKING:
    from uniclaw.config import AppConfig

from uniclaw.provider.error_classifier import is_network_or_provider_error
from uniclaw.utils.constants import TOOL_ERROR

logger = logging.getLogger(__name__)


class ToolUserError(RuntimeError):
    """工具的**预期失败**(状态校验、参数校验等非 bug 原因)。

    helper 层(如 WebBrowser._get_page)拿不到工具函数的返回值语义时,可以抛它
    来表达"这是给模型看的错误说明",`Tool.__call__` 会转成 `{TOOL_ERROR}: ...`
    **并且不写错误日志** —— 预期失败不需要堆栈。

    与约定的关系: 工具函数体内的预期失败仍应直接 `return f"{TOOL_ERROR}: ..."`;
    本异常是给"中间层"用的,避免每个调用点都包一层 try。
    """


# 类型映射: Python type → JSON Schema type
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    type(None): "null",
}

# 运行时注入的参数,不写入 schema
_INJECTED_PARAMS = {"tool_runtime"}

def _shorten(value, limit: int = 200) -> str:
    """把参数值渲染成短字符串,超长截断 —— 报错文本不能被大值撑爆。

    完整参数记录由会话保留(tool 消息 args + assistant.tool_calls), 报错文本
    只留预览, 不用重复一遍大值。
    """
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _type_name(tp) -> str:
    """把注解渲染成可读类型名,失败时退回 repr。"""
    try:
        origin = get_origin(tp)
        if origin in (typing.Union, types.UnionType):
            return " | ".join(_type_name(a) for a in get_args(tp))
        if origin is not None:
            args = ", ".join(_type_name(a) for a in get_args(tp))
            return f"{getattr(origin, '__name__', origin)}[{args}]"
        return getattr(tp, "__name__", str(tp))
    except Exception:
        return repr(tp)


def _value_matches(value, expected) -> bool:
    """保守的类型匹配: 只判定"明显对不上",拿不准就放行。

    保守是刻意的 —— 误杀正常调用的代价远大于漏判一次传参错误。
    """
    origin = get_origin(expected)
    if origin in (typing.Union, types.UnionType):
        return any(_value_matches(value, a) for a in get_args(expected))
    if origin is not None:
        # list[str] / dict[str, int] 之类: 只看容器外层
        return isinstance(value, origin)
    if expected is bool:
        return isinstance(value, bool)
    if expected in (int, float):
        # 数值类型互相放宽: timeout=0.2 给 int 参数是常见且无害的用法。
        # 只拦跨类别错误(该传数字却传了 str), 那才是模型传参错误。
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected in (str, list, dict):
        return isinstance(value, expected)
    return True

# 参数行正则: "name: desc" 或 "name (type): desc",冒号前为小写/下划线标识符,冒号后有空格
_PARAM_LINE_RE = re.compile(r"^[a-z_][a-zA-Z0-9_]*\s*(?:\(.*?\))?\s*:\s")

# 常用类型名称映射,避免 eval
_BUILTIN_TYPE_NAMES: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "bytes": bytes,
    "None": type(None),
}


@dataclass
class ToolRuntime:
    """工具运行时上下文,由调用方(agent 主循环等)构造并传入签名为 tool_runtime 的参数。

    - config: 应用配置
    - tool_call_id: 本次工具调用的 ID(原仅在事件层可见,现工具内部也可获取)
    - stream_writer: 流式输出回调,签名 async (tool_call_id: str, content: str) -> None;
      None 表示无 UI 接收方。凭 tool_call_id 路由归属,异步/后台任务推流也能落到正确工具块。
    """

    config: AppConfig | None = None
    tool_call_id: str = ""
    stream_writer: Callable[[str, str], Awaitable[None]] | None = None

    async def stream(self, content: str) -> None:
        """推送流式输出到前端,自动附上 tool_call_id 定位归属;无接收方时静默忽略。"""
        if self.stream_writer is not None:
            await self.stream_writer(self.tool_call_id, content)


def _resolve_str_annotation(tp: str, func_globals: dict = None) -> Any:
    """将字符串形式的类型注解解析为实际类型。"""
    tp = tp.strip()
    # 简单类型直接查表
    if tp in _BUILTIN_TYPE_NAMES:
        return _BUILTIN_TYPE_NAMES[tp]
    try:
        import builtins

        ns = {"__builtins__": builtins, **_BUILTIN_TYPE_NAMES}
        if func_globals:
            ns.update(func_globals)
        return eval(tp, ns)
    except Exception as e:
        logger.debug("解析类型注解 '%s' 失败,回退为 str: %s", tp, e)
        return str


def _python_type_to_schema(tp: Any, multi_type: bool = False) -> dict:
    """将 Python 类型注解转换为 JSON Schema。

    Args:
        tp: Python 类型注解。
        multi_type: 是否使用多类型写法(如 ["string", "null"])。
                    默认为 False,Optional[X] 只保留 X 的类型。
    """
    if tp is inspect.Parameter.empty:
        return {"type": "string"}

    # from __future__ import annotations 导致注解为字符串,尝试求值
    if isinstance(tp, str):
        tp = _resolve_str_annotation(tp)

    origin = getattr(tp, "__origin__", None)

    # 基本类型(必须在泛型检查之前)
    if tp in _TYPE_MAP:
        return {"type": _TYPE_MAP[tp]}

    # List[X] / list[X]
    if origin is list:
        args = getattr(tp, "__args__", None)
        if args:
            return {
                "type": "array",
                "items": _python_type_to_schema(args[0], multi_type),
            }
        return {"type": "array"}

    # Dict[str, X] / dict[str, X]
    if origin is dict:
        return {"type": "object"}

    # 处理 NoneType
    if tp is type(None):
        return {"type": "null"}

    # 处理 Optional[X] / X | None / Union[X, Y]
    if origin is typing.Union:
        schemas = [_python_type_to_schema(a, multi_type) for a in get_args(tp)]
        if multi_type:
            # 各成员都是裸 {"type": ...} 时合并成 type 数组(如 ["string", "null"]);
            # 含 array/object 等带结构的成员(如 str | list[str])时 type 数组表达不了
            # items/properties,退回 anyOf —— 只取第一个会把 list 能力弄丢
            if all(set(s.keys()) <= {"type"} for s in schemas):
                types = [s["type"] for s in schemas]
                base = copy.copy(schemas[0])
                base["type"] = types
                return base
            return {"anyOf": schemas}
        # 单类型模式:跳过 null
        non_null = [s for s in schemas if s.get("type") != "null"]
        if not non_null:
            return schemas[0]
        if len(non_null) == 1:
            # Optional[X] → X,保持既有 schema 形态不变
            return non_null[0]
        # 两个以上实际类型(如 str | list[str]): anyOf 完整表达,
        # 只取第一个会让 LLM 以为只接受 str 而丢掉列表入参
        return {"anyOf": non_null}

    # Enum → string
    if hasattr(tp, "__members__"):
        return {"type": "string", "enum": [m.value for m in tp]}

    return {"type": "string"}


def _parse_docstring(func: Callable) -> tuple[str, dict[str, str]]:
    """从 docstring 一次性提取工具描述和参数描述。

    Args 之前和之后的所有内容作为工具描述,Args 内容解析为参数描述。
    参数行格式为 "name: desc" 或 "name (type): desc",
    冒号前为小写/下划线开头的标识符。同级缩进的非参数行视为 Args 结束。

    Returns:
        tuple: (description, arg_descriptions)
    """
    doc = inspect.getdoc(func) or ""
    if not doc:
        return func.__name__, {}

    desc_lines: list[str] = []
    arg_descs: dict[str, str] = {}
    in_args = False
    args_indent = 0  # Args: 的缩进级别
    param_indent = -1  # 参数行的缩进级别
    current_name: str | None = None
    current_lines: list[str] = []

    def _flush_param():
        nonlocal current_name
        if current_name:
            arg_descs[current_name] = "\n".join(current_lines).strip()
            current_name = None

    for line in doc.split("\n"):
        stripped = line.strip()
        # Args 段落开始
        if stripped == "Args:":
            _flush_param()
            in_args = True
            args_indent = len(line) - len(line.lstrip())
            continue
        if in_args:
            if not stripped:
                continue
            indent = len(line) - len(line.lstrip())
            # 缩进比参数更深 → 当前参数的续行
            if indent > param_indent >= 0:
                if current_name:
                    current_lines.append(stripped)
                continue
            # 比 Args 更深缩进 + 匹配参数行 → 新参数
            if (
                indent > args_indent
                and _PARAM_LINE_RE.match(stripped)
                and not stripped.startswith("- ")
            ):
                _flush_param()
                if param_indent < 0:
                    param_indent = indent
                header, _, rest = stripped.partition(":")
                current_name = header.split("(")[0].strip()
                current_lines = [rest.strip()] if rest.strip() else []
                continue
            # 同级或更浅缩进,或不像参数 → Args 结束
            _flush_param()
            in_args = False
            # fall through to description collection
        if stripped:
            desc_lines.append(stripped)

    _flush_param()
    description = "\n".join(desc_lines) if desc_lines else doc
    return description, arg_descs


def _build_parameters(
    func: Callable,
    arg_descs: dict[str, str] = None,
    multi_type: bool = False,
) -> dict:
    """从函数签名生成 OpenAI function calling 的 parameters schema。

    Args:
        func: 工具函数。
        arg_descs: 参数描述字典。
        multi_type: 是否使用多类型写法(如 ["integer", "null"])。默认为 False。
    """
    sig = inspect.signature(func)
    func_globals = getattr(func, "__globals__", {})
    try:
        hints = get_type_hints(func, globalns=func_globals)
    except Exception as e:
        logger.debug("get_type_hints 失败,使用空 hints: %s", e)
        hints = {}

    if arg_descs is None:
        arg_descs = {}
    properties = {}
    required = []

    for name, param in sig.parameters.items():
        # 跳过运行时注入的参数
        if name in _INJECTED_PARAMS:
            continue

        tp = hints.get(name)
        if tp is None:
            # get_type_hints 失败时,手动解析注解字符串
            ann = param.annotation
            if isinstance(ann, str):
                tp = _resolve_str_annotation(ann, func_globals)
            else:
                tp = ann
        schema = _python_type_to_schema(tp, multi_type)

        # 注入参数描述
        if name in arg_descs:
            schema["description"] = arg_descs[name]

        # 注入默认值
        if param.default is not inspect.Parameter.empty:
            schema["default"] = param.default
        else:
            required.append(name)

        properties[name] = schema

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


@dataclass
class Tool:
    """工具对象 — 包含名称、描述、函数引用和参数 schema。"""

    name: str
    description: str
    func: Callable
    parameters: dict = field(default_factory=dict)

    def _maybe_inject_explain(self, parameters: dict) -> dict:
        """在 parameters schema 中注入 _explain 参数(explain 模式)。"""
        params = copy.deepcopy(parameters)
        params["properties"]["_explain"] = {
            "type": "string",
            "description": "简要说明这个工具的作用以及为什么要调用它。",
        }
        if "_explain" not in params.get("required", []):
            params.setdefault("required", []).append("_explain")
        return params

    def _apply_vision_constraints(self, parameters: dict, has_vision: bool) -> dict:
        """根据视觉能力调整工具参数约束。"""
        import copy

        params = copy.deepcopy(parameters)
        properties = params.get("properties", {})
        required = params.get("required", [])

        if has_vision:
            # 多模态模型:移除 as_text 参数
            if self.name == "ReadMedia" and "as_text" in properties:
                del properties["as_text"]
                if "as_text" in required:
                    required.remove("as_text")
        else:
            # 非多模态模型: as_text 强制必填
            if self.name == "ReadMedia" and "as_text" in properties:
                properties["as_text"]["description"] = "必须传 true"
                if "as_text" not in required:
                    required.append("as_text")

            # 截图/生成图片工具: save_path 改为必填
            if self.name in ("GenerateImage", "browser_screenshot", "cu_screenshot"):
                if "save_path" in properties and "save_path" not in required:
                    required.append("save_path")

        return params

    def _check_vision_support(self, model_name: str = "") -> bool:
        """检查模型是否支持视觉。"""
        if not model_name:
            return True
        try:
            from uniclaw.utils.model_info import get_model_info_provider

            provider = get_model_info_provider()
            # 尝试从缓存获取(同步方式)
            resolved_id = provider._resolve_model_id(model_name)
            if resolved_id and resolved_id in provider._cache:
                info = provider._cache[resolved_id]
                return info.supports_vision
            # 缓存未命中时默认返回 True(由系统提示词处理)
            return True
        except Exception:
            return True

    def to_openai_schema(self, explain: bool = False, model_name: str = "") -> dict:
        """转换为 OpenAI function calling 格式。"""
        parameters = (
            self._maybe_inject_explain(self.parameters) if explain else self.parameters
        )
        has_vision = self._check_vision_support(model_name)
        parameters = self._apply_vision_constraints(parameters, has_vision)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": True,
                "parameters": parameters,
            },
        }

    def to_anthropic_schema(self, explain: bool = False, model_name: str = "") -> dict:
        """转换为 Anthropic tool 格式。"""
        parameters = (
            self._maybe_inject_explain(self.parameters) if explain else self.parameters
        )
        has_vision = self._check_vision_support(model_name)
        parameters = self._apply_vision_constraints(parameters, has_vision)
        return {
            "name": self.name,
            "description": self.description,
            "strict": True,
            "input_schema": parameters,
        }

    async def execute(
        self,
        *args,
        **kwargs,
    ):
        """调用工具,自动处理:
        - 位置参数映射
        - _explain 过滤
        - 注入参数过滤(tool_runtime 由调用方构造传入,按普通 kwargs 透传)
        - 同步/异步自动适配

        支持位置参数和关键字参数,与直接调用函数一致:
            await tool.execute("ls", timeout=30)
            await tool.execute(command="ls", timeout=30)

        异常原样上抛,需要兜底请走 __call__。
        """
        # 将位置参数映射到函数参数名
        sig = inspect.signature(self.func)
        params = list(sig.parameters)
        if args:
            if len(args) > len(params):
                raise TypeError(
                    f"{self.name}() takes {len(params)} positional arguments but {len(args)} were given"
                )
            for i, arg in enumerate(args):
                kwargs[params[i]] = arg
        kwargs.pop("_explain", None)

        # 先取出 tool_runtime: 取消竞争要靠它拿 cancel_event,而下面会按签名
        # 过滤掉 func 不收的注入参数 —— 否则不收 tool_runtime 的 async 工具
        # (如 Grep/browser_*)根本进不了取消竞争,ESC 打不断
        rt = kwargs.get("tool_runtime")

        # 过滤函数签名中不接受的注入参数(如 MCP **kwargs 动态函数)
        # tool_runtime 本身按普通 kwargs 透传,由调用方负责构造传入
        for injected in _INJECTED_PARAMS:
            if injected in kwargs and injected not in params:
                kwargs.pop(injected)

        if not inspect.iscoroutinefunction(self.func):
            return self.func(**kwargs)

        # 异步工具:包装为 task,与 cancel_event 竞争
        # cancel_event 触发时自动取消工具执行,各工具无需单独检查
        cancel_event = None
        if rt is not None:
            try:
                cancel_event = rt.config.current_agent.cancel_event
            except (AttributeError, TypeError):
                pass

        # wait 不是协程函数时视为非标准事件对象(如测试 mock),跳过取消竞争
        if cancel_event is None or not inspect.iscoroutinefunction(cancel_event.wait):
            return await self.func(**kwargs)

        start = time.monotonic()
        task = asyncio.create_task(self.func(**kwargs))
        cancel_wait = asyncio.create_task(cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                [task, cancel_wait],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                return task.result()
            # cancel_event 触发,取消工具任务
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            elapsed = time.monotonic() - start
            return f"{TOOL_ERROR}: {self.name} 被用户中断(已执行 {elapsed:.1f} 秒)"
        finally:
            # 清理:确保两个 task 都被关闭。cancel 后必须 await —— 否则任务
            # 悬挂到 GC,会报 "Task was destroyed but it is pending"
            for t in (cancel_wait, task):
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

    def _bind_call(self, args: tuple, kwargs: dict) -> dict | str:
        """把 (args, kwargs) 绑定到函数签名,返回可直接调用的 kwargs。

        显式 bind 校验:漏传必填、参数名拼错、多传不存在的参数、类型不符 ——
        这些是**模型传参错误**,不是代码 bug,也不走异常:直接返回给模型看的
        错误说明文本,由 `__call__` 作为工具结果返回,与工具函数
        `return f"{TOOL_ERROR}: ..."` 是同一约定。

        Returns:
            dict: 绑定通过,可直接传给 execute() 的 kwargs。
            str: 参数错误说明(带 TOOL_ERROR 前缀),调用方应直接返回给模型。
        """
        sig = inspect.signature(self.func)
        params = list(sig.parameters)

        call = dict(kwargs)
        if args:
            if len(args) > len(params):
                return self._param_error(
                    f"位置参数过多: 最多 {len(params)} 个, 实际 {len(args)} 个",
                    args,
                    kwargs,
                )
            for i, arg in enumerate(args):
                name = params[i]
                if name in call:
                    # 与 Python 语义一致: 同一参数不能既位置又关键字传入
                    return self._param_error(
                        f"参数 '{name}' 重复传入(位置参数与关键字参数各传了一次)",
                        args,
                        kwargs,
                    )
                call[name] = arg
        call.pop("_explain", None)
        # 注入参数里只有 tool_runtime 要保留 —— execute 靠它拿 cancel_event 做
        # async 工具的取消竞争(即使目标函数不收这个形参)。其余注入参数照旧剔除。
        for injected in _INJECTED_PARAMS:
            if injected != "tool_runtime" and injected not in params:
                call.pop(injected, None)

        # 先查未知参数名 —— 模型拼错参数名是最常见的情况, 而 sig.bind() 会先报
        # "missing required argument", 定位不到真正的问题
        accepts_kwargs = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        bindable = {
            k: v for k, v in call.items() if accepts_kwargs or k in params
        }
        if not accepts_kwargs:
            unknown = sorted(
                k for k in call if k not in params and k not in _INJECTED_PARAMS
            )
            if unknown:
                return self._param_error(
                    f"未知参数: {', '.join(unknown)}(不在 {self.name} 签名中)",
                    args,
                    kwargs,
                )

        type_err = self._check_types(bindable)
        if type_err:
            return self._param_error(type_err, args, kwargs)
        # 必填参数缺失单独检查 —— sig.bind 原文是英文("missing a required
        # argument"), 报错要明确, 统一给中文原因
        missing = [
            name
            for name, p in sig.parameters.items()
            if p.default is inspect.Parameter.empty
            and p.kind is not inspect.Parameter.VAR_POSITIONAL
            and p.kind is not inspect.Parameter.VAR_KEYWORD
            and name not in bindable
        ]
        if missing:
            return self._param_error(
                f"缺少必填参数: {', '.join(missing)}", args, kwargs
            )
        try:
            sig.bind(**bindable)
        except TypeError as e:
            # 兜底: 常规检查没拦住的绑定问题, 仍按传参错误返回, 不外抛
            return self._param_error(str(e), args, kwargs)
        return call

    def _check_types(self, call: dict) -> str | None:
        """按注解做保守的参数类型检查,不匹配时返回错误说明(不抛异常)。

        模型传错类型(如该传 list 却传了 str、该传 int 却传了 str)属传参错误,
        应在调用前拦下并给出友好提示 —— 等函数体内抛 TypeError 就会掉进错误日志。
        """
        try:
            hints = get_type_hints(self.func)
        except Exception:
            return None  # 注解解析不了就跳过, 不做检查
        for name, value in call.items():
            expected = hints.get(name)
            if expected is None:
                continue
            if not _value_matches(value, expected):
                return (
                    f"参数 '{name}' 类型不匹配: "
                    f"需要 {_type_name(expected)}, 实际 {type(value).__name__}"
                )
        return None

    def _param_error(self, reason: str, args: tuple, kwargs: dict) -> str:
        """把参数校验失败转成给模型看的错误说明(返回值, 不走异常)。

        原因用中文点名具体参数;实际传入的值只留预览(_shorten)——完整调用
        参数记录由会话保留(tool 消息 args + assistant.tool_calls), 报错文本
        不用重复一遍大值。
        """
        shown = {
            k: _shorten(v)
            for k, v in kwargs.items()
            if k not in _INJECTED_PARAMS and k != "_explain"
        }
        if args:
            shown = {**{f"arg{i}": _shorten(a) for i, a in enumerate(args)}, **shown}

        visible = []
        for name, p in inspect.signature(self.func).parameters.items():
            if name in _INJECTED_PARAMS:
                continue
            if p.kind is inspect.Parameter.VAR_KEYWORD:
                visible.append(f"**{name}")
            elif p.kind is inspect.Parameter.VAR_POSITIONAL:
                visible.append(f"*{name}")
            elif p.default is not inspect.Parameter.empty:
                visible.append(f"{name}={p.default!r}")
            else:
                visible.append(name)

        # 参数完全为空时点明"没收到任何参数" —— 参数被截断/损坏的情况已在
        # parse_tc_args 拦下, 走到这里就是模型侧真的没传
        if shown:
            shown_line = f"实际传入: {shown}"
            hint = "请核对参数名拼写、必填参数是否漏传、是否多传了不存在的参数"
        else:
            shown_line = "实际传入: {} — 未收到任何参数(arguments 为空, 模型侧没有生成参数)"
            hint = "请重新调用并带上全部必填参数"
        return (
            f"{TOOL_ERROR}: {self.name} 参数有误: {reason}\n"
            f"正确签名: {self.name}({', '.join(visible)})\n"
            f"{shown_line}\n"
            f"{hint}"
        )

    async def __call__(
        self,
        *args,
        **kwargs,
    ):
        """调用工具(异常兜底):委托 execute(),**以返回值报错, 不外抛异常**。

        模型传参错误(漏传/拼错/多传参数)由 _bind_call 直接返回错误说明文本
        (含正确签名),与工具函数 `return f"{TOOL_ERROR}: ..."` 同一约定;
        **不写错误日志** —— 那是调用方的问题,不是代码 bug,不需要堆栈;完整
        调用参数记录由会话保留,排查时看会话即可。文件不存在(FileNotFoundError)
        与网络/模型厂商错误(限流 429、超时、远端 5xx 等)同属非 bug:换个路径/
        换个时间就不复现,只 warn 提示,同样不写错误堆栈。其余异常经
        console.ui.err 记日志(含调用入参与完整堆栈),并返回 `{TOOL_ERROR}: ...`
        文本,让 LLM 看到失败原因继续对话。
        asyncio.CancelledError 等 BaseException 仍会上抛,以免吞掉中断信号。
        """
        from uniclaw.console.ui import err, warn

        bound = self._bind_call(args, kwargs)
        if isinstance(bound, str):
            return bound

        rt = bound.get("tool_runtime")
        try:
            return await self.execute(**bound)
        except ToolUserError as e:
            # 预期失败(状态/参数校验): 消息已带上下文, 不写错误日志
            return f"{TOOL_ERROR}: {e}"
        except FileNotFoundError as e:
            # 非 bug: 文件/目录不存在是外部条件, 警告即可, 不写错误堆栈
            config = getattr(rt, "config", None)
            await warn(f"[{self.name}] 文件不存在: {e}", config)
            return f"{TOOL_ERROR}: 文件不存在: {e}"
        except Exception as e:
            config = getattr(rt, "config", None)
            if is_network_or_provider_error(e):
                # 非 bug: 限流/超时/远端 5xx 等外部故障(fallback 已重试耗尽),
                # 警告即可, 不写错误堆栈
                await warn(f"[{self.name}] 模型/网络服务异常: {e}", config)
                return f"{TOOL_ERROR}: 模型/网络服务异常: {e}"
            shown = {
                k: _shorten(v)
                for k, v in bound.items()
                if k not in _INJECTED_PARAMS and k != "_explain"
            }
            await err(f"{TOOL_ERROR}: [{self.name}] {e}\n参数: {shown}", config, e)
            return f"{TOOL_ERROR}: {e}"


def tool(
    func: Callable = None, *, name: str | None = None, multi_type: bool = False
) -> Tool:
    """装饰器:将函数包装为 Tool 对象,自动生成 OpenAI function calling schema。

    用法:
        @tool
        def Bash(command: str, timeout: int = 30, tool_runtime: ToolRuntime = None) -> str:
            \"\"\"执行 shell 命令。\"\"\"
            ...
            config = tool_runtime.config

        @tool(name="custom_name")
        def my_func(...):
            ...

        @tool(multi_type=True)
        def my_func2(x: int | None = None) -> str:
            \"\"\"支持多类型写法的工具。\"\"\"
            ...
    """

    def decorator(f: Callable) -> Tool:
        tool_name = name or f.__name__
        description, arg_descs = _parse_docstring(f)
        parameters = _build_parameters(f, arg_descs, multi_type)
        return Tool(
            name=tool_name, description=description, func=f, parameters=parameters
        )

    if func is not None:
        return decorator(func)
    return decorator


# ── explain 模式辅助函数 ────────────────────────────────────────────────


def extract_explains(tool_calls: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """从 tool_calls 中提取 _explain 参数并移除,返回 (清理后的 tool_calls, {id: explain})。"""
    tool_explains: dict[str, str] = {}
    for tc in tool_calls:
        fn = tc.get("function")
        if not fn:
            continue
        raw = fn.get("arguments", "{}")
        if isinstance(raw, str):
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {}
        elif isinstance(raw, dict):
            args = raw
        else:
            args = {}
        explain_text = args.pop("_explain", None)
        if explain_text:
            tool_explains[tc.get("id", "")] = explain_text
            fn["arguments"] = json.dumps(args, ensure_ascii=False)
    return tool_calls, tool_explains


def should_explain(
    tool_name: str, explain_mode: bool | set, is_sub: bool = False
) -> bool:
    """判断指定工具是否需要注入 _explain 参数。sub-agent 不受影响。"""
    if is_sub:
        return False
    if explain_mode is True:
        return True
    if isinstance(explain_mode, set):
        return tool_name in explain_mode
    return False


# ── tool_call 解析辅助函数 ──────────────────────────────────────────────


def tc_name(tc: dict) -> str:
    """从 tool_call 提取工具名(兼容 OpenAI 和旧格式)。"""
    fn = tc.get("function")
    if fn:
        return fn.get("name", "")
    return tc.get("name", "")


def parse_tc_args(tc: dict) -> tuple[dict, str | None]:
    """解析 tool_call 的参数, 区分"模型没传参数"与"参数被截断/损坏"。

    Returns:
        tuple[dict, str | None]: (参数 dict, 问题说明)。
            - 问题说明为 None: 解析成功(含合法的空参数 —— 无参工具的正常形态)
            - 问题说明非 None: arguments 非空但解析不了(JSON 被截断/损坏, 或不是
              对象)。此时参数 dict 为 `{"_raw_arguments": 原始字符串}` —— 原始
              记录随 args 流入工具事件与会话 tool 消息, 完整保留、不丢失;
              问题说明只带预览(_shorten)。调用方据此拦下执行, 免得损坏参数被
              伪装成"模型没传参数"的缺参错误。
    """
    fn = tc.get("function")
    raw = fn.get("arguments", "") if fn else tc.get("args", {})
    if isinstance(raw, dict):
        return raw, None
    if not isinstance(raw, str):
        return (
            {"_raw_arguments": repr(raw)},
            f"arguments 类型非法: {type(raw).__name__}",
        )
    text = raw.strip()
    if not text or text == "{}":
        # 空 arguments = 模型侧没生成参数, 交给参数绑定校验报缺参
        return {}, None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as e:
        return (
            {"_raw_arguments": text},
            f"arguments JSON 解析失败(疑似被截断或传输损坏): {e};"
            f"原始内容预览: {_shorten(text)}",
        )
    if not isinstance(parsed, dict):
        return (
            {"_raw_arguments": text},
            f"arguments JSON 不是对象({type(parsed).__name__});"
            f"原始内容预览: {_shorten(text)}",
        )
    return parsed, None


def tc_args(tc: dict) -> dict:
    """从 tool_call 提取参数 dict(兼容 OpenAI 和旧格式)。

    解析失败时返回 {}, 不做区分 —— 显示/死循环检测等只关心参数值的场景够用。
    工具执行入口要区分"没传参数"与"参数损坏", 用 parse_tc_args。
    """
    return parse_tc_args(tc)[0]
