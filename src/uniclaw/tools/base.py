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
import typing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, get_type_hints

if TYPE_CHECKING:
    from uniclaw.config import AppConfig

from uniclaw.utils.constants import TOOL_ERROR

logger = logging.getLogger(__name__)


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
        schemas = [_python_type_to_schema(a, multi_type) for a in tp.__args__]
        if multi_type:
            types = [s["type"] for s in schemas]
            base = copy.copy(schemas[0])
            base["type"] = types
            return base
        # 单类型模式:跳过 null,取第一个有效类型
        non_null = [s for s in schemas if s.get("type") != "null"]
        return non_null[0] if non_null else schemas[0]

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

    async def __call__(
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
            await tool("ls", timeout=30)
            await tool(command="ls", timeout=30)
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

        # 过滤函数签名中不接受的注入参数(如 MCP **kwargs 动态函数)
        # tool_runtime 本身按普通 kwargs 透传,由调用方负责构造传入
        for injected in _INJECTED_PARAMS:
            if injected in kwargs and injected not in params:
                kwargs.pop(injected)

        if not inspect.iscoroutinefunction(self.func):
            return self.func(**kwargs)

        # 异步工具:包装为 task,与 cancel_event 竞争
        # cancel_event 触发时自动取消工具执行,各工具无需单独检查
        rt = kwargs.get("tool_runtime")
        cancel_event = None
        if rt is not None:
            try:
                cancel_event = rt.config.current_agent.cancel_event
            except (AttributeError, TypeError):
                pass

        if cancel_event is None:
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
            # 清理:确保两个 task 都被关闭
            if not cancel_wait.done():
                cancel_wait.cancel()
            if not task.done():
                task.cancel()


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


def tc_args(tc: dict) -> dict:
    """从 tool_call 提取参数 dict(兼容 OpenAI 和旧格式)。"""
    fn = tc.get("function")
    if fn:
        args = fn.get("arguments", "")
        if isinstance(args, str):
            try:
                return json.loads(args) if args else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return args if isinstance(args, dict) else {}
    args = tc.get("args", {})
    return args if isinstance(args, dict) else {}
