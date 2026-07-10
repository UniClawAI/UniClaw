/* input.js — 输入框组件 */

const Input = {
    attachedFiles: [],
    completionPopup: null,
    _commandsCache: null,
    _subcommandsCache: null,
    _filesCache: null,
    _filesCacheDir: null,
    _debounceTimer: null,
    _history: [],
    _historyIdx: -1,
    _suppressAutoComplete: false,
    _recording: false,
    _mediaRecorder: null,
    _audioChunks: [],

    init() {
        const input = document.getElementById('chat-input');
        const sendBtn = document.getElementById('send-btn');
        const attachBtn = document.getElementById('attach-btn');
        const fileInput = document.getElementById('file-input');

        sendBtn.onclick = () => this.send();
        input.addEventListener('keydown', e => {
            if (this.completionPopup) {
                if (e.key === 'ArrowDown') { e.preventDefault(); this._completionNav(1); return; }
                if (e.key === 'ArrowUp') { e.preventDefault(); this._completionNav(-1); return; }
                if (e.key === 'Tab' || e.key === 'Enter') { e.preventDefault(); this._completionSelect(); return; }
            }
            if (!this.completionPopup && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
                if (this._historyNav(e.key === 'ArrowUp' ? -1 : 1)) e.preventDefault();
                return;
            }
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.send(); }
        });
        input.addEventListener('input', () => {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 150) + 'px';
            this._autoComplete();
        });
        input.addEventListener('blur', () => setTimeout(() => this._hideCompletion(), 150));
        attachBtn.onclick = () => fileInput.click();
        fileInput.onchange = e => this._onFilesSelected(e.target.files);
        const micBtn = document.getElementById('mic-btn');
        if (micBtn) micBtn.onclick = () => this.toggleMic();
    },

    send() {
        const input = document.getElementById('chat-input');
        const text = input.value.trim();
        if (!text && !this.attachedFiles.length) return;
        const sid = SessionPanel.activeSessionId;
        const rootDir = SessionPanel.activeProjectDir;
        if (!sid && !rootDir) { Utils.showToast('请先选择或创建一个项目,或点击"自由聊天"'); return; }

        if (text.startsWith('!')) {
            if (!sid) { Utils.showToast('请先创建会话'); return; }
            const cmd = text.substring(1).trim();
            if (cmd) { WS.send({ type: 'shell', session_id: sid, command: cmd, source: 'chat' }); }
        } else if (text.startsWith('/')) {
            const cmdName = text.substring(1).trim().split(/\s+/)[0].toLowerCase();
            if (cmdName === 'clear' || cmdName === 'cls') { if (rootDir === '__free__') SessionPanel.createFreeChat(); else SessionPanel.createSession(rootDir); input.value = ''; input.style.height = 'auto'; return; }
            if (!sid) { Utils.showToast('请先创建会话'); return; }
            WS.send({ type: 'command', session_id: sid, command: text });
            Chat._appendSystemMessage(text);
        } else {
            const msg = { type: 'chat', content: text, files: this.attachedFiles.map(f => ({ name: f.name, data: f.data, mime: f.mime })) };
            if (sid) msg.session_id = sid;
            else if (rootDir === '__free__') msg.free_chat = true;
            else msg.root_dir = rootDir;
            WS.send(msg);
            // 不在本地追加——服务端会广播 UserEvent 回来,由 _onUser 统一显示
        }
        if (text && (!this._history.length || this._history[this._history.length - 1] !== text)) this._history.push(text);
        this._historyIdx = -1;
        input.value = ''; input.style.height = 'auto';
        this.attachedFiles = []; this._updateFilePreview(); this._hideCompletion();
    },

    /** 切换语音录音 */
    async toggleMic() {
        const micBtn = document.getElementById('mic-btn');
        if (!micBtn) return;

        if (this._recording) {
            // 停止录音
            if (this._mediaRecorder && this._mediaRecorder.state === 'recording') {
                this._mediaRecorder.stop();
            }
            return;
        }

        // 检查浏览器支持
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            Utils.showToast('浏览器不支持录音');
            return;
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this._audioChunks = [];
            const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus' : 'audio/webm';
            this._mediaRecorder = new MediaRecorder(stream, { mimeType });

            this._mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) this._audioChunks.push(e.data);
            };

            this._mediaRecorder.onstop = async () => {
                // 停止所有音轨
                stream.getTracks().forEach(t => t.stop());
                micBtn.classList.remove('recording');
                micBtn.title = '语音输入';
                this._recording = false;

                if (!this._audioChunks.length) return;

                const blob = new Blob(this._audioChunks, { type: mimeType });
                this._audioChunks = [];

                // 转为 base64
                const reader = new FileReader();
                reader.onload = async () => {
                    const base64 = reader.result.split(',')[1];
                    const format = mimeType.includes('webm') ? 'webm' : 'wav';
                    await this._sendAsr(base64, format);
                };
                reader.readAsDataURL(blob);
            };

            this._mediaRecorder.start();
            this._recording = true;
            micBtn.classList.add('recording');
            micBtn.title = '点击停止录音';
        } catch (err) {
            console.error('录音失败:', err);
            Utils.showToast('录音失败: ' + (err.message || '请授权麦克风权限'));
        }
    },

    /** 发送音频到后端 ASR */
    async _sendAsr(audioBase64, format) {
        const micBtn = document.getElementById('mic-btn');
        const input = document.getElementById('chat-input');
        const prevPlaceholder = input.placeholder;

        try {
            if (micBtn) micBtn.disabled = true;
            input.placeholder = '识别中...';
            input.value = '';

            const resp = await fetch('/api/asr', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ audio: audioBase64, format }),
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ detail: '识别失败' }));
                Utils.showToast('ASR 失败: ' + (err.detail || resp.statusText));
                return;
            }

            const data = await resp.json();
            if (data.text) {
                input.value = data.text;
                input.style.height = 'auto';
                input.style.height = Math.min(input.scrollHeight, 150) + 'px';
                input.focus();
            }
        } catch (err) {
            console.error('ASR 请求失败:', err);
            Utils.showToast('ASR 请求失败: ' + err.message);
        } finally {
            if (micBtn) micBtn.disabled = false;
            input.placeholder = prevPlaceholder;
        }
    },

    _onFilesSelected(files) {
        Array.from(files).forEach(file => {
            const reader = new FileReader();
            reader.onload = () => {
                this.attachedFiles.push({ name: file.name, data: reader.result.split(',')[1], mime: file.type, url: reader.result });
                this._updateFilePreview();
            };
            reader.readAsDataURL(file);
        });
    },

    _updateFilePreview() {
        const preview = document.getElementById('file-preview');
        if (!this.attachedFiles.length) { preview.style.display = 'none'; preview.innerHTML = ''; return; }
        preview.style.display = 'flex';
        preview.innerHTML = this.attachedFiles.map((f, i) => {
            if (f.mime.startsWith('image/')) return `<div style="position:relative"><img src="${f.url}" style="width:60px;height:60px;object-fit:cover;border-radius:var(--r-sm);border:1px solid var(--border)"/><button onclick="Input.removeFile(${i})" style="position:absolute;top:-4px;right:-4px;width:18px;height:18px;border-radius:50%;background:var(--neon-pink);color:#fff;border:none;cursor:pointer;font-size:10px;display:flex;align-items:center;justify-content:center">×</button></div>`;
            return `<div style="position:relative;padding:4px 8px;background:var(--bg-3);border-radius:var(--r-sm);font-size:10px;color:var(--text-2)">${Utils.escapeHtml(f.name)}<button onclick="Input.removeFile(${i})" style="position:absolute;top:-4px;right:-4px;width:18px;height:18px;border-radius:50%;background:var(--neon-pink);color:#fff;border:none;cursor:pointer;font-size:10px;display:flex;align-items:center;justify-content:center">×</button></div>`;
        }).join('');
    },

    removeFile(i) { this.attachedFiles.splice(i, 1); this._updateFilePreview(); },

    _historyNav(delta) {
        const input = document.getElementById('chat-input');
        if (!this._history.length) return false;
        if (delta === -1 && this._historyIdx === -1 && input.value.trim() !== '') return false;
        let next;
        if (this._historyIdx === -1) { if (delta === -1) next = this._history.length - 1; else return false; }
        else {
            next = this._historyIdx + delta;
            if (next < 0) next = 0;
            if (next >= this._history.length) { this._historyIdx = -1; input.value = ''; input.style.height = 'auto'; return true; }
        }
        this._historyIdx = next; input.value = this._history[next];
        input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 150) + 'px';
        input.selectionStart = input.selectionEnd = input.value.length;
        return true;
    },

    _autoComplete() {
        clearTimeout(this._debounceTimer);
        if (this._suppressAutoComplete) { this._suppressAutoComplete = false; return; }
        const text = document.getElementById('chat-input').value;
        const hasSlash = text.startsWith('/');
        const lastAt = text.lastIndexOf('@');
        if (!hasSlash && lastAt < 0) { this._hideCompletion(); return; }
        this._debounceTimer = setTimeout(() => {
            if (hasSlash) this._showCommandCompletion(text);
            else if (lastAt >= 0) this._showFileCompletion(text);
        }, 150);
    },

    async _showCommandCompletion(text) {
        try {
            if (!this._commandsCache) {
                const r = await fetch('/api/commands');
                const d = await r.json();
                this._commandsCache = d.commands || [];
                this._subcommandsCache = d.subcommands || {};
            }
            const body = text.substring(1);
            const parts = body.split(/\s+/);
            const cmdName = parts[0] || '';
            const subQuery = parts.length > 1 ? parts.slice(1).join(' ').toLowerCase() : null;
            if (subQuery !== null && cmdName) {
                const subs = this._subcommandsCache[cmdName];
                if (subs?.length) {
                    const matches = subs.filter(s => s.toLowerCase().startsWith(subQuery)).slice(0, 10);
                    if (matches.length) {
                        this._renderCompletion(matches.map(s => ({ label: `/${cmdName} ${s}`, desc: '子命令', fill: () => { document.getElementById('chat-input').value = `/${cmdName} ${s}`; }, onSelect: () => { this._suppressAutoComplete = true; document.getElementById('chat-input').value = `/${cmdName} ${s}`; this._hideCompletion(); document.getElementById('chat-input').focus(); } })));
                        return;
                    }
                }
                this._hideCompletion(); return;
            }
            const matches = this._commandsCache.filter(c => c.name.startsWith(cmdName.toLowerCase())).slice(0, 10);
            if (!matches.length) { this._hideCompletion(); return; }
            this._renderCompletion(matches.map(c => ({ label: `/${c.name}`, desc: c.description, fill: () => { document.getElementById('chat-input').value = `/${c.name}`; }, onSelect: () => {
                const inp = document.getElementById('chat-input');
                const val = `/${c.name} `;
                inp.value = val;
                this._hideCompletion();
                inp.focus();
                // 选中后立即显示子命令
                if (this._subcommandsCache[c.name]?.length) {
                    this._showCommandCompletion(val);
                }
            } })));
        } catch (e) { console.error('获取命令列表失败:', e); }
    },

    async _showFileCompletion(text) {
        const rootDir = SessionPanel.activeProjectDir;
        if (!rootDir) return;
        try {
            if (this._filesCacheDir !== rootDir) {
                const r = await fetch(`/api/files?root_dir=${encodeURIComponent(rootDir)}&recursive=true`);
                this._filesCache = await r.json();
                this._filesCacheDir = rootDir;
            }
            const lastAt = text.lastIndexOf('@');
            const query = text.substring(lastAt + 1).toLowerCase();
            const matches = this._filesCache.filter(f => f.path.toLowerCase().includes(query)).slice(0, 10);
            if (!matches.length) { this._hideCompletion(); return; }
            this._renderCompletion(matches.map(f => ({
                label: f.path, desc: f.is_dir ? '目录' : '文件',
                fill: () => {
                    const inp = document.getElementById('chat-input');
                    const atIdx = inp.value.lastIndexOf('@');
                    inp.value = inp.value.substring(0, atIdx) + `@${f.path}`;
                },
                onSelect: () => {
                    this._suppressAutoComplete = true;
                    const inp = document.getElementById('chat-input');
                    const atIdx = inp.value.lastIndexOf('@');
                    inp.value = inp.value.substring(0, atIdx) + `@${f.path}`;
                    this._hideCompletion(); inp.focus();
                }
            })));
        } catch (e) { console.error('获取文件列表失败:', e); }
    },

    _renderCompletion(items) {
        this._hideCompletion();
        const input = document.getElementById('chat-input');
        const rect = input.getBoundingClientRect();
        const popup = document.createElement('div');
        popup.className = 'completion-popup';
        popup.style.cssText = `position:fixed;bottom:${window.innerHeight - rect.top + 4}px;left:${rect.left}px;min-width:200px;max-width:${rect.width}px;max-height:220px;overflow-y:auto;z-index:1000`;
        popup.innerHTML = items.map((item, i) =>
            `<div class="completion-item${i === 0 ? ' active' : ''}" data-idx="${i}"><span class="cmd-name">${Utils.escapeHtml(item.label)}</span>${item.desc ? `<span class="cmd-desc">${Utils.escapeHtml(item.desc)}</span>` : ''}</div>`
        ).join('');
        document.body.appendChild(popup);
        this.completionPopup = { el: popup, items, selectedIdx: 0 };
        popup.querySelectorAll('.completion-item').forEach(el => {
            el.onclick = () => { items[parseInt(el.dataset.idx)].onSelect(); input.focus(); };
            el.onmouseenter = () => {
                popup.querySelectorAll('.completion-item').forEach(e => e.classList.remove('active'));
                el.classList.add('active');
                this.completionPopup.selectedIdx = parseInt(el.dataset.idx);
            };
        });
    },

    _completionNav(delta) {
        if (!this.completionPopup) return;
        const { el, items, selectedIdx } = this.completionPopup;
        const total = items.length;
        const next = (selectedIdx + delta + total) % total;
        el.querySelectorAll('.completion-item').forEach(e => e.classList.remove('active'));
        el.querySelector(`[data-idx="${next}"]`).classList.add('active');
        this.completionPopup.selectedIdx = next;
        el.querySelector(`[data-idx="${next}"]`).scrollIntoView({ block: 'nearest' });
        // 选中的直接填入输入框
        if (items[next].fill) items[next].fill();
    },

    _completionSelect() {
        if (!this.completionPopup) return;
        this.completionPopup.items[this.completionPopup.selectedIdx].onSelect();
    },

    _hideCompletion() {
        if (this.completionPopup) { this.completionPopup.el.remove(); this.completionPopup = null; }
    },
};
