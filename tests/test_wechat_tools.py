"""tools/wechat.py 工具测试 — 微信消息工具(原本 18% 覆盖)。

覆盖:错误格式化、bot 解析、路径解析、发送文字/图片/文件的成功与失败分支。
所有 iLink 依赖用 mock 隔离,不连真实微信。
"""

import pytest
import requests
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.wechat import (
    wechat_list_contacts,
    wechat_send_text,
    wechat_send_image,
    wechat_send_file,
    _format_send_error,
    _resolve_bot,
    _resolve_path,
    get_tools,
    get_all_tools,
)
from uniclaw.ilink_bot.exceptions import ApiError, AuthError, MediaError, NoContextError
from uniclaw.utils.constants import TOOL_ERROR


def _rt(config=None) -> ToolRuntime:
    return ToolRuntime(config=config)


class TestGetTools:
    def test_tools_registered(self):
        names = [t.name for t in get_tools()]
        assert names == [
            "wechat_list_contacts",
            "wechat_send_text",
            "wechat_send_image",
            "wechat_send_file",
        ]

    def test_get_all_tools_same(self):
        assert get_all_tools() == get_tools()


class TestFormatSendError:
    def _ctx(self, e):
        return _format_send_error(e, "文字消息发送", "bot1")

    def test_auth_error(self):
        msg = self._ctx(AuthError("expired"))
        assert msg.startswith(TOOL_ERROR)
        assert "登录已过期" in msg
        assert "bot1" in msg

    def test_media_error(self):
        msg = self._ctx(MediaError("upload failed"))
        assert "媒体上传失败" in msg

    def test_api_error_with_code(self):
        msg = self._ctx(ApiError("bad request", code=400))
        assert "微信服务器拒绝" in msg
        assert "(错误码 400)" in msg
        assert "重新给机器人发一条消息" in msg

    def test_api_error_without_code(self):
        msg = self._ctx(ApiError("bad"))
        assert "(错误码" not in msg

    def test_connection_error(self):
        msg = self._ctx(requests.ConnectionError("refused"))
        assert "网络连接异常" in msg

    def test_timeout(self):
        msg = self._ctx(requests.Timeout("slow"))
        assert "网络连接异常" in msg

    def test_unknown_exception(self):
        msg = self._ctx(ValueError("boom"))
        assert "ValueError" in msg
        assert "boom" in msg

    def test_no_bot_name(self):
        msg = _format_send_error(AuthError("expired"), "文字消息发送")
        assert "(机器人:" not in msg


class TestResolveBot:
    """bot 解析:按名 → 会话上下文 → 首个已登录。"""

    def _bot(self, logged_in=True, name="bot1"):
        b = MagicMock()
        b.name = name
        b.is_logged_in = logged_in
        return b

    def test_by_name_found(self):
        bot = self._bot()
        with patch("uniclaw.ilink_bot.manager.BotManager") as MockMgr:
            MockMgr.return_value.get.return_value = bot
            got, err = _resolve_bot(MagicMock(), "bot1")
        assert got is bot
        assert err is None

    def test_by_name_not_found(self):
        with patch("uniclaw.ilink_bot.manager.BotManager") as MockMgr:
            MockMgr.return_value.get.return_value = None
            got, err = _resolve_bot(MagicMock(), "nosuch")
        assert got is None
        assert "未找到名为 'nosuch'" in err

    def test_by_name_not_logged_in(self):
        bot = self._bot(logged_in=False)
        with patch("uniclaw.ilink_bot.manager.BotManager") as MockMgr:
            MockMgr.return_value.get.return_value = bot
            got, err = _resolve_bot(MagicMock(), "bot1")
        assert got is None
        assert "未登录" in err

    def test_from_wechat_ctx(self):
        bot = self._bot()
        config = MagicMock()
        config.wechat_ctx = bot
        got, err = _resolve_bot(config)
        assert got is bot
        assert err is None

    def test_wechat_ctx_not_logged_falls_to_manager(self):
        ctx_bot = self._bot(logged_in=False)
        first = self._bot(logged_in=True, name="first")
        config = MagicMock()
        config.wechat_ctx = ctx_bot
        with patch("uniclaw.ilink_bot.manager.BotManager") as MockMgr:
            MockMgr.return_value.bots = [ctx_bot, first]
            got, err = _resolve_bot(config)
        assert got is first
        assert err is None

    def test_no_logged_in_bot(self):
        config = MagicMock()
        config.wechat_ctx = self._bot(logged_in=False)
        with patch("uniclaw.ilink_bot.manager.BotManager") as MockMgr:
            MockMgr.return_value.bots = [self._bot(logged_in=False)]
            got, err = _resolve_bot(config)
        assert got is None
        assert "没有可用的已登录微信机器人" in err


