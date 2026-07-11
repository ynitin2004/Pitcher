// Phase 7 (fix) — AudioWorklet mic capture processor.
//
// Runs on the audio render thread. Each `process()` call hands us up to 128
// samples of mic audio (Float32 @ the context's sample rate). We buffer them
// and post ~2048-sample chunks back to the main thread, which downsamples to
// 24 kHz and streams to the backend. This replaces ScriptProcessorNode, which
// was delivering silence.

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._chunks = [];
    this._count = 0;
    this._target = 2048;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0]; // first input, first channel
    if (channel && channel.length) {
      this._chunks.push(channel.slice(0)); // copy — the buffer is reused
      this._count += channel.length;

      if (this._count >= this._target) {
        const merged = new Float32Array(this._count);
        let offset = 0;
        for (const c of this._chunks) {
          merged.set(c, offset);
          offset += c.length;
        }
        this.port.postMessage(merged);
        this._chunks = [];
        this._count = 0;
      }
    }
    return true; // keep the processor alive
  }
}

registerProcessor("capture-processor", CaptureProcessor);
