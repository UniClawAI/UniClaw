import pytest
from pathlib import Path
from uniclaw.agent import AgentTask
from uniclaw.config import AppConfig
from uniclaw.tools.session.session_manager import SessionManager
from uniclaw.tools.session.session import Session


def _make_config(tmp_path, task: AgentTask) -> AppConfig:
    """创建测试用的 AppConfig。"""
    from uniclaw.config import ProviderProfile
    return AppConfig(
        current_agent=task,
        model_name=["test-model"],
        mini_model_name=["test-model"],
        providers={"test": ProviderProfile(name="test", protocol="openai", api_key="test", base_url="https://api.test.com/v1")},
        permission_mode="auto",
        verbose=False,
    )


def _make_task(name: str = "main", root_dir: Path = None) -> AgentTask:
    """创建测试用的 AgentTask。"""
    session = Session(root_dir=root_dir or Path.cwd())
    return AgentTask(name=name, prompt="", session=session)


@pytest.fixture(autouse=True)
def _patch_session_dir(tmp_path, monkeypatch):
    """将 SessionManager 的存储目录重定向到测试临时目录。"""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(SessionManager, "_default_dir", staticmethod(lambda: session_dir))


@pytest.mark.asyncio
async def test_save_load_preserves_full_message_fields(tmp_path):
    task = _make_task(root_dir=tmp_path)
    task.session.add_user_message(
        content=[
            {"type": "text", "text": "analyze this"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,abc123"},
            },
        ]
    )
    task.session.add_assistant_message(
        content="ok",
        model_name="test-model",
        usage={},
        reasoning_content="private reasoning",
        tool_calls=[{"id": "call_1", "name": "Read", "args": {"path": "a.py"}}],
    )
    task.session.add_tool_call_message(
        content="print('hi')",
        tool_call={"name": "Read", "tool_call_id": "call_1", "args": {}},
    )

    config = _make_config(tmp_path, task)
    await SessionManager.save_session(config)
    loaded = SessionManager.load_session(task.session.id)

    assert loaded is not None
    messages = loaded.to_openai_messages()
    assert len(messages) == 3
    assert messages[1]["reasoning_content"] == "private reasoning"
    assert messages[1]["tool_calls"][0]["id"] == "call_1"
    assert messages[0]["content"][1]["image_url"]["url"].endswith("abc123")


@pytest.mark.asyncio
async def test_search_sessions_reports_matching_message_numbers(tmp_path):
    task = _make_task(root_dir=tmp_path)
    task.session.add_user_message(content="hello")
    task.session.add_assistant_message(
        content="crawler script", model_name="test-model", usage={}
    )

    config = _make_config(tmp_path, task)
    await SessionManager.save_session(config)

    results = SessionManager.search_sessions("crawler")

    assert len(results) == 1
    assert len(results[0]["matches"]) >= 1


@pytest.mark.asyncio
async def test_session_load_command_replaces_task_messages(tmp_path):
    source = _make_task(root_dir=tmp_path)
    source.session.add_user_message(content="saved message")

    config = _make_config(tmp_path, source)
    await SessionManager.save_session(config)

    target = _make_task(root_dir=tmp_path)
    target.session.add_user_message(content="old message")

    # 直接测试加载功能
    loaded = SessionManager.load_session(source.session.id)
    assert loaded is not None
    target.session.replace_messages(loaded.to_openai_messages())

    assert target.session.to_openai_messages() == loaded.to_openai_messages()
