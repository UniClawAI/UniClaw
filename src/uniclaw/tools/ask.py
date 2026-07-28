from uniclaw.tools.base import tool


@tool
async def AskUserQuestion(
    questions: list[dict], title: str = "请选择", config=None
) -> str:
    """
    向用户(人类)提问以确认需求细节。当你需要了解用户的偏好、意图或需求时使用此工具。
    这是你唯一合法的主动与用户沟通的方式。
    每个问题提供 2-5 个预设选项供用户选择。

    适用场景:
    - 不确定用户想要什么功能/效果,需要确认需求
    - 有多种合理方案,让用户选择偏好
    - 执行前需要用户确认关键决策

    注意区分:
    - AskUserQuestion = 问需求(向用户确认"你想要什么")
    - ask_advisor = 问方案(向 AI 模型请教"怎么做更好")

    Args:
        questions: 问题列表(至少传 2 个问题),每项为 dict,格式:
            {"question": "问题文本", "options": ["选项1", "选项2", "选项3"]}
            每个问题必须提供 2-5 个纯文本 options。
        title: 对话框标题,默认为"请选择"

    示例:
        AskUserQuestion(questions=[
            {"question": "你想用什么语言？", "options": ["Python", "JavaScript", "Go", "Rust"]},
            {"question": "目标平台？", "options": ["Web 后端", "CLI 工具", "桌面应用", "移动端"]},
        ])
    """
    from uniclaw.console.ui import get_multi_input

    answers = await get_multi_input(questions=questions, title=title, config=config)
    return f"用户回答:{answers}"


def get_tools() -> list:
    return [AskUserQuestion]


def get_all_tools() -> list:
    """获取所有交互工具(无条件返回)"""
    return get_tools()
