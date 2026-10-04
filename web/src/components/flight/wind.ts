// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Tiny procedural wind: looped brown noise → low-pass → gain.
 * Cut-off and gain follow flight speed. Opt-in only (autoplay policy).
 */
export type Wind = {
  start: () => Promise<void>;
  stop: () => void;
  set: (speed01: number) => void;
  running: () => boolean;
};

export function createWind(): Wind {
  let ctx: AudioContext | null = null;
  let gain: GainNode | null = null;
  let filter: BiquadFilterNode | null = null;
  let src: AudioBufferSourceNode | null = null;

  const build = () => {
    const AC =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return;
    ctx = new AC();
    const seconds = 4;
    const buf = ctx.createBuffer(1, ctx.sampleRate * seconds, ctx.sampleRate);
    const data = buf.getChannelData(0);
    let last = 0;
    for (let i = 0; i < data.length; i++) {
      const white = Math.random() * 2 - 1;
      last = (last + 0.02 * white) / 1.02;
      data[i] = last * 3.2;
    }
    src = ctx.createBufferSource();
    src.buffer = buf;
    src.loop = true;
    filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = 320;
    filter.Q.value = 0.4;
    gain = ctx.createGain();
    gain.gain.value = 0;
    src.connect(filter).connect(gain).connect(ctx.destination);
    src.start();
  };

  return {
    async start() {
      if (!ctx) build();
      if (ctx?.state === "suspended") await ctx.resume();
    },
    stop() {
      if (!ctx) return;
      try {
        src?.stop();
      } catch {
        /* already stopped */
      }
      void ctx.close();
      ctx = null;
      gain = null;
      filter = null;
      src = null;
    },
    set(speed01: number) {
      if (!ctx || !gain || !filter) return;
      const t = ctx.currentTime;
      const s = Math.min(1, Math.max(0, speed01));
      gain.gain.setTargetAtTime(0.04 + 0.22 * s * s, t, 0.25);
      filter.frequency.setTargetAtTime(260 + 1500 * s, t, 0.3);
    },
    running: () => ctx !== null,
  };
}
