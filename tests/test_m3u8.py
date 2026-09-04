"""M3U8 下载引擎的单元测试。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.tools.download.m3u8 import (
    DEFAULT_M3U8_CONCURRENCY,
    M3u8Downloader,
    M3u8Key,
    M3u8Progress,
    M3u8Record,
    M3u8Segment,
    M3u8Status,
    M3u8Variant,
    _decrypt_aes128,
    _looks_like_html,
    _parse_attributes,
    _resolve_url,
    _safe_int,
    guess_video_name,
)

# ==================== guess_video_name 测试 ====================


class TestGuessVideoName:
    """guess_video_name 函数测试。"""

    def test_from_url(self):
        assert (
            guess_video_name("https://cdn.example.com/videos/movie/index.m3u8")
            == "movie"
        )

    def test_fallback_when_playlist_name(self):
        name = guess_video_name("https://cdn.example.com/index.m3u8")
        assert name  # 应为哈希,非空

    def test_resolution_segment(self):
        assert (
            guess_video_name("https://cdn.example.com/1080p/playlist.m3u8") == "1080p"
        )


# ==================== _parse_attributes 测试 ====================


class TestParseAttributes:
    """_parse_attributes 函数测试。"""

    def test_quoted_uri(self):
        attrs = _parse_attributes(
            'METHOD=AES-128,URI="key.key",IV=0x00000000000000000000000000000001'
        )
        assert attrs["METHOD"] == "AES-128"
        assert attrs["URI"] == "key.key"
        assert attrs["IV"] == "0x00000000000000000000000000000001"

    def test_numeric(self):
        attrs = _parse_attributes("BANDWIDTH=5000000,RESOLUTION=1920x1080")
        assert attrs["BANDWIDTH"] == "5000000"
        assert attrs["RESOLUTION"] == "1920x1080"


# ==================== _resolve_url 测试 ====================


class TestResolveUrl:
    """_resolve_url 函数测试。"""

    def test_absolute(self):
        assert _resolve_url("http://a/b/", "http://x/y.ts") == "http://x/y.ts"

    def test_https(self):
        assert _resolve_url("http://a/b/", "https://x/y.ts") == "https://x/y.ts"

    def test_relative(self):
        assert _resolve_url("http://a/b/index.m3u8", "seg1.ts") == "http://a/b/seg1.ts"

    def test_relative_base_dir(self):
        assert _resolve_url("http://a/b/", "sub/seg1.ts") == "http://a/b/sub/seg1.ts"


# ==================== _decrypt_aes128 测试 ====================


class TestDecryptAes128:
    """_decrypt_aes128 AES-128-CBC 解密测试。"""

    def _encrypt(self, data: bytes, key: bytes, iv: bytes) -> bytes:
        """用 cryptography 对数据进行 AES-128-CBC 加密并补 PKCS7 填充。"""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding

        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def test_roundtrip(self):
        key = b"0123456789abcdef"  # 16 字节
        iv = b"fedcba9876543210"  # 16 字节
        plaintext = b"hello m3u8 segment data"
        encrypted = self._encrypt(plaintext, key, iv)
        assert _decrypt_aes128(encrypted, key, iv) == plaintext

    def test_wrong_key_fails(self):
        key1 = b"0123456789abcdef"
        key2 = b"abcdefghijklmnop"
        iv = b"fedcba9876543210"
        encrypted = self._encrypt(b"secret data here", key1, iv)
        result = _decrypt_aes128(encrypted, key2, iv)
        assert result != b"secret data here"


# ==================== m3u8_download save_path 校验测试 ====================


class TestM3u8SavePathRequired:
    """m3u8_download 的 save_path 必须为绝对路径。"""

    async def _call(self, **kwargs):
        from uniclaw.tools.base import ToolRuntime
        from uniclaw.tools.download.m3u8_tools import m3u8_download

        kwargs.setdefault("tool_runtime", ToolRuntime())
        return await m3u8_download(**kwargs)

    async def test_relative_path_rejected(self):
        with pytest.raises(ValueError, match="绝对路径"):
            await self._call(url="http://x/playlist.m3u8", save_path="movie.ts")

    async def test_empty_path_rejected(self):
        with pytest.raises(ValueError, match="绝对路径"):
            await self._call(url="http://x/playlist.m3u8", save_path="")

    async def test_absolute_path_ok(self, tmp_path, monkeypatch):
        # 打桩 parse,避免真实网络请求
        async def fake_parse(self):
            self.progress.status = M3u8Status.SELECTING
            self.progress.variants = [
                M3u8Variant(index=0, bandwidth=5000000, resolution="1920x1080")
            ]

        monkeypatch.setattr(M3u8Downloader, "parse", fake_parse)
        target = tmp_path / "movie.ts"
        result = await self._call(
            url="http://x/playlist.m3u8",
            save_path=str(target),
            variant="0",
        )
        assert "1920x1080" in result


# ==================== M3u8Record 测试 ====================


class TestM3u8Record:
    """M3u8Record 分片级断点续传记录测试。"""

    def test_init(self, tmp_path):
        r = M3u8Record(tmp_path / "progress.json")
        assert r.downloaded == set()

    def test_mark_and_is_downloaded(self, tmp_path):
        r = M3u8Record(tmp_path / "progress.json")
        r.mark_downloaded(0)
        r.mark_downloaded(3)
        assert r.is_downloaded(0) is True
        assert r.is_downloaded(3) is True
        assert r.is_downloaded(1) is False
        assert r.downloaded == {0, 3}

    def test_save_and_load(self, tmp_path):
        f = tmp_path / "progress.json"
        r = M3u8Record(f)
        r.mark_downloaded(0)
        r.mark_downloaded(5)

        r2 = M3u8Record(f)
        r2.load()
        assert r2.downloaded == {0, 5}

    def test_load_nonexistent(self, tmp_path):
        r = M3u8Record(tmp_path / "nonexistent.json")
        r.load()
        assert r.downloaded == set()

    def test_load_corrupted(self, tmp_path):
        f = tmp_path / "corrupted.json"
        f.write_text("not json!!!")
        r = M3u8Record(f)
        r.load()
        assert r.downloaded == set()

    def test_clear(self, tmp_path):
        f = tmp_path / "progress.json"
        r = M3u8Record(f)
        r.mark_downloaded(0)
        assert f.exists()
        r.clear()
        assert r.downloaded == set()
        assert not f.exists()


# ==================== M3u8Progress 测试 ====================


class TestM3u8Progress:
    """M3u8Progress 进度数据类测试。"""

    def test_progress_percent(self):
        p = M3u8Progress(
            task_id="t",
            url="u",
            save_path="s",
            total_segments=10,
            downloaded_segments=5,
        )
        assert p.progress_percent == 50.0

    def test_progress_percent_zero(self):
        p = M3u8Progress(task_id="t", url="u", save_path="s")
        assert p.progress_percent == 0.0

    def test_format_status_completed(self):
        p = M3u8Progress(
            task_id="t",
            url="u",
            save_path="/tmp/v.mp4",
            total_size=1024,
            status=M3u8Status.COMPLETED,
            merged_file="/tmp/v.mp4",
            resolution="1920x1080",
            start_time=1,
            end_time=2,
        )
        result = p.format_status()
        assert "M3U8 下载完成" in result
        assert "1920x1080" in result

    def test_format_status_selecting(self):
        p = M3u8Progress(
            task_id="t",
            url="u",
            save_path="s",
            status=M3u8Status.SELECTING,
            variants=[
                M3u8Variant(index=0, bandwidth=5000000, resolution="1920x1080"),
                M3u8Variant(index=1, bandwidth=2500000, resolution="1280x720"),
            ],
        )
        result = p.format_status()
        assert "1920x1080" in result
        assert "1280x720" in result
        assert "5000kbps" in result

    def test_format_status_selecting_float_bandwidth(self):
        """Bug #6 回归:带宽为 float 时不抛 `Unknown format code 'd'` 格式化错误。"""
        p = M3u8Progress(
            task_id="t",
            url="u",
            save_path="s",
            status=M3u8Status.SELECTING,
            variants=[
                M3u8Variant(index=0, bandwidth=5000000.0, resolution="1920x1080"),
                M3u8Variant(index=1, bandwidth=2500000.0, resolution="1280x720"),
            ],
        )
        result = p.format_status()
        assert "1920x1080" in result
        assert "5000kbps" in result
        assert "2500kbps" in result

    def test_format_status_failed(self):
        p = M3u8Progress(
            task_id="t",
            url="u",
            save_path="s",
            status=M3u8Status.FAILED,
            error="网络错误",
        )
        result = p.format_status()
        assert "M3U8 下载失败" in result
        assert "网络错误" in result


