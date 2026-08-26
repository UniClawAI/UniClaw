"""ConPTY 会话封装 — 让 shell 在伪控制台中运行, 获得原生终端行为。

管道模式下 cmd/bash 不输出提示符、回显行为怪异、中文乱码;
ConPTY(Windows 伪控制台)让程序以为连接了真实终端:
- 提示符与命令同行显示
- 单份回显(远端回显, 无需本地 echo)
- 输出为 UTF-8 + VT 序列, 中文原生正常

本模块用读线程把 PTY 输出拆为两路:
- raw: 原始字节写入 log 文件, 供查看器窗口透传渲染(完整终端体验)
- clean: 剥离 VT 序列后的纯文本行, 供 AI 的 monitor_output / pattern 匹配
"""

import asyncio
import queue
import re
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

from .models import Monitor, MonitorStatus

_CSI_RE = re.compile(r"\x1b\[([0-9:;<=>?]*)([@-~])")
_WIDE_TAIL = "\0"


class VtLineParser:
    """把 VT 字节流还原为当前物理行,供 AI 读取最终显示状态。

    P0 仅维护单行；全屏 TUI 的跨行定位仍不在本解析器支持范围内。
    """

    def __init__(self, on_commit):
        self._on_commit = on_commit
        self._cells: list[str] = []
        self._cursor = 0
        self._pending = ""

    def snapshot(self) -> str:
        """返回当前屏幕行快照,宽字符的占位格不输出。"""
        return "".join(cell for cell in self._cells if cell != _WIDE_TAIL).rstrip()

    def feed(self, text: str):
        """逐字符消费一个 chunk;不完整的 VT 序列留到下一块。"""
        if not text:
            return
        text = self._pending + text
        self._pending = ""
        last_esc = text.rfind("\x1b")
        if last_esc >= 0:
            tail = text[last_esc:]
            if self._sequence_incomplete(tail):
                self._pending = tail
                text = text[:last_esc]

        i = 0
        try:
            while i < len(text):
                if text[i] == "\x1b":
                    i = self._consume_escape(text, i)
                else:
                    self._put(text[i])
                    i += 1
        except Exception:
            # PTY 偶发坏数据不能让处理线程退出；保留已经解析的屏幕状态。
            return

    @staticmethod
    def _sequence_incomplete(tail: str) -> bool:
        if tail == "\x1b":
            return True
        if tail.startswith("\x1b["):
            return _CSI_RE.match(tail) is None
        if tail.startswith(("\x1b]", "\x1bP", "\x1b^", "\x1b_")):
            return "\x07" not in tail and "\x1b\\" not in tail
        return False

    def _consume_escape(self, text: str, i: int) -> int:
        n = len(text)
        if i + 1 >= n:
            return n
        kind = text[i + 1]
        if kind in "]P^_":
            i += 2
            while i < n and text[i] not in "\x07\x1b":
                i += 1
            if i < n and text[i] == "\x1b" and i + 1 < n and text[i + 1] == "\\":
                return i + 2
            return min(i + 1, n)
        match = _CSI_RE.match(text, i)
        if match:
            self._handle_csi(match.group(1), match.group(2))
            return match.end()
        # ESC ( B 等字符集切换序列含一个附加字符。
        return min(i + (3 if kind in "()*+,-./" else 2), n)

    def _handle_csi(self, params: str, command: str):
        # DEC 私有模式、颜色和未知命令不改变单行布局。
        if any(ch in params for ch in "?><:="):
            return
        values = (
            [int(value) if value else 0 for value in params.split(";")]
            if params
            else []
        )

        def arg(default: int = 1) -> int:
            return values[0] or default if values else default

        if command == "C":
            self._cursor = min(self._cursor + arg(), len(self._cells))
        elif command == "D":
            self._cursor = max(self._cursor - arg(), 0)
        elif command == "G":
            self._cursor = min(max(arg() - 1, 0), len(self._cells))
        elif command in ("H", "f"):
            # CUP/HVP: ESC[row;colH — ConPTY 键入回显与 IME 组合重绘均以此
            # 逐字符定位(row 恒为当前物理行, 如 30;26H)。单行模型无法跟踪
            # 行号, 仅采用列号: cursor = col-1。缺省参数均为 1(ESC[H=原点)。
            # 不设上限: _put_printable 会按需补空格, snapshot 会 rstrip 尾部。
            col = values[1] if len(values) > 1 else 1
            self._cursor = max((col or 1) - 1, 0)
        elif command == "K":
            mode = arg(0)
            if mode == 0:
                del self._cells[self._cursor :]
            elif mode == 1:
                del self._cells[: self._cursor + 1]
                self._cursor = 0
            elif mode == 2:
                self._cells.clear()
                self._cursor = 0
        elif command == "J" and arg(0) == 2:
            self._cells.clear()
            self._cursor = 0

    def _put(self, ch: str):
        if ch == "\n":
            self._on_commit(self.snapshot())
            self._cells.clear()
            self._cursor = 0
            return
        if ch == "\r":
            self._cursor = 0
            return
        if ch == "\x08":
            self._cursor = max(self._cursor - 1, 0)
            return
        if ch == "\x7f":
            self._delete_before_cursor()
            return
        if ch == "\t":
            for _ in range(8 - self._cursor % 8):
                self._put_printable(" ", 1)
            return
        if ord(ch) < 32:
            return
        if unicodedata.combining(ch):
            if self._cursor:
                index = self._cursor - 1
                if self._cells[index] == _WIDE_TAIL and index:
                    index -= 1
                self._cells[index] += ch
            return
        self._put_printable(ch, 2 if unicodedata.east_asian_width(ch) in "WF" else 1)

    def _put_printable(self, ch: str, width: int):
        while len(self._cells) < self._cursor:
            self._cells.append(" ")
        if width == 2 and self._cursor == len(self._cells):
            self._cells.append(ch)
            self._cells.append(_WIDE_TAIL)
            self._cursor += 2
            return
        self._clear_wide_at(self._cursor)
        if width == 2:
            self._clear_wide_at(self._cursor + 1)
            while len(self._cells) <= self._cursor + 1:
                self._cells.append(" ")
            self._cells[self._cursor] = ch
            self._cells[self._cursor + 1] = _WIDE_TAIL
        else:
            if self._cursor == len(self._cells):
                self._cells.append(ch)
            else:
                self._cells[self._cursor] = ch
        self._cursor += width

    def _clear_wide_at(self, index: int):
        if index >= len(self._cells):
            return
        if self._cells[index] == _WIDE_TAIL and index:
            self._cells[index - 1] = " "
        elif index + 1 < len(self._cells) and self._cells[index + 1] == _WIDE_TAIL:
            self._cells[index + 1] = " "

    def _delete_before_cursor(self):
        if not self._cursor:
            return
        index = self._cursor - 1
        if self._cells[index] == _WIDE_TAIL and index:
            del self._cells[index - 1 : index + 1]
            self._cursor -= 2
        else:
            del self._cells[index]
            self._cursor -= 1


