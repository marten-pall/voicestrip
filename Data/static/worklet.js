// AudioWorkletProcessor: captures mono Float32 PCM and resamples to the target
// rate (16 kHz) when the AudioContext runs at a different rate (e.g. 48 kHz).
//
// Resampling is a simple box-filter decimator: every output sample is the
// average of the input samples that fall into its window. For 48 kHz -> 16 kHz
// this is exactly "average every 3 samples"; non-integer ratios (44.1 kHz)
// carry the fractional remainder over to the next window.

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const targetRate = (options.processorOptions && options.processorOptions.targetRate) || 16000;
    this.ratio = sampleRate / targetRate; // `sampleRate` is a worklet global
    this.acc = 0;
    this.count = 0;
    this.pos = 0;
    this.buf = new Float32Array(4096); // ~0.25 s per message at 16 kHz
    this.len = 0;

    this.port.onmessage = (event) => {
      if (event.data === 'flush') {
        this.flush();
        this.port.postMessage({ type: 'flushed' });
      }
    };
  }

  push(value) {
    this.buf[this.len++] = value;
    if (this.len === this.buf.length) this.flush();
  }

  flush() {
    if (this.len === 0) return;
    const out = this.buf.slice(0, this.len);
    this.port.postMessage({ type: 'data', samples: out }, [out.buffer]);
    this.len = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    if (this.ratio === 1) {
      for (let i = 0; i < channel.length; i++) this.push(channel[i]);
      return true;
    }

    for (let i = 0; i < channel.length; i++) {
      this.acc += channel[i];
      this.count++;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        const value = this.acc / this.count;
        // `while` also covers ratio < 1 (device slower than 16 kHz) by repeating samples
        while (this.pos >= this.ratio) {
          this.push(value);
          this.pos -= this.ratio;
        }
        this.acc = 0;
        this.count = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor);
