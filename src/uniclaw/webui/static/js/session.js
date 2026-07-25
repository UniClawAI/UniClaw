/* session.js — 左侧会话面板 */

const SessionPanel = {
    projects: {},
    activeSessionId: null,
    activeProjectDir: null,
    runningSessions: new Set(),
    attentionSessions: new Set(),
    _contextTimer: null,
    wechatBots: [],
    categoryExpanded: { projects: true, sessions: true, wechat: true },

    init() {
        this._bindEvents();
        this._loadProjects().catch(e => console.error('[SessionPanel] 初始化失败:', e));
        this._loadWechatBots().catch(e => console.error('[SessionPanel] 加载微信 Bot 失败:', e));
        WS.on('status', msg => this._onStatus(msg));
        WS.on('wechat_login_status', msg => this._onWechatLoginStatus(msg));
        WS.on('session_attention', msg => {
            if (msg.session_id) { this.attentionSessions.add(msg.session_id); this._render(); }
        });
        WS.on('session_attention_clear', msg => {
            if (msg.session_id) { this.attentionSessions.delete(msg.session_id); this._render(); }
        });
        WS.on('session_deleted', msg => this._onSessionDeleted(msg));
        WS.on('session_switched', msg => this._onSessionSwitched(msg));
        WS.on('session_created', msg => this._onSessionCreated(msg));
    },

    _bindEvents() {
        document.getElementById('search-input').oninput = Utils.debounce(e => this._onSearch(e.target.value), 300);
        // 触摸设备: 点击空白区域关闭 tooltip
        document.addEventListener('touchstart', e => {
            if (!e.target.closest('[data-tip]') && !e.target.closest('#session-tooltip')) {
                this._hideTip();
            }
        }, { passive: true });
    },

    // ============================================================
    //  微信 Bot 管理
    // ============================================================

    async _loadWechatBots() {
        try {
            const resp = await fetch('/api/wechat/bots');
            if (resp.ok) {
                this.wechatBots = await resp.json();
                this._render();
            }
        } catch (e) {
            console.error('[SessionPanel] 加载微信 Bot 失败:', e);
        }
    },

    _renderWechatSection() {
        let html = '<div class="wechat-bot-list">';
        this.wechatBots.forEach(bot => {
            const statusClass = bot.is_logged_in ? 'online' : 'offline';
            const statusText = bot.is_logged_in ? '在线' : '离线';
            html += `<div class="wechat-bot-item ${statusClass}" data-bot="${Utils.escapeHtml(bot.name)}">`;
            html += `<span class="wechat-bot-status ${statusClass}"></span>`;
            html += `<span class="wechat-bot-name">${Utils.escapeHtml(bot.name)}</span>`;
            html += `<span class="wechat-bot-status-text">${statusText}</span>`;
            if (!bot.is_logged_in) {
                html += `<button class="btn-icon compact" onclick="event.stopPropagation(); SessionPanel._reloginWechatBot('${this._esc(bot.name)}')" title="重新登录">${icon('refresh')}</button>`;
            }
            html += `<button class="btn-icon compact" onclick="event.stopPropagation(); SessionPanel._deleteWechatBot('${this._esc(bot.name)}')" title="删除">${icon('trash')}</button>`;
            html += '</div>';
        });
        html += '</div>';
        return html;
    },

    toggleCategory(category) {
        this.categoryExpanded[category] = !this.categoryExpanded[category];
        this._saveProjects();
        this._render();
    },

    async _reloginWechatBot(botName) {
        try {
            // 获取新的 QR URL
            const qrResp = await fetch(`/api/wechat/bots/${encodeURIComponent(botName)}/qrcode`, {
                method: 'POST',
            });

            if (!qrResp.ok) {
                const error = await qrResp.json();
                Utils.showError(error.detail || '获取二维码失败');
                return;
            }

            const qrData = await qrResp.json();
            const qrUrl = qrData.qrcode_url;
            const qrCode = qrData.qrcode;

            // 显示二维码弹窗
            this._showQrcodeDialog(qrUrl, botName);

            // 触发登录(阻塞等待,传入 qrcode 会话标识)
            const loginResp = await fetch(`/api/wechat/bots/${encodeURIComponent(botName)}/login?qrcode=${encodeURIComponent(qrCode)}`, {
                method: 'POST',
            });

            const loginData = await loginResp.json();

            // 关闭二维码弹窗
            this._hideQrcodeDialog();

            if (loginData.success) {
                Utils.showSuccess(`微信 Bot '${botName}' 登录成功`);
                this._loadWechatBots();
                this._refreshSessions();
            } else {
                Utils.showError(`登录失败: ${loginData.error}`);
            }
        } catch (e) {
            this._hideQrcodeDialog();
            Utils.showError(`重新登录失败: ${e.message}`);
        }
    },

    showCreateWechatDialog() {
        this._showModal('创建微信会话', `
            <div style="margin-top:4px">
                <label style="font-size:var(--text-sm);color:var(--text-2)">Bot 名称:</label>
                <input type="text" id="wechat-bot-name" class="input" placeholder="输入名称..." style="margin-top:4px" />
            </div>
        `, async () => {
            const name = document.getElementById('wechat-bot-name').value.trim();
            if (name) {
                await this._createWechatBot(name);
            }
        });
        setTimeout(() => document.getElementById('wechat-bot-name')?.focus(), 0);
    },

    async _createWechatBot(name) {
        try {
            // 第一步:创建 Bot 并获取 QR URL
            const createResp = await fetch('/api/wechat/bots', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name }),
            });

            if (!createResp.ok) {
                const error = await createResp.json();
                Utils.showError(error.detail || '创建失败');
                return;
            }

            const createData = await createResp.json();
            const botName = createData.bot_name;
            const qrUrl = createData.qrcode_url;
            const qrCode = createData.qrcode;  // QR 会话标识

            // 显示二维码弹窗
            this._showQrcodeDialog(qrUrl, botName);

            // 第二步:触发登录(阻塞等待,传入 qrcode 会话标识)
            const loginResp = await fetch(`/api/wechat/bots/${encodeURIComponent(botName)}/login?qrcode=${encodeURIComponent(qrCode)}`, {
                method: 'POST',
            });

            const loginData = await loginResp.json();

            // 关闭二维码弹窗
            this._hideQrcodeDialog();

            if (loginData.success) {
                Utils.showSuccess(`微信 Bot '${botName}' 登录成功`);
                this._loadWechatBots();
                this._refreshSessions();
            } else {
                Utils.showError(`登录失败: ${loginData.error}`);
            }
        } catch (e) {
            this._hideQrcodeDialog();
            Utils.showError(`创建失败: ${e.message}`);
        }
    },

    _showQrcodeDialog(qrUrl, botName) {
        const modal = document.getElementById('wechat-qrcode-modal');
        if (modal) {
            modal.classList.remove('hidden');
            document.getElementById('wechat-qrcode-status').textContent = '请使用微信扫描二维码';

            const container = document.getElementById('wechat-qrcode-container');
            container.innerHTML = '';

            // 检查 QRCode 是否已加载
            if (typeof QRCode === 'undefined') {
                container.innerHTML = '<p style="color:var(--neon-pink)">二维码库加载失败,请刷新页面重试</p>';
                return;
            }

            try {
                new QRCode(container, {
                    text: qrUrl,
                    width: 200,
                    height: 200,
                    colorDark: '#000000',
                    colorLight: '#ffffff',
                });
            } catch (e) {
                console.error('生成二维码异常:', e);
                container.innerHTML = '<p style="color:var(--neon-pink)">生成二维码失败</p>';
            }
        }
    },

    _hideQrcodeDialog() {
        const modal = document.getElementById('wechat-qrcode-modal');
        if (modal) modal.classList.add('hidden');
    },

    _onWechatLoginStatus(msg) {
        const statusEl = document.getElementById('wechat-qrcode-status');
        if (!statusEl) return;
        const statusMap = {
            'qrcode': '请使用微信扫描二维码',
            'wait': '等待扫码...',
            'reused': '已登录(复用缓存)',
            'scaned': '已扫码,请在手机上确认登录',
            'scanned': '已扫码,请在手机上确认登录',
            'confirmed': '已确认,正在登录...',
            'confirm': '已确认,正在登录...',
            'success': '登录成功',
            'ok': '登录成功',
            'expired': '二维码已过期',
            'timeout': '登录超时',
            'cancel': '已取消',
            'canceled': '已取消',
            'cancelled': '已取消',
            'failed': '登录失败',
            'error': '登录出错',
        };
        const text = statusMap[msg.status] || `状态: ${msg.status}`;
        statusEl.textContent = text;
        if (['expired', 'timeout', 'cancel', 'canceled', 'cancelled', 'failed', 'error'].includes(msg.status)) {
            statusEl.style.color = 'var(--neon-pink)';
        }
        // 成功或复用时自动关闭弹窗并刷新
        if (['success', 'ok', 'reused'].includes(msg.status)) {
            setTimeout(() => {
                this._hideQrcodeDialog();
                this._loadWechatBots();
                this._refreshSessions();
            }, 1000);
        }
    },

    async _deleteWechatBot(name) {
        if (!await Utils.confirm(`确定删除微信 Bot '${name}'？`)) return;
        try {
            const r = await fetch(`/api/wechat/bots/${encodeURIComponent(name)}`, { method: 'DELETE' });
            if (r.ok) {
                Utils.showSuccess('已删除');
                this._loadWechatBots();
            }
        } catch (e) {
            Utils.showError('删除失败');
        }
    },

    _onSessionDeleted(msg) {
        const sid = msg.session_id;
        if (!sid) return;
        if (this.activeSessionId === sid) {
            this._clearSessionFromUrl();
            this.activeSessionId = null;
            Chat.currentSessionId = null;
            Chat.clear();
            Chat._appendSystemMessage('会话已删除,请选择其他会话或创建新会话');
        }
        this._refreshSessions();
    },

    _onSessionSwitched(msg) {
        if (!msg.session_id) return;
        this.selectSession(msg.session_id, this.activeProjectDir);
        this._refreshSessions();
    },

    _onSessionCreated(msg) {
        if (!msg.session_id) return;
        this.activeSessionId = msg.session_id;
        Chat.currentSessionId = msg.session_id;
        this._saveSessionToUrl(msg.session_id);
        this._updateStatusBar(this.activeProjectDir, msg.session_id);
        this._refreshSessions();
    },

    _onStatus(msg) {
        const sid = msg.session_id;
        if (!sid) return;
        if (msg.status === 'running') this.runningSessions.add(sid);
        else {
            this.runningSessions.delete(sid);
            if (this.activeSessionId === sid) this._fetchContextUsage(sid);
        }
        const el = document.querySelector(`.session-item[data-sid="${sid}"]`);
        if (el) {
            let ind = el.querySelector('.running-indicator');
            if (msg.status === 'running') {
                if (!ind) { ind = document.createElement('span'); ind.className = 'running-indicator'; el.appendChild(ind); }
            } else { if (ind) ind.remove(); }
        }
    },

    async _loadProjects() {
        const saved = localStorage.getItem('uniclaw_projects');
        if (saved) {
            const data = JSON.parse(saved);
            // 兼容旧格式(纯数组)和新格式(对象)
            if (Array.isArray(data)) {
                data.forEach(dir => { if (!this.projects[dir]) this.projects[dir] = { sessions: [], expanded: true }; });
            } else {
                Object.entries(data).forEach(([dir, meta]) => {
                    if (!this.projects[dir]) this.projects[dir] = { sessions: [], expanded: meta.expanded !== false, created_at: meta.created_at || null };
                });
            }
        }
        // 恢复顶级分类展开状态
        const catSaved = localStorage.getItem('uniclaw_category_expanded');
        if (catSaved) {
            try {
                const catData = JSON.parse(catSaved);
                Object.assign(this.categoryExpanded, catData);
            } catch {}
        }
        await this._refreshSessions();
        // 恢复 URL 中指定的 session；无参数则显示欢迎页面
        const urlSid = new URLSearchParams(window.location.search).get('session_id');
        if (urlSid) {
            const found = this._findSession(urlSid);
            if (found) {
                this.selectSession(urlSid, found.rootDir);
            } else {
                // URL 中的 session_id 无效,清除参数并显示欢迎页面
                this._clearSessionFromUrl();
            }
        }
    },

    /** 在所有项目中查找 session,返回 { rootDir, session } 或 null */
    _findSession(sessionId) {
        for (const [rootDir, proj] of Object.entries(this.projects)) {
            const s = proj.sessions.find(s => s.session_id === sessionId);
            if (s) return { rootDir, session: s };
        }
        return null;
    },

    /** 将 session_id 写入 URL query 参数(不触发页面刷新) */
    _saveSessionToUrl(sessionId) {
        const url = new URL(window.location);
        url.searchParams.set('session_id', sessionId);
        history.replaceState(null, '', url);
    },

    /** 从 URL 中移除 session_id 参数 */
    _clearSessionFromUrl() {
        const url = new URL(window.location);
        url.searchParams.delete('session_id');
        history.replaceState(null, '', url);
    },

    async _refreshSessions() {
        try {
            const resp = await fetch('/api/sessions');
            if (!resp.ok) return;
            const sessions = await resp.json();
            const grouped = {};
            sessions.forEach(s => {
                if (s.session_type === 'free_chat') {
                    if (!grouped['__free__']) grouped['__free__'] = [];
                    grouped['__free__'].push(s);
                } else {
                    const d = s.root_dir || '';
                    if (!d) return;
                    if (!grouped[d]) grouped[d] = [];
                    grouped[d].push(s);
                }
            });
            const saved = localStorage.getItem('uniclaw_projects');
            let savedDirs = [];
            if (saved) {
                const data = JSON.parse(saved);
                savedDirs = Array.isArray(data) ? data : Object.keys(data);
            }
            Object.keys(this.projects).forEach(dir => { if (!grouped[dir] && !savedDirs.includes(dir)) delete this.projects[dir]; });

            // 第一步：更新所有项目的 sessions 数据
            Object.keys(grouped).forEach(dir => {
                if (!this.projects[dir]) this.projects[dir] = { sessions: [], expanded: true };
                this.projects[dir].sessions = grouped[dir].sort((a, b) => (b.end_time || b.start_time || '').localeCompare(a.end_time || a.start_time || ''));
            });

            // 第二步：找到当前活动会话所在的项目(此时 sessions 已更新)
            const urlSid = new URLSearchParams(window.location.search).get('session_id');
            const activeSid = this.activeSessionId || urlSid;
            const activeSessionProject = activeSid ? this._findSession(activeSid)?.rootDir : null;

            // 第三步：会话数超过 50 的项目,根据当前活动会话判断是否折叠
            Object.keys(grouped).forEach(dir => {
                if (this.projects[dir].sessions.length > 50) {
                    this.projects[dir].expanded = (dir === activeSessionProject);
                }
            });
            savedDirs.forEach(dir => { if (!this.projects[dir]) this.projects[dir] = { sessions: [], expanded: true }; });
            this._render();
        } catch (e) { console.error('刷新会话失败:', e); }
    },

    _render() {
        const tree = document.getElementById('session-tree');
        if (!tree) return;
        const sorted = Object.entries(this.projects).sort(([, a], [, b]) => {
            const ta = a.sessions.length > 0 ? (a.sessions[0].end_time || a.sessions[0].start_time || '') : (a.created_at || '');
            const tb = b.sessions.length > 0 ? (b.sessions[0].end_time || b.sessions[0].start_time || '') : (b.created_at || '');
            return tb.localeCompare(ta);
        });

        // 普通项目(排除自由聊天)
        const normalProjects = sorted.filter(([dir]) => dir !== '__free__');
        let projectsHtml = '';
        normalProjects.forEach(([rootDir, proj]) => {
            projectsHtml += this._renderProjectGroup(rootDir, proj);
        });

        // 自由聊天
        const freeProj = this.projects['__free__'] || { sessions: [], expanded: true };
        const freeChatHtml = this._renderFreeChatGroup(freeProj);

        // 微信会话
        const wechatHtml = this._renderWechatSection();

        // 计算各分类的计数
        const projectCount = normalProjects.length;
        const sessionCount = freeProj.sessions.length;
        const wechatCount = this.wechatBots.length;

        let html = '';

        // 顶级分类: 项目
        const projectAction = `<button class="btn-icon compact" onclick="event.stopPropagation(); SessionPanel._showNewProjectDialog()" title="新建项目">${icon('plus')}</button>`;
        html += this._renderTopCategory('projects', '📁', '项目', projectCount, projectsHtml, projectAction);

        // 顶级分类: 会话
        const sessionAction = `<button class="btn-icon compact" onclick="event.stopPropagation(); SessionPanel.createFreeChat()" title="新建自由聊天">${icon('plus')}</button>`;
        html += this._renderTopCategory('sessions', '💬', '会话', sessionCount, freeChatHtml, sessionAction);

        // 顶级分类: wechat
        const wechatAction = `<button class="btn-icon compact" onclick="event.stopPropagation(); SessionPanel.showCreateWechatDialog()" title="创建微信会话">${icon('plus')}</button>`;
        html += this._renderTopCategory('wechat', '📱', 'wechat', wechatCount, wechatHtml, wechatAction);

        if (!projectCount && !wechatCount && !sessionCount) html += '<div class="no-results">暂无会话</div>';
        tree.innerHTML = html;
    },

    _renderProjectGroup(rootDir, proj) {
        const shortName = rootDir.split(/[/\\]/).pop() || rootDir;
        const isExp = proj.expanded;
        const runCount = proj.sessions.filter(s => this.runningSessions.has(s.session_id)).length;
        const sessionTime = proj.sessions.length > 0 ? (proj.sessions[0].end_time || proj.sessions[0].start_time || '-') : '-';
        const tipData = JSON.stringify({ dir: rootDir, created: proj.created_at || '-', sessions: proj.sessions.length, latest: sessionTime }).replace(/"/g, '&quot;');
        let h = `<div class="project-group ${isExp ? 'expanded' : ''}" data-dir="${Utils.escapeHtml(rootDir)}">`;
        h += `<div class="project-header" data-tip="${tipData}" onclick="SessionPanel.toggleProject('${this._esc(rootDir)}')" onmouseenter="SessionPanel._showTip(this, event)" onmousemove="SessionPanel._moveTip(event)" onmouseleave="SessionPanel._hideTip()" ontouchstart="SessionPanel._showTip(this, event)" ondragover="SessionPanel._onDragOver(event)" ondrop="SessionPanel._onDrop(event, '${this._esc(rootDir)}')">`;
        h += `<span class="project-chevron">${icon('chevronRight')}</span>`;
        h += `<span class="project-icon">${icon('folder')}</span>`;
        h += `<span class="project-name">${Utils.escapeHtml(shortName)}</span>`;
        h += `<span class="project-count">${proj.sessions.length}</span>`;
        if (runCount) h += '<span class="running-indicator"></span>';
        h += `<button class="btn-icon compact" onclick="event.stopPropagation(); SessionPanel.createSession('${this._esc(rootDir)}')" title="新建会话">${icon('plus')}</button>`;
        h += `</div><div class="project-sessions">`;
        h += this._renderSessionItems(proj.sessions, rootDir);
        h += `</div></div>`;
        return h;
    },

    _renderFreeChatGroup(proj) {
        return this._renderSessionItems(proj.sessions, '__free__');
    },

    _renderTopCategory(category, emoji, title, count, innerHtml, actionHtml) {
        const isExp = this.categoryExpanded[category];
        let h = `<div class="tree-category ${isExp ? 'expanded' : ''}" data-category="${category}">`;
        h += `<div class="tree-category-header" onclick="SessionPanel.toggleCategory('${category}')">`;
        h += `<span class="tree-category-chevron">${icon('chevronRight')}</span>`;
        h += `<span class="tree-category-icon">${emoji}</span>`;
        h += `<span class="tree-category-title">${title}</span>`;
        h += `<span class="tree-category-count">${count}</span>`;
        if (actionHtml) h += actionHtml;
        h += `</div>`;
        h += `<div class="tree-category-content">${innerHtml}</div>`;
        h += `</div>`;
        return h;
    },

    /** 根据 session_id 查找会话标题 */
    getSessionTitle(sessionId) {
        for (const dir of Object.keys(this.projects)) {
            const s = (this.projects[dir].sessions || []).find(s => s.session_id === sessionId);
            if (s) return s.title || sessionId.substring(0, 8);
        }
        return sessionId.substring(0, 8);
    },

    _renderSessionItems(sessions, rootDir) {
        let h = '';
        sessions.forEach(s => {
            const isActive = s.session_id === this.activeSessionId;
            const isRunning = this.runningSessions.has(s.session_id);
            const isAttn = this.attentionSessions.has(s.session_id);
            const title = s.title || s.session_id;
            const time = Utils.formatRelativeTime(s.end_time || s.start_time);
            const tipData = JSON.stringify({ id: s.session_id, title: title, start: s.start_time || '', end: s.end_time || '', dir: s.root_dir || '', msg: s.message_count || 0 }).replace(/"/g, '&quot;');
            h += `<div class="session-item ${isActive ? 'active' : ''}" data-sid="${s.session_id}" data-tip="${tipData}" draggable="true" ondragstart="SessionPanel._onDragStart(event, '${s.session_id}')" onclick="SessionPanel.selectSession('${s.session_id}', '${this._esc(rootDir)}')" onmouseenter="SessionPanel._showTip(this, event)" onmousemove="SessionPanel._moveTip(event)" onmouseleave="SessionPanel._hideTip()" ontouchstart="SessionPanel._showTip(this, event)">`;
            h += `<span class="session-icon">${icon('chat')}</span>`;
            h += `<div class="session-info"><div class="session-title">${Utils.escapeHtml(title)}</div><div class="session-meta">${time}</div></div>`;
            if (isRunning) h += '<span class="running-indicator"></span>';
            if (isAttn) h += '<span class="attention-badge"></span>';
            h += `<button class="btn-icon compact" onclick="event.stopPropagation(); SessionPanel.showMenu('${s.session_id}', '${this._esc(rootDir)}')" style="opacity:0.5">${icon('moreVertical')}</button>`;
            h += `</div>`;
        });
        return h;
    },

    _onDragStart(e, sid) { e.dataTransfer.setData('text/plain', sid); e.dataTransfer.effectAllowed = 'move'; },
    _onDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; e.currentTarget.classList.add('drag-over'); },
    async _onDrop(e, targetDir) {
        e.preventDefault(); e.currentTarget.classList.remove('drag-over');
        const sid = e.dataTransfer.getData('text/plain');
        if (!sid) return;
        try {
            const r = await fetch(`/api/sessions/${sid}/move`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ root_dir: targetDir }) });
            if (r.ok) { Utils.showSuccess('会话已移动'); this._refreshSessions(); }
        } catch (_) { Utils.showError('移动失败'); }
    },

    toggleProject(dir) { if (this.projects[dir]) { this.projects[dir].expanded = !this.projects[dir].expanded; this._render(); } },

    async selectSession(sessionId, rootDir) {
        this.activeSessionId = sessionId;
        this.activeProjectDir = rootDir;
        Chat.currentSessionId = sessionId;
        this._saveSessionToUrl(sessionId);
        this.attentionSessions.delete(sessionId);
        Chat._resetStreamingState();
        // 切换会话时清空等待区
        if (typeof PendingMessages !== 'undefined') PendingMessages.clear();
        Permission.closeIfSessionMismatch(sessionId);
        InputDialog.closeIfSessionMismatch(sessionId);
        MultiInputDialog.closeIfSessionMismatch(sessionId);
        WS.send({ type: 'set_active', session_id: sessionId });
        this._updateStatusBar(rootDir, sessionId);
        try { await Chat.loadHistory(sessionId); } catch (e) { console.error('[SessionPanel] 加载历史失败:', e); }
        const si = document.getElementById('search-input');
        if (si && si.value.trim()) this._onSearch(si.value);
        else this._render();
        // 检查是否有缓存的权限请求需要显示
        Permission.onSessionSwitched(sessionId);
    },

    createSession(rootDir) {
        this.activeSessionId = null;
        this.activeProjectDir = rootDir;
        Chat.currentSessionId = null;
        Chat._resetStreamingState();
        // 创建新会话时清空等待区
        if (typeof PendingMessages !== 'undefined') PendingMessages.clear();
        this._updateStatusBar(rootDir, null, true);
        Chat.clear();
        Chat._appendWelcomeScreen();
        this._render();
        // 通知后端创建 session
        WS.send({ type: 'create_session', root_dir: rootDir });
    },

    /** 创建自由聊天会话 */
    createFreeChat() {
        this._showFreeChatDialog();
    },

    /** 显示自由聊天创建对话框(系统提示词输入) */
    _showFreeChatDialog() {
        const old = document.getElementById('session-modal'); if (old) old.remove();
        const modal = document.createElement('div');
        modal.id = 'session-modal';
        modal.className = 'modal-overlay';
        const bodyHtml = `
            <div style="margin-bottom:8px;font-size:13px;color:var(--text-2)">自定义系统提示词(可选)</div>
            <div style="position:relative;">
                <textarea id="fc-system-prompt" rows="12" placeholder="输入自定义系统提示词,留空则使用默认提示词..."
                    style="width:100%;padding:10px;border:1px solid var(--border);border-radius:var(--r-md);
                    background:var(--bg-base);color:var(--text-1);font-size:13px;resize:vertical;
                    font-family:var(--font-mono, monospace);line-height:1.5;"></textarea>
                <button id="fc-optimize-btn" title="AI 优化提示词"
                    style="position:absolute;top:6px;right:6px;padding:2px 4px;border:none;
                    background:transparent;font-size:14px;cursor:pointer;opacity:0.5;
                    transition:opacity .15s;line-height:1;"
                    onmouseover="this.style.opacity='1'"
                    onmouseout="this.style.opacity='0.5'">
                    ✨
                </button>
            </div>`;
        modal.innerHTML = `<div class="modal-content" style="max-width:680px;">
            <div class="modal-title">新建自由聊天</div>
            <div class="modal-body">${bodyHtml}</div>
            <div class="modal-actions">
                <button class="btn btn-secondary" id="sm-cancel">取消</button>
                <button class="btn btn-primary" id="sm-confirm">确认</button>
            </div></div>`;
        document.body.appendChild(modal);

        const textarea = document.getElementById('fc-system-prompt');
        const optimizeBtn = document.getElementById('fc-optimize-btn');
        let optimizing = false;

        optimizeBtn.onclick = async () => {
            const prompt = textarea.value.trim();
            if (!prompt || optimizing) return;
            optimizing = true;
            optimizeBtn.textContent = '⏳';
            optimizeBtn.disabled = true;
            try {
                const resp = await fetch('/api/optimize-prompt', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt }),
                });
                if (resp.ok) {
                    const data = await resp.json();
                    if (data.optimized) textarea.value = data.optimized;
                }
            } catch (e) {
                console.error('优化失败:', e);
            } finally {
                optimizing = false;
                optimizeBtn.textContent = '✨';
                optimizeBtn.disabled = false;
            }
        };

        document.getElementById('sm-cancel').onclick = () => modal.remove();
        document.getElementById('sm-confirm').onclick = () => {
            const systemPrompt = textarea.value.trim() || null;
            modal.remove();
            this._doCreateFreeChat(systemPrompt);
        };
        textarea.focus();
    },

    /** 执行自由聊天会话创建 */
    _doCreateFreeChat(systemPrompt) {
        this.activeSessionId = null;
        this.activeProjectDir = '__free__';
        Chat.currentSessionId = null;
        Chat._resetStreamingState();
        // 创建自由聊天时清空等待区
        if (typeof PendingMessages !== 'undefined') PendingMessages.clear();
        this._updateStatusBar('__free__', null, true);
        Chat.clear();
        Chat._appendWelcomeScreen();
        this._render();
        const msg = { type: 'create_session', free_chat: true };
        if (systemPrompt) msg.system_prompt = systemPrompt;
        WS.send(msg);
    },

    _updateStatusBar(rootDir, sessionId, skipFetch = false) {
        const shortDir = rootDir === '__free__' ? '自由聊天' : rootDir ? (rootDir.split(/[/\\]/).pop() || rootDir) : '-';
        const pel = document.getElementById('status-project');
        if (pel) { pel.textContent = shortDir; pel.title = rootDir || ''; }
        const sel = document.getElementById('status-session');
        if (sel) sel.textContent = sessionId || '新会话';
        if (!sessionId || skipFetch) {
            const mel = document.getElementById('status-model'); if (mel) mel.textContent = '-';
            this._clearContextTimer(); this._updateContextDisplay(null);
            if (typeof VoiceMode !== 'undefined') VoiceMode.reset();
            return;
        }
        fetch(`/api/sessions/${sessionId}`).then(r => r.ok ? r.json() : null).then(d => { if (d && sel) sel.textContent = d.title || sessionId; }).catch(() => {});
        fetch(`/api/config?session_id=${sessionId}`).then(r => r.json()).then(d => {
            const mel = document.getElementById('status-model');
            if (mel && d.model_name?.length) mel.textContent = d.model_name[0];
            const pel2 = document.getElementById('status-permission');
            if (pel2 && d.permission_mode) {
                const map = { auto: 'Auto', manual: 'Manual', 'accept-all': 'Accept All', plan: 'Plan' };
                pel2.textContent = map[d.permission_mode] || d.permission_mode;
                pel2.className = `perm-mode ${d.permission_mode}`;
            }
            // 同步语音模式状态
            if (typeof VoiceMode !== 'undefined') {
                VoiceMode.setAvailable(d.voice_available || false);
                VoiceMode.setEnabled(d.voice_mode || false);
                VoiceMode.setAsrAvailable(d.asr_available || false);
            }
            // Computer Use 状态
            const cuEl = document.getElementById('status-cu');
            if (cuEl) {
                cuEl.style.display = '';
                cuEl.classList.toggle('active', d.computer_use_enabled);
                cuEl.title = d.computer_use_enabled ? 'Computer Use 已启用 (点击关闭)' : 'Computer Use 已关闭 (点击启用)';
                cuEl.onclick = () => this._toggleComputerUse(!d.computer_use_enabled);
            }
            // 工具解释模式状态
            const exEl = document.getElementById('status-explain');
            if (exEl) {
                if (d.explain_mode === true) {
                    exEl.className = 'status-explain active';
                    exEl.title = '工具解释模式: 所有工具 (点击关闭)';
                } else if (Array.isArray(d.explain_mode) && d.explain_mode.length > 0) {
                    exEl.className = 'status-explain partial';
                    exEl.title = `工具解释模式: ${d.explain_mode.join(', ')} (点击关闭)`;
                } else {
                    exEl.className = 'status-explain';
                    exEl.title = '工具解释模式: 关闭 (点击开启)';
                }
                exEl.onclick = () => this._toggleExplain(d.explain_mode ? false : true);
            }
        }).catch(() => {});
        this._fetchContextUsage(sessionId);
        this._startContextTimer(sessionId);
    },

    async _toggleComputerUse(enabled) {
        const sid = this.activeSessionId;
        if (!sid) return;
        try {
            await fetch('/api/config', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sid, computer_use_enabled: enabled }),
            });
            Utils.showToast(enabled ? 'Computer Use 已启用 (下条消息生效)' : 'Computer Use 已关闭 (下条消息生效)');
            this._updateStatusBar(this.activeProjectDir, sid);
        } catch (_) {
            Utils.showError('切换失败');
        }
    },

    async _toggleExplain(enabled) {
        const sid = this.activeSessionId;
        if (!sid) return;
        try {
            await fetch('/api/config', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sid, explain_mode: enabled }),
            });
            Utils.showToast(enabled ? '工具解释模式已开启 (下条消息生效)' : '工具解释模式已关闭 (下条消息生效)');
            this._updateStatusBar(this.activeProjectDir, sid);
        } catch (_) {
            Utils.showError('切换失败');
        }
    },

    _fetchContextUsage(sid) {
        if (!sid) return;
        fetch(`/api/context?session_id=${sid}`).then(r => r.ok ? r.json() : null).then(d => { if (d) this._updateContextDisplay(d); }).catch(() => {});
    },

    _updateContextDisplay(data) {
        const pctEl = document.getElementById('context-pct');
        const fillEl = document.getElementById('context-bar-fill');
        const contextEl = document.getElementById('status-context');
        if (!pctEl || !fillEl) return;
        if (!data || data.percentage === undefined) {
            pctEl.textContent = '-';
            fillEl.style.width = '0%';
            fillEl.className = 'context-mini-fill';
            if (contextEl) contextEl.title = '上下文使用率';
            return;
        }
        const pct = data.percentage;
        pctEl.textContent = `${pct}%`;
        fillEl.style.width = `${Math.min(100, pct)}%`;
        fillEl.className = 'context-mini-fill' + (pct >= 85 ? ' critical' : pct >= 70 ? ' warn' : '');
        if (contextEl) {
            const fmt = n => !n ? '0' : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;
            const lines = [
                `已用: ${fmt(data.used_tokens)} / ${fmt(data.limit)} (${pct}%)`,
                `系统提示: ${fmt(data.system_prompt_tokens)}`,
                `工具: ${fmt(data.tool_tokens)}`,
                `消息: ${fmt(data.message_tokens)}`,
                `剩余: ${fmt(data.free_tokens)}`,
            ];
            contextEl.title = lines.join('\n');
        }
    },

    _startContextTimer(sid) { this._clearContextTimer(); this._contextTimer = setInterval(() => { if (this.activeSessionId === sid) this._fetchContextUsage(sid); }, 30000); },
    _clearContextTimer() { if (this._contextTimer) { clearInterval(this._contextTimer); this._contextTimer = null; } },

    showMenu(sessionId, rootDir) {
        this._hideMenu();
        const el = document.querySelector(`.session-item[data-sid="${sessionId}"]`);
        if (!el) return;

        // 检查是否为微信会话
        const isWechat = sessionId.startsWith('wechat-');

        const menu = document.createElement('div');
        menu.className = 'context-menu';
        menu.id = 'session-context-menu';

        let menuHtml = '';
        menuHtml += `<div class="context-menu-item" onclick="SessionPanel._renameSession('${sessionId}')"><span class="ctx-icon">${icon('edit')}</span>重命名</div>`;

        if (!isWechat) {
            // 非微信会话显示完整菜单
            menuHtml += `<div class="context-menu-sep"></div>`;
            menuHtml += `<div class="context-menu-item" onclick="SessionPanel._generateTitleQuick('${sessionId}')"><span class="ctx-icon">${icon('sparkles')}</span>生成标题</div>`;
            menuHtml += `<div class="context-menu-sep"></div>`;
            menuHtml += `<div class="context-menu-item danger" onclick="SessionPanel._deleteSession('${sessionId}')"><span class="ctx-icon">${icon('trash')}</span>删除</div>`;
        } else {
            // 微信会话只显示删除
            menuHtml += `<div class="context-menu-sep"></div>`;
            menuHtml += `<div class="context-menu-item danger" onclick="SessionPanel._deleteSession('${sessionId}')"><span class="ctx-icon">${icon('trash')}</span>删除</div>`;
        }

        menu.innerHTML = menuHtml;
        document.body.appendChild(menu);
        const rect = el.getBoundingClientRect();
        menu.style.top = rect.bottom + 4 + 'px';
        menu.style.left = (rect.right - 140) + 'px';
        setTimeout(() => document.addEventListener('click', this._hideMenu, { once: true }), 0);
    },
    _hideMenu() { const m = document.getElementById('session-context-menu'); if (m) m.remove(); },

    _renameSession(sid) {
        this._hideMenu();
        this._showModal('重命名会话', `
            <div style="display:flex;gap:8px;margin-top:4px">
                <input type="text" id="rename-input" class="input" style="flex:1" />
                <button class="btn btn-secondary" id="ai-generate-btn" style="white-space:nowrap;font-size:12px">${icon('sparkles')} AI</button>
            </div>
        `, () => {
            const t = document.getElementById('rename-input').value.trim();
            if (t) fetch(`/api/sessions/${sid}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: t }) }).then(() => this._refreshSessions());
        });
        document.getElementById('ai-generate-btn').onclick = () => this._generateTitle(sid);
        setTimeout(() => document.getElementById('rename-input')?.focus(), 0);
    },

    async _generateTitle(sid) {
        const btn = document.getElementById('ai-generate-btn');
        const inp = document.getElementById('rename-input');
        if (!btn || !inp) return;
        btn.disabled = true; btn.textContent = '生成中...';
        try {
            const r = await fetch(`/api/sessions/${sid}/title/generate`, { method: 'POST' });
            const d = await r.json();
            if (d.title) { inp.value = d.title; inp.focus(); }
            else Utils.showToast('生成失败');
        } catch (_) { Utils.showToast('生成失败'); }
        finally { btn.disabled = false; btn.innerHTML = `${icon('sparkles')} AI`; }
    },

    async _generateTitleQuick(sid) {
        this._hideMenu();
        Utils.showToast('正在生成标题...');
        try {
            const r = await fetch(`/api/sessions/${sid}/title/generate`, { method: 'POST' });
            const d = await r.json();
            if (d.title) {
                await fetch(`/api/sessions/${sid}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: d.title }) });
                Utils.showSuccess('标题已更新');
                this._refreshSessions();
            } else Utils.showToast('生成失败');
        } catch (_) { Utils.showToast('生成失败'); }
    },

    async _deleteSession(sid) {
        this._hideMenu();
        if (!await Utils.confirm('确定删除此会话？此操作不可撤销。')) return;
        try {
            const r = await fetch(`/api/sessions/${sid}`, { method: 'DELETE' });
            if (!r.ok) throw new Error();
            if (this.activeSessionId === sid) { this.activeSessionId = null; Chat.clear(); }
            Utils.showSuccess('会话已删除');
            await this._refreshSessions();
        } catch (e) { Utils.showError('删除失败'); }
    },

    _showModal(title, bodyHtml, onConfirm) {
        const old = document.getElementById('session-modal'); if (old) old.remove();
        const modal = document.createElement('div');
        modal.id = 'session-modal';
        modal.className = 'modal-overlay';
        modal.innerHTML = `<div class="modal-content"><div class="modal-title">${Utils.escapeHtml(title)}</div><div class="modal-body">${bodyHtml}</div><div class="modal-actions"><button class="btn btn-secondary" id="sm-cancel">取消</button><button class="btn btn-primary" id="sm-confirm">确认</button></div></div>`;
        document.body.appendChild(modal);
        document.getElementById('sm-cancel').onclick = () => modal.remove();
        document.getElementById('sm-confirm').onclick = () => { onConfirm(); modal.remove(); };
    },

    async _onSearch(kw) {
        if (!kw.trim()) { this._render(); return; }
        try {
            const r = await fetch(`/api/sessions/search?keyword=${encodeURIComponent(kw)}`);
            const results = await r.json();
            const tree = document.getElementById('session-tree');
            let html = '<div style="padding:8px 14px;font-size:12px;color:var(--text-3)">搜索结果</div>';
            results.forEach(s => {
                html += `<div class="session-item" data-sid="${s.session_id}" onclick="SessionPanel.selectSession('${s.session_id}', '${this._esc(s.root_dir || '')}')">`;
                html += `<span class="session-icon">${icon('chat')}</span>`;
                html += `<div class="session-info"><div class="session-title">${Utils.escapeHtml(s.title || s.session_id)}</div><div class="session-meta">${Utils.formatRelativeTime(s.end_time || s.start_time)}</div></div></div>`;
            });
            if (!results.length) html += '<div class="no-results">无匹配结果</div>';
            tree.innerHTML = html;
        } catch (e) { console.error('搜索失败:', e); }
    },

    _showNewProjectDialog() {
        const modal = document.getElementById('new-project-modal');
        if (modal) modal.classList.remove('hidden');
        const inp = document.getElementById('project-path'); if (inp) inp.value = '';
        const list = document.getElementById('dir-list'); if (list) list.innerHTML = '';
        document.getElementById('browse-btn').onclick = () => this._browseDir('');
        document.getElementById('project-confirm').onclick = () => {
            const p = document.getElementById('project-path').value.trim();
            if (p) { this.projects[p] = { sessions: [], expanded: true, created_at: this._now() }; this._saveProjects(); this._render(); modal.classList.add('hidden'); }
        };
        document.getElementById('project-cancel').onclick = () => modal.classList.add('hidden');
    },

    async _browseDir(path) {
        try {
            const r = await fetch(`/api/dirs?path=${encodeURIComponent(path)}`);
            const dirs = await r.json();
            document.getElementById('project-path').value = path;
            document.getElementById('dir-list').innerHTML = dirs.filter(d => d.is_dir).map(d =>
                `<div class="dir-item" onclick="SessionPanel._browseDir('${this._esc(d.path)}')">${icon('folder')} ${Utils.escapeHtml(d.name)}</div>`
            ).join('');
        } catch (e) { console.error('浏览目录失败:', e); }
    },

    _saveProjects() {
        const data = {};
        Object.entries(this.projects).forEach(([dir, proj]) => {
            data[dir] = { expanded: proj.expanded, created_at: proj.created_at || null };
        });
        localStorage.setItem('uniclaw_projects', JSON.stringify(data));
        localStorage.setItem('uniclaw_category_expanded', JSON.stringify(this.categoryExpanded));
    },
    _esc(s) { return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'"); },
    _now() { const d = new Date(); const pad = n => String(n).padStart(2, '0'); return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; },

    // 从 mouse/touch 事件中提取坐标
    _getEventCoords(event) {
        if (event.touches && event.touches.length > 0) {
            return { x: event.touches[0].clientX, y: event.touches[0].clientY };
        }
        return { x: event.clientX, y: event.clientY };
    },

    _showTip(el, event) {
        const raw = el.getAttribute('data-tip');
        if (!raw) return;
        let d;
        try { d = JSON.parse(raw); } catch { return; }

        let rows;
        if (d.id) {
            // 会话 tooltip
            const fmtTime = t => t ? t.replace('T', ' ').substring(0, 19) : '-';
            rows = [['标题', d.title || '-'], ['ID', d.id], ['创建', fmtTime(d.start)], ['活跃', fmtTime(d.end)], ['路径', d.dir || '-'], ['消息', d.msg]];
        } else {
            // 项目 tooltip
            rows = [['路径', d.dir], ['创建', d.created || '-'], ['会话', d.sessions], ['最新', d.latest || '-']];
        }
        const inner = rows.map(([k, v]) => `<span style="color:var(--text-3)">${k}: </span><span style="color:var(--text-0)">${Utils.escapeHtml(String(v))}</span>`).join('<br>');

        let tip = document.getElementById('session-tooltip');
        if (!tip) {
            tip = document.createElement('div');
            tip.id = 'session-tooltip';
            document.body.appendChild(tip);
        }
        tip.innerHTML = inner;
        tip.style.display = 'block';
        this._moveTip(event);
    },

    _moveTip(event) {
        const tip = document.getElementById('session-tooltip');
        if (!tip || tip.style.display === 'none') return;
        const { x, y } = this._getEventCoords(event);
        tip.style.left = `${x + 12}px`;
        tip.style.top = `${y + 12}px`;
    },

    _hideTip() {
        const tip = document.getElementById('session-tooltip');
        if (tip) tip.style.display = 'none';
    },
};
