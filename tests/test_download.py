"""下载工具的单元测试。"""

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
    parse_headers,
    time_format,
    _parse_cookies,
    _RangeNotSupported,
)


def _sha256_hex(data: bytes) -> str:
    """计算字节串的 sha256 十六进制值(测试辅助)。"""
    return hashlib.sha256(data).hexdigest()


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
        assert (
            result == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )

    def test_md5(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = calculate_checksum(f, "md5")
        assert result == "5eb63bbbe01eeed093cb22bb8f5acdc3"

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        result = calculate_checksum(f, "sha256")
        assert (
            result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )


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
        p = DownloadProgress(
            task_id="t", url="u", save_path="s", total_size=100, downloaded_size=50
        )
        assert p.progress_percent == 50.0

    def test_progress_percent_zero_total(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", total_size=0)
        assert p.progress_percent == 0.0

    def test_eta_with_speed(self):
        p = DownloadProgress(
            task_id="t",
            url="u",
            save_path="s",
            total_size=1000,
            downloaded_size=500,
            speed=100,
        )
        assert p.eta == 5.0

    def test_eta_zero_speed(self):
        p = DownloadProgress(
            task_id="t",
            url="u",
            save_path="s",
            total_size=1000,
            downloaded_size=500,
            speed=0,
        )
        assert p.eta == 0.0

    def test_elapsed(self):
        p = DownloadProgress(task_id="t", url="u", save_path="s", start_time=0)
        assert p.elapsed == 0.0

    def test_format_status_completed(self):
        p = DownloadProgress(
            task_id="t",
            url="u",
            save_path="/tmp/file",
            total_size=1024,
            status=DownloadStatus.COMPLETED,
            start_time=1,
        )
        result = p.format_status()
        assert "下载完成" in result
        assert "/tmp/file" in result

    def test_format_status_failed(self):
        p = DownloadProgress(
            task_id="t",
            url="u",
            save_path="s",
            status=DownloadStatus.FAILED,
            error="网络错误",
        )
        result = p.format_status()
        assert "下载失败" in result
        assert "网络错误" in result

    def test_format_status_pending(self):
        p = DownloadProgress(
            task_id="t", url="u", save_path="s", status=DownloadStatus.PENDING
        )
        assert "等待开始" in p.format_status()

    def test_format_status_paused(self):
        p = DownloadProgress(
            task_id="t",
            url="u",
            save_path="s",
            total_size=1000,
            downloaded_size=500,
            status=DownloadStatus.PAUSED,
        )
        result = p.format_status()
        assert "已暂停" in result

    def test_format_status_downloading_with_speed(self):
        p = DownloadProgress(
            task_id="t",
            url="u",
            save_path="s",
            total_size=1000,
            downloaded_size=500,
            status=DownloadStatus.DOWNLOADING,
            speed=100,
        )
        result = p.format_status()
        assert "下载中" in result
        assert "KB/s" in result or "B/s" in result


# ==================== DownloadChunkInfo 测试 ====================


class TestDownloadChunkInfo:
    """DownloadChunkInfo 数据类测试。"""

    def test_creation(self):
        info = DownloadChunkInfo(
            chunk_size=8192, downloaded=50000, total=100000, speed=1024.0
        )
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
        dl = HttpDownloader(
            url="https://example.com",
            save_path=tmp_path / "f",
            proxy="http://proxy:8080",
        )
        kwargs = dl._build_client_kwargs()
        assert kwargs["proxy"] == "http://proxy:8080"

    def test_build_client_kwargs_with_cookies(self, tmp_path):
        dl = HttpDownloader(
            url="https://example.com", save_path=tmp_path / "f", cookies="a=1; b=2"
        )
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
        dl = HttpDownloader(
            url="https://example.com/file.zip", save_path=tmp_path / "file.zip"
        )
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
        with patch.object(dl, "_get_file_info", side_effect=Exception("mock")):
            await dl.start()
        # start() 开头就重置了 _cancelled
        assert dl._cancelled is False

    @pytest.mark.asyncio
    async def test_start_handles_exception(self, tmp_path):
        """start() 异常时应设置 FAILED 状态。"""
        dl = HttpDownloader(url="https://example.com", save_path=tmp_path / "f")
        with patch.object(
            dl, "_get_file_info", side_effect=ConnectionError("网络错误")
        ):
            result = await dl.start()
        assert result.status == DownloadStatus.FAILED
        assert "网络错误" in result.error

    @pytest.mark.asyncio
    async def test_start_no_content_length(self, tmp_path):
        """服务器不返回 Content-Length 时应降级为流式下载（不再报错）。"""
        dl = HttpDownloader(url="https://example.com", save_path=tmp_path / "f")
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.raise_for_status = MagicMock()
        # 模拟流式响应（chunked 编码）
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_resp)
        # 流式上下文管理器
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(return_value=mock_stream)
        # aiter_bytes 返回空迭代器（模拟空文件）
        mock_resp.aiter_bytes = MagicMock(return_value=AsyncMock())
        mock_resp.aiter_bytes.return_value.__aiter__ = MagicMock(return_value=AsyncMock())
        mock_resp.aiter_bytes.return_value.__aiter__.return_value.__anext__ = AsyncMock(side_effect=StopAsyncIteration)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "uniclaw.utils.http_download.httpx.AsyncClient", return_value=mock_client
        ):
            result = await dl.start()

        # 无 Content-Length 时不再报错,降级为流式下载
        assert result.status != DownloadStatus.FAILED

    @pytest.mark.asyncio
    async def test_start_unsupported_range_fallback(self, tmp_path):
        """不支持 Range 时应降级为单连接下载。"""
        dl = HttpDownloader(url="https://example.com/file", save_path=tmp_path / "file")

        # Mock _get_file_info 返回不支持 Range
        with patch.object(dl, "_get_file_info", return_value=(100, False)):
            with patch.object(
                dl, "_download_full", new_callable=AsyncMock
            ) as mock_full:
                mock_full.return_value = None
                # 需要 mock httpx.AsyncClient 上下文
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)

                with patch(
                    "uniclaw.utils.http_download.httpx.AsyncClient",
                    return_value=mock_client,
                ):
                    await dl.start()

                mock_full.assert_called_once()

    @pytest.mark.asyncio
    async def test_range_ignored_falls_back_to_full(self, tmp_path):
        """服务器忽略 Range(返回 200 而非 206)时应降级为全量下载, 且不能把整个文件缓冲进内存。"""
        dl = HttpDownloader(url="https://example.com/file", save_path=tmp_path / "file")
        dl.progress.total_size = 100
        dl._tmp_file.write_bytes(b"\0" * 100)

        with patch.object(dl, "_get_file_info", return_value=(100, True)):
            with patch.object(
                dl,
                "_download_range",
                side_effect=_RangeNotSupported("range ignored"),
            ):
                with patch.object(
                    dl, "_download_full", new_callable=AsyncMock
                ) as mock_full:
                    mock_full.return_value = None
                    mock_client = AsyncMock()
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)

                    with patch(
                        "uniclaw.utils.http_download.httpx.AsyncClient",
                        return_value=mock_client,
                    ):
                        await dl.start()

                    # 检测到 Range 被忽略后必须降级为单连接全量下载
                    mock_full.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_file_info_head_success(self, tmp_path):
        """HEAD 成功时直接从响应头解析文件信息。"""
        dl = HttpDownloader(url="https://example.com/file", save_path=tmp_path / "file")

        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": "2048", "accept-ranges": "bytes"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_resp)
        mock_client.stream = AsyncMock()

        size, supports_range = await dl._get_file_info(mock_client)
        assert size == 2048
        assert supports_range is True
        mock_client.stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_file_info_get_fallback_uses_stream(self, tmp_path):
        """HEAD 失败时应回退到 GET, 且必须用流式请求, 避免缓冲整个文件到内存。"""
        dl = HttpDownloader(url="https://example.com/file", save_path=tmp_path / "file")

        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": "4096", "accept-ranges": "none"}
        mock_resp.raise_for_status = MagicMock()

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.head = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "HEAD not supported", request=MagicMock(), response=MagicMock()
            )
        )
        mock_client.stream = MagicMock(return_value=mock_ctx)

        size, supports_range = await dl._get_file_info(mock_client)
        assert size == 4096
        assert supports_range is False
        # 回退用的是 stream 而非 get, 避免把整个文件读入内存;
        # Accept-Ranges=none 明确否定时不做探测(头缺失才探测)
        mock_client.get.assert_not_called()
        assert mock_client.stream.call_count == 1

    def test_verify_checksum_match(self, tmp_path):
        """checksum 匹配时不抛异常。"""
        dl = HttpDownloader(
            url="https://example.com/file",
            save_path=tmp_path / "file",
            checksum_algorithm="sha256",
            checksum_value="abc123",
        )
        f = tmp_path / "file.tmp"
        f.write_bytes(b"some content")
        dl._tmp_file = f
        # 计算真实 sha256 作为期望值
        dl.checksum_value = calculate_checksum(f, "sha256")
        dl._verify_checksum()  # 不应抛异常

    def test_verify_checksum_mismatch_deletes_tmp(self, tmp_path):
        """checksum 不匹配时应抛异常并删除临时文件与记录。"""
        dl = HttpDownloader(
            url="https://example.com/file",
            save_path=tmp_path / "file",
            checksum_algorithm="sha256",
            checksum_value="deadbeef",
        )
        f = tmp_path / "file.tmp"
        f.write_bytes(b"some content")
        dl._tmp_file = f
        dl._record.set_downloaded(0, 12)

        with pytest.raises(ValueError, match="校验失败"):
            dl._verify_checksum()
        assert not f.exists(), "校验失败时应删除临时文件"
        assert dl._record.get_downloaded_size() == 0, "校验失败时应清空记录"

    @pytest.mark.asyncio
    async def test_download_full_with_checksum(self, tmp_path):
        """不支持 Range 的单连接下载也应执行 checksum 校验。"""
        content = b"hello checksum full download"
        dl = HttpDownloader(
            url="https://example.com/file",
            save_path=tmp_path / "file",
            max_retries=0,
            checksum_algorithm="sha256",
            checksum_value=_sha256_hex(content),
        )
        dl.progress.total_size = len(content)

        async def fake_aiter_bytes(chunk_size):
            for i in range(0, len(content), chunk_size):
                yield content[i : i + chunk_size]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_bytes = fake_aiter_bytes

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_ctx)

        await dl._download_full(mock_client)

        assert (tmp_path / "file").read_bytes() == content
        assert dl.progress.status == DownloadStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_download_full_checksum_mismatch_fails(self, tmp_path):
        """不支持 Range 时 checksum 不匹配应失败并清理临时文件。"""
        content = b"hello checksum full download"
        dl = HttpDownloader(
            url="https://example.com/file",
            save_path=tmp_path / "file",
            max_retries=0,
            checksum_algorithm="sha256",
            checksum_value="wrongchecksum",
        )
        dl.progress.total_size = len(content)

        async def fake_aiter_bytes(chunk_size):
            for i in range(0, len(content), chunk_size):
                yield content[i : i + chunk_size]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_bytes = fake_aiter_bytes

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_ctx)

        with pytest.raises(ValueError, match="校验失败"):
            await dl._download_full(mock_client)

        # 校验失败后临时文件应被删除, 最终文件不存在
        assert not dl._tmp_file.exists()
        assert not (tmp_path / "file").exists()


