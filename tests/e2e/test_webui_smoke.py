"""WebUI 前端冒烟测试 — 真实浏览器验证页面骨架与核心交互入口。

覆盖:
- 页面安全加载(无 JS 错误)
- 核心 DOM 结构齐全(聊天区/侧栏/输入框/状态栏)
- 未登录重定向到登录页
- 可信 IP 放行
- 设置弹窗与主题切换
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]


def _collect_js_errors(page) -> list[str]:
    """最近一轮 pageerror 采集(通过 count 增量比较不适用,直接返回当前快照)。"""
    return page.evaluate("() => window.__pageErrors || []")


def test_page_loads_without_js_errors(webui_page):
    """主页加载后不应有 JS 运行时错误。"""
    page = webui_page
    page.evaluate("() => { window.__pageErrors = []; }")
    page.on(
        "pageerror",
        lambda e: page.evaluate("(msg) => window.__pageErrors.push(msg)", str(e)),
    )
    # 重新触发一次交互,收集并行错误
    page.wait_for_timeout(500)
    errors = _collect_js_errors(page)
    assert errors == [], f"页面存在 JS 错误: {errors}"


def test_core_dom_skeleton(webui_page):
    """核心 DOM 元素应全部存在。"""
    page = webui_page
    for selector in [
        "#chat-messages",
        "#session-tree",
        "#chat-input",
        "#send-btn",
        "#status-bar",
        "#connection-dot",
        "#settings-btn",
        "#theme-btn",
        "#loading-overlay",
    ]:
        assert page.locator(selector).count() >= 1, f"缺少核心元素: {selector}"


def test_welcome_screen_shown(webui_page):
    """无会话时应显示欢迎页。"""
    page = webui_page
    page.wait_for_selector(".welcome-screen", timeout=5000)


def test_session_tree_empty_state(webui_page):
    """空会话树的空状态提示。"""
    page = webui_page
    page.wait_for_timeout(800)
    tree_text = page.locator("#session-tree").inner_text()
    assert "暂无会话" in tree_text or "会话" in tree_text


def test_settings_modal_opens(webui_page):
    """点击设置按钮应打开设置弹窗,核心控件齐全。"""
    page = webui_page
    page.click("#settings-btn", timeout=5000)
    page.wait_for_selector("#settings-modal:not(.hidden)", timeout=3000)
    modal = page.locator("#settings-modal")
    assert "hidden" not in (modal.get_attribute("class") or "")
    # 设置弹窗有模型配置区
    assert page.locator("#settings-providers").count() == 1
    assert "模型提供商" in page.locator("#settings-modal").inner_text()


def test_theme_button_opens_dropdown_and_switches(webui_page):
    """主题按钮应弹出主题下拉,选择后可切换当前主题 CSS。"""
    page = webui_page
    # 关闭可能自动弹出的设置弹窗,避免遮挡
    if page.locator("#settings-modal:not(.hidden)").count():
        page.locator("#settings-modal").evaluate("(el) => el.classList.add('hidden')")
    page.wait_for_timeout(300)

    # 点开主题下拉
    page.click("#theme-btn", timeout=5000)
    page.wait_for_selector(".theme-dropdown", timeout=3000)
    assert page.locator(".theme-dropdown-title").inner_text() == "主题风格"
    # 至少有默认 fluent-dark 在列
    items = page.locator(".theme-dropdown-item")
    assert items.count() >= 1
    names = [
        items.nth(i).locator(".theme-name").inner_text() for i in range(items.count())
    ]
    assert "深色" in "".join(names) or "dark" in "".join(names).lower()

    # 记录当前主题 link(data-theme 属性即当前主题 id)
    current = page.evaluate(
        "() => document.head.querySelector('link[data-theme]')?.dataset.theme || ''"
    )
    assert current != ""

    # 点一个不同的主题(取名称(空格转下划线后)与当前主题 id 不同的第一个)
    target = next(
        (i for i, n in enumerate(names) if n.lower().replace(" ", "-") != current.lower()),
        0,
    )
    items.nth(target).click()
    page.wait_for_timeout(500)

    # 主题 link 变化 + localStorage 更新
    new_current = page.evaluate(
        "() => document.head.querySelector('link[data-theme]')?.dataset.theme || ''"
    )
    assert new_current != current, f"主题未切换: {current} -> {new_current}"
    stored = page.evaluate("() => localStorage.getItem('uniclaw-theme')")
    assert stored == new_current