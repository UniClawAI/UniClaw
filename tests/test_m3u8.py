"""M3U8 下载引擎的单元测试。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.tools.download.m3u8 import (
    DEFAULT_M3U8_CONCURRENCY,
    M3u8Downloader,
    M3u8Key,
    M3u8Map,
    M3u8Progress,
    M3u8Record,
    M3u8Segment,
    M3u8Status,
    M3u8Variant,
    _decrypt_aes128,
    _looks_like_html,
    _parse_attributes,
    _decrypt_map_section,
    _looks_like_init_section,
    _resolve_url,
    _safe_int,
    guess_video_name,
)
from uniclaw.utils.constants import TOOL_ERROR

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
        # 走 execute() 而非 __call__: 校验语义(返回 TOOL_ERROR)是工具自身契约
        return await m3u8_download.execute(**kwargs)

    async def test_relative_path_rejected(self):
        result = await self._call(url="http://x/playlist.m3u8", save_path="movie.ts")
        assert TOOL_ERROR in result
        assert "绝对路径" in result
        assert "movie.ts" in result

    async def test_empty_path_rejected(self):
        result = await self._call(url="http://x/playlist.m3u8", save_path="")
        assert TOOL_ERROR in result
        assert "绝对路径" in result

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

    @pytest.mark.asyncio
    async def test_parse_rejects_html_content(self, tmp_path):
        """URL 返回 HTML 网页时应报"不是有效播放列表",而非把每行当分片下载。"""
        dl = M3u8Downloader(url="http://x/page.html", save_path=tmp_path / "v.ts")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=MagicMock(
                text="<!DOCTYPE html>\n<html><body>404 not found</body></html>",
                content=b"",
                raise_for_status=MagicMock(),
                status_code=200,
            )
        )
        progress = await dl.parse(mock_client)

        assert progress.status == M3u8Status.FAILED
        assert "不是有效的 M3U8 播放列表" in progress.error
        assert dl._segments == [], "非播放列表内容不应产生分片列表"

    @pytest.mark.asyncio
    async def test_parse_rejects_plain_text_content(self, tmp_path):
        """纯文本(如鉴权提示)同样应判为非播放列表,报错含内容预览。"""
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=MagicMock(
                text="access denied, please login",
                content=b"",
                raise_for_status=MagicMock(),
                status_code=200,
            )
        )
        progress = await dl.parse(mock_client)

        assert progress.status == M3u8Status.FAILED
        assert "不是有效的 M3U8 播放列表" in progress.error
        assert "access denied" in progress.error

    @pytest.mark.asyncio
    async def test_parse_media_empty_segments(self, tmp_path):
        """合法播放列表标签但没有任何分片时报错,不进入空下载。"""
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=MagicMock(
                text="#EXTM3U\n#EXT-X-ENDLIST\n",
                content=b"",
                raise_for_status=MagicMock(),
                status_code=200,
            )
        )
        progress = await dl.parse(mock_client)

        assert progress.status == M3u8Status.FAILED
        assert "未包含任何分片" in progress.error

    @pytest.mark.asyncio
    async def test_parse_master_without_variants(self, tmp_path):
        """主播放列表标记在但解析不出任何分辨率选项时报明确错误。"""
        dl = M3u8Downloader(url="http://x/master.m3u8", save_path=tmp_path / "v.ts")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=MagicMock(
                text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n",
                content=b"",
                raise_for_status=MagicMock(),
                status_code=200,
            )
        )
        progress = await dl.parse(mock_client)

        assert progress.status == M3u8Status.FAILED
        assert "分辨率选项" in progress.error

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

    def test_cancel_keeps_tmp_dir_for_resume(self, tmp_path):
        """取消下载应保留中间分片工作目录,供断点续传复用已下分片。"""
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        dl._tmp_dir.mkdir(parents=True, exist_ok=True)
        (dl._tmp_dir / "progress.json").write_text("{}")
        (dl._tmp_dir / "seg_000000.ts").write_bytes(b"seg0")
        dl._record.mark_downloaded(0)

        dl.cancel()

        assert dl._cancelled is True
        assert dl.progress.status == M3u8Status.CANCELLED
        assert dl._tmp_dir.exists(), "取消后分片目录应保留,重下同 URL 自动续传"
        assert (dl._tmp_dir / "seg_000000.ts").read_bytes() == b"seg0"
        assert dl._record.is_downloaded(0), "已下分片记录应保留"

    def test_cancel_keeps_tmp_dir_but_not_save_path(self, tmp_path):
        """取消只保留中间工作目录,同样不碰最终交付物 save_path。"""
        final = tmp_path / "v.ts"
        final.write_bytes(b"already-merged")
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=final)
        dl._tmp_dir.mkdir(parents=True, exist_ok=True)
        (dl._tmp_dir / "seg_000000.ts").write_bytes(b"seg0")

        dl.cancel()

        assert dl._tmp_dir.exists(), "临时目录应保留供续传"
        assert final.exists(), "最终交付物不应被删除"
        assert final.read_bytes() == b"already-merged"

    def test_cancel_cleanup_failure_does_not_break_cancel(self, tmp_path, monkeypatch):
        """清理失败不应影响取消操作本身(尽力而为)。"""
        dl = M3u8Downloader(url="http://x/playlist.m3u8", save_path=tmp_path / "v.ts")
        dl._tmp_dir.mkdir(parents=True, exist_ok=True)

        def boom():
            raise OSError("disk error")

        monkeypatch.setattr(dl, "cleanup", boom)
        dl.cancel()  # 不应抛异常

        assert dl._cancelled is True
        assert dl.progress.status == M3u8Status.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_during_download_keeps_tmp_dir(self, tmp_path, monkeypatch):
        """下载进行中取消: start() 返回后保留临时目录,已下分片可续传复用。"""
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

        async def cancelling_download(self, client, seg, callback=None):
            # 模拟已下完一个分片后被取消
            f = self._segment_file(seg)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(f"seg-{seg.index}".encode())
            self._record.mark_downloaded(seg.index)
            self.progress.downloaded_segments += 1
            dl.cancel()
            raise IOError("下载已取消")

        monkeypatch.setattr(M3u8Downloader, "_download_segment", cancelling_download)
        dl._tmp_dir.mkdir(parents=True, exist_ok=True)

        progress = await dl.start()

        assert progress.status == M3u8Status.CANCELLED
        assert dl._tmp_dir.exists(), "取消后临时分片工作目录应保留供续传"
        assert dl._record.is_downloaded(0), "已下分片记录应保留"

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


# ── BYTERANGE / EXT-X-MAP fMP4(第 27 轮 #1/#2/#3) ──────────


class TestByterangeMap:
    """`#EXT-X-BYTERANGE`/`#EXT-X-MAP` 解析与按区间下载。"""

    FMP4_MEDIA = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-MAP:URI="main.mp4",BYTERANGE="200@0"
#EXTINF:4.0,
#EXT-X-BYTERANGE:300@200
main.mp4
#EXTINF:4.0,
#EXT-X-BYTERANGE:400@500
main.mp4
#EXTINF:4.0,
#EXT-X-BYTERANGE:500
main.mp4
#EXT-X-ENDLIST
"""

    def _dl(self, tmp_path):
        return M3u8Downloader(url="http://x/media.m3u8", save_path=tmp_path / "v.mp4")

    @pytest.mark.asyncio
    async def test_parse_map_and_byteranges(self, tmp_path):
        """解析出初始化段与每分片的 (offset, length) 区间。"""
        dl = self._dl(tmp_path)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=MagicMock(
                text=self.FMP4_MEDIA,
                content=b"",
                raise_for_status=MagicMock(),
                status_code=200,
            )
        )
        await dl._parse_media(mock_client, "http://x/media.m3u8")

        assert dl._map_info is not None
        assert dl._map_info.url == "http://x/main.mp4"
        assert dl._map_info.byte_range == (0, 200)
        assert [s.byte_range for s in dl._segments] == [
            (200, 300),
            (500, 400),
            (900, 500),  # 省略 @offset 时接在同 URI 上一分片末尾
        ]
        assert all(s.url == "http://x/main.mp4" for s in dl._segments)

    @pytest.mark.asyncio
    async def test_byterange_download_requests_only_range(self, tmp_path):
        """BYTERANGE 分片只请求自己的区间,不重复下载整个媒体文件。"""
        dl = self._dl(tmp_path)
        resp = MagicMock()
        resp.status_code = 206
        resp.raise_for_status = MagicMock()

        async def aiter(_size=None):
            yield b"x" * 300

        resp.aiter_bytes = MagicMock(return_value=aiter())
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        client = AsyncMock()
        client.stream = MagicMock(return_value=ctx)

        data = await dl._read_byte_range(client, "http://x/main.mp4", 200, 300)

        assert data == b"x" * 300
        sent = client.stream.call_args
        headers = sent.kwargs.get("headers") or sent.args[2]
        assert headers["Range"] == "bytes=200-499"

    @pytest.mark.asyncio
    async def test_byterange_ignored_range_slices_body(self, tmp_path):
        """服务器无视 Range 返回 200 时,流式截取目标区间,不整段照收。"""
        dl = self._dl(tmp_path)
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()

        async def aiter(_size=None):
            yield b"AAAA"  # 跳过前 4 字节
            yield b"BBBBCCCC"  # 取 8 字节

        resp.aiter_bytes = MagicMock(return_value=aiter())
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        client = AsyncMock()
        client.stream = MagicMock(return_value=ctx)

        data = await dl._read_byte_range(client, "http://x/main.mp4", 4, 8)

        assert data == b"BBBBCCCC"

    @pytest.mark.asyncio
    async def test_merge_prepends_init_once(self, tmp_path):
        """合并产物 = 一份 init + 各分片按序,init 不随分片重复。"""
        dl = self._dl(tmp_path)
        dl._map_info = M3u8Map(
            uri="main.mp4", url="http://x/main.mp4", byte_range=(0, 200)
        )
        dl._segments = [
            M3u8Segment(
                index=i,
                duration=4,
                uri="main.mp4",
                url="http://x/main.mp4",
                key=None,
                byte_range=(200 + i * 100, 100),
            )
            for i in range(3)
        ]
        dl._tmp_dir.mkdir(parents=True, exist_ok=True)
        dl._init_file.write_bytes(b"I" * 200)
        for seg in dl._segments:
            dl._segment_file(seg).write_bytes(f"s{seg.index}".encode() * 50)

        await dl._merge_segments()

        out = (tmp_path / "v.mp4").read_bytes()
        assert out == b"I" * 200 + b"s0" * 50 + b"s1" * 50 + b"s2" * 50
        assert out.count(b"I" * 200) == 1, "init 只能出现一次"

    @pytest.mark.asyncio
    async def test_end_to_end_no_bloat(self, tmp_path):
        """单文件 BYTERANGE 流完整下载: 产物 = init + 总区间字节,不再膨胀百倍。

        本地起 HTTP 服务提供 32KB 的 main.mp4,播放列表把它切成 1 个 init
        (200B) + 4 个分片区间。修复前每分片都会拉整个 32KB 文件。
        """
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        payload = bytes(range(256)) * 128  # 32KB 确定性内容
        served = []  # 记录请求的 Range 头,断言没有整文件下载

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                rng = self.headers.get("Range")
                served.append(rng)
                if rng and rng.startswith("bytes="):
                    s, e = rng[6:].split("-", 1)
                    start, end = int(s), int(e)
                    body = payload[start : end + 1]
                    self.send_response(206)
                    self.send_header(
                        "Content-Range", f"bytes {start}-{end}/{len(payload)}"
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            playlist = (
                "#EXTM3U\n"
                "#EXT-X-VERSION:7\n"
                f'#EXT-X-MAP:URI="main.mp4",BYTERANGE="200@0"\n'
                "#EXTINF:4.0,\n#EXT-X-BYTERANGE:1024@200\nmain.mp4\n"
                "#EXTINF:4.0,\n#EXT-X-BYTERANGE:1024@1224\nmain.mp4\n"
                "#EXTINF:4.0,\n#EXT-X-BYTERANGE:1024@2248\nmain.mp4\n"
                "#EXTINF:4.0,\n#EXT-X-BYTERANGE:1024@3272\nmain.mp4\n"
                "#EXT-X-ENDLIST\n"
            )
            dl = M3u8Downloader(
                url=f"http://127.0.0.1:{port}/media.m3u8",
                save_path=tmp_path / "out.mp4",
                concurrency=2,
            )
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=MagicMock(
                    text=playlist,
                    content=b"",
                    raise_for_status=MagicMock(),
                    status_code=200,
                )
            )
            await dl.parse(mock_client)

            progress = await dl.start()

            assert progress.status == M3u8Status.COMPLETED, progress.error
            out = (tmp_path / "out.mp4").read_bytes()
            expected = payload[:200] + payload[200:1224] + payload[1224:2248] + payload[2248:3272] + payload[3272:4296]
            assert out == expected, "合并产物必须等于 init + 各区间拼接"
            assert len(out) == 200 + 1024 * 4
            # 所有媒体请求都带 Range —— 没有整文件(32KB)重复下载
            assert served and all(r for r in served), f"存在无 Range 请求: {served}"
        finally:
            server.shutdown()


