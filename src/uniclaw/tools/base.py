"""自定义 @tool 装饰器 — 生成 OpenAI function calling schema,自动排除 config 参数。"""

from __future__ import annotations

import copy
import inspect
import json
import logging
import re
import typing
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, get_type_hints

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
_INJECTED_PARAMS = {"config"}

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
}


def _resolve_str_annotation(tp: str, func_globals: dict = None) -> Any:
    """将字符串形式的类型注解解析为实际类型。"""
    tp = tp.strip()
    # 简单类型直接查表
    if tp in _BUILTIN_TYPE_NAMES:
        return _BUILTIN_TYPE_NAMES[tp]
    # None / NoneType
    if tp == "None":
        return type(None)
    try:
        import builtins

        ns = {"__builtins__": builtins, **_BUILTIN_TYPE_NAMES}
        if func_globals:
            ns.update(func_globals)
        return eval(tp, ns)
    except Exception as e:
        logger.debug("解析类型注解 '%s' 失败,回退为 str: %s", tp, e)
        return str


def _python_type_to_schema(tp: Any) -> dict:
    """将 Python 类型注解转换为 JSON Schema。"""
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
            return {"type": "array", "items": _python_type_to_schema(args[0])}
        return {"type": "array"}

    # Dict[str, X] / dict[str, X]
    if origin is dict:
        return {"type": "object"}

    # 处理 NoneType
    if tp is type(None):
        return {"type": "null"}

    # 处理 Optional[X] / X | None / Union[X, Y]
    if origin is typing.Union:
        schemas = [_python_type_to_schema(a) for a in tp.__args__]
        types = [s["type"] for s in schemas]
        # 如果只有一种类型,直接返回完整 schema(保留 enum/items 等)
        if len(schemas) == 1:
            return schemas[0]
        # 多种类型合并 type 数组(保留第一个非 null schema 的额外字段)
        base = copy.copy(next((s for s in schemas if s["type"] != "null"), schemas[0]))
        base["type"] = types
        return base

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
            if indent > args_indent and _PARAM_LINE_RE.match(stripped) and not stripped.startswith("- "):
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


def _build_parameters(func: Callable, arg_descs: dict[str, str] = None) -> dict:
    """从函数签名生成 OpenAI function calling 的 parameters schema。"""
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
        schema = _python_type_to_schema(tp)

        # 注入参数描述
        if name in arg_descs:
            schema["description"] = arg_descs[name]

        # 处理 list 类型的 items(从注解中提取)
        if param.default is not inspect.Parameter.empty:
            # 默认值为 None 时,自动标记为 nullable
            if param.default is None:
                tp = schema.get("type")
                if isinstance(tp, str) and tp != "null":
                    schema["type"] = [tp, "null"]
                elif isinstance(tp, list) and "null" not in tp:
                    tp.append("null")
        else:
            required.append(name)

        properties[name] = schema

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


@dataclass
class Tool:
    """工具对象 — 包含名称、描述、函数引用和参数 schema。"""

    name: str
    description: str
    func: Callable
    parameters: dict = field(default_factory=dict)

    @property
    def args(self) -> dict:
        """参数属性字典(兼容旧接口)。"""
        return self.parameters.get("properties", {})

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

    def to_openai_schema(self, explain: bool = False) -> dict:
        """转换为 OpenAI function calling 格式。"""
        parameters = (
            self._maybe_inject_explain(self.parameters) if explain else self.parameters
        )
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }

    def to_anthropic_schema(self, explain: bool = False) -> dict:
        """转换为 Anthropic tool 格式。"""
        parameters = (
            self._maybe_inject_explain(self.parameters) if explain else self.parameters
        )
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": parameters,
        }

    async def __call__(
        self,
        *args,
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        **kwargs,
    ):
        """调用工具,自动处理:
        - 过滤 _explain 参数
        - 异步/同步自动适配
        - 流式回调设置/清理

        支持位置参数和关键字参数,与直接调用函数一致:
            await tool("ls", timeout=30, config=config)
            await tool(command="ls", timeout=30, config=config)
        """
        # 将位置参数映射到函数参数名
        if args:
            params = list(inspect.signature(self.func).parameters)
            if len(args) > len(params):
                raise TypeError(
                    f"{self.name}() takes {len(params)} positional arguments but {len(args)} were given"
                )
            for i, arg in enumerate(args):
                kwargs[params[i]] = arg
        kwargs.pop("_explain", None)
        # 过滤函数签名中不接受的注入参数(如 config)
        func_params = inspect.signature(self.func).parameters
        for injected in _INJECTED_PARAMS:
            if injected in kwargs and injected not in func_params:
                kwargs.pop(injected)
        if not inspect.iscoroutinefunction(self.func):
            return self.func(**kwargs)
        # 异步工具:支持流式回调
        if stream_callback:
            from uniclaw.tools.stream import set_stream_callback, reset_stream_callback

            token = set_stream_callback(stream_callback)
            try:
                return await self.func(**kwargs)
            finally:
                reset_stream_callback(token)
        return await self.func(**kwargs)


def tool(func: Callable = None, *, name: str = None) -> Tool:
    """装饰器:将函数包装为 Tool 对象,自动生成 OpenAI function calling schema。

    用法:
        @tool
        def Bash(command: str, timeout: int = 30, config: AppConfig = None) -> str:
            \"\"\"执行 shell 命令。\"\"\"
            ...

        @tool(name="custom_name")
        def my_func(...):
            ...
    """

    def decorator(f: Callable) -> Tool:
        tool_name = name or f.__name__
        description, arg_descs = _parse_docstring(f)
        parameters = _build_parameters(f, arg_descs)
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
        args = (
            json.loads(raw)
            if isinstance(raw, str)
            else (raw if isinstance(raw, dict) else {})
        )
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
