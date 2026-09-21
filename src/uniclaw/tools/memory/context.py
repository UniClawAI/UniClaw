import math
from pathlib import Path
import time
from uniclaw.config import AppConfig
from uniclaw.context import Scope
from uniclaw.utils.format import parse_json_from_llm
from uniclaw.utils.jev import is_available, select_many, JevAPIError, JevConfigError
from .memory import Memory
from uniclaw.utils.truncation import truncate_text_by_lines
from uniclaw.utils.message import MessageRole


def _get_tool_names() -> dict:
    """延迟获取工具名称,避免循环导入"""
    from .tools import memory_save, memory_delete

    return {
        "save": memory_save.name,
        "delete": memory_delete.name,
    }


# 记忆格式示例(frontmatter)
MEMORY_FORMAT_EXAMPLE = """\
```markdown
---
name: short-kebab-case-id
description: 单行摘要——用于相关性判断
type: user | feedback | project | reference
source: user | model | tool
scope: user | project
confidence: 0.0-1.0
---

正文。feedback/project 类型需包含 **Evidence:** 和 **How to apply:** :
命令失败还需 **Failed:** 和 **Use instead:** 。\
```"""
MEMORY_SYSTEM_PROMPT = """\
## 记忆系统

你有持久化记忆,存储为带 YAML frontmatter 的 markdown 文件。只保存无法从代码库直接派生的内容。

**字段说明**:
- `name`:文件名安全的短标识。`description`:单行摘要,用于相关性判断。
- `content`:可直接指导未来行为的正文。
- `type`:
  - `user`(用户角色/偏好) | `feedback`(纠正/失败经验) | `project`(项目约定/决策) | `reference`(外部系统指针)
- `source`: `user`/`model`/`tool`,不要伪装来源。`scope`: `user`/`project`,项目专属优先用 `project`。
- `confidence`:0.0-1.0;明确事实接近 1.0,推断应降低或不保存。

**何时保存**:
- 用户纠正你、确认方法,或分享应超越本次对话的上下文。
- 工具/命令出错且已知原因和正确做法时,必须保存为 `feedback`,正文包含:
  `**Failed:**`(原命令+报错) → `**Why:**`(原因) → `**Use instead:**`(正确做法) → `**How to apply:**`(适用条件)
- 旧记忆过时或错误时,用 `{save}(..., force=True)` 覆盖或 `{delete}` 删除。
- feedback/project 正文以规则开头,附 **Evidence:** 和 **How to apply:** 。

**格式**: {format_example}

**操作**:必须用 {save}/{delete} 工具操作,直接改文件会导致索引错误。修正同名记忆传 `force=True`。

**不应该保存的内容**:
- 已在代码、README、AGENTS.md、CLAUDE.md、git 历史或现有记忆中记录的内容
- 流水账、日记式记录(如"今天修了某个bug")；如果要记录,必须包含:问题原因 + 解决方法 + 对未来任务的帮助
- 只有当前任务有用的中间状态、临时日志、未定位原因的报错片段
- 助理自己推测出的用户需求、偏好或项目事实,除非用户明确确认
- 通用编程知识或任何项目外也普遍成立的常识
- 普通调试修复默认不保存；但命令/工具失败一旦形成可复用规则,必须保存为 feedback

**在从记忆中推荐之前**:记忆可能已过时,在采取行动之前验证它仍然存在。对于当前状态,优先使用 `git log` 或阅读代码。
"""


async def ai_select_memories(
    query: str, memories: list, max_results: int, config: AppConfig
):
    """AI 语义搜索记忆。Jev 可用时优先使用,否则回退 LLM。"""
    if is_available():
        try:
            return await _select_memories_via_jev(query, memories, max_results)
        except (JevAPIError, JevConfigError) as e:
            from uniclaw.console.ui import err
            err(f"Jev 记忆搜索失败,回退 LLM: {e}")
        except Exception as e:
            from uniclaw.console.ui import err
            err(f"Jev 记忆搜索异常,回退 LLM: {e}")
    return await _select_memories_via_llm(query, memories, max_results, config)


def _memory_summary(memory) -> str:
    """构建记忆摘要,供 Jev 判断相关性。"""
    return f"[{memory.type}] {memory.name}: {memory.description}"