class TestGetStatusUnknownId:
    """task_id 不存在 与 "根本没有任务" 必须可区分。

    回归测试: 两者曾返回同一句"当前没有下载任务。",调用方无法判断是
    "ID 不存在"还是"无任务"。
    """

    def test_unknown_id_reports_not_found(self):
        from uniclaw.tools.download.manager import HttpDownloadManager

        mgr = HttpDownloadManager()
        out = mgr.get_status("nonexistent-task-xyz")
        assert "未找到任务" in out
        assert "nonexistent-task-xyz" in out
        assert "当前没有下载任务" not in out

    def test_unknown_id_lists_available(self):
        from uniclaw.tools.download.manager import HttpDownloadManager

        mgr = HttpDownloadManager()
        dl = MagicMock()
        dl.progress.format_status.return_value = "下载中"
        mgr._tasks["t1"] = dl

        out = mgr.get_status("nonexistent-task-xyz")
        assert "未找到任务" in out
        assert "t1" in out  # 列出可用 ID, 便于自纠

    def test_empty_list_still_says_no_tasks(self):
        from uniclaw.tools.download.manager import HttpDownloadManager

        mgr = HttpDownloadManager()
        assert mgr.get_status("") == "当前没有下载任务。"


# ==================== 网络错误分类测试 ====================


