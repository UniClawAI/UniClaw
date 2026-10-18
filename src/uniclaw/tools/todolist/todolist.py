from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from uniclaw.utils.constants import TOOL_ERROR
from .overseer import OverseerManager


class TodoStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class TodoItem:
    id: int
    content: str
    status: TodoStatus = TodoStatus.PENDING


class TodoList:
    """任务清单,每个 AgentTask 实例持有独立的 TodoList。"""

    def __init__(self):
        self.items: list[TodoItem] = []
        self.overseer = OverseerManager()

    def is_empty(self) -> bool:
        return len(self.items) == 0

    def add(self, content: str) -> TodoItem:
        item = TodoItem(id=len(self.items), content=content)
        self.items.append(item)
        return item

    def index_error(self, index: int) -> str:
        """步骤索引越界时的错误提示,合法索引返回空串。

        update_status 与监工模式路径共用同一文案,避免两条路径提示漂移。
        """
        if 0 <= index < len(self.items):
            return ""
        count = len(self.items)
        hint = f",有效范围: 0~{count - 1}" if count > 0 else ",列表为空"
        return f"{TOOL_ERROR}: 索引 {index} 超出范围,当前共 {count} 项{hint}"

    def update_status(self, index: int, status: TodoStatus | str) -> str:
        err = self.index_error(index)
        if err:
            return err

        status = TodoStatus(status)
        self.items[index].status = status

        # 同一时间只允许一个 in_progress
        if status == TodoStatus.IN_PROGRESS:
            for i, item in enumerate(self.items):
                if i != index and item.status == TodoStatus.IN_PROGRESS:
                    item.status = TodoStatus.PENDING

        return self.get_list()

    def clear(self):
        self.items.clear()

    def get_list(self) -> str:
        """返回 todolist 条目列表,用于工具返回值和 TUI 显示"""
        if not self.items:
            return ""
        lines = []
        for item in self.items:
            if item.status == TodoStatus.IN_PROGRESS:
                marker = "[*]"
            elif item.status == TodoStatus.COMPLETED:
                marker = "[✓]"
            else:
                marker = "[ ]"
            lines.append(f"- {marker} {item.content} ({item.status})")
        return "\n".join(lines)

    def get_brief(self) -> str:
        """返回简短进度,如 '2/5'"""
        total = len(self.items)
        done = sum(1 for i in self.items if i.status == TodoStatus.COMPLETED)
        return f"{done}/{total}"

    def get_incomplete(self) -> list[str]:
        """返回未完成的任务列表,每项包含任务内容和状态"""
        return [
            f"{item.content} ({item.status})"
            for item in self.items
            if item.status != TodoStatus.COMPLETED
        ]
