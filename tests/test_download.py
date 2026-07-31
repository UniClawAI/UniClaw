"""下载工具的单元测试。"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.utils.http_download import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    DownloadChunkInfo,
    DownloadRecord,
    DownloadStatus,
    HttpDownloader,
    DownloadProgress,
    calculate_checksum,
    file_format,
    time_format,
    _parse_cookies,
)


# ==================== file_format 测试 ====================


class TestFileFormat:
    """file_format 函数测试。"""

    def test_bytes(self):
        assert file_format(0) == "0.00B"
        assert file_format(512) == "512.00B"
        assert file_format(1023) == "1023.00B"

    def test_kilobytes(self):
        assert file_format(1024) == "1.00KB"
        assert file_format(1536) == "1.50KB"

    def test_megabytes(self):
        assert file_format(1024 * 1024) == "1.00MB"
        assert file_format(1024 * 1024 * 5) == "5.00MB"

    def test_gigabytes(self):
        assert file_format(1024**3) == "1.00GB"

    def test_terabytes(self):
        assert file_format(1024**4) == "1.00TB"

    def test_petabytes(self):
        assert file_format(1024**5) == "1.00PB"

    def test_exabytes(self):
        assert file_format(1024**6) == "1.00EB"

    def test_zettabytes(self):
        assert file_format(1024**7) == "1.00ZB"


# ==================== time_format 测试 ====================


class TestTimeFormat:
    """time_format 函数测试。"""

    def test_seconds(self):
        assert time_format(0) == "0s"
        assert time_format(30) == "30s"
        assert time_format(59) == "59s"

    def test_minutes(self):
        assert time_format(60) == "01:00"
        assert time_format(90) == "01:30"
        assert time_format(3599) == "59:59"

    def test_hours(self):
        assert time_format(3600) == "01:00:00"
        assert time_format(3661) == "01:01:01"

    def test_days(self):
        assert time_format(86400) == "1d00:00:00"
        assert time_format(90000) == "1d01:00:00"


# ==================== calculate_checksum 测试 ====================


class TestCalculateChecksum:
    """calculate_checksum 函数测试。"""

    def test_sha256(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = calculate_checksum(f, "sha256")
        assert result == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_md5(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = calculate_checksum(f, "md5")
        assert result == "5eb63bbbe01eeed093cb22bb8f5acdc3"

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        result = calculate_checksum(f, "sha256")
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ==================== _parse_cookies 测试 ====================


class TestParseCookies:
    """_parse_cookies 函数测试。"""

    def test_basic(self):
        result = _parse_cookies("key1=val1; key2=val2")
        assert result == {"key1": "val1", "key2": "val2"}

    def test_empty(self):
        assert _parse_cookies("") == {}

    def test_no_value(self):
        result = _parse_cookies("key1=val1; invalid; key2=val2")
        assert result == {"key1": "val1", "key2": "val2"}

    def test_value_with_equals(self):
        result = _parse_cookies("token=abc=def")
        assert result == {"token": "abc=def"}


# ==================== DownloadStatus 测试 ====================


class TestDownloadStatus:
    """DownloadStatus 枚举测试。"""

    def test_values(self):
        assert DownloadStatus.PENDING == "pending"
        assert DownloadStatus.DOWNLOADING == "downloading"
        assert DownloadStatus.PAUSED == "paused"
        assert DownloadStatus.COMPLETED == "completed"
        assert DownloadStatus.FAILED == "failed"


# ==================== DownloadProgress 测试 ====================


class TestDownloadProgress:
    """DownloadProgress 数据类测试。"""

    def test_progress_percent(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", total_size=100, downloaded_size=50)
        assert p.progress_percent == 50.0

    def test_progress_percent_zero_total(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", total_size=0)
        assert p.progress_percent == 0.0

    def test_eta_with_speed(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", total_size=1000, downloaded_size=500, speed=100)
        assert p.eta == 5.0

    def test_eta_zero_speed(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", total_size=1000, downloaded_size=500, speed=0)
        assert p.eta == 0.0

    def test_elapsed(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", start_time=0)
        assert p.elapsed == 0.0

    def test_format_status_completed(self):
        p = DownloadProgress(task_id="t", url="u", save_path="/tmp/file", total_size=1024, status=DownloadStatus.COMPLETED, start_time=1)
        result = p.format_status()
        assert "下载完成" in result
        assert "/tmp/file" in result

    def test_format_status_failed(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", status=DownloadStatus.FAILED, error="网络错误")
        result = p.format_status()
        assert "下载失败" in result
        assert "网络错误" in result

    def test_format_status_pending(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", status=DownloadStatus.PENDING)
        assert "等待开始" in p.format_status()

    def test_format_status_paused(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", total_size=1000, downloaded_size=500, status=DownloadStatus.PAUSED)
        result = p.format_status()
        assert "已暂停" in result

    def test_format_status_downloading_with_speed(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", total_size=1000, downloaded_size=500, status=DownloadStatus.DOWNLOADING, speed=100)
        result = p.format_status()
        assert "下载中" in result
        assert "KB/s" in result or "B/s" in result


# ==================== DownloadChunkInfo 测试 ====================


class TestDownloadChunkInfo:
    """DownloadChunkInfo 数据类测试。"""

    def test_creation(self):
        info = DownloadChunkInfo(chunk_size=8192, downloaded=50000, total=100000, speed=1024.0)
        assert info.chunk_size == 8192
        assert info.downloaded == 50000
        assert info.total == 100000
        assert info.speed == 1024.0


# ==================== DownloadRecord 测试 ====================


class TestDownloadRecord:
    """DownloadRecord 类测试。"""

    def test_init(self, tmp_path):
        r = DownloadRecord(tmp_path / "test.progress.json")
        assert r.ranges == []

    def test_set_and_check(self, tmp_path):
        r = DownloadRecord(tmp_path / "test.progress.json")
        r.set_downloaded(0, 1024)
        assert r.check_downloaded(0, 1024) is True
        assert r.check_downloaded(0, 1025) is False

    def test_merge_adjacent(self, tmp_path):
        r = DownloadRecord(tmp_path / "test.progress.json")
        r.set_downloaded(0, 1024)
        r.set_downloaded(1024, 1024)
        assert r.ranges == [(0, 2047)]
        assert r.check_downloaded(0, 2048) is True

    def test_merge_overlapping(self, tmp_path):
        r = DownloadRecord(tmp_path / "test.progress.json")
        r.set_downloaded(0, 1024)
        r.set_downloaded(512, 1024)
        assert r.ranges == [(0, 1535)]

    def test_non_adjacent(self, tmp_path):
        r = DownloadRecord(tmp_path / "test.progress.json")
        r.set_downloaded(0, 1024)
        r.set_downloaded(2048, 1024)
        assert r.ranges == [(0, 1023), (2048, 3071)]

    def test_merge_gap_fills(self, tmp_path):
        r = DownloadRecord(tmp_path / "test.progress.json")
        r.set_downloaded(0, 1024)
        r.set_downloaded(2048, 1024)
        r.set_downloaded(1024, 1024)  # 填补间隔
        assert r.ranges == [(0, 3071)]

    def test_set_zero_size(self, tmp_path):
        r = DownloadRecord(tmp_path / "test.progress.json")
        r.set_downloaded(0, 0)
        assert r.ranges == []

    def test_check_zero_size(self, tmp_path):
        r = DownloadRecord(tmp_path / "test.progress.json")
        assert r.check_downloaded(0, 0) is True

    def test_get_downloaded_size(self, tmp_path):
        r = DownloadRecord(tmp_path / "test.progress.json")
        r.set_downloaded(0, 1024)
        r.set_downloaded(2048, 512)
        assert r.get_downloaded_size() == 1024 + 512

    def test_save_and_load(self, tmp_path):
        f = tmp_path / "test.progress.json"
        r = DownloadRecord(f)
        r.set_downloaded(0, 1024)
        r.set_downloaded(2048, 512)

        # 新实例加载
        r2 = DownloadRecord(f)
        r2.load()
        assert r2.ranges == [(0, 1023), (2048, 2559)]
        assert r2.check_downloaded(0, 1024) is True

    def test_load_nonexistent(self, tmp_path):
        r = DownloadRecord(tmp_path / "nonexistent.json")
        r.load()
        assert r.ranges == []

    def test_load_corrupted(self, tmp_path):
        f = tmp_path / "corrupted.json"
        f.write_text("not json!!!")
        r = DownloadRecord(f)
        r.load()
        assert r.ranges == []

    def test_clear(self, tmp_path):
        f = tmp_path / "test.progress.json"
        r = DownloadRecord(f)
        r.set_downloaded(0, 1024)
        assert f.exists()

        r.clear()
        assert r.ranges == []
        assert not f.exists()

    def test_clear_nonexistent(self, tmp_path):
        r = DownloadRecord(tmp_path / "nonexistent.json")
        r.clear()  # 不应抛异常
        assert r.ranges == []

    def test_ranges_returns_copy(self, tmp_path):
        r = DownloadRecord(tmp_path / "test.progress.json")
        r.set_downloaded(0, 1024)
        ranges = r.ranges
        ranges.clear()
        assert len(r.ranges) == 1  # 原始数据不受影响


# ==================== HttpDownloader 单元测试 ====================


class TestHttpDownloader:
    """HttpDownloader 类测试(不涉及真实网络请求)。"""

    def test_init(self, tmp_path):
        dl = HttpDownloader(
            url="https://example.com/file.zip",
            save_path=tmp_path / "file.zip",
        )
        assert dl.url == "https://example.com/file.zip"
        assert dl.concurrency == DEFAULT_CONCURRENCY
        assert dl.chunk_size == DEFAULT_CHUNK_SIZE
        assert dl.max_retries == DEFAULT_MAX_RETRIES
        assert dl.progress.status == DownloadStatus.PENDING

    def test_init_custom_params(self, tmp_path):
        dl = HttpDownloader(
            url="https://example.com/file.zip",
            save_path=tmp_path / "file.zip",
            concurrency=8,
            chunk_size=2048,
            max_retries=5,
            proxy="http://proxy:8080",
            cookies="a=1; b=2",
        )
        assert dl.concurrency == 8
        assert dl.chunk_size == 2048
        assert dl.max_retries == 5
        assert dl.proxy == "http://proxy:8080"
        assert dl.cookies == "a=1; b=2"

    def test_build_client_kwargs_no_proxy(self, tmp_path):
        dl = HttpDownloader(url="https://example.com", save_path=tmp_path / "f")
        kwargs = dl._build_client_kwargs()
        assert "proxy" not in kwargs
        assert kwargs["timeout"] == DEFAULT_TIMEOUT
        assert kwargs["follow_redirects"] is True

    def test_build_client_kwargs_with_proxy(self, tmp_path):
        dl = HttpDownloader(url="https://example.com", save_path=tmp_path / "f", proxy="http://proxy:8080")
        kwargs = dl._build_client_kwargs()
        assert kwargs["proxy"] == "http://proxy:8080"

    def test_build_client_kwargs_with_cookies(self, tmp_path):
        dl = HttpDownloader(url="https://example.com", save_path=tmp_path / "f", cookies="a=1; b=2")
        kwargs = dl._build_client_kwargs()
        assert kwargs["cookies"] == {"a": "1", "b": "2"}

    def test_pause_resume(self, tmp_path):
        dl = HttpDownloader(url="https://example.com", save_path=tmp_path / "f")
        dl.pause()
        assert dl.progress.status == DownloadStatus.PAUSED
        assert not dl._pause_event.is_set()

        dl.resume()
        assert dl.progress.status == DownloadStatus.DOWNLOADING
        assert dl._pause_event.is_set()

    def test_cancel(self, tmp_path):
        dl = HttpDownloader(url="https://example.com", save_path=tmp_path / "f")
        dl.cancel()
        assert dl.progress.status == DownloadStatus.CANCELLED
        assert dl.progress.error == "用户取消"
        assert dl._cancelled is True
        assert dl._pause_event.is_set()  # 解除暂停以便取消生效

    def test_task_id_unique(self, tmp_path):
        dl1 = HttpDownloader(url="https://example.com", save_path=tmp_path / "f1")
        dl2 = HttpDownloader(url="https://example.com", save_path=tmp_path / "f2")
        assert dl1.task_id != dl2.task_id

    def test_progress_initial_state(self, tmp_path):
        dl = HttpDownloader(url="https://example.com/file.zip", save_path=tmp_path / "file.zip")
        assert dl.progress.total_size == 0
        assert dl.progress.downloaded_size == 0
        assert dl.progress.status == DownloadStatus.PENDING
        assert dl.progress.speed == 0.0
        assert dl.progress.error is None

    @pytest.mark.asyncio
    async def test_start_resets_state(self, tmp_path):
        """start() 应重置 _cancelled 和 _pause_event。"""
        dl = HttpDownloader(url="https://example.com", save_path=tmp_path / "f")
        dl.cancel()
        assert dl._cancelled is True

        # 模拟 start() 在获取文件信息时失败
        with patch.object(dl, '_get_file_info', side_effect=Exception("mock")):
            await dl.start()
        # start() 开头就重置了 _cancelled
        assert dl._cancelled is False

    @pytest.mark.asyncio
    async def test_start_handles_exception(self, tmp_path):
        """start() 异常时应设置 FAILED 状态。"""
        dl = HttpDownloader(url="https://example.com", save_path=tmp_path / "f")
        with patch.object(dl, '_get_file_info', side_effect=ConnectionError("网络错误")):
            result = await dl.start()
        assert result.status == DownloadStatus.FAILED
        assert "网络错误" in result.error

    @pytest.mark.asyncio
    async def test_start_no_content_length(self, tmp_path):
        """服务器不返回 Content-Length 时应失败。"""
        dl = HttpDownloader(url="https://example.com", save_path=tmp_path / "f")
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("uniclaw.utils.http_download.httpx.AsyncClient", return_value=mock_client):
            result = await dl.start()

        assert result.status == DownloadStatus.FAILED
        assert "Content-Length" in result.error

    @pytest.mark.asyncio
    async def test_start_unsupported_range_fallback(self, tmp_path):
        """不支持 Range 时应降级为单连接下载。"""
        dl = HttpDownloader(url="https://example.com/file", save_path=tmp_path / "file")

        # Mock _get_file_info 返回不支持 Range
        with patch.object(dl, '_get_file_info', return_value=(100, False)):
            with patch.object(dl, '_download_full', new_callable=AsyncMock) as mock_full:
                mock_full.return_value = None
                # 需要 mock httpx.AsyncClient 上下文
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)

                with patch("uniclaw.utils.http_download.httpx.AsyncClient", return_value=mock_client):
                    await dl.start()

                mock_full.assert_called_once()