class TestResolvePath:
    def test_absolute_existing(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        got, err = _resolve_path(str(f), MagicMock())
        assert got == f
        assert err == ""

    def test_relative_with_root(self, tmp_path):
        f = tmp_path / "b.txt"
        f.write_text("x", encoding="utf-8")
        config = MagicMock()
        config.root_dir = tmp_path
        got, err = _resolve_path("b.txt", config)
        assert got == f

    def test_relative_no_root(self):
        config = MagicMock()
        config.root_dir = None
        got, err = _resolve_path("b.txt", config)
        assert got is None
        assert "无工作目录" in err

    def test_not_exists(self, tmp_path):
        config = MagicMock()
        config.root_dir = tmp_path
        got, err = _resolve_path("missing.txt", config)
        assert got is None
        assert "文件不存在" in err


class TestWeChatListContacts:
    def test_empty_config(self):
        result = wechat_list_contacts.func(tool_runtime=_rt(None))
        assert TOOL_ERROR in result

    async def test_no_bots(self):
        with patch("uniclaw.ilink_bot.manager.BotManager") as MockMgr:
            MockMgr.return_value.bots = []
            result = wechat_list_contacts.func(tool_runtime=_rt(MagicMock()))
        assert "没有注册任何微信机器人" in result

    async def test_no_logged_in(self):
        bot = MagicMock()
        bot.is_logged_in = False
        with patch("uniclaw.ilink_bot.manager.BotManager") as MockMgr:
            MockMgr.return_value.bots = [bot]
            result = wechat_list_contacts.func(tool_runtime=_rt(MagicMock()))
        assert "没有已登录的微信机器人" in result

    async def test_lists_active(self):
        b1 = MagicMock()
        b1.name = "bot1"
        b1.is_logged_in = True
        b2 = MagicMock()
        b2.name = "bot2"
        b2.is_logged_in = True
        inactive = MagicMock()
        inactive.name = "inactive"
        inactive.is_logged_in = False
        with patch("uniclaw.ilink_bot.manager.BotManager") as MockMgr:
            MockMgr.return_value.bots = [b1, inactive, b2]
            result = wechat_list_contacts.func(tool_runtime=_rt(MagicMock()))
        assert "- bot1" in result
        assert "- bot2" in result
        assert "inactive" not in result


class TestWeChatSendText:
    async def test_empty_text(self):
        config = MagicMock()
        result = await wechat_send_text.func(
            text="   ", tool_runtime=_rt(config)
        )
        assert TOOL_ERROR in result
        assert "不能为空" in result

    async def test_no_config(self):
        result = await wechat_send_text.func(text="hi", tool_runtime=_rt(None))
        assert TOOL_ERROR in result

    async def test_success(self):
        bot = MagicMock()
        bot.name = "bot1"
        bot.reply_text = MagicMock()
        with patch(
            "uniclaw.tools.wechat._resolve_bot",
            return_value=(bot, None),
        ):
            result = await wechat_send_text.func(
                text="你好", tool_runtime=_rt(MagicMock())
            )
        assert result == "文字消息已发送"
        bot.reply_text.assert_called_once_with("你好")

    async def test_no_context_error(self):
        bot = MagicMock()
        bot.name = "bot1"
        bot.reply_text = MagicMock(side_effect=NoContextError())
        with patch(
            "uniclaw.tools.wechat._resolve_bot",
            return_value=(bot, None),
        ):
            result = await wechat_send_text.func(
                text="你好", tool_runtime=_rt(MagicMock())
            )
        assert TOOL_ERROR in result
        assert "没有对话用户" in result

    async def test_auth_error(self):
        bot = MagicMock()
        bot.name = "bot1"
        bot.reply_text = MagicMock(side_effect=AuthError("expired"))
        with patch(
            "uniclaw.tools.wechat._resolve_bot",
            return_value=(bot, None),
        ):
            result = await wechat_send_text.func(
                text="你好", tool_runtime=_rt(MagicMock())
            )
        assert "登录已过期" in result

    async def test_bot_resolve_error(self):
        err = f"{TOOL_ERROR}: 没有可用的已登录微信机器人"
        with patch("uniclaw.tools.wechat._resolve_bot", return_value=(None, err)):
            result = await wechat_send_text.func(
                text="你好", tool_runtime=_rt(MagicMock())
            )
        assert result == err


class TestWeChatSendImage:
    async def test_no_config(self):
        result = await wechat_send_image.func(
            image_path="x.png", tool_runtime=_rt(None)
        )
        assert TOOL_ERROR in result

    async def test_missing_file(self):
        config = MagicMock()
        config.root_dir = Path("/tmp")
        result = await wechat_send_image.func(
            image_path="nope.png", tool_runtime=_rt(config)
        )
        assert TOOL_ERROR in result
        assert "文件不存在" in result

    async def test_success(self, tmp_path):
        img = tmp_path / "pic.png"
        img.write_bytes(b"fake-png")
        config = MagicMock()
        config.root_dir = tmp_path
        bot = MagicMock()
        bot.name = "bot1"
        bot.reply_image = MagicMock()
        with patch("uniclaw.tools.wechat._resolve_bot", return_value=(bot, None)):
            result = await wechat_send_image.func(
                image_path=str(img), caption="说明", tool_runtime=_rt(config)
            )
        assert "图片已发送" in result
        bot.reply_image.assert_called_once()
        args, kwargs = bot.reply_image.call_args
        assert "pic.png" in str(args[0])
        assert kwargs["caption"] == "说明"

    async def test_caption_empty_passed_none(self, tmp_path):
        img = tmp_path / "pic.png"
        img.write_bytes(b"fake-png")
        config = MagicMock()
        config.root_dir = tmp_path
        bot = MagicMock()
        bot.reply_image = MagicMock()
        with patch("uniclaw.tools.wechat._resolve_bot", return_value=(bot, None)):
            result = await wechat_send_image.func(
                image_path=str(img), caption="", tool_runtime=_rt(config)
            )
        kwargs = bot.reply_image.call_args.kwargs
        assert kwargs["caption"] is None


class TestWeChatSendFile:
    async def test_no_config(self):
        result = await wechat_send_file.func(
            file_path="x.txt", tool_runtime=_rt(None)
        )
        assert TOOL_ERROR in result

    async def test_missing_file(self):
        config = MagicMock()
        config.root_dir = Path("/tmp")
        result = await wechat_send_file.func(
            file_path="nope.txt", tool_runtime=_rt(config)
        )
        assert "文件不存在" in result

    async def test_directory_rejected(self, tmp_path):
        sub = tmp_path / "adir"
        sub.mkdir()
        config = MagicMock()
        config.root_dir = tmp_path
        result = await wechat_send_file.func(
            file_path=str(sub), tool_runtime=_rt(config)
        )
        assert "是一个目录" in result

    async def test_success_custom_name(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"data")
        config = MagicMock()
        config.root_dir = tmp_path
        bot = MagicMock()
        bot.name = "bot1"
        bot.reply_file = MagicMock()
        with patch("uniclaw.tools.wechat._resolve_bot", return_value=(bot, None)):
            result = await wechat_send_file.func(
                file_path=str(f), file_name="renamed.bin", tool_runtime=_rt(config)
            )
        assert "文件已发送: renamed.bin" in result
        kwargs = bot.reply_file.call_args.kwargs
        assert kwargs["file_name"] == "renamed.bin"

    async def test_success_default_name(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"data")
        config = MagicMock()
        config.root_dir = tmp_path
        bot = MagicMock()
        bot.reply_file = MagicMock()
        with patch("uniclaw.tools.wechat._resolve_bot", return_value=(bot, None)):
            result = await wechat_send_file.func(
                file_path=str(f), tool_runtime=_rt(config)
            )
        assert "文件已发送: data.bin" in result
        kwargs = bot.reply_file.call_args.kwargs
        assert kwargs["file_name"] is None