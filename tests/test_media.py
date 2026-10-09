"""媒体工具测试 — 覆盖 ReadMedia、GenerateImage、视觉预处理和格式检测。"""

import base64
import io

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from uniclaw.tools import media as media_mod
from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.media import (
    JPEG_QUALITY,
    MAX_IMAGE_SIDE,
    SMALL_IMAGE_MAX_SIDE,
    GenerateImage,
    ReadMedia,
    _detect_media_type,
    _is_url,
    _prepare_image_for_vision,
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

    def test_small_gradient_png_kept_intact(self, tmp_path):
        """小尺寸渐变 PNG 原样透传(不缩放/不重编码)。"""
        raw = _gradient_png(100, 80)
        p = tmp_path / "grad.png"
        p.write_bytes(raw)
        blocks = _read_image(p, ".png")
        url = blocks[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        sent = base64.b64decode(url.split(",", 1)[1])
        assert sent == raw

    def test_small_gradient_bmp_converted_to_png(self, tmp_path):
        """小尺寸渐变 BMP 转 PNG,保持原尺寸与色彩。"""
        raw = _gradient_bytes("BMP", 100, 80)
        p = tmp_path / "grad.bmp"
        p.write_bytes(raw)
        blocks = _read_image(p, ".bmp")
        url = blocks[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        assert img.size == (100, 80)
        _assert_gradient_colors(img)


def _gradient_img(w: int, h: int) -> Image.Image:
    """RGB 渐变: R 随 x 渐变, G 随 y 渐变, B=128。"""
    import numpy as np

    xs = (np.arange(w) * 255 // max(1, w - 1)).astype(np.uint8)
    ys = (np.arange(h) * 255 // max(1, h - 1)).astype(np.uint8)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = xs[None, :]
    arr[:, :, 1] = ys[:, None]
    arr[:, :, 2] = 128
    return Image.fromarray(arr, "RGB")


def _gradient_png(w: int = 100, h: int = 80) -> bytes:
    """构造 RGB 渐变 PNG 字节。"""
    buf = io.BytesIO()
    _gradient_img(w, h).save(buf, format="PNG")
    return buf.getvalue()


def _gradient_bytes(fmt: str, w: int = 100, h: int = 80) -> bytes:
    """构造指定格式的渐变图字节。"""
    buf = io.BytesIO()
    _gradient_img(w, h).save(buf, format=fmt)
    return buf.getvalue()


def _assert_gradient_colors(img: Image.Image, tol: int = 8, blue: int = 128):
    """断言渐变采样点色彩接近原图(允许编码误差)。"""
    w, h = img.size
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, h // 2)):
        expected = (
            int(x * 255 / (w - 1)),
            int(y * 255 / (h - 1)),
            blue,
        )
        got = img.convert("RGB").getpixel((x, y))
        assert all(abs(a - b) <= tol for a, b in zip(got, expected)), (
            f"({x},{y}) 期望 {expected} 实际 {got}"
        )


class TestPrepareImageForVision:
    """_prepare_image_for_vision 预处理参数测试。"""

    def test_params(self):
        """预处理参数: 小图阈值 512 / 大图上限 2048 / JPEG 质量 95。"""
        assert SMALL_IMAGE_MAX_SIDE == 512
        assert MAX_IMAGE_SIDE == 2048
        assert JPEG_QUALITY == 95
        assert SMALL_IMAGE_MAX_SIDE < MAX_IMAGE_SIDE

    def test_small_png_unchanged(self):
        """标准 RGB 小图 PNG 原样透传(零改动,大图/纯色图行为不回归)。"""
        raw = _gradient_png(100, 80)
        out, mime = _prepare_image_for_vision(raw)
        assert out == raw
        assert mime == ""  # 透传,调用方按扩展名猜 mime

    def test_small_jpeg_unchanged(self):
        raw = _gradient_bytes("JPEG", 100, 80)
        out, mime = _prepare_image_for_vision(raw)
        assert out == raw
        assert mime == ""

    def test_large_png_unchanged(self):
        """标准大图 PNG 原样透传,不缩放不重编码。"""
        raw = _gradient_png(MAX_IMAGE_SIDE + 500, 400)
        out, mime = _prepare_image_for_vision(raw)
        assert out == raw

    def test_small_bmp_to_png_original_size(self):
        """小图 BMP 转 PNG: 不缩放(保持 100x80)、不降色。"""
        raw = _gradient_bytes("BMP", 100, 80)
        out, mime = _prepare_image_for_vision(raw)
        assert mime == "image/png"
        img = Image.open(io.BytesIO(out))
        assert img.size == (100, 80)  # 小图不缩放
        _assert_gradient_colors(img)

    def test_small_gradient_not_scaled_and_not_quantized(self):
        """小图渐变: 尺寸不变,色彩数不降(不量化)。"""
        raw = _gradient_bytes("BMP", 100, 80)
        out, _ = _prepare_image_for_vision(raw)
        img = Image.open(io.BytesIO(out)).convert("RGB")
        assert img.size == (100, 80)
        colors = img.getcolors(maxcolors=100000)
        assert colors is not None and len(colors) > 500, "渐变被量化降色"

    def test_palette_png_converted_to_rgb(self):
        """调色板(P)模式归一化为 RGB: 不再降色,与调色板展开结果一致。"""
        src = _gradient_img(100, 80)
        pal = src.convert("P", palette=Image.ADAPTIVE, colors=256)
        buf = io.BytesIO()
        pal.save(buf, format="PNG")
        out, mime = _prepare_image_for_vision(buf.getvalue())
        assert mime == "image/png"
        img = Image.open(io.BytesIO(out))
        assert img.size == (100, 80)  # 小图不缩放
        assert img.mode in ("RGB", "RGBA")
        # 预处理只做模式展开,不额外降色 —— 与 PIL 直接展开调色板一致
        expected = pal.convert("RGB")
        assert img.convert("RGB").tobytes() == expected.tobytes()

    def test_cmyk_jpeg_converted_to_rgb(self):
        """CMYK JPEG 归一化为 RGB JPEG,尺寸不变。"""
        src = Image.new("CMYK", (100, 80))
        for x in range(100):
            for y in range(80):
                src.putpixel((x, y), (x * 2, y * 2, 100, 0))
        buf = io.BytesIO()
        src.save(buf, format="JPEG", quality=95)
        out, mime = _prepare_image_for_vision(buf.getvalue())
        assert mime == "image/jpeg"
        img = Image.open(io.BytesIO(out))
        assert img.size == (100, 80)
        assert img.mode == "RGB"

    def test_oversized_bmp_downscaled(self):
        """超大 BMP(需重编码)等比缩小到 MAX_IMAGE_SIDE 以内。"""
        w, h = MAX_IMAGE_SIDE * 2, 1000
        raw = _gradient_bytes("BMP", w, h)
        out, mime = _prepare_image_for_vision(raw)
        assert mime == "image/png"
        img = Image.open(io.BytesIO(out))
        assert max(img.size) <= MAX_IMAGE_SIDE
        # 等比: 宽缩到 MAX_IMAGE_SIDE,高按比例
        assert img.size[0] == MAX_IMAGE_SIDE

    def test_small_image_never_resized_even_when_converted(self):
        """小图即便需要格式转换也绝不缩放(SMALL_IMAGE_MAX_SIDE 保护)。"""
        raw = _gradient_bytes("BMP", 60, 40)
        out, _ = _prepare_image_for_vision(raw)
        img = Image.open(io.BytesIO(out))
        assert img.size == (60, 40)

    def test_unparseable_bytes_pass_through(self):
        """PIL 无法解析的字节原样透传(兼容假数据/损坏文件)。"""
        data = b"\x89PNG\x0d\x0a\x1a\x0a fake-data"
        out, mime = _prepare_image_for_vision(data)
        assert out == data
        assert mime == ""


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
        """IO 异常转 TOOL_ERROR 并带路径;bug 级异常原样上抛。"""
        p = tmp_path / "test.png"
        p.write_bytes(b"data")
        with patch("uniclaw.tools.media._read_image", side_effect=OSError("boom")):
            result = _read_media_impl(str(p))
        assert isinstance(result, str)
        assert "boom" in result
        assert str(p) in result

        with patch("uniclaw.tools.media._read_image", side_effect=RuntimeError("bug")):
            with pytest.raises(RuntimeError):
                _read_media_impl(str(p))


class TestReadMediaTool:
    """ReadMedia 工具测试。"""

    @pytest.mark.asyncio
    async def test_read_media(self, tmp_path):
        """异步读取媒体。"""
        p = tmp_path / "test.png"
        p.write_bytes(b"\x89PNG data")
        result = await ReadMedia(str(p), tool_runtime=ToolRuntime())
        assert isinstance(result, list)
        assert result[1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_read_media_missing(self, tmp_path):
        """文件不存在。"""
        result = await ReadMedia(str(tmp_path / "missing.png"), tool_runtime=ToolRuntime())
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
        result = await GenerateImage("一只猫", save_path=target, tool_runtime=ToolRuntime(config=self._config()))
        assert result == f"已保存到: {(tmp_path / 'out.png').resolve()}"
        mock_retrieve.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, return_value=["aGVsbG8="])
    async def test_save_base64(self, mock_gen, tmp_path):
        """base64 结果保存到文件。"""
        target = str(tmp_path / "out.png")
        result = await GenerateImage("一只猫", save_path=target, tool_runtime=ToolRuntime(config=self._config()))
        assert (tmp_path / "out.png").read_bytes() == b"hello"

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, return_value=["http://img/x.png"])
    async def test_no_path_url(self, mock_gen):
        """无 path 且 URL 结果返回多模态。"""
        result = await GenerateImage("一只猫", tool_runtime=ToolRuntime(config=self._config()))
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[1]["image_url"]["url"] == "http://img/x.png"

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, return_value=["aGVsbG8="])
    async def test_no_path_base64(self, mock_gen):
        """无 path 且 base64 结果返回 data URI。"""
        result = await GenerateImage("一只猫", tool_runtime=ToolRuntime(config=self._config()))
        assert isinstance(result, list)
        url = result[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, side_effect=OSError("boom"))
    async def test_exception(self, mock_gen):
        """生成异常(网络/IO)转 TOOL_ERROR,报错带 prompt 与 model。"""
        result = await GenerateImage("一只猫", tool_runtime=ToolRuntime(config=self._config()))
        assert isinstance(result, str)
        assert "图片生成失败" in result
        assert "一只猫" in result
        assert "dall-e-3" in result

    @pytest.mark.asyncio
    @patch("uniclaw.provider.openai_provider.agenerate_image", new_callable=AsyncMock, return_value=[])
    async def test_empty_result(self, mock_gen):
        """空结果。"""
        result = await GenerateImage("一只猫", tool_runtime=ToolRuntime(config=self._config()))
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


# ── 空文件/远端 400 的本地拦截与友好提示 ─────────────────────


class TestEmptyAndInvalidMedia:
    """空媒体不得把空 base64 送远端;远端参数拒绝转友好提示。"""

    def test_empty_image_rejected_locally(self, tmp_path):
        """空 PNG 本地拦截,报"文件为空",不再发空 base64 挨远端 400。"""
        p = tmp_path / "empty.png"
        p.write_bytes(b"")
        result = _read_media_impl(str(p))
        assert isinstance(result, str)
        assert "文件为空" in result
        assert "0 字节" in result

    def test_empty_audio_rejected_locally(self, tmp_path):
        p = tmp_path / "empty.wav"
        p.write_bytes(b"")
        result = _read_media_impl(str(p))
        assert "文件为空" in result

    def test_empty_video_rejected_locally(self, tmp_path):
        p = tmp_path / "empty.mp4"
        p.write_bytes(b"")
        result = _read_media_impl(str(p))
        assert "文件为空" in result

    @pytest.mark.asyncio
    async def test_as_text_api_badrequest_friendly(self, tmp_path):
        """远端 400 参数拒绝(如 base64 无效)转友好提示,不裸抛原始错误。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from uniclaw.tools.base import ToolRuntime
        from uniclaw.tools.media import ReadMedia

        class BadRequestError(Exception):
            pass

        p = tmp_path / "img.png"
        p.write_bytes(b"\x89PNG data")
        config = MagicMock()
        config.multimodal_model_name = ["vision-model"]
        rt = ToolRuntime(config=config)

        with patch(
            "uniclaw.provider.fallback.achat",
            new=AsyncMock(
                side_effect=BadRequestError(
                    "Error code: 400 - the provided base64 data is not valid"
                )
            ),
        ):
            result = await ReadMedia(
                file_path=str(p), as_text=True, tool_runtime=rt
            )

        assert "TOOL_ERROR" in result
        assert "拒绝了该媒体内容" in result
        assert "损坏" in result or "无效" in result

    @pytest.mark.asyncio
    async def test_as_text_unexpected_error_not_swallowed(self, tmp_path):
        """非 API 拒绝类异常(bug 级)不套友好提示,由 Tool.__call__ 兜底透出。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from uniclaw.tools.base import ToolRuntime
        from uniclaw.tools.media import ReadMedia

        p = tmp_path / "img.png"
        p.write_bytes(b"\x89PNG data")
        config = MagicMock()
        config.multimodal_model_name = ["vision-model"]
        rt = ToolRuntime(config=config)

        with patch(
            "uniclaw.provider.fallback.achat",
            new=AsyncMock(side_effect=KeyError("boom")),
        ), patch(
            "uniclaw.console.ui.err", new=AsyncMock()
        ):
            result = await ReadMedia(file_path=str(p), as_text=True, tool_runtime=rt)

        # 不套"拒绝了该媒体内容"的外衣 —— bug 级原样透出给兜底
        assert "拒绝了该媒体内容" not in result
        assert "boom" in result

class TestBillingErrorAttribution:
    def test_is_billing_error(self):
        from uniclaw.provider.error_classifier import is_billing_error

        class Err(Exception):
            pass

        assert is_billing_error(Err("Error code: 402 - Payment Required"))
        assert is_billing_error(Err("insufficient_quota: 余额不足"))
        assert is_billing_error(Err("account balance is too low"))
        assert not is_billing_error(Err("invalid image base64 content"))

    @pytest.mark.asyncio
    async def test_readmedia_billing_message(self, tmp_path):
        """402 归因到账户余额,不说"文件损坏/模型拒绝"。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from uniclaw.tools.base import ToolRuntime
        from uniclaw.tools.media import ReadMedia

        class PayErr(Exception):
            pass

        p = tmp_path / "img.png"
        p.write_bytes(b"\x89PNG data")
        config = MagicMock()
        config.multimodal_model_name = ["mm"]
        rt = ToolRuntime(config=config)
        with patch(
            "uniclaw.provider.fallback.achat",
            AsyncMock(side_effect=PayErr("Error code: 402 - Payment Required")),
        ):
            result = await ReadMedia(file_path=str(p), as_text=True, tool_runtime=rt)

        assert "余额" in result or "配额" in result
        assert "损坏" not in result
