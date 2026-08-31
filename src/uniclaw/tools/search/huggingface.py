"""HuggingFace 搜索实现: 支持每日论文(papers/默认)、模型(models)、数据集(datasets)搜索。"""

import os
from datetime import date

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, http_get, safe_search
from .time_range import parse_time_range

NAME = "huggingface"
LABEL = "HuggingFace"

_ENDPOINT_DAILY_PAPERS = "https://huggingface.co/api/daily_papers"
_ENDPOINT_MODELS = "https://huggingface.co/api/models"
_ENDPOINT_DATASETS = "https://huggingface.co/api/datasets"


def _fmt_count(n: int) -> str:
    """格式化下载量/点赞数: 1.2K, 3.4M 等。"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """HuggingFace 搜索调度: 支持 papers(默认)/models/datasets。

    search_type 参数:
      - "papers" 或空: 每日论文搜索 (原有逻辑)
      - "models": 模型搜索
      - "datasets": 数据集搜索
    """
    st = (search_type or "").strip().lower()
    if st == "models":
        return await _search_models(query, limit, sort, config)
    elif st == "datasets":
        return await _search_datasets(query, limit, sort, config)
    else:
        return await _search_papers(query, limit, sort, config, time_range)


async def _search_models(
    query: str,
    limit: int,
    sort: str,
    config: AppConfig | None,
) -> str:
    """HuggingFace 模型搜索 (公开 API, 无需认证)。

    支持 sort: downloads(默认), likes, trendingScore, createdAt, lastModified, name
    """
    if not query or not query.strip():
        return "HuggingFace: 无搜索结果"

    sort_val = (sort or "downloads").strip().lower()
    valid_sorts = {"downloads", "likes", "trendingscore", "createdat", "lastmodified", "name"}
    if sort_val not in valid_sorts:
        sort_val = "downloads"

    params = {"search": query, "sort": sort_val, "direction": "-1", "limit": limit}
    headers = {"User-Agent": DEFAULT_UA}

    r = await http_get(_ENDPOINT_MODELS, params=params, headers=headers, config=config, use_proxy=True)
    data = r.json()
    if not isinstance(data, list) or not data:
        return "HuggingFace: 无搜索结果"

    lines = []
    count = 0
    for item in data[:limit]:
        model_id = item.get("id") or ""
        if not model_id:
            continue
        count += 1
        author = item.get("author") or ""
        downloads = int(item.get("downloads") or 0)
        likes = int(item.get("likes") or 0)
        pipeline_tag = item.get("pipeline_tag") or ""
        library_name = item.get("library_name") or ""
        tags = [t for t in (item.get("tags") or []) if not t.startswith("_")]
        tag_str = ", ".join(tags[:5]) if tags else ""

        line = f"**{model_id}**"
        if author:
            line += f" (by {author})"
        lines.append(line)

        meta_parts = []
        if downloads:
            meta_parts.append(f"📥 {_fmt_count(downloads)} downloads")
        if likes:
            meta_parts.append(f"👍 {_fmt_count(likes)} likes")
        if pipeline_tag:
            meta_parts.append(f"🏷️ {pipeline_tag}")
        if meta_parts:
            lines.append(f"  {' | '.join(meta_parts)}")
        if library_name:
            lines.append(f"  📚 {library_name}")
        if tag_str:
            lines.append(f"  标签: {tag_str}")
        lines.append(f"  https://huggingface.co/{model_id}\n")

    if count == 0:
        return "HuggingFace: 无搜索结果"
    return f"**HuggingFace 模型搜索结果** ({count} 个):\n\n" + "\n".join(lines)


async def _search_datasets(
    query: str,
    limit: int,
    sort: str,
    config: AppConfig | None,
) -> str:
    """HuggingFace 数据集搜索 (公开 API, 无需认证)。

    支持 sort: downloads(默认), likes, trendingScore, createdAt, lastModified, name
    """
    if not query or not query.strip():
        return "HuggingFace: 无搜索结果"

    sort_val = (sort or "downloads").strip().lower()
    valid_sorts = {"downloads", "likes", "trendingscore", "createdat", "lastmodified", "name"}
    if sort_val not in valid_sorts:
        sort_val = "downloads"

    params = {"search": query, "sort": sort_val, "direction": "-1", "limit": limit}
    headers = {"User-Agent": DEFAULT_UA}

    r = await http_get(_ENDPOINT_DATASETS, params=params, headers=headers, config=config, use_proxy=True)
    data = r.json()
    if not isinstance(data, list) or not data:
        return "HuggingFace: 无搜索结果"

    lines = []
    count = 0
    for item in data[:limit]:
        dataset_id = item.get("id") or ""
        if not dataset_id:
            continue
        count += 1
        author = item.get("author") or ""
        downloads = int(item.get("downloads") or 0)
        likes = int(item.get("likes") or 0)
        pipeline_tag = item.get("pipeline_tag") or ""
        tags = [t for t in (item.get("tags") or []) if not t.startswith("_")]
        tag_str = ", ".join(tags[:5]) if tags else ""

        line = f"**{dataset_id}**"
        if author:
            line += f" (by {author})"
        lines.append(line)

        meta_parts = []
        if downloads:
            meta_parts.append(f"📥 {_fmt_count(downloads)} downloads")
        if likes:
            meta_parts.append(f"👍 {_fmt_count(likes)} likes")
        if pipeline_tag:
            meta_parts.append(f"🏷️ {pipeline_tag}")
        if meta_parts:
            lines.append(f"  {' | '.join(meta_parts)}")
        if tag_str:
            lines.append(f"  标签: {tag_str}")
        lines.append(f"  https://huggingface.co/datasets/{dataset_id}\n")

    if count == 0:
        return "HuggingFace: 无搜索结果"
    return f"**HuggingFace 数据集搜索结果** ({count} 个):\n\n" + "\n".join(lines)


async def _search_papers(
    query: str,
    limit: int,
    sort: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """HuggingFace 每日论文搜索 (公开 API, 无需认证, 客户端按主题过滤)。"""
    headers = {"User-Agent": DEFAULT_UA}
    token = (
        (config.hf_token if config else "")
        or os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACE_TOKEN")
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = await http_get(_ENDPOINT_DAILY_PAPERS, headers=headers, config=config, use_proxy=True)
    data = r.json()
    if not isinstance(data, list):
        return "HuggingFace: 无搜索结果"

    q_terms = [t.lower() for t in query.split() if len(t) > 2]
    if not q_terms:
        q_terms = [query.lower().strip()]

    # API 不支持服务端时间过滤, 客户端按发布日期过滤
    tr = parse_time_range(time_range)

    lines = []
    count = 0
    for item in data:
        paper = item.get("paper") or {}
        title = (paper.get("title") or item.get("title") or "").strip()
        if not title:
            continue
        summary = paper.get("summary") or item.get("summary") or ""
        hay = (title + " " + summary).lower()
        if not any(t in hay for t in q_terms):
            continue
        if tr.start or tr.end:
            raw_date = (
                paper.get("publishedAt")
                or paper.get("submittedOnDailyAt")
                or item.get("publishedAt")
                or ""
            )
            try:
                d = date.fromisoformat(raw_date[:10])
            except ValueError:
                d = None  # 日期缺失/无法解析时保留条目, 避免误杀
            if d is not None:
                if tr.start and d < tr.start.date():
                    continue
                if tr.end and d > tr.end.date():
                    continue
        arxiv_id = paper.get("id") or ""
        url = f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else ""
        if not url:
            continue
        upvotes = int(paper.get("upvotes") or 0)
        num_comments = int(item.get("numComments") or 0)
        authors = [a.get("name", "") for a in (paper.get("authors") or [])]
        author_str = ", ".join(a for a in authors[:3] if a)
        if len(authors) > 3:
            author_str += f", +{len(authors) - 3} more"
        published = (
            paper.get("publishedAt")
            or paper.get("submittedOnDailyAt")
            or item.get("publishedAt")
            or ""
        )[:10]
        lines.append(f"**{title}**")
        lines.append(
            f"  作者: {author_str} | 日期: {published} | 👍{upvotes} 💬{num_comments}"
        )
        if summary:
            lines.append(f"  {summary.strip().replace(chr(10), ' ')[:200]}...")
        lines.append(f"  {url}\n")
        count += 1
        if count >= limit:
            break

    if not lines:
        return "HuggingFace: 无搜索结果"
    return f"**HuggingFace 搜索结果** ({count} 篇):\n\n" + "\n".join(lines)