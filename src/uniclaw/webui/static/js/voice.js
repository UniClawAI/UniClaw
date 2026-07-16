/* voice.js — 语音模式管理 */

const VoiceMode = {
    /** 当前是否开启语音模式 */
    _enabled: false,
    /** TTS 是否可用(config 中 tts_model + audio 已配置) */
    _available: false,
    /** ASR 是否可用(config 中 asr_model 已配置) */
    _asrAvailable: false,

    /** 初始化 */
    init() {
        WS.on('config_changed', (msg) => this._onConfigChanged(msg));
        const statusBtn = document.getElementById('status-voice');
        if (statusBtn) statusBtn.onclick = () => this.toggle();
    },

    /** 设置 TTS 可用性(由 session 切换时调用) */
    setAvailable(available) {
        this._available = available;
        this._updateUI();
    },

    /** 设置 ASR 可用性(由 session 切换时调用) */
    setAsrAvailable(available) {
        this._asrAvailable = available;
        this._updateMicBtn();
    },

    /** 设置语音模式状态(由 session 切换时调用) */
    setEnabled(enabled) {
        this._enabled = enabled;
        this._updateUI();
    },

    /** 切换语音模式 */
    toggle() {
        if (!this._available) {
            Utils.showToast('TTS 未配置(tts_model 或 audio 为空)');
            return;
        }
        const sid = SessionPanel.activeSessionId;
        if (!sid) {
            Utils.showToast('请先选择会话');
            return;
        }
        WS.send({ type: 'voice_mode', session_id: sid, enabled: !this._enabled });
    },

    /** config 变更时重新获取语音状态 */
    _onConfigChanged(msg) {
        if (!msg || !msg.session_id) return;
        const sid = SessionPanel.activeSessionId;
        if (msg.session_id !== sid) return;
        fetch(`/api/config?session_id=${msg.session_id}`)
            .then(r => r.json())
            .then(d => {
                this._available = d.voice_available || false;
                this._enabled = d.voice_mode || false;
                this._asrAvailable = d.asr_available || false;
                this._updateUI();
                this._updateMicBtn();
            })
            .catch(() => {});
    },

    /** 更新状态栏 UI */
    _updateUI() {
        const status = document.getElementById('status-voice');
        if (status) {
            status.style.display = this._available ? '' : 'none';
            status.title = this._enabled ? '语音模式: 开启 (/voice)' : '语音模式: 关闭 (/voice)';
            const iconOn = document.getElementById('voice-icon-on');
            const iconOff = document.getElementById('voice-icon-off');
            if (iconOn) iconOn.style.display = this._enabled ? '' : 'none';
            if (iconOff) iconOff.style.display = this._enabled ? 'none' : '';
        }
    },

    /** 更新麦克风按钮显隐(ASR 可用性) */
    _updateMicBtn() {
        const micBtn = document.getElementById('mic-btn');
        if (micBtn) {
            // 始终显示麦克风按钮(支持 Web Speech API 作为备选方案)
            micBtn.style.display = '';
        }
    },

    /** 重置状态(会话切换时调用) */
    reset() {
        this._enabled = false;
        this._asrAvailable = false;
        this._updateUI();
        this._updateMicBtn();
    },
};
