"""A2UI 交互界面组件输出规范工具。

A2UI 一段接近 1KB 的提示词从 system prompt 中移出,改为按需加载:
WebUI 模式下 agent 判断回答适合用交互界面呈现时,先调用 get_a2ui_guide
获取完整格式规范,再按规范输出 A2UI 代码块。系统提示仅保留一行触发语,省 token。
"""

from uniclaw.config import AppConfig, RunMode
from uniclaw.tools.base import tool

# A2UI 输出规范:仅在 WebUI 模式有渲染端。完整规范移出常驻 system prompt,
# 由 get_a2ui_guide 工具按需返回。触发入口是 context.get_base_system_prompt 里的
# 一行精简触发语(WebUI 主 agent 时注入)。
A2UI_OUTPUT_GUIDE = """## A2UI 交互界面组件输出规范

需要以结构化交互界面呈现回答时(状态概览、操作按钮组、配置表单、多标签内容等),在正文中以 a2ui 为语言标记的代码块声明组件(格式见下方示例),前端会渲染为可交互组件;纯文字回答不需要界面时不要输出。

格式:以 a2ui 为语言标记的代码块,内含单个 JSON 对象,根节点为 {"root": {组件}}。示例:

```a2ui
{"root": {"type": "Card", "title": "部署状态", "children": [
  {"type": "Row", "children": [
    {"type": "Badge", "content": "运行中", "tone": "success"},
    {"type": "Progress", "value": 72, "label": "进度"}
  ]},
  {"type": "Button", "label": "重启服务", "variant": "danger", "action": {"type": "agent", "payload": "重启服务"}}
]}}
```

可用组件(仅限以下类型,未知组件会被忽略并回退显示代码块):
- 布局: Column{children, gap}、Row{children, gap}
- 展示: Text{content, variant: h1|h2|body|caption, color}、Badge{content, tone: info|success|warn|danger}、Divider{}、Progress{value: 0-100, label}、Chart{chart: bar|line|pie, title, data: {labels: [...], series: [{name, values: [...]}]}}
- 容器: Card{title, children, footer}、Tabs{tabs: [{label, children}]}
- 交互: Button{label, variant: primary|default|danger, action}、TextField{label, name, placeholder, value}、Checkbox{label, name, checked}、Select{label, name, options: [{label, value}], value}

action 取值:
- "copy" 或 {type: "copy", text: "..."}: 点击复制文本
- {type: "link", url: "https://..."}: 点击打开链接
- {type: "agent", payload: "..."}: 点击将 payload 作为系统消息回传给你;若界面上同时有 TextField/Checkbox/Select,其 name/value 会一并回传,可据此收集用户输入

注意:组件树可与说明文本混合输出;JSON 必须合法,非法则回退为普通代码块显示。"""


@tool
def get_a2ui_guide() -> str:
    """获取 A2UI 交互界面组件的输出规范。
    当你的回答适合用结构化交互界面呈现时(状态概览、操作按钮组、配置表单、
    多标签内容等),调用本工具获取 A2UI 组件输出的格式说明,包括可用组件
    类型、JSON 结构示例与 action 取值,随后在正文中按规范输出可交互组件;
    纯文字回答无需调用。

    Returns:
        str: A2UI 组件输出规范全文(组件类型、格式示例、action 说明)。
    """
    return A2UI_OUTPUT_GUIDE


def get_tools(config: AppConfig = None) -> list:
    """获取 A2UI 说明工具列表。

    仅 WebUI 模式下暴露,其他模式无渲染端调用无意义
    (工具内部仅返回说明文本,无副作用;按需隐藏以省 token)。

    Args:
        config: AppConfig 实例,用于判断运行模式。None 时不限制。

    Returns:
        list: WebUI 模式下返回 [get_a2ui_guide],其他模式返回空列表。
    """
    if config is None or config.run_mode != RunMode.WEBUI:
        if config:
            config.record_unavailable_tools(
                [get_a2ui_guide.name],
                "仅 WebUI 模式下可用",
            )
        return []
    config.clear_unavailable_tools([get_a2ui_guide.name])
    return [get_a2ui_guide]


def get_all_tools() -> list:
    """获取所有 A2UI 说明工具(无条件返回,供 registry BM25 索引发现)。"""
    return [get_a2ui_guide]
