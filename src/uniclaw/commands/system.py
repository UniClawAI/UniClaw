import os
from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn, err


async def cmd_cwd(args: str, config: AppConfig) -> bool:
    """显示或更改当前工作目录

    - 无参数:显示当前工作目录的完整路径
    - <路径>:切换到指定的目录(支持相对路径和绝对路径)
    """
    task = config.current_agent
    if task.session.root_dir is None:
        await warn("当前会话不支持工作目录操作(无 root_dir)", config)
        return True
    if not args.strip():
        await info(f"当前工作目录: {task.session.root_dir}", config)
    else:
        import pathlib

        target_path = pathlib.Path(args.strip()).resolve()
        if not target_path.exists():
            await err(f"目录不存在: {args.strip()}", config)
            return True
        if not target_path.is_dir():
            await err(f"不是目录: {args.strip()}", config)
            return True
        try:
            task.session.root_dir = target_path
            await ok(f"工作目录已切换到: {target_path}", config)
        except Exception as e:
            await err(str(e), config)
    return True


async def cmd_skills(_args: str, config: AppConfig) -> bool:
    """列出所有可用的技能

    从多个常见项目目录中自动加载技能文件,按来源分组显示:
    内置技能、用户技能和项目技能。
    """
    task = config.current_agent
    from uniclaw.tools.skill.loader import load_skills

    skills = load_skills(
        task.session.root_dir
    )  # root_dir 为 None 时仅跳过项目级 skills
    if not skills:
        await warn("当前没有可用的技能", config)
        return True

    groups = {
        "builtin": ("【内置技能】", []),
        "user": ("【用户技能】", []),
        "project": ("【项目技能】", []),
    }
    for skill in skills:
        if skill.source in groups:
            groups[skill.source][1].append(skill)

    await info(f"\n可用技能 (共 {len(skills)} 个):\n", config)
    for source_key, (title, skill_list) in groups.items():
        if not skill_list:
            continue
        await info(title, config)
        for skill in skill_list:
            triggers = ", ".join(skill.triggers[:3])
            if len(skill.triggers) > 3:
                triggers += f" (+{len(skill.triggers) - 3})"
            await info(f"  - {skill.name}: {skill.description}", config)
            await info(f"    触发器: {triggers}", config)
            if skill.when_to_use:
                await info(f"    使用时机: {skill.when_to_use}", config)
            if skill.argument_hint:
                await info(f"    参数提示: {skill.argument_hint}", config)
            await info("", config)
    return True


async def cmd_exit(_args: str, config: AppConfig) -> bool:
    """退出程序,显示告别消息并终止运行。"""
    await ok("再见！", config)
    raise SystemExit(0)


async def cmd_usage(_args: str, config: AppConfig) -> bool:
    """显示 Token 使用统计,包括输入/输出 token 数和 API 调用次数。"""
    from uniclaw.utils.usage import format_stats

    await info(format_stats(), config)
    return True


