"""内置 Skill: 创建 PR"""

from uniclaw.tools.skill.loader import SkillDef, register_builtin

_PR_CREATE_PROMPT = r"""## 创建 Pull Request

分析当前分支的变更,自动生成 PR 标题和描述,创建 GitHub PR。

### 前置检查

1. 检查 gh CLI 是否可用:`gh auth status`
   - 如果不可用,提示用户安装 GitHub CLI 并运行 `gh auth login`
2. 检查当前是否在非 main/master 分支上:`git branch --show-current`
   - 如果在 main/master 上,提示用户先创建功能分支

### 步骤

1. **获取变更信息**
   - `git branch --show-current` — 当前分支名
   - `git diff main...HEAD --stat` — 变更统计
   - `git diff main...HEAD` — 完整 diff
   - `git log main...HEAD --oneline` — 提交历史
   - 如果 main 不存在,尝试 master、develop

2. **生成 PR 内容**
   - **标题**:遵循 Conventional Commits 格式,如 `feat(auth): 添加 OAuth2 登录支持`
   - **描述**包含以下部分:
     - ## 变更概述 — 简要说明做了什么
     - ## 变更动机 — 为什么需要这个变更
     - ## 影响范围 — 涉及哪些模块
     - ## 测试说明 — 如何验证变更

3. **向用户确认**
   展示生成的标题和描述,询问:
   - 确认创建
   - 修改标题/描述
   - 取消

4. **创建 PR**
   - `gh pr create --title "<title>" --body "<body>"`
   - 如果用户指定了参数,将其作为 PR 的补充说明融入 body
   - 输出 PR 链接

### 注意

- 如果当前分支已有 PR,提示用户并显示 PR 链接
- 如果有未提交的变更,提醒用户先提交
- 标题不超过 72 字符"""


def register():
    """注册 pr-create 技能"""
    register_builtin(
        SkillDef(
            name="pr-create",
            description="分析当前分支的 git 变更,自动生成 PR 标题和描述,通过 gh CLI 创建 GitHub PR。"
            "当用户要求创建 PR、提 PR、pull request 时使用。",
            triggers=["/pr-create", "/pr", "pr-create"],
            tools=["Bash"],
            prompt=_PR_CREATE_PROMPT,
            file_path=__file__,
            source="builtin",
            when_to_use="用户要求创建 PR、提 PR、pull request、merge request 时",
            argument_hint="[补充说明]",
        )
    )