# ==================== M3u8Downloader 单元测试 ====================


class TestM3u8Downloader:
    """M3u8Downloader 类测试(不涉及真实网络请求)。"""

    MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
1080p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=854x480
480p/index.m3u8
"""

    MEDIA = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
seg0.ts
#EXTINF:10.0,
seg1.ts
#EXT-X-ENDLIST
"""

    def test_init(self, tmp_path):
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        assert dl.concurrency == DEFAULT_M3U8_CONCURRENCY
        assert dl.progress.status == M3u8Status.PENDING
        assert dl._tmp_dir.name.startswith(".v_")  # 临时目录与目标同目录

    def test_init_custom(self, tmp_path):
        dl = M3u8Downloader(
            url="http://x/playlist.m3u8",
            save_path=tmp_path / "v.ts",
            concurrency=4,
            proxy="http://proxy:8080",
        )
        assert dl.concurrency == 4
        assert dl.proxy == "http://proxy:8080"

    def test_parse_master(self, tmp_path):
        dl = M3u8Downloader(url="http://x/master.m3u8", save_path=tmp_path / "v.ts")
        dl._base_url = "http://x/"
        dl._parse_master(self.MASTER)
        assert len(dl._variants) == 3
        assert dl._variants[0].resolution == "1920x1080"
        assert dl._variants[0].bandwidth == 5000000
        assert dl._variants[1].resolution == "1280x720"
        assert dl._variants[0].url == "http://x/1080p/index.m3u8"

    def test_parse_master_bad_bandwidth(self, tmp_path):
        """BANDWIDTH 为非数字时不应抛 ValueError,应回退为 0。"""
        dl = M3u8Downloader(url="http://x/master.m3u8", save_path=tmp_path / "v.ts")
        dl._base_url = "http://x/"
        content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=abc,RESOLUTION=1920x1080
