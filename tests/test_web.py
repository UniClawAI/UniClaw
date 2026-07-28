"""
web.py 模块的单元测试

测试网页获取和搜索功能
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from uniclaw.tools.web import _get_proxy


class TestGetProxy:
    """_get_proxy 函数测试"""

    def test_none_config(self):
        """测试 None 配置"""
        result = _get_proxy(None)
        assert result is None

    def test_empty_proxy(self):
        """测试空代理"""
        config = MagicMock()
        config.proxy_url = ""
        result = _get_proxy(config)
        assert result is None

    def test_valid_proxy(self):
        """测试有效代理"""
        config = MagicMock()
        config.proxy_url = "http://proxy:8080"
        result = _get_proxy(config)
        assert result == "http://proxy:8080"

    def test_https_proxy(self):
        """测试 HTTPS 代理"""
        config = MagicMock()
        config.proxy_url = "https://proxy:8080"
        result = _get_proxy(config)
        assert result == "https://proxy:8080"

    def test_invalid_proxy(self):
        """测试无效代理"""
        config = MagicMock()
        config.proxy_url = "socks5://proxy:8080"
        result = _get_proxy(config)
        assert result is None

    def test_non_string_proxy(self):
        """测试非字符串代理"""
        config = MagicMock()
        config.proxy_url = 123
        result = _get_proxy(config)
        assert result is None

    def test_ftp_proxy(self):
        """测试 FTP 代理"""
        config = MagicMock()
        config.proxy_url = "ftp://proxy:8080"
        result = _get_proxy(config)
        assert result is None
