// Phase 7 — capture the microphone and stream it to the backend as PCM16.
//
// gpt-realtime wants 24 kHz, mono, 16-bit little-endian PCM. We capture with an
// AudioWorklet (see mic-worklet.js) at the mic's native rate, then downsample to
// 24 kHz here on the main thread. (We used to use ScriptProcessorNode, but it
// delivered silence in Chromium/Edge — AudioWorklet is the reliable modern API.)

const TARGET_RATE = 24000;

class MicStreamer {
  constructor() {
    this.active = false;
    this.ctx = null;
    this.stream = null;
    this.source = null;
    this.node = null;
    this._frames = 0;
    this._peak = 0;
  }

  get isActive() {
    return this.active;
  }

  async start(onChunk) {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });

    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.ctx = new Ctx(); // native rate (usually 48000)
    await this.ctx.resume();
    const inRate = this.ctx.sampleRate;
    console.log(`[mic] capturing at ${inRate} Hz, downsampling to ${TARGET_RATE} Hz`);

    await this.ctx.audioWorklet.addModule("mic-worklet.js");

    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "capture-processor");

    this.node.port.onmessage = (e) => {
      if (!this.active) return;
      const input = e.data; // Float32 @ inRate

      // Level meter: prove the mic is actually capturing sound (check console).
      for (let i = 0; i < input.length; i++) {
        const a = Math.abs(input[i]);
        if (a > this._peak) this._peak = a;
      }
      if (++this._frames % 12 === 0) {
        console.log(`[mic] level peak ≈ ${this._peak.toFixed(3)} (should rise when you talk)`);
        this._peak = 0;
      }

      const down = this._downsample(input, inRate, TARGET_RATE);
      const int16 = this._floatToInt16(down);
      onChunk(this._b64(int16));
    };

    this.source.connect(this.node);
    this.node.connect(this.ctx.destination); // keeps the graph pulling; output is silent
    this.active = true;
  }

  stop() {
    this.active = false;
    if (this.node) this.node.disconnect();
    if (this.source) this.source.disconnect();
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.ctx) this.ctx.close();
    this.ctx = this.source = this.node = this.stream = null;
  }

  _downsample(input, inRate, outRate) {
    if (inRate === outRate) return input;
    const ratio = inRate / outRate;
    const outLen = Math.floor(input.length / ratio);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const start = Math.floor(i * ratio);
      const end = Math.floor((i + 1) * ratio);
      let sum = 0, count = 0;
      for (let j = start; j < end && j < input.length; j++) {
        sum += input[j];
        count++;
      }
      out[i] = count ? sum / count : input[start] || 0;
    }
    return out;
  }

  _floatToInt16(f32) {
    const int16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return int16;
  }

  _b64(int16) {
    const bytes = new Uint8Array(int16.buffer);
    let bin = "";
    const CHUNK = 0x8000; // avoid arg-count limits on fromCharCode
    for (let i = 0; i < bytes.length; i += CHUNK) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return btoa(bin);
  }
}

window.micStreamer = new MicStreamer();