1080p/index.m3u8
"""
        dl._parse_master(content)
        assert len(dl._variants) == 1
        assert dl._variants[0].bandwidth == 0
        assert dl._variants[0].resolution == "1920x1080"

    @pytest.mark.asyncio
    async def test_parse_media(self, tmp_path):
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=MagicMock(
                text=self.MEDIA,
                content=b"",
                raise_for_status=MagicMock(),
                status_code=200,
            )
        )
        await dl._parse_media(mock_client, "http://x/playlist.m3u8")
        assert len(dl._segments) == 2
        assert dl._segments[0].index == 0
        assert dl._segments[0].url == "http://x/seg0.ts"
        assert dl._segments[1].url == "http://x/seg1.ts"

    def test_apply_variant_by_index(self, tmp_path):
        dl = M3u8Downloader(url="http://x/master.m3u8", save_path=tmp_path / "v.ts")
        dl._base_url = "http://x/"
        dl._parse_master(self.MASTER)
        assert dl._apply_variant_choice("1") is True
        assert dl._selected_index == 1
        assert dl._apply_variant_choice("99") is False

    def test_apply_variant_by_resolution(self, tmp_path):
        dl = M3u8Downloader(url="http://x/master.m3u8", save_path=tmp_path / "v.ts")
        dl._base_url = "http://x/"
        dl._parse_master(self.MASTER)
        assert dl._apply_variant_choice("1920x1080") is True
        assert dl._selected_index == 0
        assert dl._apply_variant_choice("720p") is True
        assert dl._selected_index == 1
        assert dl._apply_variant_choice("999x999") is False

    def test_segment_iv_default(self, tmp_path):
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        dl._media_sequence = 0
        seg = M3u8Segment(
            index=1, duration=10, uri="seg1.ts", url="http://x/seg1.ts", key=None
        )
        iv = dl._segment_iv(seg)
        assert iv == (1).to_bytes(16, byteorder="big")

    def test_segment_iv_explicit(self, tmp_path):
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        seg = M3u8Segment(
            index=1,
            duration=10,
            uri="seg1.ts",
            url="http://x/seg1.ts",
            key=M3u8Key(method="AES-128", uri="key.key", iv="00" * 16),
        )
        iv = dl._segment_iv(seg)
        assert iv == b"\x00" * 16

    def test_pause_resume_cancel(self, tmp_path):
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        dl.pause()
        assert not dl._pause_event.is_set()
        assert dl.progress.status == M3u8Status.PAUSED
        dl.resume()
        assert dl._pause_event.is_set()
        assert dl.progress.status == M3u8Status.DOWNLOADING
        dl.cancel()
        assert dl._cancelled is True
        assert dl.progress.status == M3u8Status.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_during_merge_still_completes(self, tmp_path, monkeypatch):
        """合并期间被取消: 合并是本地快速操作,不应中断,最终文件照常生成。"""
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        dl._base_url = "http://x/"
        dl._segments = [
            M3u8Segment(
                index=0, duration=10, uri="seg0.ts", url="http://x/seg0.ts", key=None
            ),
            M3u8Segment(
                index=1, duration=10, uri="seg1.ts", url="http://x/seg1.ts", key=None
            ),
        ]
        # 预生成分片文件并标记已下载,避免真实网络请求
        dl._tmp_dir.mkdir(parents=True, exist_ok=True)
        for seg in dl._segments:
            dl._segment_file(seg).write_bytes(f"seg-{seg.index}".encode())
            dl._record.mark_downloaded(seg.index)

        # 模拟合并过程中被取消: 合并开始时触发 cancel()
        original_merge = M3u8Downloader._merge_segments

        async def cancelling_merge(self):
            self.cancel()
            await original_merge(self)

        monkeypatch.setattr(M3u8Downloader, "_merge_segments", cancelling_merge)

        progress = await dl.start()

        # 合并不受取消影响,仍应产出完整文件
        assert progress.status == M3u8Status.COMPLETED
        assert (tmp_path / "v.ts").exists(), "取消不应阻止合并完成"
        assert (tmp_path / "v.ts").read_bytes() == b"seg-0seg-1"

    @pytest.mark.asyncio
    async def test_resume_no_double_count(self, tmp_path, monkeypatch):
        """断点续传:已存在分片文件不应被重新下载,downloaded_size 不应双重计数。"""
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        dl._base_url = "http://x/"
        dl._segments = [
            M3u8Segment(
                index=0, duration=10, uri="seg0.ts", url="http://x/seg0.ts", key=None
            ),
            M3u8Segment(
                index=1, duration=10, uri="seg1.ts", url="http://x/seg1.ts", key=None
            ),
            M3u8Segment(
                index=2, duration=10, uri="seg2.ts", url="http://x/seg2.ts", key=None
            ),
        ]
        # 分片 0 文件已存在,但 record 未标记(模拟上次崩溃在写文件后、写 record 前)
        dl._tmp_dir.mkdir(parents=True, exist_ok=True)
        dl._segment_file(dl._segments[0]).write_bytes(b"existing0")

        # 只下载缺失的分片 1,2;分片 0 不应被重新下载
        async def fake_download(self, client, seg, callback=None):
            data = b"new-" + str(seg.index).encode()
            seg_file = dl._segment_file(seg)
            seg_file.write_bytes(data)
            dl._record.mark_downloaded(seg.index)
            dl.progress.downloaded_size += len(data)
            dl.progress.downloaded_segments += 1
            return len(data)

        monkeypatch.setattr(M3u8Downloader, "_download_segment", fake_download)
        progress = await dl.start()

        # 合并结果 = existing0 + new-1 + new-2,说明分片 0 未被重新下载覆盖
        assert (tmp_path / "v.ts").read_bytes() == b"existing0new-1new-2"
        # downloaded_size = 分片0(9字节) + 分片1(5字节) + 分片2(5字节) = 19,无双重计数
        assert progress.downloaded_size == 9 + 5 + 5
        assert progress.status == M3u8Status.COMPLETED

    @pytest.mark.asyncio
    async def test_start_completed_cleans_tmp_dir(self, tmp_path):
        """下载完成后应删除临时目录(分片 + 进度记录)。"""
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        dl._base_url = "http://x/"
        dl._segments = [
            M3u8Segment(
                index=0, duration=10, uri="seg0.ts", url="http://x/seg0.ts", key=None
            ),
            M3u8Segment(
                index=1, duration=10, uri="seg1.ts", url="http://x/seg1.ts", key=None
            ),
        ]
        # 预生成分片文件并标记已下载,避免真实网络请求
        dl._tmp_dir.mkdir(parents=True, exist_ok=True)
        for seg in dl._segments:
            dl._segment_file(seg).write_bytes(f"seg-{seg.index}".encode())
            dl._record.mark_downloaded(seg.index)

        progress = await dl.start()

        assert progress.status == M3u8Status.COMPLETED
        assert not dl._tmp_dir.exists(), "完成后临时目录应被清理"
        assert (tmp_path / "v.ts").exists(), "合并后的最终文件应存在"
        assert (tmp_path / "v.ts").read_bytes() == b"seg-0seg-1"

    @pytest.mark.asyncio
    async def test_start_aborts_on_consecutive_failures(self, tmp_path):
        """连续失败达到阈值应提前终止,不再下载后续分片。"""
        dl = M3u8Downloader(
            url="http://x/playlist.m3u8",
            save_path=tmp_path / "v.ts",
            max_retries=0,
            concurrency=1,
            consecutive_failure_limit=2,
        )
        dl._base_url = "http://x/"
        dl._segments = [
            M3u8Segment(
                index=i, duration=10, uri=f"seg{i}.ts", url=f"http://x/seg{i}.ts", key=None
            )
            for i in range(3)
        ]

        calls = 0
        async def failing_download(client, seg, callback=None):
            nonlocal calls
            calls += 1
            raise IOError(f"分片 {seg.index} 下载失败")

        with patch.object(dl, "_download_segment", new=failing_download):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "uniclaw.tools.download.m3u8.httpx.AsyncClient",
                return_value=mock_client,
            ):
                progress = await dl.start()

        assert progress.status == M3u8Status.FAILED
        # 第 3 个分片不应再发起下载(提前结束)
        assert calls == 2

    @pytest.mark.asyncio
    async def test_start_abort_reason_summarizes_first_three(self, tmp_path):
        """连续失败达到阈值时,终止原因只汇总前 3 个错误,但内部记录全部失败。"""
        dl = M3u8Downloader(
            url="http://x/playlist.m3u8",
            save_path=tmp_path / "v.ts",
            max_retries=0,
            concurrency=1,
            consecutive_failure_limit=5,
        )
        dl._base_url = "http://x/"
        dl._segments = [
            M3u8Segment(
                index=i,
                duration=10,
                uri=f"seg{i}.ts",
                url=f"http://x/seg{i}.ts",
                key=None,
            )
            for i in range(6)
        ]

        async def failing_download(client, seg, callback=None):
            raise IOError(f"分片 {seg.index} 下载失败")

        with patch.object(dl, "_download_segment", new=failing_download):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "uniclaw.tools.download.m3u8.httpx.AsyncClient",
                return_value=mock_client,
            ):
                await dl.start()

        # 内部记录了全部 5 个失败分片
        assert len(dl._abort_errors) == 5
        # 终止原因只汇总前 3 个错误详情
        assert "连续 5 个分片下载失败" in dl._abort_reason
        assert "分片 0" in dl._abort_reason
        assert "分片 1" in dl._abort_reason
        assert "分片 2" in dl._abort_reason
        assert "分片 3" not in dl._abort_reason
        assert "分片 4" not in dl._abort_reason

    @pytest.mark.asyncio
    async def test_download_segment_rejects_html(self, tmp_path):
        """分片返回 HTML 错误页时应抛 ValueError 并通过 callback 通知,不写入垃圾内容。"""
        dl = M3u8Downloader(
            url="http://x/playlist.m3u8",
            save_path=tmp_path / "v.ts",
            max_retries=0,
        )
        seg = M3u8Segment(
            index=0, duration=10, uri="seg0.ts", url="http://x/seg0.ts", key=None
        )

        content = b"<!DOCTYPE html><html><body>404 Not Found</body></html>"

        async def fake_aiter_bytes(chunk_size):
            yield content

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_bytes = fake_aiter_bytes

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        client = MagicMock()
        client.stream = MagicMock(return_value=mock_ctx)

        # 记录 callback 收到的错误通知
        error_notifications = []
        async def progress_callback(info):
            error_notifications.append(info)

        with pytest.raises(ValueError, match="HTML"):
            await dl._download_segment(client, seg, callback=progress_callback)

        # 不应写入任何分片文件
        assert not dl._segment_file(seg).exists()
        # 应通过 callback 通知错误原因
        assert len(error_notifications) == 1
        assert error_notifications[0].error is not None
        assert "HTML" in error_notifications[0].error

    @pytest.mark.asyncio
    async def test_download_segment_uses_block_timeout(self, tmp_path):
        """分片流式读取应受 block_timeout 约束,服务器停止发送数据后超时中止。"""
        dl = M3u8Downloader(
            url="http://x/playlist.m3u8",
            save_path=tmp_path / "v.ts",
            block_timeout=0.2,
            max_retries=0,
        )
        seg = M3u8Segment(
            index=0, duration=10, uri="seg0.ts", url="http://x/seg0.ts", key=None
        )

        # 流永不完结,应被逐块 wait_for 超时中止
        async def hanging_aiter_bytes(chunk_size):
            await asyncio.sleep(10)
            yield b""

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_bytes = hanging_aiter_bytes

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        client = MagicMock()
        client.stream = MagicMock(return_value=mock_ctx)

        start = asyncio.get_event_loop().time()
        with pytest.raises(IOError, match="下载失败"):
            await dl._download_segment(client, seg)
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 2, f"分片请求应受 block_timeout 约束,实际耗时 {elapsed:.1f}s"

    @pytest.mark.asyncio
    async def test_fetch_cancelled_raises_ioerror(self, tmp_path):
        """取消后 _fetch 应抛 IOError(而非 CancelledError),便于外层 except Exception 捕获。"""
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        dl._cancelled = True
        client = AsyncMock()
        with pytest.raises(IOError, match="取消"):
            await dl._fetch(client, "http://x/seg.ts")
        # 取消后不应发起真实网络请求
        client.get.assert_not_called()


# ==================== _safe_int 测试 ====================


class TestSafeInt:
    """_safe_int 安全整数解析测试。"""

    def test_normal_int(self):
        assert _safe_int("5000000") == 5000000

    def test_zero(self):
        assert _safe_int("0") == 0

    def test_empty_string(self):
        assert _safe_int("") == 0

    def test_non_numeric(self):
        assert _safe_int("abc") == 0

    def test_default_override(self):
        assert _safe_int("abc", default=-1) == -1


# ==================== _looks_like_html 测试 ====================


class TestLooksLikeHtml:
    """_looks_like_html HTML 误判检测测试。"""

    def test_html_doctype(self):
        assert _looks_like_html(b"<!DOCTYPE html><html><body>404</body></html>") is True

    def test_html_tag(self):
        assert _looks_like_html(b"<html><body>Not Found</body></html>") is True

    def test_binary_ts(self):
        assert _looks_like_html(b"\x47\x40\x11\x00\x01\x02\x03") is False

    def test_empty(self):
        assert _looks_like_html(b"") is False

    def test_text_js(self):
        assert _looks_like_html(b"window.__NUXT__={}") is False
