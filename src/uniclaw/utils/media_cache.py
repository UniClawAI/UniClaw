import hashlib
import json
import time
from pathlib import Path

from uniclaw.context import get_app_dir

# 内存缓存:hash -> description,会话内免 IO
_memory_cache: dict[str, str] = {}


def _cache_dir() -> Path:
    d = get_app_dir() / "media_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def compute_hash(media_url: str) -> str:
    return hashlib.sha256(media_url.encode("utf-8")).hexdigest()


def get_cached_description(content_hash: str) -> str | None:
    if content_hash in _memory_cache:
        return _memory_cache[content_hash]
    cache_file = _cache_dir() / f"{content_hash}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            desc = data.get("description")
            if desc:
                _memory_cache[content_hash] = desc
                return desc
        except (json.JSONDecodeError, OSError):
            pass
    return None


def save_description(content_hash: str, description: str, model: str, source: str):
    _memory_cache[content_hash] = description
    cache_file = _cache_dir() / f"{content_hash}.json"
    try:
        cache_file.write_text(
            json.dumps(
                {
                    "description": description,
                    "model": model,
                    "source": source,
                    "timestamp": time.time(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
