"""媒体工具测试 — 覆盖 ReadMedia、GenerateImage 和格式检测。"""

import base64
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from uniclaw.tools import media as media_mod
from uniclaw.tools.media import (
    GenerateImage,
    ReadMedia,
    _detect_media_type,
    _is_url,
    _read_audio,
    _read_image,
    _read_media_impl,
    _read_video,
    _url_ext,
    get_all_tools,
    get_tools,
    is_image_file,
    is_media_file,
)
from uniclaw.utils.constants import TOOL_ERROR


class TestFormatDetection:
    """格式检测测试。"""

    def test_is_image_file(self):
        """图片扩展名。"""
        assert is_image_file("a.PNG")
        assert is_image_file("b.jpg")
        assert not is_image_file("c.txt")

    def test_is_media_file(self):
        """媒体扩展名。"""
        assert is_media_file("a.mp3")
        assert is_media_file("b.mp4")
        assert is_media_file("c.png")
        assert not is_media_file("d.py")

    def test_detect_media_type(self):
        """媒体类型检测。"""
        assert _detect_media_type(".png") == "image"
        assert _detect_media_type(".mp3") == "audio"
        assert _detect_media_type(".mp4") == "video"
        assert _detect_media_type(".txt") is None

    def test_is_url(self):
        """URL 检测。"""
        assert _is_url("https://example.com/a.png") is True
        assert _is_url("http://example.com/a.png") is True
        assert _is_url("C:/local/file.png") is False

    def test_url_ext(self):
        """URL 扩展名提取。"""
        assert _url_ext("https://a.com/img.png") == ".png"
        assert _url_ext("https://a.com/img.png?size=large") == ".png"
        assert _url_ext("https://a.com/noext") == ""


