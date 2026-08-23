"""中英文分词工具。"""

import re

import jieba

# camelCase/PascalCase 边界: 小写或数字后跟大写, 或大写序列末尾后跟小写
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _split_identifier(word: str) -> list[str]:
    """将 snake_case/camelCase 标识符拆为子词。
    """
    parts = []
    for seg in word.split("_"):
        if seg:
            parts.extend(p for p in _CAMEL_BOUNDARY.split(seg) if p)
    return parts


def tokenize(text: str) -> list[str]:
    """中英文分词:中文用 jieba,英文按单词。

    >>> tokenize("Python 代码风格")
    ['python', '代码', '风格']
    """
    lowered = text.lower()
    tokens = []
    for word in re.findall(r"[a-zA-Z_]+", text):
        tokens.append(word.lower())
        sub_parts = _split_identifier(word)
        if len(sub_parts) > 1:
            tokens.extend(p.lower() for p in sub_parts)
    cn_tokens = [w for w in jieba.cut(lowered) if re.match(r"[一-鿿]", w)]
    return tokens + cn_tokens
