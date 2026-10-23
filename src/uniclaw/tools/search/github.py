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
) -> list[str]:
    """GitHub 搜索。

    Returns:
        list[str]: 每条结果一个元素。
    """
    st = search_type if search_type in _VALID_TYPES else "repositories"
    s = sort if sort in _VALID_SORTS else "stars"
    token = config.GITHUB_TOKEN if config else ""
    # GitHub code search API 强制要求认证 token, 匿名请求一律 401
    # (curl 实测: "Requires authentication")。无 token 时直接提示, 不发请求
    # 免得用户看到晦涩的 "HTTP 401" 或空结果。
    if st == "code" and not token:
        return [
            "[github] code 搜索需要 GitHub Token, 请在 settings.json 中配置 "
            "GITHUB_TOKEN 后重试 (匿名请求会被 GitHub 拒绝, 返回 401)"
        ]
    url = f"https://api.github.com/search/{st}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": DEFAULT_UA}
    if token:
        headers["Authorization"] = f"token {token}"
    # GitHub 不支持独立的日期参数, 通过查询限定词实现时间过滤:
    # repositories 用 pushed:, issues 用 updated:; code/users 无对应字段
    tr = parse_time_range(time_range)
    field = {"repositories": "pushed", "issues": "updated"}.get(st)
    q = query
    if st == "users":
        # users 搜索默认是全字段部分匹配 (gvanrossum 会混入 gvanrossum-ms 等),
        # 用 in:login 限定到用户名, 再对精确命中优先重排
        q += " in:login"
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
        return ["GitHub: 无搜索结果"]
    if st == "users":
        # in:login 仍是子串匹配 (查 gvanrossum 会返回 gvanrossum-ms 等), 
        # 精确命中用户名的排最前, 其余按原序跟随
        qlogin = query.strip().lower()
        items = sorted(
            items,
            key=lambda it: it.get("login", "").lower() != qlogin,
        )
    results: list[str] = [f"**GitHub 搜索结果** ({st}, {len(items)} 条):"]
    for item in items:
        if st == "repositories":
            name = item.get("full_name", "")
            desc = item.get("description", "") or ""
            stars = item.get("stargazers_count", 0)
            lang = item.get("language", "") or ""
            link = item.get("html_url", "")
            lines = [f"**{name}** ⭐{stars} [{lang}]"]
            if desc:
                lines.append(f"  {desc}")
            lines.append(f"  {link}")
            results.append("\n".join(lines))
        elif st == "code":
            name = item.get("name", "")
            path = item.get("path", "")
            repo = item.get("repository", {}).get("full_name", "")
            link = item.get("html_url", "")
            lines = [f"**{name}** ({path})"]
            if repo:
                lines.append(f"  仓库: {repo}")
            lines.append(f"  {link}")
            results.append("\n".join(lines))
        elif st == "issues":
            title = item.get("title", "")
            state = item.get("state", "")
            link = item.get("html_url", "")
            lines = [f"**{title}** [{state}]"]
            lines.append(f"  {link}")
            results.append("\n".join(lines))
        else:  # users
            login = item.get("login", "")
            desc = item.get("bio", "") or ""
            link = item.get("html_url", "")
            lines = [f"**{login}** — {desc}"]
            lines.append(f"  {link}")
            results.append("\n".join(lines))
    return results
