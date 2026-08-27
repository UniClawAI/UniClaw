"""alphaXiv 搜索实现。

alphaXiv 无公开全文搜索 API, 但提供稳定的 URL 模式:
`https://www.alphaxiv.org/abs/{arxiv_id}` 指向某篇论文的讨论页。
本实现委托 arXiv 搜索, 再为每条结果生成对应的 alphaXiv 讨论页链接。
"""

import re

from uniclaw.config import AppConfig

from . import arxiv

NAME = "alphaxiv"
LABEL = "alphaXiv"


async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """alphaXiv 搜索 (基于 arXiv 结果生成社区讨论页链接)。"""
    # time_range 透传给 arXiv (submittedDate 过滤), 自动生效
    arxiv_text = await arxiv.search(
        query, limit, sort, search_type, config, time_range=time_range
    )
    if arxiv_text.startswith("arXiv: 无搜索结果") or arxiv_text.startswith(
        "[PLATFORM_ERROR]"
    ):
        return arxiv_text

    # 从 arXiv 结果文本中提取 arxiv_id 与标题
    lines = []
    count = 0
    for block in arxiv_text.split("\n\n"):
        m = re.search(r"(https?://arxiv\.org/abs/([\w\.\-]+?)(?:v\d+)?(?:/|$))", block)
        if not m:
            continue
        arxiv_url = m.group(1)
        arxiv_id = m.group(2)
        title_m = re.search(r"\*\*(.+?)\*\*", block)
        title = title_m.group(1) if title_m else arxiv_id
        alpha_url = f"https://www.alphaxiv.org/abs/{arxiv_id}"
        lines.append(f"**{title}**")
        lines.append(f"  arXiv: {arxiv_url}")
        lines.append(f"  讨论页: {alpha_url}\n")
        count += 1
        if count >= limit:
            break

    if not lines:
        return "alphaXiv: 无搜索结果"
    return f"**alphaXiv 搜索结果** ({count} 条):\n\n" + "\n".join(lines)
