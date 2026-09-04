"""
search 包单元测试

测试 webSearch 工具(并发搜索多个平台)的缓存、超时、错误处理等,
以及各单平台搜索函数的返回格式。
使用 mock 避免真实网络请求。
"""

import asyncio
import re
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.search import (
    webSearch,
    cache_key,
    get_proxy,
    PLATFORM_SEARCHERS,
    get_tools,
    get_all_tools,
    search_cache,
    client_kwargs,
    PLATFORM_ERROR,
)
from uniclaw.tools.search import exa
from uniclaw.utils.constants import TOOL_ERROR

# webSearch 是 Tool 对象, 通过 .func 调用 (默认只搜 exa, 不接受 platform 参数)
_search = webSearch.func


def _mock_client(mock_response):
    """构造一个模拟 httpx.AsyncClient 的 patch 目标。

    适用于通过 base.http_get 发起请求的平台。
    """
    mock_client = MagicMock()
    mock_client.return_value.__aenter__ = AsyncMock(
        return_value=MagicMock(get=AsyncMock(return_value=mock_response))
    )
    mock_client.return_value.__aexit__ = AsyncMock()
    return mock_client


def _patch_base(mock_response):
    """patch base 模块的 httpx.AsyncClient。"""
    return patch(
        "uniclaw.tools.search.base.httpx.AsyncClient", _mock_client(mock_response)
    )


def _patch_bilibili(mock_response):
    """patch bilibili 模块的 httpx.AsyncClient (B站直接使用 httpx)。"""
    return patch(
        "uniclaw.tools.search.bilibili.httpx.AsyncClient", _mock_client(mock_response)
    )


def _make_mcp_tool(name="search", props=None):
    """构造一个模拟的 MCP 工具对象 (list_tools 返回, 仅需 name 字段)。

    props: 可选的 inputSchema.properties 字典, 供 exa time_range 门控测试使用;
    不传时保持 MagicMock 默认行为 (迭代为空 -> 视为无属性)。
    """
    tool = MagicMock()
    tool.name = name
    tool.description = "Exa search"
    if props is not None:
        tool.inputSchema = {"properties": props}
    return tool


@contextmanager
def _patch_mcp(tools=None, call_result="mock exa result", connection_hook=None):
    """patch exa 搜索用到的 MCP 接口 (get_server + _connect_mcp + ClientSession), 避免真实连接。

    新流程为单长连接: 首次 _acquire_session 建立连接后缓存复用, 不再走
    _discover_tools_async / _make_mcp_caller 的两段式连接。

    Args:
        tools: session.list_tools 返回的工具列表 (默认一个名为 search 的工具)
        call_result: session.call_tool 返回的文本内容
        connection_hook: 可选回调, 接收传入 _connect_mcp 的 connection 字典
    """
    if tools is None:
        tools = [_make_mcp_tool("search")]
    server = {
        "name": "exa",
        "transport": "streamable_http",
        "url": "https://mcp.exa.ai/mcp",
        "enabled": True,
    }

    class _Block:
        def __init__(self, text):
            self.text = text

    call_result_obj = MagicMock()
    call_result_obj.content = [_Block(call_result)]

    @asynccontextmanager
    async def _fake_connect(connection):
        if connection_hook is not None:
            connection_hook(connection)
        yield "read", "write"

    def _fake_session(read, write):
        session = MagicMock()
        session.initialize = AsyncMock()
        session.list_tools = AsyncMock(return_value=MagicMock(tools=tools))
        session.call_tool = AsyncMock(return_value=call_result_obj)
        return session

    with (
        patch(
            "uniclaw.tools.mcp.MCPManager.get_server",
            new=AsyncMock(return_value=server),
        ),
        patch("uniclaw.tools.mcp._connect_mcp", new=_fake_connect),
        patch("mcp.ClientSession", new=_fake_session),
    ):
        yield


def _empty_mock():
    """构造空结果的统一 mock (base 平台与 bilibili 共用同一 httpx.AsyncClient)。"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "code": 0,
        "data": {},
        "items": [],
        "hits": [],
        "results": [],
        "markets": [],
    }
    mock_response.text = "<html></html>"
    mock_response.raise_for_status = MagicMock()
    return mock_response


@contextmanager
def _patch_all_platforms(mock):
    """patch 全部平台的网络层, 使 webSearch 搜索所有平台时不发起真实请求。

    注意: base 平台与 bilibili 共用同一个 httpx.AsyncClient (同一 httpx 模块),
    不能分别 patch, 需用单个统一 mock 同时覆盖两者。
    """
    with _patch_base(mock), _patch_mcp():
        yield


@contextmanager
def _patch_all_with_github(github_mock):
    """patch 全部平台为空结果, 仅 github 使用指定 mock (用于重排阈值测试)。"""
    with (
        _patch_base(_empty_mock()),
        _patch_mcp(),
        patch(
            "uniclaw.tools.search.github.http_get",
            new=AsyncMock(return_value=github_mock),
        ),
    ):
        yield


@contextmanager
def _patch_exa_capture(captured, props=None):
    """patch exa MCP 接口并捕获 call_tool 的 arguments 到 captured 字典。

    props: 工具 inputSchema.properties 字典; None 时用 MagicMock 默认行为
    (迭代为空 -> 视为 schema 未声明任何属性)。
    """
    server = {
        "name": "exa",
        "transport": "streamable_http",
        "url": "https://mcp.exa.ai/mcp",
        "enabled": True,
    }

    @asynccontextmanager
    async def _fake_connect(connection):
        yield "read", "write"

    def _fake_session(read, write):
        session = MagicMock()
        session.initialize = AsyncMock()
        session.list_tools = AsyncMock(
            return_value=MagicMock(tools=[_make_mcp_tool("search", props=props)])
        )

        async def _capture_call(name, arguments=None):
            captured["arguments"] = dict(arguments or {})
            return MagicMock(content=[MagicMock(text="result")])

        session.call_tool = _capture_call
        return session

    with (
        patch(
            "uniclaw.tools.mcp.MCPManager.get_server",
            new=AsyncMock(return_value=server),
        ),
        patch("uniclaw.tools.mcp._connect_mcp", new=_fake_connect),
        patch("mcp.ClientSession", new=_fake_session),
    ):
        yield


async def _platform(
    platform_name, query, config, limit=10, sort="", search_type="", time_range=""
):
    """直接调用单个平台的搜索函数 (绕过 webSearch 工具层)。"""
    return await PLATFORM_SEARCHERS[platform_name](
        query, limit, sort, search_type, config, time_range=time_range
    )


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清空搜索缓存和 Exa 长连接缓存。"""
    search_cache.clear()
    exa._reset_exa_cache()
    yield
    search_cache.clear()
    exa._reset_exa_cache()


@pytest.fixture
def mock_config():
    """模拟 AppConfig。"""
    config = MagicMock()
    config.proxy_url = ""
    config.GITHUB_TOKEN = ""
    config.EXA_API_KEY = ""
    return config


@pytest.fixture
def mock_config_with_proxy():
    """带代理的 AppConfig。"""
    config = MagicMock()
    config.proxy_url = "http://127.0.0.1:7890"
    config.GITHUB_TOKEN = "test-token"
    config.EXA_API_KEY = ""
    return config


# ── 辅助函数测试 ─────────────────────────────────────────────


class TestGetProxy:
    """get_proxy 测试"""

    def test_none_config(self):
        assert get_proxy(None) is None

    def test_empty_proxy(self):
        config = MagicMock()
        config.proxy_url = ""
        assert get_proxy(config) is None

    def test_valid_proxy(self):
        config = MagicMock()
        config.proxy_url = "http://proxy:8080"
        assert get_proxy(config) == "http://proxy:8080"

    def test_https_proxy(self):
        config = MagicMock()
        config.proxy_url = "https://proxy:8080"
        assert get_proxy(config) == "https://proxy:8080"

    def test_invalid_proxy(self):
        config = MagicMock()
        config.proxy_url = "socks5://proxy:8080"
        assert get_proxy(config) is None


class TestClientKwargs:
    """client_kwargs 代理策略测试"""

    def test_proxy_used_for_overseas(self):
        """海外平台配置了代理就使用代理"""
        config = MagicMock()
        config.proxy_url = "http://proxy:8080"
        assert client_kwargs(config) == {"proxy": "http://proxy:8080"}
        assert client_kwargs(config, use_proxy=True) == {"proxy": "http://proxy:8080"}

    def test_no_proxy_for_china(self):
        """国内平台 (use_proxy=False) 即使配置了代理也直连"""
        config = MagicMock()
        config.proxy_url = "http://proxy:8080"
        assert client_kwargs(config, use_proxy=False) == {}

    def test_no_proxy_configured(self):
        """未配置代理时不使用代理"""
        config = MagicMock()
        config.proxy_url = ""
        assert client_kwargs(config) == {}

    def test_none_config(self):
        assert client_kwargs(None) == {}


