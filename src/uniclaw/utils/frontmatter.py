import re
import yaml
from typing import Any, Dict, Tuple


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """
    解析包含 frontmatter 的文本内容

    Frontmatter 是位于文件开头的 YAML 格式元数据,被 --- 标记包围。
    常见于 Markdown 文件中,用于存储标题、日期、标签等元信息。

    Args:
        content (str): 包含 frontmatter 的完整文本内容
                      格式示例:
                      ```
                      ---
                      title: 文章标题
                      date: 2024-01-01
                      tags:
                        - python
                        - tutorial
                      ---
                      这里是正文内容...
                      ```

    Returns:
        Tuple[Dict[str, Any], str]: 返回一个元组,包含:
            - 第一个元素:解析后的 frontmatter 字典(如果没有 frontmatter 则为空字典)
            - 第二个元素:去除 frontmatter 后的正文内容

    Examples:
        >>> content = "---\\ntitle: Hello\\n---\\nBody text"
        >>> metadata, body = parse_frontmatter(content)
        >>> metadata
        {'title': 'Hello'}
        >>> body
        'Body text'
    """
    if not content or not content.strip():
        return {}, content

    # 定义 frontmatter 的正则表达式模式
    # 匹配以 --- 开头和结尾的 YAML 块
    pattern = r"^(.*?)---\s*\n(.*?)\n---\s*\n(.*)"
    match = re.match(pattern, content, re.DOTALL)

    if not match:
        # 如果没有找到 frontmatter,返回空字典和原文本
        return {}, content

    prefix = match.group(1)      # --- 之前的内容(可能为空)
    yaml_content = match.group(2)
    body_content = match.group(3)

    # 使用 PyYAML 安全地解析 YAML 内容
    try:
        metadata = yaml.safe_load(yaml_content)
        # safe_load 可能返回 None(当 YAML 为空时)
        if metadata is None:
            metadata = {}
    except yaml.YAMLError:
        # YAML 解析失败时,尝试修复常见问题后重新解析
        fixed = _fix_yaml(yaml_content)
        try:
            metadata = yaml.safe_load(fixed)
            if metadata is None:
                metadata = {}
        except yaml.YAMLError:
            metadata = {}

    # 将 --- 之前的内容拼回正文,避免丢失
    if prefix:
        body_content = prefix + "---\n" + body_content if body_content else prefix

    return metadata, body_content


def _fix_yaml(yaml_content: str) -> str:
    """修复常见的 YAML 语法错误,主要是未加引号的冒号值。

    例如: description: text with: colon -> description: "text with: colon"
    """
    fixed_lines = []
    for line in yaml_content.split("\n"):
        # 跳过空行和纯列表项
        stripped = line.strip()
        if not stripped or stripped.startswith("- "):
            fixed_lines.append(line)
            continue

        # 匹配 key: value 模式 (支持缩进)
        indent = len(line) - len(line.lstrip())
        match = re.match(r"^\s*([\w][\w-]*):\s+(.+)$", line)
        if match:
            key = match.group(1)
            value = match.group(2)
            # 检查值中是否包含未加引号的冒号
            # 排除已加引号的情况
            is_quoted = (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            )
            if not is_quoted and re.search(r":\s", value):
                # 值中包含冒号+空格,需要加引号
                # 转义值中的双引号
                escaped = value.replace('"', '\\"')
                fixed_lines.append(f'{" " * indent}{key}: "{escaped}"')
                continue

        fixed_lines.append(line)

    return "\n".join(fixed_lines)


def write_frontmatter(metadata: Dict[str, Any], body: str = "") -> str:
    """
    将元数据和正文内容组合成包含 frontmatter 的完整文本

    Args:
        metadata (Dict[str, Any]): 要写入的元数据字典
                                  支持字符串、数字、布尔值和简单列表类型
                                  示例:{'title': '文章标题', 'tags': ['python', 'tutorial']}
        body (str): 正文内容,默认为空字符串

    Returns:
        str: 包含 frontmatter 的完整文本内容
             格式为:
             ```
             ---
             key1: value1
             key2: value2
             ---
             正文内容
             ```

    Examples:
        >>> metadata = {'title': 'Hello', 'count': 42}
        >>> result = write_frontmatter(metadata, 'Body text')
        >>> print(result)
        ---
        title: Hello
        count: 42
        ---
        Body text
    """
    if not metadata:
        return body

    # 使用 PyYAML 将字典转换为 YAML 格式字符串
    yaml_content = yaml.dump(
        metadata,
        allow_unicode=True,  # 允许 Unicode 字符
        default_flow_style=False,  # 使用块样式而非流样式
        sort_keys=False,  # 保持键的顺序
    ).rstrip()  # 移除末尾的换行符

    # 组合完整的 frontmatter 格式
    if body:
        return f"---\n{yaml_content}\n---\n{body}"
    else:
        return f"---\n{yaml_content}\n---\n"
