"""alphaXiv 搜索实现。

alphaXiv 无公开全文搜索 API, 但提供稳定的 URL 模式:
`https://www.alphaxiv.org/abs/{arxiv_id}` 指向某篇论文的讨论页。
本实现委托 arXiv 搜索, 再为每条结果生成对应的 alphaXiv 讨论页链接。
"""

import re

from uniclaw.config import AppConfig

from . import arxiv
from .base import PLATFORM_ERROR

NAME = "alphaxiv"
LABEL = "alphaXiv"


async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> list[str]:
    """alphaXiv 搜索 (基于 arXiv 结果生成社区讨论页链接)。

    Returns:
        list[str]: 每条结果一个元素。
    """
    # time_range 透传给 arXiv (submittedDate 过滤), 自动生效
    arxiv_results = await arxiv.search(
        query, limit, sort, search_type, config, time_range=time_range
    )
    if arxiv_results and arxiv_results[0].startswith(PLATFORM_ERROR):
        # 平台失败透传错误(保持 PLATFORM_ERROR 前缀供 _split_results 识别)
        return arxiv_results
    if arxiv_results and arxiv_results[0].startswith("arXiv: 无搜索结果"):
        # 无结果: 换成本平台的文案, 避免用户选 alphaXiv 却看到 arXiv 字样
        return ["alphaXiv: 无搜索结果"]

    # 从 arXiv 结果元素中提取 arxiv_id 与标题 (结果元素以 **标题** 开头, 含 arxiv 链接)
    results: list[str] = []
    count = 0
    for block in arxiv_results:
        # 跳过平台标题行/提示行 (不含 arxiv 链接)
        m = re.search(r"(https?://arxiv\.org/abs/([\w\.\-]+?)(?:v\d+)?(?:/|$))", block)
        if not m:
            continue
        arxiv_url = m.group(1)
        arxiv_id = m.group(2)
        title_m = re.search(r"\*\*(.+?)\*\*", block)
        title = title_m.group(1) if title_m else arxiv_id
        alpha_url = f"https://www.alphaxiv.org/abs/{arxiv_id}"
        lines = [f"**{title}**"]
        lines.append(f"  arXiv: {arxiv_url}")
        lines.append(f"  讨论页: {alpha_url}")
        results.append("\n".join(lines))
        count += 1
        if count >= limit:
            break

    if not results:
        return ["alphaXiv: 无搜索结果"]
    results.insert(0, f"**alphaXiv 搜索结果** ({count} 条):")
    return results
