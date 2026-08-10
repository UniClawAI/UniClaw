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