class TestCacheKey:
    """cache_key 测试"""

    def test_basic(self):
        key = cache_key("python", "github")
        assert "python" in key and "github" in key

    def test_with_kwargs(self):
        key1 = cache_key("python", "github", limit=5)
        key2 = cache_key("python", "github", limit=10)
        assert key1 != key2

    def test_empty_kwargs_ignored(self):
        key1 = cache_key("python", "github", sort="")
        key2 = cache_key("python", "github")
        assert key1 == key2


# ── 模块导出测试 ─────────────────────────────────────────────


class TestModuleExports:
    """模块导出函数测试"""

    def test_get_tools(self):
        tools = get_tools()
        assert len(tools) == 1
        assert tools[0].name == "webSearch"

    def test_get_all_tools(self):
        tools = get_all_tools()
        assert len(tools) == 1
        assert tools[0].name == "webSearch"

    def test_tool_schema(self):
        tools = get_tools()
        schema = tools[0].to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "webSearch"
        params = schema["function"]["parameters"]["properties"]
        assert "query" in params
        assert "platform" not in params
        assert "platforms" in params
        assert "limit" in params
        assert "sort" in params
        assert "search_type" in params
        assert "timeout" in params
        assert "intent" in params
        assert "time_range" in params


# ── 原有平台搜索函数测试 (mock) ──────────────────────────────


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

        with _patch_base(mock_response):
            result = await _platform("github", "test", mock_config)

        assert "GitHub" in result
        assert "user/repo" in result

    @pytest.mark.asyncio
    async def test_with_token(self, mock_config_with_proxy):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response) as mock_client:
            await _platform("github", "test", mock_config_with_proxy)

            # 验证 token 被传递到请求头
            client_mock = mock_client.return_value.__aenter__.return_value
            call_kwargs = client_mock.get.call_args
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

        with _patch_base(mock_response):
            result = await _platform("arxiv", "test", mock_config)

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

        with _patch_base(mock_response):
            result = await _platform("stackoverflow", "test", mock_config)

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

        with _patch_base(mock_response):
            result = await _platform("hackernews", "test", mock_config)

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

        with _patch_bilibili(mock_response) as mock_client:
            result = await _platform("bilibili", "test", mock_config)

        assert "B站" in result
        assert "测试视频" in result
        assert "UP主" in result

    @pytest.mark.asyncio
    async def test_china_direct_no_proxy(self):
        """B站是国内平台, 即使配置了代理也应直连 (AsyncClient 不传 proxy)"""
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0, "data": {"result": []}}
        mock_response.raise_for_status = MagicMock()

        config = MagicMock()
        config.proxy_url = "http://proxy:8080"

        with _patch_bilibili(mock_response) as mock_client:
            await _platform("bilibili", "test", config)

        # 验证 AsyncClient 调用时不带 proxy 参数
        call = mock_client.call_args
        assert call is not None
        kwargs = call.kwargs if call.kwargs else (call.args[0] if call.args else {})
        assert "proxy" not in kwargs


# ── 新增平台搜索函数测试  ─────────


class TestPlatformSearchReddit:
    """Reddit 搜索测试 (.rss 端点, Atom 格式)"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        atom_response = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <author><name>/u/tester</name></author>
                <category term="python"/>
                <content type="html">&amp;lt;p&amp;gt;A test body&amp;lt;/p&amp;gt;</content>
                <id>t3_123</id>
                <link href="https://www.reddit.com/r/python/comments/123/test_post/" />
                <published>2023-01-01T00:00:00+00:00</published>
                <title>Test Post</title>
            </entry>
        </feed>"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = atom_response
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("reddit", "test", mock_config)

        assert "Reddit" in result
        assert "Test Post" in result
        assert "r/python" in result

    @pytest.mark.asyncio
    async def test_skips_subreddit_info_card(self, mock_config):
        """t5_ 开头的版块信息卡应被过滤, 只保留 t3_ 帖子"""
        atom_response = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <content type="html">community description</content>
                <id>t5_2qh0y</id>
                <link href="https://www.reddit.com/r/python/" />
                <title>Python</title>
            </entry>
            <entry>
                <category term="python"/>
                <content type="html">body text</content>
                <id>t3_456</id>
                <link href="https://www.reddit.com/r/python/comments/456/real/" />
                <title>Real Post</title>
            </entry>
        </feed>"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = atom_response
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("reddit", "test", mock_config)

        assert "Real Post" in result
        assert "community description" not in result


class TestPlatformSearchGoogleNews:
    """Google News 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
            <item>
                <title>News Headline</title>
                <link>https://example.com/news/1</link>
                <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
                <description>&lt;p&gt;A news summary&lt;/p&gt;</description>
                <source>Example News</source>
            </item>
        </channel></rss>"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = xml_response
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("google_news", "test", mock_config)

        assert "Google News" in result
        assert "News Headline" in result
        assert "Example News" in result


