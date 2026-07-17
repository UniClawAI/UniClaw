/* ws.js — WebSocket 客户端管理 */

const WS = {
    socket: null,
    handlers: {},
    reconnectTimer: null,
    reconnectDelay: 1000,
    connected: false,

    /** 从 cookie 获取 token */
    _getCookie(name) {
        const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
        return match ? match[2] : null;
    },

    /** 连接 WebSocket */
    connect() {
        // 优先从 localStorage 获取,其次从 cookie 获取
        let token = localStorage.getItem('uniclaw_token');
        if (!token) {
            token = this._getCookie('uniclaw_token');
        }
        console.log('[WS] 连接, token:', token ? token.substring(0, 20) + '...' : 'null');
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        // 可信 IP 可以不带 token 连接,后端会自动放行
        const tokenParam = token ? `?token=${encodeURIComponent(token)}` : '';
        const url = `${protocol}//${location.host}/ws${tokenParam}`;
        console.log('[WS] URL:', url);
        this.socket = new WebSocket(url);

        this.socket.onopen = () => {
            console.log('[WS] 已连接');
            this.connected = true;
            this.reconnectDelay = 1000;
            this._updateStatus(true);
            this._emit('connected');
        };

        this.socket.onerror = (e) => {
            console.error('[WS] 错误:', e);
        };

        this.socket.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                this._emit(msg.event, msg);
            } catch (err) {
                console.error('[WS] 解析消息失败:', err);
            }
        };

        this.socket.onclose = (e) => {
            console.log('[WS] 连接断开, code:', e.code, 'reason:', e.reason, 'wasClean:', e.wasClean);
            this.connected = false;
            this._updateStatus(false);
            this._emit('disconnected');
            // 认证失败,跳转登录页
            if (e.code === 4001) {
                console.log('[WS] 认证失败,跳转登录页');
                localStorage.removeItem('uniclaw_token');
                document.cookie = 'uniclaw_token=;path=/;max-age=0';
                window.location.href = '/login.html';
                return;
            }
            // 其他断开情况,尝试重连(app.js 会通过事件显示 toast)
            if (!this._getCookie('uniclaw_token') && !localStorage.getItem('uniclaw_token')) {
                console.log('[WS] 没有token,尝试重连(可信IP可能无需认证)');
            } else {
                console.log('[WS] 连接断开,尝试重连');
            }
            this._scheduleReconnect();
        };

        this.socket.onerror = (e) => {
            console.error('[WS] 错误:', e);
        };
    },

    /** 发送消息 */
    send(msg) {
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify(msg));
        } else {
            console.warn('[WS] 未连接,无法发送消息');
            Utils.showError('未连接到服务器');
        }
    },

    /** 注册事件处理器 */
    on(event, handler) {
        if (!this.handlers[event]) this.handlers[event] = [];
        this.handlers[event].push(handler);
    },

    /** 移除事件处理器 */
    off(event, handler) {
        if (!this.handlers[event]) return;
        this.handlers[event] = this.handlers[event].filter(h => h !== handler);
    },

    /** 触发事件 */
    _emit(event, data) {
        const handlers = this.handlers[event] || [];
        handlers.forEach(h => {
            try { h(data); } catch (e) { console.error(`[WS] 处理器错误 (${event}):`, e); }
        });
    },

    /** 更新连接状态指示器 */
    _updateStatus(connected) {
        const dot = document.getElementById('connection-dot');
        if (dot) {
            dot.className = `connection-dot ${connected ? 'connected' : 'disconnected'}`;
        }
        const model = document.getElementById('status-model');
        if (model) {
            model.textContent = connected ? '已连接' : '未连接';
        }
    },

    /** 自动重连 */
    _scheduleReconnect() {
        if (this.reconnectTimer) return;
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            console.log('[WS] 尝试重连...');
            this.connect();
        }, this.reconnectDelay);
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, 60000);
    },
};
