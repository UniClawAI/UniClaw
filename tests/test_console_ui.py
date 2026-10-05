"""console/ui.err 的 e 参数测试 — 异常堆栈仅落日志,不进 UI 输出。"""

from pathlib import Path
from types import SimpleNamespace

from uniclaw.console.ui import err
from uniclaw.utils import logger as logger_mod


def _cfg(root: Path) -> SimpleNamespace:
    """最小可用的 config 替身: 无输出回调,root_dir 指向临时目录。"""
    return SimpleNamespace(
        root_dir=root, root_config=None, output_callback=None, wechat_ctx=None
    )


def _release_handlers():
    """关闭并清空 handler 缓存,避免 Windows 上临时目录删除失败。"""
    for h in logger_mod._handler_cache.values():
        h.close()
    logger_mod._handler_cache.clear()


class TestErrException:
    async def test_exception_written_to_log_dir(self, tmp_path):
        cfg = _cfg(tmp_path)
        try:
            raise ValueError("详细堆栈")
        except ValueError as e:
            await err("摘要信息", cfg, e=e)
        try:
            log = Path(logger_mod._log_dir_for(cfg.root_dir)) / "uniclaw.agent.log"
            text = log.read_text(encoding="utf-8")
            assert "摘要信息" in text
            assert "ValueError" in text
            assert "详细堆栈" in text
            assert "Traceback" in text
        finally:
            _release_handlers()

    async def test_no_exception_writes_nothing(self, tmp_path):
        cfg = _cfg(tmp_path)
        await err("摘要信息", cfg)
        try:
            log = Path(logger_mod._log_dir_for(cfg.root_dir)) / "uniclaw.agent.log"
            assert not log.exists()
        finally:
            _release_handlers()


class TestErrNeverRaises:
    """info/ok/warn/err 是最后的错误出口,不能因 root_dir 取不到而自己抛出。

    回归测试: 旧实现在 except 分支里再访问 config.root_dir,而 root_dir 是
    便捷属性(链到 current_agent.session.root_dir),链上任一环未就绪都会抛出
    —— 二次异常把真正的错误掩盖掉、甚至打断调用方。
    """

    async def test_root_dir_property_unavailable(self):
        """root_dir 属性链未就绪(如未挂载 agent)时退化到用户级目录。"""

        class _Cfg:
            @property
            def root_dir(self):
                raise AttributeError("current_agent 未初始化")

        try:
            await err("摘要信息", _Cfg(), e=ValueError("x"))  # 不应抛出
        finally:
            _release_handlers()

    async def test_root_dir_none_falls_back_to_user_dir(self):
        """root_dir 为 None 时退化到用户级目录。"""
        cfg = SimpleNamespace(root_dir=None, output_callback=None)
        try:
            await err("摘要信息", cfg, e=ValueError("x"))  # 不应抛出
        finally:
            _release_handlers()

    async def test_config_is_none(self):
        """config 为 None 时不能抛出。"""
        try:
            await err("摘要信息", None, e=ValueError("x"))  # 不应抛出
        finally:
            _release_handlers()

    async def test_warn_and_info_also_never_raise(self):
        """warn/info 同样不能抛出。"""
        from uniclaw.console.ui import info, warn

        cfg = SimpleNamespace(root_dir=None, output_callback=None, wechat_ctx=None)
        await warn("警告", cfg)
        await info("信息", cfg)
