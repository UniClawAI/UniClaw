"""tests for src/uniclaw/tools/base.py — @tool 装饰器和工具 schema 生成。"""

import inspect
from enum import Enum
from typing import Optional, Union

from uniclaw.tools.base import (
    Tool,
    _build_parameters,
    _parse_docstring,
    _python_type_to_schema,
    extract_explains,
    should_explain,
    tc_args,
    tc_name,
    tool,
)

# ── _python_type_to_schema ───────────────────────────────────────


class TestPythonTypeToSchema:
    def test_str(self):
        assert _python_type_to_schema(str) == {"type": "string"}

    def test_int(self):
        assert _python_type_to_schema(int) == {"type": "integer"}

    def test_float(self):
        assert _python_type_to_schema(float) == {"type": "number"}

    def test_bool(self):
        assert _python_type_to_schema(bool) == {"type": "boolean"}

    def test_list(self):
        assert _python_type_to_schema(list) == {"type": "array"}

    def test_dict(self):
        assert _python_type_to_schema(dict) == {"type": "object"}

    def test_none_type(self):
        assert _python_type_to_schema(type(None)) == {"type": "null"}

    def test_empty_param(self):
        assert _python_type_to_schema(inspect.Parameter.empty) == {"type": "string"}

    def test_list_of_str(self):
        result = _python_type_to_schema(list[str])
        assert result == {"type": "array", "items": {"type": "string"}}

    def test_dict_type(self):
        result = _python_type_to_schema(dict[str, int])
        assert result == {"type": "object"}

    def test_optional_single_type_mode(self):
        result = _python_type_to_schema(Optional[str], multi_type=False)
        assert result == {"type": "string"}

    def test_optional_multi_type_mode(self):
        result = _python_type_to_schema(Optional[str], multi_type=True)
        assert result["type"] == ["string", "null"]

    def test_union_multi_type(self):
        result = _python_type_to_schema(Union[int, str], multi_type=True)
        assert set(result["type"]) == {"integer", "string"}

    def test_enum(self):
        class Color(Enum):
            RED = "red"
            BLUE = "blue"

        result = _python_type_to_schema(Color)
        assert result == {"type": "string", "enum": ["red", "blue"]}

    def test_unknown_type_fallback(self):
        class Custom:
            pass

        result = _python_type_to_schema(Custom)
        assert result == {"type": "string"}


# ── _parse_docstring ─────────────────────────────────────────────


class TestParseDocstring:
    def test_no_docstring(self):
        def no_doc(x):
            pass

        desc, args = _parse_docstring(no_doc)
        assert desc == "no_doc"
        assert args == {}

    def test_description_only(self):
        def desc_only():
            """This is a description."""

        desc, args = _parse_docstring(desc_only)
        assert "This is a description." in desc
        assert args == {}

    def test_with_args(self):
        def with_args(x, y):
            """My tool.

            Args:
                x: The x value.
                y: The y value.
            """

        desc, args = _parse_docstring(with_args)
        assert "My tool." in desc
        assert args == {"x": "The x value.", "y": "The y value."}

    def test_multiline_arg(self):
        def multi(x):
            """Tool.

            Args:
                x: Line one.
                   Line two continues here.
            """

        _, args = _parse_docstring(multi)
        assert "Line one." in args["x"]
        assert "Line two" in args["x"]

    def test_typed_param_header(self):
        def typed(x):
            """Tool.

            Args:
                x (str): The x value.
            """

        _, args = _parse_docstring(typed)
        assert args["x"] == "The x value."

    def test_empty_args(self):
        def empty():
            """Tool.

            Args:
            """

        desc, args = _parse_docstring(empty)
        assert args == {}


# ── _build_parameters ────────────────────────────────────────────


class TestBuildParameters:
    def test_basic_types(self):
        def func(a: str, b: int, c: float, d: bool):
            """Tool.

            Args:
                a: str param
                b: int param
            """

        params = _build_parameters(func)
        assert params["type"] == "object"
        assert params["properties"]["a"]["type"] == "string"
        assert params["properties"]["b"]["type"] == "integer"
        assert params["properties"]["c"]["type"] == "number"
        assert params["properties"]["d"]["type"] == "boolean"
        assert set(params["required"]) == {"a", "b", "c", "d"}

    def test_default_not_required(self):
        def func(a: str, b: int = 10):
            """Tool."""

        params = _build_parameters(func)
        assert "a" in params["required"]
        assert "b" not in params["required"]
        assert params["properties"]["b"]["default"] == 10

    def test_config_excluded(self):
        def func(a: str, config=None):
            """Tool."""

        params = _build_parameters(func)
        assert "a" in params["properties"]
        assert "config" not in params["properties"]

    def test_arg_descs_injected(self):
        def func(a: str):
            """Tool.

            Args:
                a: Description for a.
            """

        params = _build_parameters(func, {"a": "Description for a."})
        assert params["properties"]["a"]["description"] == "Description for a."

    def test_list_param(self):
        def func(items: list[str]):
            """Tool."""

        params = _build_parameters(func)
        assert params["properties"]["items"]["type"] == "array"
        assert params["properties"]["items"]["items"]["type"] == "string"

    def test_additional_properties_false(self):
        def func():
            """Tool."""

        params = _build_parameters(func)
        assert params["additionalProperties"] is False


# ── extract_explains ─────────────────────────────────────────────


