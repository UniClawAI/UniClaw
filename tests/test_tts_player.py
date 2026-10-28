"""tools/tts/player.py 测试 — 流式音频播放器(原本 0% 覆盖)。

用 numpy 构造 PCM 数据验证 _callback 的缓冲区填充与消耗逻辑,
sounddevice 用 mock 隔离(不真正打开音频设备)。
"""

import sys
import numpy as np
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from uniclaw.tools.tts.player import StreamPlayer


@pytest.fixture(autouse=True)
def _mock_sounddevice():
    """把 mock 注入 sys.modules —— player 内 `import sounddevice` 是延迟导入,
    只能通过 sys.modules 拦截,不能 patch player 模块属性。"""
    fake_sd = MagicMock()
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        yield fake_sd


def _make_player(sample_rate=16000):
    """构造 StreamPlayer:mock sounddevice,避免真正打开音频设备。"""
    return StreamPlayer(sample_rate=sample_rate)


class TestStreamPlayerInit:
    def test_with_sample_rate_creates_stream(self, _mock_sounddevice):
        player = _make_player(16000)
        assert player._stream is not None
        _mock_sounddevice.OutputStream.assert_called_once()
        _, kwargs = _mock_sounddevice.OutputStream.call_args
        assert kwargs["samplerate"] == 16000
        assert kwargs["channels"] == 1
        assert kwargs["dtype"] == "float32"

    def test_no_sample_rate_no_stream(self, _mock_sounddevice):
        player = _make_player(None)
        assert player._stream is None
        assert player._start is False


class TestWrite:
    def test_write_puts_float32(self, _mock_sounddevice):
        player = _make_player(None)  # 无 stream
        player.write(np.array([1, 2, 3], dtype=np.int16))
        chunk = player._queue.get_nowait()
        assert chunk.dtype == np.float32

    def test_write_initializes_stream_on_demand(self, _mock_sounddevice):
        player = _make_player(None)  # 无流
        player.write(np.array([1.0, 2.0]), sample_rate=8000)
        _mock_sounddevice.OutputStream.assert_called_once()
        # _start 初始 False,不自动 start
        player._stream.start.assert_not_called()

    def test_write_to_existing_stream(self, _mock_sounddevice):
        player = _make_player(16000)
        player.write(np.array([1.0, 2.0]))
        assert _mock_sounddevice.OutputStream.call_count == 1  # 不重复创建


class TestCallback:
    """缓冲区填充与消耗。"""

    def test_fills_from_queue(self, _mock_sounddevice):
        player = _make_player(16000)
        player._queue.put(np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32))
        outdata = np.zeros((4, 1), dtype=np.float32)
        player._callback(outdata, 4, None, None)
        assert outdata[0, 0] == 1.0
        assert outdata[3, 0] == 1.0
        assert len(player._buffer) == 0  # 缓冲区耗尽

    def test_partial_pads_zeros(self, _mock_sounddevice):
        player = _make_player(16000)
        player._queue.put(np.array([1.0], dtype=np.float32))
        outdata = np.zeros((4, 1), dtype=np.float32)
        player._callback(outdata, 4, None, None)
        assert outdata[0, 0] == 1.0
        assert outdata[1, 0] == 0.0  # 补零
        assert outdata[3, 0] == 0.0

    def test_consumes_multiple_chunks(self, _mock_sounddevice):
        player = _make_player(16000)
        player._queue.put(np.array([1.0, 1.0], dtype=np.float32))
        player._queue.put(np.array([2.0, 2.0], dtype=np.float32))
        outdata = np.zeros((4, 1), dtype=np.float32)
        player._callback(outdata, 4, None, None)
        assert outdata[0, 0] == 1.0
        assert outdata[1, 0] == 1.0
        assert outdata[2, 0] == 2.0
        assert outdata[3, 0] == 2.0

    def test_none_marker_stops(self, _mock_sounddevice):
        player = _make_player(16000)
        player._start = True
        player._queue.put(None)
        outdata = np.zeros((2, 1), dtype=np.float32)
        player._callback(outdata, 2, None, None)
        assert player._start is False

    def test_empty_queue_pads_all_zeros(self, _mock_sounddevice):
        player = _make_player(16000)
        outdata = np.ones((3, 1), dtype=np.float32)
        player._callback(outdata, 3, None, None)
        assert np.all(outdata == 0)


class TestStart:
    def test_start_starts_stream(self, _mock_sounddevice):
        player = _make_player(16000)
        player.start()
        player._stream.start.assert_called_once()
        assert player._start is True

    def test_start_no_stream(self, _mock_sounddevice):
        player = _make_player(None)
        player.start()
        assert player._start is True  # 无 stream 也置标记


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_no_stream(self, _mock_sounddevice):
        player = _make_player(None)
        await player.stop()
        assert player._start is False

    @pytest.mark.asyncio
    async def test_stop_stream_stops_and_closes(self, _mock_sounddevice):
        player = _make_player(16000)
        # 结束标记被放进队列;回调(None)会把 _start 置 False,
        # 这里模拟回调已消费:置 _start=False,while 立即退出
        player._queue.put(None)
        player._start = False
        with patch(
            "uniclaw.tools.tts.player.asyncio.sleep",
            new_callable=MagicMock,
        ):
            await player.stop()
        player._stream.stop.assert_called_once()
        player._stream.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_context_manager(self, _mock_sounddevice):
        player = _make_player(16000)
        with patch(
            "uniclaw.tools.tts.player.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            async with player:
                # 模拟回调已消费结束标记(真实场景中 _callback 遇 None 会置 False),
                # 否则 stop() 的 while _start 会死循环
                player._start = False
        assert player._stream is not None
        player._stream.start.assert_called_once()
        player._stream.stop.assert_called_once()
        player._stream.close.assert_called_once()