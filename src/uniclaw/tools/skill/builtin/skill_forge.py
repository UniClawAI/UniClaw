"""内置 Skill: 技能锻造"""

from uniclaw.tools.skill.loader import SkillDef, register_builtin
from uniclaw.context import APP_NAME

_SKILL_FORGE_PROMPT = f"""## 技能锻造

创建新 Skill 或优化已有的自定义 Skill。你的任务是帮用户把可复用的工作流变成高质量的 Skill,或者根据使用反馈改进已有的自定义 Skill。

### 操作边界

⚠️ **只操作自定义 Skill**: 仅允许创建新 Skill 和修改 `source: skill-forge` 的 Skill。

**禁止修改**: builtin 来源 / 用户手动安装 / 项目自带的 Skill。
**允许操作**: `~/.{APP_NAME}/skills/` 目录下,且包含 `source: skill-forge` 标记的 Skill。

---

### 前置检查

1. 确保 skills 目录存在: `~/.{APP_NAME}/skills/`
   - 如果不存在,使用 `Bash` 创建: `mkdir -p ~/.{APP_NAME}/skills`
2. 如果用户指定了具体 Skill 名称(如 `/skill-forge api-gen`),直接进入创建流程的"捕获意图"步骤

---

### 参数解析

根据用户参数决定执行模式:

- **空 / `all`**: 执行全部任务(先创建,再优化)
- **`创建` / `create`**: 只创建新 Skill
- **`优化` / `optimize`**: 只优化已有的自定义 Skill
- **`<skill 名称>`**: 针对指定主题直接创建 Skill

---

## 任务 1: 创建 Skill

创建过程: 捕获意图 → 访谈调研 → 写 SKILL.md → 质量检查 → 保存

### Step 1: 捕获意图

如果当前会话里已经有一个用户想变成 Skill 的工作流(比如用户说"把这个变成 skill"),先从会话历史中提取: 用了哪些工具、步骤顺序、用户做了哪些修正、输入输出格式。然后让用户确认,补齐缺失的部分。

如果用户只是给了一个名字或主题,主动问清楚这 4 个问题:

1. **做什么?** — Skill 的核心任务
2. **什么时候触发?** — 用户说什么话 / 什么场景下自动使用
3. **输出格式是什么?** — 期望的结果长什么样
4. **需要测试用例吗?** — 有明确可验证输出的 Skill(文件转换、数据提取、代码生成)适合加测试用例;纯主观输出(写作风格、设计审美)的不需要。根据 Skill 类型给用户建议,但让用户决定。

如果需求模糊,向用户确认后再继续。不要猜。

### Step 2: 访谈调研

在写 Skill 之前,主动追问:

- **边界情况**: 输入不合法怎么办? 文件不存在怎么办? 格式不对怎么办?
- **依赖**: 需要什么工具、库、MCP 服务?
- **成功标准**: 怎么判断 Skill 执行成功了?

同时检查是否有相关的 MCP 工具可以用。如果有,调研一下文档,带着上下文来减少用户负担。

### Step 3: 写 SKILL.md

根据用户访谈结果,生成 Skill 文件。

#### Skill 目录解剖

```
skill-name/
├── SKILL.md (必需)
│   ├── YAML frontmatter (name, description 必需)
│   └── Markdown 指令正文
└── 附属资源 (可选)
    ├── scripts/    - 可执行脚本
    ├── references/ - 按需加载的参考文档
    └── assets/     - 输出用的模板、图标等
```

#### Frontmatter

- **name**: Skill 标识符,kebab-case,简短唯一
- **description**: **主要触发机制** — 做什么 + 何时使用。这是决定 Claude 是否调用此 Skill 的关键字段。注意: Claude 有"触发不足"的倾向,所以 description 要写得稍微"pushy"一些。不要只写"生成 API 客户端",要写"生成 API 客户端代码。当用户提到 API 调用、接口封装、SDK 生成、API client 时使用,即使用户没有明确要求使用 skill"
- **triggers**: 触发词列表,至少 1 个命令 + 1 个自然语言
- **tools**: 只列出真正需要的工具,不多不少
- **when_to_use**: 详细使用场景,补充 description
- **argument_hint**: 参数提示
- **source**: 必须是 `skill-forge`

#### 渐进式披露

Skill 使用三级加载:
1. **Metadata** (name + description) — 始终在上下文中(~100 词)
2. **SKILL.md 正文** — Skill 触发时加载(理想 <500 行)
3. **附属资源** — 按需加载(脚本可执行而不加载内容)

SKILL.md 正文控制在 500 行以内。如果接近这个限制,拆到 references/ 目录,在正文中加指针说明什么时候去读。

#### 正文写作模式

**定义输出格式** — 用模板:
在正文中用代码块展示期望的输出结构,标注哪些部分是固定的、哪些是动态的。

**示例模式** — 给输入输出对:
当输出质量依赖于看到示例时,提供 input/output 对:
- Input: 用户的原始请求
- Output: 期望的 Skill 输出

#### 正文写作风格

- **用祈使句**: "读取文件" 而不是 "你应该读取文件"
- **解释为什么,而不是堆砌 MUST**: 今天的 LLM 很聪明,有心理理论能力。给它一个好的框架,它能超越死板指令真正把事情做好。如果你发现自己在写全大写的 ALWAYS 或 NEVER,或者用非常僵硬的结构,这是黄色警告 — 尽量重新组织,解释推理过程,让模型理解为什么你要求的东西很重要。这是更人性化、更强大、更有效的方式。
- **从特例中泛化**: 不要把 Skill 写得只适用于你见过的那个例子。理解背后的原因,写通用指令。

### Step 4: 质量检查

保存前,用这个清单验证:

- [ ] name 是 kebab-case 且唯一
- [ ] description 包含"做什么 + 何时使用",且写得足够"pushy"
- [ ] triggers 至少有 1 个命令 + 1 个自然语言
- [ ] tools 只列出真正需要的
- [ ] 步骤用祈使句,每步解释为什么
- [ ] 有具体的输出格式(模板或示例)
- [ ] 有边界与约束(异常处理、兜底策略)
- [ ] source 是 `skill-forge`
- [ ] 正文不超过 500 行

### Step 5: 保存

- 路径: `~/.{APP_NAME}/skills/<skill-name>/skill.md`
- 使用 `Write` 工具保存
- **必须** 在 frontmatter 中包含 `source: skill-forge`

### 创建报告格式

```
## 🛠️ Skill 创建报告

### 意图捕获
- 做什么: ...
- 何时触发: ...
- 输出格式: ...
- 测试用例: 有/无

### 创建的 Skill

#### <skill-name>
- 文件: ~/.{APP_NAME}/skills/<skill-name>/skill.md
- 触发词: /command, 自然语言
- 功能: ...
- 使用场景: ...

### 质量检查: 全部通过 / 有 N 项未通过
```

---

## 任务 2: 优化 Skill

优化过程: 加载 → 诊断 → 分析反馈 → 改进 → 验证

### Step 1: 加载自定义 Skill

- 使用 `Glob` 查找 `~/.{APP_NAME}/skills/*/skill.md`
- 使用 `Read` 读取每个文件
- 只处理 `source: skill-forge` 的 Skill,跳过其他来源

### Step 2: 诊断

对每个 Skill,从这些维度检查:

- **触发精准度**: description + triggers 能否准确命中用户意图? 会不会误触发?
- **指令完整度**: prompt 是否覆盖所有步骤? AI 执行时会不会卡住?
- **边界清晰度**: 异常情况有没有处理? 有没有兜底策略?
- **输出一致性**: 每次输出格式稳不稳定?
- **精简度**: 有没有废话? 每一句是否都在拉权重?

### Step 3: 分析使用反馈

从最近会话中寻找 Skill 使用痕迹:
- 使用 `session_list` 获取最近会话
- 使用 `session_detail` 分析内容
- 寻找: 调用记录和频率 / 执行失败或用户手动修正 / 用户的改进建议

### Step 4: 改进

根据诊断和反馈,改进 Skill。改进时记住这 4 条原则:

1. **从反馈中泛化** — 你和用户在这里只迭代了几个例子,但这个 Skill 未来会被用无数次。如果用户反馈了某个问题,不要只针对那个特例修修补补,要理解背后的根本原因,写通用的解决方案。不要堆砌过拟合的 MUST,或者过度限制性的规则。

2. **保持精简** — 移除不起作用的内容。读执行记录,不只看最终输出 — 如果 Skill 让模型浪费时间做无用功,去掉导致那些行为的部分。

3. **解释为什么** — 尽力解释你要求模型做每件事的**原因**。LLM 有很强的心理理论能力,给它一个好的推理框架,它能超越死板指令。如果你发现自己在写全大写的 ALWAYS 或 NEVER,这是黄色警告 — 尽量重新组织,解释推理过程。

4. **查找重复工作** — 读执行记录,如果模型在每次运行中都独立写了类似的脚本或采取了相同的多步流程,这是强信号: 应该把这个脚本打包到 Skill 的 scripts/ 目录中,告诉 Skill 直接用它。写一次,省每次。

改进时:
- 使用 `Read` 读取现有 Skill 文件
- 修改 prompt 内容、触发词、工具列表
- 使用 `Write` 保存更新后的文件
- **保留原有 name 字段**
- **保留 `source: skill-forge` 标记**

### Step 5: 验证

优化后重新过一遍质量检查清单,确保问题已修复。

### 优化报告格式

```
## 🔧 Skill 优化报告

### 诊断

#### <skill-name>
- 触发精准度: N/5
- 指令完整度: N/5
- 边界清晰度: N/5
- 输出一致性: N/5
- 精简度: N/5

### 发现的问题

1. [问题类型] 问题描述
   - 优化方案: ...

### 执行的优化

#### <skill-name>
- 修改内容: ...
- 修改原因: ...
- 原则: 从反馈泛化 / 保持精简 / 解释为什么 / 查找重复工作

### 统计: 分析 N 个, 优化 N 个, 跳过 N 个
```

---

## 综合执行策略

执行全部任务时:

1. **先创建** — 基于记忆和会话中的模式创建新 Skill(创建前检查是否已存在相似 Skill)
2. **再优化** — 根据使用反馈改进已有的自定义 Skill(包括从创建任务转过来的)

### Skill 模板

```markdown
---
name: <skill-name>
description: <做什么>。<何时使用,积极主动,稍微 pushy>
triggers:
  - /<command>
  - <自然语言触发词>
tools:
  - <Tool1>
  - <Tool2>
when_to_use: <详细使用场景>
argument_hint: [<参数提示>]
source: skill-forge
---

## <Skill 标题>

<一句话说清楚做什么>

### 步骤

1. <动词> <对象> — <怎么做> — <为什么这样做>
2. ...

### 输出格式

<用模板或示例展示期望的输出>

### 边界与约束

- <什么情况下不做>
- <什么情况下终止>
- <兜底策略>
```"""


def register():
    """注册 skill-forge 技能"""
    register_builtin(
        SkillDef(
            name="skill-forge",
            description="技能锻造: 创建新 Skill 和优化已有的自定义 Skill。"
            "只操作由本技能创建的 Skill,不修改内置或安装的 Skill。"
            "当用户要求创建 skill、生成 skill、优化 skill、改进 skill、技能锻造时使用。",
            triggers=["/skill-forge", "/forge-skill", "skill-forge", "技能锻造"],
            tools=["Bash", "Read", "Write", "Glob", "memory_list", "memory_search", "session_list", "session_detail"],
            prompt=_SKILL_FORGE_PROMPT,
            file_path=__file__,
            source="builtin",
            when_to_use="用户要求创建 skill、生成 skill、优化 skill、改进 skill、技能锻造时",
            argument_hint="[all/创建/优化/创建 <主题>/优化 <名称>]",
        )
    )
