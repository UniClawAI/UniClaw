/* multi-input-dialog.js — 多问题 Tab 输入弹窗组件 */

const MultiInputDialog = {
    currentRequest: null,
    _countdownTimer: null,
    _countdownSeconds: 300,
    _countdownCancelled: false,
    _questions: [],
    _currentTab: 0,
    _selections: {},   // {tabIndex: optionIndex | [optionIndex, ...]}
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
        // 多选问题初始化为空数组,单选为 undefined
        this._selections = {};
        this._questions.forEach((q, i) => {
            if (q.multi) this._selections[i] = [];
        });
        this._otherTexts = {};
        this._otherActive = false;

        document.getElementById('multi-input-title').textContent = msg.title || '请选择';
        this._renderTabs();
        this._renderQuestion();
        this._renderOptions();
        this._checkCanSubmit();
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
        // 切会话仅隐藏, 不提交: 后端 set_active 会重发该请求, 用户切回仍可作答。
        // 在此提交会把"中途离开"变成正式回答(部分选择被模型当成完整答复), 超时路径才用 _timeoutSubmit。
        if (this.currentRequest && this.currentRequest.session_id !== targetSid) {
            this._stopCountdown();
            FloatingWindow.hide('multi-input-modal');
            this.currentRequest = null;
        }
    },

    /** 会话被删除时调用: 不会再有重发, 用空回答唤醒后端 future 并隐藏。 */
    abandonFor(sessionId) {
        const req = this.currentRequest;
        if (req && req.session_id === sessionId) {
            this._stopCountdown();
            WS.send({ type: 'input_response', session_id: req.session_id, id: req.id, value: '' });
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

    _isMulti(tabIndex) {
        const q = this._questions[tabIndex];
        return q && q.multi === true;
    },

    _renderOptions() {
        const container = document.getElementById('multi-input-options');
        container.innerHTML = '';
        const q = this._questions[this._currentTab];
        if (!q) return;
        const options = (q.options || []).map(o => this._normalizeOpt(o));
        const isMulti = this._isMulti(this._currentTab);
        const sel = this._selections[this._currentTab];
        // 多选时 sel 是数组,单选时是数字
        const selectedSet = isMulti ? new Set(sel || []) : new Set(sel !== undefined ? [sel] : []);

        options.forEach((opt, i) => {
            const isSelected = selectedSet.has(i);
            const item = document.createElement('div');
            item.className = 'multi-input-option' + (isSelected ? ' selected' : '');
            const icon = isMulti
                ? (isSelected ? '☑' : '☐')
                : (isSelected ? '●' : '○');
            item.innerHTML = `<span class="multi-input-radio ${isMulti ? 'checkbox' : ''}">${icon}</span> ${this._escapeHtml(opt)}`;
            item.onclick = () => this._selectOption(i);
            container.appendChild(item);
        });

        // "其他"选项
        const otherIdx = options.length;
        const isOtherSelected = selectedSet.has(otherIdx);
        const otherItem = document.createElement('div');
        otherItem.className = 'multi-input-option multi-input-other' + (isOtherSelected ? ' selected' : '');

        const radio = document.createElement('span');
        radio.className = 'multi-input-radio' + (isMulti ? ' checkbox' : '');
        radio.textContent = isMulti
            ? (isOtherSelected ? '☑' : '☐')
            : (isOtherSelected ? '●' : '○');
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
            if (!selectedSet.has(otherIdx)) {
                this._selectOption(otherIdx);
            }
        };
        input.onkeydown = (e) => {
            if (Utils.isImeComposing(e)) return;  // IME 组合中不触发确认
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
        const isMulti = this._isMulti(this._currentTab);
        const q = this._questions[this._currentTab];
        const options = (q.options || []).map(o => this._normalizeOpt(o));
        const otherIdx = options.length;

        if (isMulti) {
            // 多选:切换选中状态
            let arr = this._selections[this._currentTab] || [];
            if (!Array.isArray(arr)) arr = [];
            const pos = arr.indexOf(idx);
            if (pos === -1) {
                arr.push(idx);
                // 选中"其他"时激活编辑模式
                if (idx === otherIdx) {
                    this._otherActive = true;
                }
            } else {
                arr.splice(pos, 1);
                // 取消选中"其他"时停用编辑模式
                if (idx === otherIdx) {
                    this._otherActive = false;
                }
            }
            this._selections[this._currentTab] = arr;
        } else {
            // 单选:直接设置
            this._selections[this._currentTab] = idx;
            if (idx !== otherIdx) {
                this._otherActive = false;
            }
        }
        this._renderOptions();
        this._checkCanSubmit();
        // 如果选中的是"其他", 恢复输入框焦点
        if (idx === otherIdx) {
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
        const allSelected = this._questions.every((q, i) => {
            const sel = this._selections[i];
            if (q.multi) {
                // 多选:数组且非空
                return Array.isArray(sel) && sel.length > 0;
            }
            // 单选:有值
            return sel !== undefined;
        });
        btn.disabled = !allSelected;
        btn.style.opacity = allSelected ? '1' : '0.5';
    },

    /** 从当前选择构造答案字典(跳过未作答的问题) */
    _buildAnswers() {
        const answers = {};
        this._questions.forEach((q, i) => {
            const sel = this._selections[i];
            const options = (q.options || []).map(o => this._normalizeOpt(o));
            const label = q.question || `Q${i + 1}`;

            if (q.multi && Array.isArray(sel)) {
                // 多选:返回选项列表
                const selectedOpts = [];
                sel.forEach(idx => {
                    if (idx < options.length) {
                        selectedOpts.push(options[idx]);
                    } else if (idx === options.length) {
                        const otherText = (this._otherTexts[i] || '').trim();
                        selectedOpts.push(otherText ? `其他:${otherText}` : '其他');
                    }
                });
                if (selectedOpts.length) answers[label] = selectedOpts;
            } else if (sel !== undefined && sel < options.length) {
                // 单选:返回单个值
                answers[label] = options[sel];
            } else if (sel !== undefined && sel === options.length) {
                // sel 必须已定义: 未作答时 undefined === 0(无选项)会误入此分支伪造"其他"
                const otherText = (this._otherTexts[i] || '').trim();
                answers[label] = otherText ? `其他:${otherText}` : '其他';
            }
        });
        return answers;
    },

    _submit() {
        this._stopCountdown();
        if (!this.currentRequest) return;
        const allSelected = this._questions.every((q, i) => {
            const sel = this._selections[i];
            if (q.multi) {
                return Array.isArray(sel) && sel.length > 0;
            }
            return sel !== undefined;
        });
        if (!allSelected) {
            // 兜底:未选完时提交按钮本应禁用;若仍触发提交,必须唤醒后端 Future,
            // 否则 agent 会一直挂到 5 分钟超时
            Utils.showToast('未选择完所有问题,已作为空回答提交');
            this._timeoutSubmit();
            return;
        }

        const answers = this._buildAnswers();
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

    _timeoutSubmit() {
        // 超时/放弃作答时直接提交,不检查是否全部选择,确保后端 Future 能被唤醒。
        // 已选部分随空回答一起带回,模型至少能看到用户已给出的信息
        this._stopCountdown();
        if (!this.currentRequest) return;
        const req = this.currentRequest;
        const answers = this._buildAnswers();
        const value = Object.keys(answers).length ? JSON.stringify(answers) : '';
        WS.send({ type: 'input_response', session_id: req.session_id, id: req.id, value });
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
            this._timeoutSubmit();
            Utils.showToast('多问题输入已超时');
            return;
        }
        this._countdownTimer = setInterval(() => {
            if (this._countdownCancelled) return;
            this._countdownSeconds--;
            el.textContent = this._fmtTime(this._countdownSeconds);
            if (this._countdownSeconds <= 0) {
                this._timeoutSubmit();
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