class TestM3u8InputValidation:
    """参数校验(第 27 轮 #9/#10 同类)。"""

    @pytest.mark.asyncio
    async def test_concurrency_rejected(self, tmp_path):
        """concurrency <= 0 直接报参数错误,不再静默接受。"""
        from uniclaw.tools.base import ToolRuntime
        from uniclaw.tools.download.m3u8_tools import m3u8_download

        config = MagicMock()
        rt = ToolRuntime(config=config)
        result = await m3u8_download(
            url="http://x/m.m3u8",
            save_path=str(tmp_path / "a.ts"),
            concurrency=0,
            tool_runtime=rt,
        )
        assert "TOOL_ERROR" in result
        assert "concurrency" in result


# ── BYTERANGE 非法值/越界/init 双计数(第 28 轮 #2/#3/#4) ──


class TestByterangeValidation:
    """非法 BYTERANGE/MAP 区间必须报错,拒绝静默回退整文件。"""

    def _dl(self, tmp_path):
        return M3u8Downloader(url="http://x/media.m3u8", save_path=tmp_path / "v.mp4")

    def _client_with(self, text):
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=MagicMock(
                text=text,
                content=b"",
                raise_for_status=MagicMock(),
                status_code=200,
            )
        )
        return client

    @pytest.mark.asyncio
    async def test_negative_length_rejected(self, tmp_path):
        """`#EXT-X-BYTERANGE:-5@0` 非法,解析期报错,不得整文件回退。"""
        dl = self._dl(tmp_path)
        media = (
            "#EXTM3U\n#EXTINF:4.0,\n#EXT-X-BYTERANGE:-5@0\nmain.mp4\n"
        )
        progress = await dl.parse(self._client_with(media))

        assert progress.status == M3u8Status.FAILED
        assert "非法 #EXT-X-BYTERANGE" in progress.error
        assert dl._segments == []

    @pytest.mark.asyncio
    async def test_zero_length_rejected(self, tmp_path):
        """`#EXT-X-BYTERANGE:0@720` 非法,同样拒绝(否则静默整文件+init 重复)。"""
        dl = self._dl(tmp_path)
        media = (
            "#EXTM3U\n#EXT-X-MAP:URI=\"main.mp4\",BYTERANGE=\"200@0\"\n"
            "#EXTINF:4.0,\n#EXT-X-BYTERANGE:0@720\nmain.mp4\n"
        )
        progress = await dl.parse(self._client_with(media))

        assert progress.status == M3u8Status.FAILED
        assert "非法 #EXT-X-BYTERANGE" in progress.error
        assert dl._segments == []

    @pytest.mark.asyncio
    async def test_negative_offset_rejected(self, tmp_path):
        """负 offset 同样非法。"""
        dl = self._dl(tmp_path)
        media = "#EXTM3U\n#EXTINF:4.0,\n#EXT-X-BYTERANGE:100@-1\nmain.mp4\n"
        progress = await dl.parse(self._client_with(media))

        assert progress.status == M3u8Status.FAILED
        assert "offset 不能为负" in progress.error

    @pytest.mark.asyncio
    async def test_bad_map_range_rejected(self, tmp_path):
        """MAP 的 BYTERANGE 非法与分片同口径: 解析期报错。"""
        dl = self._dl(tmp_path)
        media = (
            "#EXTM3U\n#EXT-X-MAP:URI=\"main.mp4\",BYTERANGE=\"abc@0\"\n"
            "#EXTINF:4.0,\n#EXT-X-BYTERANGE:100@0\nmain.mp4\n"
        )
        progress = await dl.parse(self._client_with(media))

        assert progress.status == M3u8Status.FAILED
        assert "#EXT-X-MAP" in progress.error
        assert "非法" in progress.error

    @pytest.mark.asyncio
    async def test_map_out_of_range_rejected_at_download(self, tmp_path):
        """MAP 区间越界(服务器截断/误返整文件当 init)下载期报错,不产出损坏文件。"""
        dl = M3u8Downloader(
            url="http://x/m.m3u8", save_path=tmp_path / "v.mp4", max_retries=0
        )
        dl._map_info = M3u8Map(
            uri="main.mp4", url="http://x/main.mp4", byte_range=(0, 999_999_999)
        )

        async def short_range(client, url, offset, length):
            return b"tiny"  # 服务器只给了一点点,远小于 length

        with patch.object(dl, "_read_byte_range", new=short_range):
            ok = await dl._ensure_map()

        assert ok is False
        assert not dl._init_file.exists(), "越界 init 不得落地"

    @pytest.mark.asyncio
    async def test_init_size_counted_once(self, tmp_path):
        """init 字节只计一次进度(此前 _ensure_map 与恢复段双计数)。"""
        dl = self._dl(tmp_path)
        dl._map_info = M3u8Map(
            uri="main.mp4", url="http://x/main.mp4", byte_range=(0, 200)
        )
        dl._segments = []

        async def fake_range(client, url, offset, length):
            return b"I" * length

        with patch.object(dl, "_read_byte_range", new=fake_range):
            with patch.object(dl, "parse", new=AsyncMock()):
                progress = await dl.start()

        assert progress.status == M3u8Status.COMPLETED, progress.error
        # 成功路径会清理临时目录(含 init.bin),只验进度计数未双计
        assert progress.downloaded_size == 200, (
            f"init 只应计一次, 实际 {progress.downloaded_size}"
        )


