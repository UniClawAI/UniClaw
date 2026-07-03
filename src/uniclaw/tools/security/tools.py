import json
from pathlib import Path
from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.config import AppConfig

# ── 系统提示词(静态常量,最大化 LLM 缓存命中) ──────────────

_SECURITY_SYSTEM_PROMPT = """\
# 安全策略管理
工具调用安全检测:当你调用非白名单工具时,系统会通过 LLM 分析该调用是否安全,决定自动执行或要求用户确认。\
检测时会读取一段用户自定义的安全策略注入提示词,你可以通过 {read}、{write}、{edit}、{clear} 工具管理它。
"""


def get_security_system_prompt() -> str:
    """返回安全策略管理的系统提示词(静态内容,适合放在缓存前缀区域)。"""
    return _SECURITY_SYSTEM_PROMPT.format(
        read=read_llm_safe_prompt.name,
        write=write_llm_safe_prompt.name,
        edit=edit_llm_safe_prompt.name,
        clear=clear_llm_safe_prompt.name,
    )


# ── LLM 安全策略提示词管理 ──────────────────────────────────


def _llm_safe_prompt_path(root_dir: Path) -> Path:
    from uniclaw.context import get_app_dir

    return get_app_dir(root_dir) / "llm_safe_prompt.json"


def _load_llm_safe_prompt(root_dir: Path | None) -> str:
    if root_dir is None:
        return ""
    path = _llm_safe_prompt_path(root_dir)
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("prompt", "").strip()
    except (json.JSONDecodeError, OSError):
        return ""


def _save_llm_safe_prompt(prompt: str, root_dir: Path):
    path = _llm_safe_prompt_path(root_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"prompt": prompt}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _clear_llm_safe_prompt(root_dir: Path):
    path = _llm_safe_prompt_path(root_dir)
    if path.exists():
        path.unlink()


@tool
def read_llm_safe_prompt(config: AppConfig = None) -> str:
    """读取当前安全策略注入提示词。

    读取存储的安全审核策略提示词。这个提示词会被自动注入到 LLM 的安全检测系统提示中,
    用于动态调整工具调用的安全审核规则。例如:可以在提示词中指定某些命令或工具的安全性,
    AI 会根据这个策略来判断是否需要用户确认。

    注意:config 参数由系统框架自动注入,请勿手动传入。

    Returns:
        str: 当前存储的安全策略提示词内容,如果未设置则返回提示信息
    """

    prompt = _load_llm_safe_prompt(config.root_dir)
    return prompt or "当前未设置 llm_safe_check 注入提示词。"


@tool
def write_llm_safe_prompt(prompt: str, config: AppConfig = None) -> str:
    """覆盖保存安全审核策略提示词。

    将新的安全策略提示词完整替换并保存。使用此工具时,旧的提示词会被完全覆盖。
    如果需要修改部分内容,建议使用 edit_llm_safe_prompt。

    注意:config 参数由系统框架自动注入,请勿手动传入。

    Args:
        prompt (str): 完整的安全策略注入提示词文本

    Returns:
        str: 保存成功提示
    """
    _save_llm_safe_prompt(prompt, config.root_dir)
    return "已保存 llm_safe_check 注入提示词。"


@tool
def edit_llm_safe_prompt(
    old_string: str, new_string: str, config: AppConfig = None
) -> str:
    """精确编辑安全审核策略提示词中的特定部分。

    使用替换法修改安全策略提示词。找到 old_string 并替换为 new_string,
    适合对现有策略进行增量修改。例如:修改某条规则、添加新的安全策略、或调整现有的审核标准。

    注意:config 参数由系统框架自动注入,请勿手动传入。

    与 write_llm_safe_prompt 的区别:
    - write: 完全覆盖整个提示词(破坏式操作)
    - edit: 只修改指定的部分(精确更新)

    Args:
        old_string (str): 要被替换的原始字符串。必须与提示词中的内容完全匹配,
                         包括空格和换行符。
        new_string (str): 用于替换的新字符串。

    Returns:
        str: 操作结果。成功时显示修改前后的预览；失败时返回错误信息。
    """
    try:
        current_prompt = _load_llm_safe_prompt(config.root_dir)

        # 验证旧字符串存在
        if old_string not in current_prompt:
            return f"{TOOL_ERROR}: 在提示词中未找到 old_string。请确保完全匹配。"

        # 检查是否存在多个匹配
        count = current_prompt.count(old_string)
        if count > 1:
            return (
                f"{TOOL_ERROR}: old_string 出现了 {count} 次。"
                "请提供更多上下文以使其唯一。"
            )

        # 执行替换
        new_prompt = current_prompt.replace(old_string, new_string, 1)

        # 保存并返回差异
        _save_llm_safe_prompt(new_prompt, config.root_dir)

        # 生成简单的差异报告
        old_preview = old_string[:100] + ("..." if len(old_string) > 100 else "")
        new_preview = new_string[:100] + ("..." if len(new_string) > 100 else "")
        return f"已编辑 llm_safe_check 注入提示词:\n- 删除:{old_preview}\n+ 添加:{new_preview}"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
def clear_llm_safe_prompt(config: AppConfig = None) -> str:
    """清除所有存储的安全审核策略提示词。

    删除保存的安全策略,恢复到默认的安全审核规则。此后 llm_safe_check 将不再使用
    自定义的安全策略,仅使用内置的默认规则。

    注意:config 参数由系统框架自动注入,请勿手动传入。

    Returns:
        str: 清除成功提示
    """
    _clear_llm_safe_prompt(config.root_dir)
    return "已清除 llm_safe_check 注入提示词。"


def get_tools() -> list:
    """获取安全管理工具列表"""
    return [
        read_llm_safe_prompt,
        write_llm_safe_prompt,
        edit_llm_safe_prompt,
        clear_llm_safe_prompt,
    ]


def get_all_tools() -> list:
    """获取所有安全管理工具"""
    return [
        read_llm_safe_prompt,
        write_llm_safe_prompt,
        edit_llm_safe_prompt,
        clear_llm_safe_prompt,
    ]
