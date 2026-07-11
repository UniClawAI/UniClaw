"""
search.py 模块的单元测试

测试 platform_search 工具的平台路由、缓存、超时、错误处理等。
使用 mock 避免真实网络请求,验证各平台搜索函数的调用和返回格式。
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from uniclaw.tools.search import (
    platform_search,
    _parse_platforms,
    _cache_key,
    _get_proxy,
    _PLATFORM_SEARCHERS,
    get_tools,
    get_all_tools,
    _search_cache,
)

# platform_search 是 Tool 对象,通过 .func 调用
_search = platform_search.func


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清空缓存。"""
    _search_cache.clear()
    yield
    _search_cache.clear()


@pytest.fixture
def mock_config():
    """模拟 AppConfig。"""
    config = MagicMock()
    config.proxy_url = ""
    config.GITHUB_TOKEN = ""
    return config


@pytest.fixture
def mock_config_with_proxy():
    """带代理的 AppConfig。"""
    config = MagicMock()
    config.proxy_url = "http://127.0.0.1:7890"
    config.GITHUB_TOKEN = "test-token"
    return config


# ── 辅助函数测试 ─────────────────────────────────────────────


class TestParsePlatforms:
    """_parse_platforms 测试"""

    def test_all(self):
        assert _parse_platforms("all") == list(_PLATFORM_SEARCHERS.keys())

    def test_single(self):
        assert _parse_platforms("github") == ["github"]

    def test_multiple(self):
        result = _parse_platforms("github,arxiv")
        assert result == ["github", "arxiv"]

    def test_with_spaces(self):
        result = _parse_platforms("github, arxiv , bilibili")
        assert result == ["github", "arxiv", "bilibili"]

    def test_unknown_filtered(self):
        result = _parse_platforms("github,unknown,arxiv")
        assert result == ["github", "arxiv"]

    def test_all_unknown(self):
        result = _parse_platforms("unknown1,unknown2")
        assert result == []

    def test_case_insensitive(self):
        assert _parse_platforms("GitHub") == ["github"]
        assert _parse_platforms("STACKOVERFLOW") == ["stackoverflow"]


class TestGetProxy:
    """_get_proxy 测试"""

    def test_none_config(self):
        assert _get_proxy(None) is None

    def test_empty_proxy(self):
        config = MagicMock()
        config.proxy_url = ""
        assert _get_proxy(config) is None

    def test_valid_proxy(self):
        config = MagicMock()
        config.proxy_url = "http://proxy:8080"
        assert _get_proxy(config) == "http://proxy:8080"

    def test_https_proxy(self):
        config = MagicMock()
        config.proxy_url = "https://proxy:8080"
        assert _get_proxy(config) == "https://proxy:8080"

    def test_invalid_proxy(self):
        config = MagicMock()
        config.proxy_url = "socks5://proxy:8080"
        assert _get_proxy(config) is None


class TestCacheKey:
    """_cache_key 测试"""

    def test_basic(self):
        key = _cache_key("python", "github")
        assert "python" in key and "github" in key

    def test_with_kwargs(self):
        key1 = _cache_key("python", "github", limit=5)
        key2 = _cache_key("python", "github", limit=10)
        assert key1 != key2

    def test_empty_kwargs_ignored(self):
        key1 = _cache_key("python", "github", sort="")
        key2 = _cache_key("python", "github")
        assert key1 == key2


# ── 模块导出测试 ─────────────────────────────────────────────


class TestModuleExports:
    """模块导出函数测试"""

    def test_get_tools(self):
        tools = get_tools()
        assert len(tools) == 1
        assert tools[0].name == "platform_search"

    def test_get_all_tools(self):
        tools = get_all_tools()
        assert len(tools) == 1
        assert tools[0].name == "platform_search"

    def test_tool_schema(self):
        tools = get_tools()
        schema = tools[0].to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "platform_search"
        params = schema["function"]["parameters"]["properties"]
        assert "query" in params
        assert "platform" in params
        assert "limit" in params
        assert "sort" in params
        assert "search_type" in params
        assert "timeout" in params


