"""可视模式查看器——在弹出的控制台窗口中实时显示进程输出并转发用户键入。

可视模式下 MonitorManager 会弹出一个真实终端窗口运行本脚本(独立 Python 进程):

- 输出: manager 把主进程的每一行输出镜像写入 <id>.log, 本脚本 tail 该文件并打印
- 输入: 用户在窗口中的键入追加到 <id>.in, manager 轮询该文件并写入主进程 stdin
- 退出: <id>.exit 文件出现时本脚本打印结束语并退出(窗口随之关闭)

由此用户和 AI 操作的是同一个进程的 I/O, 实现人机配合。
"""

import sys
import tempfile
from pathlib import Path

# 查看器脚本源码, 由 ensure_viewer_script 写入临时目录后以独立进程运行。
# 通过文件而非 -c 传参, 避免多行代码在 Windows 命令行上的引号/换行转义问题。
VIEWER_SOURCE = """\
import pathlib
import sys
import threading
import time
import codecs
from collections import deque
import unicodedata

log_path, input_path, exit_path = (pathlib.Path(p) for p in sys.argv[1:4])
label = sys.argv[4] if len(sys.argv) > 4 else ""
arg5 = sys.argv[5] if len(sys.argv) > 5 else ""
no_echo = arg5 == "no-echo"
raw_term = arg5 == "raw-term"
# ConPTY 尺寸(如"120x30"): viewer 窗口必须与之严格一致, 否则
# 绝对定位/换行/滚动错位(提示符插入输出中间、回显跑到底部)
pty_size = sys.argv[6] if len(sys.argv) > 6 else ""

# no-echo: 关闭控制台本地回显(ENABLE_ECHO_INPUT)。
# shell 类被监控程序会自行回显输入, 保留本地回显会看到两份
# raw-term(ConPTY)模式同理, 回显由伪控制台内的程序完成。
if no_echo and sys.platform == "win32":
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        h_stdin = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h_stdin, ctypes.byref(mode)):
            k32.SetConsoleMode(h_stdin, mode.value & ~0x0004)
    except Exception:
        pass

if raw_term:
    # ===== ConPTY 原始终端模式(纯透传) =====
    # 输出: tail 镜像日志的原始字节(含 VT 序列)直接写 stdout,
    #   由 conhost 原生 VT 渲染(已实测 SetConsoleMode(VT) 在 conhost 下可用)。
    #   之前"剥离 VT 转纯文本"的方案从原理上不可行: ConPTY 的输出模型是
    #   "光标定位+重绘", 剥掉定位序列后重绘文本会错位覆盖(退格恢复/乱码)。
    # 键盘: msvcrt.getch() 逐键读取; 扩展键(\\xe0 前缀)转换为 VT 序列转发。

    if sys.platform == "win32":
        try:
            import ctypes

            k32 = ctypes.windll.kernel32
            # 使用 ASCII 固定前缀,避免查看器源码的历史编码问题污染窗口标题。
            k32.SetConsoleTitleW(f"UniClaw Terminal - {label}")
            h_out = k32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k32.GetConsoleMode(h_out, ctypes.byref(mode)):
                k32.SetConsoleMode(h_out, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            h_stdin = k32.GetStdHandle(-10)
            if k32.GetConsoleMode(h_stdin, ctypes.byref(mode)):
                k32.SetConsoleMode(h_stdin, mode.value & ~0x0004)  # 禁用 ECHO
        except Exception:
            pass

    # ConPTY 尺寸同步: 缓冲必须等于窗口(无滚动缓冲), 否则 ConPTY 的
    # 单调行号 + 终端不滚动 = 提示符/回显错位插入输出中间。
    # 两阶段:
    #   1) 启动同步: 强制窗口/缓冲 == ConPTY 尺寸(argv 传入)
    #   2) 跟随模式: 用户拖动窗口后, 缓冲对齐新窗口尺寸, 并把新尺寸
    #      写入 .size 文件, 由 manager 转发给 ConPTY(setwinsize)
    _cols = _rows = 0
    if "x" in pty_size:
        try:
            _cols_s, _rows_s = pty_size.split("x", 1)
            _cols, _rows = int(_cols_s), int(_rows_s)
        except ValueError:
            _cols = _rows = 0

    _SCROLLBACK = 1000

    def _clear_console_buffer():
        # 非 Windows 平台无原生控制台缓冲可清。
        return None

    if sys.platform == "win32" and _cols and _rows:
        import ctypes as _ct

        class _COORD(_ct.Structure):
            _fields_ = [("X", _ct.c_short), ("Y", _ct.c_short)]

        class _SMALL_RECT(_ct.Structure):
            _fields_ = [
                ("L", _ct.c_short),
                ("T", _ct.c_short),
                ("R", _ct.c_short),
                ("B", _ct.c_short),
            ]

        class _CSBI(_ct.Structure):
            _fields_ = [
                ("dwSize", _COORD),
                ("dwCursorPosition", _COORD),
                ("wAttributes", _ct.c_ushort),
                ("srWindow", _SMALL_RECT),
                ("dwMaximumWindowSize", _COORD),
            ]

        _h_out = _ct.windll.kernel32.GetStdHandle(-11)
        _k32 = _ct.windll.kernel32
        # ctypes 未声明 argtypes 时会把 Python str 当作指针传给 W 版本 API,
        # 导致缓冲区被写入指针低位字符(例如“ઐ”)而不是空格。
        _k32.FillConsoleOutputCharacterW.argtypes = [
            _ct.c_void_p,
            _ct.c_wchar,
            _ct.c_ulong,
            _COORD,
            _ct.POINTER(_ct.c_ulong),
        ]
        _k32.FillConsoleOutputCharacterW.restype = _ct.c_int
        _k32.FillConsoleOutputAttribute.argtypes = [
            _ct.c_void_p,
            _ct.c_ushort,
            _ct.c_ulong,
            _COORD,
            _ct.POINTER(_ct.c_ulong),
        ]
        _k32.FillConsoleOutputAttribute.restype = _ct.c_int
        _size_file = input_path.with_suffix(".size")
        _synced = False
        _reported = (0, 0)
        # 仅保留当前 monitor 的滚动历史；启动新会话和 cls 会清空它。

        def _clear_console_buffer():
            # 物理清空整个 conhost 缓冲,包括不可见的滚动历史。
            info = _CSBI()
            if not _k32.GetConsoleScreenBufferInfo(_h_out, _ct.byref(info)):
                return
            width, height = info.dwSize.X, info.dwSize.Y
            if width <= 0 or height <= 0:
                return
            written = _ct.c_ulong()
            origin = _COORD(0, 0)
            _k32.FillConsoleOutputCharacterW(
                _h_out, _ct.c_wchar(" "), width * height, origin, _ct.byref(written)
            )
            _k32.FillConsoleOutputAttribute(
                _h_out, 7, width * height, origin, _ct.byref(written)
            )
            _k32.SetConsoleCursorPosition(_h_out, origin)

        def _resize_to(w, h, clear_history=False):
            # 保留本次会话最多 1000 行回滚；仅在新 monitor 启动时清空。
            _k32.SetConsoleWindowInfo(_h_out, True, _ct.byref(_SMALL_RECT(0, 0, 0, 0)))
            _k32.SetConsoleScreenBufferSize(_h_out, _COORD(w, max(h, _SCROLLBACK)))
            _k32.SetConsoleWindowInfo(
                _h_out, True, _ct.byref(_SMALL_RECT(0, 0, w - 1, h - 1))
            )
            if clear_history:
                _clear_console_buffer()

        def _check_size():
            # 返回 True 表示初始同步完成
            global _synced, _reported
            try:
                info = _CSBI()
                if not _k32.GetConsoleScreenBufferInfo(_h_out, _ct.byref(info)):
                    return _synced
                win_w = info.srWindow.R - info.srWindow.L + 1
                win_h = info.srWindow.B - info.srWindow.T + 1
                if not _synced:
                    if (
                        info.dwSize.X == _cols
                        and info.dwSize.Y >= _rows
                        and win_w == _cols
                        and win_h == _rows
                    ):
                        _synced = True
                        _reported = (_cols, _rows)
                    else:
                        _resize_to(_cols, _rows, clear_history=True)
                    return _synced
                # 跟随模式: 用户拖动窗口 -> 重建虚拟屏幕(新尺寸) + 上报给 ConPTY。
                # 不主动改 buffer: SetConsoleScreenBufferSize 改变缓冲宽度会与
                # conhost/ConPTY 的自动 resize 冲突, 导致已输出内容重排错乱
                if (win_w, win_h) != _reported:
                    # ConPTY 已切换到新的坐标系,虚拟屏幕也必须同步重建。
                    global _view_h, _view_w
                    _view_h = win_h
                    _view_w = win_w
                    # 调整尺寸保留本次会话的滚动历史。
                    _resize_to(win_w, win_h)
                    _v_resize(win_h, win_w)
                    # shell 在收到 resize 后不一定主动重绘；立即重绘保留的
                    # 虚拟屏幕,避免窗口拖动后只能看到全黑背景。
                    _v_render()
                    try:
                        _size_file.write_text(f"{win_w}x{win_h}", encoding="utf-8")
                    except OSError:
                        pass
                    _reported = (win_w, win_h)
            except Exception:
                pass
            return _synced

        _check_size()
        # 即使 conhost 初始尺寸恰好匹配,也必须清掉可能复用的旧缓冲。
        _clear_console_buffer()

    def _reader_raw():
        # getch 扩展键码(\\xe0 或 \\x00 前缀) → VT 序列;
        # Backspace 键(getch \\x08)必须转发为 \\x7f(DEL):
        #   实测 ConPTY/cmd 约定 DEL=退格一字, \\x08 会清空整行
        #
        # 使用 getwch()(Unicode 版)代替 getch()(字节版):
        #   getch 按控制台输入代码页(中文 Windows 默认 936/GBK)返回字节,
        #   IME 中文输入/paste 中文会产生 GBK 字节, 转发到 ConPTY(期望 UTF-8)会乱码。
        #   getwch 返回 Unicode 字符, 不受代码页影响, encode UTF-8 即可。
        _VT_MAP = {
            # 方向/编辑键
            "H": "\\x1b[A",     # Up
            "P": "\\x1b[B",     # Down
            "K": "\\x1b[D",     # Left
            "M": "\\x1b[C",     # Right
            "G": "\\x1b[H",     # Home
            "O": "\\x1b[F",     # End
            "R": "\\x1b[2~",    # Insert
            "S": "\\x1b[3~",    # Delete
            "I": "\\x1b[5~",    # Page Up
            "Q": "\\x1b[6~",    # Page Down
            # F1-F10 (getwch 0x3B-0x44)
            ";": "\\x1bOP",
            "<": "\\x1bOQ",
            "=": "\\x1bOR",
            ">": "\\x1bOS",
            "?": "\\x1b[15~",
            "@": "\\x1b[17~",
            "A": "\\x1b[18~",
            "B": "\\x1b[19~",
            "C": "\\x1b[20~",
            "D": "\\x1b[21~",
            # F11/F12
            "\\x85": "\\x1b[23~",
            "\\x86": "\\x1b[24~",
        }
        if sys.platform == "win32":
            try:
                import msvcrt
            except ImportError:
                msvcrt = None
        else:
            msvcrt = None
        if msvcrt is not None:
            while True:
                try:
                    ch = msvcrt.getwch()
                except OSError:
                    return
                if not ch:
                    return
                if ch == "\\x08":
                    # Backspace 键 -> DEL(ConPTY/cmd 的退格约定)
                    data = "\\x7f"
                elif ch in ("\\xe0", "\\x00"):
                    try:
                        ch2 = msvcrt.getwch()
                    except OSError:
                        return
                    data = _VT_MAP.get(ch2, "")
                else:
                    data = ch
                if not data:
                    continue
                try:
                    with input_path.open("ab") as f:
                        f.write(data.encode("utf-8"))
                except OSError:
                    return
        else:
            while True:
                try:
                    b = sys.stdin.buffer.read(1)
                except OSError:
                    return
                if not b:
                    return
                try:
                    with input_path.open("ab") as f:
                        f.write(b)
                except OSError:
                    return

    threading.Thread(target=_reader_raw, daemon=True).start()

    # OSC 标题序列拦截: cmd 等程序会改写窗口标题(如"C:\\...cmd.EXE"),
    # 直接透传会覆盖"UniClaw 终端 - {label}"导致用户无法辨认窗口。
    # 方案: 拦截 OSC 0/1/2, 本地设置 "UniClaw 终端 - {label} | {原标题}",
    # 既保留身份标识又显示程序动态标题, 序列本身不透传。
    import re as _re2

    _title_prefix = f"UniClaw Terminal - {label}"
    _OSC_TITLE_RE = _re2.compile(rb"\\x1b\\][012];([^\\x07\\x1b]*)(?:\\x07|\\x1b\\\\)")
    _OSC_START = rb"\\x1b\\]"

    def _title_sub(m):
        try:
            import ctypes

            title = m.group(1).decode("utf-8", errors="replace")
            ctypes.windll.kernel32.SetConsoleTitleW(_title_prefix + " | " + title)
        except Exception:
            pass
        return b""

    # ---- 虚拟屏幕(修正 ConPTY 透传渲染漂移) ----
    # conhost 对 ConPTY 增量 VT 流的翻译在滚动后光标漂移错乱,
    # 故 viewer 自行解析 VT 流维护屏幕状态, 每次更新全量重绘。
    # 任何解析/渲染异常写入 viewer_error.log, 便于定位(不静默崩溃)。
    import traceback as _tb
    _ERR_LOG = log_path.parent / "viewer_error.log"
    _VROWS = _rows or 30
    _VCOLS = _cols or 120
    # 单元格: (字符, 前景色码, 背景色码); 色码为 ANSI 参数值(如"32"), 空为默认
    _screen = [[(" ", "", "") for _ in range(_VCOLS)] for _ in range(_VROWS)]
    # 查看器每次都按“最终屏幕状态”全量重绘,不能依赖 conhost 自己
    # 留下的历史。把滚出虚拟屏幕的行显式保存在这里,滚轮才会稳定地
    # 看到当前 monitor_start 的内容,而不会看到上一个会话的残留。
    _history = deque(maxlen=970)
    _cur_r = _cur_c = 0
    _cur_fg = _cur_bg = ""
    _view_h = _rows or 30
    _view_w = _cols or 120
    # CSI 的终止字符可为 @-~,而不只是字母;WSL 会发送 DEC 私有
    # 模式切换和短 ESC 序列,必须完整吞掉,不能把残余字符画到屏幕上。
    _CSI_RE_V = _re2.compile(r"\\x1b\\[([0-9:;<=>?]*)([@-~])")

    def _v_log(exc):
        try:
            with open(_ERR_LOG, "a", encoding="utf-8") as f:
                f.write(_tb.format_exc() + "\\n")
        except Exception:
            pass

    def _v_scroll_up(n):
        global _screen
        for _ in range(min(n, _VROWS)):
            _history.append(_screen.pop(0))
            _screen.append([(" ", "", "") for _ in range(_VCOLS)])

    def _v_scroll_down(n):
        global _screen
        for _ in range(min(n, _VROWS)):
            _screen.pop()
            _screen.insert(0, [(" ", "", "") for _ in range(_VCOLS)])

    def _v_put(ch):
        global _cur_r, _cur_c
        if ch == "\\n":
            _cur_r += 1
            if _cur_r >= _VROWS:
                _v_scroll_up(_cur_r - _VROWS + 1)
                _cur_r = _VROWS - 1
        elif ch == "\\r":
            _cur_c = 0
        elif ch == "\\x08":
            if _cur_c > 0:
                _cur_c -= 1
        elif ch == "\\x07":
            pass
        else:
            # 终端按显示列定位：中文、全角符号占两列,组合字符不占列。
            # 按 Python 字符数前进会使 CSI 定位和光标逐字符漂移。
            width = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in "WF" else 1)
            if width == 0:
                if _cur_c > 0 and 0 <= _cur_r < _VROWS:
                    old, fg, bg = _screen[_cur_r][_cur_c - 1]
                    _screen[_cur_r][_cur_c - 1] = (old + ch, fg, bg)
                return
            if width == 2 and _cur_c == _VCOLS - 1:
                _cur_c = 0
                _cur_r += 1
                if _cur_r >= _VROWS:
                    _v_scroll_up(1)
                    _cur_r = _VROWS - 1
            if 0 <= _cur_r < _VROWS and 0 <= _cur_c < _VCOLS:
                _screen[_cur_r][_cur_c] = (ch, _cur_fg, _cur_bg)
                if width == 2 and _cur_c + 1 < _VCOLS:
                    # 宽字符的后半格不写出,但保留其列坐标。
                    _screen[_cur_r][_cur_c + 1] = ("", _cur_fg, _cur_bg)
            _cur_c += width
            if _cur_c >= _VCOLS:
                _cur_c = 0
                _cur_r += 1
                if _cur_r >= _VROWS:
                    _v_scroll_up(_cur_r - _VROWS + 1)
                    _cur_r = _VROWS - 1

    def _v_handle_csi(params, cmd):
        global _cur_r, _cur_c
        # DEC 私有模式/含非数字参数(如 ?1004h、?7l、[0c)不影响布局, 忽略
        if params and any(c in "?><!:=" for c in params):
            return
        p = [int(x) for x in params.split(";") if x] if params else []
        def _g(i, d):
            return p[i] if i < len(p) and p[i] else d
        if cmd in "Hf":
            _cur_r = min(_g(0, 1) - 1, _VROWS - 1)
            _cur_c = min(_g(1, 1) - 1, _VCOLS - 1)
        elif cmd == "A":
            _cur_r = max(_cur_r - _g(0, 1), 0)
        elif cmd == "B":
            _cur_r = min(_cur_r + _g(0, 1), _VROWS - 1)
        elif cmd == "C":
            _cur_c = min(_cur_c + _g(0, 1), _VCOLS - 1)
        elif cmd == "D":
            _cur_c = max(_cur_c - _g(0, 1), 0)
        elif cmd == "G":
            _cur_c = min(_g(0, 1) - 1, _VCOLS - 1)
        elif cmd == "J":
            if _g(0, 0) == 2:
                for r in range(_VROWS):
                    for c in range(_VCOLS):
                        _screen[r][c] = (" ", "", "")
                # cls 的语义应同时丢弃本次 monitor 此前的滚动历史。
                _history.clear()
                _clear_console_buffer()
            elif _g(0, 0) == 0:
                for c in range(_cur_c, _VCOLS):
                    _screen[_cur_r][c] = (" ", "", "")
                for r in range(_cur_r + 1, _VROWS):
                    for c in range(_VCOLS):
                        _screen[r][c] = (" ", "", "")
            elif _g(0, 0) == 1:
                for c in range(0, _cur_c + 1):
                    _screen[_cur_r][c] = (" ", "", "")
                for r in range(0, _cur_r):
                    for c in range(_VCOLS):
                        _screen[r][c] = (" ", "", "")
        elif cmd == "K":
            if _g(0, 0) == 0:
                for c in range(_cur_c, _VCOLS):
                    _screen[_cur_r][c] = (" ", "", "")
            elif _g(0, 0) == 2:
                for c in range(_VCOLS):
                    _screen[_cur_r][c] = (" ", "", "")
            elif _g(0, 0) == 1:
                for c in range(0, _cur_c + 1):
                    _screen[_cur_r][c] = (" ", "", "")
        elif cmd == "S":
            _v_scroll_up(_g(0, 1))
        elif cmd == "T":
            _v_scroll_down(_g(0, 1))
        elif cmd == "m":
            _v_handle_sgr(p)


    def _v_handle_sgr(params):
        # 解析 SGR 颜色参数(30-37/90-97 前景, 40-47/100-107 背景, 0 重置)
        global _cur_fg, _cur_bg
        if not params:
            params = [0]
        fg, bg = _cur_fg, _cur_bg
        for v in params:
            if v == 0:
                fg = bg = ""
            elif 30 <= v <= 37:
                fg = str(v)
            elif 40 <= v <= 47:
                bg = str(v)
            elif 90 <= v <= 97:
                fg = str(v)
            elif 100 <= v <= 107:
                bg = str(v)
            elif v == 39:
                fg = ""
            elif v == 49:
                bg = ""
        _cur_fg, _cur_bg = fg, bg

    def _v_parse(text):
        i = 0
        n = len(text)
        while i < n:
            if text[i] == "\\x1b":
                # OSC 序列(ESC]...ST): 跳过到 \\x1b\\ 或 \\x07,
                # 避免字节层面 OSC 拦截扣留 ESC 失败后残留写入屏幕
                if i + 1 < n and text[i + 1] == "]":
                    i += 2
                    while i < n and text[i] not in "\\x07\\x1b":
                        i += 1
                    if i < n and text[i] == "\\x1b":
                        i += 1  # 跳过 ESC
                        if i < n and text[i] == "\\\\":
                            i += 1
                    elif i < n and text[i] == "\\x07":
                        i += 1
                    continue
                # DCS / APC / PM 与 OSC 一样以 BEL 或 ST(ESC \\) 结束。
                # WSL 退出时会发送 ST；未处理时会残留一个反斜杠,
                # 随后所有输入都会在错误的屏幕状态上绘制。
                if i + 1 < n and text[i + 1] in "P^_":
                    i += 2
                    while i < n and text[i] not in "\\x07\\x1b":
                        i += 1
                    if i < n and text[i] == "\\x1b":
                        i += 1
                        if i < n and text[i] == "\\\\":
                            i += 1
                    elif i < n:
                        i += 1
                    continue
                m = _CSI_RE_V.match(text, i)
                if m:
                    _v_handle_csi(m.group(1), m.group(2))
                    i = m.end()
                    continue
                # ST(ESC \\) 及其他两字符 ESC 控制序列不是可显示文本。
                # 字符集选择 ESC ( B 这类序列还包含一个附加字符。
                if i + 1 < n:
                    if text[i + 1] in "()*+,-./":
                        i += 3
                    else:
                        i += 2
                else:
                    i += 1
                continue
            _v_put(text[i])
            i += 1

    def _v_sequence_incomplete(tail):
        # 判断日志块尾部的 ESC 是否必须留到下一个块。完整的 ESC\、
        # ESC> 等短序列不能被误留,否则会连同后续普通文本永久不渲染。
        if tail == "\\x1b":
            return True
        if tail.startswith("\\x1b["):
            return _CSI_RE_V.match(tail) is None
        if tail.startswith(("\\x1b]", "\\x1bP", "\\x1b^", "\\x1b_")):
            return "\\x07" not in tail and "\\x1b\\\\" not in tail
        return False

    def _v_render():
        # 物理缓冲每次从“本会话历史 + 当前虚拟屏幕”重建。这样全量重绘
        # 不会吞掉滚轮历史,也不会泄漏上一 monitor 的历史。
        view_h = max(1, _view_h)
        start_r = max(0, _VROWS - view_h)
        rows = list(_history) + _screen[start_r:]
        if len(rows) > _SCROLLBACK:
            rows = rows[-_SCROLLBACK:]
        history_rows = len(rows) - (_VROWS - start_r)
        # 禁用自动换行后再逐行重绘。若一行恰好填满窗口宽度,
        # conhost 的隐式换行再叠加我们写入的 CRLF 会产生额外空行,
        # 进而导致滚动、内容混叠和光标漂移。
        _clear_console_buffer()
        out = [b"\\x1b[?7l\\x1b[0m\\x1b[H"]
        _cr, _cc = _cur_r, _cur_c
        for index, cells in enumerate(rows):
            row = []
            last_fg = last_bg = None
            for c in range(min(_VCOLS, _view_w)):
                ch, fg, bg = cells[c] if c < len(cells) else (" ", "", "")
                if fg != last_fg or bg != last_bg:
                    esc = b"\\x1b[0m"
                    if fg:
                        esc += ("\\x1b[" + fg + "m").encode("utf-8")
                    if bg:
                        esc += ("\\x1b[" + bg + "m").encode("utf-8")
                    row.append(esc)
                    last_fg, last_bg = fg, bg
                if ch:
                    row.append(ch.encode("utf-8"))
            if index < len(rows) - 1:
                row.append(b"\\x1b[K\\r\\n")
            else:
                row.append(b"\\x1b[K")
            out.append(b"".join(row))
        # 光标定位: 相对视口(渲染的最后 view_h 行内)
        rel_r = _cr - start_r
        rel_r = max(0, min(rel_r, view_h - 1))
        _up = len(rows) - 1 - (history_rows + rel_r)
        if _up > 0:
            out.append(f"\\x1b[{_up}A".encode("utf-8"))
        _cc2 = min(_cc, max(_view_w - 1, 0))
        out.append(f"\\x1b[{_cc2 + 1}G".encode("utf-8"))
        out.append(b"\\x1b[?7h")
        sys.stdout.buffer.write(b"".join(out))
        sys.stdout.buffer.flush()
    def _v_resize(rows, cols):
        # 窗口尺寸变化: 保留交集区域。此前清空并等待被监控程序重绘,
        # 但普通 shell 通常不会在 resize 后重绘,窗口因而永久黑屏。
        global _screen, _VROWS, _VCOLS, _cur_r, _cur_c
        rows = max(int(rows), 1)
        cols = max(int(cols), 1)
        if rows == _VROWS and cols == _VCOLS:
            return
        old_screen, old_rows, old_cols = _screen, _VROWS, _VCOLS
        _VROWS, _VCOLS = rows, cols
        _screen = [[(" ", "", "") for _ in range(_VCOLS)] for _ in range(_VROWS)]
        for r in range(min(old_rows, _VROWS)):
            for c in range(min(old_cols, _VCOLS)):
                _screen[r][c] = old_screen[r][c]
        _cur_r = min(_cur_r, _VROWS - 1)
        _cur_c = min(_cur_c, _VCOLS - 1)

    def _v_reset():
        # 原始日志滚动截断后,旧屏幕状态不能再与新日志尾部混用；但已
        # 滚出的本会话历史仍可供用户回看,不能一并清掉。
        global _screen, _cur_r, _cur_c, _cur_fg, _cur_bg
        _screen = [[(" ", "", "") for _ in range(_VCOLS)] for _ in range(_VROWS)]
        _cur_r = _cur_c = 0
        _cur_fg = _cur_bg = ""

    # 清空 conhost 滚动缓冲和历史, 确保新窗口无旧数据残留\r\n    sys.stdout.write("\\x1b[3J\\x1b[2J\\x1b[H")\r\n    sys.stdout.flush()

    pos = 0
    _pend = b""
    _decoder = codecs.getincrementaldecoder("utf-8")("replace")
    _vt_pend = ""
    try:
        while True:
            if exit_path.exists():
                sys.stdout.buffer.write(
                    ("\\r\\n" + "-" * 60 + "[UniClaw Terminal] Process ended, window will close.").encode("utf-8")
                )
                sys.stdout.buffer.flush()
                time.sleep(1.5)
                break
            if _cols and _rows:
                # 每轮检查: 未同步时强制对齐; 同步后进入跟随模式
                # (用户拖动窗口 -> 缓冲对齐 + 上报新尺寸给 manager)
                _check_size()
            try:
                size = log_path.stat().st_size
            except OSError:
                size = 0
            if size < pos:
                pos = 0
                _decoder.reset()
                _vt_pend = ""
                _pend = b""
                _v_reset()
            if size > pos:
                try:
                    with log_path.open("rb") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                    if _pend:
                        chunk = _pend + chunk
                        _pend = b""
                    # 拦截 OSC 标题序列(本地改写标题, 不透传)
                    out = _OSC_TITLE_RE.sub(_title_sub, chunk)
                    # 扣留尾部未完成的 OSC 序列(可能跨 chunk 分割)
                    idx = out.rfind(_OSC_START)
                    if idx != -1:
                        tail = out[idx:]
                        if b"\\x07" not in tail and b"\\x1b\\\\" not in tail:
                            _pend = tail
                            out = out[:idx]
                    # 扣留尾部孤立 ESC(可能与下一 chunk 组成 OSC)
                    if out.endswith(b"\\x1b"):
                        _pend = out[-1:] + _pend
                        out = out[:-1]
                    if out:
                        try:
                            # 文件 tail 没有消息边界：保留跨块 UTF-8 和 ANSI
                            # 控制序列,避免显示乱码或把半截 CSI 当普通文本。
                            text = _vt_pend + _decoder.decode(out, final=False)
                            _vt_pend = ""
                            incomplete = text.rfind("\\x1b")
                            if incomplete >= 0:
                                tail = text[incomplete:]
                                if _v_sequence_incomplete(tail):
                                    _vt_pend = tail
                                    text = text[:incomplete]
                            if text:
                                _v_parse(text)
                            _v_render()
                        except Exception as _e:
                            _v_log(_e)
                except OSError:
                    pass
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    sys.exit(0)

print("[UniClaw Monitor]" + (f" {label}" if label else ""))
print("Output displayed below; type directly in this window.")
print("-" * 60)
sys.stdout.flush()

# 后台线程读取用户键入 -> 追加到输入文件(保留原始换行)
def _reader():
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        try:
            with input_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            return

threading.Thread(target=_reader, daemon=True).start()

# 主循环: tail 日志文件 + 检查退出标记
pos = 0
try:
    while True:
        if exit_path.exists():
            print("-" * 60)
            print("[UniClaw Monitor]" + (f" {label}" if label else ""))
            sys.stdout.flush()
            time.sleep(1.5)
            break
        try:
            size = log_path.stat().st_size
        except OSError:
            size = 0
        if size < pos:
            pos = 0  # 文件被重建
        if size > pos:
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                sys.stdout.write(chunk)
                sys.stdout.flush()
            except OSError:
                pass
        time.sleep(0.15)
except KeyboardInterrupt:
    pass
"""