async def cmd_help(_args: str, config: AppConfig) -> bool:
    """显示所有可用的斜杠命令帮助信息,按分类列出命令和快捷键提示。"""
    await info("\n📖 UniClaw 斜杠命令帮助\n", config)

    await info("【会话管理】", config)
    await info("  /btw <问题>            - 侧问题:不打断当前对话提问", config)
    await info("  /name [名称]          - 为会话命名(无参数自动生成)", config)
    await info("  /clear, /cls          - 清空当前对话历史并清屏", config)
    await info("  /compact [关键词]      - 压缩上下文,优化 Token 使用", config)
    await info("  /export [路径]         - 导出当前会话到文件(Markdown/JSON)", config)
    await info("  /resume [ID]           - 恢复会话(无参数交互式选择)", config)
    await info("  /resume list           - 列出所有历史对话", config)
    await info("  /resume del <ID>       - 删除指定会话", config)
    await info("  /resume search <关键词> - 搜索对话内容", config)
    await info("", config)

    await info("【Git 检查点】", config)
    await info("  /undo                 - 撤销 AI 最近的文件编辑", config)
    await info("  /undo <序号>          - 恢复到指定检查点", config)
    await info("  /checkpoint           - 列出所有检查点", config)
    await info("  /checkpoint diff      - 查看当前未提交的变更", config)
    await info("  /checkpoint diff <序号> - 当前修改 vs 指定检查点", config)
    await info("  /checkpoint diff <a> <b> - 比较两个检查点", config)
    await info("  /checkpoint restore   - 恢复最近的检查点", config)
    await info("  /checkpoint <序号>    - 恢复指定检查点", config)
    await info("", config)

    await info("【监工模式】", config)
    await info("  /overseer start        - 启动监工模式(TodoList完成需审核)", config)
    await info("  /overseer stop         - 退出监工模式", config)
    await info("  /overseer              - 查看监工模式状态", config)
    await info("", config)

    await info("【模型与系统】", config)
    await info("  /model [名称]          - 查看或切换当前使用的模型", config)
    await info("  /config [get|set|reset] - 运行时配置管理", config)
    await info("  /cwd, /cd, /pwd [路径] - 查看或切换工作目录", config)
    await info(
        "  /add-dir <路径>        - 添加额外工作空间目录(仅当前会话有效)", config
    )
    await info("  /usage                 - 查看 Token 使用统计", config)
    await info(
        "  /cost                  - 查看费用统计(按模型计费,价格来自 OpenRouter)",
        config,
    )
    await info("  /context               - 查看当前上下文 token 构成", config)
    await info("  /skills                - 列出所有可用技能", config)
    await info("  /init                  - 扫描项目并生成/更新 CLAUDE.md", config)
    await info("  /doctor                - 环境诊断(检查依赖和配置状态)", config)
    await info("  /help                  - 显示本帮助信息", config)
    await info("  /exit, /quit           - 退出程序", config)
    await info("", config)

    await info("【记忆管理】", config)
    await info("  /memory                - 列出所有记忆", config)
    await info("  /memory <关键词>       - 搜索相关记忆", config)
    await info("  /memory consolidate    - 从当前对话提取记忆", config)
    await info("", config)

    await info("【MCP 管理】", config)
    await info("  /mcp list              - 列出 MCP 服务器", config)
    await info("  /mcp add <名称> [JSON] - 添加 MCP 服务器", config)
    await info("  /mcp remove <名称>     - 删除 MCP 服务器", config)
    await info("  /mcp show <名称>       - 查看服务器详情", config)
    await info("  /mcp edit <名称> [JSON] - 编辑服务器配置", config)
    await info("  /mcp enable/disable <名称> - 启用/禁用服务器", config)
    await info("  /mcp tools [名称]      - 列出 MCP 工具", config)
    await info("  /mcp refresh           - 刷新 MCP 工具", config)
    await info("", config)

    await info("【定时任务】", config)
    await info("  /schedule list         - 列出所有定时任务", config)
    await info("  /schedule add <id> <调度> <动作> - 创建定时任务", config)
    await info("  /schedule remove <id>  - 删除定时任务", config)
    await info("  /schedule enable <id>  - 启用定时任务", config)
    await info("  /schedule disable <id> - 禁用定时任务", config)
    await info("", config)

    await info("【后台任务】", config)
    await info("  /task                  - 列出所有后台任务", config)
    await info("  /task list             - 列出所有后台任务", config)
    await info("  /task output <id> [N]  - 获取任务输出(默认 50 行)", config)
    await info("  /task stop <id>        - 停止指定任务", config)
    await info("  /task matched <id>     - 获取监控匹配结果", config)
    await info("", config)

    await info("【权限管理】", config)
    await info("  /permissions list      - 查看所有权限规则", config)
    await info("  /permissions remove <类型> <模式> - 删除权限规则", config)
    await info("", config)

    await info("💡 提示:", config)
    await info("  - 输入 /<命令> help 可查看该命令的详细说明(如 /memory help)", config)
    await info("  - 输入 ! 开头的命令可直接执行 Shell 命令(如 !ls -la)", config)
    await info("  - 按 F2 键可切换详细/简洁显示模式", config)
    await info("  - 按 ESC 键可中断正在运行的任务", config)
    await info("  - 按 Ctrl+K 可聚焦对话侧边栏", config)
    await info("", config)

    return True
