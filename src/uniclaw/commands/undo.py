from uniclaw.config import AppConfig
from uniclaw.console.ui import ok, err, info
from uniclaw.utils.checkpoint import apply_checkpoint


async def cmd_undo(args: str, config: AppConfig) -> bool:
    """撤销 AI 的文件编辑,恢复到检查点

    /undo        — 恢复到最近的检查点(保留)
    /undo <序号>  — 恢复到指定检查点(保留)
    """
    task = config.current_agent
    root_dir = task.session.root_dir
    if root_dir is None:
        await err("无法执行 /undo: 当前会话未设置工作目录(root_dir 为 None)", config)
        return True
    idx = int(args.strip()) if args.strip().isdigit() else 0
    success, message = await apply_checkpoint(root_dir, index=idx)
    if success:
        await ok(f"✓ {message}", config)
    else:
        await err(f"✗ {message}", config)
    return True