# ── 各平台搜索函数测试 (mock) ────────────────────────────────


class TestPlatformSearchGitHub:
    """GitHub 搜索测试"""

    @pytest.mark.asyncio
    async def test_repositories(self, mock_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "items": [
                {
                    "full_name": "user/repo",
                    "description": "A test repo",
                    "stargazers_count": 100,
                    "language": "Python",
                    "html_url": "https://github.com/user/repo",
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(query="test", platform="github", config=mock_config)

        assert "GitHub" in result
        assert "user/repo" in result

    @pytest.mark.asyncio
    async def test_with_token(self, mock_config_with_proxy):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}
        mock_response.raise_for_status = MagicMock()

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_get = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=mock_get)
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            await _search(query="test", platform="github", config=mock_config_with_proxy)

            # 验证 token 被传递
            call_kwargs = mock_get.call_args
            headers = call_kwargs.kwargs.get("headers", {})
            assert "Authorization" in headers
            assert "test-token" in headers["Authorization"]


class TestPlatformSearchArxiv:
    """arXiv 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <id>http://arxiv.org/abs/2301.00001v1</id>
                <title>Test Paper</title>
                <author><name>John Doe</name></author>
                <summary>A test paper summary</summary>
                <published>2023-01-01T00:00:00Z</published>
            </entry>
        </feed>"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = xml_response
        mock_response.raise_for_status = MagicMock()

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(query="test", platform="arxiv", config=mock_config)

        assert "arXiv" in result
        assert "Test Paper" in result
        assert "John Doe" in result


class TestPlatformSearchStackOverflow:
    """Stack Overflow 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "items": [
                {
                    "title": "How to test?",
                    "link": "https://stackoverflow.com/q/123",
                    "answer_count": 5,
                    "view_count": 100,
                    "tags": ["python", "testing"],
                    "is_answered": True,
                    "score": 10,
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(query="test", platform="stackoverflow", config=mock_config)

        assert "Stack Overflow" in result
        assert "How to test?" in result
        assert "已解决" in result


class TestPlatformSearchHackerNews:
    """Hacker News 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "title": "Show HN: Test Project",
                    "url": "https://example.com",
                    "author": "testuser",
                    "points": 50,
                    "num_comments": 20,
                    "created_at": "2023-01-01T00:00:00Z",
                    "objectID": "12345",
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(query="test", platform="hackernews", config=mock_config)

        assert "Hacker News" in result
        assert "Show HN: Test Project" in result
        assert "testuser" in result


class TestPlatformSearchBilibili:
    """B站搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": {
                "result": [
                    {
                        "title": "测试视频",
                        "author": "UP主",
                        "play": 10000,
                        "video_review": 500,
                        "description": "这是一个测试视频",
                        "bvid": "BV1xx411c7mD",
                    }
                ]
            },
        }
        mock_response.raise_for_status = MagicMock()

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(query="test", platform="bilibili", config=mock_config)

        assert "B站" in result
        assert "测试视频" in result
        assert "UP主" in result


# ── 多平台搜索测试 ───────────────────────────────────────────


class TestMultiPlatformSearch:
    """多平台并发搜索测试"""

    @pytest.mark.asyncio
    async def test_two_platforms(self, mock_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}
        mock_response.raise_for_status = MagicMock()

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(query="test", platform="github,arxiv", config=mock_config)

        # 多平台应有分隔符
        assert "GITHUB" in result
        assert "ARXIV" in result

    @pytest.mark.asyncio
    async def test_all_platforms(self, mock_config):
        """测试 platform=all 搜索全部平台"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": [], "hits": [], "data": {"result": []}, "code": 0}
        mock_response.text = "<html></html>"
        mock_response.raise_for_status = MagicMock()

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(query="test", platform="all", config=mock_config)

        # 全部平台都应有结果(即使是空或错误)
        for platform in _PLATFORM_SEARCHERS:
            assert platform.upper() in result.upper() or platform in result.lower()


# ── 错误处理测试 ─────────────────────────────────────────────


class TestErrorHandling:
    """错误处理测试"""

    @pytest.mark.asyncio
    async def test_unknown_platform(self, mock_config):
        result = await _search(query="test", platform="unknown", config=mock_config)
        assert "TOOL_ERROR" in result
        assert "unknown" in result

    @pytest.mark.asyncio
    async def test_connect_error(self, mock_config):
        import httpx

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(query="test", platform="github", timeout=3, config=mock_config)

        assert "连接超时" in result
        assert "proxy_url" in result

    @pytest.mark.asyncio
    async def test_timeout(self, mock_config):
        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            # 模拟超时: 抛出 asyncio.TimeoutError
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=asyncio.TimeoutError()
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(query="test", platform="github", timeout=1, config=mock_config)

        assert "超时" in result
        assert "proxy_url" in result

    @pytest.mark.asyncio
    async def test_http_error(self, mock_config):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Forbidden",
            request=MagicMock(),
            response=mock_response,
        )

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(
                    get=AsyncMock(return_value=mock_response)
                )
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(query="test", platform="github", config=mock_config)

        assert "403" in result


# ── 缓存测试 ─────────────────────────────────────────────────


class TestCache:
    """缓存行为测试"""

    @pytest.mark.asyncio
    async def test_cache_hit(self, mock_config):
        """相同查询应返回缓存结果"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}
        mock_response.raise_for_status = MagicMock()

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            # 第一次调用
            result1 = await _search(query="cache_test", platform="github", config=mock_config)
            # 第二次调用应命中缓存
            result2 = await _search(query="cache_test", platform="github", config=mock_config)

        assert result1 == result2
        assert len(_search_cache) > 0

    @pytest.mark.asyncio
    async def test_different_query_no_cache(self, mock_config):
        """不同查询不应命中缓存"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}
        mock_response.raise_for_status = MagicMock()

        with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_response))
            )
            mock_client.return_value.__aexit__ = AsyncMock()

            await _search(query="query1", platform="github", config=mock_config)
            await _search(query="query2", platform="github", config=mock_config)

        # 两个不同的查询都应被缓存
        assert len(_search_cache) == 2


# ── 平台路由完整性测试 ───────────────────────────────────────


class TestPlatformRouting:
    """平台路由完整性测试"""

    def test_all_platforms_have_searcher(self):
        """所有声明的平台都有对应的搜索函数"""
        for platform_name, searcher in _PLATFORM_SEARCHERS.items():
            assert callable(searcher), f"{platform_name} 的搜索函数不可调用"

    def test_platform_list_completeness(self):
        """平台列表包含所有预期平台"""
        expected = {"github", "arxiv", "stackoverflow", "hackernews", "bilibili"}
        assert set(_PLATFORM_SEARCHERS.keys()) == expected

    @pytest.mark.asyncio
    async def test_each_platform_can_be_called(self, mock_config):
        """每个平台都可以被调用(不会抛出未处理异常)"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": [], "hits": [], "data": {"result": []}, "code": 0}
        mock_response.text = "<html></html>"
        mock_response.raise_for_status = MagicMock()

        for platform_name in _PLATFORM_SEARCHERS:
            with patch("uniclaw.tools.search.httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(
                    return_value=MagicMock(get=AsyncMock(return_value=mock_response))
                )
                mock_client.return_value.__aexit__ = AsyncMock()
                # 不应抛出异常
                result = await _search(query="test", platform=platform_name, config=mock_config)
                assert isinstance(result, str), f"{platform_name} 未返回字符串"
                assert len(result) > 0, f"{platform_name} 返回空字符串"
