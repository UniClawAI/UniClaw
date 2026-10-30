"""内置 Skill: A2UI 交互界面组件输出规范

原 get_a2ui_guide 工具迁来的完整规范。静态内容,由 LLM 按需通过 skill_read 加载。
"""

from uniclaw.tools.skill.loader import SkillDef, register_builtin

_A2UI_SKILL_PROMPT = r"""## A2UI 交互界面组件输出规范

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


def register():
    """注册 a2ui 技能"""
    register_builtin(
        SkillDef(
            name="a2ui",
            description="A2UI 交互界面组件输出规范:可用组件类型(卡片/表单/按钮组/标签页/"
            "图表等)、JSON 结构示例与 action 取值。仅 WebUI 模式可用(console/微信模式"
            "无渲染端,不要加载);当回答适合用结构化交互界面呈现时,先加载本文档再按"
            "规范输出 a2ui 代码块。",
            triggers=[
                "/a2ui",
                "/a2ui-guide",
                "a2ui",
                "interactive ui",
                "交互界面",
                "界面组件",
            ],
            tools=[],
            prompt=_A2UI_SKILL_PROMPT,
            file_path=__file__,
            source="builtin",
            when_to_use="仅 WebUI 模式且为主 agent:用户希望以交互式界面(卡片、表单、按钮组、图表等)"
            "呈现回答,或问题适合用结构化界面展示时。console/微信/子代理不要使用",
            argument_hint="",
        )
    )