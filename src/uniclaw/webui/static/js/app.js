/* app.js — 应用主入口 */

const App = {
    init() {
        WS.connect();
        Chat.init();
        Input.init();
        SessionPanel.init();
        Permission.init();
        InputDialog.init();
        MultiInputDialog.init();
        Sidebar.init();
        MsgNav.init();
        AudioPlayer.init();

        this._bindGlobalEvents();
        this._bindDragHandles();
        this._bindPanelToggles();
        this._restorePanelState();

        Utils.hideLoading();
        console.log('[App] UniClaw WebUI 已初始化');
    },

    _bindGlobalEvents() {
        document.addEventListener('keydown', e => {
            if (e.shiftKey && e.key === 'Tab') { e.preventDefault(); Permission._cycleMode(); }
            if (e.key === 'F3') { e.preventDefault(); this._toggleLeftPanel(); }
            if (e.key === 'Escape') {
                const modals = document.querySelectorAll('.modal-overlay:not(.hidden)');
                if (modals.length) modals.forEach(m => m.classList.add('hidden'));
                else {
                    const sid = SessionPanel.activeSessionId;
                    if (sid) { WS.send({ type: 'cancel', session_id: sid }); Utils.showToast('已发送取消请求'); }
                }
                Input._hideCompletion();
            }
        });

        WS.on('connected', () => {
            const dot = document.getElementById('connection-dot');
            if (dot) dot.className = 'connection-dot connected';
            document.getElementById('status-model').textContent = '已连接';
        });
        WS.on('disconnected', () => {
            const dot = document.getElementById('connection-dot');
            if (dot) dot.className = 'connection-dot disconnected';
            document.getElementById('status-model').textContent = '未连接';
            Utils.showError('连接断开,正在重连...');
        });
    },

    _bindDragHandles() {
        this._makeDraggable('left-drag', 'left-panel', 'left');
        this._makeDraggable('right-drag', 'right-panel', 'right');
    },

    _makeDraggable(handleId, panelId, side) {
        const handle = document.getElementById(handleId);
        const panel = document.getElementById(panelId);
        if (!handle || !panel) return;
        let startX, startW;
        handle.addEventListener('mousedown', e => {
            startX = e.clientX; startW = panel.offsetWidth;
            const onMove = e2 => {
                const dx = e2.clientX - startX;
                let w = side === 'left' ? startW + dx : startW - dx;
                w = Math.max(200, Math.min(side === 'left' ? 500 : 600, w));
                panel.style.width = w + 'px';
            };
            const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                document.body.style.cursor = ''; document.body.style.userSelect = '';
                localStorage.setItem(`panel_width_${side}`, panel.style.width);
            };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none';
        });
    },

    _bindPanelToggles() {
        document.getElementById('toggle-left').onclick = () => this._toggleLeftPanel();
        document.getElementById('toggle-right').onclick = () => this._toggleRightPanel();
        document.getElementById('toggle-usage').onclick = () => this._toggleUsage();
        document.getElementById('left-panel').addEventListener('click', e => { if (e.currentTarget.classList.contains('collapsed')) this._toggleLeftPanel(); });
        document.getElementById('right-panel').addEventListener('click', e => { if (e.currentTarget.classList.contains('collapsed')) this._toggleRightPanel(); });
    },

    _toggleLeftPanel() {
        const p = document.getElementById('left-panel'), d = document.getElementById('left-drag');
        p.classList.toggle('collapsed');
        d.style.display = p.classList.contains('collapsed') ? 'none' : '';
        localStorage.setItem('left_collapsed', p.classList.contains('collapsed'));
    },

    _toggleRightPanel() {
        const p = document.getElementById('right-panel'), d = document.getElementById('right-drag');
        p.classList.toggle('collapsed');
        d.style.display = p.classList.contains('collapsed') ? 'none' : '';
        if (!p.classList.contains('collapsed')) Sidebar.switchTab(Sidebar.currentTab);
        localStorage.setItem('right_collapsed', p.classList.contains('collapsed'));
    },

    _toggleUsage() {
        const btn = document.getElementById('toggle-usage');
        const chat = document.getElementById('chat-messages');
        if (!btn || !chat) return;
        const show = chat.classList.toggle('show-usage');
        btn.classList.toggle('active', show);
        localStorage.setItem('show_usage', show ? '1' : '0');
    },

    _restorePanelState() {
        const lw = localStorage.getItem('panel_width_left');
        if (lw) document.getElementById('left-panel').style.width = lw;
        const rw = localStorage.getItem('panel_width_right');
        if (rw) document.getElementById('right-panel').style.width = rw;
        if (localStorage.getItem('left_collapsed') === 'true') {
            document.getElementById('left-panel').classList.add('collapsed');
            document.getElementById('left-drag').style.display = 'none';
        }
        if (localStorage.getItem('right_collapsed') === 'true') {
            document.getElementById('right-panel').classList.add('collapsed');
            document.getElementById('right-drag').style.display = 'none';
        }
        if (localStorage.getItem('show_usage') === '1') {
            document.getElementById('chat-messages').classList.add('show-usage');
            document.getElementById('toggle-usage').classList.add('active');
        }
    },
};

document.addEventListener('DOMContentLoaded', () => App.init());