class TestByterangeProgressUnit:
    """BYTERANGE 流进度展示口径用"区间"(第 28 轮 #9)。"""

    def test_status_uses_qujian_for_byterange(self):
        progress = M3u8Progress(task_id="t", url="u", save_path="s")
        progress.status = M3u8Status.DOWNLOADING
        progress.total_segments = 127
        progress.downloaded_segments = 40
        progress.has_byterange = True
        assert "40/127 区间" in progress.format_status()

    def test_status_uses_fenpian_for_plain_ts(self):
        progress = M3u8Progress(task_id="t", url="u", save_path="s")
        progress.status = M3u8Status.DOWNLOADING
        progress.total_segments = 10
        progress.downloaded_segments = 3
        progress.has_byterange = False
        assert "3/10 分片" in progress.format_status()

    @pytest.mark.asyncio
    async def test_parse_sets_has_byterange(self, tmp_path):
        dl = M3u8Downloader(url="http://x/m.m3u8", save_path=tmp_path / "v.mp4")
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=MagicMock(
                text="#EXTM3U\n#EXTINF:4.0,\n#EXT-X-BYTERANGE:10@0\nmain.mp4\n",
                content=b"",
                raise_for_status=MagicMock(),
                status_code=200,
            )
        )
        await dl.parse(client)
        assert dl.progress.has_byterange is True



