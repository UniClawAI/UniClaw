/* sidebar.js — 右侧面板(文件树、控制台、Checkpoint、Git) */

const Sidebar = {
    currentTab: 'files',
    _consoleHistory: [],
    _consoleHistoryIdx: -1,
    _gitCollapsed: { staged: false, changes: false },

    init() {
        document.querySelectorAll('.tab-btn').forEach(btn => { btn.onclick = () => this.switchTab(btn.dataset.tab); });
        const ci = document.getElementById('console-input');
        ci.addEventListener('keydown', e => {
            if (e.key === 'Enter') { this._execCmd(e.target.value); e.target.value = ''; }
            else if (e.key === 'ArrowUp') { e.preventDefault(); if (this._consoleHistoryIdx < this._consoleHistory.length - 1) { this._consoleHistoryIdx++; e.target.value = this._consoleHistory[this._consoleHistory.length - 1 - this._consoleHistoryIdx]; } }
            else if (e.key === 'ArrowDown') { e.preventDefault(); if (this._consoleHistoryIdx > 0) { this._consoleHistoryIdx--; e.target.value = this._consoleHistory[this._consoleHistory.length - 1 - this._consoleHistoryIdx]; } else { this._consoleHistoryIdx = -1; e.target.value = ''; } }
        });
        WS.on('shell_result', msg => this._onShellResult(msg));
    },

    switchTab(tab) {
        this.currentTab = tab;
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
        document.querySelectorAll('.tab-content').forEach(el => el.classList.toggle('active', el.id === `tab-${tab}`));
        if (tab === 'files') this._loadFileTree();
        if (tab === 'checkpoint') this._loadCheckpoints();
        if (tab === 'git') this._loadGitStatus();
        if (tab === 'console') this._loadMonitors();
    },

    // ---- 文件树 ----
    async _loadFileTree() {
        const rd = SessionPanel.activeProjectDir;
        if (!rd) return;
        try {
            const r = await fetch(`/api/files?root_dir=${encodeURIComponent(rd)}`);
            const files = await r.json();
            const tree = document.getElementById('file-tree');
            tree.innerHTML = files.map(f => this._renderTreeNode(f, 0, '')).join('');
        } catch (e) { console.error('加载文件树失败:', e); }
    },

    _renderTreeNode(item, depth, parentPath) {
        const fullPath = parentPath ? `${parentPath}/${item.name}` : item.name;
        const indent = depth * 16;
        if (item.is_dir) {
            return `<div class="tree-branch" style="margin-left:${indent}px">
                <div class="tree-node dir" onclick="Sidebar._toggleDir(this, '${this._escPath(fullPath)}')" data-path="${this._escPath(fullPath)}">
                    <span class="tree-toggle">${icon('chevronRight')}</span>
                    ${icon('folder', 'tree-icon folder')}
                    <span class="tree-name">${Utils.escapeHtml(item.name)}</span>
                </div>
                <div class="tree-children" data-loaded="false"></div>
            </div>`;
        }
        return `<div class="tree-branch" style="margin-left:${indent}px">
            <div class="tree-node file" onclick="Sidebar._insertFile('${this._escPath(fullPath)}')">
                <span class="tree-toggle placeholder">${icon('chevronRight')}</span>
                ${icon('file', 'tree-icon file')}
                <span class="tree-name">${Utils.escapeHtml(item.name)}</span>
            </div>
        </div>`;
    },

    _escPath(p) {
        return p.replace(/\\/g, '/').replace(/'/g, "\\'");
    },

    async _toggleDir(nodeEl, path) {
        const branch = nodeEl.parentElement;
        const children = branch.querySelector('.tree-children');
        const toggle = nodeEl.querySelector('.tree-toggle');

        if (toggle.classList.contains('expanded')) {
            // 折叠
            toggle.classList.remove('expanded');
            children.classList.remove('open');
        } else {
            // 展开
            if (children.dataset.loaded === 'false') {
                // 首次展开,加载子目录
                const rd = SessionPanel.activeProjectDir;
                if (!rd) return;
                try {
                    const r = await fetch(`/api/files?root_dir=${encodeURIComponent(rd)}&path=${encodeURIComponent(path)}`);
                    const files = await r.json();
                    const depth = this._getDepth(nodeEl);
                    children.innerHTML = files.map(f => this._renderTreeNode(f, depth, path)).join('');
                    children.dataset.loaded = 'true';
                } catch (e) { console.error('加载子目录失败:', e); return; }
            }
            toggle.classList.add('expanded');
            children.classList.add('open');
        }
    },

    _getDepth(el) {
        let depth = 0;
        let cur = el.parentElement;
        while (cur) {
            if (cur.classList && cur.classList.contains('tree-branch')) depth++;
            cur = cur.parentElement;
        }
        return depth;
    },

    _insertFile(path) {
        const inp = document.getElementById('chat-input');
        const s = inp.selectionStart, e = inp.selectionEnd;
        const insert = `@${path} `;
        inp.value = inp.value.substring(0, s) + insert + inp.value.substring(e);
        inp.setSelectionRange(s + insert.length, s + insert.length);
        inp.focus(); inp.dispatchEvent(new Event('input'));
    },

    // ---- Checkpoint ----
    async _loadCheckpoints() {
        const rd = SessionPanel.activeProjectDir;
        if (!rd) return;
        try {
            const r = await fetch(`/api/checkpoints?root_dir=${encodeURIComponent(rd)}`);
            const d = await r.json();
            const cps = this._parseCps(d.output || '');
            const c = document.getElementById('checkpoint-content');
            let html = '<div style="padding:8px 14px">';
            if (!cps.length) {
                html += '<div class="panel-empty"><p style="color:var(--text-3)">无 checkpoint</p></div>';
            } else {
                html += '<select id="cp-from" class="input" style="margin-bottom:6px;font-size:12px"><option value="current">当前工作区</option>';
                cps.forEach(cp => { html += `<option value="${cp.idx}">${Utils.escapeHtml(cp.label)}</option>`; });
                html += '</select><select id="cp-to" class="input" style="margin-bottom:8px;font-size:12px">';
                cps.forEach(cp => { html += `<option value="${cp.idx}">${Utils.escapeHtml(cp.label)}</option>`; });
                html += '</select>';
                html += '<div style="display:flex;gap:6px;margin-bottom:8px">';
                html += '<button class="btn btn-primary" onclick="Sidebar._showCpDiff()" style="font-size:11px;padding:4px 12px">对比</button>';
                html += '<button class="btn btn-secondary" onclick="Sidebar._restoreCp()" style="font-size:11px;padding:4px 12px">恢复</button>';
                html += '<button class="btn btn-ghost" onclick="Sidebar._showWsDiff()" style="font-size:11px;padding:4px 12px">工作区变更</button>';
                html += '</div>';
            }
            html += '<div id="cp-diff-area"></div></div>';
            c.innerHTML = html;
        } catch (e) { console.error('加载 checkpoint 失败:', e); }
    },

    _parseCps(output) {
        return output.split('\n').filter(l => l.trim()).map(line => {
            let m = line.match(/^\[(\d+)\]\s+(\S+)\s+-\s+(.+)$/);
            if (m) return { idx: parseInt(m[1]), label: `[${m[1]}] ${m[3]}` };
            m = line.match(/^stash@\{(\d+)\}:\s+(.+)$/);
            if (m) return { idx: parseInt(m[1]), label: `[${m[1]}] ${m[2]}` };
            return null;
        }).filter(Boolean);
    },

    _cpDiffRaw: '',
    _cpDiffMode: 'unified',
    _cpDiffFilter: '',

    async _showCpDiff() {
        const rd = SessionPanel.activeProjectDir;
        if (!rd) return;
        const from = document.getElementById('cp-from').value, to = document.getElementById('cp-to').value;
        const area = document.getElementById('cp-diff-area');
        area.innerHTML = '<span style="color:var(--text-3)">加载中...</span>';
        try {
            let url;
            if (from === 'current') url = `/api/checkpoints/${to}/diff?root_dir=${encodeURIComponent(rd)}`;
            else if (to === 'current') url = `/api/checkpoints/${from}/diff?root_dir=${encodeURIComponent(rd)}`;
            else url = `/api/checkpoints/diff-between?from_idx=${from}&to_idx=${to}&root_dir=${encodeURIComponent(rd)}`;
            const r = await fetch(url);
            const d = await r.json();
            this._cpDiffRaw = d.output || '';
            this._cpDiffMode = 'unified';
            this._cpDiffFilter = '';
            this._renderCpDiffArea(area);
        } catch (e) { area.innerHTML = `<span style="color:var(--neon-pink)">加载失败: ${e.message}</span>`; }
    },

    async _showWsDiff() {
        const rd = SessionPanel.activeProjectDir;
        if (!rd) return;
        const area = document.getElementById('cp-diff-area');
        area.innerHTML = '<span style="color:var(--text-3)">加载中...</span>';
        try {
            const r = await fetch(`/api/checkpoints/diff-current?root_dir=${encodeURIComponent(rd)}`);
            const d = await r.json();
            this._cpDiffRaw = d.output || '';
            this._cpDiffMode = 'unified';
            this._cpDiffFilter = '';
            this._renderCpDiffArea(area);
        } catch (e) { area.innerHTML = `<span style="color:var(--neon-pink)">加载失败: ${e.message}</span>`; }
    },

    _parseDiffFiles(output) {
        const files = [];
        output.split('\n').forEach(line => {
            const m = line.match(/^diff --git a\/(.+?) b\//);
            if (m && !files.includes(m[1])) files.push(m[1]);
        });
        return files;
    },

    _renderCpDiffArea(area) {
        if (!this._cpDiffRaw) { area.innerHTML = '<span style="color:var(--text-3)">无差异</span>'; return; }
        const files = this._parseDiffFiles(this._cpDiffRaw);
        let html = '';
        if (files.length > 0) {
            html += '<select id="cp-file-filter" onchange="Sidebar._filterCpDiff()" class="input" style="font-size:11px;padding:4px 8px;margin-bottom:6px">';
            html += '<option value="">全部文件</option>';
            files.forEach(f => { html += `<option value="${Utils.escapeHtml(f)}" ${this._cpDiffFilter === f ? 'selected' : ''}>${Utils.escapeHtml(f)}</option>`; });
            html += '</select>';
        }
        html += '<div style="display:flex;gap:4px;margin-bottom:6px">';
        html += `<button class="btn ${this._cpDiffMode === 'unified' ? 'btn-primary' : 'btn-ghost'}" onclick="Sidebar._switchCpDiffMode('unified')" style="font-size:10px;padding:2px 8px">Unified</button>`;
        html += `<button class="btn ${this._cpDiffMode === 'split' ? 'btn-primary' : 'btn-ghost'}" onclick="Sidebar._switchCpDiffMode('split')" style="font-size:10px;padding:2px 8px">Split</button>`;
        html += '</div>';
        html += `<div id="cp-diff-content">${this._cpDiffMode === 'split' ? this._renderSplitGitDiff(this._cpDiffRaw, this._cpDiffFilter) : this._renderUnifiedGitDiff(this._cpDiffRaw, this._cpDiffFilter)}</div>`;
        area.innerHTML = html;
    },

    _switchCpDiffMode(mode) {
        this._cpDiffMode = mode;
        this._renderCpDiffArea(document.getElementById('cp-diff-area'));
    },

    _filterCpDiff() {
        this._cpDiffFilter = document.getElementById('cp-file-filter')?.value || '';
        const content = document.getElementById('cp-diff-content');
        if (content) content.innerHTML = this._cpDiffMode === 'split'
            ? this._renderSplitGitDiff(this._cpDiffRaw, this._cpDiffFilter)
            : this._renderUnifiedGitDiff(this._cpDiffRaw, this._cpDiffFilter);
    },

    _renderUnifiedGitDiff(output, fileFilter) {
        const lines = output.split('\n');
        let html = '<pre style="font-size:11px;white-space:pre-wrap;background:var(--bg-inset);padding:10px;border-radius:var(--r-md);margin:0;border:1px solid var(--border)">';
        let inFile = !fileFilter;
        for (const line of lines) {
            if (line.startsWith('diff --git')) {
                const m = line.match(/b\/(.+)$/);
                inFile = !fileFilter || (m && m[1] === fileFilter);
                if (inFile) html += `<span style="color:var(--neon-cyan);font-weight:bold">${Utils.escapeHtml(line)}\n</span>`;
                continue;
            }
            if (!inFile) continue;
            if (line.startsWith('@@')) html += `<span style="color:var(--accent-bright)">${Utils.escapeHtml(line)}\n</span>`;
            else if (line.startsWith('+') && !line.startsWith('+++')) html += `<span style="color:var(--neon-green)">${Utils.escapeHtml(line)}\n</span>`;
            else if (line.startsWith('-') && !line.startsWith('---')) html += `<span style="color:var(--neon-pink)">${Utils.escapeHtml(line)}\n</span>`;
            else html += Utils.escapeHtml(line) + '\n';
        }
        html += '</pre>';
        return html;
    },

    _renderSplitGitDiff(output, fileFilter) {
        const lines = output.split('\n');
        let leftLines = [], rightLines = [];
        let inFile = !fileFilter;
        for (const line of lines) {
            if (line.startsWith('diff --git')) {
                const m = line.match(/b\/(.+)$/);
                inFile = !fileFilter || (m && m[1] === fileFilter);
                continue;
            }
            if (!inFile) continue;
            if (line.startsWith('index ') || line.startsWith('---') || line.startsWith('+++')) continue;
            if (line.startsWith('@@')) { leftLines.push({ t: 'hunk', l: line }); rightLines.push({ t: 'hunk', l: line }); continue; }
            if (line.startsWith('+')) { leftLines.push({ t: 'empty', l: '' }); rightLines.push({ t: 'add', l: line.slice(1) }); }
            else if (line.startsWith('-')) { leftLines.push({ t: 'remove', l: line.slice(1) }); rightLines.push({ t: 'empty', l: '' }); }
            else { leftLines.push({ t: 'ctx', l: line }); rightLines.push({ t: 'ctx', l: line }); }
        }
        const renderSide = (items) => items.map(item => {
            if (item.t === 'hunk') return `<div class="diff-line" style="color:var(--accent-bright)">${Utils.escapeHtml(item.l)}</div>`;
            if (item.t === 'add') return `<div class="diff-line add">${Utils.escapeHtml(item.l)}</div>`;
            if (item.t === 'remove') return `<div class="diff-line remove">${Utils.escapeHtml(item.l)}</div>`;
            if (item.t === 'empty') return `<div class="diff-line context">&nbsp;</div>`;
            return `<div class="diff-line context">${Utils.escapeHtml(item.l)}</div>`;
        }).join('');
        return `<div class="diff-container" style="display:grid;grid-template-columns:1fr 1fr;gap:1px"><div>${renderSide(leftLines)}</div><div>${renderSide(rightLines)}</div></div>`;
    },

    async _restoreCp() {
        const rd = SessionPanel.activeProjectDir;
        if (!rd) return;
        const idx = document.getElementById('cp-to').value;
        if (idx === 'current') { Utils.showToast('请选择一个 checkpoint'); return; }
        if (!await Utils.confirm(`确定恢复到 checkpoint [${idx}]？`)) return;
        try {
            Utils.showLoading('正在恢复...');
            await fetch(`/api/checkpoints/${idx}/restore`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ root_dir: rd }) });
            Utils.showSuccess('恢复成功');
            this._loadCheckpoints();
        } catch (e) { Utils.showError('恢复失败'); }
        finally { Utils.hideLoading(); }
    },

    // ---- Git ----
    async _loadGitStatus() {
        const rd = SessionPanel.activeProjectDir;
        if (!rd) return;
        try {
            const r = await fetch(`/api/git/status?root_dir=${encodeURIComponent(rd)}`);
            const d = await r.json();
            const lines = (d.output || '').split('\n').filter(l => l.trim());
            const c = document.getElementById('git-content');
            if (!lines.length) { c.innerHTML = `<div class="panel-empty">${icon("check")}<div class="panel-empty-text">没有更改</div></div>`; return; }
            const staged = [], changes = [];
            for (const line of lines) {
                const sc = line.substring(0, 2), file = line.substring(3);
                if (sc[0] !== ' ' && sc[0] !== '?') staged.push({ file, ch: sc[0] });
                if (sc[1] !== ' ' || sc === '??') changes.push({ file, ch: sc === '??' ? '?' : sc[1] });
            }
            let html = '<div class="git-commit-box"><textarea id="git-commit-msg" class="git-commit-input" rows="2" placeholder="提交消息..."></textarea>';
            html += '<div class="git-commit-actions"><span style="font-size:10px;color:var(--text-3)">Ctrl+Enter 提交</span><span style="flex:1"></span>';
            html += `<button class="btn btn-ghost" onclick="Sidebar._aiCommit()" style="font-size:11px">${icon('sparkles')} AI</button>`;
            html += `<button class="btn btn-primary" onclick="Sidebar._gitCommit()" style="font-size:11px">提交</button></div></div>`;
            html += this._renderGitSection('staged', '暂存的更改', staged, true);
            html += this._renderGitSection('changes', '更改', changes, false);
            c.innerHTML = html;
            const ta = document.getElementById('git-commit-msg');
            if (ta) ta.addEventListener('keydown', e => { if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); Sidebar._gitCommit(); } });
        } catch (e) { console.error('加载 git 状态失败:', e); }
    },

    _renderGitSection(key, label, files, isStaged) {
        if (!files.length) return '';
        const collapsed = this._gitCollapsed[key];
        const chevronIcon = collapsed ? icon('chevronRight') : icon('chevronDown');
        let html = `<div class="git-section"><div class="git-section-title" onclick="Sidebar._toggleGitSection('${key}')" style="cursor:pointer">${chevronIcon} ${label} <span class="count">${files.length}</span></div>`;
        if (!collapsed) {
            for (const { file, ch } of files) {
                html += `<div class="git-file-item"><input type="checkbox" class="git-file-check" value="${Utils.escapeHtml(file)}" ${isStaged ? 'checked' : ''} onchange="Sidebar._toggleStage(this)"/><span class="git-file-status ${ch}">${ch}</span><span class="git-file-name" title="${Utils.escapeHtml(file)}">${Utils.escapeHtml(file)}</span></div>`;
            }
        }
        html += '</div>';
        return html;
    },

    _toggleGitSection(key) { this._gitCollapsed[key] = !this._gitCollapsed[key]; this._loadGitStatus(); },

    async _toggleStage(cb) {
        const rd = SessionPanel.activeProjectDir;
        if (!rd) return;
        try {
            await fetch(cb.checked ? '/api/git/stage' : '/api/git/unstage', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ root_dir: rd, files: [cb.value] }) });
            this._loadGitStatus();
        } catch (e) { Utils.showError('操作失败'); cb.checked = !cb.checked; }
    },

    async _gitCommit() {
        const rd = SessionPanel.activeProjectDir;
        const msg = document.getElementById('git-commit-msg')?.value;
        if (!rd || !msg) return;
        const files = Array.from(document.querySelectorAll('.git-file-check:checked')).map(el => el.value);
        try {
            await fetch('/api/git/commit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ root_dir: rd, message: msg, files }) });
            Utils.showSuccess('提交成功');
            this._loadGitStatus();
        } catch (e) { Utils.showError('提交失败'); }
    },

    async _aiCommit() {
        const rd = SessionPanel.activeProjectDir;
        if (!rd) return;
        try {
            const r = await fetch('/api/git/ai-commit-message', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ root_dir: rd }) });
            const d = await r.json();
            if (d.message) { const inp = document.getElementById('git-commit-msg'); if (inp) { inp.value = d.message; inp.focus(); } }
            else Utils.showToast(d.error || '生成失败');
        } catch (e) { Utils.showError('AI 生成失败'); }
    },

    // ---- 控制台 ----
    _execCmd(cmd) {
        if (!cmd.trim()) return;
        const sid = SessionPanel.activeSessionId;
        if (!sid) { this._appendConsole('错误: 未选择会话\n', 'error'); return; }
        this._consoleHistory.push(cmd); this._consoleHistoryIdx = -1;
        this._appendConsole(`$ ${cmd}\n`, 'cmd');
        WS.send({ type: 'shell', session_id: sid, command: cmd, source: 'console' });
    },

    _onShellResult(msg) {
        if (msg.source !== 'console') return;
        if (msg.output?.trim()) this._appendConsole(msg.output + '\n', msg.success !== false ? 'output' : 'error');
    },

    _appendConsole(text, type) {
        const out = document.getElementById('console-output');
        // 处理 \r：只保留最后一个 \r 之后的内容(覆盖当前行)
        const lines = text.split('\n');
        for (let i = 0; i < lines.length; i++) {
            let lineText = lines[i];
            // \r 覆盖：取最后一个 \r 之后的内容
            const crIdx = lineText.lastIndexOf('\r');
            if (crIdx >= 0) lineText = lineText.slice(crIdx + 1);
            if (!lineText && i < lines.length - 1) continue; // 跳过空行(除了最后一行)
            const el = document.createElement('div');
            el.className = `console-line ${type}`;
            el.textContent = lineText;
            out.appendChild(el);
        }
        out.scrollTop = out.scrollHeight;
    },

    async _loadMonitors() {
        try {
            const r = await fetch('/api/monitors');
            const monitors = await r.json();
            const out = document.getElementById('console-output');
            out.querySelectorAll('.monitor-list').forEach(el => el.remove());
            if (!monitors.length) return;
            const list = document.createElement('div');
            list.className = 'monitor-list';
            list.innerHTML = '<div class="monitor-header">后台进程</div>';
            monitors.forEach(m => {
                const isRunning = m.status === 'running';
                const card = document.createElement('div');
                card.className = 'monitor-card';
                card.innerHTML = `
                    <span class="monitor-dot ${isRunning ? 'running' : 'stopped'}"></span>
                    <div class="monitor-info">
                        <div class="monitor-name">${Utils.escapeHtml(m.description || m.command)}</div>
                        <div class="monitor-status">${isRunning ? '运行中' : '已停止'}</div>
                    </div>
                `;
                if (isRunning) {
                    const btn = document.createElement('button');
                    btn.className = 'monitor-stop';
                    btn.textContent = '停止';
                    btn.onclick = async () => { try { await fetch(`/api/monitors/${m.id}/stop`, { method: 'POST' }); Utils.showSuccess('已停止'); this._loadMonitors(); } catch (_) { Utils.showError('停止失败'); } };
                    card.appendChild(btn);
                }
                list.appendChild(card);
            });
            out.appendChild(list);
        } catch (e) { console.error('加载进程列表失败:', e); }
    },
};
