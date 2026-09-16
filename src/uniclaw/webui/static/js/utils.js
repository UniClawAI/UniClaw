/* utils.js — 工具函数 */

const Utils = {
    // ============================================================
    //  Markdown 渲染
    // ============================================================

    /** 初始化 marked + highlight.js */
    initMarkdown() {
        if (typeof marked === 'undefined') return;
        marked.setOptions({
            highlight: (code, lang) => {
                if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
                    try { return hljs.highlight(code, { language: lang }).value; }
                    catch (_) {}
                }
                if (typeof hljs !== 'undefined') {
                    try { return hljs.highlightAuto(code).value; }
                    catch (_) {}
                }
                return code;
            },
            breaks: true,
            gfm: true,
        });
    },

    /** 渲染 Markdown 为 HTML */
    renderMarkdown(text) {
        if (!text) return '';
        if (typeof marked === 'undefined') return this.escapeHtml(text).replace(/\n/g, '<br>');
        try {
            const html = marked.parse(text);
            // 消毒:LLM 输出/历史消息中的原生 HTML(如 <img onerror>)不得执行。
            // DOMPurify 缺失(CDN 故障)时不能直接返回未消毒的 html(fail-open 等于放弃防护),
            // 退化为纯文本转义,保证任何路径都不会执行原始 HTML
            if (typeof DOMPurify !== 'undefined') return DOMPurify.sanitize(html);
            return this.escapeHtml(text).replace(/\n/g, '<br>');
        } catch (_) {
            return this.escapeHtml(text).replace(/\n/g, '<br>');
        }
    },

    // ============================================================
    //  HTML 转义
    // ============================================================

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        // textContent→innerHTML 只转义 & < >,不转义引号;补上才能安全用于属性上下文
        return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },

    /** IME 组合中按 Enter 应只上屏,不触发提交/选中。所有 Enter 处理器统一用此判断 */
    isImeComposing(e) {
        return !!(e.isComposing || e.keyCode === 229);
    },

    // ============================================================
    //  格式化
    // ============================================================

    /** 格式化工具参数 */
    formatArgs(args, maxLen = 80) {
        if (!args) return '';
        const str = typeof args === 'string' ? args : JSON.stringify(args);
        if (str.length <= maxLen) return str;
        return str.substring(0, maxLen) + '...';
    },

    /** 格式化时间 */
    formatTime(ts) {
        if (!ts) return '';
        const d = new Date(ts.replace ? ts.replace(' ', 'T') : ts);
        if (isNaN(d.getTime())) return ts;
        const pad = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    },

    /** 格式化相对时间 */
    formatRelativeTime(ts) {
        if (!ts) return '';
        const d = new Date(ts.replace ? ts.replace(' ', 'T') : ts);
        if (isNaN(d.getTime())) return ts;
        const diff = (Date.now() - d.getTime()) / 1000;
        if (diff < 60) return '刚刚';
        if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
        if (diff < 604800) return `${Math.floor(diff / 86400)}天前`;
        return this.formatTime(ts);
    },

    /** 格式化 token 统计 */
    formatTokens(usage) {
        if (!usage) return 'Tokens: -';
        const inp = usage.input_tokens || usage.in_tokens || 0;
        const out = usage.output_tokens || usage.out_tokens || 0;
        const total = inp + out;
        if (total >= 1000) return `Tokens: ${(total / 1000).toFixed(1)}k`;
        return `Tokens: ${total}`;
    },

    /** 格式化 token 数 */
    formatTokenNum(n) {
        if (n == null) return '-';
        if (n < 1000) return String(n);
        if (n < 1000000) return (n / 1000).toFixed(1) + 'k';
        return (n / 1000000).toFixed(2) + 'M';
    },

    /** 截断文本 */
    truncate(text, maxLen = 100) {
        if (!text || text.length <= maxLen) return text || '';
        return text.substring(0, maxLen) + '...';
    },

    /** 格式化秒数 */
    formatSeconds(s) {
        if (s < 60) return `${s}s`;
        const m = Math.floor(s / 60);
        const sec = s % 60;
        return sec ? `${m}m${sec}s` : `${m}m`;
    },

    // ============================================================
    //  Toast 通知
    // ============================================================

    _toastContainer: null,

    _getToastContainer() {
        if (!this._toastContainer) {
            this._toastContainer = document.getElementById('toast-container');
            if (!this._toastContainer) {
                this._toastContainer = document.createElement('div');
                this._toastContainer.id = 'toast-container';
                document.body.appendChild(this._toastContainer);
            }
        }
        return this._toastContainer;
    },

    showToast(message, duration = 3000) {
        const container = this._getToastContainer();
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.innerHTML = `<span>${icon('info')}</span><span>${this.escapeHtml(message)}</span>`;
        container.appendChild(toast);
        setTimeout(() => {
            toast.classList.add('fade-out');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    },

    showError(message, duration = 5000) {
        const container = this._getToastContainer();
        const toast = document.createElement('div');
        toast.className = 'toast error';
        toast.innerHTML = `<span>${icon('warning')}</span><span>${this.escapeHtml(message)}</span>`;
        container.appendChild(toast);
        setTimeout(() => {
            toast.classList.add('fade-out');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    },

    showSuccess(message) {
        const container = this._getToastContainer();
        const toast = document.createElement('div');
        toast.className = 'toast success';
        toast.innerHTML = `<span>${icon('check')}</span><span>${this.escapeHtml(message)}</span>`;
        container.appendChild(toast);
        setTimeout(() => {
            toast.classList.add('fade-out');
            setTimeout(() => toast.remove(), 300);
        }, 2500);
    },

    // ============================================================
    //  Loading
    // ============================================================

    showLoading(text = '加载中...') {
        const overlay = document.getElementById('loading-overlay');
        const textEl = document.getElementById('loading-text');
        if (overlay) {
            overlay.classList.remove('hidden');
            if (textEl) textEl.textContent = text;
        }
    },

    hideLoading() {
        const overlay = document.getElementById('loading-overlay');
        if (overlay) overlay.classList.add('hidden');
    },

    // ============================================================
    //  快捷键提示
    // ============================================================


    // ============================================================
    //  确认对话框
    // ============================================================

    confirm(message) {
        return new Promise(resolve => {
            const overlay = document.createElement('div');
            overlay.className = 'confirm-overlay';
            overlay.innerHTML = `
                <div class="confirm-box">
                    <div class="confirm-title">确认操作</div>
                    <div class="confirm-message">${this.escapeHtml(message)}</div>
                    <div class="confirm-actions">
                        <button class="btn btn-secondary confirm-cancel">取消</button>
                        <button class="btn btn-danger confirm-ok">确认</button>
                    </div>
                </div>
            `;
            document.body.appendChild(overlay);
            const cleanup = (result) => { overlay.remove(); resolve(result); };
            overlay.querySelector('.confirm-cancel').onclick = () => cleanup(false);
            overlay.querySelector('.confirm-ok').onclick = () => cleanup(true);
        });
    },

    // ============================================================
    //  Diff 渲染
    // ============================================================

    renderDiff(oldText, newText, mode = 'unified') {
        const ops = this._computeDiff((oldText || '').split('\n'), (newText || '').split('\n'));
        if (mode === 'split') return this._renderSplitDiff(ops);
        return this._renderUnifiedDiff(ops);
    },

    /** LCS diff: 返回 [{type:'context'|'remove'|'add', line:string}] */
    _computeDiff(oldLines, newLines) {
        const m = oldLines.length, n = newLines.length;
        // LCS DP
        const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
        for (let i = 1; i <= m; i++)
            for (let j = 1; j <= n; j++)
                dp[i][j] = oldLines[i - 1] === newLines[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
        // 回溯
        const ops = [];
        let i = m, j = n;
        while (i > 0 || j > 0) {
            if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
                ops.push({ type: 'context', line: oldLines[i - 1] }); i--; j--;
            } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
                ops.push({ type: 'add', line: newLines[j - 1] }); j--;
            } else {
                ops.push({ type: 'remove', line: oldLines[i - 1] }); i--;
            }
        }
        return ops.reverse();
    },

    _renderUnifiedDiff(ops) {
        let html = '<div class="diff-container">';
        for (const op of ops) {
            const cls = op.type === 'add' ? 'add' : op.type === 'remove' ? 'remove' : 'context';
            const prefix = op.type === 'add' ? '+' : op.type === 'remove' ? '-' : ' ';
            html += `<div class="diff-line ${cls}">${prefix} ${this.escapeHtml(op.line)}</div>`;
        }
        html += '</div>';
        return html;
    },

    _renderSplitDiff(ops) {
        let leftHtml = '', rightHtml = '';
        for (const op of ops) {
            if (op.type === 'context') {
                leftHtml += `<div class="diff-line context">${this.escapeHtml(op.line)}</div>`;
                rightHtml += `<div class="diff-line context">${this.escapeHtml(op.line)}</div>`;
            } else if (op.type === 'remove') {
                leftHtml += `<div class="diff-line remove">${this.escapeHtml(op.line)}</div>`;
                rightHtml += `<div class="diff-line context">&nbsp;</div>`;
            } else {
                leftHtml += `<div class="diff-line context">&nbsp;</div>`;
                rightHtml += `<div class="diff-line add">${this.escapeHtml(op.line)}</div>`;
            }
        }
        return `<div class="diff-container" style="display:grid;grid-template-columns:1fr 1fr;gap:1px"><div>${leftHtml}</div><div>${rightHtml}</div></div>`;
    },

    /** 渲染 unified diff 文本 (来自 git diff) */
    renderUnifiedDiffText(diffText) {
        if (!diffText) return '<div class="diff-container"><p style="color:var(--text-3)">无差异</p></div>';
        const lines = diffText.split('\n');
        let html = '<div class="diff-container">';
        for (const line of lines) {
            const escaped = this.escapeHtml(line);
            if (line.startsWith('+') && !line.startsWith('+++')) {
                html += `<div class="diff-line add">${escaped}</div>`;
            } else if (line.startsWith('-') && !line.startsWith('---')) {
                html += `<div class="diff-line remove">${escaped}</div>`;
            } else if (line.startsWith('@@')) {
                html += `<div class="diff-line" style="color:var(--neon-cyan)">${escaped}</div>`;
            } else {
                html += `<div class="diff-line context">${escaped}</div>`;
            }
        }
        html += '</div>';
        return html;
    },

    // ============================================================
    //  工具函数
    // ============================================================

    debounce(fn, ms) {
        let timer;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), ms);
        };
    },

    /** 复制到剪贴板 */
    async copyToClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (_) {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.cssText = 'position:fixed;left:-9999px';
            document.body.appendChild(ta);
            ta.select();
            const ok = document.execCommand('copy');
            ta.remove();
            return ok;
        }
    },

    /** 给 pre>code 块添加复制按钮 */
    addCopyButtons(container) {
        container.querySelectorAll('pre > code').forEach(codeEl => {
            const pre = codeEl.parentElement;
            if (pre.querySelector('.code-copy-btn')) return;

            const btn = document.createElement('button');
            btn.className = 'code-copy-btn';
            btn.innerHTML = `${icon('copy')} <span>复制</span>`;
            btn.onclick = async () => {
                const ok = await this.copyToClipboard(codeEl.textContent);
                if (ok) {
                    btn.innerHTML = `${icon('check')} <span>已复制</span>`;
                    btn.classList.add('copied');
                    setTimeout(() => {
                        btn.innerHTML = `${icon('copy')} <span>复制</span>`;
                        btn.classList.remove('copied');
                    }, 2000);
                }
            };
            pre.style.position = 'relative';
            pre.appendChild(btn);

            // 语言标签
            const match = codeEl.className.match(/language-(\w+)/);
            if (match) {
                const tag = document.createElement('span');
                tag.className = 'code-lang-tag';
                tag.textContent = match[1];
                pre.appendChild(tag);
            }
        });
    },

    /** 带加载状态的 fetch 请求 */
    async fetchWithLoading(url, options = {}, loadingText = '加载中...') {
        this.showLoading(loadingText);
        try {
            const response = await fetch(url, options);
            if (!response.ok) {
                const error = await response.json().catch(() => ({ detail: response.statusText }));
                throw new Error(error.detail || `请求失败: ${response.status}`);
            }
            return await response.json();
        } catch (error) {
            this.showError(error.message);
            throw error;
        } finally {
            this.hideLoading();
        }
    },
};

// 初始化 Markdown
Utils.initMarkdown();
