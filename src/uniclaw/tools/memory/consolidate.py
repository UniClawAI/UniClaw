import json
from pathlib import Path

from uniclaw.config import AppConfig
from uniclaw.provider.fallback import achat
from uniclaw.tools.memory.memory import Memory
from uniclaw.utils.message import MessageRole


def get_consolidate_system_prompt(root_dir: Path | None = None) -> str:
    existing_memories = Memory.get_memory_index_preview(root_dir).strip()
    if existing_memories:
        existing_memories = (
            "\n\n【已永久保存的记忆 - 以下内容绝对不要再次提取】\n"
            "这些记忆已经存储在磁盘上,会自动加载使用。提取与它们相同、相似或包含相同信息的记忆是错误行为。\n"
            "即使措辞不同,只要核心含义一致,就属于重复,必须跳过。\n"
            "已保存的记忆列表:\n" + existing_memories
        )

    CONSOLIDATE_SYSTEM_PROMPT = f"""\
    你是一个保守的长期记忆筛选器。你的任务不是总结对话,而是只保存未来多次对话中明显有用、且有证据支持的少量长期记忆。
    {existing_memories}

    核心原则:
    - 默认返回空列表；只有内容通过下面的保存门槛时才提取。
    - 不要推断、补全、泛化或幻想用户没有明确表达的需求。
    - 模型自己的建议、计划、分析、猜测、实现选择都不能当作记忆,除非用户明确确认或纠正。
    - 小事、一次性任务、临时偏好、当前这次修 bug 的中间过程,默认不保存。
    - 不要记录流水账或日记。记忆必须是对未来执行任务有直接帮助的经验教训,例如:"xx错误是因为某原因导致的,应该用yy方法解决",而不是"今天修了某个bug"。
    - 工具/命令执行失败是高优先级例外:只要已经知道失败原因、正确命令、正确环境或规避方法,就必须保存,避免下次重复出错。
    - 如果对话证明现有记忆明显错误、API 已更新或命令/路径已经失效,应提取一条替代旧记忆的更新内容,而不是继续保留错误指导。

    保存门槛:
    1. 信息来自用户明确表达,或来自用户确认过的客观结果。
    2. 未来很可能会反复影响助理行为,或能避免重复踩同一个坑。
    3. 离开当前这次任务后仍然有用；不是只对当前文件、当前一次调试有效。命令/工具失败的正确做法属于例外,可只绑定到当前项目。
    4. 【严格检查】上方"已永久保存的记忆"列表中不存在相同、相似或包含相同核心信息的内容。即使你打算用不同的措辞表达,只要本质相同就不要提取。

    可以提取的类型:
    1. **用户偏好 (user)**:稳定的沟通偏好、工作方式、技术取舍偏好。必须由用户直接说出或多次稳定体现。
    2. **工作流反馈 (feedback)**:用户明确纠正过的方法、确认有效的做法、明确的"不要做X/以后要做Y"。
    3. **项目信息 (project)**:长期有效的项目约定、架构事实、验证路径、环境限制、重要决策。
    4. **工具/命令失败经验 (feedback)**:失败命令或工具、报错原因、适用环境、下次应使用的正确命令/流程。

    不要提取:
    - 【最重要】任何与上方"已永久保存的记忆"列表中内容相同、相似或本质一致的信息,即使措辞完全不同。
    - 流水账、日记式记录(如"今天修了某个bug"、"处理了xx问题")。如果要记录,必须是:错误原因 + 解决方法,且对未来任务有帮助。
    - 用户只是本次要求你做的一件事,例如"修改某个提示词"、"运行某个测试"。
    - 临时调试日志、报错片段、一次性的代码修改、一次性的命令输出；但如果报错已经定位出可复用的原因和正确做法,必须保存为 feedback。
    - 无关紧要的 UI 文案、变量名、文件名、实现细节,除非用户强调它是长期约定。
    - 已经在代码、README、AGENTS.md、CLAUDE.md 或现有记忆中记录的信息。
    - 助理自己提出但用户没有确认的需求、偏好、原因、后续计划。
    - 通用编程知识或任何项目外也普遍成立的常识。

    输出要求:
    - 最多提取 3 条；不确定时少提取,优先返回 {{"memories": []}}。
    - 如果同一段对话里既有普通偏好又有命令/工具失败经验,优先保存失败经验。
    - 如果是修正旧记忆,尽量复用旧记忆的 name,让保存层可以识别冲突并替换/升级。
    - 每条记忆必须能在对话中找到直接证据；如果只能靠推测,丢弃。
    - 内容要具体、可执行,避免"用户重视质量"这类空泛描述。

    返回一个 JSON 对象,包含键 "memories",其值为对象列表,每个对象包含:
    - name: 简短标识符,例如 "user_prefers_concise_responses"
    - type: "user" | "feedback" | "project"
    - description: 单行描述(用于搜索相关性)
    - content: 记忆主体;对于 feedback/project 类型,以规则/事实开头,然后添加 **Evidence:** 和 **How to apply:** 行。命令/工具失败经验还要写出 **Failed:** 和 **Use instead:**。
    - confidence: 浮点数 0.0-1.0;用户明确陈述或确认用 0.9,重复稳定体现用 0.8,不允许低于 0.8 的推测性记忆
    - replace_existing: 可选布尔值;只有当这条内容是在修正同名旧记忆的明显错误或失效内容时才设为 true

    如果没有新的或值得保存的内容,返回 {{"memories": []}}。
    直接输出 JSON,不要用 markdown 代码块包裹。"""
    return CONSOLIDATE_SYSTEM_PROMPT