# ── init 段 IV 兼容(第 29 轮 #1) ───────────────────────────


class TestMapIvCompat:
    """加密 init 的 IV 兼容: 显式 IV(RFC 8216)与全 0 IV(打包器实践)都认。"""

    def _enc(self, plain: bytes, iv: bytes) -> bytes:
        """AES-128-CBC + PKCS7 加密(测试自构加密流用)。"""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        pad = 16 - len(plain) % 16
        padded = plain + bytes([pad]) * pad
        enc = Cipher(algorithms.AES(b"K" * 16), modes.CBC(iv)).encryptor()
        return enc.update(padded) + enc.finalize()

    def test_zero_iv_init_decrypted(self, tmp_path):
        """init 用全 0 IV 加密、KEY 带分片显式 IV —— 必须解出合法 init。"""
        plain = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8  # 合法 box 头
        data = self._enc(plain, b"\x00" * 16)
        map_info = M3u8Map(
            uri="m.mp4",
            url="http://x/m.mp4",
            key=M3u8Key(method="AES-128", iv="01" * 16, key_bytes=b"K" * 16),
        )

        out, note = _decrypt_map_section(data, map_info)

        assert out == plain

    def test_explicit_iv_init_decrypted(self):
        """init 按规范用 KEY 显式 IV 加密 —— 同样解出合法 init。"""
        plain = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8
        data = self._enc(plain, bytes.fromhex("01" * 16))
        map_info = M3u8Map(
            uri="m.mp4",
            url="http://x/m.mp4",
            key=M3u8Key(method="AES-128", iv="01" * 16, key_bytes=b"K" * 16),
        )

        out, note = _decrypt_map_section(data, map_info)

        assert out == plain

    def test_no_iv_key_uses_zero(self):
        """KEY 无显式 IV: 按全 0 解密。"""
        plain = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8
        data = self._enc(plain, b"\x00" * 16)
        map_info = M3u8Map(
            uri="m.mp4", url="http://x/m.mp4", key=M3u8Key(key_bytes=b"K" * 16)
        )

        out, note = _decrypt_map_section(data, map_info)

        assert out == plain

    def test_bad_iv_hex_raises(self):
        """显式 IV 非法 hex 与分片口径一致: 报错而非默默换 IV。"""
        map_info = M3u8Map(
            uri="m.mp4",
            url="http://x/m.mp4",
            key=M3u8Key(method="AES-128", iv="zz", key_bytes=b"K" * 16),
        )
        with pytest.raises(ValueError, match="IV 非法"):
            _decrypt_map_section(b"\x00" * 16, map_info)

    def test_looks_like_init_section(self):
        assert _looks_like_init_section(b"\x00\x00\x00\x18ftyp....")
        assert _looks_like_init_section(b"\x00\x00\x00\x20moov....")
        assert not _looks_like_init_section(b"\xde\xad\xbe\xef\x01\x02\x03\x04")
        assert not _looks_like_init_section(b"sh")


