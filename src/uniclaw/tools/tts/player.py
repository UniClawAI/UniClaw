from __future__ import annotations

import asyncio

import numpy as np


class StreamPlayer:
    """流式音频播放器,使用队列缓冲实现无缝播放。"""

    def __init__(self, sample_rate: int | None = None):
        import sounddevice as sd
        import queue

        self._queue: queue.Queue[np.ndarray | None] = queue.Queue()
        if sample_rate:
            self._stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
        else:
            self._stream = None
        self._buffer = np.array([], dtype=np.float32)
        self._start = False

    def _callback(self, outdata, frames, _time, _status):
        """sounddevice 回调,从缓冲区填充数据。"""
        # 从队列取数据补充缓冲区
        while len(self._buffer) < frames:
            try:
                chunk = self._queue.get_nowait()
                if chunk is None:
                    self._start = False
                    break
                self._buffer = np.concatenate([self._buffer, chunk])
            except Exception:
                break

        # 填充输出
        available = min(frames, len(self._buffer))
        outdata[:available, 0] = self._buffer[:available]
        if available < frames:
            outdata[available:, 0] = 0
        self._buffer = self._buffer[available:]

    def start(self):
        """开始播放。"""
        if self._stream:
            self._stream.start()
        self._start = True

    def write(self, pcm: np.ndarray, sample_rate: int | None = None):
        """写入 PCM 数据块。"""
        import sounddevice as sd

        if sample_rate and self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            if self._start:
                self._stream.start()
        self._queue.put(pcm.astype(np.float32))

    async def stop(self):
        """停止播放,等待缓冲区播完。"""
        if self._stream is None:
            self._start = False
            return
        self._queue.put(None)  # 结束标记
        while self._start:
            await asyncio.sleep(0.01)
        self._stream.stop()
        self._stream.close()

    async def __aenter__(self):
        self.start()
        return self

    async def __aexit__(self, *args):
        await self.stop()
