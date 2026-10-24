"""批量文件修改工具 — 一次调用完成多个文件的 Add / Update / Delete / Move。

使用标准 patch 文本协议:

    *** Begin Patch
    *** Add File: src/new.py
    +line1
    +line2
    *** Update File: src/old.py
    @@
    -old
    +new
    @@ optional_label
     -more_ctx
    -removed
    +added
    *** Delete File: src/obsolete.py
    *** End Patch

规则:
- 标记行精确以 ``*** ``(三颗星+空格)开头;``@@`` 只是可选上下文标签,不携带行号
- ``*** Move to: newpath`` 紧跟在其所属 Update File 标记后,表示重命名
- ``*** End of File`` 表示该 hunk 延伸到文件末尾(可在尾部追加)
- 文件路径必须相对,禁止绝对路径与 ``..``;同一文件只能有一个 Update 块,多个改动点用多个 ``@@`` 合并
- hunk 按文件内自上而下顺序排列;上下文行以空格开头(建议 2~3 行)
- 匹配宽松:精确 → 忽略行尾空白 → 忽略首尾空白 逐级降级
- 契合 CRLF 文件(自动保持原换行);拒绝二进制文件与逃逸工作区的路径
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.tools.fs import (
    _binary_file_kind,
    _read_preserving_newlines,
    generate_unified_diff,
)
from uniclaw.utils.constants import TOOL_ERROR

_ADD_RE = re.compile(r"^\*\*\* Add File: (.+?)\s*$")
_UPDATE_RE = re.compile(r"^\*\*\* Update File: (.+?)\s*$")
_DELETE_RE = re.compile(r"^\*\*\* Delete File: (.+?)\s*$")
_MOVE_RE = re.compile(r"^\*\*\* Move to: (.+?)\s*$")

# Windows 盘符形式(C:foo)与 UNC 前缀 —— os.path.isabs 不识别前者,需兜底
_ABS_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# Windows 保留设备名(含扩展名形式如 con.txt,按 "." 前基名判定)
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

_BINARY_HEAD_BYTES = 8192


class InvalidContext(ValueError):
    """hunk 上下文未命中或乱序。携带定位信息供模型自纠。"""


class PathEscapeError(ValueError):
    """路径逃逸出允许工作区。"""


@dataclass
class Hunk:
    """patch 中一个更新块。行内容均已去前缀且保持末尾换行(LF 归一)。

    body 保留行的原始顺序(标准 diff 语义): context 可分布在删除行两侧,
    仅靠 context/del_lines/ins_lines 三个列表无法复原顺序。body 每项为
    (kind, content), kind ∈ {"ctx", "del", "ins"}。

    context / del_lines / ins_lines 属性从 body 派生,保持既有调用兼容。
    """

    body: list[tuple[str, str]] = field(default_factory=list)
    is_eof: bool = False  # *** End of File 之后 → 按文件尾部语义操作
    patch_line: int = 0  # hunk 在 patch 文本中的起始行号(报错定位)

    @property
    def context(self) -> list[str]:
        return [c for k, c in self.body if k == "ctx"]

    @property
    def del_lines(self) -> list[str]:
        return [c for k, c in self.body if k == "del"]

    @property
    def ins_lines(self) -> list[str]:
        return [c for k, c in self.body if k == "ins"]

    @property
    def has_changes(self) -> bool:
        return any(k != "ctx" for k, _ in self.body)


@dataclass
class PatchOp:
    """一次 patch 中的一个文件操作(Add / Update / Delete / Move)。"""

    kind: str  # "add" | "update" | "delete"
    rel: str  # 原始相对路径字符串(报告展示)
    src_abs: Path | None = None  # 已 resolve + 安全校验通过
    dst_rel: str | None = None  # Move 目标相对路径
    dst_abs: Path | None = None  # Move 目标绝对路径
    hunks: list[Hunk] = field(default_factory=list)
    patch_line: int = 0  # 标记行在 patch 文本中的行号


# ── Parser ────────────────────────────────────────────────────────────────


def _is_marker_line(text: str) -> bool:
    """判断一行(去行尾空白后)是否为合法的 patch 结构标记行。

    只有这 6 种才算标记行,块内循环据此定界。裸 `*** xxx` 内容行
    (漏写 + / - / 空格前缀)不是标记行,不应中断块解析 —— 否则 Add 块内
    漏前缀的 `*** Delete File:` 会被静默当成真实删除指令执行(Bug G/C)。

    注意:只去行尾空白、不去行首 —— 空格前缀的 context 行(如 ` *** End
    of File`)不能被误判成标记,否则会中断块解析(Bug D)。
    """
    t = text.rstrip("\r\n").rstrip()
    return t in ("*** Begin Patch", "*** End Patch", "*** End of File") or (
        t.startswith("*** ")
        and (
            t.startswith("*** Add File: ")
            or t.startswith("*** Update File: ")
            or t.startswith("*** Delete File: ")
            or t.startswith("*** Move to: ")
        )
    )


def _strip_eol(line: str) -> str:
    """去掉行尾 \\r\\n / \\n,保留行内其它空白。"""
    return line.rstrip("\r\n")


def _norm_line(content: str) -> str:
    """把行内容(已去前缀)换行归一到 LF(keepends 保留)。

    patch 可能用 CRLF 写,文件可能用 CRLF;匹配统一在 LF 域。
    """
    return content.replace("\r\n", "\n") if "\r\n" in content else content


def parse_patch(patch: str) -> tuple[list[PatchOp], str | None]:
    """把 patch 文本解析为操作列表。

    纯行级扫描,不查文件系统。同一文件只能有一个 Update 块。

    Args:
        patch: 完整 patch 文本。

    Returns:
        tuple: (ops, error)。ops 为解析出的操作列表(顺序与 patch 一致);
             error 非 None 表示解析失败,内容为带 patch 行号的报错文本。
    """
    if not patch or not patch.strip():
        return [], "patch 为空"
    lines = patch.splitlines(keepends=True)
    ops: list[PatchOp] = []
    seen_updates: set[str] = set()
    i = 0

    # 跳过首行前的空行
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return [], "patch 为空"
    if _strip_eol(lines[i]).strip() != "*** Begin Patch":
        return [], f"patch 必须以 '*** Begin Patch' 开头(第 {i + 1} 行)但不是: {_strip_eol(lines[i])!r}"

    def _line_no() -> int:
        return i + 1

    while i < len(lines):
        text = _strip_eol(lines[i])
        stripped = text.strip()

        if not stripped:
            i += 1
            continue

        if stripped == "*** Begin Patch":
            i += 1  # 已消费
            continue
        if stripped == "*** End Patch":
            # 检查 End Patch 之后是否还有非空内容: 有则报错(可能掩盖截断/拼写错误)
            tail = [l for l in lines[i + 1 :] if _strip_eol(l).strip()]
            if tail:
                return [], (
                    f"第 {i + 1} 行出现 '*** End Patch' 后,之后还有 {len(tail)} 行非空内容"
                    f"(首行: {_strip_eol(tail[0])!r}),疑似多余内容或结构错误,请去除"
                )
            return ops, None

        m = _ADD_RE.match(text)
        if m:
            rel = m.group(1).strip()
            ln = _line_no()
            i += 1
            ins: list[str] = []
            # Add 内容体: 所有行必须是 '+' 前缀;裸空行容错为空内容行。
            # 只有"合法标记行"才终止块(Add/Update/Delete/Move 都是合法的下一段,
            # 标准协议允许多操作连续书写 —— 先建后删/多文件批量等)。
            while i < len(lines):
                raw = lines[i]
                if _is_marker_line(raw):
                    break
                if not raw.strip():
                    ins.append("\n")
                    i += 1
                    continue
                if not raw.startswith("+"):
                    return [], f"第 {i + 1} 行: Add File 块中只能出现 '+ 前缀行,收到: {_strip_eol(raw)!r}"
                ins.append(_norm_line(raw[1:]))
                i += 1
            if not ins:
                return [], f"第 {ln} 行: Add File 块为空,至少需要一个 '+ 前缀行"
            body = [("ins", c) for c in ins]
            ops.append(PatchOp(kind="add", rel=rel, hunks=[Hunk(body=body, patch_line=ln)], patch_line=ln))
            continue

        m = _UPDATE_RE.match(text)
        if m:
            rel = m.group(1).strip()
            ln = _line_no()
            if rel in seen_updates:
                return [], (f"第 {ln} 行: 同一文件 {rel} 在同一 patch 中只能出现一次 Update,"
                             "请合并为单个 Update 块的多个 @@ hunk")
            seen_updates.add(rel)
            op = PatchOp(kind="update", rel=rel, patch_line=ln)
            # 可选 Move to(紧跟标记行)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            m2 = _MOVE_RE.match(_strip_eol(nxt))
            if m2:
                op.dst_rel = m2.group(1).strip()
                i += 1
            i += 1
            cur_hunk: Hunk | None = None
            eof_pending = False  # 遇到 *** End of File 后,其后 hunk 均为 EOF 语义

            def _start_hunk():
                nonlocal cur_hunk
                # 相邻 '@@' 产生的真空 hunk(无任何内容行)丢弃
                if cur_hunk is not None and not cur_hunk.context and not cur_hunk.has_changes:
                    op.hunks.pop()
                cur_hunk = Hunk(patch_line=_line_no())
                if eof_pending:
                    cur_hunk.is_eof = True
                op.hunks.append(cur_hunk)

            while i < len(lines):
                raw = lines[i]
                text_i = _strip_eol(raw)
                # EOF 标记是"裸行"(行首无前缀)。用 rstrip(只去尾空白)比较,
                # 不带 strip —— 后者会吞掉 context 行的前导空格,把内容恰为
                # "*** End of File" 的上下文行误判成标记(Bug D)。
                if text_i.rstrip() == "*** End of File":
                    eof_pending = True
                    if cur_hunk is not None:
                        cur_hunk.is_eof = True
                    i += 1
                    continue
                if _is_marker_line(raw):
                    break
                if not text_i:
                    if cur_hunk is not None:
                        cur_hunk.body.append(("ctx", "\n"))  # 空行是空格+空内容的 context 行
                    i += 1
                    continue
                if text_i.startswith("@@"):
                    _start_hunk()
                    i += 1
                    continue
                if cur_hunk is None:
                    return [], (f"第 {i + 1} 行: Update File 块内容必须在 '@@' 之后,"
                                 f"收到: {text_i!r}")
                if text_i.startswith("+"):
                    cur_hunk.body.append(("ins", _norm_line(raw[1:])))
                elif text_i.startswith("-"):
                    cur_hunk.body.append(("del", _norm_line(raw[1:])))
                elif text_i.startswith(" "):
                    cur_hunk.body.append(("ctx", _norm_line(raw[1:])))
                else:
                    return [], (f"第 {i + 1} 行: Update File 块中无法识别的行前缀"
                                 f"(需 '+', '-', ' ' 或 '@@'): {text_i!r}")
                i += 1
            # 丢弃真空 hunk(相邻 @@ 或空 @@ 产生的无内容块),全部为空则报错
            op.hunks = [h for h in op.hunks if h.context or h.has_changes]
            if not op.hunks:
                return [], f"第 {ln} 行: Update File 块为空,至少需要一个 '@@' 起始的 hunk"
            ops.append(op)
            continue

        m = _DELETE_RE.match(text)
        if m:
            ops.append(PatchOp(kind="delete", rel=m.group(1).strip(), patch_line=_line_no()))
            i += 1
            continue

        return [], (f"第 {i + 1} 行: 无法识别的标记行: {text!r}"
                    "(应为 '*** Add File:', '*** Update File:', '*** Delete File:' 或 '*** End Patch')")

    # patch 缺 *** End Patch —— 视为不完整(可能被截断),报错而非静默应用。
    # 截断常导致最后一行 ins 缺换行,直接应用会损坏文件(行粘连);报错让模型重试。
    return [], (
        f"patch 缺少 '*** End Patch' 结尾(第 {i + 1} 行后没有结束标记),"
        "疑似被截断。请补全 patch 并以 '*** End Patch' 结束后再调用"
    )


# ── 路径安全校验 ──────────────────────────────────────────────────────────


def _reject_abs_or_dotdot(rel: str) -> str | None:
    """字符串级路径拒绝,返回 None 或错误文本(不做文件 IO)。

    除绝对路径 / '..' 外,还拒绝 Windows 路径边界问题:
    - 尾部空格 / 尾部点(Windows 会静默归一化,报告与实际文件名不一致)
    - 保留设备名(CON / PRN / AUX / NUL / COM1-9 / LPT1-9,含扩展名形式)
      写入这些名字在 Windows 会触发设备访问而非文件操作。
    """
    if not rel:
        return "空路径不受支持"
    if "\x00" in rel:
        return "路径含 NUL 字节"
    if os.path.isabs(rel) or rel.startswith("/") or _ABS_DRIVE_RE.match(rel) or rel.startswith("\\\\"):
        return f"绝对路径不受支持: {rel}(只能使用相对路径)"
    if ".." in Path(rel).parts:
        return f"禁止 '..' 路径: {rel}"
    if rel.endswith(" ") or rel.endswith(".") or any(
        part.endswith(" ") or part.endswith(".") for part in Path(rel).parts
    ):
        return f"路径含尾部空格或点(Windows 会静默归一化,文件名不一致): {rel}"
    base = Path(rel).name
    stem = base.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        return f"路径 {rel} 使用了 Windows 保留设备名({stem}),不受支持"
    return None


def _resolve_checked(rel: str, root_dir: Path, allowed_roots: list[Path]) -> Path:
    """把相对路径解析为绝对路径并做安全校验(symlink 逐级检查)。

    Args:
        rel: 相对路径字符串。
        root_dir: 会话工作目录(root)。相对路径以此为基础解析。
        allowed_roots: 额外允许根(如 workspace / writable_dirs / plans 目录)。

    Returns:
        Path: resolve 后的绝对路径。

    Raises:
        PathEscapeError: 路径逃逸出允许工作区(绝对路径、..、symlink 逃逸、越界)。
    """
    err = _reject_abs_or_dotdot(rel)
    if err:
        raise PathEscapeError(err)

    root_resolved = Path(root_dir).resolve()
    allowed = [root_resolved] + [Path(p).resolve() for p in allowed_roots]

    cand = (root_resolved / rel).resolve(strict=False)
    if not any(cand.is_relative_to(r) for r in allowed):
        raise PathEscapeError(
            f"路径 {rel} 解析为 {cand},不在允许的工作区内"
            f"(允许根: {', '.join(str(r) for r in allowed)})"
        )

    # symlink 逐级检查: 不只查末级,防止中间组件 symlink 指向工作区外
    cur = root_resolved
    for part in Path(rel).parts:
        cur = cur / part
        try:
            if cur.is_symlink():
                target = cur.resolve(strict=False)
                if not any(target.is_relative_to(r) for r in allowed):
                    raise PathEscapeError(
                        f"路径组件 {part} 是 symlink,目标 {target} 逃逸出工作区"
                    )
        except OSError:
            # Windows 权限不足时无法 stat,放弃该级检查,末级 resolve+is_relative_to 兜底
            pass
    return cand


def _allowed_roots(config) -> list[Path]:
    """从 config 收集允许根: root_dir + workspace + writable_dirs + plans 目录。"""
    root = getattr(config, "root_dir", None)
    roots: list[Path] = []
    if root is not None:
        roots.append(Path(root))
    for attr in ("workspace", "writable_dirs"):
        for p in getattr(config, attr, None) or []:
            if p:
                roots.append(Path(p))
    try:
        from uniclaw.tools.plan import get_plans_dir

        roots.append(get_plans_dir(config))
    except Exception:
        pass
    return roots


# ── Update 应用 ────────────────────────────────────────────────────────────


def _line_eq(a: str, b: str, mode: int) -> bool:
    """行相等判定。mode: 0=精确, 1=忽略行尾空白, 2=忽略首尾空白。"""
    if mode == 0:
        return a == b
    if mode == 1:
        return a.rstrip() == b.rstrip()
    return a.strip() == b.strip()


def _find_window(lines: list[str], cursor: int, win: list[str]) -> tuple[int, int]:
    """在 lines[cursor:] 中找 win 的匹配窗口(win = context + del_lines)。

    三遍宽松: 全窗口精确(mode=0) → 全窗口 rstrip(mode=1) → 全窗口 strip(mode=2)。
    候选起点从 cursor 递增扫描,取第一个命中(锚点)。
    找不到时,若窗口在文件更早处(pos_all < cursor)出现过 → 报乱序,否则报未匹配。

    Returns:
        (start, matched_mode): 命中起点与使用的宽松模式。start 指向窗口首行。

    Raises:
        InvalidContext: 未命中或乱序。
    """
    if not win:
        return cursor, 0
    win_len = len(win)
    last_start = len(lines) - win_len
    if last_start < 0:
        raise InvalidContext(
            f"上下文窗口({win_len} 行)比文件剩余内容还长,无法匹配"
        )
    # 整个文件中该窗口首次精确出现的位置 —— 找不到匹配时用来区分"乱序"与"不存在"
    pos_all: int | None = None
    for s in range(0, last_start + 1):
        if all(lines[s + k] == win[k] for k in range(win_len)):
            pos_all = s
            break
    for mode in (0, 1, 2):
        for s in range(cursor, last_start + 1):
            if all(_line_eq(lines[s + k], win[k], mode) for k in range(win_len)):
                return s, mode
    if pos_all is not None and pos_all < cursor:
        raise InvalidContext(
            f"hunk 乱序: 该上下文在文件第 {pos_all + 1} 行就出现过,游标已在第 {cursor + 1} 行之后;"
            f"请按文件内自上而下顺序排列 hunk"
        )
    raise InvalidContext(
        f"上下文未匹配(从游标第 {cursor + 1} 行之后到文件尾找 {win_len} 行未命中)。"
        f"首行期望: {win[0]!r}"
    )


def _apply_body(hunk: Hunk, file_rows: list[str]) -> list[str]:
    """按 hunk 的 body 顺序生成匹配区的新内容。

    标准 diff 语义: 匹配序列 = body 中所有非 ins 行按原始顺序(context 与
    del 交错分布)。匹配序列的长度等于 file_rows 的长度(调用方已保证)。
    逐行处理:
    - ctx 行: 保留 file_rows 中对应位置的行
    - del 行: 跳过该文件行(删除)
    - ins 行: 插入内容(到"当前已消费的文件行"之后)

    Args:
        hunk: 要应用的 hunk。
        file_rows: 匹配序列对应的文件行(长度 = 非 ins 行数)。

    Returns:
        list[str]: 匹配区的新内容(含保留的 ctx、跳过的 del、插入的 ins)。
    """
    out: list[str] = []
    fi = 0  # file_rows 指针
    body = hunk.body
    for idx, (kind, content) in enumerate(body):
        if kind == "ctx":
            row = file_rows[fi]
            # 该行无尾换行(通常是被匹配到文件尾部),且其后仍有内容要输出
            # (ins 插入 / 后续 ctx 保留)—— 补换行防止与后续内容粘连
            if not row.endswith("\n") and idx < len(body) - 1:
                row += "\n"
            out.append(row)
            fi += 1
        elif kind == "del":
            fi += 1  # 删除: 跳过
        else:  # ins
            out.append(content)
    return out


def _apply_update(old_content: str, hunks: list[Hunk]) -> str:
    """对文件旧内容应用全部 hunk,返回新内容(LF 域)。

    Args:
        old_content: 文件原始内容(保持原换行,内部归一 LF)。
        hunks: 该文件的全部 hunk(已按 patch 顺序)。

    Returns:
        str: 新内容(LF 域;调用方按 is_pure_crlf 恢复)。

    Raises:
        InvalidContext: hunk 上下文未命中或乱序。
    """
    mode = _eol_mode(old_content)
    if mode == "crlf":
        content_norm = old_content.replace("\r\n", "\n")
    elif mode == "cr":
        content_norm = old_content.replace("\r", "\n")
    else:
        content_norm = old_content
    lines = content_norm.splitlines(keepends=True)
    out: list[str] = []
    cursor = 0
    replace_eof: list[bool] = []  # 每个"匹配窗口延伸到文件末尾"的 hunk 是否替换了原末行

    for hunk in hunks:
        # 匹配序列 = body 中非 ins 行按原始顺序(标准 diff: context 可在删除区两侧)
        seq = [c for k, c in hunk.body if k != "ins"]

        if hunk.is_eof:
            # EOF: 该 hunk 只作用于文件尾部。窗口定位从尾部倒搜,删除区
            # 必须锚定到文件末尾,否则删到的不是尾部而是文件中部的同名行。
            if seq:
                win_len = len(seq)
                if win_len > len(lines) - cursor:
                    raise InvalidContext(
                        f"EOF 上下文窗口({win_len} 行)比游标后文件内容"
                        f"({len(lines) - cursor} 行)还长,无法匹配"
                    )
                found = None
                max_start = len(lines) - win_len
                for mode in (0, 1, 2):
                    for s in range(max_start, cursor - 1, -1):
                        if all(_line_eq(lines[s + k], seq[k], mode) for k in range(win_len)):
                            found = s
                            break
                    if found is not None:
                        break
                if found is None:
                    raise InvalidContext(f"EOF hunk 上下文未匹配: {seq[0]!r}")
                if any(k == "del" for k, _ in hunk.body):
                    # 有删除时,匹配区必须锚定文件末尾,防止误删中部同名行
                    if found + win_len != len(lines):
                        raise InvalidContext(
                            f"EOF hunk 删除区不锚定在文件末尾: 窗口定位在 {found + 1} 行,"
                            f"匹配区 [found 起 {win_len} 行] 之后还有 "
                            f"{len(lines) - (found + win_len)} 行;"
                            f"EOF 只应作用于文件末尾,请检查上下文"
                        )
                out.extend(lines[cursor:found])
                out.extend(_apply_body(hunk, lines[found : found + win_len]))
                cursor = found + win_len
                continue
            # 纯追加(无任何匹配行): 保留游标后全部行,末尾行若无换行先补换行再追加
            out.extend(lines[cursor:])
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            ins = [c for k, c in hunk.body if k == "ins"]
            out.extend(ins)
            cursor = len(lines)
            continue

        # 普通 hunk: 在文件中找匹配序列
        start, _ = _find_window(lines, cursor, seq)
        out.extend(lines[cursor:start])
        out.extend(_apply_body(hunk, lines[start : start + len(seq)]))
        # 匹配序列在文件中占 len(seq) 行;ins 行是输出新增,不占文件行。
        cursor = start + len(seq)
        # Bug H/Bug B/Bug J: 跟踪"匹配窗口延伸到原文件末尾 ∧ 原末行被替换"的 hunk。
        # 精确特征: body 中"最后一个 del 行"之后还有 ins 行 —— 即 del 删掉
        # 的内容(含可能的原末行)被 ins 补位替换。
        # 纯删除末行(Bug H): 最后一个 del 后无 ins → 不裁剪。
        # 替换中间行+删末行(Bug J): 末行 l4 是 del 且其后无 ins → 不裁剪。
        if start + len(seq) == len(lines):
            last_del_idx = max((i for i, (k, _) in enumerate(hunk.body) if k == "del"), default=-1)
            has_ins_after_last_del = any(k == "ins" for k, _ in hunk.body[last_del_idx + 1 :])
            replace_eof.append(last_del_idx >= 0 and has_ins_after_last_del)

    out.extend(lines[cursor:])
    result = "".join(out)
    # Bug B: 原文件末行无尾换行,且存在"替换原末行"的 hunk(匹配窗口覆盖原末行
    # 且含 ins 行)时,保持无尾换行 —— 避免等量替换末行被静默补 \n。
    # 纯删除末行(Bug H): 新末行是原倒数第二行,自带换行,不得裁剪。
    if (
        result
        and lines
        and not lines[-1].endswith("\n")
        and not any(h.is_eof for h in hunks)
        and replace_eof
        and any(replace_eof)
    ):
        result = result[:-1] if result.endswith("\n") else result
    return result


# ── 权限层 helper ──────────────────────────────────────────────────────────


def extract_patch_paths(patch: str, root_dir: Path | None) -> list[Path]:
    """从 patch 文本纯静态提取所有目标文件的路径(不做任何文件 IO)。

    收集 Add / Update / Delete File 标记行的路径,以及 Update 块内紧跟的
    Move to 目标。绝对路径与 '..' 路径被排除(不可判定)。root_dir 为 None
    时无法 resolve,返回按相对形式构造的 Path(用于展示)。

    Args:
        patch: patch 文本。
        root_dir: 会话工作目录;为 None 时返回相对 Path(展示用)。

    Returns:
        list[Path]: 目标路径列表(无文件操作时为空)。
    """
    if not patch:
        return []
    paths: list[str] = []
    for text in patch.splitlines():
        stripped = text.rstrip("\r\n")
        if not stripped.startswith("*** "):
            continue  # 只处理标记行,跳过 + / - / 空格前缀的内容行
        m = _ADD_RE.match(stripped) or _UPDATE_RE.match(stripped) or _DELETE_RE.match(stripped)
        if m:
            rel = m.group(1).strip()
            if rel and not _reject_abs_or_dotdot(rel):
                paths.append(rel)
        m2 = _MOVE_RE.match(stripped)
        if m2:
            rel = m2.group(1).strip()
            if rel and not _reject_abs_or_dotdot(rel):
                paths.append(rel)
    if root_dir is None:
        return [Path(p) for p in paths]
    root_resolved = Path(root_dir).resolve()
    return [(root_resolved / p).resolve(strict=False) for p in paths]


# ── 工具函数 ──────────────────────────────────────────────────────────────


def _eol_mode(old_content: str) -> str:
    """检测文件换行模式: "cr"(\r-only) / "crlf"(纯 CRLF) / "lf"(LF 或混合)。"""
    if "\r\n" in old_content:
        crlf_count = old_content.count("\r\n")
        lf_count = old_content.count("\n")
        if crlf_count == lf_count:
            return "crlf"
        return "lf"  # 混合换行: 保持原样
    if "\r" in old_content and "\n" not in old_content:
        return "cr"  # 老 Mac 换行
    return "lf"


def _maybe_restore_bom(content: str, src_path: Path) -> str:
    """若源文件是 UTF-8 BOM(EF BB BF),给内容补回 BOM。

    匹配阶段剥离了 BOM(Bug A 修复),这里在读盘结果仍可用时恢复,
    保证写回后 BOM 保留。读不出或非 BOM 则原样返回。
    """
    try:
        with src_path.open("rb") as f:
            head = f.read(3)
    except OSError:
        return content
    if head == b"\xef\xbb\xbf" and not content.startswith("﻿"):
        return "﻿" + content
    return content


def _restore_eol(content_norm: str, old_content: str) -> str:
    """按文件原换行格式恢复: 纯 CRLF 恢复 CRLF,\r-only 恢复 \\r,其它保持 LF。"""
    mode = _eol_mode(old_content)
    if mode == "crlf":
        return content_norm.replace("\n", "\r\n")
    if mode == "cr":
        return content_norm.replace("\n", "\r")
    return content_norm


def _binary_check(p: Path) -> str | None:
    """二进制防护: 返回 None 或错误文本。UTF-16/32/BOM 豁免。

    检测覆盖整个文件(不只是头部),避免 8KB 之后的 NUL 字节漏检:
    - 常见二进制魔数(头部判断)
    - 全文 NUL 字节扫描(文本文件不应含 NUL)
    """
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    head = raw[:_BINARY_HEAD_BYTES]
    if len(head) >= 2 and head.startswith(
        (b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
    ):
        return None
    kind = _binary_file_kind(head)
    if kind:
        return f"{p} 是二进制文件({kind}),不适合文本修改"
    # 全文 NUL 扫描: 文本文件不应含 NUL(UTF-16/32 已在上面 BOM 豁免)
    if b"\x00" in raw:
        return f"{p} 是二进制文件(内容含 NUL 字节),不适合文本修改"
    return None


def _detect_non_utf8(p: Path) -> str | None:
    """检测文件是否为非 UTF-8 文本编码,返回 None 或错误文本。

    apply_patch 只支持 UTF-8 文本(可含 UTF-8 BOM)。GBK / latin-1 /
    UTF-16 / UTF-32 等编码经 errors="replace" 读取后会产生替换字符,
    导致匹配必然失败,且报"上下文未匹配"误导用户。这里用字节级
    strict 解码确认编码。

    Args:
        p: 文件路径(读字节用)。

    Returns:
        str | None: 非 UTF-8 时的报错文本;可正常处理时返回 None。
    """
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    # UTF-16/32(BOM 前缀): 不支持,明确报编码错误而非"上下文未匹配"
    if raw.startswith(b"\xef\xbb\xbf"):
        return None  # UTF-8 BOM: 支持(读取时剥离)
    if len(raw) >= 2 and raw.startswith(
        (b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
    ):
        return (
            f"{p} 的编码是 UTF-16/UTF-32(检测到 BOM),"
            "apply_patch 只支持 UTF-8 文本文件,请先转换为 UTF-8"
        )
    try:
        raw.decode("utf-8")
        return None
    except UnicodeDecodeError:
        return (
            f"{p} 的编码不是 UTF-8(字节级解码失败),"
            "apply_patch 只支持 UTF-8 文本文件,请先转换编码"
        )


def _op_paths(op: PatchOp) -> list[str]:
    """返回该操作影响的全部相对路径(delete/add/update → [rel];move → [src, dst])。"""
    if op.kind == "move":
        return [op.rel, op.dst_rel or ""]
    return [op.rel]


def _check_path_conflict(op: PatchOp, path_ops: dict[str, list[str]]) -> str | None:
    """同路径多操作冲突检测(Bug K)。

    除"前序恰好一次 Delete → 本次 Add"(先删后建,合法)外,同一路径在同一次
    patch 中出现第二个操作即冲突 —— 否则应用期按顺序执行会静默删后建、
    部分应用或产生误导性报告。

    Returns:
        str | None: 冲突报错文本;无冲突返回 None。
    """
    for path in _op_paths(op):
        prev = path_ops.get(path, [])
        if prev and not (prev == ["delete"] and op.kind == "add"):
            return (
                f"第 {op.patch_line} 行: 同一路径 {path} 在同一次 patch 中"
                f"出现多个操作({'+'.join(prev)} 后再 {op.kind}),语义矛盾,"
                "请拆分为单独调用或调整操作顺序"
            )
    return None


def _record_path_op(op: PatchOp, path_ops: dict[str, list[str]]) -> None:
    """把本操作涉及的路径记入 path_ops(供后续冲突检测)。"""
    for path in _op_paths(op):
        path_ops.setdefault(path, []).append(op.kind)


def _calculate_plan(
    op: PatchOp,
    root_dir: Path,
    allowed: list[Path],
    pre_deleted: set[str] | None = None,
) -> tuple[Path, str, str, str]:
    """为单个操作计算目标绝对路径与写盘内容(不落盘)。

    Args:
        op: 要计算的操作。
        root_dir: 会话工作目录。
        allowed: 允许根列表。
        pre_deleted: 同 patch 中已在前序 Delete 中删除的相对路径集合。
            Add 校验目标已存在时,若路径在此集合中且文件确实已不在磁盘,
            视为"先删后建"的替换意图,放行。

    Returns:
        (abs_path, old_content, final_content, kind_detail), kind_detail 为
        "add" | "update" | "move" | "delete"。

    Raises:
        PathEscapeError / InvalidContext / ValueError / FileNotFoundError / FileExistsError。
    """
    if op.kind == "delete":
        op.src_abs = _resolve_checked(op.rel, root_dir, allowed)
        if not op.src_abs.exists():
            raise FileNotFoundError(f"文件不存在: {op.rel}")
        if op.src_abs.is_dir():
            raise ValueError(f"{op.rel} 是一个目录,不能删除(如需删除目录请用其它工具)")
        return op.src_abs, "", "", "delete"

    if op.kind == "add":
        op.src_abs = _resolve_checked(op.rel, root_dir, allowed)
        # 同 patch 前序 Delete 同路径 = "先删后建"的替换意图: 放行。
        # (计划期文件还在磁盘,delete 会在应用期先执行,之后 add 才能落盘。)
        replaced = pre_deleted is not None and op.rel in pre_deleted
        if op.src_abs.exists() and not replaced:
            raise FileExistsError(f"Add File 目标已存在: {op.rel}(如需修改请用 Update File)")
        new_content = "".join(op.hunks[0].ins_lines)
        return op.src_abs, "", new_content, "add"

    # update / move
    op.src_abs = _resolve_checked(op.rel, root_dir, allowed)
    if not op.src_abs.exists():
        raise FileNotFoundError(f"文件不存在: {op.rel}")
    if op.src_abs.is_dir():
        raise ValueError(f"{op.rel} 是一个目录,不能作为文本文件修改")
    if op.dst_rel is not None:
        op.dst_abs = _resolve_checked(op.dst_rel, root_dir, allowed)
        if op.dst_abs.exists():
            if op.dst_abs == op.src_abs:
                pass  # move 到同路径: 等价于普通 update
            elif op.dst_abs.is_dir():
                raise ValueError(f"Move 目标 {op.dst_rel} 是一个目录,不能覆盖")
            else:
                raise FileExistsError(
                    f"Move 目标已存在: {op.dst_rel},为避免覆盖请先删除或用 Update 直接修改"
                )
    b_err = _binary_check(op.src_abs)
    if b_err:
        raise ValueError(b_err)
    old = _read_preserving_newlines(op.src_abs)
    enc_err = _detect_non_utf8(op.src_abs)
    if enc_err:
        raise ValueError(enc_err)
    # 剥离 UTF-8 BOM 再匹配(hunk 内容不含 BOM,首行才能命中——Bug A)。
    # 写回时由 _restore_bom 恢复。
    if old.startswith("﻿"):
        old = old[1:]
    new_norm = _apply_update(old, op.hunks)
    final = _restore_eol(new_norm, old)
    kind_detail = "move" if op.dst_abs is not None else "update"
    return op.src_abs, old, final, kind_detail


def _compose_report(entries: list[tuple[str, str, str]], header: str) -> str:
    """把 (rel, summary, detail) 列表排版为报告文本。"""
    lines_out = [header]
    for rel, summary, detail in entries:
        prefix = f"- {rel}: {summary}" if summary else f"- {rel}:"
        lines_out.append(prefix if not detail else f"{prefix}\n  {detail}")
    return "\n".join(lines_out)


def _diff_safe(old: str, final: str, filename: str) -> str:
    """生成可读 diff: 补换行避免 difflib 把 -/+ 行粘连展示。

    difflib.unified_diff 对"行尾无 \\n"的行直接原样输出(不带换行),
    导致 `-old+new` 粘连成一行,看似数据损坏(实际文件正确)。处理两类:
    - 末行无尾换行: 补一个 \\n
    - CR-only(\\r 分隔,老 Mac): 先把 \\r 归一为 \\n 再 diff(仅展示)
    """
    old_norm = old.replace("\r", "\n") if "\r" in old and "\n" not in old else old
    final_norm = final.replace("\r", "\n") if "\r" in final and "\n" not in final else final
    old_ready = old_norm if not old_norm or old_norm.endswith("\n") else old_norm + "\n"
    final_ready = final_norm if not final_norm or final_norm.endswith("\n") else final_norm + "\n"
    return generate_unified_diff(old_ready, final_ready, filename)


@tool
async def apply_patch(
    patch: str, dry_run: bool = False, tool_runtime: ToolRuntime = None
) -> str:
    """
    批量修改多个文件 — 一次调用完成多个文件的读、增、改、删、移动。

    使用以下格式(@@ 是可选标签,不携带行号):

        *** Begin Patch
        *** Add File: src/new.py
        +line1
        +line2
        *** Update File: src/old.py
        @@
        -old
        +new
        @@ optional_label
         -more_ctx
        -removed
        +added
        *** Delete File: src/obsolete.py
        *** End Patch

    规则:
    - 标记行必须精确以 "*** "(三颗星+空格)开头
    - 文件路径必须相对,禁止绝对路径与 ".."
    - "*** Move to: newpath" 紧跟在其所属 "*** Update File" 标记后,表示重命名
    - "*** End of File" 表示该 hunk 延伸到文件末尾(可在尾部追加)
    - 同一文件只能有一个 Update 块,多个改动点用多个 "@@" 合并
    - hunk 按文件内自上而下顺序排列;上下文行以空格开头(建议 2~3 行)
    - 匹配宽松:精确 → 忽略行尾空白 → 忽略首尾空白 逐级降级;未命中会报错,可据报错调整后重试
    - 契合 CRLF 文件(自动保持原换行);拒绝二进制文件与逃逸工作区的路径
    - 一次调用内出现失败会停止后续应用,报告中列明已应用/失败的文件
    - 警告: 每一行 "*** Add/Update/Delete File:" / "*** Move to:" 都是真实操作。
      Add 内容行必须以 "+" 开头;若漏写前缀,该行会被当作独立操作执行
      (如 "*** Delete File: x" 会真的删除 x)。写文本内容前务必补全前缀

    Args:
        patch: 按上述格式书写的完整 patch 文本
        dry_run: 为 True 时只解析和校验,不写入磁盘,返回将应用的差异报告

    Returns:
        str: 各文件的差异/创建/删除报告;失败时返回以 {TOOL_ERROR} 开头的报错说明
    """
    config = tool_runtime.config if tool_runtime else None
    root_dir = getattr(config, "root_dir", None) if config else None
    if root_dir is None:
        return (
            f"{TOOL_ERROR}: apply_patch 需要会话工作目录(config.root_dir)"
            "以解析相对路径。调用时需提供 tool_runtime/config;不能用进程 cwd 代替。"
        )

    ops, err = parse_patch(patch)
    if err:
        return f"{TOOL_ERROR}: {err}"
    if not ops:
        return f"{TOOL_ERROR}: patch 不完整(没有文件操作)"

    allowed = _allowed_roots(config)

    # 1. 全量路径安全校验 + 计算计划(不落盘),任一失败则整体不执行任何写
    pre_deleted: set[str] = set()  # 前序 Delete 已声明删除的相对路径(供 Add 放行"先删后建")
    path_ops: dict[str, list[str]] = {}  # 相对路径 → 已出现操作类型(同路径冲突检测)
    plan: list[tuple[PatchOp, Path, str, str, str]] = []
    for op in ops:
        conflict = _check_path_conflict(op, path_ops)
        if conflict:
            return f"{TOOL_ERROR}: {conflict}"
        if op.kind == "delete":
            pre_deleted.add(op.rel)
        try:
            abs_path, old, final, kind_detail = _calculate_plan(
                op, Path(root_dir), allowed, pre_deleted
            )
        except FileNotFoundError as e:
            return f"{TOOL_ERROR}: {e}"
        except FileExistsError as e:
            return f"{TOOL_ERROR}: {e}"
        except ValueError as e:
            return f"{TOOL_ERROR}: {e}"
        # 记录本 op 涉及的路径(src;Move 还涉及 dst),供后续冲突检测
        _record_path_op(op, path_ops)
        plan.append((op, abs_path, old, final, kind_detail))

    # 2. 构建报告条目
    entries: list[tuple[str, str, str]] = []
    for op, abs_path, old, final, kind_detail in plan:
        if kind_detail == "delete":
            # Delete 是真实删除操作,报告醒目标注目标路径(可追溯性):
            # 便于区分"主动删除"与"漏写前缀被误当操作"的文本行。
            entries.append((op.rel, "⚠️ 已删除", "删除目标路径(相对): " + op.rel))
        elif kind_detail == "add":
            lc = final.count("\n") + (1 if final and not final.endswith("\n") else 0)
            entries.append((op.rel, f"已创建 ({lc} 行)", ""))
        elif kind_detail == "move":
            diff = _diff_safe(old, final, Path(op.rel).name)
            entries.append((op.rel, f"已移动至 {op.dst_rel}", diff))
        else:
            diff = _diff_safe(old, final, Path(op.rel).name)
            entries.append((op.rel, "", diff if diff else "无变化"))

    # 3. dry_run: 不落盘
    if dry_run:
        return _compose_report(entries, f"将应用修改到 {len(plan)} 个文件(dry-run,未写盘):")

    # 4. 串行应用
    applied: list[tuple[str, str, str]] = []
    for (op, abs_path, old, final, kind_detail), entry in zip(plan, entries):
        try:
            if kind_detail == "delete":
                abs_path.unlink()
            elif kind_detail == "add":
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(final, encoding="utf-8", newline="")
            elif kind_detail == "move":
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                op.dst_abs.parent.mkdir(parents=True, exist_ok=True)
                op.dst_abs.write_text(
                    _maybe_restore_bom(final, abs_path),
                    encoding="utf-8",
                    newline="",
                )
                if abs_path != op.dst_abs:
                    abs_path.unlink()
            else:
                # update: 源文件还在,读其头判断是否需要恢复 UTF-8 BOM(Bug A)
                abs_path.write_text(
                    _maybe_restore_bom(final, abs_path),
                    encoding="utf-8",
                    newline="",
                )
            applied.append(entry)
        except OSError as e:
            applied_desc = "\n".join(f"- {r}: {s}" for r, s, _ in applied) or "(无)"
            return (
                f"{TOOL_ERROR}: 文件 {op.rel} 应用失败: {type(e).__name__}: {e}\n"
                f"已应用的 {len(applied)} 个文件已写入:\n{applied_desc}\n"
                f"如需撤销请使用 /undo(本轮修改已自动建立检查点)"
            )
    return _compose_report(applied, f"已应用修改到 {len(applied)} 个文件:")


def get_tools() -> list:
    """获取批量文件修改工具"""
    return [apply_patch]


def get_all_tools() -> list:
    """获取所有批量文件修改工具(无条件返回)"""
    return [apply_patch]