/* audio.js — 音频播放管理 */

const AudioPlayer = {
    /** 当前会话的音频缓冲区 {sessionId: Uint8Array[]} */
    _buffers: {},
    /** 是否正在播放 */
    _playing: false,
    /** 默认采样率 */
    SAMPLE_RATE: 24000,

    /** 初始化 */
    init() {
        WS.on('audio_chunk', (msg) => this._onChunk(msg));
        WS.on('audio_end', (msg) => this._onEnd(msg));
        console.log('[Audio] 音频播放器已初始化');
    },

    /** base64 解码为 Uint8Array */
    _decode(base64) {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes;
    },

    /** 收到音频数据块 */
    _onChunk(msg) {
        const { session_id, audio } = msg;
        if (!session_id || !audio) return;

        if (!this._buffers[session_id]) {
            this._buffers[session_id] = [];
        }
        this._buffers[session_id].push(this._decode(audio));
    },

    /** 音频流结束 */
    async _onEnd(msg) {
        const { session_id } = msg;
        const chunks = this._buffers[session_id];
        if (!chunks || chunks.length === 0) return;

        // 清空缓冲区
        delete this._buffers[session_id];

        // 合并所有数据块
        const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
        const merged = new Uint8Array(totalLength);
        let offset = 0;
        for (const chunk of chunks) {
            merged.set(chunk, offset);
            offset += chunk.length;
        }

        // 播放音频
        await this._playPcm16(merged);
    },

    /** 播放 PCM16 数据 */
    async _playPcm16(pcmBytes) {
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const numSamples = pcmBytes.length / 2; // 16bit = 2 bytes per sample

            // 创建 AudioBuffer
            const audioBuffer = ctx.createBuffer(1, numSamples, this.SAMPLE_RATE);
            const channelData = audioBuffer.getChannelData(0);

            // PCM16 (int16 LE) → float32 [-1, 1]
            const view = new DataView(pcmBytes.buffer);
            for (let i = 0; i < numSamples; i++) {
                const int16 = view.getInt16(i * 2, true); // little-endian
                channelData[i] = int16 / 32768;
            }

            // 播放
            const source = ctx.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(ctx.destination);
            source.start(0);
            this._playing = true;

            source.onended = () => {
                this._playing = false;
                ctx.close();
            };
        } catch (err) {
            console.error('[Audio] 播放失败:', err);
        }
    },

    /** 停止播放 */
    stop() {
        this._playing = false;
    }
};