class TestExtractExplains:
    def test_explain_in_json_string(self):
        tc = {
            "id": "call_1",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls", "_explain": "list files"}',
            },
        }
        result, explains = extract_explains([tc])
        assert explains == {"call_1": "list files"}
        import json

        args = json.loads(result[0]["function"]["arguments"])
        assert "_explain" not in args
        assert args["command"] == "ls"

    def test_explain_in_dict(self):
        tc = {
            "id": "call_2",
            "function": {
                "name": "Read",
                "arguments": {"path": "/tmp", "_explain": "read file"},
            },
        }
        _, explains = extract_explains([tc])
        assert explains == {"call_2": "read file"}

    def test_no_explain(self):
        tc = {
            "id": "call_3",
            "function": {"name": "Read", "arguments": '{"path": "/tmp"}'},
        }
        _, explains = extract_explains([tc])
        assert explains == {}

    def test_no_function_key(self):
        tc = {"id": "call_4"}
        _, explains = extract_explains([tc])
        assert explains == {}

    def test_multiple_calls(self):
        tcs = [
            {"id": "c1", "function": {"name": "A", "arguments": '{"_explain": "a"}'}},
            {"id": "c2", "function": {"name": "B", "arguments": '{"_explain": "b"}'}},
        ]
        _, explains = extract_explains(tcs)
        assert explains == {"c1": "a", "c2": "b"}


# ── should_explain ───────────────────────────────────────────────


class TestShouldExplain:
    def test_sub_agent_always_false(self):
        assert should_explain("Read", True, is_sub=True) is False
        assert should_explain("Read", {"Read"}, is_sub=True) is False

    def test_mode_true(self):
        assert should_explain("Read", True) is True

    def test_mode_false(self):
        assert should_explain("Read", False) is False

    def test_mode_set_match(self):
        assert should_explain("Read", {"Read", "Write"}) is True

    def test_mode_set_no_match(self):
        assert should_explain("Bash", {"Read", "Write"}) is False

    def test_mode_empty_set(self):
        assert should_explain("Read", set()) is False


# ── tc_name / tc_args ────────────────────────────────────────────


class TestTcName:
    def test_openai_format(self):
        tc = {"function": {"name": "Read"}}
        assert tc_name(tc) == "Read"

    def test_legacy_format(self):
        tc = {"name": "Bash"}
        assert tc_name(tc) == "Bash"

    def test_missing_name(self):
        tc = {"function": {}}
        assert tc_name(tc) == ""

    def test_no_function_no_name(self):
        tc = {}
        assert tc_name(tc) == ""


class TestTcArgs:
    def test_json_string(self):
        tc = {"function": {"arguments": '{"path": "/tmp"}'}}
        assert tc_args(tc) == {"path": "/tmp"}

    def test_dict(self):
        tc = {"function": {"arguments": {"path": "/tmp"}}}
        assert tc_args(tc) == {"path": "/tmp"}

    def test_empty_string(self):
        tc = {"function": {"arguments": ""}}
        assert tc_args(tc) == {}

    def test_invalid_json(self):
        tc = {"function": {"arguments": "not json"}}
        assert tc_args(tc) == {}

    def test_no_arguments(self):
        tc = {"function": {}}
        assert tc_args(tc) == {}

    def test_legacy_format(self):
        tc = {"args": {"x": 1}}
        assert tc_args(tc) == {"x": 1}

    def test_no_function_no_args(self):
        tc = {}
        assert tc_args(tc) == {}


# ── Tool schema 转换 ─────────────────────────────────────────────


class TestToolSchema:
    def _make_tool(self):
        def my_func(a: str, b: int = 5):
            """My tool.

            Args:
                a: param a
            """

        return tool(my_func)

    def test_to_openai_schema(self):
        t = self._make_tool()
        schema = t.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "my_func"
        assert schema["function"]["strict"] is True
        assert "a" in schema["function"]["parameters"]["properties"]

    def test_to_anthropic_schema(self):
        t = self._make_tool()
        schema = t.to_anthropic_schema()
        assert schema["name"] == "my_func"
        assert schema["strict"] is True
        assert "input_schema" in schema
        assert "a" in schema["input_schema"]["properties"]

    def test_explain_injects_param(self):
        t = self._make_tool()
        schema = t.to_openai_schema(explain=True)
        props = schema["function"]["parameters"]["properties"]
        assert "_explain" in props

    def test_explain_does_not_mutate_original(self):
        t = self._make_tool()
        original_keys = set(t.parameters.get("properties", {}).keys())
        t.to_openai_schema(explain=True)
        assert set(t.parameters.get("properties", {}).keys()) == original_keys

    def test_args_property(self):
        t = self._make_tool()
        props = t.parameters.get("properties", {})
        assert "a" in props
        assert "b" in props


# ── @tool 装饰器 ─────────────────────────────────────────────────


class TestToolDecorator:
    def test_basic_decoration(self):
        @tool
        def hello(name: str) -> str:
            """Say hello.

            Args:
                name: The name.
            """

        assert isinstance(hello, Tool)
        assert hello.name == "hello"
        assert "Say hello" in hello.description
        assert hello.parameters["properties"]["name"]["type"] == "string"

    def test_custom_name(self):
        @tool(name="custom")
        def func():
            """Doc."""

        assert func.name == "custom"

    def test_config_not_in_schema(self):
        @tool
        def func(a: str, config=None):
            """Doc."""

        assert "config" not in func.parameters["properties"]
        assert "a" in func.parameters["properties"]
