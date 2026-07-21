"""自定义 @tool 装饰器 — 生成 OpenAI function calling schema,自动排除 config 参数。"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, get_type_hints


# 类型映射: Python type → JSON Schema type
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

# 运行时注入的参数,不写入 schema
_INJECTED_PARAMS = {"config"}

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


def _resolve_str_annotation(tp: str) -> Any:
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
        return eval(tp, {"__builtins__": builtins}, _BUILTIN_TYPE_NAMES)
    except Exception:
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
    if hasattr(tp, "__args__"):
        args = [a for a in tp.__args__ if a is not type(None)]
        if len(args) == 1:
            return _python_type_to_schema(args[0])
        return {"type": "string"}

    # Enum → string
    if hasattr(tp, "__members__"):
        return {"type": "string", "enum": list(tp.__members__.keys())}

    return {"type": "string"}


def _build_parameters(func: Callable) -> dict:
    """从函数签名生成 OpenAI function calling 的 parameters schema。"""
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    properties = {}
    required = []

    for name, param in sig.parameters.items():
        # 跳过运行时注入的参数
        if name in _INJECTED_PARAMS:
            continue

        tp = hints.get(name, param.annotation)
        schema = _python_type_to_schema(tp)

        # 处理 list 类型的 items(从注解中提取)
        if param.default is not inspect.Parameter.empty:
            schema["default"] = param.default
        else:
            required.append(name)

        properties[name] = schema

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _extract_description(func: Callable) -> str:
    """从 docstring 提取工具描述(跳过 Args/Returns 之前的内容)。"""
    doc = inspect.getdoc(func) or ""
    if not doc:
        return func.__name__

    # 提取 Args/Returns 之前的所有内容
    lines = []
    for line in doc.split("\n"):
        stripped = line.strip()
        # 遇到 Args: 或 Returns: 停止
        if stripped.startswith("Args:") or stripped.startswith("Returns:"):
            break
        if stripped:
            lines.append(stripped)

    return " ".join(lines) if lines else func.__name__


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
        import copy

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
        parameters = self._maybe_inject_explain(self.parameters) if explain else self.parameters
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
        parameters = self._maybe_inject_explain(self.parameters) if explain else self.parameters
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": parameters,
        }

    async def __call__(self, args: dict, config=None, stream_callback=None):
        """调用工具,自动处理:
        - 过滤 _explain 参数
        - 注入 config (如果函数签名需要)
        - 异步/同步自动适配
        - 流式回调设置/清理
        """
        kwargs = {k: v for k, v in args.items() if k != "_explain"}
        if config is not None and "config" in inspect.signature(self.func).parameters:
            kwargs["config"] = config
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
        description = _extract_description(f)
        parameters = _build_parameters(f)
        return Tool(name=tool_name, description=description, func=f, parameters=parameters)

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
        args = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
        explain_text = args.pop("_explain", None)
        if explain_text:
            tool_explains[tc.get("id", "")] = explain_text
            fn["arguments"] = json.dumps(args, ensure_ascii=False)
    return tool_calls, tool_explains


def should_explain(tool_name: str, explain_mode: bool | set, is_sub: bool = False) -> bool:
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
    import json

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
