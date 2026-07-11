// Phase 6 — stream + play the model's speech in the browser.
//
// gpt-realtime sends audio as `response.audio.delta` events: base64-encoded
// PCM16, 24 kHz, mono, little-endian. Browsers can't feed a live stream to an
// <audio> tag easily, so we use the Web Audio API: decode each chunk to a small
// AudioBuffer and schedule it back-to-back on a running clock (`nextTime`) so the
// chunks play gaplessly.
//
// Note: AudioContext starts "suspended" until a user gesture — call ensure()
// from a click/submit handler before the first chunk.

class PCMPlayer {
  constructor(sampleRate = 24000) {
    this.sampleRate = sampleRate;
    this.ctx = null;
    this.nextTime = 0;      // when the next chunk should start
    this.sources = new Set(); // live sources (so Phase 8 can stop them for barge-in)
  }

  ensure() {
    if (!this.ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new Ctx({ sampleRate: this.sampleRate });
    }
    if (this.ctx.state === "suspended") this.ctx.resume();
  }

  play(base64) {
    this.ensure();
    const buffer = this._decode(base64);
    if (!buffer) return;

    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);

    const startAt = Math.max(this.ctx.currentTime, this.nextTime);
    src.start(startAt);
    this.nextTime = startAt + buffer.duration;

    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
  }

  // Seconds of audio still queued to play (used to pace Present Mode).
  remainingSeconds() {
    if (!this.ctx) return 0;
    return Math.max(0, this.nextTime - this.ctx.currentTime);
  }

  // Stop everything immediately and reset the clock (used for Phase 8 barge-in).
  stop() {
    for (const s of this.sources) {
      try { s.stop(); } catch (_) {}
    }
    this.sources.clear();
    if (this.ctx) this.nextTime = this.ctx.currentTime;
  }

  _decode(base64) {
    const bin = atob(base64);
    let len = bin.length;
    if (len < 2) return null;
    if (len % 2) len -= 1; // keep whole 16-bit samples

    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);

    const int16 = new Int16Array(bytes.buffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;

    const buffer = this.ctx.createBuffer(1, float32.length, this.sampleRate);
    buffer.copyToChannel(float32, 0);
    return buffer;
  }
}

window.pcmPlayer = new PCMPlayer(24000);
