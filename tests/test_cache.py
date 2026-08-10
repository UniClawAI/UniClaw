"""ttl_cache 装饰器测试 — 覆盖缓存命中、过期、清除等场景。"""

import time
from unittest.mock import patch

from uniclaw.utils.cache import ttl_cache


class TestTtlCache:
    """ttl_cache 带过期时间的缓存装饰器测试。"""

    def test_cache_hit(self):
        """相同参数在 TTL 内返回缓存值。"""
        call_count = 0

        @ttl_cache(ttl_seconds=10)
        def add(a, b):
            nonlocal call_count
            call_count += 1
            return a + b

        assert add(1, 2) == 3
        assert add(1, 2) == 3
        assert call_count == 1  # 第二次命中缓存

    def test_cache_miss_different_args(self):
        """不同参数不命中缓存。"""
        call_count = 0

        @ttl_cache(ttl_seconds=10)
        def add(a, b):
            nonlocal call_count
            call_count += 1
            return a + b

        assert add(1, 2) == 3
        assert add(3, 4) == 7
        assert call_count == 2

    def test_cache_expiration(self):
        """超过 TTL 后缓存失效。"""
        call_count = 0

        @ttl_cache(ttl_seconds=0.1)
        def add(a, b):
            nonlocal call_count
            call_count += 1
            return a + b

        assert add(1, 2) == 3
        assert call_count == 1

        # 等待过期
        time.sleep(0.15)
        assert add(1, 2) == 3
        assert call_count == 2  # 缓存已过期,重新调用

    def test_cache_clear(self):
        """cache_clear 清除缓存。"""
        call_count = 0

        @ttl_cache(ttl_seconds=60)
        def add(a, b):
            nonlocal call_count
            call_count += 1
            return a + b

        add(1, 2)
        add(1, 2)
        assert call_count == 1

        add.cache_clear()
        add(1, 2)
        assert call_count == 2  # 清除后重新调用

    def test_kwargs_caching(self):
        """关键字参数也参与缓存。"""
        call_count = 0

        @ttl_cache(ttl_seconds=10)
        def greet(name="world"):
            nonlocal call_count
            call_count += 1
            return f"hello {name}"

        assert greet(name="alice") == "hello alice"
        assert greet(name="alice") == "hello alice"
        assert call_count == 1

        assert greet(name="bob") == "hello bob"
        assert call_count == 2

    def test_preserves_function_name(self):
        """装饰器保留原函数名。"""

        @ttl_cache(ttl_seconds=10)
        def my_function():
            pass

        assert my_function.__name__ == "my_function"

    def test_preserves_return_value(self):
        """装饰器正确返回原函数的返回值。"""

        @ttl_cache(ttl_seconds=10)
        def get_dict():
            return {"key": "value"}

        result = get_dict()
        assert result == {"key": "value"}
        assert get_dict() is result  # 同一对象

    def test_none_return_value(self):
        """None 也可以被缓存。"""
        call_count = 0

        @ttl_cache(ttl_seconds=10)
        def returns_none():
            nonlocal call_count
            call_count += 1
            return None

        assert returns_none() is None
        assert returns_none() is None
        assert call_count == 1

    def test_exception_not_cached(self):
        """异常不会被缓存(每次都会重新调用)。"""
        call_count = 0

        @ttl_cache(ttl_seconds=10)
        def raises():
            nonlocal call_count
            call_count += 1
            raise ValueError("test")

        try:
            raises()
        except ValueError:
            pass
        assert call_count == 1

        try:
            raises()
        except ValueError:
            pass
        assert call_count == 2  # 异常不缓存,重新调用
