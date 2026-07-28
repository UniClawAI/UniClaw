"""
RunCode 工具的单元测试
需要 Docker 环境才能运行
"""

import asyncio
import pytest
from uniclaw.tools.sandbox import RunCode, _check_docker, LANG_CONFIG


def _is_docker_available() -> bool:
    """同步检查 Docker 是否可用"""
    try:
        return asyncio.run(_check_docker()) is None
    except Exception:
        return False


# 跳过整个模块如果 Docker 不可用
pytestmark = pytest.mark.skipif(
    not _is_docker_available(),
    reason="Docker 不可用",
)


class TestRunCodePython:
    """Python 代码执行测试"""

    def test_hello_world(self):
        result = asyncio.run(RunCode.func("python", "print('hello world')"))
        assert result == "hello world"

    def test_multiline_code(self):
        code = """
for i in range(3):
    print(i)
"""
        result = asyncio.run(RunCode.func("python", code.strip()))
        assert result == "0\n1\n2"

    def test_import_stdlib(self):
        code = "import json; print(json.dumps({'a': 1}))"
        result = asyncio.run(RunCode.func("python", code))
        assert '{"a": 1}' in result

    def test_syntax_error(self):
        result = asyncio.run(RunCode.func("python", "def foo("))
        assert "Error" in result or "SyntaxError" in result

    def test_runtime_error(self):
        result = asyncio.run(RunCode.func("python", "raise ValueError('test error')"))
        assert "ValueError" in result
        assert "test error" in result


class TestRunCodeJavaScript:
    """JavaScript 代码执行测试"""

    def test_hello_world(self):
        result = asyncio.run(RunCode.func("javascript", "console.log('hello world')"))
        assert result == "hello world"

    def test_multiline(self):
        code = "for (let i = 0; i < 3; i++) { console.log(i); }"
        result = asyncio.run(RunCode.func("javascript", code))
        assert "0" in result
        assert "2" in result


class TestRunCodeShell:
    """Shell 代码执行测试"""

    def test_echo(self):
        result = asyncio.run(RunCode.func("shell", "echo hello"))
        assert result == "hello"

    def test_ls(self):
        result = asyncio.run(RunCode.func("shell", "ls /code"))
        assert "code" in result  # 代码文件本身


class TestRunCodeValidation:
    """参数校验测试"""

    def test_unsupported_language(self):
        result = asyncio.run(RunCode.func("rust", "fn main() {}"))
        assert "ERROR" in result
        assert "不支持" in result

    def test_empty_code(self):
        result = asyncio.run(RunCode.func("python", ""))
        assert "ERROR" in result
        assert "不能为空" in result

    def test_whitespace_only_code(self):
        result = asyncio.run(RunCode.func("python", "   \n  "))
        assert "ERROR" in result
        assert "不能为空" in result


class TestRunCodeSecurity:
    """安全限制测试"""

    def test_network_disabled_by_default(self):
        code = """
import urllib.request
try:
    urllib.request.urlopen("http://example.com", timeout=5)
    print("NETWORK_OK")
except Exception as e:
    print(f"BLOCKED: {e}")
"""
        result = asyncio.run(RunCode.func("python", code.strip()))
        assert "BLOCKED" in result or "NETWORK_OK" not in result

    def test_network_enabled(self):
        code = """
import urllib.request
try:
    r = urllib.request.urlopen("http://example.com", timeout=5)
    print("NETWORK_OK")
except Exception as e:
    print(f"FAILED: {e}")
"""
        result = asyncio.run(RunCode.func("python", code.strip(), network=True))
        assert "NETWORK_OK" in result


class TestRunCodeTimeout:
    """超时测试"""

    def test_timeout(self):
        code = "import time; time.sleep(60)"
        result = asyncio.run(RunCode.func("python", code, timeout=3))
        assert "超时" in result or "Error" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