class TestPlatformSearchOpenAlex:
    """OpenAlex 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "OpenAlex Paper",
                    "doi": "https://doi.org/10.1234/abc",
                    "publication_year": 2023,
                    "cited_by_count": 5,
                    "authorships": [{"author": {"display_name": "Bob"}}],
                    "abstract_inverted_index": {"hello": [0], "world": [1]},
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("openalex", "test", mock_config)

        assert "OpenAlex" in result
        assert "OpenAlex Paper" in result
        assert "引用" in result

    @pytest.mark.asyncio
    async def test_429_retry_then_success(self, mock_config):
        """OpenAlex 首次 429 后自动退避重试并成功"""
        import httpx

        err = httpx.HTTPStatusError(
            "Too Many Requests",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "results": [
                {
                    "title": "Retry OpenAlex",
                    "doi": "https://doi.org/10.1234/retry",
                    "publication_year": 2024,
                    "cited_by_count": 3,
                    "authorships": [],
                    "abstract_inverted_index": None,
                }
            ]
        }
        mock_ok.raise_for_status = MagicMock()

        with (
            patch(
                "uniclaw.tools.search.base.http_get",
                new=AsyncMock(side_effect=[err, mock_ok]),
            ),
            patch("uniclaw.tools.search.base.asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            result = await _platform("openalex", "test", mock_config)

        assert "Retry OpenAlex" in result
        assert PLATFORM_ERROR not in result
        assert mock_sleep.await_count == 1


class TestPlatformSearchPolymarket:
    """Polymarket 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        # public-search 端点返回 {events: [...]}, outcomes/outcomePrices
        # 在嵌套 market 内且为 JSON 字符串 (与真实 API 一致)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "events": [
                {
                    "title": "Will test event happen?",
                    "slug": "test-event",
                    "volume": 100000,
                    "liquidity": 50000,
                    "active": True,
                    "closed": False,
                    "description": "An event about testing things",
                    "markets": [
                        {
                            "closed": False,
                            "outcomes": '["Yes", "No"]',
                            "outcomePrices": '["0.65", "0.35"]',
                        }
                    ],
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("polymarket", "test", mock_config)

        assert "Polymarket" in result
        assert "test event" in result.lower()
        assert "成交量" in result
        assert "Yes 65%" in result

    @pytest.mark.asyncio
    async def test_short_query(self, mock_config):
        """短查询 (全部词 ≤2 字符, 如 "AI") 不应把全部结果过滤掉"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "events": [
                {
                    "title": "Will AI change the world?",
                    "slug": "ai-change-world",
                    "volume": 100000,
                    "liquidity": 50000,
                    "active": True,
                    "closed": False,
                    "description": "A market about AI and its future impact",
                    "markets": [
                        {
                            "closed": False,
                            "outcomes": '["Yes", "No"]',
                            "outcomePrices": '["0.65", "0.35"]',
                        }
                    ],
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("polymarket", "AI", mock_config)

        assert "Polymarket" in result
        assert "AI" in result


class TestPlatformSearchSecEdgar:
    """SEC EDGAR 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": {
                "hits": [
                    {
                        "_id": "0001234-24-000001:10-K",
                        "_source": {
                            "display_names": ["Acme Corp"],
                            "form": "10-K",
                            "file_date": "2024-01-15",
                            "file_type": "10-K",
                            "ciks": [1000],
                            "adsh": "0001234-24-000001",
                        },
                    }
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("sec_edgar", "test", mock_config)

        assert "SEC EDGAR" in result
        assert "Acme Corp" in result
        assert "10-K" in result


class TestPlatformSearchHuggingFace:
    """HuggingFace 搜索测试 (papers/models/datasets 三种类型)"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "paper": {
                    "id": "2301.00001",
                    "title": "A Transformer Test Paper",
                    "summary": "A summary about transformers",
                    "upvotes": 15,
                    "authors": [{"name": "Carol"}],
                    "publishedAt": "2023-01-02T00:00:00Z",
                },
                "numComments": 3,
            }
        ]
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("huggingface", "transformer", mock_config)

        assert "HuggingFace" in result
        assert "Transformer" in result
        assert "15" in result

    @pytest.mark.asyncio
    async def test_models_search(self, mock_config):
        """search_type=models 走模型搜索, 输出 id/作者/下载量/点赞/标签"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "id": "google/gemma-2b",
                "author": "google",
                "downloads": 1234567,
                "likes": 890,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "tags": ["transformers", "_private", "text-generation"],
            }
        ]
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform(
                "huggingface", "gemma", mock_config, search_type="models"
            )

        assert "模型搜索结果" in result
        assert "google/gemma-2b" in result
        assert "by google" in result
        assert "1.2M downloads" in result
        assert "890 likes" in result
        assert "text-generation" in result
        assert "transformers" in result
        # 下划线开头的内部标签被过滤
        assert "_private" not in result
        assert "https://huggingface.co/google/gemma-2b" in result

    @pytest.mark.asyncio
    async def test_datasets_search(self, mock_config):
        """search_type=datasets 走数据集搜索, URL 带 /datasets/ 前缀"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "id": "squad",
                "author": "rajpurkar",
                "downloads": 200000,
                "likes": 30,
                "pipeline_tag": "",
                "tags": ["qa"],
            }
        ]
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform(
                "huggingface", "squad", mock_config, search_type="datasets"
            )

        assert "数据集搜索结果" in result
        assert "squad" in result
        assert "200.0K downloads" in result
        assert "https://huggingface.co/datasets/squad" in result
        assert "https://huggingface.co/squad" not in result

    @pytest.mark.asyncio
    async def test_models_no_query_skips_request(self, mock_config):
        """模型搜索空查询直接返回无搜索结果, 不发起网络请求"""
        with patch(
            "uniclaw.tools.search.huggingface.http_get", new=AsyncMock()
        ) as mock_get:
            result = await _platform(
                "huggingface", "   ", mock_config, search_type="models"
            )

        assert result == "HuggingFace: 无搜索结果"
        mock_get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_models_empty_list(self, mock_config):
        """模型搜索 API 返回空列表时返回无搜索结果"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform(
                "huggingface", "gemma", mock_config, search_type="models"
            )
        assert "HuggingFace: 无搜索结果" in result

    @pytest.mark.asyncio
    async def test_datasets_empty_list(self, mock_config):
        """数据集搜索 API 返回空列表时返回无搜索结果"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform(
                "huggingface", "squad", mock_config, search_type="datasets"
            )
        assert "HuggingFace: 无搜索结果" in result

    @pytest.mark.asyncio
    async def test_models_invalid_sort_falls_back(self, mock_config):
        """非法 sort 值回退为 downloads (默认排序), direction=-1 降序"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": "m/model"}]
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response) as mock_client:
            await _platform(
                "huggingface", "gemma", mock_config, sort="bogus", search_type="models"
            )
        params = mock_client.return_value.__aenter__.return_value.get.call_args.kwargs.get(
            "params", {}
        )
        assert params["sort"] == "downloads"
        assert params["direction"] == "-1"

    @pytest.mark.asyncio
    async def test_models_skips_items_without_id(self, mock_config):
        """缺少 id 的条目被跳过, 有 id 的条目正常输出"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"downloads": 1},
            {"id": "valid/model", "downloads": 5},
        ]
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform(
                "huggingface", "gemma", mock_config, search_type="models"
            )

        assert "valid/model" in result
        assert "📥 5 downloads" in result
        assert "1 downloads" not in result

    @pytest.mark.asyncio
    async def test_papers_filtered_by_query(self, mock_config):
        """papers 搜索按标题+摘要过滤, 不匹配的论文被剔除"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "paper": {
                    "id": "2401.00001",
                    "title": "Transformers for Vision",
                    "summary": "a study about transformers",
                    "upvotes": 5,
                    "authors": [{"name": "Alice"}],
                    "publishedAt": "2024-01-01T00:00:00Z",
                },
                "numComments": 2,
            },
            {
                "paper": {
                    "id": "2401.00002",
                    "title": "Diffusion Models",
                    "summary": "unrelated topic",
                    "upvotes": 0,
                    "authors": [],
                    "publishedAt": "2024-01-01T00:00:00Z",
                },
                "numComments": 0,
            },
        ]
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("huggingface", "transformer", mock_config)

        assert "Transformers for Vision" in result
        assert "Diffusion Models" not in result
        assert "Alice" in result
        assert "huggingface.co/papers/2401.00001" in result

    @pytest.mark.asyncio
    async def test_papers_author_ellipsis(self, mock_config):
        """超过 3 位作者时显示前 3 位 + '+N more'"""
        authors = [{"name": f"Author{i}"} for i in range(5)]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "paper": {
                    "id": "2401.00003",
                    "title": "Multi Author Paper",
                    "summary": "summary about authors",
                    "upvotes": 1,
                    "authors": authors,
                    "publishedAt": "2024-01-01T00:00:00Z",
                },
                "numComments": 0,
            }
        ]
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("huggingface", "author", mock_config)

        assert "Author0, Author1, Author2" in result
        assert "+2 more" in result

    @pytest.mark.asyncio
    async def test_papers_no_matching_query(self, mock_config):
        """papers 搜索无匹配时返回无搜索结果"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"paper": {"id": "2401.00004", "title": "Unrelated", "summary": "x"}}
        ]
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("huggingface", "zzzz", mock_config)

        assert "HuggingFace: 无搜索结果" in result

    @pytest.mark.asyncio
    async def test_papers_limit_applied(self, mock_config):
        """papers 搜索结果受 limit 限制"""
        def paper(i):
            return {
                "paper": {
                    "id": f"2401.{i:05d}",
                    "title": f"Matching Paper {i}",
                    "summary": "transformer content",
                    "upvotes": 0,
                    "authors": [],
                    "publishedAt": "2024-01-01T00:00:00Z",
                },
                "numComments": 0,
            }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [paper(i) for i in range(1, 6)]
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform(
                "huggingface", "transformer", mock_config, limit=3
            )

        assert "(3 篇)" in result
        assert "Matching Paper 1" in result
        assert "Matching Paper 5" not in result

    @pytest.mark.asyncio
    async def test_papers_uses_hf_token(self, mock_config_with_proxy):
        """配置 hf_token 时 papers 请求携带 Authorization 头"""
        mock_config_with_proxy.hf_token = "hf-secret"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response) as mock_client:
            await _platform("huggingface", "transformer", mock_config_with_proxy)

        client_mock = mock_client.return_value.__aenter__.return_value
        headers = client_mock.get.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer hf-secret"

    @pytest.mark.asyncio
    async def test_papers_no_token_no_auth_header(self, mock_config):
        """未配置 token 时 papers 请求不携带 Authorization 头"""
        mock_config.hf_token = ""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with (
            _patch_base(mock_response) as mock_client,
            patch.dict("os.environ", {"HF_TOKEN": "", "HUGGINGFACE_TOKEN": ""}),
        ):
            await _platform("huggingface", "transformer", mock_config)

        client_mock = mock_client.return_value.__aenter__.return_value
        headers = client_mock.get.call_args.kwargs.get("headers", {})
        assert "Authorization" not in headers


class TestPlatformSearchAlphaXiv:
    """alphaXiv 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <id>http://arxiv.org/abs/2301.00001v1</id>
                <title>Discussion Paper</title>
                <author><name>John Doe</name></author>
                <summary>Summary</summary>
                <published>2023-01-01T00:00:00Z</published>
            </entry>
        </feed>"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = xml_response
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("alphaxiv", "test", mock_config)

        assert "alphaXiv" in result
        assert "Discussion Paper" in result
        assert "alphaxiv.org" in result


