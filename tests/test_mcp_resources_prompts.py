"""MCP resources/prompts 桥接测试。

覆盖:
- 桥接工具生成(_make_bridge_tools):resources/prompts 各生成 list + read/get
- 服务器 capabilities 声明时的发现(_discover_tools_async)
- capabilities 未声明时主连接探测(_session_probes_capability)
- 桥接工具调用链路:list_resources/read_resource/list_prompts/get_prompt
- 格式化:资源列表(含模板)、资源内容(文本/blob/多模态)、提示词列表(含参数)、提示词内容(含媒体降级)
- 检索关键词(_bridge_keywords):桥接工具命中、普通工具不命中
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from uniclaw.tools.base import Tool
from uniclaw.tools.mcp import (
    _bridge_keywords,
    _discover_tools_async,
    _format_prompts_list,
    _format_prompt_result,
    _format_read_result,
    _format_resources_list,
    _make_bridge_tools,
    _run_bridge_op,
    _session_probes_capability,
)


# ── 桥接工具生成 ─────────────────────────────────────────────


class TestBridgeTools:
    def test_resources_bridge_names(self):
        """resources 生成 {server}_list_resources / _read_resource。"""
        tools = _make_bridge_tools("srv", {}, "resources")
        assert [t.name for t in tools] == [
            "srv_list_resources",
            "srv_read_resource",
        ]
        assert all(isinstance(t, Tool) for t in tools)

    def test_prompts_bridge_names(self):
        """prompts 生成 {server}_list_prompts / _get_prompt。"""
        tools = _make_bridge_tools("srv", {}, "prompts")
        assert [t.name for t in tools] == ["srv_list_prompts", "srv_get_prompt"]

    def test_bridge_schemas(self):
        """桥接工具参数 schema:read 需要 uri,get 需要 name。"""
        _, rd = _make_bridge_tools("srv", {}, "resources")
        assert rd.parameters["required"] == ["uri"]
        _, gp = _make_bridge_tools("srv", {}, "prompts")
        assert gp.parameters["required"] == ["name"]
        assert "arguments" in gp.parameters["properties"]


# ── 检索关键词 ───────────────────────────────────────────────


class TestBridgeKeywords:
    def test_resources_keywords(self):
        for name in ("srv_list_resources", "srv_read_resource"):
            kws = _bridge_keywords(name)
            assert "资源" in kws or "resources" in kws or "read_resource" in kws

    def test_prompts_keywords(self):
        for name in ("srv_list_prompts", "srv_get_prompt"):
            kws = _bridge_keywords(name)
            assert "prompt" in kws

    def test_normal_tool_no_keywords(self):
        assert _bridge_keywords("srv_do_thing") == []
        assert _bridge_keywords("exa_search") == []


# ── 能力探测 ─────────────────────────────────────────────────


class _FakeSession:
    """通用 Fake ClientSession:可注入 capabilities 与各方法行为。"""

    def __init__(self, read, write):
        self.read = read
        self.write = write

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def initialize(self):
        self.server_capabilities = getattr(self, "_caps", None)

    async def list_tools(self):
        return SimpleNamespace(tools=[])


def _set_caps(session, resources=None, prompts=None):
    session._caps = SimpleNamespace(resources=resources, prompts=prompts)


def _patch_connect_and_session(session):
    """把 _connect_mcp 替换为直接 yield 预设 session 的上下文管理器。

    现在 _connect_mcp 直接产出已初始化的 session(fastmcp Client 语义),
    测试 mock 时只需让它 yield 那个预设好 capabilities/methods 的实例即可。
    外层是同步上下文管理器(可直接 with),内部 fake_connect 是 async 的
    (因为它替换的是 asynccontextmanager)。

    注意: 真实路径里 _connect_mcp 会 initialize 并填充 server_capabilities;
    mock 直接 yield 时没有这一步, 这里从 _caps 补齐, 避免 discover 误判
    所有能力都"未声明"而去探测。
    """
    from contextlib import asynccontextmanager, contextmanager

    @asynccontextmanager
    async def fake_connect(connection):
        if not hasattr(session, "server_capabilities"):
            try:
                session.server_capabilities = session._caps
            except AttributeError:
                session.server_capabilities = None
        yield session

    @contextmanager
    def _ctx():
        with patch("uniclaw.tools.mcp._connect_mcp", fake_connect):
            yield

    return _ctx()


class TestCapabilityProbe:
    async def test_declared_caps_generate_bridges(self):
        """capabilities 声明 resources/prompts → 生成 4 个桥接工具。"""
        s = _FakeSession(None, None)
        _set_caps(s)  # resources=SimpleNamespace()? 见下

        # resources 能力用一个非 None 对象表示"已声明"。
        # mcp 的 ServerCapabilities.resources 是 ResourcesCapability 或 None。
        s._caps = SimpleNamespace(
            resources=SimpleNamespace(subscribe=False),  # 非 None = 已声明
            prompts=SimpleNamespace(),  # 非 None = 已声明
        )
        s.list_resources = AsyncMock(
            return_value=SimpleNamespace(resources=[], resourceTemplates=[])
        )
        s.list_prompts = AsyncMock(return_value=SimpleNamespace(prompts=[]))

        with _patch_connect_and_session(s):
            tools = await _discover_tools_async("srv", {})

        names = {t.name for t in tools}
        assert names == {
            "srv_list_resources",
            "srv_read_resource",
            "srv_list_prompts",
            "srv_get_prompt",
        }
        # 已声明时不走探测:list_resources 在发现阶段不应被调用
        # (桥接工具的调用是懒的,实际使用时才建连执行)
        s.list_resources.assert_not_awaited()

    async def test_undeclared_but_available_probed(self):
        """capabilities 未声明但方法可用 → 探测后注册。"""
        s = _FakeSession(None, None)
        _set_caps(s, None, None)  # resources/prompts 均 None
        s.list_resources = AsyncMock(
            return_value=SimpleNamespace(resources=[], resourceTemplates=[])
        )
        s.list_prompts = AsyncMock(return_value=SimpleNamespace(prompts=[]))

        with _patch_connect_and_session(s):
            tools = await _discover_tools_async("srv", {})

        names = {t.name for t in tools}
        # 探测成功 → 注册桥接工具
        assert "srv_list_resources" in names
        assert "srv_read_resource" in names
        assert "srv_list_prompts" in names
        assert "srv_get_prompt" in names
        s.list_resources.assert_awaited()
        s.list_prompts.assert_awaited()

    async def test_undeclared_and_unavailable_skipped(self):
        """capabilities 未声明且方法不可用 → 静默跳过,不注册。"""
        s = _FakeSession(None, None)
        _set_caps(s, None, None)

        async def _missing():
            raise AttributeError("list_resources not implemented")

        s.list_resources = _missing
        s.list_prompts = AsyncMock(side_effect=RuntimeError("not supported"))

        with _patch_connect_and_session(s):
            tools = await _discover_tools_async("srv", {})

        assert tools == []  # 无普通工具,桥接探测失败 → 全空

    async def test_probe_returns_bool(self):
        """_session_probes_capability 按方法结果返回布尔。"""
        s = _FakeSession(None, None)
        s.list_resources = AsyncMock(return_value=object())
        assert await _session_probes_capability(s, "resources") is True

        s.list_prompts = AsyncMock(side_effect=RuntimeError("x"))
        assert await _session_probes_capability(s, "prompts") is False


# ── 桥接工具调用链路 ─────────────────────────────────────────


class _CallableFakeSession(_FakeSession):
    """支持 list/read/get 的 Fake,记录调用参数。"""

    def __init__(self, read, write):
        super().__init__(read, write)
        self._calls = []
        self._caps = SimpleNamespace(
            resources=SimpleNamespace(), prompts=SimpleNamespace()
        )

    async def list_resources(self):
        self._calls.append("list_resources")
        return SimpleNamespace(
            resources=[
                SimpleNamespace(
                    name="doc", uri="file:///a.txt", description="说明", mime_type="text/plain"
                )
            ],
            resourceTemplates=[],
        )

    async def read_resource(self, uri):
        self._calls.append(("read_resource", uri))
        return SimpleNamespace(
            contents=[SimpleNamespace(mime_type="text/plain", text="内容:" + uri)]
        )

    async def list_prompts(self):
        self._calls.append("list_prompts")
        return SimpleNamespace(
            prompts=[
                SimpleNamespace(
                    name="greet",
                    title="问候",
                    description="生成问候语",
                    arguments=[{"name": "who", "required": True}, {"name": "lang", "required": False}],
                )
            ]
        )

    async def get_prompt(self, name, arguments=None):
        self._calls.append(("get_prompt", name, arguments))
        return SimpleNamespace(
            description="生成问候",
            messages=[
                SimpleNamespace(
                    role="user",
                    content=SimpleNamespace(type="text", text="你好,{who}!"),
                )
            ],
        )


class TestBridgeCallFlow:
    async def test_full_bridge_flow(self):
        """四种桥接工具端到端可调用,格式化正确。"""
        with _patch_connect_and_session(_CallableFakeSession(None, None)):
            # 通过 _discover_tools_async 发现(发现与调用共用同一 mock 区)
            tools = await _discover_tools_async("srv", {})
            tmap = {t.name: t for t in tools}

            out = await tmap["srv_list_resources"].func()
            assert "doc" in out
            assert "file:///a.txt" in out
            assert "text/plain" in out

            out = await tmap["srv_read_resource"].func(uri="file:///a.txt")
            assert "内容:file:///a.txt" in out

            out = await tmap["srv_list_prompts"].func()
            assert "greet" in out
            assert "who*" in out  # 必填参数标注星号
            assert "lang" in out

            out = await tmap["srv_get_prompt"].func(
                name="greet", arguments={"who": "world"}
            )
            assert "你好,{who}!" in out
            assert "[user]" in out


# ── 格式化 ───────────────────────────────────────────────────


class TestResourceFormatting:
    def test_resources_list_with_templates(self):
        res = SimpleNamespace(
            resources=[
                SimpleNamespace(
                    name="doc",
                    uri="file:///a.txt",
                    description="说明",
                    mime_type="text/plain",
                    size=123,
                ),
                SimpleNamespace(name="", uri="file:///b", description="", mime_type=""),
            ],
            resourceTemplates=[
                SimpleNamespace(
                    name="tpl",
                    uri_template="file:///{id}.md",
                    description="模板说明",
                )
            ],
        )
        out = _format_resources_list(res, "srv")
        assert "共 2 个" in out
        assert "file:///a.txt [doc]: 说明 (mime: text/plain) (123 bytes)" in out
        assert "file:///b" in out
        assert "file:///{id}.md [tpl]: 模板说明" in out

    def test_resources_list_empty(self):
        out = _format_resources_list(SimpleNamespace(resources=[], resourceTemplates=[]), "srv")
        assert "(无)" in out

    @pytest.mark.parametrize(
        "contents_kind,expect",
        [
            ("text", "文本资源内容"),
            ("image", "image_url"),
            ("audio", "input_audio"),
        ],
    )
    def test_read_resource_contents(self, contents_kind, expect):
        if contents_kind == "text":
            contents = [SimpleNamespace(mime_type="text/plain", text="文本资源内容")]
        elif contents_kind == "image":
            contents = [SimpleNamespace(mime_type="image/png", blob="aGVsbG8=", text=None)]
        else:
            contents = [SimpleNamespace(mime_type="audio/mpeg", blob="QQ==", text=None)]
        out = _format_read_result(SimpleNamespace(contents=contents), "srv")
        if contents_kind == "text":
            assert out == "资源读取成功(srv):\n文本资源内容"
        else:
            assert isinstance(out, list)
            assert out[0]["text"].startswith("资源读取成功")
            assert out[1]["type"] == expect

    def test_read_resource_empty(self):
        out = _format_read_result(SimpleNamespace(contents=[]), "srv")
        assert "(空资源内容)" in out

    def test_read_resource_mime_no_blob(self):
        """blob 为空时不走 media block,退化为空占位。"""
        out = _format_read_result(
            SimpleNamespace(contents=[SimpleNamespace(mime_type="image/png", blob=None, text=None)]),
            "srv",
        )
        assert "(空资源内容)" in out


class TestPromptFormatting:
    def test_prompts_list(self):
        res = SimpleNamespace(
            prompts=[
                SimpleNamespace(
                    name="greet",
                    title="问候",
                    description="生成问候语",
                    arguments=[{"name": "who", "required": True}, {"name": "lang", "required": False}],
                )
            ]
        )
        out = _format_prompts_list(res, "srv")
        assert "greet [问候]" in out
        assert "生成问候语" in out
        assert "who*, lang" in out

    def test_prompts_list_empty(self):
        out = _format_prompts_list(SimpleNamespace(prompts=[]), "srv")
        assert "(无)" in out

    def test_prompt_result_text(self):
        res = SimpleNamespace(
            description="生成问候",
            messages=[
                SimpleNamespace(
                    role="user",
                    content=SimpleNamespace(type="text", text="你好!"),
                )
            ],
        )
        out = _format_prompt_result(res, "srv")
        assert "生成问候" in out
        assert "[user]" in out
        assert "你好!" in out
        assert "[/user]" in out

    def test_prompt_result_image_degraded(self):
        """图片内容降级为文本占位,不展开 base64。"""
        res = SimpleNamespace(
            description="",
            messages=[
                SimpleNamespace(
                    role="assistant",
                    content=SimpleNamespace(type="image", mime_type="image/png"),
                )
            ],
        )
        out = _format_prompt_result(res, "srv")
        assert "[图片内容(image/png),需多模态模型查看]" in out

    def test_prompt_result_resource_blob_not_dumped(self):
        """内嵌资源 blob 只留占位,不把 base64 倾泻进上下文。"""
        res = SimpleNamespace(
            description="",
            messages=[
                SimpleNamespace(
                    role="user",
                    content=SimpleNamespace(
                        type="resource",
                        resource=SimpleNamespace(blob="U0VDUkVU" * 100),
                    ),
                )
            ],
        )
        out = _format_prompt_result(res, "srv")
        assert "U0VDUkVU" not in out  # base64 不裸奔

    async def test_prompt_argument_pydantic_object(self):
        """mcp 2.x 的 PromptArgument 是 pydantic 对象而非 dict,格式化不崩。"""
        # 模拟 mcp 2.x 真实返回:arguments 是对象列表
        class FakePromptArg:
            name = "who"
            required = True

        class FakePromptArg2:
            name = "lang"
            required = False

        res = SimpleNamespace(
            prompts=[
                SimpleNamespace(
                    name="greet",
                    title=None,
                    description="问候",
                    arguments=[FakePromptArg(), FakePromptArg2()],
                )
            ]
        )
        out = _format_prompts_list(res, "srv")
        assert "who*" in out  # 必填带星号
        assert "lang" in out


# ── 资源模板聚合 ─────────────────────────────────────────────


class TestBridgeListResources:
    async def test_list_resources_merges_templates(self):
        """list_resources 桥接自动调用 list_resource_templates 并聚合展示。"""
        class FakeSession2(_FakeSession):
            def __init__(self, read, write):
                super().__init__(read, write)
                self._caps = SimpleNamespace(
                    resources=SimpleNamespace(), prompts=None
                )

            async def list_resources(self):
                return SimpleNamespace(
                    resources=[SimpleNamespace(name="s", uri="static://a", description="")]
                )

            async def list_resource_templates(self):
                return SimpleNamespace(
                    resource_templates=[
                        SimpleNamespace(
                            name="tpl", uri_template="file:///{id}", description="模板"
                        )
                    ]
                )

        s = FakeSession2(None, None)
        result = await _run_bridge_op(s, "list_resources", {})
        out = _format_resources_list(result, "srv")
        assert "static://a" in out
        assert "file:///{id}" in out
        assert "资源模板(共 1 个" in out

    async def test_list_resources_without_templates_api(self):
        """服务器不支持 list_resource_templates 时静默降级为静态资源。"""
        s = _FakeSession(None, None)
        s.list_resources = AsyncMock(
            return_value=SimpleNamespace(
                resources=[SimpleNamespace(name="s", uri="static://a", description="")]
            )
        )
        # 无 list_resource_templates 方法 → 捕获异常降级
        result = await _run_bridge_op(s, "list_resources", {})
        out = _format_resources_list(result, "srv")
        assert "static://a" in out
        assert "资源模板(共 0 个" in out


# ── 超时/错误透传 ────────────────────────────────────────────


class TestBridgeErrorHandling:
    async def test_call_error_returns_tool_error(self):
        """桥接调用失败返回 TOOL_ERROR 文本(不抛异常)。"""
        from uniclaw.utils.constants import TOOL_ERROR

        tools = _make_bridge_tools("srv", {}, "resources")

        @asynccontextmanager
        async def raising_connect(connection):
            raise TimeoutError("connect timed out")
            yield  # pragma: no cover

        with patch("uniclaw.tools.mcp._connect_mcp", raising_connect):
            out = await tools[0].func()
        assert out.startswith(TOOL_ERROR)

    async def test_bridge_uses_shared_session(self):
        """桥接工具统一经共享持久会话(get_session)调用。"""
        from uniclaw.tools.mcp import _make_bridge_caller

        ps = SimpleNamespace(
            get_session=AsyncMock(
                return_value=SimpleNamespace(
                    list_resources=AsyncMock(
                        return_value=SimpleNamespace(
                            resources=[], resourceTemplates=[]
                        )
                    )
                )
            )
        )
        mgr = SimpleNamespace(get_connection=lambda *a, **k: ps)
        with patch("uniclaw.tools.mcp.MCPManager.get_instance", return_value=mgr):
            caller = _make_bridge_caller(
                "srv", "list_resources", {"transport": "stdio"}
            )
            out = await caller()
        assert "共 0 个" in out
        ps.get_session.assert_awaited_once()