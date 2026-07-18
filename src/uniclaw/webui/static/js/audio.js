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
    /** 当前正在播放的 AudioContext / source(用于中断) */
    _activeCtx: null,
    _activeSource: null,
    /** 中断代次: stop() 递增,旧代次的音频全部丢弃 */
    _interruptGen: 0,
    /** 中断后等待当前流结束标记(丢弃残余音频块) */
    _interruptedUntilEnd: false,
    /** 中断安全兜底定时器 */
    _interruptTimer: null,

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

        // 中断后丢弃残余音频块,直到当前流结束
        if (this._interruptedUntilEnd) return;

        if (!this._buffers[stream_id]) {
            this._buffers[stream_id] = [];
        }
        this._buffers[stream_id].push(this._decode(audio));
    },

    /** 音频流结束 — 合并并入队 */
    _onEnd(msg) {
        const { stream_id } = msg;
        if (!stream_id) return;

        // 中断后的残余流结束,清除标记并丢弃这批音频
        if (this._interruptedUntilEnd) {
            delete this._buffers[stream_id];
            this._interruptedUntilEnd = false;
            clearTimeout(this._interruptTimer);
            return;
        }

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

        this._queue.push({ pcm: merged, gen: this._interruptGen });
        this._drain();
    },

    /** 消费播放队列: 前一个播完才播下一个 */
    async _drain() {
        if (this._draining) return;
        this._draining = true;
        // 整个队列开始播放
        this._setPlaying(true);
        while (this._queue.length > 0) {
            const item = this._queue.shift();
            // 代次不匹配(被 stop 过),跳过
            if (item.gen !== this._interruptGen) continue;
            await this._play(item.pcm);
        }
        // 整个队列播放完毕
        this._draining = false;
        this._setPlaying(false);
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

                // 保存引用,供 stop() 中断使用
                this._activeCtx = ctx;
                this._activeSource = source;

                source.start(0);

                source.onended = () => {
                    // 清理引用(正常结束或被 stop() 中断都会触发)
                    this._activeCtx = null;
                    this._activeSource = null;
                    ctx.close().catch(() => {});
                    resolve();
                };
            } catch (err) {
                this._activeCtx = null;
                this._activeSource = null;
                resolve();
            }
        });
    },

    /** 停止播放并清空队列(中断当前播放) */
    stop() {
        this._queue = [];
        this._draining = false;
        // 递增代次,丢弃旧队列项
        this._interruptGen++;
        // 标记中断状态,丢弃当前流的残余音频块
        this._interruptedUntilEnd = true;
        // 清空所有缓冲区
        this._buffers = {};
        // 中断正在播放的音频
        if (this._activeSource) {
            try { this._activeSource.stop(); } catch (e) {}
        }
        // 安全兜底:若 audio_end 未到达(比如已经收完),1 秒后自动清除中断标记
        clearTimeout(this._interruptTimer);
        this._interruptTimer = setTimeout(() => { this._interruptedUntilEnd = false; }, 1000);
    },

    /** 当前是否正在播放 */
    isPlaying() {
        return !!this._isPlaying;
    },

    /** 设置播放状态并触发事件 */
    _setPlaying(val) {
        this._isPlaying = val;
        // 直接通知 Input 模块(兼容性更好)
        if (typeof Input !== 'undefined' && Input._onTtsPlayback) {
            Input._onTtsPlayback(val);
        }
    }
};
