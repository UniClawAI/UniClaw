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

    lines = [f"\n可用技能 (共 {len(skills)} 个):\n"]
    for source_key, (title, skill_list) in groups.items():
        if not skill_list:
            continue
        lines.append(title)
        for skill in skill_list:
            triggers = ", ".join(skill.triggers[:3])
            if len(skill.triggers) > 3:
                triggers += f" (+{len(skill.triggers) - 3})"
            lines.append(f"  - {skill.name}: {skill.description}")
            lines.append(f"    触发器: {triggers}")
            if skill.when_to_use:
                lines.append(f"    使用时机: {skill.when_to_use}")
            if skill.argument_hint:
                lines.append(f"    参数提示: {skill.argument_hint}")
            lines.append("")
    await info("\n".join(lines), config)
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
    help_text = """
📖 UniClaw 斜杠命令帮助

【会话管理】
  /btw <问题>            - 侧问题:不打断当前对话提问
  /name [名称]          - 为会话命名(无参数自动生成)
  /clear, /cls          - 清空当前对话历史并清屏
  /compact [关键词]      - 压缩上下文,优化 Token 使用
  /export [路径]         - 导出当前会话到文件(Markdown/JSON)
  /resume [ID]           - 恢复会话(无参数交互式选择)
  /resume list           - 列出所有历史对话
  /resume del <ID>       - 删除指定会话
  /resume search <关键词> - 搜索对话内容

【Git 检查点】
  /undo                 - 撤销 AI 最近的文件编辑
  /undo <序号>          - 恢复到指定检查点
  /checkpoint           - 列出所有检查点
  /checkpoint diff      - 查看当前未提交的变更
  /checkpoint diff <序号> - 当前修改 vs 指定检查点
  /checkpoint diff <a> <b> - 比较两个检查点
  /checkpoint restore   - 恢复最近的检查点
  /checkpoint <序号>    - 恢复指定检查点

【监工模式】
  /overseer start        - 启动监工模式(TodoList完成需审核)
  /overseer stop         - 退出监工模式
  /overseer              - 查看监工模式状态

【模型与系统】
  /model [名称]          - 查看或切换当前使用的模型
  /config [get|set|reset] - 运行时配置管理
  /cwd, /cd, /pwd [路径] - 查看或切换工作目录
  /add-dir <路径>        - 添加额外工作空间目录(仅当前会话有效)
  /usage                 - 查看 Token 使用统计
  /cost                  - 查看费用统计(按模型计费,价格来自 OpenRouter)
  /context               - 查看当前上下文 token 构成
  /skills                - 列出所有可用技能
  /init                  - 扫描项目并生成/更新 CLAUDE.md
  /doctor                - 环境诊断(检查依赖和配置状态)
  /help                  - 显示本帮助信息
  /exit, /quit           - 退出程序

【记忆管理】
  /memory                - 列出所有记忆
  /memory <关键词>       - 搜索相关记忆
  /memory consolidate    - 从当前对话提取记忆

【MCP 管理】
  /mcp list              - 列出 MCP 服务器
  /mcp add <名称> [JSON] - 添加 MCP 服务器
  /mcp remove <名称>     - 删除 MCP 服务器
  /mcp show <名称>       - 查看服务器详情
  /mcp edit <名称> [JSON] - 编辑服务器配置
  /mcp enable/disable <名称> - 启用/禁用服务器
  /mcp tools [名称]      - 列出 MCP 工具
  /mcp refresh           - 刷新 MCP 工具

【定时任务】
  /schedule list         - 列出所有定时任务
  /schedule add <id> <调度> <动作> - 创建定时任务
  /schedule remove <id>  - 删除定时任务
  /schedule enable <id>  - 启用定时任务
  /schedule disable <id> - 禁用定时任务

【后台任务】
  /task                  - 列出所有后台任务
  /task list             - 列出所有后台任务
  /task output <id> [N]  - 获取任务输出(默认 50 行)
  /task stop <id>        - 停止指定任务
  /task matched <id>     - 获取监控匹配结果

【权限管理】
  /permissions list      - 查看所有权限规则
  /permissions remove <类型> <模式> - 删除权限规则

【A2A】
  /a2a start [token]     - 启用 A2A 端点(仅 WebUI 模式)
  /a2a stop              - 禁用 A2A 端点
  /a2a                   - 查看 A2A 服务状态
  /a2a add <名称> <URL> [token] - 添加远程 A2A Agent
  /a2a list              - 列出已配置的远程 Agent
  /a2a remove <名称>     - 删除远程 Agent
  /a2a test <名称>       - 测试远程 Agent 连接

💡 提示:
  - 输入 /<命令> help 可查看该命令的详细说明(如 /memory help)
  - 输入 ! 开头的命令可直接执行 Shell 命令(如 !ls -la)
  - 按 F2 键可切换详细/简洁显示模式
  - 按 ESC 键可中断正在运行的任务
  - 按 Ctrl+K 可聚焦对话侧边栏
"""
    await info(help_text, config)
    return True
