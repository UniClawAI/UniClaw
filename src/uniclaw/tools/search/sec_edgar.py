"""SEC EDGAR 搜索实现。"""

import os
import re

from uniclaw.config import AppConfig

from .base import http_get, safe_search
from .time_range import iso_date, parse_time_range

NAME = "sec_edgar"
LABEL = "SEC EDGAR"

_ENDPOINT = "https://efts.sec.gov/LATEST/search-index"


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """SEC EDGAR 全文搜索 (美国上市公司文件, 无需 API key, 需标识 User-Agent)。"""
    contact = (
        (config.research_email if config else "")
        or os.environ.get("SEC_CONTACT_EMAIL")
        or "research@uniclaw.local"
    )
    headers = {
        "User-Agent": f"UniClaw-Research/1.0 ({contact})",
        "Accept": "application/json",
    }
    # 未传 time_range 时保留历史默认区间; 传入时覆盖对应一侧
    startdt, enddt = "2024-01-01", "2030-12-31"
    tr = parse_time_range(time_range)
    if tr.start:
        startdt = iso_date(tr.start)
    if tr.end:
        enddt = iso_date(tr.end)
    # EDGAR FTS 中引号表示精确短语匹配: 强制加引号会让日常多词查询
    # (如 "nvidia annual report")因文档中不存在该短语而恒为 0 结果。
    # 因此原样传参, 用户查询自带引号时才做短语搜索。
    q_param = query.strip()
    r = await http_get(
        _ENDPOINT,
        params={
            "q": q_param,
            "dateRange": "custom",
            "startdt": startdt,
            "enddt": enddt,
            "forms": "10-K,10-Q,8-K,S-1,DEF 14A,13F-HR",
        },
        headers=headers,
        config=config,
        use_proxy=False,
    )
    data = r.json()
    all_hits = (data.get("hits") or {}).get("hits") or []
    if not all_hits:
        return "SEC EDGAR: 无搜索结果"
    # 轻量重排: 公司名/ticker 命中查询词的文件优先。
    # EDGAR FTS 的相关度会把提到公司名的第三方文件(如 13F 持仓)
    # 排在公司自身文件前面; stable sort 只提升实体匹配项, 其余保持原序。
    stop = {
        "inc",
        "corp",
        "corporation",
        "llc",
        "ltd",
        "co",
        "company",
        "trust",
        "the",
        "report",
        "reports",
        "annual",
        "filing",
        "filings",
        "sec",
        "edgar",
        "10-k",
        "10-q",
        "8-k",
        "s-1",
    }
    q_tokens = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if t not in stop}

    def entity_match(hit: dict) -> int:
        names = " ".join((hit.get("_source") or {}).get("display_names") or []).lower()
        return sum(1 for t in q_tokens if t and t in names)

    hits = sorted(all_hits, key=entity_match, reverse=True)[:limit]
    lines = [f"**SEC EDGAR 搜索结果** ({len(hits)} 条):\n"]
    for hit in hits:
        source = hit.get("_source") or {}
        adsh = (hit.get("_id") or "").split(":")[0]
        display = source.get("display_names") or []
        company = display[0] if display else (source.get("name") or "")
        form = source.get("form") or ""
        filed = source.get("file_date") or ""
        cik = (source.get("ciks") or [""])[0]
        adsh_clean = adsh.replace("-", "")
        if adsh and cik:
            try:
                url = (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{adsh_clean}/{adsh}-index.htm"
                )
            except (TypeError, ValueError):
                url = ""
        else:
            url = ""
        if not url:
            url = (
                f"https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&CIK={cik}&type={form}"
            )
        loc = (source.get("biz_locations") or [""])[0]
        period = source.get("period_ending") or ""
        meta = f"filed {filed}"
        if period:
            meta += f" · 报告期 {period}"
        if loc:
            meta += f" · {loc}"
        lines.append(f"**{company} — {form}**")
        lines.append(f"  {meta}")
        lines.append(f"  {url}\n")
    return "\n".join(lines)
