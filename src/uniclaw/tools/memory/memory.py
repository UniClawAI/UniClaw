"""
记忆数据模型定义
"""

from __future__ import annotations
from datetime import datetime
from enum import StrEnum
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)
from uniclaw.context import Scope, get_app_dir, APP_NAME
from pathlib import Path
from uniclaw.utils import frontmatter
from uniclaw.utils.truncation import truncate_text_by_lines


class MemoryType(StrEnum):
    """记忆类型。"""

    user = "user"
    feedback = "feedback"
    project = "project"
    reference = "reference"


class MemorySource(StrEnum):
    """记忆来源。"""

    user = "user"
    model = "model"
    tool = "tool"


class Memory:
    INDEX_FILENAME = "memory.md"

    def __init__(
        self,
        name: str,
        description: str,
        content: str,
        scope: Scope | Path,
        type: MemoryType | str = MemoryType.user,
        source: MemorySource | str = MemorySource.user,
        confidence: float = 1,
        created: Optional[str] = None,
        last_used_at: Optional[str] = None,
    ):
        """
        初始化记忆对象,表示一条可持久化的知识片段。

        记忆以 Markdown 文件形式存储,包含元数据(frontmatter)和正文内容。
        文件名由 name 参数自动生成(转换为 slug 格式)。

        Args:
            name: 记忆的人类可读名称,将自动转换为文件系统安全的 slug 作为文件名
                  示例:"用户偏好-代码风格" -> "用户偏好-代码风格.md"
            description: 简短的单行描述,用于相关性检索时的快速判断
                         示例:"用户偏好使用中文注释"
            content: 记忆的正文文本内容,支持 Markdown 格式
                     示例:"在编写代码时,所有注释必须使用中文"
            scope: 记忆作用范围,决定存储位置
                   - "user": 存储在用户全局目录,跨项目共享
                   - "project": 存储在项目本地目录,仅限当前项目
            type: 记忆类型,决定其用途和优先级
                  - "user": 用户偏好、角色设定等个人信息
                  - "feedback": 关于工作流程的指导性反馈
                  - "project": 项目相关的决策、正在进行的工作
                  - "reference": 外部系统指针或参考资料链接
            source: 记忆来源,标识信息获取渠道
                    - "user": 用户明确陈述(默认值,可信度最高)
                    - "model": AI 模型推断得出
                    - "tool": 从工具输出中提取
            confidence: 可靠性评分,范围 0.0-1.0,默认 1.0
                        1.0 表示明确的用户陈述,0.5 表示模型推断的不确定信息
            created: 记忆创建时间,格式为 "YYYY-MM-DD HH:MM:SS",默认为当前时间
                     示例:"2024-01-15 14:30:00"
            last_used_at: 上次被检索使用的时间,格式为 "YYYY-MM-DD HH:MM:SS",用于评估记忆活跃度

        注意:
            - filename 属性会根据 name 和 scope 自动生成,无需手动指定
            - created 和 last_used_at 如果未提供,会自动设置为当前时间的字符串格式
            - name 会通过 _slugify 方法转换为合法的文件名(小写、下划线分隔、最多60字符)
        """
        self.name = name.strip()
        self.description = description
        self.content = content
        self.type = type
        self.source = source
        self.scope = scope  # Scope | Path,用于路径解析
        self.confidence = confidence

        if created is None:
            self.created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        else:
            self.created = created
        # 处理最后使用时间,转换为字符串格式
        if last_used_at is None:
            self.last_used_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        else:
            self.last_used_at = last_used_at

        self.filename = self.get_memory_path(scope, name)

    @property
    def scope_name(self) -> str:
        """显示用的 scope 字符串("user"/"project")。"""
        if isinstance(self.scope, Scope):
            return self.scope.value
        if isinstance(self.scope, Path):
            return "project"
        return self.scope  # 从磁盘加载的原始字符串

    @classmethod
    def get_memory_path(cls, scope: Scope | Path, name: str) -> Path:
        return cls.get_memory_dir(scope) / f"{cls._slugify(name)}.md"

    @classmethod
    def exists(cls, scope: Scope | Path, name: str) -> bool:
        return cls.get_memory_path(scope, name).exists()

    @staticmethod
    def _slugify(name: str) -> str:
        """将名称转换为文件系统安全的 slug(最多 60 个字符)。"""
        s = name.lower().strip().replace(" ", "_")
        s = re.sub(r"[^a-z0-9_一-鿿]", "", s)
        return s[:60]

    @staticmethod
    def get_memory_dir(scope: Scope | Path = Scope.USER) -> Path:
        """获取记忆目录路径。"""
        if isinstance(scope, Path):
            return scope.resolve() / f".{APP_NAME}" / "memory"
        return get_app_dir(scope) / "memory"

    @classmethod
    def get_index_content(cls, scope: Scope | Path = Scope.USER) -> str:
        index_path = cls.get_memory_dir(scope) / cls.INDEX_FILENAME
        if not index_path.exists():
            return ""
        return index_path.read_text(encoding="utf-8").strip()

    def save_memory(self, force: bool = False) -> dict:
        """
        将当前记忆对象持久化保存到文件系统。

        该方法执行以下操作:
        1. 将记忆对象转换为包含元数据和正文的 Markdown 文本格式
        2. 确保目标目录存在(如果不存在则递归创建)
        3. 将文本内容写入到对应的 .md 文件中
        4. 重建该作用域下的记忆索引文件,以保持索引与记忆文件的同步

        注意:
            - 文件路径由 self.filename 属性决定,该属性在初始化时根据 name 和 scope 自动生成
            - 保存后会自动调用 rebuild_index 更新索引,确保新记忆可被检索
            - 使用 UTF-8 编码写入文件,确保中文字符正确保存


        如果同名记忆已存在且内容不同,默认不会写入,返回冲突信息供调用方决策。
        如果 force=True,则用当前记忆替换旧记忆,并在返回值中包含旧记忆内容。
        如果内容完全相同,返回 identical 状态,不重复写入。

        Returns:
            dict: 包含 status 和 message 的状态字典
                - "created": 新记忆已保存
                - "identical": 同名同内容记忆已存在,未重复保存
                - "replaced": 同名旧记忆已被强制替换,包含 existing 字段
                - "conflict": 同名但不同内容的记忆已存在,包含 existing 字段
        """
        file_path = self.filename

        if file_path.exists():
            existing = Memory.load_memory(file_path)
            if (
                existing.content == self.content
                and existing.description == self.description
            ):
                return {
                    "status": "identical",
                    "message": f"记忆 '{self.name}' 已存在且内容相同,无需保存。",
                }
            existing_data = {
                "name": existing.name,
                "description": existing.description,
                "content": existing.content,
                "type": existing.type,
                "scope": existing.scope_name,
                "source": existing.source,
                "confidence": existing.confidence,
                "created": existing.created,
                "last_used_at": existing.last_used_at,
            }
            if force:
                text = self.to_text()
                with open(self.filename, "w", encoding="utf8") as f:
                    f.write(text)
                self.rebuild_index(self.scope)
                # 同步 FTS5 索引
                try:
                    from .fts import index_memory

                    index_memory(self)
                except Exception as e:
                    logger.debug("FTS 索引同步失败: %s", e)
                return {
                    "status": "replaced",
                    "message": f"记忆 '{self.name}' 已强制替换。",
                    "existing": existing_data,
                }
            return {
                "status": "conflict",
                "message": f"记忆 '{self.name}' 已存在但内容不同。",
                "existing": existing_data,
            }

        text = self.to_text()
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)
        with open(self.filename, "w", encoding="utf8") as f:
            f.write(text)
        self.rebuild_index(self.scope)
        # 同步 FTS5 索引
        try:
            from .fts import index_memory

            index_memory(self)
        except Exception as e:
            logger.debug("FTS 索引同步失败,不影响保存: %s", e)

        return {
            "status": "created",
            "message": f"记忆 保存成功: '{self.name}' [{self.type}/{self.scope_name}]",
        }

    @staticmethod
    def load_memory(filename: Path):
        text = filename.read_text(encoding="utf-8")
        metadata, content = frontmatter.parse_frontmatter(text)
        metadata["content"] = content
        # 从磁盘加载时 scope 是字符串,转为 Scope 或 Path
        scope = metadata.get("scope")
        if scope == "project":
            scope = filename.parent.parent.parent
            metadata["scope"] = scope
        return Memory(**metadata)

    def to_text(self):
        """
        将记忆对象转换为包含元数据和正文的 Markdown 文本格式。

        该方法将记忆的所有属性组织成元数据字典,然后使用 frontmatter 格式
        将元数据和正文内容组合成标准的 Markdown 文本,便于持久化存储。

        Returns:
            str: 包含 frontmatter 元数据和正文内容的完整 Markdown 文本字符串
                 格式示例:
                 ```
                 ---
                 name: 用户偏好-代码风格
                 description: 用户偏好使用中文注释
                 type: user
                 source: user
                 scope: user
                 confidence: 1.0
                 created: 2024-01-15 14:30:00
                 last_used_at: 2024-01-15 14:30:00
                 ---

                 在编写代码时,所有注释必须使用中文
                 ```

        注意:
            - 元数据采用 YAML frontmatter 格式,位于文档顶部,用 --- 分隔
            - 该方法是 save_memory 的核心步骤,用于序列化记忆对象
            - 返回的文本可以直接写入 .md 文件进行持久化存储
        """
        # 构建记忆元数据字典,包含所有需要持久化的属性
        metadata = {
            "name": self.name,
            "description": self.description,
            "type": str(self.type),
            "source": str(self.source),
            "scope": self.scope_name,
            "confidence": self.confidence,
            "created": self.created,
            "last_used_at": self.last_used_at,
        }
        text = frontmatter.write_frontmatter(metadata, self.content)
        return text

    @classmethod
    def rebuild_index(cls, scope: Scope | Path = Scope.USER) -> None:
        memories = cls.load_all_memories(scope=scope)
        lines = [
            f"[{memory.name}]({memory.filename.name}) - {memory.description}"
            for memory in memories
        ]
        text = "\n".join(lines) + ("\n" if lines else "")
        index_path = cls.get_memory_dir(scope) / cls.INDEX_FILENAME
        index_path.write_text(text, encoding="utf-8")

    @classmethod
    def load_all_memories(cls, scope: Scope | Path = Scope.USER) -> list[Memory]:
        if isinstance(scope, Path):
            scopes = [scope]
        elif scope == Scope.ALL:
            scopes = [Scope.USER]
            # ALL 模式下也需要项目目录,但这里无法获取 cwd
            # 调用方应分别传入 Scope.USER 和 Path
        else:
            scopes = [scope]
        memories: list[Memory] = []
        for s in scopes:
            memory_dir = cls.get_memory_dir(s)
            for fp in sorted(memory_dir.glob("*.md")):
                if fp.name == cls.INDEX_FILENAME:
                    continue
                try:
                    memory = cls.load_memory(fp)
                except Exception as e:
                    continue
                memories.append(memory)
        return memories

    def touch_last_used(self):
        """
        更新记忆的 last_used_at 属性,并保存到文件系统。

        该方法执行以下操作:
        1. 获取当前时间字符串
        2. 更新 last_used_at 属性
        3. 调用 save_memory 方法保存到文件系统
        4. 确保该作用域下的记忆索引文件被重建
        """
        self.last_used_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = self.to_text()
        with open(self.filename, "w", encoding="utf8") as f:
            f.write(text)

    @staticmethod
    def get_memory_index_preview(cwd: Path | None = None):
        parts: list[str] = []
        roots: list[Scope | Path] = [Scope.USER]
        if cwd:
            roots.append(cwd)
        for root in roots:
            content = Memory.get_index_content(root)
            content = truncate_text_by_lines(content)
            parts.append(content)
        body = "\n\n".join(parts)
        return body
