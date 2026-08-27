"""Stack Overflow 搜索实现。"""

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, http_get, safe_search
from .time_range import epoch, parse_time_range

NAME = "stackoverflow"
LABEL = "Stack Overflow"

_VALID_SORTS = ("relevance", "votes", "creation", "activity")


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """Stack Overflow 搜索。"""
    s = sort if sort in _VALID_SORTS else "relevance"
    # 用 /search/advanced 的 q (全文搜索, 匹配标题+正文) 而非 /search 的
    # intitle (仅标题且要求全部词命中): intitle 对多词查询过于严格,
    # 实测 "pandas merge vs join" 直接返回空; 且 /search 强制要求 intitle
    # 或 tagged, 无法单独使用 q。
    params = {
        "order": "desc",
        "sort": s,
        "q": query,
        "site": "stackoverflow",
        "pagesize": limit,
    }
    tr = parse_time_range(time_range)
    if tr.start:
        params["fromdate"] = epoch(tr.start)
    if tr.end:
        params["todate"] = epoch(tr.end)
    r = await http_get(
        "https://api.stackexchange.com/2.3/search/advanced",
        params=params,
        headers={"User-Agent": DEFAULT_UA},
        config=config,
        use_proxy=False,
    )
    data = r.json()
    items = data.get("items", [])
    if not items:
        return "Stack Overflow: 无搜索结果"
    lines = [f"**Stack Overflow 搜索结果** ({len(items)} 条):\n"]
    for item in items:
        title = item.get("title", "")
        link = item.get("link", "")
        answers = item.get("answer_count", 0)
        views = item.get("view_count", 0)
        tags = item.get("tags", [])
        is_answered = item.get("is_answered", False)
        score = item.get("score", 0)
        status = "✅已解决" if is_answered else "❌未解决"
        lines.append(f"**{title}** [{status}] 👍{score}")
        lines.append(f"  回答: {answers} | 浏览: {views} | 标签: {', '.join(tags[:4])}")
        lines.append(f"  {link}\n")
    return "\n".join(lines)
