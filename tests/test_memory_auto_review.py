import asyncio
from pathlib import Path
from types import SimpleNamespace

from uniclaw.agent import AgentTask
from uniclaw.spinner import NoopSpinner
from uniclaw.tools.memory import auto_review
from uniclaw.tools.memory.memory import Memory
from uniclaw.tools.session.session import Session
from uniclaw.utils.message import MessageRole


class _Response:
    def __init__(self, content: str):
        self.content = content


def _make_config(task: AgentTask) -> SimpleNamespace:
    return SimpleNamespace(
        current_agent=task,
        root_dir=task.session.root_dir,
        mini_model_name="test-model",
        model_name="test-model",
        OPENAI_BASE_URL="",
        OPENAI_API_KEY="",
        multimodal_model_name=None,
        proxy_url="",
        spinner=NoopSpinner(),
    )


def _task_with_user_messages(count: int) -> AgentTask:
    session = Session(root_dir=Path.cwd())
    task = AgentTask(name="main", prompt="", session=session)
    for index in range(count):
        session.add_message(MessageRole.USER, f"user preference {index}")
        session.add_message(MessageRole.ASSISTANT, "ok")
    return task


def test_auto_review_skips_before_ten_messages(monkeypatch):
    calls = []

    async def fake_achat(*args, **kwargs):
        calls.append((args, kwargs))
        return _Response('{"memories": []}')

    monkeypatch.setattr("uniclaw.tools.memory.consolidate.achat", fake_achat)

    # 4 组 user+assistant = 8 条消息，低于 10 条阈值
    task = _task_with_user_messages(4)
    saved = asyncio.run(auto_review.review_and_save_if_due(_make_config(task)))

    assert saved == []
    assert calls == []


def test_auto_review_saves_new_memory(monkeypatch):
    task = _task_with_user_messages(10)
    saved_args = []

    async def fake_achat(*args, **kwargs):
        return _Response("""{
                "memories": [{
                    "name": "feedback-memory-save-tool",
                    "type": "feedback",
                    "description": "Use memory_save for durable memory writes",
                    "content": "Use the existing memory_save tool instead of writing a fixed path.",
                    "confidence": 1
                }]
            }""")

    def fake_save_memory(self, force=False):
        saved_args.append({"name": self.name, "scope": self.scope, "type": self.type})
        return {"status": "created", "message": "ok"}

    monkeypatch.setattr("uniclaw.tools.memory.consolidate.achat", fake_achat)
    monkeypatch.setattr(Memory, "save_memory", fake_save_memory)

    config = _make_config(task)
    saved = asyncio.run(auto_review.review_and_save_if_due(config))
    saved_again = asyncio.run(auto_review.review_and_save_if_due(config))

    assert [memory.name for memory in saved] == ["feedback-memory-save-tool"]
    assert saved_again == []
    # scope 是 Path 对象（项目根目录）或字符串 "project"
    assert saved_args[0]["scope"] is not None
    assert saved_args[0]["type"] == "feedback"
    # 10 组 user+assistant = 20 条消息
    assert task.memory_review_user_count == 20


def test_auto_review_deduplicates_identical_memory(monkeypatch):
    """当 save_memory 返回 identical 时，consolidate_session 跳过该记忆。"""
    task = _task_with_user_messages(10)

    async def fake_achat(*args, **kwargs):
        return _Response("""{
                "memories": [{
                    "name": "existing-memory",
                    "type": "feedback",
                    "description": "Already saved",
                    "content": "This memory already exists with identical content.",
                    "confidence": 1
                }]
            }""")

    def fake_save_memory(self, force=False):
        return {"status": "identical", "message": "already exists"}

    monkeypatch.setattr("uniclaw.tools.memory.consolidate.achat", fake_achat)
    monkeypatch.setattr(Memory, "save_memory", fake_save_memory)

    saved = asyncio.run(auto_review.review_and_save_if_due(_make_config(task)))

    # identical 状态的记忆被跳过，返回空列表
    assert saved == []
    # 但 review 计数仍然更新，不会重复触发
    assert task.memory_review_user_count == 20
