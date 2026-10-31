/* Batch mono PCM16-LE in ~50ms chunks, using the AudioContext's actual rate. */
class StudentPCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunkFrames = Math.max(256, Math.round(sampleRate / 20 / 128) * 128);
    this.bytes = new ArrayBuffer(this.chunkFrames * 2);
    this.view = new DataView(this.bytes); this.offset = 0; this.active = true;
    this.port.onmessage = event => {
      if (event.data.type === 'stop') {
        this.flush(); this.active = false; this.port.postMessage({type: 'stopped'});
      }
    };
  }
  flush() {
    if (!this.offset) return;
    const buffer = this.bytes.slice(0, this.offset * 2);
    this.port.postMessage({type: 'pcm', buffer}, [buffer]); this.offset = 0;
  }
  process(inputs, outputs) {
    for (const channel of outputs[0] || []) channel.fill(0);
    if (!this.active) return false;
    const channels = inputs[0] || [];
    if (!channels.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i] || 0;
      value = Math.max(-1, Math.min(1, value / channels.length));
      this.view.setInt16(this.offset * 2, value < 0 ? value * 32768 : value * 32767, true);
      if (++this.offset === this.chunkFrames) this.flush();
    }
    return true;
  }
}
registerProcessor('student-pcm', StudentPCMProcessor);