class ScreenBuffer:
    """维护 ConPTY 可见画面的网格状态, 供 monitor_screen 抓取当前屏幕全部内容。

    与 VtLineParser(单行提交模型)并行消费同一份 VT 流: 前者还原 AI 可读的
    输出行流, 本类还原窗口此刻显示的画面 —— 提示符同行残留、尚未回车的键入
    以及 TUI 全屏界面都只存在于屏幕上, 不会进入 output_lines。
    行为对齐 viewer.py 的虚拟屏幕(用户在窗口里看到什么, 这里就是什么)。
    """

    def __init__(self, rows: int = 30, cols: int = 120):
        self._lock = threading.Lock()
        self._rows = max(int(rows), 1)
        self._cols = max(int(cols), 1)
        # 单元格只存字符不带颜色: 抓屏给 AI 读的是纯文本
        self._grid = [[" "] * self._cols for _ in range(self._rows)]
        self._row = 0
        self._col = 0
        # 不完整的 VT 序列留到下一个 chunk(与 VtLineParser 相同的边界策略)
        self._pending = ""

    def text(self) -> str:
        """整屏渲染: 宽字符占位格跳过, 每行 rstrip, 折叠尾部空行。"""
        with self._lock:
            lines = [
                "".join(cell for cell in row if cell != _WIDE_TAIL).rstrip()
                for row in self._grid
            ]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def resize(self, rows: int, cols: int):
        """窗口尺寸变化: 重建网格并保留旧内容交集(viewer 同款策略)。"""
        rows = max(int(rows), 1)
        cols = max(int(cols), 1)
        with self._lock:
            if rows == self._rows and cols == self._cols:
                return
            old_grid, old_rows, old_cols = self._grid, self._rows, self._cols
            self._rows, self._cols = rows, cols
            self._grid = [[" "] * cols for _ in range(rows)]
            for r in range(min(old_rows, rows)):
                for c in range(min(old_cols, cols)):
                    self._grid[r][c] = old_grid[r][c]
            self._row = min(self._row, rows - 1)
            self._col = min(self._col, cols - 1)

    def feed(self, text: str):
        """逐字符消费一个 chunk; 不完整的 VT 序列留到下一块。"""
        if not text:
            return
        text = self._pending + text
        self._pending = ""
        last_esc = text.rfind("\x1b")
        if last_esc >= 0:
            tail = text[last_esc:]
            if VtLineParser._sequence_incomplete(tail):
                self._pending = tail
                text = text[:last_esc]
        i = 0
        with self._lock:
            try:
                while i < len(text):
                    if text[i] == "\x1b":
                        i = self._consume_escape(text, i)
                    else:
                        self._put(text[i])
                        i += 1
            except Exception:
                # PTY 偶发坏数据不能让处理线程崩溃; 已解析的屏幕状态保留。
                return

    def _consume_escape(self, text: str, i: int) -> int:
        n = len(text)
        if i + 1 >= n:
            return n
        kind = text[i + 1]
        if kind in "]P^_":
            # OSC/DCS/APC/PM: 跳到 BEL 或 ST(ESC \)
            i += 2
            while i < n and text[i] not in "\x07\x1b":
                i += 1
            if i < n and text[i] == "\x1b" and i + 1 < n and text[i + 1] == "\\":
                return i + 2
            return min(i + 1, n)
        match = _CSI_RE.match(text, i)
        if match:
            self._handle_csi(match.group(1), match.group(2))
            return match.end()
        # ESC ( B 等字符集切换序列含一个附加字符。
        return min(i + (3 if kind in "()*+,-./" else 2), n)

    def _scroll_up(self, n: int):
        for _ in range(min(n, self._rows)):
            self._grid.pop(0)
            self._grid.append([" "] * self._cols)

    def _scroll_down(self, n: int):
        for _ in range(min(n, self._rows)):
            self._grid.pop()
            self._grid.insert(0, [" "] * self._cols)

    def _handle_csi(self, params: str, command: str):
        # DEC 私有模式、颜色和未知命令不改变布局, 忽略
        if any(ch in params for ch in "?><!:="):
            return
        values = (
            [int(value) if value else 0 for value in params.split(";")]
            if params
            else []
        )

        def at(index: int, default: int) -> int:
            value = values[index] if index < len(values) else 0
            return value or default

        if command in ("H", "f"):
            self._row = min(max(at(0, 1) - 1, 0), self._rows - 1)
            self._col = min(max(at(1, 1) - 1, 0), self._cols - 1)
        elif command == "A":
            self._row = max(self._row - at(0, 1), 0)
        elif command == "B":
            self._row = min(self._row + at(0, 1), self._rows - 1)
        elif command == "C":
            self._col = min(self._col + at(0, 1), self._cols - 1)
        elif command == "D":
            self._col = max(self._col - at(0, 1), 0)
        elif command == "G":
            self._col = min(max(at(0, 1) - 1, 0), self._cols - 1)
        elif command == "K":
            mode = at(0, 0)
            if mode == 0:
                for c in range(self._col, self._cols):
                    self._grid[self._row][c] = " "
            elif mode == 1:
                for c in range(self._col + 1):
                    self._grid[self._row][c] = " "
            elif mode == 2:
                self._grid[self._row] = [" "] * self._cols
        elif command == "J":
            mode = at(0, 0)
            if mode == 2:
                self._grid = [[" "] * self._cols for _ in range(self._rows)]
            elif mode == 0:
                for c in range(self._col, self._cols):
                    self._grid[self._row][c] = " "
                for r in range(self._row + 1, self._rows):
                    self._grid[r] = [" "] * self._cols
            elif mode == 1:
                for r in range(self._row):
                    self._grid[r] = [" "] * self._cols
                for c in range(self._col + 1):
                    self._grid[self._row][c] = " "
        elif command == "S":
            self._scroll_up(at(0, 1))
        elif command == "T":
            self._scroll_down(at(0, 1))

    def _linefeed(self):
        if self._row + 1 >= self._rows:
            self._scroll_up(1)
            self._row = self._rows - 1
        else:
            self._row += 1

    def _put(self, ch: str):
        if ch == "\n":
            self._linefeed()
        elif ch == "\r":
            self._col = 0
        elif ch == "\x08":
            # BS 只移光标不删字: 后续覆盖写自然生效(cmd 回显即此模式)
            self._col = max(self._col - 1, 0)
        elif ch == "\x7f":
            # DEL 删除前一字符(与 VtLineParser 语义一致)
            if self._col:
                self._col -= 1
                self._grid[self._row][self._col] = " "
        elif ch == "\t":
            # 制表符移动光标但不清格: 中间已有内容不能被吞掉
            self._col = min((self._col // 8 + 1) * 8, self._cols - 1)
        elif ord(ch) < 32:
            pass
        elif unicodedata.combining(ch):
            index = self._col - 1
            if index < 0:
                return
            if self._grid[self._row][index] == _WIDE_TAIL and index:
                index -= 1
            self._grid[self._row][index] += ch
        else:
            self._put_printable(
                ch, 2 if unicodedata.east_asian_width(ch) in "WF" else 1
            )

    def _put_printable(self, ch: str, width: int):
        if width == 2 and self._col >= self._cols - 1:
            # 行尾只剩一列放不下宽字符: 折行后再写
            self._col = 0
            self._linefeed()
        self._grid[self._row][self._col] = ch
        if width == 2 and self._col + 1 < self._cols:
            # 宽字符的后半格保留占位标记, 渲染时跳过
            self._grid[self._row][self._col + 1] = _WIDE_TAIL
        self._col += width
        if self._col >= self._cols:
            # 自动换行: 光标折到下一行行首(与 viewer 的虚拟屏幕一致)
            self._col = 0
            self._linefeed()


class PtySession:
    """单个 ConPTY 会话: spawn 进程 + 读线程 + 双路输出分发"""

    # ConPTY 缓冲区尺寸: viewer 窗口必须与此严格一致(启动时调整),
    # 否则绝对定位/换行/滚动错位(提示符插入输出中间、回显跑到底部等)
    PTY_COLS = 120
    PTY_ROWS = 30

    def __init__(
        self,
        command: str,
        monitor: Monitor,
        loop: asyncio.AbstractEventLoop,
        on_notify,
    ):
        from winpty import PtyProcess

        self.monitor = monitor
        self.loop = loop
        self.on_notify = on_notify  # async (monitor, line) -> None
        self.proc: PtyProcess = PtyProcess.spawn(
            command,
            cwd=str(monitor.cwd) if monitor.cwd else None,
            dimensions=(
                self.PTY_ROWS,
                self.PTY_COLS,
            ),  # pywinpty 参数顺序是 (rows, cols)!
        )
        self.raw_log_path: Path | None = None
        # raw log 大小上限: 超过后截断为保留尾部(见 _write_raw_log),
        # 防止长时间会话无限写盘
        self.raw_log_max = 20 * 1024 * 1024  # 20MB
        self.raw_log_keep = 5 * 1024 * 1024  # 保留 5MB 尾部
        self._stop = threading.Event()
        # 进程退出标志: 读线程读到 EOF(ConPTY 关闭)时置位。
        # manager 据此判断进程死亡, 避免高频轮询 proc.isalive()
        # (isalive 与 ConPTY teardown 竞争会概率性死锁 cmd 的 ExitProcess)
        self.exited = threading.Event()
        self._raw_queue: queue.Queue[str | None] = queue.Queue()
        # 读线程: 只负责阻塞 read + 写 raw log + 数据入队(不 sleep)
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        # 处理线程: 从队列取数据, 用 VT 状态机还原最终屏幕行
        self._proc_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._last_idle_snapshot: str | None = None
        self._parser = VtLineParser(self._commit_line)
        # 全屏网格: 与单行解析器并行消费同一份流, 供 monitor_screen 抓画面
        self._screen = ScreenBuffer(self.PTY_ROWS, self.PTY_COLS)

    def start(self):
        self._thread.start()
        self._proc_thread.start()

    def write(self, data: bytes):
        """写入原始字节到 PTY(键盘输入/VT 序列透传)

        pywinpty 的 write 接受 str 并按 UTF-8 编码写入 ConPTY 输入管道,
        因此这里必须按 UTF-8 解码;用 latin-1 会导致中文双重编码变乱码
        (如 "中" -> \xe4\xb8\xad -> \xc3\xa4\xc2\xb8\xc2\xad)。
        VT 序列字节均为 ASCII,UTF-8 解码无损。
        """
        try:
            self.proc.write(data.decode("utf-8", errors="replace"))
        except Exception:
            pass

    def screen_text(self) -> str:
        """返回控制台当前可见画面的纯文本快照(monitor_screen 工具使用)"""
        return self._screen.text()

    def resize_screen(self, rows: int, cols: int):
        """viewer 窗口尺寸变化后同步屏幕网格(manager 在 setwinsize 时调用)"""
        self._screen.resize(rows, cols)

    def terminate(self):
        self._stop.set()
        try:
            self.proc.terminate()
        except Exception:
            pass

    def _write_raw_log(self, raw_bytes: bytes):
        """追加原始 VT 流到 raw log, 超上限时截断保留尾部。

        viewer 每 0.15s 轮询读该文件, 检测到 size < pos 会重置从头读,
        因此截断不会导致 viewer 卡死, 只是丢失早期内容(可接受)。
        """
        path = self.raw_log_path
        with open(path, "rb+") as f:
            f.seek(0, 2)  # 文件末尾
            size = f.tell()
            if size + len(raw_bytes) > self.raw_log_max:
                # 保留尾部 + 新数据, 截断文件
                keep_start = max(0, size - self.raw_log_keep)
                f.seek(keep_start)
                tail = f.read()
                f.seek(0)
                f.truncate()
                f.write(tail)
                f.write(raw_bytes)
            else:
                f.write(raw_bytes)

    # ---------- 内部 ----------

    def _read_loop(self):
        """PTY 读线程: 阻塞读 → 原始字节写 log + 数据入队。

        读线程只负责持续读取(不 sleep, 不处理文本), 保证 winpty
        逐字符回显的数据不被丢弃; 文本合并/退格/分发由 _process_loop 负责。
        """
        while not self._stop.is_set():
            try:
                data = self.proc.read(4096)
            except Exception:
                break
            if not data:
                if not self.proc.isalive():
                    break
                continue

            raw_bytes = data.encode("utf-8", errors="replace")

            # 原始 VT 流镜像给查看器窗口(带大小上限, 防止无限写盘)
            if self.raw_log_path is not None:
                try:
                    self._write_raw_log(raw_bytes)
                except OSError:
                    pass

            self._raw_queue.put(data)

        # EOF/异常: 放哨兵通知处理线程结束
        try:
            self._raw_queue.put(None)
        except Exception:
            pass

    def _dispatch(self, data: str):
        """同一段 VT 流喂给两路解析器(单行提交 + 屏幕网格)"""
        self._parser.feed(data)
        self._screen.feed(data)

    def _process_loop(self):
        """处理线程: 以 VT 状态机维护单行屏幕并在真实换行时提交。"""
        monitor = self.monitor
        idle_timeout = 2.0
        while True:
            try:
                data = self._raw_queue.get(timeout=idle_timeout)
            except queue.Empty:
                # 对无换行长输出保留进度播报；状态机不清空,后续重绘仍能覆盖。
                snapshot = self._parser.snapshot()
                if (
                    snapshot
                    and snapshot != self._last_idle_snapshot
                    and not self._looks_like_prompt(snapshot)
                ):
                    self._emit(snapshot)
                    self._last_idle_snapshot = snapshot
                continue
            if data is None:
                break
            self._dispatch(data)

        # 退出前刷出残留
        tail = self._parser.snapshot()
        if tail and tail != self._last_idle_snapshot:
            self._emit(tail)
        # 宣告退出: 无论正常 EOF 还是异常, 都让 manager 停止等待
        self.exited.set()
        # 包含 MATCHED: 匹配后若进程随即结束, 不得卡在 matched(与 manager 一致)
        if monitor.status in (MonitorStatus.RUNNING, MonitorStatus.MATCHED):
            monitor.status = MonitorStatus.STOPPED
            self._touch_exit()

    @staticmethod
    def _looks_like_prompt(line: str) -> bool:
        """启发式判断是否为 shell 提示符。

        cmd 提示符(D:\\...>)、bash 提示符(user@host:path$)、
        PowerShell(PS C:\\>)、Python REPL(>>>)等均以 >/$/# 结尾。
        此类内容不主动刷出, 等待用户输入合并为同行。
        """
        s = line.strip()
        return bool(s) and len(s) <= 80 and s.endswith((">", "$", "#"))

    def _commit_line(self, line: str):
        """提交状态机定稿行；避免与同内容的空闲快照重复。"""
        if line != self._last_idle_snapshot:
            self._emit(line)
        self._last_idle_snapshot = None

    def _emit(self, line: str):
        """分发一行已由 VT 状态机还原的最终文本到 AI 侧。"""
        monitor = self.monitor
        part = line.rstrip()
        # 连续空行折叠；前导缩进保留,供 ipconfig 等表格式输出使用。
        if not part and monitor.output_lines and monitor.output_lines[-1] == "":
            return
        # 输出缓冲区达到上限(1000行)时插入一次性警告。
        if (
            len(monitor.output_lines) == monitor.output_lines.maxlen
            and not monitor.buffer_full_warned
        ):
            monitor.buffer_full_warned = True
            monitor.output_lines.append("⚠️ 输出缓冲区已满(1000行), 最旧的输出将被丢弃")
        monitor.output_lines.append(part)
        if not part:
            return
        if monitor.pattern and re.search(monitor.pattern, part):
            monitor.matched_lines.append(part)
            monitor.match_time = datetime.now()
            monitor.status = MonitorStatus.MATCHED
            try:
                self.loop.call_soon_threadsafe(
                    asyncio.create_task, self.on_notify(monitor, part)
                )
            except Exception:
                pass
        elif monitor.status == MonitorStatus.MATCHED:
            monitor.status = MonitorStatus.RUNNING

    def _touch_exit(self):
        exit_path = getattr(self, "exit_path", None)
        if exit_path is not None:
            try:
                exit_path.touch()
            except OSError:
                pass
