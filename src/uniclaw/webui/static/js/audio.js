/* audio.js — 音频播放管理 */

const AudioPlayer = {
    /** 当前会话的音频缓冲区 {streamId: Uint8Array[]} */
    _buffers: {},
    /** 默认采样率 */
    SAMPLE_RATE: 24000,

    /** 播放队列: 待播放的 PCM 数据(FIFO) */
    _queue: [],
    /** 是否正在消费队列 */
    _draining: false,

    /** 初始化 */
    init() {
        WS.on('audio_chunk', (msg) => this._onChunk(msg));
        WS.on('audio_end', (msg) => this._onEnd(msg));
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

    /** 收到音频数据块 — 按 stream_id 隔离 buffer */
    _onChunk(msg) {
        const { stream_id, audio } = msg;
        if (!stream_id || !audio) return;

        if (!this._buffers[stream_id]) {
            this._buffers[stream_id] = [];
        }
        this._buffers[stream_id].push(this._decode(audio));
    },

    /** 音频流结束 — 合并并入队 */
    _onEnd(msg) {
        const { stream_id } = msg;
        if (!stream_id) return;

        const chunks = this._buffers[stream_id];
        if (!chunks || chunks.length === 0) return;

        delete this._buffers[stream_id];

        const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
        const merged = new Uint8Array(totalLength);
        let offset = 0;
        for (const chunk of chunks) {
            merged.set(chunk, offset);
            offset += chunk.length;
        }

        this._queue.push(merged);
        this._drain();
    },

    /** 消费播放队列: 前一个播完才播下一个 */
    async _drain() {
        if (this._draining) return;
        this._draining = true;
        while (this._queue.length > 0) {
            const pcm = this._queue.shift();
            await this._play(pcm);
            await new Promise(r => setTimeout(r, 100));
        }
        this._draining = false;
    },

    /** 播放 PCM16 数据。独立 AudioContext + source.onended 确保播放完成。 */
    _play(pcmBytes) {
        return new Promise((resolve) => {
            try {
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                const numSamples = pcmBytes.length / 2;

                const audioBuffer = ctx.createBuffer(1, numSamples, this.SAMPLE_RATE);
                const channelData = audioBuffer.getChannelData(0);

                const view = new DataView(pcmBytes.buffer);
                for (let i = 0; i < numSamples; i++) {
                    channelData[i] = view.getInt16(i * 2, true) / 32768;
                }

                const source = ctx.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(ctx.destination);
                source.start(0);

                source.onended = () => {
                    ctx.close().catch(() => {});
                    resolve();
                };
            } catch (err) {
                resolve();
            }
        });
    },

    /** 停止播放并清空队列 */
    stop() {
        this._queue = [];
        this._draining = false;
    }
};
