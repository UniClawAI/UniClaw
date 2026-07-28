from pathlib import Path


def get_wechat_dir() -> Path:
    """获取微信数据目录。"""
    from uniclaw.context import get_app_dir

    return get_app_dir() / "wechat"
