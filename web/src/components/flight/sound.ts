// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Small synthesised sounds for the sky: a soft call tone and answering chirps.
 * Created lazily on a user gesture, so autoplay rules are respected.
 */
let ctx: AudioContext | null = null;

/** The shared audio context, created on first use (call from a user gesture). */
export function audioContext(): AudioContext | null {
  return ensure();
}

function ensure(): AudioContext | null {
  if (ctx) return ctx;
  const AC =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return null;
  ctx = new AC();
  return ctx;
}

function tone(
  ac: AudioContext,
  freq: number,
  at: number,
  dur: number,
  gain: number,
  glide = 0,
  type: OscillatorType = "sine",
) {
  const o = ac.createOscillator();
  const g = ac.createGain();
  const f = ac.createBiquadFilter();
  f.type = "lowpass";
  f.frequency.value = Math.min(8000, freq * 3);
  o.type = type;
  o.frequency.setValueAtTime(freq, at);
  if (glide) o.frequency.exponentialRampToValueAtTime(freq * glide, at + dur);
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(gain, at + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  o.connect(f).connect(g).connect(ac.destination);
  o.start(at);
  o.stop(at + dur + 0.05);
}

/** The call: a warm two-partial tone that swells and fades. */
export function playCall() {
  const ac = ensure();
  if (!ac) return;
  void ac.resume();
  const t = ac.currentTime;
  tone(ac, 392, t, 1.1, 0.14);
  tone(ac, 587.3, t + 0.04, 0.9, 0.07);
  tone(ac, 784, t + 0.08, 0.6, 0.03, 1, "triangle");
}

/** Birds answering: a few short bright chirps, staggered. */
export function playChirps(count: number) {
  const ac = ensure();
  if (!ac) return;
  const t = ac.currentTime;
  for (let k = 0; k < Math.min(count, 6); k++) {
    const at = t + 0.35 + k * 0.19 + Math.random() * 0.12;
    const f = 1500 + Math.random() * 900;
    tone(ac, f, at, 0.11, 0.035, 1.35, "sine");
  }
}

/** A single wing-beat: a soft whoosh from filtered noise. */
export function playFlap() {
  const ac = ensure();
  if (!ac) return;
  const t = ac.currentTime;
  const dur = 0.28;
  const buf = ac.createBuffer(1, Math.ceil(ac.sampleRate * dur), ac.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
  const src = ac.createBufferSource();
  src.buffer = buf;
  const f = ac.createBiquadFilter();
  f.type = "bandpass";
  f.frequency.setValueAtTime(400, t);
  f.frequency.exponentialRampToValueAtTime(1400, t + dur * 0.6);
  f.Q.value = 0.8;
  const g = ac.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.09, t + 0.05);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  src.connect(f).connect(g).connect(ac.destination);
  src.start(t);
}

/** Needle tick: a very short, quiet pluck. Pitch carries the year. */
export function playTick(freq = 880, gain = 0.03) {
  const ac = ensure();
  if (!ac) return;
  const t = ac.currentTime;
  tone(ac, freq, t, 0.06, gain, 0.985, "triangle");
}
