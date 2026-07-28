from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn, err, clr, C, _get_tui, tui_clr
from uniclaw.console.dialog import DialogManager
from uniclaw.utils.checkpoint import (
    create_checkpoint,
    list_checkpoints,
    pop_checkpoint,
    apply_checkpoint,
    delete_checkpoint,
    diff_checkpoint,
    diff_current,
    diff_between,
)

# 子命令列表
SUBCOMMANDS = ["create", "pop", "apply", "delete", "diff"]


async def cmd_checkpoint(args: str, config: AppConfig) -> bool:
    """管理检查点

    /checkpoint           — 列出所有检查点
    /checkpoint create [描述] — 创建检查点
    /checkpoint diff      — 查看当前未提交的变更
    /checkpoint diff <序号> — 当前修改 vs 指定检查点
    /checkpoint diff <a> <b> — 比较两个检查点
    /checkpoint pop       — 恢复最近的检查点并删除
    /checkpoint pop <序号> — 恢复指定检查点并删除
    /checkpoint apply     — 恢复最近的检查点(保留)
    /checkpoint apply <序号> — 恢复指定检查点(保留)
    /checkpoint delete <序号> — 删除指定检查点
    /checkpoint <序号>     — 恢复指定检查点(保留)
    """
    if config.is_wechat:
        await warn("微信模式不支持检查点功能。", config)
        return True

    task = config.current_agent
    root_dir = task.session.root_dir
    if not root_dir:
        await warn("当前会话没有工作目录,检查点功能不可用。", config)
        return True

    parts = args.strip().split() if args else []
    cmd = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""
    arg2 = parts[2] if len(parts) > 2 else ""

    def _print_diff(header: str, diff: str):
        tui = _get_tui()
        if tui:
            tui.print(tui_clr(header, C.CYAN))
            tui.print(DialogManager.diff_fragments(diff))
        else:
            print(clr(header, C.CYAN))
            print(diff)

    # /checkpoint create [描述]
    if cmd == "create":
        message = " ".join(parts[1:]) if len(parts) > 1 else ""
        success = await create_checkpoint(root_dir, message)
        if success:
            await ok("✓ 检查点已创建", config)
        else:
            await info("没有变更,跳过创建", config)
        return True

    # /checkpoint diff [序号] [序号]
    if cmd == "diff":
        if arg.isdigit() and arg2.isdigit():
            diff = await diff_between(root_dir, int(arg), int(arg2))
            _print_diff(f"📸 检查点[{arg}] vs 检查点[{arg2}]:", diff)
        elif arg.isdigit():
            diff = await diff_checkpoint(root_dir, int(arg))
            _print_diff(f"📸 当前 vs 检查点[{arg}]:", diff)
        else:
            diff = await diff_current(root_dir)
            _print_diff("📸 当前变更:", diff)
        return True

    # /checkpoint delete <序号>
    if cmd == "delete":
        if not arg.isdigit():
            await err("用法: /checkpoint delete <序号>", config)
            return True
        success, message = await delete_checkpoint(root_dir, index=int(arg))
        if success:
            await ok(f"✓ {message}", config)
        else:
            await err(f"✗ {message}", config)
        return True

    # /checkpoint pop <序号>
    if cmd == "pop":
        idx = int(arg) if arg.isdigit() else 0
        success, message = await pop_checkpoint(root_dir, index=idx)
        if success:
            await ok(f"✓ {message}", config)
        else:
            await err(f"✗ {message}", config)
        return True

    # /checkpoint apply 或 /checkpoint <序号>
    if cmd == "apply" or cmd.isdigit():
        idx = 0 if cmd == "apply" else int(cmd)
        if arg.isdigit():
            idx = int(arg)
        success, message = await apply_checkpoint(root_dir, index=idx)
        if success:
            await ok(f"✓ {message}", config)
        else:
            await err(f"✗ {message}", config)
        return True

    # 默认行为:列出检查点
    if not cmd:
        output = await list_checkpoints(root_dir)
        await info(f"📸 检查点列表:\n{output}", config)
        return True

    await err(f"未知命令: {cmd}", config)
    return True
