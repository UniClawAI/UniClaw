/* multi-input-dialog.js — 多问题 Tab 输入弹窗组件 */

const MultiInputDialog = {
    currentRequest: null,
    _countdownTimer: null,
    _countdownSeconds: 300,
    _countdownCancelled: false,
    _questions: [],
    _currentTab: 0,
    _selections: {},   // {tabIndex: optionIndex}
    _otherTexts: {},   // {tabIndex: string}
    _otherActive: false,

    init() {
        FloatingWindow.init('multi-input-modal');
        WS.on('multi_input_request', msg => this._onRequest(msg));
        document.getElementById('multi-input-submit').onclick = () => this._submit();
        document.getElementById('multi-input-cancel-countdown').onclick = () => this._cancelCountdown();
        // 拦截 backspace 防止浏览器后退
        document.getElementById('multi-input-modal').addEventListener('keydown', e => {
            if (e.key === 'Backspace' && !e.target.matches('input, textarea')) {
                e.preventDefault();
            }
        });
    },

    _onRequest(msg) {
        if (!msg || msg.session_id !== SessionPanel.activeSessionId) return;
        this.currentRequest = msg;
        this._questions = msg.questions || [];
        this._currentTab = 0;
        this._selections = {};
        this._otherTexts = {};
        this._otherActive = false;

        document.getElementById('multi-input-title').textContent = msg.title || '请选择';
        this._renderTabs();
        this._renderQuestion();
        this._renderOptions();
        const countdownEl = document.getElementById('multi-input-countdown');
        const cancelBtn = document.getElementById('multi-input-cancel-countdown');
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
        FloatingWindow.show('multi-input-modal');
        if (!msg.countdown_cancelled) {
            this._startCountdown(msg.created_at, msg.timeout);
        }
    },

    closeIfSessionMismatch(targetSid) {
        if (this.currentRequest && this.currentRequest.session_id !== targetSid) {
            this._stopCountdown();
            FloatingWindow.hide('multi-input-modal');
            this.currentRequest = null;
        }
    },

    _renderTabs() {
        const container = document.getElementById('multi-input-tabs');
        container.innerHTML = '';
        this._questions.forEach((q, i) => {
            const tab = document.createElement('div');
            tab.className = 'multi-input-tab' + (i === this._currentTab ? ' active' : '');
            const shortQ = q.question && q.question.length > 10
                ? q.question.substring(0, 10) + '…'
                : (q.question || `Q${i + 1}`);
            tab.textContent = `Q${i + 1}: ${shortQ}`;
            tab.onclick = () => this._switchTab(i);
            container.appendChild(tab);
        });
        this._setupTabScroll();
    },

    _setupTabScroll() {
        const container = document.getElementById('multi-input-tabs');
        const arrowL = document.getElementById('multi-input-arrow-left');
        const arrowR = document.getElementById('multi-input-arrow-right');

        const updateArrows = () => {
            const sl = container.scrollLeft;
            const maxSl = container.scrollWidth - container.clientWidth;
            arrowL.classList.toggle('hidden', sl <= 2);
            arrowR.classList.toggle('hidden', sl >= maxSl - 2);
        };

        // 移除旧监听器, 绑定新的
        container.onscroll = updateArrows;
        arrowL.onclick = () => container.scrollBy({ left: -120, behavior: 'smooth' });
        arrowR.onclick = () => container.scrollBy({ left: 120, behavior: 'smooth' });

        // 需要等 DOM 渲染完再检测溢出
        requestAnimationFrame(() => {
            const hasOverflow = container.scrollWidth > container.clientWidth + 4;
            if (!hasOverflow) {
                arrowL.classList.add('hidden');
                arrowR.classList.add('hidden');
            } else {
                updateArrows();
                // 滚动当前激活 tab 到可见区域
                const active = container.querySelector('.multi-input-tab.active');
                if (active) active.scrollIntoView({ inline: 'nearest', block: 'nearest' });
            }
        });
    },

    _renderQuestion() {
        const container = document.getElementById('multi-input-question');
        const q = this._questions[this._currentTab];
        container.textContent = q ? q.question : '';
    },

    _normalizeOpt(opt) {
        if (typeof opt === 'string') return opt;
        if (typeof opt === 'object' && opt !== null) {
            return opt.text || opt.label || opt.value || opt.name || opt.option || JSON.stringify(opt);
        }
        return String(opt);
    },

    _renderOptions() {
        const container = document.getElementById('multi-input-options');
        container.innerHTML = '';
        const q = this._questions[this._currentTab];
        if (!q) return;
        const options = (q.options || []).map(o => this._normalizeOpt(o));
        const sel = this._selections[this._currentTab];

        options.forEach((opt, i) => {
            const item = document.createElement('div');
            item.className = 'multi-input-option' + (sel === i ? ' selected' : '');
            item.innerHTML = `<span class="multi-input-radio">${sel === i ? '●' : '○'}</span> ${this._escapeHtml(opt)}`;
            item.onclick = () => this._selectOption(i);
            container.appendChild(item);
        });

        // "其他"选项
        const otherIdx = options.length;
        const otherItem = document.createElement('div');
        otherItem.className = 'multi-input-option multi-input-other' + (sel === otherIdx ? ' selected' : '');

        const radio = document.createElement('span');
        radio.className = 'multi-input-radio';
        radio.textContent = sel === otherIdx ? '●' : '○';
        otherItem.appendChild(radio);

        const label = document.createElement('span');
        label.textContent = ' 其他: ';
        otherItem.appendChild(label);

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'multi-input-other-input';
        input.placeholder = '请输入你的想法...';
        input.value = this._otherTexts[this._currentTab] || '';
        input.onclick = (e) => { e.stopPropagation(); this._activateOther(); };
        input.oninput = (e) => {
            this._otherTexts[this._currentTab] = e.target.value;
            if (this._selections[this._currentTab] !== otherIdx) {
                this._selectOption(otherIdx);
            }
        };
        input.onkeydown = (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this._otherActive = false;
                input.blur();
                this._renderOptions();
                this._checkCanSubmit();
            }
        };
        otherItem.appendChild(input);

        otherItem.onclick = (e) => {
            if (e.target === input) return;
            this._selectOption(otherIdx);
            this._activateOther();
        };
        container.appendChild(otherItem);
    },

    _selectOption(idx) {
        this._selections[this._currentTab] = idx;
        const q = this._questions[this._currentTab];
        const options = (q.options || []).map(o => this._normalizeOpt(o));
        if (idx !== options.length) {
            this._otherActive = false;
        }
        this._renderOptions();
        this._checkCanSubmit();
        // 如果选中的是"其他", 恢复输入框焦点
        if (idx === options.length) {
            this._activateOther();
        }
    },

    _activateOther() {
        this._otherActive = true;
        const input = document.querySelector('.multi-input-other-input');
        if (input) setTimeout(() => input.focus(), 0);
    },

    _switchTab(idx) {
        this._currentTab = idx;
        this._otherActive = false;
        this._renderTabs();
        this._renderQuestion();
        this._renderOptions();
    },

    _checkCanSubmit() {
        const btn = document.getElementById('multi-input-submit');
        const allSelected = this._questions.every((_, i) => this._selections[i] !== undefined);
        btn.disabled = !allSelected;
        btn.style.opacity = allSelected ? '1' : '0.5';
    },

    _submit() {
        if (!this.currentRequest) return;
        const allSelected = this._questions.every((_, i) => this._selections[i] !== undefined);
        if (!allSelected) return;

        this._stopCountdown();
        const answers = {};
        this._questions.forEach((q, i) => {
            const sel = this._selections[i];
            const options = (q.options || []).map(o => this._normalizeOpt(o));
            const label = q.question || `Q${i + 1}`;
            if (sel !== undefined && sel < options.length) {
                answers[label] = options[sel];
            } else if (sel === options.length) {
                const otherText = (this._otherTexts[i] || '').trim();
                answers[label] = otherText ? `其他:${otherText}` : '其他';
            }
        });

        const value = JSON.stringify(answers);
        WS.send({
            type: 'input_response',
            session_id: this.currentRequest.session_id,
            id: this.currentRequest.id,
            value,
        });
        FloatingWindow.hide('multi-input-modal');
        this.currentRequest = null;
    },

    _startCountdown(createdAt, timeout) {
        this._stopCountdown();
        const T = timeout || 300;
        if (createdAt) {
            const elapsed = Math.floor(Date.now() / 1000) - createdAt;
            this._countdownSeconds = Math.max(0, T - elapsed);
        } else {
            this._countdownSeconds = T;
        }
        const el = document.getElementById('multi-input-countdown');
        if (!el) return;
        el.textContent = this._fmtTime(this._countdownSeconds);
        if (this._countdownSeconds <= 0) {
            this._submit();
            Utils.showToast('多问题输入已超时');
            return;
        }
        this._countdownTimer = setInterval(() => {
            if (this._countdownCancelled) return;
            this._countdownSeconds--;
            el.textContent = this._fmtTime(this._countdownSeconds);
            if (this._countdownSeconds <= 0) {
                this._submit();
                Utils.showToast('多问题输入已超时');
            }
        }, 1000);
    },

    _stopCountdown() {
        if (this._countdownTimer) {
            clearInterval(this._countdownTimer);
            this._countdownTimer = null;
        }
        this._countdownCancelled = false;
    },

    _cancelCountdown() {
        this._countdownCancelled = true;
        this._stopCountdown();
        const el = document.getElementById('multi-input-countdown');
        if (el) el.style.display = 'none';
        const btn = document.getElementById('multi-input-cancel-countdown');
        if (btn) btn.style.display = 'none';
        if (this.currentRequest) {
            WS.send({ type: 'input_cancel_countdown', id: this.currentRequest.id, session_id: this.currentRequest.session_id });
        }
    },

    _fmtTime(s) {
        return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`;
    },

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },
};
