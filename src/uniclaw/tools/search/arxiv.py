"""arXiv 搜索实现。"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, http_get, safe_search
from .time_range import arxiv_stamp, parse_time_range

NAME = "arxiv"
LABEL = "arXiv"

_VALID_SORTS = ("relevance", "lastUpdatedDate", "submittedDate")

# 仅指定截止日期时的下限占位: arXiv 创立日 (1991-08-14)
_ARXIV_EPOCH_STAMP = "199108140000"


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> list[str]:
    """arXiv 搜索。

    Returns:
        list[str]: 每条结果一个元素。
    """
    s = sort if sort in _VALID_SORTS else "relevance"
    sq = f"all:{query}"
    tr = parse_time_range(time_range)
    if tr.start or tr.end:
        lo = arxiv_stamp(tr.start) if tr.start else _ARXIV_EPOCH_STAMP
        hi = arxiv_stamp(tr.end) if tr.end else arxiv_stamp(datetime.now(timezone.utc))
        sq += f" AND submittedDate:[{lo} TO {hi}]"
        # arXiv API 的日期范围过滤仅在按提交/更新日期排序时才严格生效,
        # relevance 排序下会被忽略而混入窗口外的旧论文 (实测验证), 故强制切换
        s = "submittedDate"
    http_timeout = 60 if (tr.start or tr.end) else 15
    r = await http_get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": sq,
            "max_results": limit,
            "sortBy": s,
            "sortOrder": "descending",
        },
        headers={"User-Agent": DEFAULT_UA},
        config=config,
        use_proxy=True,
        timeout=http_timeout,
    )
    root = ET.fromstring(r.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)
    if not entries:
        return ["arXiv: 无搜索结果"]
    results: list[str] = [f"**arXiv 搜索结果** ({len(entries)} 篇):"]
    if tr.start or tr.end:
        results.append("[提示: 因时间范围过滤, 排序已切换为按提交日期, 相关度可能不如 relevance 排序]")
    for entry in entries:
        title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
        authors = [
            a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)
        ]
        summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")[:200]
        link = entry.find("atom:id", ns).text.strip()
        published = entry.find("atom:published", ns).text[:10]
        lines = [f"**{title}**"]
        author_str = ", ".join(authors[:3])
        if author_str:
            lines.append(f"  作者: {author_str}")
        lines.append(f"  日期: {published}")
        lines.append(f"  摘要: {summary}...")
        lines.append(f"  {link}")
        results.append("\n".join(lines))
    return results
