"""tools/send_file.py 测试 — 文件发送工具(原本 28% 覆盖)。

WebUI 模式:文本文件→[show_doc:...],其他→[file_download:...]。
非 WebUI: 用 wechat bot 直接发送;Console 模式不注册。
下载映射注册/查询/过期清理单独测试。
"""

import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.send_file import (
    send_file,
    register_download,
    get_download,
    _is_text_file,
    _file_downloads,
    get_tools,
    get_all_tools,
    DEFAULT_EXPIRE_MINUTES,
    MAX_PREVIEW_SIZE,
)
from uniclaw.utils.constants import TOOL_ERROR


@pytest.fixture(autouse=True)
def _clean_downloads():
    """每个测试前后清空下载映射。"""
    _file_downloads.clear()
    yield
    _file_downloads.clear()


def _rt(config=None) -> ToolRuntime:
    return ToolRuntime(config=config)


def _cfg(is_webui=False):
    c = MagicMock()
    c.is_webui = is_webui
    c.is_console = not is_webui
    c.root_dir = Path("/tmp")
    return c


class TestRegisterDownload:
    def test_register_generates_id(self):
        file_id, expires_at = register_download(Path("/tmp/a.txt"), "a.txt")
        assert len(file_id) == 12
        assert expires_at > int(time.time())
        assert file_id in _file_downloads
        assert _file_downloads[file_id]["name"] == "a.txt"

    @patch("uniclaw.tools.send_file.time.time")
    def test_expire_minutes(self, mock_time):
        mock_time.return_value = 1000
        _, expires_at = register_download(
            Path("/tmp/a.txt"), "a", expire_minutes=5
        )
        assert expires_at == 1000 + 5 * 60

    def test_cleanup_expired_on_register(self):
        register_download(Path("/tmp/a.txt"), "a")
        # 手工把有效期改成过去
        file_id = next(iter(_file_downloads))
        _file_downloads[file_id]["expires_at"] = int(time.time()) - 1
        register_download(Path("/tmp/b.txt"), "b")
        assert file_id not in _file_downloads  # 过期已清理


class TestGetDownload:
    def test_get_existing(self):
        register_download(Path("/tmp/a.txt"), "a")
        file_id = next(iter(_file_downloads))
        got = get_download(file_id)
        assert got is not None
        assert got["path"] == Path("/tmp/a.txt")

    def test_get_missing(self):
        assert get_download("nonexistent") is None

    def test_get_expired(self):
        register_download(Path("/tmp/a.txt"), "a")
        file_id = next(iter(_file_downloads))
        _file_downloads[file_id]["expires_at"] = int(time.time()) - 1
        assert get_download(file_id) is None


class TestIsTextFile:
    def test_utf8(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("你好 world", encoding="utf-8")
        assert _is_text_file(f) is True

    def test_binary_nul(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"\x00\x01\x02")
        assert _is_text_file(f) is False

    def test_non_utf8(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"\xff\xfe\xfd\xfc")
        assert _is_text_file(f) is False

    def test_too_large(self, tmp_path):
        f = tmp_path / "big.bin"
        f.write_bytes(b"x" * (MAX_PREVIEW_SIZE + 1))
        assert _is_text_file(f) is False

    def test_missing_file(self, tmp_path):
        assert _is_text_file(tmp_path / "nope.txt") is False


class TestSendFile:
    async def test_no_config(self):
        result = await send_file.func(file_path="/tmp/a", tool_runtime=_rt(None))
        assert TOOL_ERROR in result

    async def test_relative_no_root(self):
        c = _cfg()
        c.root_dir = None
        result = await send_file.func(file_path="a.txt", tool_runtime=_rt(c))
        assert "无工作目录" in result

    async def test_missing_file(self):
        c = _cfg()
        result = await send_file.func(
            file_path="/nonexistent/xyz.txt", tool_runtime=_rt(c)
        )
        assert "文件不存在" in result

    async def test_is_dir(self, tmp_path):
        c = _cfg()
        c.root_dir = tmp_path
        result = await send_file.func(file_path=str(tmp_path), tool_runtime=_rt(c))
        assert "是一个目录" in result

    async def test_webui_text_file_show_doc(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("markdown 内容", encoding="utf-8")
        c = _cfg(is_webui=True)
        c.root_dir = tmp_path
        result = await send_file.func(file_path=str(f), tool_runtime=_rt(c))
        assert result.startswith("[show_doc:")
        assert ":doc.txt:" in result

    async def test_webui_binary_file_download(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01")
        c = _cfg(is_webui=True)
        c.root_dir = tmp_path
        result = await send_file.func(file_path=str(f), tool_runtime=_rt(c))
        assert result.startswith("[file_download:")
        assert ":data.bin:" in result

    async def test_webui_custom_name(self, tmp_path):
        f = tmp_path / "orig.bin"
        f.write_bytes(b"\x00\x01")
        c = _cfg(is_webui=True)
        c.root_dir = tmp_path
        result = await send_file.func(
            file_path=str(f), file_name="renamed.bin", tool_runtime=_rt(c)
        )
        assert ":renamed.bin:" in result

    async def test_wechat_success(self, tmp_path):
        f = tmp_path / "file.png"
        f.write_bytes(b"png")
        c = _cfg(is_webui=False)
        c.root_dir = tmp_path
        bot = MagicMock()
        bot.reply_file = MagicMock()
        c.wechat_ctx = bot
        result = await send_file.func(file_path=str(f), tool_runtime=_rt(c))
        assert "文件已发送" in result
        args, kwargs = bot.reply_file.call_args
        assert kwargs["file_name"] == "file.png"

    async def test_wechat_error(self, tmp_path):
        f = tmp_path / "file.png"
        f.write_bytes(b"png")
        c = _cfg(is_webui=False)
        c.root_dir = tmp_path
        bot = MagicMock()
        bot.reply_file = MagicMock(side_effect=RuntimeError("upload failed"))
        c.wechat_ctx = bot
        result = await send_file.func(file_path=str(f), tool_runtime=_rt(c))
        assert TOOL_ERROR in result
        assert "upload failed" in result

    async def test_console_unsupported(self, tmp_path):
        f = tmp_path / "file.png"
        f.write_bytes(b"png")
        c = _cfg(is_webui=False)
        c.root_dir = tmp_path
        c.wechat_ctx = None
        result = await send_file.func(file_path=str(f), tool_runtime=_rt(c))
        assert "不支持当前模式" in result


class TestGetTools:
    def test_console_not_registered(self):
        c = _cfg(is_webui=False)
        c.record_unavailable_tools = MagicMock()
        c.clear_unavailable_tools = MagicMock()
        assert get_tools(c) == []
        c.record_unavailable_tools.assert_called_once()

    def test_console_not_registered_no_config(self):
        # config=None 时始终返回工具
        assert len(get_tools(None)) == 1

    def test_normal_registered(self):
        c = _cfg(is_webui=True)
        c.clear_unavailable_tools = MagicMock()
        assert len(get_tools(c)) == 1
        assert get_all_tools() == [send_file]