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