class TestPlatformSearchBing:
    """Bing 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        # 标题/摘要需包含查询词 "test", 否则会被相关性粗筛过滤掉
        html = """
        <li class="b_algo">
            <h2><a href="https://example.com/page1">Bing Result 1</a></h2>
            <p>This is a test snippet for result 1.</p>
        </li>
        <li class="b_algo">
            <h2><a href="https://example.com/page2">Bing Result 2</a></h2>
            <p>This is a test snippet for result 2.</p>
        </li>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("bing", "test", mock_config)

        assert "Bing" in result
        assert "Bing Result 1" in result
        assert "Bing Result 2" in result
        assert "example.com" in result

    @pytest.mark.asyncio
    async def test_china_direct_no_proxy(self):
        """Bing 是国内直连平台, 即使配置了代理也应直连"""
        html = """<li class="b_algo"><h2><a href="https://example.com">Test</a></h2><p>Snippet</p></li>"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        config = MagicMock()
        config.proxy_url = "http://proxy:8080"

        with _patch_base(mock_response) as mock_client:
            await _platform("bing", "test", config)

        # 验证 AsyncClient 调用时不带 proxy 参数
        call = mock_client.call_args
        assert call is not None
        kwargs = call.kwargs if call.kwargs else (call.args[0] if call.args else {})
        assert "proxy" not in kwargs

    @pytest.mark.asyncio
    async def test_no_results(self, mock_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>No results</body></html>"
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("bing", "test", mock_config)

        assert "Bing: 无搜索结果" in result


class TestPlatformSearchDuckDuckGo:
    """DuckDuckGo 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        html = """
        <div class="result">
            <div class="result__title">
                <a href="https://example.com/page1" class="result__a">DDG Result 1</a>
            </div>
            <div class="result__snippet">Snippet for result 1.</div>
        </div>
        <div class="result">
            <div class="result__title">
                <a href="https://example.com/page2" class="result__a">DDG Result 2</a>
            </div>
            <div class="result__snippet">Snippet for result 2.</div>
        </div>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("duckduckgo", "test", mock_config)

        assert "DuckDuckGo" in result
        assert "DDG Result 1" in result
        assert "DDG Result 2" in result
        assert "example.com" in result

    @pytest.mark.asyncio
    async def test_no_results(self, mock_config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>No results</body></html>"
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform("duckduckgo", "test", mock_config)

        assert "DuckDuckGo: 无搜索结果" in result


class TestPlatformSearchExa:
    """Exa MCP 搜索测试"""

    @pytest.mark.asyncio
    async def test_basic(self, mock_config):
        """正常搜索: 通过 MCP 发现工具并调用"""
        with _patch_mcp(call_result='[{"title": "Exa Result"}]'):
            result = await _platform("exa", "test", mock_config)

        assert "Exa" in result
        assert "Exa Result" in result

    @pytest.mark.asyncio
    async def test_connection_reused_across_calls(self, mock_config):
        """长连接复用: 同一事件循环内多次搜索只建立一次 MCP 连接"""
        connects = {"count": 0}
        inits = {"count": 0}

        @asynccontextmanager
        async def _counting_connect(connection):
            connects["count"] += 1
            yield "read", "write"

        def _counting_session(read, write):
            session = MagicMock()

            async def _init():
                inits["count"] += 1

            session.initialize = _init
            session.list_tools = AsyncMock(
                return_value=MagicMock(tools=[_make_mcp_tool("search")])
            )
            session.call_tool = AsyncMock(
                return_value=MagicMock(content=[MagicMock(text="result")])
            )
            return session

        # 连续两次搜索, 共享同一个全局 _exa_cache, 不应重复建连
        with (
            patch("uniclaw.tools.mcp._connect_mcp", new=_counting_connect),
            patch("mcp.ClientSession", new=_counting_session),
        ):
            await _platform("exa", "test", mock_config)
            await _platform("exa", "test", mock_config)

        assert connects["count"] == 1
        assert inits["count"] == 1

    @pytest.mark.asyncio
    async def test_connection_not_reused_across_loops(self, mock_config):
        """跨事件循环不复用缓存: 缓存的 session 绑定旧 loop, 换 loop 必须重建"""
        connects = {"count": 0}

        @asynccontextmanager
        async def _counting_connect(connection):
            connects["count"] += 1
            yield "read", "write"

        def _counting_session(read, write):
            session = MagicMock()
            session.initialize = AsyncMock()
            session.list_tools = AsyncMock(
                return_value=MagicMock(tools=[_make_mcp_tool("search")])
            )
            session.call_tool = AsyncMock(
                return_value=MagicMock(content=[MagicMock(text="result")])
            )
            return session

        async def _run_once():
            with (
                patch("uniclaw.tools.mcp._connect_mcp", new=_counting_connect),
                patch("mcp.ClientSession", new=_counting_session),
            ):
                await _platform("exa", "test", mock_config)

        # 首次搜索: 建连, 缓存绑定当前事件循环
        await _run_once()
        assert connects["count"] == 1

        # 模拟换了个事件循环: 缓存中 session 绑定的 loop 引用与当前不同,
        # _acquire_session 应丢弃缓存重建连接 (而非在新 loop 上 await 旧 IO 挂起)
        exa._exa_cache["loop"] = object()
        await _run_once()
        assert connects["count"] == 2

    @pytest.mark.asyncio
    async def test_server_unavailable(self, mock_config):
        """MCP 服务器不可用时返回平台错误信息"""
        with _patch_mcp(tools=[]):
            result = await _platform("exa", "test", mock_config)

        assert PLATFORM_ERROR in result

    @pytest.mark.asyncio
    async def test_tool_call_fails(self, mock_config):
        """工具调用失败 (返回 TOOL_ERROR) 时转为平台错误"""
        with _patch_mcp(call_result=f"{TOOL_ERROR}: boom"):
            result = await _platform("exa", "test", mock_config)

        assert PLATFORM_ERROR in result
        assert "boom" in result

    @pytest.mark.asyncio
    async def test_proxy_not_used_even_when_set(self):
        """Exa 可直连, 即使配置了 proxy_url 也不注入 MCP 连接"""
        config = MagicMock()
        config.proxy_url = "http://127.0.0.1:7890"
        config.EXA_API_KEY = ""

        captured = {}

        def _hook(connection):
            captured["connection"] = connection

        with _patch_mcp(connection_hook=_hook):
            result = await _platform("exa", "test", config)

        assert "Exa" in result
        assert "proxy" not in captured["connection"]

    @pytest.mark.asyncio
    async def test_no_proxy_when_unset(self, mock_config):
        """未配置 proxy_url 时不注入 proxy 字段"""
        captured = {}

        def _hook(connection):
            captured["connection"] = connection

        with _patch_mcp(connection_hook=_hook):
            result = await _platform("exa", "test", mock_config)

        assert "Exa" in result
        assert "proxy" not in captured["connection"]

    @pytest.mark.asyncio
    async def test_api_key_appended_to_url(self):
        """配置了 EXA_API_KEY 且 URL 未带 key 时, 应由 MCPManager 统一拼接"""
        config = MagicMock()
        config.proxy_url = ""
        config.EXA_API_KEY = "exa-secret-key"

        captured = {}

        def _hook(connection):
            captured["connection"] = connection

        # _ensure_exa_api_key 直接操作 mcp 配置字典, 与 get_server 的 mock 无关,
        # 单独验证其幂等补齐逻辑即可 (exa.py 已不再负责拼接)。
        from uniclaw.tools.mcp import _ensure_exa_api_key

        mcp_config = {
            "servers": {
                "exa": {
                    "transport": "streamable_http",
                    "url": "https://mcp.exa.ai/mcp",
                    "enabled": True,
                }
            }
        }
        _ensure_exa_api_key(mcp_config, config)
        assert "exaApiKey=exa-secret-key" in mcp_config["servers"]["exa"]["url"]

    @pytest.mark.asyncio
    async def test_api_key_not_duplicated(self):
        """URL 已带 exaApiKey 时 _ensure_exa_api_key 不应重复拼接"""
        config = MagicMock()
        config.proxy_url = ""
        config.EXA_API_KEY = "exa-secret-key"

        from uniclaw.tools.mcp import _ensure_exa_api_key

        mcp_config = {
            "servers": {
                "exa": {
                    "transport": "streamable_http",
                    "url": "https://mcp.exa.ai/mcp?exaApiKey=existing-key",
                    "enabled": True,
                }
            }
        }
        _ensure_exa_api_key(mcp_config, config)
        url = mcp_config["servers"]["exa"]["url"]
        assert "exaApiKey=existing-key" in url
        assert url.count("exaApiKey=") == 1


class TestExaRestFastPath:
    """配置 EXA_API_KEY 时优先走 REST 直连, 失败静默回退 MCP。"""

    @staticmethod
    def _rest_response(results):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"results": results}
        return resp

    @pytest.mark.asyncio
    async def test_rest_used_when_api_key_set(self, mock_config):
        """有 key 时单次 POST api.exa.ai, 不触碰 MCP"""
        mock_config.EXA_API_KEY = "exa-k"
        rest_resp = self._rest_response(
            [{"title": "Rest Hit", "url": "https://example.com/a", "text": "snippet"}]
        )

        captured = {}
        with (
            patch("uniclaw.tools.search.exa.httpx.AsyncClient") as mock_cls,
            _patch_exa_capture(captured),
        ):
            client = AsyncMock()
            client.post = AsyncMock(return_value=rest_resp)
            mock_cls.return_value.__aenter__.return_value = client
            result = await _platform("exa", "rest-query", mock_config)

        assert "Rest Hit" in result
        assert "https://example.com/a" in result
        payload = client.post.await_args.kwargs["json"]
        assert payload["query"] == "rest-query"
        assert payload["numResults"] == 10
        assert payload["type"] == "auto"
        # 无时间范围时不带日期参数
        assert "startPublishedDate" not in payload
        assert "endPublishedDate" not in payload
        # 未发起任何 MCP 调用
        assert "arguments" not in captured

    @pytest.mark.asyncio
    async def test_rest_forwards_time_range(self, mock_config):
        """time_range 映射为 RFC3339 日期参数, 预设区间无上界"""
        mock_config.EXA_API_KEY = "exa-k"
        rest_resp = self._rest_response([])

        with patch("uniclaw.tools.search.exa.httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=rest_resp)
            mock_cls.return_value.__aenter__.return_value = client
            await _platform("exa", "q", mock_config, time_range="7d")

        payload = client.post.await_args.kwargs["json"]
        assert payload["startPublishedDate"].endswith("T00:00:00Z")
        assert "endPublishedDate" not in payload

    @pytest.mark.asyncio
    async def test_rest_failure_falls_back_to_mcp(self, mock_config):
        """REST 抛错时静默回退 MCP 路径"""
        import httpx

        mock_config.EXA_API_KEY = "exa-k"

        with (
            patch("uniclaw.tools.search.exa.httpx.AsyncClient") as mock_cls,
            _patch_mcp(call_result="**Exa 搜索结果** (1 条):\nmcp fallback"),
        ):
            client = AsyncMock()
            client.post = AsyncMock(side_effect=httpx.ConnectError("REST 不可达"))
            mock_cls.return_value.__aenter__.return_value = client
            result = await _platform("exa", "q", mock_config)

        assert "mcp fallback" in result

    @pytest.mark.asyncio
    async def test_no_api_key_goes_straight_to_mcp(self, mock_config):
        """无 key 时不发 REST 请求, 直接走 MCP"""
        with (
            patch("uniclaw.tools.search.exa.httpx.AsyncClient") as mock_cls,
            _patch_mcp(),
        ):
            result = await _platform("exa", "q", mock_config)

        mock_cls.assert_not_called()
        assert "mock exa result" in result


# ── time_range 时间范围过滤测试 ──────────────────────────────


class TestTimeRangeInjection:
    """各平台 time_range -> 原生过滤参数的注入测试"""

    def _params(self, mock_client):
        """提取 patched AsyncClient 上 get 调用的 params。"""
        client_mock = mock_client.return_value.__aenter__.return_value
        return client_mock.get.call_args.kwargs.get("params", {})

    @pytest.mark.asyncio
    async def test_github_repositories_pushed(self, mock_config):
        with _patch_base(_empty_mock()) as mock_client:
            await _platform(
                "github",
                "llm",
                mock_config,
                search_type="repositories",
                time_range="2024-01-01..2024-06-30",
            )
        q = self._params(mock_client)["q"]
        assert "pushed:>=2024-01-01" in q
        assert "pushed:<=2024-06-30" in q
        assert q.startswith("llm")

    @pytest.mark.asyncio
    async def test_github_issues_updated(self, mock_config):
        with _patch_base(_empty_mock()) as mock_client:
            await _platform(
                "github",
                "bug",
                mock_config,
                search_type="issues",
                time_range="7d",
            )
        q = self._params(mock_client)["q"]
        # github 对 issues 类型注入 updated:>=DATE 过滤 (预设区间无上界)
        from uniclaw.tools.search.time_range import parse_time_range as ptr

        expected = "updated:>=" + ptr("7d").start.strftime("%Y-%m-%d")
        assert expected in q
        assert "pushed:" not in q

    @pytest.mark.asyncio
    async def test_github_code_type_ignores_range(self, mock_config):
        with _patch_base(_empty_mock()) as mock_client:
            await _platform(
                "github",
                "llm",
                mock_config,
                search_type="code",
                time_range="7d",
            )
        assert "pushed:" not in self._params(mock_client)["q"]
        assert "updated:" not in self._params(mock_client)["q"]

    @pytest.mark.asyncio
    async def test_arxiv_submitted_date(self, mock_config):
        with _patch_base(_empty_mock()) as mock_client:
            await _platform("arxiv", "transformer", mock_config, time_range="7d")
        params = self._params(mock_client)
        sq = params["search_query"]
        m = re.search(r"AND submittedDate:\[(\d{12}) TO (\d{12})\]", sq)
        assert m, sq
        # 下限时间戳应早于上限
        assert int(m.group(1)) < int(m.group(2))
        # arXiv API 的日期过滤仅在按日期排序时生效, 必须强制切换 sortBy
        assert params["sortBy"] == "submittedDate"

    @pytest.mark.asyncio
    async def test_arxiv_no_range_keeps_relevance_sort(self, mock_config):
        """未传 time_range 时保持默认 relevance 排序"""
        with _patch_base(_empty_mock()) as mock_client:
            await _platform("arxiv", "transformer", mock_config)
        params = self._params(mock_client)
        assert "submittedDate" not in params["search_query"]
        assert params["sortBy"] == "relevance"

    @pytest.mark.asyncio
    async def test_arxiv_end_only_uses_epoch_placeholder(self, mock_config):
        """仅指定上界时, 下限用 arXiv 创立日占位"""
        with _patch_base(_empty_mock()) as mock_client:
            await _platform("arxiv", "test", mock_config, time_range="..2020-01-01")
        sq = self._params(mock_client)["search_query"]
        assert "submittedDate:[199108140000 TO " in sq

    @pytest.mark.asyncio
    async def test_stackoverflow_epoch_bounds(self, mock_config):
        from uniclaw.tools.search.time_range import epoch as to_epoch

        with _patch_base(_empty_mock()) as mock_client:
            await _platform(
                "stackoverflow",
                "asyncio",
                mock_config,
                time_range="2024-01-01..2024-06-30",
            )
        params = self._params(mock_client)
        assert params["fromdate"] == to_epoch(datetime(2024, 1, 1, tzinfo=timezone.utc))
        assert params["todate"] == to_epoch(
            datetime(2024, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
        )

    @pytest.mark.asyncio
    async def test_hackernews_numeric_filters(self, mock_config):
        with _patch_base(_empty_mock()) as mock_client:
            await _platform("hackernews", "rust", mock_config, time_range="30d")
        nf = self._params(mock_client)["numericFilters"]
        assert re.match(r"^created_at_i>\d+$", nf)
        # 预设区间无上界, 只有下界过滤
        assert "created_at_i<" not in nf

    @pytest.mark.asyncio
    async def test_reddit_maps_to_week(self, mock_config):
        with _patch_base(_empty_mock()) as mock_client:
            await _platform("reddit", "python", mock_config, time_range="7d")
        assert self._params(mock_client)["t"] == "week"

    @pytest.mark.asyncio
    async def test_reddit_default_month_kept(self, mock_config):
        """不传 time_range 时维持历史默认 t=month"""
        with _patch_base(_empty_mock()) as mock_client:
            await _platform("reddit", "python", mock_config)
        assert self._params(mock_client)["t"] == "month"

    @pytest.mark.asyncio
    async def test_google_news_after_before(self, mock_config):
        with _patch_base(_empty_mock()) as mock_client:
            await _platform(
                "google_news",
                "nvidia",
                mock_config,
                time_range="2024-01-01..2024-06-30",
            )
        q = self._params(mock_client)["q"]
        assert " after:2024-01-01" in q
        assert " before:2024-06-30" in q

    @pytest.mark.asyncio
    async def test_openalex_filter_coexists_with_mailto(self, mock_config):
        """filter (时间) 与 mailto (礼节邮箱) 是不同 key, 互不覆盖"""
        with _patch_base(_empty_mock()) as mock_client:
            await _platform("openalex", "protein", mock_config, time_range="90d")
        params = self._params(mock_client)
        assert "from_publication_date:" in params["filter"]
        assert "mailto" in params

    @pytest.mark.asyncio
    async def test_sec_edgar_default_range_kept(self, mock_config):
        """未传 time_range 时保留 EDGAR 的历史默认区间"""
        with _patch_base(_empty_mock()) as mock_client:
            await _platform("sec_edgar", "merger", mock_config)
        params = self._params(mock_client)
        assert params["startdt"] == "2024-01-01"
        assert params["enddt"] == "2030-12-31"

    @pytest.mark.asyncio
    async def test_sec_edgar_overridden(self, mock_config):
        with _patch_base(_empty_mock()) as mock_client:
            await _platform(
                "sec_edgar",
                "merger",
                mock_config,
                time_range="2024-03-01..2024-06-30",
            )
        params = self._params(mock_client)
        assert params["startdt"] == "2024-03-01"
        assert params["enddt"] == "2024-06-30"

    @pytest.mark.asyncio
    async def test_exa_passes_start_published_date(self, mock_config):
        """schema 声明支持日期参数时, call_tool 携带 startPublishedDate"""
        captured = {}
        props = {
            "query": {},
            "numResults": {},
            "startPublishedDate": {},
            "endPublishedDate": {},
        }
        with _patch_exa_capture(captured, props=props):
            result = await _platform("exa", "ai agents", mock_config, time_range="7d")
        assert PLATFORM_ERROR not in result
        args = captured["arguments"]
        assert args["startPublishedDate"].endswith("T00:00:00Z")
        # 预设区间无上界, 不传 endPublishedDate
        assert "endPublishedDate" not in args

    @pytest.mark.asyncio
    async def test_exa_schema_without_dates_degrades_silently(self, mock_config):
        """远端工具 schema 未声明日期参数时不传日期, 平台静默降级不失败"""
        captured = {}
        props = {"query": {}, "numResults": {}}
        with _patch_exa_capture(captured, props=props):
            result = await _platform("exa", "ai agents", mock_config, time_range="7d")
        assert "startPublishedDate" not in captured["arguments"]
        assert PLATFORM_ERROR not in result

    @pytest.mark.asyncio
    async def test_alphaxiv_forwards_to_arxiv(self, mock_config):
        """alphaxiv 把 time_range 关键字转发给 arXiv"""
        with patch(
            "uniclaw.tools.search.arxiv.search",
            new=AsyncMock(return_value="arXiv: 无搜索结果"),
        ) as mock_arxiv:
            await _platform("alphaxiv", "test", mock_config, time_range="7d")
        assert mock_arxiv.await_count == 1
        assert mock_arxiv.await_args.kwargs.get("time_range") == "7d"

    @pytest.mark.asyncio
    async def test_huggingface_filters_old_papers(self, mock_config):
        """HuggingFace 客户端日期过滤: 超出范围的旧论文被剔除"""

        def paper(pid, title, published_at):
            return {
                "paper": {
                    "id": pid,
                    "title": title,
                    "summary": f"a study about transformers {pid}",
                    "upvotes": 1,
                    "authors": [{"name": "A"}],
                    "publishedAt": published_at,
                },
                "numComments": 0,
            }

        fresh = (datetime.now(timezone.utc) - timedelta(days=3)).strftime(
            "%Y-%m-%dT00:00:00Z"
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            paper("2501.00001", "Fresh Transformer Paper", fresh),
            paper("2001.00002", "Old Transformer Paper", "2020-01-01T00:00:00Z"),
        ]
        mock_response.raise_for_status = MagicMock()

        with _patch_base(mock_response):
            result = await _platform(
                "huggingface", "transformer", mock_config, time_range="1y"
            )

        assert "Fresh Transformer Paper" in result
        assert "Old Transformer Paper" not in result


class TestTimeRangeMulti:
    """webSearch 层的 time_range 行为 (校验/缓存/提示/回归网)"""

    @pytest.mark.asyncio
    async def test_invalid_time_range_fails_fast(self, mock_config):
        """非法 time_range 在入口快速失败, 不发起任何请求"""
        with pytest.raises(ValueError), _patch_all_platforms(_empty_mock()):
            await _search(
                query="x",
                platforms="github",
                tool_runtime=ToolRuntime(config=mock_config),
                time_range="not-a-range",
            )

    @pytest.mark.asyncio
    async def test_cache_key_distinguishes_time_range(self, mock_config):
        """同 query 不同 time_range 是独立缓存条目, 不串缓存"""
        with _patch_all_platforms(_empty_mock()):
            await _search(query="dup", platforms="github", tool_runtime=ToolRuntime(config=mock_config))
            await _search(
                query="dup", platforms="github", tool_runtime=ToolRuntime(config=mock_config), time_range="7d"
            )
        assert len(search_cache) == 2

    @pytest.mark.asyncio
    async def test_unsupported_platform_hint_once(self, mock_config):
        """不支持平台 (duckduckgo) 出现恰一次提示, 支持平台 (github) 不受影响"""
        with _patch_all_platforms(_empty_mock()):
            result = await _search(
                query="hint",
                platforms="duckduckgo,github",
                tool_runtime=ToolRuntime(config=mock_config),
                time_range="7d",
            )
        assert result.count("[提示]") == 1
        assert "[提示] duckduckgo 平台不支持时间范围过滤" in result

    @pytest.mark.asyncio
    async def test_no_hint_when_time_range_empty(self, mock_config):
        """默认调用 (无 time_range) 全文不含提示行"""
        with _patch_all_platforms(_empty_mock()):
            result = await _search(
                query="clean",
                platforms="bing,duckduckgo",
                tool_runtime=ToolRuntime(config=mock_config),
            )
        assert "[提示]" not in result

    @pytest.mark.asyncio
    async def test_each_platform_accepts_time_range(self, mock_config):
        """回归网: 全平台以 time_range 直调均不得报错。

        safe_search 会把漏改签名导致的 TypeError 吞成平台错误文本,
        只有全平台直调才能抓住这类回归。
        """
        for platform_name, searcher in PLATFORM_SEARCHERS.items():
            with _patch_all_platforms(_empty_mock()):
                result = await searcher(
                    "test", 10, "", "", mock_config, time_range="7d"
                )
            assert isinstance(result, str), f"{platform_name} 未返回字符串"
            assert len(result) > 0, f"{platform_name} 返回空字符串"
            assert not result.startswith(
                PLATFORM_ERROR
            ), f"{platform_name} 不接受 time_range 参数: {result}"


# ── 多平台搜索测试 ───────────────────────────────────────────


class TestMultiPlatformSearch:
    """webSearch 多平台并发搜索测试"""

    @pytest.mark.asyncio
    async def test_default_platforms_is_exa(self, mock_config):
        """默认只搜 Exa (通用网页搜索), 不发起其他平台的请求"""
        with _patch_all_platforms(_empty_mock()):
            result = await _search(query="test", tool_runtime=ToolRuntime(config=mock_config))

        assert len(search_cache) == 1
        assert "EXA" in result.upper()
        assert "GITHUB" not in result.upper()
        assert "ARXIV" not in result.upper()

    @pytest.mark.asyncio
    async def test_searches_all_platforms(self, mock_config):
        """platforms='all' 搜索全部平台, 每个平台都有结果(即使为空)"""
        with _patch_all_platforms(_empty_mock()):
            result = await _search(query="test", platforms="all", tool_runtime=ToolRuntime(config=mock_config))

        # 全部平台都应出现在结果中(多平台分隔线含平台名)
        for platform in PLATFORM_SEARCHERS:
            assert platform.upper() in result.upper(), f"缺少平台 {platform}"

    @pytest.mark.asyncio
    async def test_platforms_filter_selected_only(self, mock_config):
        """platforms 参数只搜索指定平台, 不包含其他平台"""
        with _patch_all_platforms(_empty_mock()):
            result = await _search(
                query="test", platforms="github,arxiv", tool_runtime=ToolRuntime(config=mock_config)
            )

        # 只应出现 github 和 arxiv (分隔线含平台名)
        assert "GITHUB" in result.upper()
        assert "ARXIV" in result.upper()
        # 不应出现其他平台
        for platform in PLATFORM_SEARCHERS:
            if platform not in ("github", "arxiv"):
                assert (
                    platform.upper() not in result.upper()
                ), f"不应包含平台 {platform}"

    @pytest.mark.asyncio
    async def test_platforms_filter_single(self, mock_config):
        """platforms 传单个平台只搜索该平台"""
        with _patch_all_platforms(_empty_mock()):
            result = await _search(query="test", platforms="github", tool_runtime=ToolRuntime(config=mock_config))

        assert "GITHUB" in result.upper()
        assert "ARXIV" not in result.upper()

    @pytest.mark.asyncio
    async def test_platforms_filter_case_and_space_insensitive(self, mock_config):
        """platforms 参数大小写不敏感, 容忍空格"""
        with _patch_all_platforms(_empty_mock()):
            result = await _search(
                query="test", platforms=" GitHub , BING ", tool_runtime=ToolRuntime(config=mock_config)
            )

        assert "GITHUB" in result.upper()
        assert "BING" in result.upper()
        assert "ARXIV" not in result.upper()

    @pytest.mark.asyncio
    async def test_platforms_all_covers_everything(self, mock_config):
        """platforms='all' 覆盖全部平台 (与只搜单个平台的默认行为不同)"""
        with _patch_all_platforms(_empty_mock()):
            result = await _search(query="test", platforms="all", tool_runtime=ToolRuntime(config=mock_config))
        assert len(search_cache) == len(PLATFORM_SEARCHERS)

    @pytest.mark.asyncio
    async def test_platforms_invalid_raises(self, mock_config):
        """platforms 全为无效平台名时抛出 ValueError"""
        with pytest.raises(ValueError):
            await _search(
                query="test", platforms="notexist1,notexist2", tool_runtime=ToolRuntime(config=mock_config)
            )

    @pytest.mark.asyncio
    async def test_platforms_partial_invalid_ignored(self, mock_config):
        """platforms 部分无效时忽略无效名, 只搜有效平台"""
        with _patch_all_platforms(_empty_mock()):
            result = await _search(
                query="test", platforms="github,notexist", tool_runtime=ToolRuntime(config=mock_config)
            )

        assert "GITHUB" in result.upper()
        assert "ARXIV" not in result.upper()


# ── 错误处理测试 ─────────────────────────────────────────────


class TestErrorHandling:
    """错误处理测试"""

    @pytest.mark.asyncio
    async def test_connect_error(self, mock_config):
        import httpx

        with (
            patch("uniclaw.tools.search.base.httpx.AsyncClient") as mock_client,
            _patch_mcp(),
        ):
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(
                query="test", platforms="github", timeout=3, tool_runtime=ToolRuntime(config=mock_config)
            )

        # safe_search 捕获连接错误, 返回平台失败信息(非 TOOL_ERROR)而非抛异常
        assert PLATFORM_ERROR in result
        assert "连接失败" in result
        assert "proxy_url" in result

    @pytest.mark.asyncio
    async def test_timeout(self, mock_config):
        with (
            patch("uniclaw.tools.search.base.httpx.AsyncClient") as mock_client,
            _patch_mcp(),
        ):
            # 模拟超时: 抛出 asyncio.TimeoutError
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=asyncio.TimeoutError()
            )
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await _search(
                query="test", platforms="github", timeout=1, tool_runtime=ToolRuntime(config=mock_config)
            )

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

        with _patch_base(mock_response), _patch_mcp():
            result = await _search(query="test", platforms="github", tool_runtime=ToolRuntime(config=mock_config))

        # 平台 HTTP 错误返回 PLATFORM_ERROR(单平台失败), 而非 TOOL_ERROR
        assert PLATFORM_ERROR in result
        assert "403" in result


# ── 缓存测试 ─────────────────────────────────────────────────


class TestCache:
    """缓存行为测试"""

    @pytest.mark.asyncio
    async def test_cache_hit(self, mock_config):
        """相同查询应命中缓存; 缓存只存干净结果, 耗时标注仅在首次真实搜索时附加"""
        with _patch_all_platforms(_empty_mock()):
            # 第一次调用: 真实搜索, 结果带耗时标注
            result1 = await _search(
                query="cache_test", platforms="all", tool_runtime=ToolRuntime(config=mock_config)
            )
            # 第二次调用: 命中缓存, 返回干净结果 (无耗时标注)
            result2 = await _search(
                query="cache_test", platforms="all", tool_runtime=ToolRuntime(config=mock_config)
            )

        assert "[搜索用时" in result1
        assert "[搜索用时" not in result2
        # 首次搜索=干净结果+各平台耗时标注行; 去掉这些行后应与缓存命中结果一致
        import re

        stripped = re.sub(r"\n\[搜索用时 [\d.]+秒\]", "", result1)
        assert stripped == result2
        assert len(search_cache) > 0

    @pytest.mark.asyncio
    async def test_different_query_no_cache(self, mock_config):
        """不同查询不应命中缓存"""
        with _patch_all_platforms(_empty_mock()):
            await _search(query="query1", platforms="all", tool_runtime=ToolRuntime(config=mock_config))
            await _search(query="query2", platforms="all", tool_runtime=ToolRuntime(config=mock_config))

        # 两个查询 × 全部平台 = 独立缓存条目
        assert len(search_cache) == 2 * len(PLATFORM_SEARCHERS)

    @pytest.mark.asyncio
    async def test_cached_platform_not_requeued(self, mock_config):
        """已缓存的平台结果被复用, 不会再次发起网络请求"""
        import httpx
        from unittest.mock import AsyncMock as _AM

        bing_get = _AM(side_effect=httpx.ConnectError("bing 不应再次请求"))

        with _patch_all_platforms(_empty_mock()):
            # 第一次: 全部平台搜索成功(含 bing), 写入缓存
            result1 = await _search(query="reuse", platforms="all", tool_runtime=ToolRuntime(config=mock_config))
            assert "bing" in result1.lower()

            # 第二次: bing.http_get 被替换为抛错, 但 bing 命中缓存, 不会调用它
            with patch("uniclaw.tools.search.bing.http_get", new=bing_get):
                result2 = await _search(
                    query="reuse", platforms="all", tool_runtime=ToolRuntime(config=mock_config)
                )
                assert "bing" in result2.lower()
                assert bing_get.await_count == 0

    @pytest.mark.asyncio
    async def test_failure_not_cached(self, mock_config):
        """平台搜索失败的结果不应被缓存, 再次搜索会重新请求"""
        import httpx
        from unittest.mock import AsyncMock as _AM

        failing_get = _AM(side_effect=httpx.ConnectError("Connection refused"))

        # 第一次搜索: bing 失败, 其他平台成功
        with (
            _patch_all_platforms(_empty_mock()),
            patch("uniclaw.tools.search.bing.http_get", failing_get),
        ):
            result1 = await _search(query="retry", platforms="all", tool_runtime=ToolRuntime(config=mock_config))
            assert PLATFORM_ERROR in result1
            # 失败结果不写入缓存 (平台级缓存无该 key, 与生产 key 格式一致)
            p_ck = cache_key("retry", "bing", limit=10)
            assert p_ck not in search_cache

        # 第二次搜索: 应再次请求 bing (失败未被缓存), 而非直接返回旧失败信息
        with (
            _patch_all_platforms(_empty_mock()),
            patch("uniclaw.tools.search.bing.http_get", failing_get),
        ):
            result2 = await _search(query="retry", platforms="all", tool_runtime=ToolRuntime(config=mock_config))
            assert PLATFORM_ERROR in result2

        # bing 每轮搜索最多请求 2 次 (RSS 失败后回退 HTML), 两轮共 4 次
        assert failing_get.await_count == 4


# ── 平台路由完整性测试 ───────────────────────────────────────


class TestPlatformRouting:
    """平台路由完整性测试"""

    def test_all_platforms_have_searcher(self):
        """所有声明的平台都有对应的搜索函数"""
        for platform_name, searcher in PLATFORM_SEARCHERS.items():
            assert callable(searcher), f"{platform_name} 的搜索函数不可调用"

    def test_platform_list_completeness(self):
        """平台列表包含所有预期平台 (5 原有 + 7 移植 + exa/bing/duckduckgo)"""
        expected = {
            "github",
            "arxiv",
            "stackoverflow",
            "hackernews",
            "bilibili",
            "reddit",
            "google_news",
            "openalex",
            "polymarket",
            "sec_edgar",
            "huggingface",
            "alphaxiv",
            "exa",
            "bing",
            "duckduckgo",
        }
        assert set(PLATFORM_SEARCHERS.keys()) == expected

    @pytest.mark.asyncio
    async def test_each_platform_can_be_called(self, mock_config):
        """每个平台都可以被调用(不会抛出未处理异常)"""
        for platform_name, searcher in PLATFORM_SEARCHERS.items():
            with _patch_all_platforms(_empty_mock()):
                # 直接调用单平台搜索函数, 不应抛出异常
                result = await searcher("test", 10, "", "", mock_config)
                assert isinstance(result, str), f"{platform_name} 未返回字符串"
                assert len(result) > 0, f"{platform_name} 返回空字符串"


# ── LLM 重排测试 ────────────────────────────────────────────


class TestRerankResults:
    """搜索结果超过阈值时的 LLM 重排测试"""

    def _github_mock(self, n_items):
        """构造含 n_items 条结果的 GitHub mock 响应。"""
        items = [
            {
                "full_name": f"user/repo{i}",
                "description": f"desc {i}",
                "stargazers_count": i,
                "language": "Python",
                "html_url": f"https://github.com/user/repo{i}",
            }
            for i in range(n_items)
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": items}
        mock_response.raise_for_status = MagicMock()
        return mock_response

    @pytest.mark.asyncio
    async def test_rerank_when_over_threshold(self, mock_config):
        """结果超过 10 条时调用 achat 重排, 返回重排后的文本"""
        reranked_text = "**精选结果**\n  https://github.com/user/repo_best\n  desc best"
        mock_response = MagicMock()
        mock_response.content = reranked_text

        with (
            _patch_all_with_github(self._github_mock(12)),
            patch(
                "uniclaw.provider.fallback.achat",
                new=AsyncMock(return_value=mock_response),
            ) as mock_achat,
        ):
            result = await _search(query="test", platforms="all", tool_runtime=ToolRuntime(config=mock_config))

        mock_achat.assert_awaited_once()
        assert result == reranked_text

    @pytest.mark.asyncio
    async def test_no_rerank_below_threshold(self, mock_config):
        """结果不超过 10 条时不调用 achat, 直接返回原文本"""
        with (
            _patch_all_with_github(self._github_mock(5)),
            patch(
                "uniclaw.provider.fallback.achat",
                new=AsyncMock(return_value=MagicMock(content="不应被使用")),
            ) as mock_achat,
        ):
            result = await _search(query="test", platforms="all", tool_runtime=ToolRuntime(config=mock_config))

        mock_achat.assert_not_awaited()
        assert "GitHub" in result

    @pytest.mark.asyncio
    async def test_rerank_failure_falls_back(self, mock_config):
        """achat 调用失败时回退到原文本, 不抛异常"""
        with (
            _patch_all_with_github(self._github_mock(12)),
            patch(
                "uniclaw.provider.fallback.achat",
                new=AsyncMock(side_effect=RuntimeError("LLM 不可用")),
            ),
        ):
            result = await _search(query="test", platforms="all", tool_runtime=ToolRuntime(config=mock_config))

        assert "GitHub" in result
        assert "user/repo" in result

    @pytest.mark.asyncio
    async def test_error_info_kept_after_rerank(self, mock_config):
        """重排后仍必须包含失败平台的错误信息"""
        import httpx

        reranked_text = "**精选结果**\n  https://github.com/user/repo_best"
        mock_response = MagicMock()
        mock_response.content = reranked_text

        # github 成功(12 条, 触发重排), bing 失败(连接错误)
        with (
            _patch_all_with_github(self._github_mock(12)),
            patch(
                "uniclaw.tools.search.bing.http_get",
                new=AsyncMock(side_effect=httpx.ConnectError("Connection refused")),
            ),
            patch(
                "uniclaw.provider.fallback.achat",
                new=AsyncMock(return_value=mock_response),
            ),
        ):
            result = await _search(query="test", platforms="all", tool_runtime=ToolRuntime(config=mock_config))

        # 重排文本 + 失败平台错误信息
        assert "精选结果" in result
        assert PLATFORM_ERROR in result
        assert "bing" in result.lower()

    @pytest.mark.asyncio
    async def test_intent_passed_to_rerank(self, mock_config):
        """搜索意图 intent 应透传给 LLM 重排的 user message"""
        reranked_text = "**精选结果**\n  https://github.com/user/repo_best"
        mock_response = MagicMock()
        mock_response.content = reranked_text

        captured = {}

        async def _fake_achat(**kwargs):
            captured["session"] = kwargs.get("session")
            return mock_response

        with (
            _patch_all_with_github(self._github_mock(12)),
            patch("uniclaw.provider.fallback.achat", new=_fake_achat),
        ):
            result = await _search(
                query="python",
                platforms="all",
                intent="找用于数据分析的库",
                tool_runtime=ToolRuntime(config=mock_config),
            )

        assert result == reranked_text
        session = captured["session"]
        assert session is not None
        # user message 文本应包含搜索意图 (含 query 与 intent 的拼接)
        user_content = "\n".join(
            m.content if isinstance(m.content, str) else "" for m in session._messages
        )
        assert "搜索关键词: python" in user_content
        assert "搜索意图: 找用于数据分析的库" in user_content

    @pytest.mark.asyncio
    async def test_intent_empty_omitted_from_rerank(self, mock_config):
        """intent 为空时不往 user message 里塞空意图行"""
        reranked_text = "**精选结果**\n  https://github.com/user/repo_best"
        mock_response = MagicMock()
        mock_response.content = reranked_text

        captured = {}

        async def _fake_achat(**kwargs):
            captured["session"] = kwargs.get("session")
            return mock_response

        with (
            _patch_all_with_github(self._github_mock(12)),
            patch("uniclaw.provider.fallback.achat", new=_fake_achat),
        ):
            await _search(query="python", platforms="all", tool_runtime=ToolRuntime(config=mock_config))

        session = captured["session"]
        user_content = "\n".join(
            m.content if isinstance(m.content, str) else "" for m in session._messages
        )
        assert "搜索关键词: python" in user_content
        assert "搜索意图" not in user_content


# ── 流式输出测试 ─────────────────────────────────────────────


class TestStreamingOutput:
    """webSearch 流式输出: 每个平台完成后立即推送, 不等最慢平台"""

    @pytest.mark.asyncio
    async def test_streams_each_platform_on_completion(self, mock_config):
        """每个平台完成时都应通过 tool_runtime.stream 推送, 且推送内容含平台名"""
        streamed: list[str] = []

        async def _writer(tool_call_id: str, content: str):
            streamed.append(content)

        rt = ToolRuntime(config=mock_config, stream_writer=_writer)
        with _patch_all_platforms(_empty_mock()):
            await _search(query="stream", platforms="all", tool_runtime=rt)

        # 每个平台都推送了一次
        assert len(streamed) == len(PLATFORM_SEARCHERS)
        # 推送内容包含平台名
        for platform in PLATFORM_SEARCHERS:
            assert any(
                f"[{platform}]" in content for content in streamed
            ), f"缺少 {platform} 的流式推送"

    @pytest.mark.asyncio
    async def test_stream_noop_without_callback(self, mock_config):
        """无流式回调时 (命令行直接调用), 推送被静默忽略, 不影响正常返回"""
        with _patch_all_platforms(_empty_mock()):
            result = await _search(
                query="noop", platforms="all", tool_runtime=ToolRuntime(config=mock_config)
            )

        # 正常返回合并结果 (非空)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "无搜索结果" in result

    @pytest.mark.asyncio
    async def test_stream_preserves_final_result(self, mock_config):
        """流式推送不影响最终返回值 (缓存 + 失败信息逻辑不变)"""
        streamed: list[str] = []

        async def _writer(tool_call_id: str, content: str):
            streamed.append(content)

        rt = ToolRuntime(config=mock_config, stream_writer=_writer)
        with _patch_all_platforms(_empty_mock()):
            result_stream = await _search(
                query="same", platforms="all", tool_runtime=rt
            )

        # 不设流式回调时结果一致 (流式只影响 UI 展示)
        # 注意: 首次调用写入缓存, 第二次调用可能命中缓存; 清空缓存保证两次都真实搜索
        search_cache.clear()
        with _patch_all_platforms(_empty_mock()):
            result_plain = await _search(
                query="same", platforms="all", tool_runtime=ToolRuntime(config=mock_config)
            )

        assert result_stream == result_plain
        assert len(streamed) == len(PLATFORM_SEARCHERS)