async def _select_memories_via_jev(
    query: str, memories: list, max_results: int
) -> list[dict]:
    """通过 Jev select_many 选择相关记忆。"""
    by_name = {memory.name: memory for memory in memories}
    options = {memory.name: _memory_summary(memory) for memory in memories}
    results = await select_many(
        state=query,
        instruction="这条记忆是否与查询相关。关注记忆的名称和描述。",
        options=options,
    )
    # noul > 0.5 视为相关,按 noul 降序取 top max_results
    matched = [
        (name, noul_result.noul)
        for name, noul_result in results.items()
        if noul_result.noul > 0.5 and name in by_name
    ]
    matched.sort(key=lambda x: x[1], reverse=True)
    return [_build_memory_result(by_name[name]) for name, _ in matched[:max_results]]


async def _select_memories_via_llm(
    query: str, memories: list, max_results: int, config: AppConfig
) -> list[dict]:
    """通过 LLM 选择相关记忆(原有方案)。"""
    text_lines = []
    for i, memory in enumerate(memories):
        text_line = f"{i}:[{memory.type}] {memory.name} {memory.description}"
        text_lines.append(text_line)
    text = "\n".join(text_lines)

    system = (
        "你负责选择与查询相关的记忆。"
        "返回一个 JSON 对象,包含键 'indices',其值为整数索引列表(从0开始),"
        f"来自提供的列表。最多选择 {max_results} 个条目。"
        '仅包含与查询明确相关的索引。如果没有相关项,返回 {"indices": []}。'
        "重要:直接输出原始 JSON 字符串,不要使用 Markdown 代码块(如 ```json)包裹,不要添加任何额外文本。"
    )
    from uniclaw.provider.fallback import achat
    from uniclaw.tools.session.session import Session

    _session = Session()
    _session.add_user_message(content=f"查询:{query}\n\n记忆:\n{text}")

    wait_id = config.spinner.start("搜索相关记忆...")
    try:
        ai_message = await achat(
            system,
            _session,
            model_name=config.mini_model_name,
            enable_thinking=False,
            thinking=False,
            response_format={"type": "json_object"},
            config=config,
        )
    finally:
        config.spinner.stop(wait_id=wait_id)
    parsed = parse_json_from_llm(ai_message.content)
    if not parsed or "indices" not in parsed:
        return []
    indices = [int(i) for i in parsed["indices"]]
    indices = indices[:max_results]
    return [
        _build_memory_result(memories[i])
        for i in indices
        if 0 <= i < len(memories)
    ]


def _build_memory_result(memory) -> dict:
    """从 Memory 对象构建结果 dict。"""
    mtime_s = Path(memory.filename).stat().st_mtime
    return {
        "name": memory.name,
        "description": memory.description,
        "type": memory.type,
        "scope": memory.scope_name,
        "content": memory.content,
        "filename": memory.filename,
        "mtime_s": mtime_s,
        "freshness_text": memory_freshness_text(mtime_s),
        "confidence": memory.confidence,
        "source": memory.source,
        "memory": memory,
    }


def memory_freshness_text(mtime_s: float) -> str:
    """对于超过 1 天的记忆的陈旧警告(如果是新的则为空字符串)。

    由用户报告的陈旧代码状态记忆(引用已更改的代码的文件:行号)
    被断言为事实的问题驱动。
    """
    d = memory_age_days(mtime_s)
    if d <= 1:
        return ""
    return (
        f"这条记忆已有 {d} 天。 "
        "记忆是特定时间点的观察记录,而非实时状态 — "
        "关于代码行为或文件:行号引用的声明可能已过时。 "
        "在断言为事实之前,请对照当前代码进行验证。"
    )


def memory_age_days(mtime_s: float) -> int:
    """自 mtime_s 以来的天数(向下取整,对未来时间限制为 0)。"""
    return max(0, math.floor((time.time() - mtime_s) / 86_400))


def get_memory_system_prompt(root_dir: Path | None = None) -> str:
    """获取内存系统提示。"""
    body = Memory.get_memory_index_preview(root_dir)
    tool_names = _get_tool_names()
    prompt = MEMORY_SYSTEM_PROMPT.format(
        save=tool_names["save"],
        delete=tool_names["delete"],
        format_example=MEMORY_FORMAT_EXAMPLE,
    )
    return f"{prompt}\n\n## MEMORY.md\n{body}"
