"""GitHub 搜索实现。"""

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, http_get, safe_search
from .time_range import parse_time_range, iso_date

# 平台标识与显示名
NAME = "github"
LABEL = "GitHub"

_VALID_TYPES = ("repositories", "code", "issues", "users")
_VALID_SORTS = ("stars", "forks", "updated", "best-match")


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """GitHub 搜索。"""
    st = search_type if search_type in _VALID_TYPES else "repositories"
    s = sort if sort in _VALID_SORTS else "stars"
    url = f"https://api.github.com/search/{st}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": DEFAULT_UA}
    token = config.GITHUB_TOKEN if config else ""
    if token:
        headers["Authorization"] = f"token {token}"
    # GitHub 不支持独立的日期参数, 通过查询限定词实现时间过滤:
    # repositories 用 pushed:, issues 用 updated:; code/users 无对应字段
    tr = parse_time_range(time_range)
    field = {"repositories": "pushed", "issues": "updated"}.get(st)
    q = query
    if field:
        if tr.start:
            q += f" {field}:>={iso_date(tr.start)}"
        if tr.end:
            q += f" {field}:<={iso_date(tr.end)}"
    r = await http_get(
        url,
        params={"q": q, "sort": s, "per_page": limit},
        headers=headers,
        config=config,
        use_proxy=False,
    )
    data = r.json()
    items = data.get("items", [])
    if not items:
        return "GitHub: 无搜索结果"
    lines = [f"**GitHub 搜索结果** ({st}, {len(items)} 条):\n"]
    for item in items:
        if st == "repositories":
            name = item.get("full_name", "")
            desc = item.get("description", "") or ""
            stars = item.get("stargazers_count", 0)
            lang = item.get("language", "") or ""
            link = item.get("html_url", "")
            lines.append(f"**{name}** ⭐{stars} [{lang}]")
            lines.append(f"  {desc}")
            lines.append(f"  {link}\n")
        elif st == "code":
            name = item.get("name", "")
            path = item.get("path", "")
            repo = item.get("repository", {}).get("full_name", "")
            link = item.get("html_url", "")
            lines.append(f"**{name}** ({path})")
            lines.append(f"  仓库: {repo}")
            lines.append(f"  {link}\n")
        elif st == "issues":
            title = item.get("title", "")
            state = item.get("state", "")
            link = item.get("html_url", "")
            lines.append(f"**{title}** [{state}]")
            lines.append(f"  {link}\n")
        else:  # users
            login = item.get("login", "")
            desc = item.get("bio", "") or ""
            link = item.get("html_url", "")
            lines.append(f"**{login}** — {desc}")
            lines.append(f"  {link}\n")
    return "\n".join(lines)
