from pathlib import Path
from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn, err


async def cmd_add_dir(args: str, config: AppConfig) -> bool:
    """将目录添加到当前工作空间

    支持以下功能:
    - 无参数:列出额外工作空间目录
    - <路径>: 添加额外工作空间目录
    - rm/remove <路径>: 移除额外工作空间目录

    Args:
        args: 目录路径或 rm/remove 子命令
        task: 当前代理任务对象
        config: 配置字典

    Returns:
        bool: 始终返回 True
    """
    task = config.current_agent
    args = args.strip()

    # 无参数:列出已添加的目录
    if not args:
        dirs = config.workspace
        if not dirs:
            await info("没有额外工作空间目录", config)
        else:
            lines = [f"\n额外工作空间目录 ({len(dirs)} 个):"]
            for i, d in enumerate(dirs, 1):
                lines.append(f"  {i}. {d}")
            lines.append("")
            await info("\n".join(lines), config)
        return True

    # rm/remove 子命令:移除目录
    if args.lower().startswith(("rm ", "remove ")):
        _, _, path = args.partition(maxsplit=1)
        path = path.strip()
        if not path:
            await err("请指定要移除的目录路径", config)
            return True
        try:
            abs_path = str(Path(path).resolve())
        except Exception:
            await err(f"无效路径: {path}", config)
            return True
        extra = config.workspace
        found = False
        for i, d in enumerate(extra):
            try:
                if str(Path(d).resolve()) == abs_path:
                    extra.pop(i)
                    found = True
                    break
            except Exception:
                continue
        if found:
            await ok(f"已移除额外工作空间目录: {abs_path}", config)
        else:
            await warn(f"该目录不是额外工作空间目录: {abs_path}", config)
        return True

    # 添加目录
    try:
        abs_path = Path(args).resolve()
    except Exception:
        await err(f"无效路径: {args}", config)
        return True

    if not abs_path.exists():
        await err(f"路径不存在: {args}", config)
        return True
    if not abs_path.is_dir():
        await err(f"不是目录: {args}", config)
        return True

    abs_str = str(abs_path)

    # 检查是否与当前工作目录重复
    session_root_dir = task.session.root_dir
    if session_root_dir:
        try:
            if str(Path(session_root_dir).resolve()) == abs_str:
                await warn(f"该目录已是当前工作目录: {abs_str}", config)
                return True
        except Exception:
            pass

    # 去重检查
    for d in config.workspace:
        try:
            if str(Path(d).resolve()) == abs_str:
                await warn(f"该目录已是工作空间: {abs_str}", config)
                return True
        except Exception:
            continue

    config.workspace.append(abs_str)
    await ok(f"已添加额外工作空间: {abs_str}", config)
    return True
