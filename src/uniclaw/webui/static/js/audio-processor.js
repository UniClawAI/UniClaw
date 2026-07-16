/**
 * AudioWorklet Processor
 * 实时捕获麦克风音频并转为 PCM16 格式
 * 静音检测由 VAD 负责,此处仅做格式转换和分块
 */
class PCMProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this._chunkSize = 1024; // 每次发送的采样数
        // 预分配环形 buffer,避免运行时分配内存导致 GC 卡顿
        this._ringBufferSize = 16384; // 1秒@16kHz 的容量
        this._ringBuffer = new Float32Array(this._ringBufferSize);
        this._writePos = 0;
        this._readPos = 0;
    }

    process(inputs) {
        const input = inputs[0][0];
        if (!input) return true;

        // 写入环形 buffer(无内存分配)
        for (let i = 0; i < input.length; i++) {
            this._ringBuffer[this._writePos] = input[i];
            this._writePos = (this._writePos + 1) % this._ringBufferSize;
        }

        // 读取完整 chunk 并发送
        while (this._available() >= this._chunkSize) {
            const pcm16 = new Int16Array(this._chunkSize);
            for (let i = 0; i < this._chunkSize; i++) {
                const s = Math.max(-1, Math.min(1, this._ringBuffer[this._readPos]));
                pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                this._readPos = (this._readPos + 1) % this._ringBufferSize;
            }
            // transferable 避免拷贝
            this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
        }

        return true;
    }

    _available() {
        return (this._writePos - this._readPos + this._ringBufferSize) % this._ringBufferSize;
    }
}

registerProcessor('pcm-processor', PCMProcessor);
