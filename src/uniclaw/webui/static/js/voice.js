/* voice.js — 语音模式管理 */

const VoiceMode = {
    /** 当前是否开启语音模式 */
    _enabled: false,
    /** TTS 是否可用(config 中 tts_model + audio 已配置) */
    _available: false,

    /** 初始化 */
    init() {
        WS.on('config_changed', (msg) => this._onConfigChanged(msg));
        const btn = document.getElementById('voice-toggle');
        if (btn) btn.onclick = () => this.toggle();
        console.log('[Voice] 语音模式已初始化');
    },

    /** 设置 TTS 可用性(由 session 切换时调用) */
    setAvailable(available) {
        this._available = available;
        this._updateUI();
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
                this._updateUI();
            })
            .catch(() => {});
    },

    /** 更新按钮 UI */
    _updateUI() {
        const btn = document.getElementById('voice-toggle');
        if (!btn) return;
        btn.classList.toggle('active', this._enabled);
        btn.style.display = this._available ? '' : 'none';
    },

    /** 重置状态(会话切换时调用) */
    reset() {
        this._enabled = false;
        this._updateUI();
    },
};