# ── 变体排序/音轨清单/discontinuity 提示(第 31 轮 #2/#3/#4) ──


class TestMasterTracksAndOrder:
    """主播放列表: 变体降序编号、音轨/字幕清单、纯音频变体标注。"""

    MASTER_WITH_MEDIA = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="English",LANGUAGE="en",DEFAULT=YES,AUTOSELECT=YES,URI="audio/en/prog_index.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="Deutsch",LANGUAGE="de",URI="audio/de/prog_index.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",URI="subs/en/prog_index.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=416x234,CODECS="avc1.42e00a,mp4a.40.2",AUDIO="aac"
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2",AUDIO="aac"
mid/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",AUDIO="aac"
high/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=41000,CODECS="mp4a.40.2",AUDIO="aac"
audio/gear0/prog_index.m3u8
"""

    def _dl(self, tmp_path):
        return M3u8Downloader(url="http://x/master.m3u8", save_path=tmp_path / "v.ts")

    @pytest.mark.asyncio
    async def test_variants_sorted_by_bandwidth_desc(self, tmp_path):
        """变体按码率降序编号: [0]=最高清,不按文件序让 [0] 是最差画质。"""
        dl = self._dl(tmp_path)
        dl._base_url = "http://x/"
        dl._parse_master(self.MASTER_WITH_MEDIA)

        assert [v.index for v in dl._variants] == [0, 1, 2, 3]
        assert dl._variants[0].resolution == "1920x1080"
        assert dl._variants[1].resolution == "1280x720"
        assert dl._variants[2].resolution == "416x234"
        assert dl._variants[3].resolution == ""  # 纯音频变体

    @pytest.mark.asyncio
    async def test_media_tracks_parsed(self, tmp_path):
        """#EXT-X-MEDIA 音轨/字幕解析出清单(名称/语言/URI)。"""
        dl = self._dl(tmp_path)
        dl._base_url = "http://x/"
        dl._parse_master(self.MASTER_WITH_MEDIA)

        audios = [t for t in dl._media_tracks if t.type == "AUDIO"]
        subs = [t for t in dl._media_tracks if t.type == "SUBTITLES"]
        assert len(audios) == 2
        assert len(subs) == 1
        assert audios[0].language == "en"
        assert audios[0].is_default is True
        assert audios[0].url == "http://x/audio/en/prog_index.m3u8"
        assert subs[0].uri == "subs/en/prog_index.m3u8"

    @pytest.mark.asyncio
    async def test_variants_listing_shows_tracks_and_audio_label(self, tmp_path):
        """变体清单显示音轨/字幕(含可独立下载 URI),纯音频变体标"音频"。"""
        dl = self._dl(tmp_path)
        dl._base_url = "http://x/"
        dl._parse_master(self.MASTER_WITH_MEDIA)
        dl.progress.variants = dl._variants
        dl.progress.media_tracks = dl._media_tracks
        dl.progress.status = M3u8Status.SELECTING

        text = dl.progress.format_status()

        assert "音轨(2)" in text
        assert "字幕(1)" in text
        assert "audio/en/prog_index.m3u8" in text
        assert "音频" in text  # 纯音频变体标注
        assert "单独下载" in text  # 独立音轨获取指引