class TestNetworkErrorExplain:
    """网络层错误分类: 保留原始错误,追加人类可读解释。"""

    def test_dns_failure_explained(self):
        """DNS 解析失败 → 追加"域名无法解析"。"""
        from uniclaw.utils.http_download import format_network_error

        text = format_network_error(OSError("[Errno 11001] getaddrinfo failed"))
        assert "getaddrinfo failed" in text  # 原始错误保留
        assert "域名无法解析" in text

    def test_connection_refused_explained(self):
        """连接被拒 → 追加"连接被拒绝"。"""
        from uniclaw.utils.http_download import format_network_error

        text = format_network_error(
            ConnectionRefusedError("[WinError 10061] 由于目标计算机积极拒绝,无法连接")
        )
        assert "WinError 10061" in text
        assert "连接被拒绝" in text

    def test_timeout_explained(self):
        """超时 → 追加"连接超时"。"""
        from uniclaw.utils.http_download import format_network_error

        text = format_network_error(TimeoutError("timed out"))
        assert "timed out" in text
        assert "连接超时" in text

    def test_original_error_not_replaced(self):
        """解释是追加,不替换原始错误(技术人员要看原文)。"""
        from uniclaw.utils.http_download import format_network_error

        original = "[Errno 11001] getaddrinfo failed: 'no-such-host.invalid'"
        text = format_network_error(OSError(original))
        assert original in text
        assert text.index(original) < text.index("域名无法解析")

    def test_non_network_error_has_no_hint(self):
        """非网络错误不追加网络解释。"""
        from uniclaw.utils.http_download import format_network_error

        text = format_network_error(ValueError("校验失败: 期望 x, 实际 y"))
        assert "校验失败" in text
        assert "域名无法解析" not in text
        assert "连接被拒绝" not in text
        assert "连接超时" not in text

    def test_cause_chain_preserved(self):
        """包装异常(IOError from 根因)也要保留根因原文。"""
        from uniclaw.utils.http_download import format_network_error

        try:
            try:
                raise OSError("[Errno 11001] getaddrinfo failed")
            except OSError as root:
                raise IOError("下载失败: getaddrinfo failed") from root
        except IOError as wrapped:
            text = format_network_error(wrapped)
        assert "getaddrinfo failed" in text
        assert "域名无法解析" in text

    @pytest.mark.asyncio
    async def test_start_dns_failure_gets_explanation(self, tmp_path):
        """start() 失败时 progress.error 带人类可读解释,不只是原始错误。"""
        dl = HttpDownloader(url="https://no-such-host.invalid", save_path=tmp_path / "f")
        with patch.object(
            dl,
            "_get_file_info",
            side_effect=OSError("[Errno 11001] getaddrinfo failed"),
        ):
            result = await dl.start()
        assert result.status == DownloadStatus.FAILED
        assert "getaddrinfo failed" in result.error
        assert "域名无法解析" in result.error
        assert "下载失败" in result.format_status()
        assert "域名无法解析" in result.format_status()