class TestReadImage:
    """_read_image 测试。"""

    def test_png(self, tmp_path):
        """PNG 读取为 base64。"""
        p = tmp_path / "test.png"
        p.write_bytes(b"\x89PNG\x0d\x0a\x1a\x0a fake-data")
        blocks = _read_image(p, ".png")
        assert blocks[0]["type"] == "text"
        assert "test.png" in blocks[0]["text"]
        assert blocks[1]["type"] == "image_url"
        url = blocks[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_svg(self, tmp_path):
        """SVG 读取为文本。"""
        p = tmp_path / "test.svg"
        p.write_text("<svg><circle/></svg>", encoding="utf-8")
        blocks = _read_image(p, ".svg")
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "text"
        assert "<circle" in blocks[1]["text"]


class TestReadAudioVideo:
    """_read_audio / _read_video 测试。"""

    def test_audio(self, tmp_path):
        """音频读取。"""
        p = tmp_path / "test.mp3"
        p.write_bytes(b"ID3 fake audio")
        blocks = _read_audio(p)
        assert blocks[0]["type"] == "text"
        assert "test.mp3" in blocks[0]["text"]
        assert blocks[1]["type"] == "input_audio"
        assert blocks[1]["input_audio"]["data"].startswith("data:audio")

    def test_video(self, tmp_path):
        """视频读取。"""
        p = tmp_path / "test.mp4"
        p.write_bytes(b"fake video data")
        blocks = _read_video(p, fps=5)
        assert blocks[0]["type"] == "text"
        assert "test.mp4" in blocks[0]["text"]
        assert blocks[1]["type"] == "video_url"
        assert blocks[1]["fps"] == 5
        assert blocks[1]["video_url"]["url"].startswith("data:video")


class TestReadMediaImpl:
    """_read_media_impl 测试。"""

    def test_file_not_found(self, tmp_path):
        """文件不存在。"""
        result = _read_media_impl(str(tmp_path / "missing.png"))
        assert isinstance(result, str)
        assert "文件不存在" in result

    def test_is_directory(self, tmp_path):
        """目录。"""
        result = _read_media_impl(str(tmp_path))
        assert "是一个目录" in result

    def test_unsupported_format(self, tmp_path):
        """不支持格式。"""
        p = tmp_path / "test.xyz"
        p.write_text("data", encoding="utf-8")
        result = _read_media_impl(str(p))
        assert "不支持的格式" in result

    def test_too_large(self, tmp_path):
        """文件过大。"""
        p = tmp_path / "test.png"
        p.write_bytes(b"x" * 100)
        with patch.dict(media_mod.SIZE_LIMITS, {"image": 10}):
            result = _read_media_impl(str(p))
        assert "文件过大" in result

    def test_image_success(self, tmp_path):
        """图片读取成功。"""
        p = tmp_path / "test.png"
        p.write_bytes(b"\x89PNG data")
        blocks = _read_media_impl(str(p))
        assert isinstance(blocks, list)
        assert blocks[1]["type"] == "image_url"

    def test_audio_success(self, tmp_path):
        """音频读取成功。"""
        p = tmp_path / "test.mp3"
        p.write_bytes(b"ID3")
        blocks = _read_media_impl(str(p))
        assert isinstance(blocks, list)
        assert blocks[1]["type"] == "input_audio"

    def test_url_image(self):
        """URL 图片。"""
        blocks = _read_media_impl("https://example.com/a.png")
        assert isinstance(blocks, list)
        assert blocks[1]["type"] == "image_url"

    def test_url_audio(self):
        """URL 音频。"""
        blocks = _read_media_impl("https://example.com/a.mp3")
        assert blocks[1]["type"] == "input_audio"

    def test_url_video(self):
        """URL 视频。"""
        blocks = _read_media_impl("https://example.com/a.mp4", fps=3)
        assert blocks[1]["type"] == "video_url"
        assert blocks[1]["fps"] == 3

    def test_url_unknown_format(self):
        """URL 无法识别格式。"""
        result = _read_media_impl("https://example.com/a.xyz")
        assert isinstance(result, str)
        assert "无法从 URL 识别" in result

    def test_read_exception(self, tmp_path):
        """读取异常。"""
        p = tmp_path / "test.png"
        p.write_bytes(b"data")
        with patch("uniclaw.tools.media._read_image", side_effect=RuntimeError("boom")):
            result = _read_media_impl(str(p))
        assert isinstance(result, str)
        assert "boom" in result


class TestReadMediaTool:
    """ReadMedia 工具测试。"""

    @pytest.mark.asyncio
    async def test_read_media(self, tmp_path):
        """异步读取媒体。"""
        p = tmp_path / "test.png"
        p.write_bytes(b"\x89PNG data")
        result = await ReadMedia(str(p))
        assert isinstance(result, list)
        assert result[1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_read_media_missing(self, tmp_path):
        """文件不存在。"""
        result = await ReadMedia(str(tmp_path / "missing.png"))
        assert isinstance(result, str)
        assert TOOL_ERROR in result


class TestGenerateImage:
    """GenerateImage 工具测试。"""

    def _config(self):
        config = MagicMock()
        config.image_model = "dall-e-3"
        return config

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, return_value=["http://img/x.png"])
    @patch("urllib.request.urlretrieve")
    async def test_save_url(self, mock_retrieve, mock_gen, tmp_path):
        """URL 结果保存到文件。"""
        target = str(tmp_path / "out.png")
        result = await GenerateImage("一只猫", path=target, config=self._config())
        assert result == f"已保存到: {(tmp_path / 'out.png').resolve()}"
        mock_retrieve.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, return_value=["aGVsbG8="])
    async def test_save_base64(self, mock_gen, tmp_path):
        """base64 结果保存到文件。"""
        target = str(tmp_path / "out.png")
        result = await GenerateImage("一只猫", path=target, config=self._config())
        assert (tmp_path / "out.png").read_bytes() == b"hello"

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, return_value=["http://img/x.png"])
    async def test_no_path_url(self, mock_gen):
        """无 path 且 URL 结果返回多模态。"""
        result = await GenerateImage("一只猫", config=self._config())
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[1]["image_url"]["url"] == "http://img/x.png"

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, return_value=["aGVsbG8="])
    async def test_no_path_base64(self, mock_gen):
        """无 path 且 base64 结果返回 data URI。"""
        result = await GenerateImage("一只猫", config=self._config())
        assert isinstance(result, list)
        url = result[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, side_effect=RuntimeError("boom"))
    async def test_exception(self, mock_gen):
        """生成异常。"""
        result = await GenerateImage("一只猫", config=self._config())
        assert isinstance(result, str)
        assert "图片生成失败" in result

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, return_value=[])
    async def test_empty_result(self, mock_gen):
        """空结果。"""
        result = await GenerateImage("一只猫", config=self._config())
        assert isinstance(result, str)
        assert "返回为空" in result


class TestGetMediaTools:
    """get_tools / get_all_tools 测试。"""

    def test_get_tools_with_image_model(self):
        """有 image_model 包含 GenerateImage。"""
        config = MagicMock()
        config.image_model = "dall-e-3"
        tools = get_tools(config)
        assert len(tools) == 2

    def test_get_tools_without_image_model(self):
        """无 image_model 只有 ReadMedia。"""
        config = MagicMock()
        config.image_model = None
        tools = get_tools(config)
        assert len(tools) == 1
        assert tools[0].name == "ReadMedia"

    def test_get_all_tools(self):
        """无条件返回全部。"""
        tools = get_all_tools()
        names = {t.name for t in tools}
        assert names == {"ReadMedia", "GenerateImage"}
