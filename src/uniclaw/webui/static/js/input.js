/* input.js — 输入框组件 */

/** 等待发送消息管理 */
const PendingMessages = {
    _messages: [],  // { id, text }
    _counter: 0,

    /** 添加一条等待消息 */
    add(text) {
        const id = ++this._counter;
        const displayText = text.length > 60 ? text.substring(0, 60) + '...' : text;
        this._messages.push({ id, text: displayText });
        this._render();
        return id;
    },

    /** 删除一条等待消息(收到后端确认后调用) */
    removeById(id) {
        this._fadeOutAndRemove(id);
    },

    /** 根据消息内容模糊匹配删除(后端返回的 content 可能不完全一致) */
    removeByContent(content) {
        if (!content) return;
        const normalized = content.trim();
        // 尝试精确匹配
        let idx = this._messages.findIndex(m => m.text === normalized || m.text === normalized.substring(0, 60) + '...');
        if (idx === -1) {
            // 模糊匹配:等待消息是否是 content 的前缀
            idx = this._messages.findIndex(m => normalized.startsWith(m.text) || m.text.startsWith(normalized.substring(0, 60)));
        }
        if (idx !== -1) {
            this._fadeOutAndRemove(this._messages[idx].id);
        }
    },

    /** 清空所有等待消息 */
    clear() {
        this._messages = [];
        this._render();
    },

    /** 淡出动画后移除 */
    _fadeOutAndRemove(id) {
        const el = document.querySelector(`.pending-msg[data-id="${id}"]`);
        if (el) {
            el.style.animation = 'pendingOut 0.2s ease-out forwards';
            setTimeout(() => {
                const idx = this._messages.findIndex(m => m.id === id);
                if (idx !== -1) {
                    this._messages.splice(idx, 1);
                    this._render();
                }
            }, 200);
        } else {
            const idx = this._messages.findIndex(m => m.id === id);
            if (idx !== -1) {
                this._messages.splice(idx, 1);
                this._render();
            }
        }
    },

    /** 渲染等待区域 */
    _render() {
        const container = document.getElementById('pending-messages');
        const textarea = document.getElementById('chat-input');
        if (!container || !textarea) return;

        if (this._messages.length === 0) {
            container.style.display = 'none';
            container.innerHTML = '';
            container.className = 'pending-messages';
            return;
        }

        // 合并所有消息到一行,换行替换为空格
        const combinedText = this._messages
            .map(m => m.text.replace(/\n/g, ' '))
            .join(' ');

        container.style.display = 'block';
        container.className = 'pending-messages active';
        container.innerHTML = `
            <div class="pending-header">等待中...</div>
            <div class="pending-msg">
                <span class="pending-text">${Utils.escapeHtml(combinedText)}</span>
            </div>
        `;
    }
};

