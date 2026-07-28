/* input-dialog.js — 通用输入弹窗组件 */

const InputDialog = {
    currentRequest: null,
    _countdownTimer: null,
    _countdownSeconds: 300,
    _countdownCancelled: false,

    init() {
        FloatingWindow.init('input-dialog-modal');
        WS.on('input_request', msg => this._onRequest(msg));
        document.getElementById('input-dialog-confirm').onclick = () => this._respond();
        document.getElementById('input-cancel-countdown').onclick = () => this._cancelCountdown();
        document.getElementById('input-dialog-text').addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this._respond(); }
        });
        // 拦截 backspace 防止浏览器后退
        document.getElementById('input-dialog-modal').addEventListener('keydown', e => {
            if (e.key === 'Backspace' && !e.target.matches('input, textarea')) {
                e.preventDefault();
            }
        });
    },

    _onRequest(msg) {
        if (!msg || msg.session_id !== SessionPanel.activeSessionId) return;
        this.currentRequest = msg;
        document.getElementById('input-dialog-title').textContent = msg.title || '输入';
        document.getElementById('input-dialog-prompt').textContent = msg.prompt || '';
        const input = document.getElementById('input-dialog-text');
        input.value = '';
        const countdownEl = document.getElementById('input-countdown');
        const cancelBtn = document.getElementById('input-cancel-countdown');
        if (msg.countdown_cancelled) {
            this._countdownCancelled = true;
            this._stopCountdown();
            if (countdownEl) countdownEl.style.display = 'none';
            if (cancelBtn) cancelBtn.style.display = 'none';
        } else {
            this._countdownCancelled = false;
            if (countdownEl) countdownEl.style.display = '';
            if (cancelBtn) cancelBtn.style.display = '';
        }
        FloatingWindow.show('input-dialog-modal');
        this._resizeDialog();
        setTimeout(() => input.focus(), 0);
        if (!msg.countdown_cancelled) {
            this._startCountdown(msg.created_at, msg.timeout);
        }
    },

    closeIfSessionMismatch(targetSid) {
        if (this.currentRequest && this.currentRequest.session_id !== targetSid) {
            this._stopCountdown();
            FloatingWindow.hide('input-dialog-modal');
            this.currentRequest = null;
        }
    },

    _respond(value) {
        if (!this.currentRequest) return;
        this._stopCountdown();
        if (value === undefined) value = document.getElementById('input-dialog-text').value;
        WS.send({ type: 'input_response', session_id: this.currentRequest.session_id, id: this.currentRequest.id, value });
        FloatingWindow.hide('input-dialog-modal');
        this.currentRequest = null;
    },

    _resizeDialog() {
        const modal = document.querySelector('#input-dialog-modal .modal-content');
        const prompt = document.getElementById('input-dialog-prompt');
        const text = prompt.textContent || '';
        const lines = text.split('\n');
        const maxLineLen = Math.max(...lines.map(l => l.length), 0);
        const lineCount = lines.length;

        // Estimate character width: CJK ~18px, Latin ~8px at --text-sm (12.8px)
        let totalWidth = 0;
        let maxLineWidth = 0;
        for (const line of lines) {
            let lineWidth = 0;
            for (const ch of line) {
                const w = ch.charCodeAt(0) > 0x7F ? 18 : 8;
                lineWidth += w;
                totalWidth += w;
            }
            maxLineWidth = Math.max(maxLineWidth, lineWidth);
        }
        const avgLineLen = lineCount > 0 ? totalWidth / lineCount : 0;

        // Modal padding (24px each side) + prompt padding (12px each side) + border + buffer
        const chrome = 24 * 2 + 12 * 2 + 2 + 32;
        const idealWidth = Math.round(Math.max(Math.min(maxLineWidth + chrome, 720), 320));

        // Short prompts get a compact dialog
        if (lineCount <= 2 && maxLineLen < 30) {
            modal.style.width = 'min(90vw, 400px)';
        } else {
            modal.style.width = `min(90vw, ${idealWidth}px)`;
        }

        // Auto-adjust height: measure actual rendered content
        // Reset height to auto first to get accurate scrollHeight
        prompt.style.height = 'auto';
        const scrollH = prompt.scrollHeight;
        // Line height ~20px at --text-sm, estimate min height for 1-2 lines
        const lineHeight = 20;
        const minHeight = lineHeight * 2;
        // Cap at 60vh to avoid oversized dialogs
        const maxHeight = Math.round(window.innerHeight * 0.6);
        const idealHeight = Math.max(Math.min(scrollH, maxHeight), minHeight);
        prompt.style.height = `${idealHeight}px`;
    },

    _startCountdown(createdAt, timeout) {
        this._stopCountdown();
        const T = timeout || 300;
        if (createdAt) { const elapsed = Math.floor(Date.now() / 1000) - createdAt; this._countdownSeconds = Math.max(0, T - elapsed); }
        else this._countdownSeconds = T;
        const el = document.getElementById('input-countdown');
        if (!el) return;
        el.textContent = this._fmtTime(this._countdownSeconds);
        if (this._countdownSeconds <= 0) { this._respond(''); Utils.showToast('输入请求已超时'); return; }
        this._countdownTimer = setInterval(() => {
            if (this._countdownCancelled) return;
            this._countdownSeconds--;
            el.textContent = this._fmtTime(this._countdownSeconds);
            if (this._countdownSeconds <= 0) { this._respond(''); Utils.showToast('输入请求已超时'); }
        }, 1000);
    },

    _stopCountdown() {
        if (this._countdownTimer) { clearInterval(this._countdownTimer); this._countdownTimer = null; }
        this._countdownCancelled = false;
    },

    _cancelCountdown() {
        this._countdownCancelled = true;
        this._stopCountdown();
        const el = document.getElementById('input-countdown');
        if (el) el.style.display = 'none';
        const btn = document.getElementById('input-cancel-countdown');
        if (btn) btn.style.display = 'none';
        if (this.currentRequest) {
            WS.send({ type: 'input_cancel_countdown', id: this.currentRequest.id, session_id: this.currentRequest.session_id });
        }
    },

    _fmtTime(s) { return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`; },
};