# 临时目录: 存放查看器脚本和每个 monitor 的 log/in/exit 文件
VIEWER_DIR = Path(tempfile.gettempdir()) / "uniclaw-monitor"


def viewer_paths(monitor_id: str) -> tuple[Path, Path, Path]:
    """返回指定 monitor 的(日志, 输入, 退出标记)文件路径"""
    return (
        VIEWER_DIR / f"{monitor_id}.log",
        VIEWER_DIR / f"{monitor_id}.in",
        VIEWER_DIR / f"{monitor_id}.exit",
    )


def ensure_viewer_script() -> Path:
    """确保查看器脚本已写入临时目录, 返回脚本路径"""
    VIEWER_DIR.mkdir(parents=True, exist_ok=True)
    script_path = VIEWER_DIR / "_viewer.py"
    script_path.write_text(VIEWER_SOURCE, encoding="utf-8")
    return script_path


def build_viewer_argv(
    script_path: Path,
    log_path: Path,
    input_path: Path,
    exit_path: Path,
    label: str,
    no_echo: bool = False,
    raw_term: bool = False,
    size: tuple[int, int] | None = None,
) -> list[str]:
    """构建查看器的启动命令行(-X utf8 保证中文输出不乱码)"""
    argv = [
        sys.executable,
        "-X",
        "utf8",
        str(script_path),
        str(log_path),
        str(input_path),
        str(exit_path),
        label,
    ]
    if raw_term:
        argv.append("raw-term")
    elif no_echo:
        argv.append("no-echo")
    if size:
        argv.append(f"{size[0]}x{size[1]}")
    return argv