class TestDiscontinuityNotice:
    """#EXT-X-DISCONTINUITY 计数与完成提示(第 31 轮 #4)。"""

    @pytest.mark.asyncio
    async def test_discontinuity_counted_and_warned(self, tmp_path):
        dl = M3u8Downloader(url="http://x/m.m3u8", save_path=tmp_path / "v.ts")
        media = (
            "#EXTM3U\n"
            "#EXTINF:4.0,\na.ts\n"
            "#EXT-X-DISCONTINUITY\n"
            "#EXTINF:4.0,\nb.ts\n"
            "#EXT-X-DISCONTINUITY\n"
            "#EXTINF:4.0,\nc.ts\n"
        )
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=MagicMock(
                text=media,
                content=b"",
                raise_for_status=MagicMock(),
                status_code=200,
            )
        )
        await dl.parse(client)
        assert dl.progress.discontinuity_count == 2

        dl.progress.finish(M3u8Status.COMPLETED)
        status = dl.progress.format_status()
        assert "2 处 discontinuity" in status
        assert "试播检查" in status

    @pytest.mark.asyncio
    async def test_no_discontinuity_no_notice(self, tmp_path):
        dl = M3u8Downloader(url="http://x/m.m3u8", save_path=tmp_path / "v.ts")
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=MagicMock(
                text="#EXTM3U\n#EXTINF:4.0,\na.ts\n",
                content=b"",
                raise_for_status=MagicMock(),
                status_code=200,
            )
        )
        await dl.parse(client)
        assert dl.progress.discontinuity_count == 0
        dl.progress.finish(M3u8Status.COMPLETED)
        # 断言提示句式而非裸词 —— tmp_path 目录名含 "discontinuity" 会误伤
        assert "处 discontinuity" not in dl.progress.format_status()

