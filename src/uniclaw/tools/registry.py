"""工具注册表 — 管理核心工具和扩展工具的发现与加载。

对齐 Anthropic 的 defer_loading 模式:
- 核心工具(defer_loading: false):始终加载完整 schema
- 扩展工具(defer_loading: true):初始不加载,通过 search_tools 按需发现
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rank_bm25 import BM25Okapi
from uniclaw.utils.tokenize import tokenize as _tokenize

from .base import Tool, tool

# ── 工具分类定义 ──────────────────────────────────────────────


def _build_core_tools() -> list:
    """从核心工具对象动态构建核心工具列表(避免硬编码字符串)。"""
    from .fs import Read, Write, Edit, Glob
    from .shell import Bash, Grep
    from .web import webFetch, webSearch
    from .search import platform_search
    from .memory.tools import memory_save, memory_delete, memory_list, memory_search
    from .plan import enter_plan_mode, exit_plan_mode
    from .skill.tools import skill_suggest, skill_read

    return [
        Read,
        Write,
        Edit,
        Glob,
        Bash,
        Grep,
        webFetch,
        webSearch,
        platform_search,
        memory_save,
        memory_delete,
        memory_list,
        memory_search,
        enter_plan_mode,
        exit_plan_mode,
        skill_suggest,
        skill_read,
    ]


CORE_TOOLS = _build_core_tools()
CORE_TOOL_NAMES = {t.name for t in CORE_TOOLS}


def _build_extended_keywords() -> dict[str, list[str]]:
    """从扩展工具对象动态构建关键词映射(避免硬编码字符串)。"""
    from .fs import ReadPDF
    from .shell import search_files_with_everything
    from .computer_use import (
        cu_screenshot,
        cu_mouse_move,
        cu_mouse_click,
        cu_mouse_double_click,
        cu_mouse_drag,
        cu_mouse_scroll,
        cu_keyboard_type,
        cu_keyboard_type_unicode,
        cu_keyboard_press,
        cu_keyboard_key_down,
        cu_keyboard_key_up,
        cu_locate_on_screen,
        cu_get_elements,
        cu_find_element,
        cu_interact,
    )
    from .multi_agent.tools import (
        subagent_create,
        subagent_send_message,
        subagent_close,
        subagent_check_result,
        subagent_list_tasks,
        subagent_discuss,
        subagent_list_definitions,
        subagent_get_definition,
    )
    from .todolist.tools import (
        todolist_create,
        todolist_update,
        todolist_clear,
        todolist_cancel,
        todolist_list,
    )
    from .monitor.tools import (
        monitor_start,
        monitor_stop,
        monitor_list,
        monitor_output,
        monitor_input,
        monitor_get_matched,
        monitor_update_pattern,
    )
    from .session.tools import (
        session_list,
        session_detail,
        session_delete,
        session_update_title,
    )
    from .session.recall import recall_history, get_history_range
    from .scheduler.tools import (
        schedule_create,
        schedule_list,
        schedule_update,
        schedule_remove,
        schedule_toggle,
    )
    from .mcp.tools import (
        mcp_add_server,
        mcp_remove_server,
        mcp_toggle_server,
        mcp_list_servers,
    )
    from .security.tools import (
        read_llm_safe_prompt,
        write_llm_safe_prompt,
        edit_llm_safe_prompt,
        clear_llm_safe_prompt,
    )
    from .hooks.tools import hook_docs, hook_read, hook_add, hook_remove
    from .sandbox import RunCode
    from .sleep import sleep_timer, wait
    from .media import ReadMedia, GenerateImage
    from .ask import AskUserQuestion
    from .notify import push_notification
    from .tts.tools import text_to_speech
    from .web_browse.tools import (
        browser_start,
        browser_close,
        browser_navigate,
        browser_click,
        browser_type,
        browser_screenshot,
        browser_get_text,
        browser_evaluate,
        browser_get_html,
        browser_get_attribute,
        browser_get_elements,
        browser_wait,
        browser_scroll,
        browser_back,
        browser_forward,
        browser_reload,
        browser_get_url,
        browser_get_title,
        browser_toggle_mode,
        browser_press_key,
        browser_select_option,
        browser_check,
        browser_hover,
        browser_drag,
        browser_new_page,
        browser_close_page,
        browser_switch_page,
        browser_list_pages,
        browser_dblclick,
        browser_focus,
        browser_scroll_into_view,
        browser_key_down,
        browser_key_up,
        browser_keyboard_type,
        browser_insert_text,
        browser_get_value,
        browser_get_count,
        browser_get_box,
        browser_get_styles,
    )
    from .help import list_slash_commands, get_command_help
    from .send_file import send_file
    from .wechat import (
        wechat_list_contacts,
        wechat_send_text,
        wechat_send_image,
        wechat_send_file,
    )
    from .knowledge.tools import (
        kg_add_entity,
        kg_add_relation,
        kg_add_alias,
        kg_update_entity,
        kg_delete_entity,
        kg_delete_relation,
        kg_merge_entities,
        kg_get_entity,
        kg_search,
        kg_neighbors,
        kg_path,
        kg_stats,
        kg_export,
        kg_list,
        kg_extract,
        kg_clear,
    )
    from .advisor import advisor_list, ask_advisor, investigate
    from .download.tools import http_download, http_download_status, http_download_remove
    from .rag.tools import (
        rag_ingest,
        rag_search,
        rag_list_collections,
        rag_delete_collection,
        rag_set_desc,
    )
    from uniclaw.tools.a2a.tools import (
        a2a_send_task, a2a_add_agent, a2a_list_agents,
        a2a_submit_task, a2a_get_task, a2a_cancel_task,
    )

    # tool.name → 关键词列表(中英文+语义同义词)
    return {
        a2a_send_task.name: ["a2a", "remote agent", "远程智能体", "控制电脑", "委托任务"],
        a2a_add_agent.name: ["a2a", "add remote agent", "添加远程服务", "连接 A2A"],
        a2a_list_agents.name: ["a2a", "list remote agents", "外部智能体", "远程服务列表"],
        a2a_submit_task.name: ["a2a", "async", "异步任务", "多轮对话", "继续远程对话"],
        a2a_get_task.name: ["a2a", "task status", "任务状态", "查询远程任务"],
        a2a_cancel_task.name: ["a2a", "cancel task", "取消远程任务"],
        ReadPDF.name: ["pdf", "PDF", "文档", "阅读PDF", "read pdf", "parse pdf"],
        search_files_with_everything.name: [
            "everything",
            "文件搜索",
            "快速搜索",
            "es",
            "file search",
        ],
        cu_screenshot.name: [
            "截图",
            "屏幕",
            "screenshot",
            "截屏",
            "屏幕截图",
            "capture screen",
        ],
        cu_mouse_move.name: ["鼠标移动", "mouse", "移动鼠标", "move mouse", "cursor"],
        cu_mouse_click.name: ["点击", "click", "鼠标点击", "单击", "tap"],
        cu_mouse_double_click.name: ["双击", "double click", "鼠标双击"],
        cu_mouse_drag.name: ["拖拽", "drag", "拖动", "鼠标拖拽", "drag and drop"],
        cu_mouse_scroll.name: ["滚动", "scroll", "滚轮", "鼠标滚动", "wheel"],
        cu_keyboard_type.name: [
            "输入",
            "type",
            "键盘输入",
            "打字",
            "input text",
            "typing",
        ],
        cu_keyboard_type_unicode.name: ["unicode输入", "中文输入", "unicode type"],
        cu_keyboard_press.name: [
            "按键",
            "press",
            "键盘按键",
            "快捷键",
            "hotkey",
            "key press",
        ],
        cu_keyboard_key_down.name: ["按下", "key down", "键盘按下"],
        cu_keyboard_key_up.name: ["松开", "key up", "键盘松开"],
        cu_locate_on_screen.name: [
            "定位",
            "locate",
            "屏幕定位",
            "查找图片",
            "find on screen",
            "template match",
        ],
        cu_get_elements.name: [
            "UI元素",
            "元素列表",
            "控件",
            "按钮",
            "输入框",
            "get elements",
            "list elements",
            "UI tree",
            "accessibility",
            "交互元素",
        ],
        cu_find_element.name: [
            "查找元素",
            "find element",
            "搜索控件",
            "查找控件",
            "定位元素",
            "search element",
        ],
        cu_interact.name: [
            "操作元素",
            "点击元素",
            "interact",
            "click element",
            "invoke",
            "focus",
            "UI操作",
            "控件操作",
        ],
        subagent_create.name: [
            "创建代理",
            "子代理",
            "sub agent",
            "create agent",
            "多智能体",
            "spawn agent",
        ],
        subagent_send_message.name: [
            "发送消息",
            "send message",
            "代理消息",
            "message agent",
        ],
        subagent_close.name: ["关闭代理", "close agent", "停止代理", "kill agent"],
        subagent_check_result.name: [
            "检查结果",
            "agent result",
            "代理结果",
            "check result",
        ],
        subagent_list_tasks.name: ["列出代理", "list agents", "代理列表", "代理任务"],
        subagent_discuss.name: [
            "讨论",
            "discuss",
            "代理讨论",
            "多代理讨论",
            "multi agent discuss",
        ],
        subagent_list_definitions.name: [
            "代理定义",
            "agent definitions",
            "可用代理",
            "available agents",
        ],
        subagent_get_definition.name: [
            "代理详情",
            "agent detail",
            "代理信息",
            "agent info",
            "子代理详情",
        ],
        todolist_create.name: [
            "创建任务",
            "待办",
            "todolist",
            "任务清单",
            "创建待办",
            "create task",
            "todo",
        ],
        todolist_update.name: [
            "更新任务",
            "更新待办",
            "完成任务",
            "update task",
            "complete task",
        ],
        todolist_clear.name: ["清除任务", "清空待办", "clear todolist", "clear tasks"],
        todolist_cancel.name: ["取消任务", "cancel todolist", "cancel task"],
        todolist_list.name: [
            "列出任务",
            "查看待办",
            "list todolist",
            "任务列表",
            "list tasks",
        ],
        monitor_start.name: [
            "启动监控",
            "monitor",
            "后台监控",
            "日志监控",
            "start monitor",
            "log monitor",
        ],
        monitor_stop.name: ["停止监控", "stop monitor"],
        monitor_list.name: ["列出监控", "monitor list", "监控列表"],
        monitor_output.name: ["监控输出", "monitor output", "查看日志", "view log"],
        monitor_input.name: ["监控输入", "monitor input"],
        monitor_get_matched.name: ["匹配结果", "matched", "监控匹配", "grep monitor"],
        monitor_update_pattern.name: [
            "修改匹配",
            "更新模式",
            "update pattern",
            "change pattern",
            "修改监控",
        ],
        session_list.name: [
            "会话列表",
            "session list",
            "列出会话",
            "历史会话",
            "history",
        ],
        session_detail.name: ["会话详情", "session detail", "查看会话"],
        session_delete.name: ["删除会话", "delete session", "清除会话"],
        session_update_title.name: ["更新标题", "session title", "会话标题"],
        recall_history.name: [
            "回忆",
            "历史",
            "搜索历史",
            "recall",
            "history",
            "之前讨论",
            "归档",
            "旧消息",
            "历史消息",
        ],
        get_history_range.name: [
            "历史范围",
            "历史消息范围",
            "history range",
            "消息序号",
            "历史区间",
        ],
        schedule_create.name: [
            "创建定时",
            "cron",
            "定时任务",
            "schedule",
            "计划任务",
            "timer",
            "periodic",
        ],
        schedule_list.name: ["定时列表", "list schedule", "定时任务列表"],
        schedule_update.name: ["修改定时", "update schedule", "更新定时", "修改action"],
        schedule_remove.name: [
            "删除定时",
            "remove schedule",
            "取消定时",
            "cancel schedule",
        ],
        schedule_toggle.name: ["切换定时", "toggle schedule", "启用定时", "禁用定时"],
        mcp_add_server.name: [
            "添加MCP",
            "MCP服务器",
            "add MCP",
            "添加工具服务",
            "tool server",
        ],
        mcp_remove_server.name: ["删除MCP", "remove MCP", "移除MCP"],
        mcp_toggle_server.name: ["切换MCP", "toggle MCP", "启用MCP", "禁用MCP"],
        mcp_list_servers.name: ["MCP列表", "list MCP", "MCP服务器列表"],
        read_llm_safe_prompt.name: [
            "安全提示",
            "safe prompt",
            "读取安全规则",
            "security rules",
        ],
        write_llm_safe_prompt.name: ["写入安全", "write safe prompt"],
        edit_llm_safe_prompt.name: ["编辑安全", "edit safe prompt"],
        clear_llm_safe_prompt.name: ["清除安全", "clear safe prompt"],
        hook_docs.name: [
            "hook文档",
            "hook docs",
            "hook说明",
            "hook帮助",
            "hook帮助文档",
            "钩子文档",
        ],
        hook_read.name: ["读取钩子", "hook", "查看钩子", "读取hook", "read hook"],
        hook_add.name: ["添加钩子", "add hook", "创建钩子", "create hook"],
        hook_remove.name: ["删除钩子", "remove hook", "移除钩子"],
        RunCode.name: [
            "沙箱",
            "sandbox",
            "Docker",
            "代码执行",
            "运行代码",
            "run code",
            "execute code",
        ],
        sleep_timer.name: ["睡眠", "sleep", "等待", "定时器", "wait", "delay", "timer"],
        wait.name: ["等待", "wait", "延迟", "delay"],
        ReadMedia.name: [
            "图片",
            "media",
            "多媒体",
            "媒体",
            "读取图片",
            "视频",
            "音频",
            "image",
            "photo",
            "picture",
            "video",
            "audio",
            "read image",
            "OCR",
            "识别图片",
            "图片识别",
            "看图",
            "读图",
            "analyze image",
            "image recognition",
            "text recognition",
            "extract text",
        ],
        GenerateImage.name: [
            "生成图片",
            "生图",
            "AI画图",
            "图片生成",
            "画图",
            "生成",
            "generate image",
            "create image",
            "AI image",
            "image generation",
            "draw",
            "painting",
            "illustration",
            "图片创作",
        ],
        AskUserQuestion.name: [
            "询问用户",
            "ask user",
            "用户问题",
            "确认",
            "confirm",
            "question",
            "用户选择",
            "让用户决定",
            "问需求",
            "确认需求",
            "用户偏好",
        ],
        push_notification.name: [
            "通知",
            "notification",
            "推送通知",
            "桌面通知",
            "push notify",
        ],
        # Web Browse 工具(都带 browser 关键词)
        browser_start.name: [
            "browser",
            "启动浏览器",
            "browser start",
            "打开浏览器",
            "start browser",
            "playwright",
            "open browser",
        ],
        browser_close.name: [
            "browser",
            "关闭浏览器",
            "browser close",
            "停止浏览器",
            "stop browser",
            "quit browser",
        ],
        browser_navigate.name: [
            "browser",
            "导航",
            "navigate",
            "打开网页",
            "goto",
            "访问网址",
            "open url",
            "go to url",
        ],
        browser_click.name: [
            "browser",
            "浏览器点击",
            "browser click",
            "网页点击",
            "点击链接",
            "点击按钮",
            "click element",
        ],
        browser_type.name: [
            "browser",
            "浏览器输入",
            "browser type",
            "网页输入",
            "填写表单",
            "输入文本",
            "input text",
            "fill form",
        ],
        browser_screenshot.name: [
            "browser",
            "网页截图",
            "browser screenshot",
            "页面截图",
            "capture page",
            "take screenshot",
        ],
        browser_get_text.name: [
            "browser",
            "获取网页文本",
            "get text",
            "提取文本",
            "网页内容",
            "page text",
            "extract text",
        ],
        browser_get_elements.name: [
            "browser",
            "获取元素",
            "get elements",
            "页面元素",
            "网页元素",
            "可交互元素",
            "interactive elements",
            "元素列表",
            "DOM元素",
            "查找元素",
            "find element",
            "元素选择器",
            "selector",
            "定位元素",
            "locate element",
            "页面结构",
            "page structure",
            "navigate",
            "导航元素",
            "网页结构",
        ],
        browser_evaluate.name: [
            "browser",
            "执行JS",
            "evaluate",
            "运行JavaScript",
            "执行脚本",
            "run js",
            "execute script",
        ],
        browser_get_html.name: [
            "browser",
            "获取HTML",
            "get html",
            "网页源码",
            "page source",
            "page html",
            "网页内容",
        ],
        browser_get_attribute.name: [
            "browser",
            "获取属性",
            "get attribute",
            "元素属性",
            "element attribute",
        ],
        browser_wait.name: [
            "browser",
            "等待元素",
            "wait",
            "等待加载",
            "wait for element",
            "等待页面",
            "wait load",
        ],
        browser_scroll.name: [
            "browser",
            "滚动页面",
            "scroll page",
            "页面滚动",
            "scroll down",
            "scroll up",
        ],
        browser_back.name: ["browser", "浏览器后退", "back", "后退", "go back"],
        browser_forward.name: [
            "browser",
            "浏览器前进",
            "forward",
            "前进",
            "go forward",
        ],
        browser_reload.name: ["browser", "刷新页面", "reload", "刷新", "refresh page"],
        browser_get_url.name: [
            "browser",
            "获取URL",
            "get url",
            "当前网址",
            "current url",
            "页面地址",
        ],
        browser_get_title.name: [
            "browser",
            "获取标题",
            "get title",
            "页面标题",
            "page title",
        ],
        browser_toggle_mode.name: [
            "browser",
            "切换模式",
            "toggle mode",
            "显示浏览器",
            "隐藏浏览器",
            "有头模式",
            "无头模式",
            "headed",
            "headless",
        ],
        browser_press_key.name: [
            "browser",
            "浏览器按键",
            "browser key",
            "页面按键",
            "press key",
            "键盘操作",
        ],
        browser_select_option.name: [
            "browser",
            "选择选项",
            "select",
            "下拉框",
            "dropdown",
            "select option",
        ],
        browser_check.name: [
            "browser",
            "勾选",
            "check",
            "复选框",
            "checkbox",
            "勾选框",
        ],
        browser_hover.name: ["browser", "悬停", "hover", "鼠标悬停", "mouse over"],
        browser_drag.name: ["browser", "拖拽元素", "drag element", "拖动元素", "拖放"],
        browser_dblclick.name: [
            "browser",
            "双击",
            "double click",
            "dblclick",
            "双击元素",
        ],
        browser_focus.name: ["browser", "聚焦", "focus", "聚焦元素", "元素聚焦"],
        browser_scroll_into_view.name: [
            "browser",
            "滚动到元素",
            "scroll into view",
            "显示元素",
            "滚动可见",
        ],
        browser_key_down.name: ["browser", "按住按键", "key down", "按住键", "keydown"],
        browser_key_up.name: ["browser", "松开按键", "key up", "松开键", "keyup"],
        browser_keyboard_type.name: [
            "browser",
            "键盘输入",
            "keyboard type",
            "真实按键",
            "逐字输入",
            "按键输入",
        ],
        browser_insert_text.name: [
            "browser",
            "插入文本",
            "insert text",
            "快速填充",
            "文本插入",
        ],
        browser_get_value.name: [
            "browser",
            "获取值",
            "get value",
            "表单值",
            "input value",
            "输入框值",
        ],
        browser_get_count.name: [
            "browser",
            "元素数量",
            "get count",
            "统计元素",
            "count elements",
            "匹配数量",
        ],
        browser_get_box.name: [
            "browser",
            "边界框",
            "bounding box",
            "元素位置",
            "元素尺寸",
            "元素大小",
            "get box",
        ],
        browser_get_styles.name: [
            "browser",
            "计算样式",
            "computed styles",
            "get styles",
            "CSS样式",
            "元素样式",
        ],
        # 页面管理工具
        browser_new_page.name: [
            "browser",
            "新建页面",
            "new page",
            "新标签页",
            "new tab",
            "打开新页面",
        ],
        browser_close_page.name: [
            "browser",
            "关闭页面",
            "close page",
            "关闭标签页",
            "close tab",
        ],
        browser_switch_page.name: [
            "browser",
            "切换页面",
            "switch page",
            "切换标签页",
            "switch tab",
        ],
        browser_list_pages.name: [
            "browser",
            "页面列表",
            "list pages",
            "标签页列表",
            "列出页面",
            "所有页面",
        ],
        # 帮助工具
        list_slash_commands.name: [
            "命令列表",
            "斜杠命令",
            "slash commands",
            "list commands",
            "帮助",
            "help",
            "命令帮助",
            "可用命令",
            "what commands",
        ],
        get_command_help.name: [
            "命令帮助",
            "命令用法",
            "command help",
            "命令详情",
            "命令说明",
            "how to use",
            "怎么用",
            "用法",
        ],
        # TTS 工具
        text_to_speech.name: [
            "tts",
            "语音合成",
            "文本转语音",
            "text to speech",
            "朗读",
            "语音",
            "说话",
            "播放语音",
            "音频生成",
            "voice",
        ],
        send_file.name: [
            "发送文件",
            "send file",
            "文件发送",
            "下载文件",
            "download file",
            "提供文件",
            "文件下载",
        ],
        # 微信工具
        wechat_list_contacts.name: [
            "微信",
            "wechat",
            "微信列表",
            "微信号",
            "可用微信",
            "微信账号",
            "机器人列表",
            "bot list",
            "联系人",
            "contacts",
            "微信联系人",
        ],
        wechat_send_text.name: [
            "微信",
            "wechat",
            "发消息",
            "send message",
            "微信消息",
            "发送文字",
            "send text",
            "微信文字",
            "微信发送",
        ],
        wechat_send_image.name: [
            "微信",
            "wechat",
            "发图片",
            "send image",
            "微信图片",
            "发送图片",
            "微信发图",
            "wechat image",
        ],
        wechat_send_file.name: [
            "微信",
            "wechat",
            "发文件",
            "send file to",
            "微信文件",
            "发送文件到",
            "微信发送文件",
            "wechat file",
        ],
        # 知识图谱工具
        kg_add_entity.name: [
            "知识图谱",
            "添加实体",
            "knowledge graph",
            "add entity",
            "创建实体",
            "新建实体",
            "实体",
            "entity",
        ],
        kg_add_relation.name: [
            "添加关系",
            "知识图谱",
            "add relation",
            "创建关系",
            "关联实体",
            "关系",
            "relation",
        ],
        kg_add_alias.name: [
            "别名",
            "alias",
            "实体别名",
            "添加别名",
            "add alias",
            "知识图谱",
        ],
        kg_search.name: [
            "搜索实体",
            "知识图谱",
            "search entity",
            "search knowledge",
            "查找实体",
            "图谱搜索",
        ],
        kg_neighbors.name: [
            "邻居",
            "neighbors",
            "知识图谱",
            "关联实体",
            "遍历",
            "traverse",
            "图谱遍历",
        ],
        kg_path.name: [
            "路径",
            "path",
            "知识图谱",
            "实体路径",
            "find path",
            "最短路径",
            "图谱路径",
        ],
        kg_stats.name: ["图谱统计", "knowledge stats", "知识图谱", "图谱信息"],
        kg_export.name: [
            "导出图谱",
            "export knowledge",
            "知识图谱",
            "可视化",
            "visualize",
            "图谱导出",
        ],
        kg_extract.name: [
            "提取实体",
            "extract entities",
            "知识图谱",
            "自动提取",
            "AI提取",
            "实体提取",
            "关系提取",
        ],
        kg_list.name: ["列出实体", "list entities", "知识图谱", "图谱列表", "实体列表"],
        kg_get_entity.name: [
            "实体详情",
            "entity detail",
            "知识图谱",
            "查看实体",
            "实体信息",
        ],
        kg_update_entity.name: ["更新实体", "update entity", "知识图谱", "修改实体"],
        kg_delete_entity.name: ["删除实体", "delete entity", "知识图谱", "移除实体"],
        kg_delete_relation.name: [
            "删除关系",
            "delete relation",
            "知识图谱",
            "移除关系",
        ],
        kg_merge_entities.name: [
            "合并实体",
            "merge entities",
            "知识图谱",
            "实体合并",
            "去重",
            "实体去重",
            "deduplicate entity",
        ],
        kg_clear.name: [
            "清空图谱",
            "clear knowledge",
            "知识图谱",
            "清除图谱",
            "重置图谱",
        ],
        advisor_list.name: [
            "顾问模型",
            "advisor",
            "咨询模型",
            "列出顾问",
            "list advisors",
            "大模型",
            "顾问列表",
            "AI模型列表",
            "高级模型",
        ],
        ask_advisor.name: [
            "问顾问",
            "咨询顾问",
            "ask advisor",
            "consult",
            "询问模型",
            "请教",
            "ask model",
            "顾问提问",
            "问方案",
            "怎么做",
            "技术方案",
            "AI分析",
        ],
        investigate.name: [
            "调查",
            "调查研究",
            "investigate",
            "侦察",
            "搜集信息",
            "调研",
            "查一下",
            "了解一下",
        ],
        http_download.name: [
            "下载",
            "download",
            "下载文件",
            "HTTP下载",
            "HTTPS下载",
            "文件下载",
            "download file",
            "http download",
            "下载链接",
            "多线程下载",
            "断点续传",
            "限速下载",
            "URL下载",
        ],
        http_download_status.name: [
            "下载状态",
            "download status",
            "下载进度",
            "查看下载",
            "暂停下载",
            "恢复下载",
            "取消下载",
        ],
        http_download_remove.name: [
            "删除下载",
            "清理下载",
            "remove download",
            "delete download",
            "移除下载任务",
            "清除下载记录",
        ],
        # RAG 工具
        rag_ingest.name: [
            "RAG",
            "导入文档",
            "向量数据库",
            "文档导入",
            "ingest",
            "embedding",
            "文档入库",
            "知识库导入",
            "add documents",
            "index documents",
        ],
        rag_search.name: [
            "RAG",
            "语义检索",
            "向量检索",
            "文档检索",
            "semantic search",
            "vector search",
            "搜索文档",
            "search documents",
            "知识库检索",
            "相似搜索",
        ],
        rag_list_collections.name: [
            "RAG",
            "集合列表",
            "list collections",
            "向量集合",
            "RAG列表",
        ],
        rag_delete_collection.name: [
            "RAG",
            "删除集合",
            "delete collection",
            "清除集合",
            "移除集合",
        ],
        rag_set_desc.name: [
            "RAG",
            "设置描述",
            "set description",
            "集合描述",
            "collection description",
            "修改描述",
        ],
    }


def _build_tool_categories() -> dict[str, str]:
    """从扩展工具对象动态构建类别映射(避免硬编码字符串)。"""
    from .fs import ReadPDF
    from .shell import search_files_with_everything
    from .computer_use import (
        cu_screenshot,
        cu_mouse_move,
        cu_mouse_click,
        cu_mouse_double_click,
        cu_mouse_drag,
        cu_mouse_scroll,
        cu_keyboard_type,
        cu_keyboard_type_unicode,
        cu_keyboard_press,
        cu_keyboard_key_down,
        cu_keyboard_key_up,
        cu_locate_on_screen,
        cu_get_elements,
        cu_find_element,
        cu_interact,
    )
    from .multi_agent.tools import (
        subagent_create,
        subagent_send_message,
        subagent_close,
        subagent_check_result,
        subagent_list_tasks,
        subagent_discuss,
        subagent_list_definitions,
        subagent_get_definition,
    )
    from .todolist.tools import (
        todolist_create,
        todolist_update,
        todolist_clear,
        todolist_cancel,
        todolist_list,
    )
    from .monitor.tools import (
        monitor_start,
        monitor_stop,
        monitor_list,
        monitor_output,
        monitor_input,
        monitor_get_matched,
        monitor_update_pattern,
    )
    from .session.tools import (
        session_list,
        session_detail,
        session_delete,
        session_update_title,
    )
    from .session.recall import recall_history, get_history_range
    from .scheduler.tools import (
        schedule_create,
        schedule_list,
        schedule_update,
        schedule_remove,
        schedule_toggle,
    )
    from .mcp.tools import (
        mcp_add_server,
        mcp_remove_server,
        mcp_toggle_server,
        mcp_list_servers,
    )
    from .security.tools import (
        read_llm_safe_prompt,
        write_llm_safe_prompt,
        edit_llm_safe_prompt,
        clear_llm_safe_prompt,
    )
    from .hooks.tools import hook_docs, hook_read, hook_add, hook_remove
    from .sandbox import RunCode
    from .sleep import sleep_timer, wait
    from .media import ReadMedia, GenerateImage
    from .ask import AskUserQuestion
    from .notify import push_notification
    from .tts.tools import text_to_speech
    from .web_browse.tools import (
        browser_start,
        browser_close,
        browser_navigate,
        browser_click,
        browser_type,
        browser_screenshot,
        browser_get_text,
        browser_evaluate,
        browser_get_html,
        browser_get_attribute,
        browser_get_elements,
        browser_wait,
        browser_scroll,
        browser_back,
        browser_forward,
        browser_reload,
        browser_get_url,
        browser_get_title,
        browser_toggle_mode,
        browser_press_key,
        browser_select_option,
        browser_check,
        browser_hover,
        browser_drag,
        browser_new_page,
        browser_close_page,
        browser_switch_page,
        browser_list_pages,
        browser_dblclick,
        browser_focus,
        browser_scroll_into_view,
        browser_key_down,
        browser_key_up,
        browser_keyboard_type,
        browser_insert_text,
        browser_get_value,
        browser_get_count,
        browser_get_box,
        browser_get_styles,
    )
    from .help import list_slash_commands, get_command_help
    from .send_file import send_file
    from .wechat import (
        wechat_list_contacts,
        wechat_send_text,
        wechat_send_image,
        wechat_send_file,
    )
    from .knowledge.tools import (
        kg_add_entity,
        kg_add_relation,
        kg_add_alias,
        kg_update_entity,
        kg_delete_entity,
        kg_delete_relation,
        kg_merge_entities,
        kg_get_entity,
        kg_search,
        kg_neighbors,
        kg_path,
        kg_stats,
        kg_export,
        kg_list,
        kg_extract,
        kg_clear,
    )
    from .advisor import advisor_list, ask_advisor, investigate
    from .download.tools import http_download, http_download_status, http_download_remove
    from .rag.tools import (
        rag_ingest,
        rag_search,
        rag_list_collections,
        rag_delete_collection,
        rag_set_desc,
    )

    # tool.name → 类别
    return {
        ReadPDF.name: "文件系统",
        search_files_with_everything.name: "Shell",
        cu_screenshot.name: "计算机操作",
        cu_mouse_move.name: "计算机操作",
        cu_mouse_click.name: "计算机操作",
        cu_mouse_double_click.name: "计算机操作",
        cu_mouse_drag.name: "计算机操作",
        cu_mouse_scroll.name: "计算机操作",
        cu_keyboard_type.name: "计算机操作",
        cu_keyboard_type_unicode.name: "计算机操作",
        cu_keyboard_press.name: "计算机操作",
        cu_keyboard_key_down.name: "计算机操作",
        cu_keyboard_key_up.name: "计算机操作",
        cu_locate_on_screen.name: "计算机操作",
        cu_get_elements.name: "计算机操作",
        cu_find_element.name: "计算机操作",
        cu_interact.name: "计算机操作",
        subagent_create.name: "多智能体",
        subagent_send_message.name: "多智能体",
        subagent_close.name: "多智能体",
        subagent_check_result.name: "多智能体",
        subagent_list_tasks.name: "多智能体",
        subagent_discuss.name: "多智能体",
        subagent_list_definitions.name: "多智能体",
        subagent_get_definition.name: "多智能体",
        todolist_create.name: "任务清单",
        todolist_update.name: "任务清单",
        todolist_clear.name: "任务清单",
        todolist_cancel.name: "任务清单",
        todolist_list.name: "任务清单",
        monitor_start.name: "进程监控",
        monitor_stop.name: "进程监控",
        monitor_list.name: "进程监控",
        monitor_output.name: "进程监控",
        monitor_input.name: "进程监控",
        monitor_get_matched.name: "进程监控",
        monitor_update_pattern.name: "进程监控",
        session_list.name: "会话管理",
        session_detail.name: "会话管理",
        session_delete.name: "会话管理",
        session_update_title.name: "会话管理",
        recall_history.name: "会话管理",
        get_history_range.name: "会话管理",
        schedule_create.name: "定时任务",
        schedule_list.name: "定时任务",
        schedule_update.name: "定时任务",
        schedule_remove.name: "定时任务",
        schedule_toggle.name: "定时任务",
        mcp_add_server.name: "MCP管理",
        mcp_remove_server.name: "MCP管理",
        mcp_toggle_server.name: "MCP管理",
        mcp_list_servers.name: "MCP管理",
        read_llm_safe_prompt.name: "安全管理",
        write_llm_safe_prompt.name: "安全管理",
        edit_llm_safe_prompt.name: "安全管理",
        clear_llm_safe_prompt.name: "安全管理",
        hook_docs.name: "Hook管理",
        hook_read.name: "Hook管理",
        hook_add.name: "Hook管理",
        hook_remove.name: "Hook管理",
        RunCode.name: "沙箱",
        sleep_timer.name: "睡眠/等待",
        wait.name: "睡眠/等待",
        ReadMedia.name: "媒体",
        GenerateImage.name: "媒体",
        AskUserQuestion.name: "用户交互",
        push_notification.name: "通知",
        # Web Browse 工具
        browser_start.name: "浏览器",
        browser_close.name: "浏览器",
        browser_navigate.name: "浏览器",
        browser_click.name: "浏览器",
        browser_type.name: "浏览器",
        browser_screenshot.name: "浏览器",
        browser_get_text.name: "浏览器",
        browser_evaluate.name: "浏览器",
        browser_get_html.name: "浏览器",
        browser_get_attribute.name: "浏览器",
        browser_get_elements.name: "浏览器",
        browser_wait.name: "浏览器",
        browser_scroll.name: "浏览器",
        browser_back.name: "浏览器",
        browser_forward.name: "浏览器",
        browser_reload.name: "浏览器",
        browser_get_url.name: "浏览器",
        browser_get_title.name: "浏览器",
        browser_toggle_mode.name: "浏览器",
        browser_press_key.name: "浏览器",
        browser_select_option.name: "浏览器",
        browser_check.name: "浏览器",
        browser_hover.name: "浏览器",
        browser_drag.name: "浏览器",
        browser_dblclick.name: "浏览器",
        browser_focus.name: "浏览器",
        browser_scroll_into_view.name: "浏览器",
        browser_key_down.name: "浏览器",
        browser_key_up.name: "浏览器",
        browser_keyboard_type.name: "浏览器",
        browser_insert_text.name: "浏览器",
        browser_get_value.name: "浏览器",
        browser_get_count.name: "浏览器",
        browser_get_box.name: "浏览器",
        browser_get_styles.name: "浏览器",
        # 页面管理工具
        browser_new_page.name: "浏览器",
        browser_close_page.name: "浏览器",
        browser_switch_page.name: "浏览器",
        browser_list_pages.name: "浏览器",
        # 帮助工具
        list_slash_commands.name: "帮助",
        get_command_help.name: "帮助",
        # TTS 工具
        text_to_speech.name: "TTS",
        send_file.name: "文件发送",
        # 微信工具
        wechat_list_contacts.name: "微信",
        wechat_send_text.name: "微信",
        wechat_send_image.name: "微信",
        wechat_send_file.name: "微信",
        # 知识图谱工具
        kg_add_entity.name: "知识图谱",
        kg_add_relation.name: "知识图谱",
        kg_add_alias.name: "知识图谱",
        kg_update_entity.name: "知识图谱",
        kg_delete_entity.name: "知识图谱",
        kg_delete_relation.name: "知识图谱",
        kg_merge_entities.name: "知识图谱",
        kg_get_entity.name: "知识图谱",
        kg_search.name: "知识图谱",
        kg_neighbors.name: "知识图谱",
        kg_path.name: "知识图谱",
        kg_stats.name: "知识图谱",
        kg_export.name: "知识图谱",
        kg_list.name: "知识图谱",
        kg_extract.name: "知识图谱",
        kg_clear.name: "知识图谱",
        # 顾问工具
        advisor_list.name: "顾问",
        ask_advisor.name: "顾问",
        investigate.name: "顾问",
        http_download.name: "下载",
        http_download_status.name: "下载",
        http_download_remove.name: "下载",
        # RAG 工具
        rag_ingest.name: "RAG",
        rag_search.name: "RAG",
        rag_list_collections.name: "RAG",
        rag_delete_collection.name: "RAG",
        rag_set_desc.name: "RAG",
    }


# ── 注册表 ──────────────────────────────────────────────────


@dataclass
class ToolEntry:
    tool: Tool
    keywords: list[str]
    category: str


class ToolRegistry:
    """工具注册表 — 管理核心工具和扩展工具的发现与加载。"""

    _instance: Optional["ToolRegistry"] = None

    def __init__(self):
        self._entries: dict[str, ToolEntry] = {}
        self._core_names: set[str] = set()
        self._bm25: Optional[BM25Okapi] = None
        self._bm25_keys: list[str] = []  # 与 BM25 矩阵对齐的工具名列表
        self._bm25_corpus: list[list[str]] = []

    @classmethod
    def get_instance(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(
        self,
        tool: Tool,
        keywords: list[str],
        category: str,
        is_core: bool = False,
    ):
        """注册工具到注册表。"""
        self._entries[tool.name] = ToolEntry(
            tool=tool, keywords=keywords, category=category
        )
        if is_core:
            self._core_names.add(tool.name)
        # 标记需要重建 BM25 索引
        self._bm25 = None

    def unregister(self, *names: str):
        """从注册表中移除一个或多个工具。"""
        for name in names:
            self._entries.pop(name, None)
        self._bm25 = None

    def clear_bm25(self):
        """标记 BM25 索引需要重建。"""
        self._bm25 = None

    def _build_bm25(self):
        """构建 BM25 索引。"""
        self._bm25_keys = []
        self._bm25_corpus = []
        for name, entry in self._entries.items():
            if name in self._core_names:
                continue  # 核心工具不需要搜索
            # 构建文档:工具名 + 描述 + 关键词 + 类别
            doc = f"{name} {entry.tool.description} {' '.join(entry.keywords)} {entry.category}"
            tokens = _tokenize(doc)
            self._bm25_keys.append(name)
            self._bm25_corpus.append(tokens)
        if self._bm25_corpus:
            self._bm25 = BM25Okapi(self._bm25_corpus)
        else:
            self._bm25 = None

    def search(self, query: str, top_k: int = 10) -> list[ToolEntry]:
        """BM25 搜索工具。"""
        if not self._entries:
            return []
        if self._bm25 is None:
            self._build_bm25()
        if self._bm25 is None:
            return []

        query_tokens = _tokenize(query)
        scores = self._bm25.get_scores(query_tokens)

        # 按分数排序,取 top_k
        scored = sorted(zip(self._bm25_keys, scores), key=lambda x: x[1], reverse=True)
        results = []
        for name, score in scored[:top_k]:
            if score > 0:
                results.append(self._entries[name])
        return results

    def get_all_entries(self) -> dict[str, ToolEntry]:
        """获取所有注册的工具条目。"""
        return dict(self._entries)

    def resolve_tools(self, names: list) -> list:
        """将工具名/Tool对象混合列表解析为 Tool 对象列表。未找到的工具会被跳过并记录警告。"""
        resolved = []
        for item in names:
            if not isinstance(item, str):
                resolved.append(item)
            elif item in self._entries:
                resolved.append(self._entries[item].tool)
            else:
                from uniclaw.utils.logger import get_logger

                get_logger("registry").warning(f"工具 '{item}' 未在注册表中找到,已忽略")
        return resolved

    def get_core_names(self) -> set[str]:
        """获取核心工具名集合。"""
        return set(self._core_names)


# ── search_tools 元工具 ──────────────────────────────────────

MAX_LOADED_EXTENDED = 25  # 扩展工具最大加载数量
EXTENDED_TOOL_ENERGY_MAX = 30  # 扩展工具初始能量,每轮对话-1,被调用/搜索到恢复,为0时卸载


class ExtendedToolManager:
    """管理扩展工具的加载、能量和淘汰。

    能量机制:每个扩展工具有 EXTENDED_TOOL_ENERGY_MAX 点能量,
    每轮对话扣 1 点,被调用或搜索命中恢复满,归零自动卸载。
    """

    def __init__(self):
        self.loaded: list[str] = []  # MRU 顺序,最近使用在前
        self.energy: dict[str, int] = {}  # tool.name → 能量值
        self.pending_evicted: set[str] = set()  # 待清理的工具名
        self.pending_tools: list = []  # 待加载的 Tool 对象

    # ── 查询 ──

    @property
    def loaded_names(self) -> set[str]:
        """当前已加载的扩展工具名集合。"""
        return set(self.loaded)

    @property
    def slot_count(self) -> int:
        """已占用的槽位数。"""
        return len(self.loaded)

    # ── 工具使用 ──

    def touch(self, tool: Tool | str):
        """工具被使用或搜索命中:移到 MRU 端并恢复满能量。
        传入 Tool 对象时同时加入待加载队列(新工具首次加载)。核心工具不参与能量管理。"""
        if isinstance(tool, str):
            name = tool
        else:
            name = tool.name
            if name not in self.loaded and name not in CORE_TOOL_NAMES:
                self.pending_tools.append(tool)
        if name in CORE_TOOL_NAMES:
            return
        try:
            self.loaded.remove(name)
        except ValueError:
            pass
        self.loaded.insert(0, name)
        self.energy[name] = EXTENDED_TOOL_ENERGY_MAX

    # ── 加载与淘汰 ──

    def evict(self, names: list[str]):
        """淘汰工具:从 loaded 移除,清理能量,标记待清理。"""
        for name in names:
            if name in self.loaded:
                self.loaded.remove(name)
                print(f"evict {name}")
            self.energy.pop(name, None)
        self.pending_evicted.update(names)

    def drain_energy(self) -> list[str]:
        """每轮工具调用结束时调用:所有已加载工具能量-1,移除耗尽的工具。"""
        exhausted = []
        for name in list(self.loaded):
            e = self.energy.get(name, EXTENDED_TOOL_ENERGY_MAX) - 1
            if e <= 0:
                exhausted.append(name)
            else:
                self.energy[name] = e
        if exhausted:
            self.evict(exhausted)
        return exhausted

    # ── 应用变更到工具列表 ──

    def apply(self, tools: list, name2tool: dict):
        """加载待发现工具,清理被淘汰的工具。由 agent 循环调用。"""
        if self.pending_tools:
            for t in self.pending_tools:
                if t.name not in name2tool:
                    tools.append(t)
                    name2tool[t.name] = t
            self.pending_tools.clear()
        if self.pending_evicted:
            evicted = self.pending_evicted.copy()
            tools[:] = [t for t in tools if t.name not in evicted]
            for name in evicted:
                name2tool.pop(name, None)
            self.pending_evicted.clear()

    def restore_session(
        self,
        names: list[str],
        entries: dict[str, "ToolEntry"],
        name2tool: dict,
        tools: list,
        allowed: set[str],
    ):
        """从会话恢复扩展工具到工具列表。"""
        for name in names:
            if name not in name2tool:
                entry = entries.get(name)
                if entry and name in allowed:
                    tools.append(entry.tool)
                    name2tool[name] = entry.tool


@tool
def search_tools(query: str, config=None) -> str:
    """搜索可用的扩展工具。当你需要使用非常用工具时,先搜索再使用。
    搜索结果会自动加载到可用工具集中,下一轮即可调用。支持中英文关键词。
    优先使用工具名搜索(如 "screenshot"、"mouse_click")比用功能描述搜索更精准。

    Args:
        query: 搜索关键词,优先传入工具名,其次用功能描述(如 "screenshot"、"截图"、"定时任务")
    """
    registry = ToolRegistry.get_instance()
    results = registry.search(query)
    if not results:
        return f"未找到匹配 '{query}' 的工具。尝试其他关键词。"
    # 只保留当前允许的工具(由 run() 在启动时计算,包含模块启用状态和子代理白名单)
    task = config.current_agent
    mgr: ExtendedToolManager = task.extended_mgr
    available = [e for e in results if e.tool.name in task.allowed_tools_set]
    blocked = [e for e in results if e.tool.name not in task.allowed_tools_set]
    # 为本次搜索命中的已加载工具恢复能量
    matched_names = {e.tool.name for e in available}
    for name in list(mgr.loaded):
        if name in matched_names:
            mgr.touch(name)
    # 过滤掉已加载的扩展工具
    new_available = [e for e in available if e.tool.name not in mgr.loaded_names]
    if not new_available:
        if not available:
            lines = [f"未找到匹配 '{query}' 的可用工具。"]
            if blocked:
                lines.append(
                    f"以下 {len(blocked)} 个工具存在但当前不可用(未启用或无权限):"
                )
                for entry in blocked:
                    lines.append(f"- {entry.tool.name}")
            lines.append("尝试其他关键词。")
            return "\n".join(lines)
        # 所有匹配的工具都已加载
        for entry in available:
            mgr.touch(entry.tool.name)
        return f"匹配到 {len(available)} 个工具,均已加载: {', '.join(e.tool.name for e in available)}"
    # LRU 淘汰:计算可新增的数量
    slots_available = MAX_LOADED_EXTENDED - mgr.slot_count
    evicted_names: list[str] = []
    if slots_available <= 0:
        evict_count = min(len(new_available), mgr.slot_count)
        evicted_names = mgr.loaded[-evict_count:]
        del mgr.loaded[-evict_count:]
        slots_available = evict_count
    elif len(new_available) > slots_available:
        evict_count = len(new_available) - slots_available
        evicted_names = mgr.loaded[-evict_count:]
        del mgr.loaded[-evict_count:]
        slots_available = len(new_available)
    if evicted_names:
        mgr.evict(evicted_names)
    # 只加载能放下的数量
    to_load = new_available[:slots_available]
    for entry in reversed(to_load):
        mgr.touch(entry.tool)
    # 生成结果消息
    lines = [f"找到 {len(to_load)} 个新工具(已加载到可用工具集):"]
    for entry in to_load:
        lines.append(f"- {entry.tool.name}: {entry.tool.description}")
    if evicted_names:
        lines.append(
            f"\n♻️ 已淘汰 {len(evicted_names)} 个久未使用的工具: {', '.join(evicted_names)}"
        )
    skipped_count = len(new_available) - len(to_load)
    if skipped_count > 0:
        lines.append(
            f"\n⚠️ 还有 {skipped_count} 个工具因上限({MAX_LOADED_EXTENDED})未加载,可通过再次搜索加载。"
        )
    if blocked:
        lines.append(f"\n以下 {len(blocked)} 个工具当前不可用(未启用或无权限):")
        for entry in blocked:
            lines.append(f"- {entry.tool.name}")
    return "\n".join(lines)


def get_tools() -> list:
    """获取 search_tools 元工具。"""
    return [search_tools]


# search_tools 是元工具,始终可用,不参与能量管理
CORE_TOOL_NAMES.add(search_tools.name)


async def get_registry_system_prompt(config=None) -> str:
    """生成扩展工具的系统提示词。"""
    from . import _ensure_registry

    await _ensure_registry()
    registry = ToolRegistry.get_instance()
    # 子代理只展示可用的扩展工具
    allowed = None
    if config and config.is_sub:
        allowed = config.current_agent.allowed_tools_set
    categories: dict[str, list[tuple[str, str]]] = {}
    for name, entry in registry.get_all_entries().items():
        if name not in registry.get_core_names():
            if allowed is not None and name not in allowed:
                continue
            short_desc = entry.tool.description.split("。")[0].split(".")[0]
            categories.setdefault(entry.category, []).append((name, short_desc))
    if not categories:
        return ""
    cat_lines = []
    for cat, tools in categories.items():
        # 前5个显示 工具名(简短描述), 后续只显示工具名
        parts = [f"{name}({desc})" for name, desc in tools[:5]]
        parts.extend(name for name, _ in tools[5:])
        cat_lines.append(f"  - {cat}: {', '.join(parts)}")
    return (
        "# 扩展工具\n"
        f"你有 {search_tools.name} 工具可用于发现以下扩展工具(按需搜索加载):\n"
        + "\n".join(cat_lines)
        + f"\n⚠️ 重要规则:当需要使用上面列出的工具时,必须先调用 {search_tools.name} 搜索加载。"
        + "在搜索之前禁止说'我无法做到'、'我没有这个能力'、'我是文本AI'等拒绝性话语。"
    )


# ── 注册表初始化 ──────────────────────────────────────────────


def init_registry(all_tools: list[Tool]):
    """从全量工具列表初始化注册表。

    Args:
        all_tools: 所有工具的 Tool 对象列表
    """
    registry = ToolRegistry.get_instance()
    keywords_map = _build_extended_keywords()
    categories_map = _build_tool_categories()
    for t in all_tools:
        is_core = t.name in CORE_TOOL_NAMES
        keywords = keywords_map.get(t.name, [])
        category = categories_map.get(t.name, "mcp")
        registry.register(t, keywords, category, is_core=is_core)
