"""内置 Skill: Git 提交"""

from uniclaw.tools.skill.loader import SkillDef, register_builtin

_COMMIT_PROMPT = r"""## AI Commit

分析当前变更,自动生成规范的 commit message 并提交。

### 步骤

1. **获取变更内容**
   - 运行 `git diff --cached` 查看已暂存的变更
   - 如果没有暂存内容,运行 `git diff` 查看未暂存的变更
   - 如果都没有变更,告知用户"没有可提交的变更"并结束

2. **生成 Commit Message**
   - 格式遵循 Conventional Commits:`<type>(<scope>): <description>`
   - type 选项:
     - `feat` — 新功能
     - `fix` — Bug 修复
     - `refactor` — 重构(不改变行为)
     - `docs` — 文档变更
     - `style` — 代码格式(不影响逻辑)
     - `test` — 测试相关
     - `chore` — 构建/工具/依赖变更
     - `perf` — 性能优化
     - `ci` — CI/CD 相关
   - scope:变更影响的模块或文件(可选)
   - description:简短描述,不超过 50 字符,使用中文
   - body:如有必要,补充变更动机和上下文(可选)
   - footer:如有 breaking change,标注 `BREAKING CHANGE: <描述>`

3. **向用户确认**
   展示以下内容并询问确认:
   - 变更文件列表(`git status --short`)
   - 生成的 commit message
   - 选项:确认提交 / 修改 message / 取消

4. **执行提交**
   - 如果有未暂存的变更:`git add -A`
   - `git commit -m "<message>"`
   - 输出提交结果

### 用户自定义参数

如果用户提供了参数,将其作为 commit message 的参考或补充说明:
- 如果参数看起来像完整的 commit message,直接使用
- 如果参数是简短描述,将其融入生成的 message 中
- 如果参数为空,完全由 AI 生成"""


def register():
    """注册 commit 技能"""
    register_builtin(
        SkillDef(
            name="commit",
            description="分析当前 git 变更,自动生成 Conventional Commits 格式的 commit message 并提交。"
            "当用户要求提交代码、commit、git commit 时使用。",
            triggers=["/commit", "commit"],
            tools=["Bash"],
            prompt=_COMMIT_PROMPT,
            file_path=__file__,
            source="builtin",
            when_to_use="用户要求提交代码、commit、git commit、提价代码时",
            argument_hint="[commit message 或描述]",
        )
    )