class TestMapIvAmbiguity:
    """IV 差异落在 box 无关字节时按全 0 解并提示(验证 #4)。"""

    def _enc(self, plain: bytes, iv: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        e = Cipher(algorithms.AES(b"K" * 16), modes.CBC(iv)).encryptor()
        return e.update(plain) + e.finalize()

    def test_ambiguous_zero_tail_auto_picks_zero(self, ):
        """IV 差异落在零填充字节: 自动双解对比选全 0 解,无提示。"""
        iv_expl = bytes.fromhex("00" * 15 + "01")  # 与全 0 仅差末位
        plain = b"\x00\x00\x00ftypisom" + b"\x00" * (96 - 12)
        data = self._enc(plain, b"\x00" * 16)  # init 用全 0 加密(无 padding)
        map_info = M3u8Map(
            uri="m.mp4",
            url="http://x/m.mp4",
            key=M3u8Key(method="AES-128", iv=iv_expl.hex(), key_bytes=b"K" * 16),
        )

        out, note = _decrypt_map_section(data, map_info)

        assert out == plain
        assert note == "", "自动双解对比已定,不再报歧义"

    def test_ambiguous_both_nonzero_defaults_key_with_note(self):
        """差异字节双方都非零: 分不出时默认显式 IV + init_iv=zero 提示。"""
        iv_expl = bytes.fromhex("00" * 8 + "01" + "00" * 7)  # 差第 9 字节(major_brand 内)
        plain = b"\x00\x00\x00ftypisom" + b"\x00" * (96 - 12)
        data = self._enc(plain, b"\x00" * 16)
        map_info = M3u8Map(
            uri="m.mp4",
            url="http://x/m.mp4",
            key=M3u8Key(method="AES-128", iv=iv_expl.hex(), key_bytes=b"K" * 16),
        )

        out, note = _decrypt_map_section(data, map_info)

        assert "歧义" in note
        assert "init_iv=zero" in note
        assert out == _decrypt_aes128(data, b"K" * 16, iv_expl)
        # 逃生舱
        out2, _ = _decrypt_map_section(data, map_info, iv_mode="zero")
        assert out2 == plain

    def test_spec_iv_unique_pass_no_note(self):
        iv_expl = bytes.fromhex("01" * 16)  # 与全 0 差 16 字节
        plain = b"\x00\x00\x00\x18ftypisom" + b"\x00" * (96 - 12)
        data = self._enc(plain, iv_expl)  # 规范: init 用显式 IV 加密
        map_info = M3u8Map(
            uri="m.mp4",
            url="http://x/m.mp4",
            key=M3u8Key(method="AES-128", iv=iv_expl.hex(), key_bytes=b"K" * 16),
        )

        out, note = _decrypt_map_section(data, map_info)

        assert out == plain
        assert note == ""

    def test_force_modes(self):
        iv_expl = bytes.fromhex("00" * 15 + "01")
        plain = b"\x00\x00\x00\x18ftypisom" + b"\x00" * (96 - 12)
        data = self._enc(plain, b"\x00" * 16)
        map_info = M3u8Map(
            uri="m.mp4",
            url="http://x/m.mp4",
            key=M3u8Key(method="AES-128", iv=iv_expl.hex(), key_bytes=b"K" * 16),
        )

        out, _ = _decrypt_map_section(data, map_info, iv_mode="zero")
        assert out == plain