// 添加动画样式
if (!document.getElementById('pending-messages-style')) {
    const style = document.createElement('style');
    style.id = 'pending-messages-style';
    style.textContent = `
        @keyframes pendingIn {
            from { opacity: 0; transform: translateY(-6px) scale(0.98); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes pendingOut {
            from { opacity: 1; transform: translateY(0) scale(1); }
            to { opacity: 0; transform: translateY(-6px) scale(0.98); }
        }
        @keyframes pendingPulse {
            0%, 100% { opacity: 0.4; }
            50% { opacity: 1; }
        }

        #pending-messages.active {
            position: relative;
            background: var(--bg-2);
            border: 1px solid var(--border);
            border-bottom: none;
            border-left: none;
            border-radius: 0 6px 0 0;
            padding: 5px 12px 6px 16px;
            margin-bottom: -1px;
            margin-left: 30px;
            z-index: 1;
            animation: pendingIn 0.2s ease-out;
        }

        #pending-messages.active::before {
            content: '';
            position: absolute;
            left: -30px;
            bottom: 0;
            width: 200px;
            height: 100%;
            background: var(--bg-2);
            border-left: 1px solid var(--border);
            border-top: 1px solid var(--border);
            border-radius: 6px 0 0 0;
            transform: skewX(-25deg);
            transform-origin: bottom right;
            z-index: -1;
        }

        #pending-messages.active::after {
            content: '';
            position: absolute;
            right: 12px;
            top: 50%;
            transform: translateY(-50%);
            width: 5px;
            height: 5px;
            border-radius: 50%;
            background: var(--primary);
            animation: pendingPulse 1.5s ease-in-out infinite;
        }

        .pending-header {
            font-size: 10px;
            color: var(--text-3);
            margin-bottom: 1px;
            padding-left: 2px;
            letter-spacing: 0.5px;
        }

        .pending-msg {
            display: flex;
            align-items: center;
            animation: pendingIn 0.2s ease-out;
        }

        .pending-text {
            color: var(--text-1);
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-size: 13px;
            line-height: 1.4;
        }

        #chat-input {
            position: relative;
            z-index: 2;
        }
    `;
    document.head.appendChild(style);
}

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
    /* 免提模式 */
    _handsfree: false,
    _vad: null,
    _handsfreeStream: null,
    _recognition: null,  // Web Speech API
    _manualEdit: false,  // 用户是否手动编辑了输入框
    _speechUpdating: false,  // onresult 正在更新 input.value
    _ttsPlaying: false,  // TTS 是否正在播放
    _ttsCooldown: false,  // TTS 播完后的短暂冷却期
    _speechIsTts: false,  // 当前 VAD 检测到的语音是否来自 TTS
    _speechIsTtsTimer: null,  // _speechIsTts 安全兜底定时器

    init() {
        const input = document.getElementById('chat-input');
        const sendBtn = document.getElementById('send-btn');
        const attachBtn = document.getElementById('attach-btn');
        const fileInput = document.getElementById('file-input');
        const optimizeBtn = document.getElementById('optimize-btn');

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
            // 仅在非程序性更新时标记手动编辑(onresult 会先清除该标记)
            if (!this._speechUpdating) this._manualEdit = true;
        });
        input.addEventListener('blur', () => setTimeout(() => this._hideCompletion(), 150));
        attachBtn.onclick = () => fileInput.click();
        fileInput.onchange = e => this._onFilesSelected(e.target.files);
        const micBtn = document.getElementById('mic-btn');
        if (micBtn) micBtn.onclick = () => this.toggleMic();
        if (optimizeBtn) optimizeBtn.onclick = () => this.optimizePrompt();
        this._setupDragDrop();

        // 注册免提语音 WebSocket 事件
        WS.on('asr_stream_result', (msg) => this._onAsrStreamResult(msg));
    },

    /** TTS 播放状态变更回调(由 AudioPlayer._setPlaying 调用) */
    _onTtsPlayback(isPlaying) {
        this._ttsPlaying = isPlaying;
        // 仅免提模式下阻断语音输入(非免提模式用户是打字,不存在回声问题)
        // 底层已有 _ttsPlaying 标志过滤音频发送和 ASR 结果,无需禁用按钮
        if (!this._handsfree) return;
        const input = document.getElementById('chat-input');
        if (isPlaying) {
            if (input) input.placeholder = '🔊 AI 正在说话...';
        } else {
            this._clearSpeechBuffer();
        }
    },

    /** 清空语音识别缓存(TTS 播完后调用,避免把之前的语音残留发出去) */
    _clearSpeechBuffer() {
        clearTimeout(this._speechSendTimer);
        this._speechSendTimer = null;
        this._speechAllText = '';
        this._manualEdit = false;
        this._isSpeaking = false;
        // 注意:不在这里重置 _speechIsTts,由 onSpeechEnd/onVADMisfire 负责重置
        // 安全兜底:1 秒后强制重置(防止 onSpeechEnd 未触发的情况)
        clearTimeout(this._speechIsTtsTimer);
        this._speechIsTtsTimer = setTimeout(() => { this._speechIsTts = false; }, 1000);
        const input = document.getElementById('chat-input');
        if (input) {
            input.value = '';
            input.style.height = 'auto';
            input.placeholder = this._handsfree ? '🎤 免提模式 - 请说话' : '输入消息... (!Shell /命令 @文件)';
        }
        // 通知后端清空 ASR 缓冲区
        const sid = SessionPanel.activeSessionId;
        if (sid) WS.send({ type: 'asr_stream_reset', session_id: sid });
        // 短暂冷却,等后端处理完 reset 再恢复 ASR
        this._ttsCooldown = true;
        setTimeout(() => { this._ttsCooldown = false; }, 300);
    },

    /** 拖拽文件到主聊天区添加附件 */
    _setupDragDrop() {
        const mainArea = document.getElementById('main-area');
        if (!mainArea) return;
        let dragCounter = 0;
        const overlay = document.createElement('div');
        overlay.className = 'drop-overlay';
        overlay.innerHTML = `<div class="drop-overlay-inner">${Icons.attach}<span>松开以添加附件</span></div>`;
        mainArea.appendChild(overlay);

        mainArea.addEventListener('dragenter', e => {
            e.preventDefault();
            dragCounter++;
            if (e.dataTransfer.types.includes('Files')) overlay.classList.add('visible');
        });
        mainArea.addEventListener('dragleave', e => {
            e.preventDefault();
            dragCounter--;
            if (dragCounter <= 0) { dragCounter = 0; overlay.classList.remove('visible'); }
        });
        mainArea.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
        mainArea.addEventListener('drop', e => {
            e.preventDefault();
            dragCounter = 0;
            overlay.classList.remove('visible');
            if (e.dataTransfer.files.length) this._onFilesSelected(e.dataTransfer.files);
        });
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
            if (!sid) { Utils.showToast('会话创建中,请稍候'); return; }
            const msg = { type: 'chat', session_id: sid, content: text, files: this.attachedFiles.map(f => ({ name: f.name, data: f.data, mime: f.mime })) };
            WS.send(msg);
            // 添加到等待区——收到后端 UserEvent 确认后会移除
            PendingMessages.add(text);
        }
        if (text && (!this._history.length || this._history[this._history.length - 1] !== text)) this._history.push(text);
        this._historyIdx = -1;
        input.value = ''; input.style.height = 'auto';
        this.attachedFiles = []; this._updateFilePreview(); this._hideCompletion();
    },

    /** 切换语音录音/免提模式 */
    async toggleMic() {
        const micBtn = document.getElementById('mic-btn');
        if (!micBtn) return;

        // 如果正在传统录音,停止
        if (this._recording) {
            if (this._mediaRecorder && this._mediaRecorder.state === 'recording') {
                this._mediaRecorder.stop();
            }
            return;
        }

        // 如果正在免提模式,停止
        if (this._handsfree) {
            this._stopHandsfree();
            return;
        }

        // 检查浏览器支持
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (SpeechRecognition) {
                await this._startSpeechRecognition();
                return;
            }
            Utils.showToast('浏览器不支持录音,请使用 Chrome/Edge/Safari 或升级浏览器');
            return;
        }

        if (typeof vad === 'undefined' || !vad.MicVAD) {
            Utils.showToast('VAD 库未加载,回退到传统录音模式');
            await this._startTraditionalRecording();
            return;
        }

        // 进入免提模式
        await this._startHandsfree();
    },

    /** 使用 Web Speech API (浏览器内置语音识别) */
    async _startSpeechRecognition() {
        const micBtn = document.getElementById('mic-btn');
        const input = document.getElementById('chat-input');
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

        if (!SpeechRecognition) {
            Utils.showToast('浏览器不支持语音识别');
            return;
        }

        this._recognition = new SpeechRecognition();
        this._recognition.lang = 'zh-CN';
        this._recognition.continuous = true;
        this._recognition.interimResults = true;
        this._speechAllText = '';
        this._manualEdit = false;

        this._recognition.onstart = () => {
            this._handsfree = true;
            micBtn.classList.add('listening');
            micBtn.title = '语音识别中 - 点击停止';
            input.placeholder = '🎤 正在聆听...';
        };

        this._recognition.onresult = (event) => {
            let interim = '';
            let newFinal = '';

            // 只处理新增的结果,避免重复累加已 final 的文字
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const t = event.results[i][0].transcript;
                if (event.results[i].isFinal) {
                    newFinal += t;
                } else {
                    interim += t;
                }
            }

            if (newFinal) {
                this._speechAllText += newFinal;
            }

            // 如果用户手动编辑过输入框,保留其内容并追加新识别结果
            this._speechUpdating = true;
            if (this._manualEdit) {
                if (newFinal) input.value += newFinal;
            } else {
                input.value = this._speechAllText + interim;
            }
            this._speechUpdating = false;
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 150) + 'px';
            this._startSpeechSendTimer(2000);
        };

        this._recognition.onerror = (event) => {
            if (event.error === 'not-allowed') {
                Utils.showToast('请授权麦克风权限');
                this._stopSpeechRecognition();
            } else if (event.error === 'network') {
                Utils.showToast('语音识别网络错误');
                this._stopSpeechRecognition();
            } else if (event.error !== 'no-speech' && event.error !== 'aborted') {
                console.warn('[Speech] 识别错误:', event.error);
                Utils.showToast('语音识别出错: ' + event.error);
                this._stopSpeechRecognition();
            }
        };

        this._recognition.onend = () => {
            if (!this._handsfree) return;
            try { this._recognition.start(); } catch (e) { }
        };

        try {
            this._recognition.start();
            Utils.showToast('语音识别已启动');
        } catch (err) {
            Utils.showToast('启动失败');
        }
    },

    /** 发送输入框中待发送的语音文本(供定时器和停止时共用) */
    _flushPendingSpeechInput() {
        clearTimeout(this._speechSendTimer);
        this._speechSendTimer = null;
        const input = document.getElementById('chat-input');
        const sid = SessionPanel.activeSessionId;
        const text = input.value.trim();
        if (text && sid) {
            WS.send({
                type: 'asr_stream',
                session_id: sid,
                audio: text,
                format: 'text',
            });
        }
        input.value = '';
        this._speechAllText = '';
        this._manualEdit = false;

        // 清空后端 ASR 缓冲区
        if (sid) WS.send({ type: 'asr_stream_reset', session_id: sid });

        // 重置免提模式 UI
        const micBtn = document.getElementById('mic-btn');
        if (this._handsfree && micBtn) {
            micBtn.classList.remove('processing');
            micBtn.title = '免提模式 - 请说话';
            input.placeholder = '🎤 免提模式 - 请说话';
        }
    },

    /** 启动语音发送定时器 */
    _startSpeechSendTimer(delay) {
        clearTimeout(this._speechSendTimer);
        this._speechSendTimer = setTimeout(() => this._flushPendingSpeechInput(), delay);
    },

    /** 停止 Web Speech API */
    _stopSpeechRecognition() {
        const micBtn = document.getElementById('mic-btn');
        const input = document.getElementById('chat-input');

        if (this._recognition) {
            this._handsfree = false;
            this._recognition.stop();
            this._recognition = null;
        }

        // 停止前先发送待发送的识别文本,避免丢失
        this._flushPendingSpeechInput();
        micBtn.classList.remove('listening', 'processing');
        micBtn.title = '语音输入';
        input.placeholder = '输入消息... (!Shell /命令 @文件)';
    },

    /** 传统录音模式(按住说话) */
    async _startTraditionalRecording() {
        const micBtn = document.getElementById('mic-btn');
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: { noiseSuppression: true, echoCancellation: true, autoGainControl: true },
            });
            this._audioChunks = [];
            const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus' : 'audio/webm';
            this._mediaRecorder = new MediaRecorder(stream, { mimeType });

            this._mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) this._audioChunks.push(e.data);
            };

            this._mediaRecorder.onstop = async () => {
                stream.getTracks().forEach(t => t.stop());
                micBtn.classList.remove('recording');
                micBtn.title = '语音输入';
                this._recording = false;

                if (!this._audioChunks.length) return;

                const blob = new Blob(this._audioChunks, { type: mimeType });
                this._audioChunks = [];

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

    /** 启动免提模式 (AudioWorklet + VAD) */
    async _startHandsfree() {
        const micBtn = document.getElementById('mic-btn');
        const input = document.getElementById('chat-input');

        try {
            Utils.showToast('正在启动免提模式...');
            micBtn.disabled = true;

            // 获取麦克风权限(关闭回声消除和降噪,避免 DSP 处理导致音频卡顿)
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: { noiseSuppression: false, echoCancellation: false, autoGainControl: true },
            });
            this._handsfreeStream = stream;

            // 创建 AudioContext
            const audioContext = new AudioContext({ sampleRate: 16000 });
            this._audioContext = audioContext;

            // 加载 AudioWorklet
            await audioContext.audioWorklet.addModule('/static/js/audio-processor.js');

            // 创建音频节点
            const source = audioContext.createMediaStreamSource(stream);
            const workletNode = new AudioWorkletNode(audioContext, 'pcm-processor');
            this._workletNode = workletNode;

            this._isSpeaking = false;

            // 环形缓冲区:持续保存最近 ~600ms 的音频,VAD 触发时先发缓冲区(避免丢首字)
            this._preRollBuffer = [];
            this._preRollMaxChunks = 20; // 约 640ms (每块 32ms @16kHz/512samples)

            // 仅在说话时发送音频,VAD 只控制何时把识别结果发给 Agent
            // TTS 播放期间/冷却期内不发送,防止回声循环(AEC 关闭时靠软件隔离)
            workletNode.port.onmessage = (e) => {
                const pcmData = new Int16Array(e.data);
                const speaking = this._isSpeaking && !this._ttsPlaying && !this._ttsCooldown;

                if (speaking) {
                    // 缓冲区有内容说明刚从不说话切换过来,先发缓冲区(避免丢首字)
                    if (this._preRollBuffer.length > 0) {
                        for (const chunk of this._preRollBuffer) this._sendAudioChunk(chunk);
                        this._preRollBuffer = [];
                    }
                    this._sendAudioChunk(pcmData);
                } else {
                    // 未说话:存入环形缓冲区
                    this._preRollBuffer.push(pcmData);
                    if (this._preRollBuffer.length > this._preRollMaxChunks) {
                        this._preRollBuffer.shift();
                    }
                }
            };

            // 连接音频节点(不连接到扬声器,避免回声)
            source.connect(workletNode);

            // VAD 可用性已在 toggleMic 中检查,此处直接使用

            // 使用 AudioNodeVAD 复用已有 AudioContext,避免两个 AudioContext 争抢麦克风
            this._vad = await vad.AudioNodeVAD.new(audioContext, {
                baseAssetPath: '/static/js/',
                onnxWASMBasePath: 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.14.0/dist/',
                positiveSpeechThreshold: 0.5,   // 语音概率超过此值判定为开始说话 (0~1, 越高越不灵敏)
                negativeSpeechThreshold: 0.35,  // 语音概率低于此值判定为停止说话 (越高越容易结束)
                redemptionFrames: 10,            // 连续 N 帧低于阈值后才确认停止 (防止单词间断误切)
                preSpeechPadFrames: 7,          // 说话开始前回填 N 帧音频 (避免截掉开头)
                minSpeechFrames: 3,             // 至少连续 N 帧才视为有效语音 (过滤短噪声)
                onSpeechStart: () => {
                    clearTimeout(this._speechSendTimer);
                    // TTS 播放期间标记为 TTS 语音,不当作用户说话
                    if (this._ttsPlaying) { this._speechIsTts = true; return; }
                    this._isSpeaking = true;
                    micBtn.classList.remove('processing');
                    micBtn.classList.add('listening');
                    micBtn.title = '正在聆听...';
                    input.placeholder = '🎤 正在聆听...';
                },
                onSpeechEnd: () => {
                    this._isSpeaking = false;
                    // TTS 触发的语音结束或冷却期内,直接回到就绪状态
                    if (this._speechIsTts || this._ttsCooldown) {
                        this._speechIsTts = false;
                        micBtn.classList.remove('listening', 'processing');
                        micBtn.title = '免提模式 - 请说话';
                        input.placeholder = '🎤 免提模式 - 请说话';
                        return;
                    }
                    micBtn.classList.remove('listening');
                    micBtn.classList.add('processing');
                    micBtn.title = '发送中...';
                    input.placeholder = '发送中...';
                    this._startSpeechSendTimer(1000);
                },
                onVADMisfire: () => {
                    this._isSpeaking = false;
                    if (this._ttsPlaying || this._speechIsTts) { this._speechIsTts = false; return; }
                    micBtn.classList.remove('listening', 'processing');
                    micBtn.title = '免提模式 - 请说话';
                    input.placeholder = '🎤 免提模式 - 请说话';
                    this._startSpeechSendTimer(1000);
                },
            });
            this._vad.receive(source);
            this._vad.start();

            this._handsfree = true;
            micBtn.disabled = false;
            micBtn.classList.add('listening');
            micBtn.title = '免提模式 - 点击退出';
            input.placeholder = '🎤 免提模式 - 请说话';
            Utils.showToast('免提模式已启动,请说话');

        } catch (err) {
            console.error('启动免提模式失败:', err, err.message, err.stack);
            micBtn.disabled = false;
            micBtn.classList.remove('listening', 'processing');
            Utils.showToast('启动免提模式失败: ' + (err.message || '请授权麦克风权限'));
        }
    },

    /** 发送音频块到后端 */
    _sendAudioChunk(pcmData) {
        // 将 PCM16 转为 base64(批量处理,避免逐字节拼接阻塞主线程)
        const bytes = new Uint8Array(pcmData.buffer);
        const chunks = [];
        const CHUNK = 8192;
        for (let i = 0; i < bytes.length; i += CHUNK) {
            chunks.push(String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK)));
        }
        const base64 = btoa(chunks.join(''));

        const sid = SessionPanel.activeSessionId;

        // 通过 WebSocket 发送
        WS.send({
            type: 'asr_stream_audio',
            session_id: sid,
            audio: base64,
            format: 'pcm16',
            sample_rate: 16000,
        });
    },

    /** 停止免提模式 */
    _stopHandsfree() {
        const micBtn = document.getElementById('mic-btn');
        const input = document.getElementById('chat-input');

        // 提前标记为非免提,防止异步触发时误重启
        this._handsfree = false;
        this._isSpeaking = false;

        // 停止前先发送待发送的识别文本,避免丢失
        this._flushPendingSpeechInput();

        // 停止 VAD
        if (this._vad) {
            this._vad.destroy();
            this._vad = null;
        }

        // 停止 AudioWorklet
        if (this._workletNode) {
            this._workletNode.disconnect();
            this._workletNode = null;
        }

        // 停止 AudioContext
        if (this._audioContext) {
            this._audioContext.close();
            this._audioContext = null;
        }

        // 停止麦克风
        if (this._handsfreeStream) {
            this._handsfreeStream.getTracks().forEach(t => t.stop());
            this._handsfreeStream = null;
        }

        micBtn.classList.remove('listening', 'processing');
        micBtn.title = '语音输入';
        input.placeholder = '输入消息... (!Shell /命令 @文件)';
        Utils.showToast('免提模式已关闭');
    },


    /** 处理 ASR 流结果事件 */
    _onAsrStreamResult(msg) {
        if (msg.session_id !== SessionPanel.activeSessionId) return;
        // TTS 播放期间/冷却期忽略 ASR 结果(防止 TTS 声音被识别)
        if (this._ttsPlaying || this._ttsCooldown) return;
        const input = document.getElementById('chat-input');
        const micBtn = document.getElementById('mic-btn');

        if (msg.status === 'recognition') {
            // 识别结果(实时显示)——仅在当前仍在等待识别时更新,避免覆盖已发送的内容
            if (this._isSpeaking || micBtn.classList.contains('processing') || micBtn.classList.contains('listening')) {
                this._speechUpdating = true;
                input.value = msg.text;
                this._speechUpdating = false;
                input.style.height = 'auto';
                input.style.height = Math.min(input.scrollHeight, 150) + 'px';
            }
        } else if (msg.status === 'sent') {
            // 发送成功
            this._speechUpdating = true;
            input.value = '';
            this._speechUpdating = false;
            input.placeholder = `✓ ${msg.text}`;
            setTimeout(() => {
                if (this._handsfree) {
                    micBtn.classList.remove('processing');
                    input.placeholder = '🎤 免提模式 - 请说话';
                }
            }, 2000);
        } else if (msg.status === 'ignored') {
            // 无意义内容,静默忽略
            micBtn.classList.remove('processing');
            if (this._handsfree) {
                input.placeholder = '🎤 免提模式 - 请说话';
            }
        } else if (msg.status === 'error') {
            console.error('ASR 流处理失败:', msg.message);
            Utils.showToast('语音处理失败: ' + msg.message);
            micBtn.classList.remove('processing');
            if (this._handsfree) {
                input.placeholder = '🎤 免提模式 - 请说话';
            }
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
        const MAX_FILE_SIZE = 500 * 1024 * 1024; // 500MB
        Array.from(files).forEach(file => {
            if (file.size > MAX_FILE_SIZE) {
                Utils.showToast(`文件 ${file.name} 超过500MB限制,已跳过`);
                return;
            }
            const reader = new FileReader();
            reader.onload = () => {
                this.attachedFiles.push({ name: file.name, data: reader.result.split(',')[1], mime: file.type || 'application/octet-stream', url: reader.result });
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
            const removeBtn = `<button onclick="Input.removeFile(${i})" style="position:absolute;top:-4px;right:-4px;width:18px;height:18px;border-radius:50%;background:var(--neon-pink);color:#fff;border:none;cursor:pointer;font-size:10px;display:flex;align-items:center;justify-content:center">×</button>`;
            if (f.mime.startsWith('image/')) return `<div style="position:relative"><img src="${f.url}" style="width:60px;height:60px;object-fit:cover;border-radius:var(--r-sm);border:1px solid var(--border)"/>${removeBtn}</div>`;
            if (f.mime.startsWith('video/')) return `<div style="position:relative"><video src="${f.url}" style="width:60px;height:60px;object-fit:cover;border-radius:var(--r-sm);border:1px solid var(--border)"/>${removeBtn}</div>`;
            if (f.mime.startsWith('audio/')) return `<div style="position:relative;display:flex;align-items:center;gap:4px;padding:4px 8px;background:var(--bg-3);border-radius:var(--r-sm)">${Icons.file || '🎵'}<span style="font-size:10px;color:var(--text-2);max-width:50px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${Utils.escapeHtml(f.name)}</span>${removeBtn}</div>`;
            return `<div style="position:relative;display:flex;align-items:center;gap:4px;padding:4px 8px;background:var(--bg-3);border-radius:var(--r-sm)">${Icons.file || '📄'}<span style="font-size:10px;color:var(--text-2);max-width:50px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${Utils.escapeHtml(f.name)}</span>${removeBtn}</div>`;
        }).join('');
    },

    removeFile(i) { this.attachedFiles.splice(i, 1); this._updateFilePreview(); },

    /** 优化输入框中的提示词 */
    async optimizePrompt() {
        const input = document.getElementById('chat-input');
        const optimizeBtn = document.getElementById('optimize-btn');
        const text = input.value.trim();

        if (!text) {
            Utils.showToast('请先输入需要优化的内容');
            return;
        }

        const sid = SessionPanel.activeSessionId;
        if (!sid) {
            Utils.showToast('请先选择或创建一个会话');
            return;
        }

        let optimizing = false;
        if (optimizing) return;
        optimizing = true;
        optimizeBtn.textContent = '⏳';
        optimizeBtn.disabled = true;
        optimizeBtn.style.opacity = '1';

        try {
            const resp = await fetch('/api/optimize-user-prompt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt: text,
                    session_id: sid,
                }),
            });

            if (resp.ok) {
                const data = await resp.json();
                if (data.optimized) {
                    input.value = data.optimized;
                    input.style.height = 'auto';
                    input.style.height = Math.min(input.scrollHeight, 150) + 'px';
                    input.focus();
                    Utils.showToast('提示词已优化');
                }
            } else {
                const err = await resp.json().catch(() => ({ detail: '优化失败' }));
                Utils.showToast('优化失败: ' + (err.detail || resp.statusText));
            }
        } catch (e) {
            console.error('优化失败:', e);
            Utils.showToast('优化失败: ' + e.message);
        } finally {
            optimizing = false;
            optimizeBtn.textContent = '✨';
            optimizeBtn.disabled = false;
            optimizeBtn.style.opacity = '0.5';
        }
    },

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
                const rootDir = SessionPanel.activeProjectDir || '';
                const r = await fetch(`/api/commands?root_dir=${encodeURIComponent(rootDir)}`);
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
            this._renderCompletion(matches.map(c => ({
                label: `/${c.name}`, desc: c.is_skill ? `技能: ${c.description}` : c.description, fill: () => { document.getElementById('chat-input').value = `/${c.name}`; }, onSelect: () => {
                    const inp = document.getElementById('chat-input');
                    const val = `/${c.name} `;
                    inp.value = val;
                    this._hideCompletion();
                    inp.focus();
                    // 选中后立即显示子命令
                    if (this._subcommandsCache[c.name]?.length) {
                        this._showCommandCompletion(val);
                    }
                }
            })));
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