async def consolidate_session(session, config: AppConfig) -> list[Memory]:
    """分析会话消息,提取值得长期保留的记忆并保存。

    使用 LLM 分析对话内容,识别用户偏好、工作流反馈、
    项目信息等值得记忆的内容,最多提取 3 条,自动保存到磁盘。

    Args:
        session: Session 对象或消息列表(支持 session 切片)
        config: 配置字典,需包含 model_name 或 mini_model_name

    Returns:
        Memory 对象列表(已保存)
    """
    if not session:
        return []

    if isinstance(session, list):
        session_text = "\n".join([m.to_str() for m in session if m.to_str()])
    else:
        session_text = session.to_str(include_tools=True)
    if not session_text.strip():
        return []

    root_dir = config.root_dir
    system_prompt = get_consolidate_system_prompt(root_dir)

    from uniclaw.tools.session.session import Session

    _session = Session()
    _session.add_user_message(
        content=f"请分析以下对话并提取值得长期保存的记忆:\n\n{session_text}"
    )

    resp = await achat(
        system_prompt,
        _session,
        model_name=config.mini_model_name,
        enable_thinking=False,
        thinking=False,
        response_format={"type": "json_object"},
        config=config,
    )

    raw = resp.content.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    items = data.get("memories", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []

    memories = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "").strip()
        description = item.get("description", "").strip()
        content = item.get("content", "").strip()
        mem_type = item.get("type", "user").strip()
        confidence = item.get("confidence", 0.8)
        replace_existing = bool(item.get("replace_existing", False))

        if not name or not content:
            continue
        if mem_type not in ("user", "feedback", "project"):
            mem_type = "user"

        mem = Memory(
            name=name,
            description=description,
            content=content,
            scope=root_dir,
            type=mem_type,
            source="model",
            confidence=float(confidence),
        )

        result = mem.save_memory(force=replace_existing)
        if result["status"] == "identical":
            continue
        if result["status"] == "replaced":
            memories.append(mem)
            continue
        if result["status"] == "conflict":
            old_content = result["existing"]["content"].strip()
            if old_content == content.strip():
                continue
            counter = 2
            base_name = name
            while Memory.exists(mem.scope, f"{base_name}_v{counter}"):
                counter += 1
            mem.name = f"{base_name}_v{counter}"
            mem.filename = Memory.get_memory_path(mem.scope, mem.name)
            mem.save_memory()

        memories.append(mem)

    return memories