# ==================== parse_headers 测试 ====================


class TestParseHeaders:
    """parse_headers 解析自定义请求头。"""

    def test_empty(self):
        assert parse_headers("") == {}
        assert parse_headers("   ") == {}

    def test_json_object(self):
        result = parse_headers('{"Referer": "https://a.com/", "User-Agent": "UA/1.0"}')
        assert result == {"Referer": "https://a.com/", "User-Agent": "UA/1.0"}

    def test_line_format(self):
        result = parse_headers("Referer: https://a.com/\nUser-Agent: UA/1.0")
        assert result == {"Referer": "https://a.com/", "User-Agent": "UA/1.0"}

    def test_colon_in_value(self):
        """值里含冒号(如 URL)不应被截断。"""
        result = parse_headers("Referer: https://a.com/path?q=1")
        assert result == {"Referer": "https://a.com/path?q=1"}

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            parse_headers('{"Referer": ')

    def test_non_object_json_raises(self):
        with pytest.raises(ValueError):
            parse_headers('["Referer"]')

    def test_line_without_colon_raises(self):
        with pytest.raises(ValueError):
            parse_headers("just-a-header")


# ==================== 实时进度/幽灵进度 测试 ====================


def _make_range_response(status_code: int, chunks: list[bytes]):
    """构造模拟的流式响应对象(206 + 分块体)。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()

    async def aiter_bytes(_size=None):
        for c in chunks:
            yield c

    resp.aiter_bytes = MagicMock(return_value=aiter_bytes())
    return resp


def _make_stream_ctx(resp):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestIncrementalProgress:
    """分块边写边计数: 状态查询实时反映真实写入量。"""

    @pytest.mark.asyncio
    async def test_progress_updates_per_chunk(self, tmp_path):
        """range 下载中每到一块就应更新 downloaded_size,而非整个 range 完成后才更新。"""
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        dl.progress.total_size = 12
        dl._tmp_file.write_bytes(b"\0" * 12)

        seen: list[int] = []

        async def cb(info: DownloadChunkInfo):
            seen.append(info.downloaded)

        resp = _make_range_response(206, [b"aaaa", b"bbbb", b"cccc"])
        client = AsyncMock()
        client.stream = MagicMock(return_value=_make_stream_ctx(resp))

        n = await dl._download_range(client, 0, 11, cb)
        assert n == 12
        assert dl.progress.downloaded_size == 12
        # 每块都有一次回调,且进度逐块递增(不是只在末尾一次性跳变)
        assert seen == [4, 8, 12]
        # 落盘内容与进度一致
        assert dl._tmp_file.read_bytes()[:12] == b"aaaabbbbcccc"
        # 完整 range 才记入续传记录
        assert dl._record.check_downloaded(0, 12)

    @pytest.mark.asyncio
    async def test_retry_rolls_back_partial_count(self, tmp_path):
        """本次尝试失败重下时,已计数字节要回退,不重复累计。"""
        dl = HttpDownloader(
            url="https://example.com/f", save_path=tmp_path / "f", max_retries=1
        )
        dl.progress.total_size = 8
        dl._tmp_file.write_bytes(b"\0" * 8)

        # 第一次尝试: 只收到一半就断开(不完整 range → IOError → 重试)
        resp_fail = _make_range_response(206, [b"aaaa"])
        resp_ok = _make_range_response(206, [b"aaaa", b"bbbb"])
        client = AsyncMock()
        client.stream = MagicMock(
            side_effect=[_make_stream_ctx(resp_fail), _make_stream_ctx(resp_ok)]
        )

        n = await dl._download_range(client, 0, 7, None)
        assert n == 8
        # 只按最终成功的 8 字节计一次
        assert dl.progress.downloaded_size == 8
        assert dl._record.check_downloaded(0, 8)

    @pytest.mark.asyncio
    async def test_stale_record_without_tmp_is_cleared(self, tmp_path):
        """有进度记录但没有 .tmp 数据(幽灵进度)时不得跳过下载。"""
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        # 只有 progress.json,没有 .tmp
        dl._record.set_downloaded(0, 5)
        assert dl._record.get_downloaded_size() == 5

        calls = []

        async def fake_range(client, start, end, callback=None):
            calls.append((start, end))
            return end - start + 1

        with patch.object(dl, "_get_file_info", return_value=(10, True)):
            with patch.object(dl, "_download_range", new=fake_range):
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                with patch(
                    "uniclaw.utils.http_download.httpx.AsyncClient",
                    return_value=mock_client,
                ):
                    await dl.start()

        # 记录已失效: 全部 range 都要重新下载,而不是被幽灵记录"跳过"
        assert calls == [(0, 9)]

    @pytest.mark.asyncio
    async def test_tmp_size_mismatch_resets(self, tmp_path):
        """.tmp 大小与远端文件不符(远端已更新)时,旧进度不可信,应重新下载。"""
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        dl._tmp_file.write_bytes(b"\0" * 3)  # 大小 3,远端是 10
        dl._record.set_downloaded(0, 3)

        calls = []

        async def fake_range(client, start, end, callback=None):
            calls.append((start, end))
            return end - start + 1

        with patch.object(dl, "_get_file_info", return_value=(10, True)):
            with patch.object(dl, "_download_range", new=fake_range):
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                with patch(
                    "uniclaw.utils.http_download.httpx.AsyncClient",
                    return_value=mock_client,
                ):
                    await dl.start()

        assert calls == [(0, 9)], "旧进度应被丢弃,全部重新下载"

    @pytest.mark.asyncio
    async def test_resume_restores_downloaded_size(self, tmp_path):
        """续传时已下字节从记录恢复,状态显示不再从 0 开始。"""
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        dl._tmp_file.write_bytes(b"\0" * 10)
        dl._record.set_downloaded(0, 4)

        with patch.object(dl, "_get_file_info", return_value=(10, True)):
            with patch.object(
                dl, "_download_range", new_callable=AsyncMock, return_value=6
            ):
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                with patch(
                    "uniclaw.utils.http_download.httpx.AsyncClient",
                    return_value=mock_client,
                ):
                    await dl.start()

        # 已下 4 字节从记录恢复(mock 的 range 不计数,用于验证恢复本身)
        assert dl.progress.downloaded_size == 4


# ── 超长路径/残留清理/取消语义/同目标互斥(第 27 轮 #4/#7/#9-#11/#14) ──


class TestLongPathAndCleanup:
    """超长文件名场景的暂存命名、路径提示与残留 GC。"""

    def test_stable_id_short_even_for_long_names(self, tmp_path):
        """旁路暂存文件名要短 —— 长文件名 + `.{id}.tmp` 会顶破 MAX_PATH。"""
        from uniclaw.utils.http_download import _stable_id

        long_name = "x" * 200
        sid = _stable_id("http://u", tmp_path / f"{long_name}.dat")
        assert len(sid) <= 40, f"暂存标识过长: {sid}"
        tmp_name = f".{sid}.tmp"
        assert len(tmp_name) <= 50

    def test_path_length_hint_on_not_found(self, tmp_path):
        """ENOENT + 超长路径要提示 MAX_PATH,而不是报"找不到文件"了事。"""
        from uniclaw.utils.http_download import _path_length_hint

        long_path = tmp_path / ("y" * 220)
        exc = FileNotFoundError(2, "No such file or directory")
        hint = _path_length_hint(long_path, long_path, exc)
        assert "路径过长" in hint
        assert "MAX_PATH" in hint

    def test_path_length_hint_absent_for_real_missing(self, tmp_path):
        """短路径的文件不存在不属于路径过长,不能乱提示。"""
        from uniclaw.utils.http_download import _path_length_hint

        exc = FileNotFoundError(2, "No such file or directory")
        assert _path_length_hint(tmp_path / "a.dat", tmp_path / "b.tmp", exc) == ""

    @pytest.mark.asyncio
    async def test_error_no_double_prefix(self, tmp_path):
        """单连接失败报错不能是"下载失败: 下载失败: ..."。"""
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        with patch.object(dl, "_get_file_info", return_value=(10, False)):
            with patch.object(
                dl,
                "_download_full",
                side_effect=IOError("单连接下载失败: boom"),
            ):
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                with patch(
                    "uniclaw.utils.http_download.httpx.AsyncClient",
                    return_value=mock_client,
                ):
                    result = await dl.start()
        assert result.status == DownloadStatus.FAILED
        assert "下载失败: 下载失败" not in result.format_status()

    def test_cleanup_removes_tmp_and_record(self, tmp_path):
        """cleanup() 清掉 .tmp 与 progress.json,remove 任务时不再留孤儿。"""
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        dl._tmp_file.write_bytes(b"\0" * 10)
        dl._record.set_downloaded(0, 5)
        assert dl._tmp_file.exists()

        dl.cleanup()

        assert not dl._tmp_file.exists()
        assert not dl._record._file.exists()


class TestCancelSemantics:
    """取消语义: 已结束任务不改状态;进行中取消可续传(第 27 轮 #11)。"""

    def test_cancel_after_completed_ignored(self, tmp_path):
        """完成后 cancel 不能把状态标成"已取消"——文件已就绪。"""
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        dl.progress.finish(DownloadStatus.COMPLETED)

        dl.cancel()

        assert dl.progress.status == DownloadStatus.COMPLETED
        assert dl._cancelled is False

    def test_manager_cancel_finished_task_reports_ignored(self, tmp_path):
        """管理器对已结束任务的取消请求如实回报"已忽略"。"""
        from uniclaw.tools.download.manager import HttpDownloadManager

        mgr = HttpDownloadManager()
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        dl.progress.finish(DownloadStatus.COMPLETED)
        mgr._tasks[dl.task_id] = dl

        msg = mgr.cancel(dl.task_id)

        assert "已忽略" in msg or "已结束" in msg
        assert dl.progress.status == DownloadStatus.COMPLETED

    def test_manager_remove_task_cleans_up(self, tmp_path):
        """remove 任务时清理断点残留(第 27 轮 #7)。"""
        from uniclaw.tools.download.manager import HttpDownloadManager

        mgr = HttpDownloadManager()
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        dl.progress.finish(DownloadStatus.CANCELLED, "用户取消")
        dl._tmp_file.write_bytes(b"\0" * 4)
        dl._record.set_downloaded(0, 4)
        mgr._tasks[dl.task_id] = dl

        msg = mgr.remove_task(dl.task_id)

        assert "已删除" in msg
        assert not dl._tmp_file.exists(), "remove 后 .tmp 不得残留"
        assert not dl._record._file.exists()


class TestSubmitSameTargetGuard:
    """同一保存路径禁止并发任务(第 27 轮 #14: 并发互踩丢产物)。"""

    @pytest.mark.asyncio
    async def test_second_submit_same_path_rejected(self, tmp_path):
        from uniclaw.tools.download.manager import HttpDownloadManager

        mgr = HttpDownloadManager()
        config = MagicMock()
        dl1 = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        dl1.progress.finish(DownloadStatus.CANCELLED)
        mgr._tasks[dl1.task_id] = dl1  # 已结束的不占道

        dl2 = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        mgr.submit(dl2, config)  # 进行中

        dl3 = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        with pytest.raises(ValueError, match="同一保存路径"):
            mgr.submit(dl3, config)
        if dl2.task:
            dl2.task.cancel()

    @pytest.mark.asyncio
    async def test_submit_different_path_ok(self, tmp_path):
        from uniclaw.tools.download.manager import HttpDownloadManager

        mgr = HttpDownloadManager()
        config = MagicMock()
        dl1 = HttpDownloader(url="https://a/f", save_path=tmp_path / "a")
        dl2 = HttpDownloader(url="https://b/f", save_path=tmp_path / "b")
        id1 = mgr.submit(dl1, config)
        id2 = mgr.submit(dl2, config)
        assert id1 != id2
        for dl in (dl1, dl2):
            if dl.task:
                dl.task.cancel()


class TestToolInputValidation:
    """chunk_size/concurrency 非法值拒绝(第 27 轮 #9/#10)。"""

    @pytest.mark.asyncio
    async def test_chunk_size_zero_rejected(self, tmp_path):
        from uniclaw.tools.base import ToolRuntime
        from uniclaw.tools.download.http_tools import http_download

        config = MagicMock()
        config.root_dir = tmp_path
        rt = ToolRuntime(config=config)
        result = await http_download(
            url="https://example.com/f",
            save_path=str(tmp_path / "f"),
            chunk_size=0,
            tool_runtime=rt,
        )
        assert "TOOL_ERROR" in result
        assert "chunk_size" in result

    @pytest.mark.asyncio
    async def test_concurrency_negative_rejected(self, tmp_path):
        from uniclaw.tools.base import ToolRuntime
        from uniclaw.tools.download.http_tools import http_download

        config = MagicMock()
        config.root_dir = tmp_path
        rt = ToolRuntime(config=config)
        result = await http_download(
            url="https://example.com/f",
            save_path=str(tmp_path / "f"),
            concurrency=-3,
            tool_runtime=rt,
        )
        assert "TOOL_ERROR" in result
        assert "concurrency" in result

class TestMissingIn:
    """DownloadRecord.missing_in: 按字节缺口补下而非整 range 重下。"""

    def test_full_gap(self):
        rec = DownloadRecord(Path("nope.json"))
        assert rec.missing_in(0, 99) == [(0, 99)]

    def test_partial_gap(self):
        rec = DownloadRecord(Path("nope.json"))
        rec.set_downloaded(0, 40, persist=False)
        assert rec.missing_in(0, 99) == [(40, 99)]

    def test_multi_gap(self):
        rec = DownloadRecord(Path("nope.json"))
        rec.set_downloaded(10, 10, persist=False)  # [10,19]
        rec.set_downloaded(30, 10, persist=False)  # [30,39]
        assert rec.missing_in(0, 49) == [(0, 9), (20, 29), (40, 49)]

    def test_covered(self):
        rec = DownloadRecord(Path("nope.json"))
        rec.set_downloaded(0, 100, persist=False)
        assert rec.missing_in(0, 99) == []


class TestPartialResume:
    """取消后的部分字节不重下(汇总 #2)。"""

    @pytest.mark.asyncio
    async def test_partial_range_resumed_not_refetched(self, tmp_path):
        dl = HttpDownloader(
            url="https://example.com/f",
            save_path=tmp_path / "f",
            chunk_size=64,
        )
        dl._tmp_file.write_bytes(b"\0" * 100)
        dl._record.set_downloaded(0, 30)  # 已下 30 字节(partial range)
        dl._record.save()

        fetched: list[tuple[int, int]] = []

        async def fake_range(client, start, end, callback=None):
            fetched.append((start, end))
            dl._record.set_downloaded(start, end - start + 1, persist=False)
            dl._count_received(end - start + 1)
            dl._record.save()
            return end - start + 1

        with patch.object(dl, "_get_file_info", return_value=(100, True)):
            with patch.object(dl, "_download_range", new=fake_range):
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                with patch(
                    "uniclaw.utils.http_download.httpx.AsyncClient",
                    return_value=mock_client,
                ):
                    await dl.start()

        # 只补 30..99 的缺口,不重下 0..29
        # 缺口按 chunk 边界切分下发,但 0..29 绝不能重下
        assert all(start >= 30 for start, _ in fetched), f"重下了已有字节: {fetched}"
        assert sum(e - s + 1 for s, e in fetched) == 70

    def test_cancel_persists_partial_record(self, tmp_path):
        """取消时部分区间落盘 —— 不落盘等于没有续传。"""
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        dl._record.set_downloaded(0, 10, persist=False)  # 内存累积

        dl.cancel()

        assert dl._record._file.exists()
        assert dl._record.get_downloaded_size() == 10

    def test_progress_percent_capped(self, tmp_path):
        """显示层百分比封顶 100%。"""
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        dl.progress.total_size = 100
        dl.progress.downloaded_size = 139
        assert dl.progress.progress_percent == 100.0

class TestRangeProbeWhenHeaderAbsent:
    """Accept-Ranges 头缺失时探测 Range 支持,避免误走全量路径(验证 #2)。"""

    @pytest.mark.asyncio
    async def test_missing_header_triggers_probe(self):
        dl = HttpDownloader(url="https://example.com/f", save_path="unused")

        head_resp = MagicMock()
        head_resp.headers = {"content-length": "100"}  # 无 accept-ranges
        head_resp.raise_for_status = MagicMock()

        probe_resp = MagicMock()
        probe_resp.status_code = 206
        probe_ctx = MagicMock()
        probe_ctx.__aenter__ = AsyncMock(return_value=probe_resp)
        probe_ctx.__aexit__ = AsyncMock(return_value=False)

        client = AsyncMock()
        client.head = AsyncMock(return_value=head_resp)
        client.stream = MagicMock(return_value=probe_ctx)

        size, supports = await dl._get_file_info(client)
        assert size == 100
        assert supports is True, "206 探测成功应视为支持 Range"
        client.stream.assert_called_once()
        probe_headers = client.stream.call_args.kwargs.get("headers")
        assert probe_headers["Range"] == "bytes=0-0"

    @pytest.mark.asyncio
    async def test_explicit_none_respected(self):
        """明确 Accept-Ranges: none 不探测,尊重服务器。"""
        dl = HttpDownloader(url="https://example.com/f", save_path="unused")

        head_resp = MagicMock()
        head_resp.headers = {"content-length": "100", "accept-ranges": "none"}
        head_resp.raise_for_status = MagicMock()

        client = AsyncMock()
        client.head = AsyncMock(return_value=head_resp)

        size, supports = await dl._get_file_info(client)
        assert supports is False
        client.stream.assert_not_called()

# ── http 完整文件跳过 / webSearch limit / ipython 阈值(第 34 轮 #5/#6/#7) ──


class TestMiscLowFixes:
    @pytest.mark.asyncio
    async def test_existing_complete_file_skipped(self, tmp_path):
        """目标文件已存在且大小一致 → 跳过重下并说明。"""
        dl = HttpDownloader(url="https://example.com/f", save_path=tmp_path / "f")
        (tmp_path / "f").write_bytes(b"x" * 100)

        with patch.object(dl, "_get_file_info", return_value=(100, True)):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            with patch(
                "uniclaw.utils.http_download.httpx.AsyncClient",
                return_value=mock_client,
            ):
                progress = await dl.start()

        assert progress.status == DownloadStatus.COMPLETED
        assert "跳过下载" in progress.skipped_note
        assert (tmp_path / "f").read_bytes() == b"x" * 100

    @pytest.mark.asyncio
    async def test_websearch_limit_zero_rejected(self):
        from uniclaw.tools.base import ToolRuntime
        from uniclaw.tools.search.tools import webSearch

        rt = ToolRuntime(config=MagicMock())
        result = await webSearch(query="q", limit=0, tool_runtime=rt)
        assert "TOOL_ERROR" in result
        assert "limit" in result

    def test_ipython_inspect_threshold(self):
        from uniclaw.tools.ipython import kernel

        assert kernel.INSPECT_TEXT_LIMIT <= 1500
