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
    //  图表辅助(SVG,纯 DOM API,无 innerHTML 拼接)
    // ============================================================
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const CHART_COLORS = ['#0078d4', '#07b364', '#fce100', '#d13438', '#f7630c', '#2899f5', '#adadad'];

    function svgEl(tag, attrs) {
        const n = document.createElementNS(SVG_NS, tag);
        if (attrs) {
            for (const k in attrs) n.setAttribute(k, String(attrs[k]));
        }
        return n;
    }

    function chartNumbers(values) {
        return (Array.isArray(values) ? values : []).map(v => Number(v)).filter(v => Number.isFinite(v) && v >= 0);
    }

    function chartLegendItem(name, color) {
        const item = el('span', 'a2ui-chart-legend-item');
        const dot = el('i', 'a2ui-chart-legend-dot');
        dot.style.background = color;
        item.appendChild(dot);
        item.appendChild(el('span', '', name));
        return item;
    }

    /** 柱状图 / 折线图(共轴布局) */
    function renderAxisChart(node, chartType, labels, series) {
        const plot = {
            width: 640, height: 220,
            left: 12, right: 12, top: 12, bottom: 40,
        };
        const plotW = plot.width - plot.left - plot.right;
        const plotH = plot.height - plot.top - plot.bottom;
        // 收集所有有效值,计算最大值
        const allValues = [];
        series.forEach(s => allValues.push(...chartNumbers(s.values)));
        const maxVal = Math.max(...allValues);
        if (!Number.isFinite(maxVal) || maxVal <= 0) return chartEmpty(node);
        const scale = (v) => v / maxVal;

        const svg = svgEl('svg', { viewBox: `0 0 ${plot.width} ${plot.height}`, class: 'a2ui-chart-svg' });
        // 横向网格线(每 25%)
        for (let i = 0; i <= 4; i++) {
            const y = plot.top + plotH - (plotH * i / 4);
            svg.appendChild(svgEl('line', {
                x1: plot.left, y1: y, x2: plot.left + plotW, y2: y,
                stroke: 'rgba(255,255,255,0.07)', 'stroke-width': 1,
            }));
            const tick = svgEl('text', { x: plot.left + plotW + 4, y: y + 3, 'font-size': 9, fill: '#8a8a8a' });
            tick.textContent = Math.round(maxVal * i / 4);
            svg.appendChild(tick);
        }
        // 类目轴标签
        const step = labels.length ? plotW / labels.length : 0;
        labels.forEach((label, i) => {
            const cx = plot.left + (i + 0.5) * step;
            const t = svgEl('text', { x: cx, y: plot.top + plotH + 16, 'font-size': 10, fill: '#adadad', 'text-anchor': 'middle' });
            t.textContent = label.length > 8 ? label.slice(0, 8) + '…' : label;
            svg.appendChild(t);
        });

        if (chartType === 'line') {
            series.forEach((s, si) => {
                const color = CHART_COLORS[si % CHART_COLORS.length];
                const nums = chartNumbers(s.values);
                if (!nums.length) return;
                const pts = nums.map((v, i) => {
                    const x = plot.left + (i + 0.5) * step;
                    const y = plot.top + plotH - scale(v) * plotH;
                    return [x, y];
                });
                const poly = svgEl('polyline', {
                    points: pts.map(p => p.map(n => n.toFixed(1)).join(',')).join(' '),
                    fill: 'none', stroke: color, 'stroke-width': 2,
                    'stroke-linejoin': 'round', 'stroke-linecap': 'round',
                });
                svg.appendChild(poly);
                pts.forEach((p, i) => {
                    const c = svgEl('circle', { cx: p[0], cy: p[1], r: 3, fill: color });
                    svg.appendChild(c);
                    if (nums.length <= 12) {
                        const v = svgEl('text', { x: p[0], y: p[1] - 7, 'font-size': 9, fill: '#adadad', 'text-anchor': 'middle' });
                        v.textContent = String(nums[i]);
                        svg.appendChild(v);
                    }
                });
            });
        } else {
            const groupW = labels.length ? plotW / labels.length : plotW;
            const barCount = Math.max(1, series.length);
            const barW = Math.max(2, Math.min(32, groupW * 0.8 / barCount));
            series.forEach((s, si) => {
                const color = CHART_COLORS[si % CHART_COLORS.length];
                const nums = chartNumbers(s.values);
                nums.forEach((v, i) => {
                    const gx = plot.left + i * groupW + (groupW * 0.1) + si * (barW + 1);
                    const h = Math.max(0, scale(v) * plotH);
                    const y = plot.top + plotH - h;
                    svg.appendChild(svgEl('rect', { x: gx, y: y, width: barW, height: h, fill: color, rx: 2 }));
                    if (nums.length <= 12 && barW >= 10) {
                        const vTxt = svgEl('text', { x: gx + barW / 2, y: y - 3, 'font-size': 9, fill: '#adadad', 'text-anchor': 'middle' });
                        vTxt.textContent = String(v);
                        svg.appendChild(vTxt);
                    }
                });
            });
        }

        const wrap = el('div', 'a2ui-chart');
        if (node.title) wrap.appendChild(el('div', 'a2ui-chart-title', String(node.title)));
        wrap.appendChild(svg);
        const legend = el('div', 'a2ui-chart-legend');
        series.forEach((s, si) => {
            const name = s && typeof s.name === 'string' && s.name ? s.name : ('系列' + (si + 1));
            legend.appendChild(chartLegendItem(name, CHART_COLORS[si % CHART_COLORS.length]));
        });
        wrap.appendChild(legend);
        return wrap;
    }

    /** 饼图(单系列) */
    function renderPie(node, labels, values) {
        const nums = chartNumbers(values);
        const total = nums.reduce((a, b) => a + b, 0);
        if (!total || !nums.length) return chartEmpty(node);
        const size = 180, cx = size / 2, cy = size / 2, r = 62;
        const svg = svgEl('svg', { viewBox: `0 0 ${size} ${size}`, class: 'a2ui-chart-svg' });
        let angle = -Math.PI / 2;
        nums.forEach((v, i) => {
            const sweep = v / total * Math.PI * 2;
            const x1 = cx + r * Math.cos(angle), y1 = cy + r * Math.sin(angle);
            angle += sweep;
            const x2 = cx + r * Math.cos(angle), y2 = cy + r * Math.sin(angle);
            const large = sweep > Math.PI ? 1 : 0;
            const color = CHART_COLORS[i % CHART_COLORS.length];
            const path = svgEl('path', {
                d: `M${cx},${cy} L${x1.toFixed(1)},${y1.toFixed(1)} A${r},${r} 0 ${large} 1 ${x2.toFixed(1)},${y2.toFixed(1)} Z`,
                fill: color,
            });
            svg.appendChild(path);
            // 百分比标签(扇区足够大时)
            if (v / total >= 0.06) {
                const mid = angle - sweep / 2;
                const lx = cx + r * 0.62 * Math.cos(mid), ly = cy + r * 0.62 * Math.sin(mid);
                const t = svgEl('text', { x: lx, y: ly + 3, 'font-size': 11, fill: '#141414', 'text-anchor': 'middle', 'font-weight': 600 });
                t.textContent = Math.round(v / total * 100) + '%';
                svg.appendChild(t);
            }
        });
        const wrap = el('div', 'a2ui-chart');
        if (node.title) wrap.appendChild(el('div', 'a2ui-chart-title', String(node.title)));
        wrap.appendChild(svg);
        const legend = el('div', 'a2ui-chart-legend');
        nums.forEach((v, i) => {
            const name = labels[i] && String(labels[i]) ? String(labels[i]) : ('项目' + (i + 1));
            const pct = Math.round(v / total * 100);
            legend.appendChild(chartLegendItem(`${name} ${pct}%`, CHART_COLORS[i % CHART_COLORS.length]));
        });
        wrap.appendChild(legend);
        return wrap;
    }

    /** 图表无有效数据时的占位(不回退代码块,给出友好提示) */
    function chartEmpty(node) {
        const wrap = el('div', 'a2ui-chart');
        if (node.title) wrap.appendChild(el('div', 'a2ui-chart-title', String(node.title)));
        const hint = el('div', 'a2ui-chart-empty');
        hint.textContent = '⚠️ 图表数据为空或无效';
        wrap.appendChild(hint);
        return wrap;
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
        Chart(node) {
            const data = node && typeof node.data === 'object' ? node.data : {};
            const labels = Array.isArray(data.labels) ? data.labels.filter(l => l != null).map(String) : [];
            const series = Array.isArray(data.series) ? data.series : [];
            const chartType = node.chart === 'line' ? 'line' : node.chart === 'pie' ? 'pie' : 'bar';
            if (chartType === 'pie') {
                const values = series[0] && Array.isArray(series[0].values)
                    ? series[0].values
                    : (Array.isArray(data.values) ? data.values : []);
                return renderPie(node, labels, values);
            }
            return renderAxisChart(node, chartType, labels, series);
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