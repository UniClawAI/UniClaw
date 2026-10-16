/* a2ui-renderer.js — A2UI (Agent-to-UI) 声明式组件渲染器(UniClaw 子集)。
 * Agent 在 markdown 中输出 ```a2ui {json} ``` 代码块,渲染器将其替换为可交互组件。
 * 安全模型:仅白名单组件 + textContent 赋值,未知组件/非法 JSON 保持代码块原样。
 */
const A2UI = (() => {
    const VARIANTS = new Set(['h1', 'h2', 'body', 'caption']);
    const TONES = new Set(['info', 'success', 'warn', 'danger']);
    const BTN_VARIANTS = new Set(['primary', 'default', 'danger']);

    function el(tag, cls, text) {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }

    function renderChildren(nodes) {
        const frag = document.createDocumentFragment();
        (Array.isArray(nodes) ? nodes : []).forEach(node => {
            const rendered = renderComponent(node);
            if (rendered) frag.appendChild(rendered);
        });
        return frag;
    }

    function renderComponent(node) {
        if (!node || typeof node !== 'object' || typeof node.type !== 'string') return null;
        const fn = COMPONENTS[node.type];
        if (!fn) return null;
        try {
            return fn(node);
        } catch (_) {
            return null;
        }
    }

    // ============================================================
    //  组件目录(对齐 A2UI 基元,UniClaw 子集)
    // ============================================================
    const COMPONENTS = {
        // ---- 布局 ----
        Column(node) {
            const d = el('div', 'a2ui-col');
            if (typeof node.gap === 'number' && node.gap >= 0 && node.gap <= 64) d.style.gap = node.gap + 'px';
            d.appendChild(renderChildren(node.children));
            return d;
        },
        Row(node) {
            const d = el('div', 'a2ui-row');
            if (typeof node.gap === 'number' && node.gap >= 0 && node.gap <= 64) d.style.gap = node.gap + 'px';
            d.appendChild(renderChildren(node.children));
            return d;
        },
        // ---- 展示 ----
        Text(node) {
            const variant = VARIANTS.has(node.variant) ? node.variant : 'body';
            const d = el('div', 'a2ui-text a2ui-text-' + variant);
            d.textContent = node.content ?? '';
            if (typeof node.color === 'string' && /^[\w#,().\s-]{1,40}$/.test(node.color)) d.style.color = node.color;
            return d;
        },
        Badge(node) {
            const tone = TONES.has(node.tone) ? node.tone : 'info';
            const d = el('span', 'a2ui-badge a2ui-badge-' + tone);
            d.textContent = node.content ?? '';
            return d;
        },
        Divider() {
            return el('div', 'a2ui-divider');
        },
        Progress(node) {
            const value = Math.max(0, Math.min(100, Number(node.value) || 0));
            const d = el('div', 'a2ui-progress');
            const track = el('div', 'a2ui-progress-track');
            const fill = el('div', 'a2ui-progress-fill');
            fill.style.width = value + '%';
            track.appendChild(fill);
            d.appendChild(track);
            if (node.label !== undefined) {
                const label = el('div', 'a2ui-progress-label');
                const t = String(node.label);
                label.textContent = t;
                if (!/[%％]/.test(t)) label.textContent = t + ' ' + Math.round(value) + '%';
                d.appendChild(label);
            }
            return d;
        },
        // ---- 容器 ----
        Card(node) {
            const d = el('div', 'a2ui-card');
            if (node.title !== undefined) d.appendChild(el('div', 'a2ui-card-title', String(node.title)));
            const body = el('div', 'a2ui-card-body');
            body.appendChild(renderChildren(node.children));
            d.appendChild(body);
            if (node.footer !== undefined) {
                const f = el('div', 'a2ui-card-footer');
                f.appendChild(renderChildren(Array.isArray(node.footer) ? node.footer : [node.footer]));
                d.appendChild(f);
            }
            return d;
        },
        Tabs(node) {
            const d = el('div', 'a2ui-tabs');
            const tabs = Array.isArray(node.tabs) ? node.tabs : [];
            if (!tabs.length) return d;
            const nav = el('div', 'a2ui-tabs-nav');
            const panes = [];
            tabs.forEach((tab, i) => {
                const label = tab && typeof tab.label === 'string' ? tab.label : 'Tab ' + (i + 1);
                const btn = el('button', 'a2ui-tabs-tab' + (i === 0 ? ' active' : ''), label);
                btn.type = 'button';
                const pane = el('div', 'a2ui-tabs-pane' + (i === 0 ? ' active' : ''));
                pane.appendChild(renderChildren(tab && tab.children));
                panes.push(pane);
                btn.onclick = () => {
                    nav.querySelectorAll('.a2ui-tabs-tab').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    panes.forEach(p => p.classList.remove('active'));
                    pane.classList.add('active');
                };
                nav.appendChild(btn);
            });
            d.appendChild(nav);
            panes.forEach(p => d.appendChild(p));
            return d;
        },
        // ---- 交互 ----
        Button(node) {
            const variant = BTN_VARIANTS.has(node.variant) ? node.variant : 'default';
            const b = el('button', 'a2ui-btn a2ui-btn-' + variant, node.label ?? '按钮');
            b.type = 'button';
            b.onclick = () => handleAction(node.action, b);
            return b;
        },
        TextField(node) {
            const wrap = el('label', 'a2ui-field');
            if (node.label !== undefined) wrap.appendChild(el('span', 'a2ui-field-label', String(node.label)));
            const input = el('input', 'a2ui-field-input');
            input.type = 'text';
            if (node.placeholder !== undefined) input.placeholder = String(node.placeholder);
            if (node.value !== undefined) input.value = String(node.value);
            if (node.name) { input.dataset.a2uiField = String(node.name); input.name = String(node.name); }
            wrap.appendChild(input);
            return wrap;
        },
        Checkbox(node) {
            const wrap = el('label', 'a2ui-field a2ui-field-checkbox');
            const input = el('input', 'a2ui-field-input');
            input.type = 'checkbox';
            input.checked = !!node.checked;
            if (node.name) { input.dataset.a2uiField = String(node.name); input.name = String(node.name); }
            wrap.appendChild(input);
            if (node.label !== undefined) wrap.appendChild(el('span', 'a2ui-field-label', String(node.label)));
            return wrap;
        },
        Select(node) {
            const wrap = el('label', 'a2ui-field');
            if (node.label !== undefined) wrap.appendChild(el('span', 'a2ui-field-label', String(node.label)));
            const sel = el('select', 'a2ui-field-input');
            (Array.isArray(node.options) ? node.options : []).forEach(opt => {
                const o = document.createElement('option');
                o.value = String(opt.value ?? opt.label ?? '');
                o.textContent = opt.label !== undefined ? String(opt.label) : String(opt.value ?? '');
                if (node.value !== undefined && String(opt.value ?? opt.label) === String(node.value)) o.selected = true;
                sel.appendChild(o);
            });
            if (node.name) { sel.dataset.a2uiField = String(node.name); sel.name = String(node.name); }
            wrap.appendChild(sel);
            return wrap;
        },
    };

    // ============================================================
    //  动作处理
    // ============================================================
    function handleAction(action, btn) {
        if (!action) return;
        let type = null, payload = null;
        if (typeof action === 'string') {
            if (action === 'copy') { type = 'copy'; payload = null; }
            else return;
        } else if (action && typeof action === 'object') {
            type = action.type;
            payload = action.payload;
            if (type === 'copy' && action.text !== undefined) payload = action.text;
        } else return;

        if (type === 'copy') {
            const text = payload !== null && payload !== undefined ? String(payload) : '';
            if (text && navigator.clipboard) {
                navigator.clipboard.writeText(text).then(() => {
                    if (typeof Utils !== 'undefined' && Utils.showToast) Utils.showToast('已复制');
                }).catch(() => {});
            }
            return;
        }
        if (type === 'link') {
            const url = typeof payload === 'string' && payload ? payload : action.url;
            if (url && /^https?:\/\//i.test(String(url))) window.open(String(url), '_blank', 'noopener');
            return;
        }
        if (type === 'agent') {
            const fields = collectFields(btn);
            const msg = {
                type: 'a2ui_action',
                session_id: (typeof Chat !== 'undefined' && Chat.currentSessionId) || '',
                payload: typeof payload === 'string' ? payload : payload,
                fields: fields,
            };
            if (typeof WS !== 'undefined' && WS.socket && WS.socket.readyState === WebSocket.OPEN) {
                WS.socket.send(JSON.stringify(msg));
                btn.classList.add('clicked');
                const prev = btn.textContent;
                btn.textContent = '已发送 ✓';
                setTimeout(() => { btn.textContent = prev; btn.classList.remove('clicked'); }, 1200);
            } else {
                if (typeof Utils !== 'undefined' && Utils.showToast) Utils.showToast('会话未连接');
            }
            return;
        }
    }

    /** 收集所在组件区域内的表单字段值(仅 [data-a2ui-field] 白名单)。 */
    function collectFields(btn) {
        const root = btn.closest('.a2ui-surface') || btn.closest('.markdown-body') || btn.parentElement;
        if (!root) return null;
        const fields = {};
        root.querySelectorAll('[data-a2ui-field]').forEach(input => {
            const name = input.dataset.a2uiField;
            if (!name) return;
            fields[name] = input.type === 'checkbox' ? input.checked : input.value;
        });
        return Object.keys(fields).length ? fields : null;
    }

    // ============================================================
    //  对外 API
    // ============================================================
    /** 渲染 A2UI JSON 根对象为组件容器 DOM;失败返回 null。 */
    function renderSurface(json) {
        if (!json || typeof json !== 'object') return null;
        const root = json.root && typeof json.root === 'object' ? json.root : json;
        const node = renderComponent(root);
        if (!node) return null;
        const surface = el('div', 'a2ui-surface');
        surface.appendChild(node);
        return surface;
    }

    /** 扫描容器内 marked 渲染出的 pre>code.language-a2ui,替换为组件;
     *  非法 JSON / 未知组件保持原代码块显示。 */
    function enhance(root) {
        if (!root) return;
        let codes;
        try {
            codes = root.querySelectorAll('pre > code.language-a2ui');
        } catch (_) {
            return;
        }
        codes.forEach(code => {
            const pre = code.parentElement;
            if (!pre) return;
            let json;
            try {
                json = JSON.parse(code.textContent || '');
            } catch (_) {
                return; // 非法 JSON:保留代码块,避免页面崩溃
            }
            const surface = renderSurface(json);
            if (!surface) return; // 未知组件:保留代码块
            pre.replaceWith(surface);
        });
    }

    return { renderSurface, enhance };
})();