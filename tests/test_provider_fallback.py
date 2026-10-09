"""provider.fallback 测试 — 覆盖多模型回退逻辑。"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from uniclaw.provider import fallback


class TestFallbackChat:
    """同步 chat 回退测试。"""

    @patch("uniclaw.provider.fallback.router")
    def test_single_model_success(self, mock_router):
        """单模型成功调用。"""
        mock_router.chat.return_value = MagicMock(content="ok")
        result = fallback.chat("sys", [], model_name="model-a")
        assert result.content == "ok"
        mock_router.chat.assert_called_once()

    @patch("uniclaw.provider.fallback.router")
    def test_single_model_failure_raises(self, mock_router):
        """单模型失败时抛出异常。"""
        mock_router.chat.side_effect = RuntimeError("fail")
        with pytest.raises(RuntimeError, match="fail"):
            fallback.chat("sys", [], model_name="model-a")

    @patch("uniclaw.provider.fallback.router")
    def test_fallback_to_second_model(self, mock_router):
        """主模型失败,回退到第二个模型。"""
        mock_router.chat.side_effect = [
            RuntimeError("model-a failed"),
            MagicMock(content="ok from b"),
        ]
        result = fallback.chat("sys", [], model_name=["model-a", "model-b"])
        assert result.content == "ok from b"
        assert mock_router.chat.call_count == 2

    @patch("uniclaw.provider.fallback.router")
    def test_all_models_fail_raises(self, mock_router):
        """所有模型都失败时抛出最后一个异常。"""
        mock_router.chat.side_effect = [
            RuntimeError("a failed"),
            RuntimeError("b failed"),
        ]
        with pytest.raises(RuntimeError, match="b failed"):
            fallback.chat("sys", [], model_name=["model-a", "model-b"])

    @patch("uniclaw.provider.fallback.router")
    def test_string_model_name_converted_to_list(self, mock_router):
        """字符串 model_name 自动转为列表处理。"""
        mock_router.chat.return_value = MagicMock(content="ok")
        fallback.chat("sys", [], model_name="single-model")
        mock_router.chat.assert_called_once()
        call_kwargs = mock_router.chat.call_args
        assert call_kwargs.kwargs["model_name"] == "single-model"

    @patch("uniclaw.provider.fallback.router")
    def test_third_model_used_when_first_two_fail(self, mock_router):
        """前两个模型失败,使用第三个。"""
        mock_router.chat.side_effect = [
            RuntimeError("a failed"),
            RuntimeError("b failed"),
            MagicMock(content="ok from c"),
        ]
        result = fallback.chat(
            "sys", [], model_name=["model-a", "model-b", "model-c"]
        )
        assert result.content == "ok from c"
        assert mock_router.chat.call_count == 3


class TestFallbackAchat:
    """异步 achat 回退测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.provider.fallback.router")
    @patch("uniclaw.provider.fallback.warn", new_callable=AsyncMock)
    async def test_async_single_model_success(self, mock_warn, mock_router):
        """异步单模型成功调用。"""
        mock_router.achat = AsyncMock(return_value=MagicMock(content="ok"))
        result = await fallback.achat("sys", [], model_name="model-a")
        assert result.content == "ok"

    @pytest.mark.asyncio
    @patch("uniclaw.provider.fallback.router")
    @patch("uniclaw.provider.fallback.warn", new_callable=AsyncMock)
    async def test_async_fallback_to_second(self, mock_warn, mock_router):
        """异步主模型失败,回退到第二个。"""
        mock_router.achat = AsyncMock(
            side_effect=[RuntimeError("a failed"), MagicMock(content="ok from b")]
        )
        result = await fallback.achat("sys", [], model_name=["model-a", "model-b"])
        assert result.content == "ok from b"

    @pytest.mark.asyncio
    @patch("uniclaw.provider.fallback.router")
    @patch("uniclaw.provider.fallback.warn", new_callable=AsyncMock)
    async def test_async_all_fail_raises(self, mock_warn, mock_router):
        """异步所有模型失败时抛出异常。"""
        mock_router.achat = AsyncMock(
            side_effect=[RuntimeError("a failed"), RuntimeError("b failed")]
        )
        with pytest.raises(RuntimeError, match="b failed"):
            await fallback.achat("sys", [], model_name=["model-a", "model-b"])


# ── 多模态降级无限递归防护(现场: Stack overflow + invalid base64) ──


class TestMultimodalFallbackNoReentry:
    """_describe_multimodal → describe_media → achat → _describe_multimodal 不得无限递归。"""

    @pytest.mark.asyncio
    async def test_flag_set_during_describe(self):
        """describe_media 执行期间降级标志置位(achat 降级分支据此跳过)。"""
        from uniclaw.provider.openai_provider import (
            _MM_FALLBACK_ACTIVE,
            _describe_multimodal,
        )

        seen: dict = {}

        async def fake_describe(*args, **kwargs):
            seen["flag"] = _MM_FALLBACK_ACTIVE.get()
            return "文本描述"

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                ],
            }
        ]
        with patch(
            "uniclaw.utils.media_describer.describe_media", fake_describe
        ):
            out = await _describe_multimodal(messages, "mm-model", config=MagicMock())

        assert seen["flag"] is True, "describe 期间必须处于降级上下文(防重入)"
        assert out[0]["content"][1]["text"] == "文本描述"
        assert _MM_FALLBACK_ACTIVE.get() is False, "退出后标志复位"

    @pytest.mark.asyncio
    async def test_describe_media_failure_placeholder(self):
        """describe_media 失败返回占位文本,不把降级链炸掉。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from uniclaw.utils.media_describer import describe_media

        class BadRequestError(Exception):
            pass

        with patch(
            "uniclaw.provider.fallback.achat",
            AsyncMock(side_effect=BadRequestError("invalid image base64 content")),
        ), patch(
            "uniclaw.utils.media_describer.get_cached_description", return_value=None
        ), patch(
            "uniclaw.utils.media_describer.save_description"
        ):
            out = await describe_media(
                "data:image/png;base64,bad", "image", "mm", config=MagicMock()
            )

        assert "描述失败" in out
        assert "invalid image base64" in out

    @pytest.mark.asyncio
    async def test_nested_multimodal_error_does_not_recurse(self):
        """降级上下文中再遇多模态错误: 直接抛出而非二次降级(防栈溢出)。"""
        from uniclaw.provider.openai_provider import _MM_FALLBACK_ACTIVE

        class FakeErr(Exception):
            pass

        # 模拟 achat 的降级判定条件
        def would_fallback(err, mm_model) -> bool:
            from uniclaw.provider.openai_provider import is_multimodal_error

            return (
                is_multimodal_error(err) and mm_model and not _MM_FALLBACK_ACTIVE.get()
            )

        import uniclaw.provider.openai_provider as op

        class BadReq(Exception):
            status_code = 400  # is_multimodal_error 要求 400/404

        err = BadReq("Error code: 400 - invalid image base64 content")
        assert op.is_multimodal_error(err) is True
        assert would_fallback(err, "mm") is True  # 正常上下文:降级

        token = _MM_FALLBACK_ACTIVE.set(True)
        try:
            assert would_fallback(err, "mm") is False, "降级中禁止二次降级(递归)"
        finally:
            _MM_FALLBACK_ACTIVE.reset(token)
