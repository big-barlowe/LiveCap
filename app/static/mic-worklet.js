// Posts roughly 100 ms blocks of the first input channel at the context's sample rate.
class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = new Float32Array(Math.round(sampleRate / 10));
    this.filled = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      let at = 0;
      while (at < channel.length) {
        const count = Math.min(channel.length - at, this.block.length - this.filled);
        this.block.set(channel.subarray(at, at + count), this.filled);
        this.filled += count;
        at += count;
        if (this.filled === this.block.length) {
          const full = this.block.slice().buffer;
          this.port.postMessage(full, [full]);
          this.filled = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("mic-capture", MicCapture);
