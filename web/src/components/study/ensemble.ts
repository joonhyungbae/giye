// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The ensemble: turns the conductor's beat plans into sound with Web Audio.
 *
 * Instruments are sampled, not synthesised: VCSL vibraphone, marimba and
 * glockenspiel (CC0) and the Salamander grand piano (CC BY 3.0), trimmed and
 * level-matched under public/samples (see the README there). One instrument
 * per rim arc (entry generation); the pulse is a high piano note, as in most
 * realisations of "In C". A look-ahead scheduler keeps timing off the
 * animation frame.
 */
import { audioContext } from "@/components/flight/sound";
import type { Layout } from "./model";
import { Conductor, type NoteEvent } from "./conductor";

export const PULSE_SEC = 0.31;
const LOOKAHEAD = 0.28;
const TICK_MS = 45;
const SAMPLE_ROOT = "/samples/";

export type SoundedNote = {
  artist: number;
  record: number;
  cell: number;
  /** performance.now() milliseconds at which the note sounds */
  at: number;
  dur: number;
};

type ManifestNote = { midi: number; layer: number; file: string; gain: number };
type Manifest = Record<string, { layers: number; notes: ManifestNote[] }>;
type Sample = ManifestNote & { buffer: AudioBuffer };
type Instrument = { name: string; layers: number; samples: Sample[]; base: number; level: number };

/** instrument index → sample set, base pitch (MIDI) and mix level */
const INSTRUMENTS: Array<{ name: string; base: number; level: number }> = [
  { name: "vibes", base: 60, level: 0.55 },
  { name: "marimba", base: 55, level: 0.6 },
  { name: "glock", base: 72, level: 0.3 },
  { name: "piano", base: 60, level: 0.5 },
];

type Channel = {
  gain: GainNode;
  pan: StereoPannerNode;
  inst: number;
  base: number;
  detune: number;
};

function makeRoom(ctx: AudioContext, seconds = 2.4): AudioBuffer {
  const len = Math.ceil(ctx.sampleRate * seconds);
  const buf = ctx.createBuffer(2, len, ctx.sampleRate);
  for (let c = 0; c < 2; c++) {
    const d = buf.getChannelData(c);
    let lp = 0;
    for (let i = 0; i < len; i++) {
      const t = i / len;
      const env = Math.pow(1 - t, 2.8) * (i < 300 ? i / 300 : 1);
      const white = Math.random() * 2 - 1;
      lp += (white - lp) * 0.2;
      d[i] = lp * env;
    }
  }
  return buf;
}

let manifestPromise: Promise<Manifest> | null = null;
const bufferCache = new Map<string, Promise<AudioBuffer>>();

function loadManifest(): Promise<Manifest> {
  manifestPromise ??= fetch(`${SAMPLE_ROOT}manifest.json`).then((r) => {
    if (!r.ok) throw new Error("manifest");
    return r.json() as Promise<Manifest>;
  });
  return manifestPromise;
}

function loadBuffer(ctx: AudioContext, file: string): Promise<AudioBuffer> {
  let p = bufferCache.get(file);
  if (!p) {
    p = fetch(SAMPLE_ROOT + file)
      .then((r) => {
        if (!r.ok) throw new Error(file);
        return r.arrayBuffer();
      })
      .then((ab) => ctx.decodeAudioData(ab));
    bufferCache.set(file, p);
  }
  return p;
}

export class Ensemble {
  readonly conductor: Conductor;
  private ctx: AudioContext;
  private master: GainNode;
  private bus: GainNode;
  private channels = new Map<number, Channel>();
  private instruments: Array<Instrument | null> = INSTRUMENTS.map(() => null);
  private timer = 0;
  private nextBeatAt = 0;
  private layout: Layout;
  private onNote: (n: SoundedNote) => void;
  private instOf: (artist: number) => number;
  running = false;
  ready = false;

  constructor(layout: Layout, onNote: (n: SoundedNote) => void, ctx = audioContext()) {
    if (!ctx) throw new Error("no audio");
    this.ctx = ctx;
    this.layout = layout;
    this.onNote = onNote;
    this.conductor = new Conductor(layout);
    // one instrument per rim arc (entry generation), cycling round the rim so neighbouring arcs differ
    // (0 vibraphone, 1 marimba, 2 glockenspiel, 3 piano)
    this.instOf = (a) => layout.artists[a]!.group % 4;

    this.master = ctx.createGain();
    this.master.gain.value = 0;
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -20;
    comp.ratio.value = 2.5;
    comp.knee.value = 16;
    comp.attack.value = 0.008;
    comp.release.value = 0.3;
    const dry = ctx.createGain();
    const wet = ctx.createGain();
    dry.gain.value = 0.78;
    wet.gain.value = 0.26;
    const room = ctx.createConvolver();
    room.buffer = makeRoom(ctx);
    this.bus = ctx.createGain();
    this.bus.gain.value = 1;
    this.bus.connect(comp);
    comp.connect(dry).connect(this.master);
    comp.connect(room).connect(wet).connect(this.master);
    this.master.connect(ctx.destination);
  }

  /** Fetch and decode every sample; resolves when the ensemble can play. */
  async load(): Promise<void> {
    const man = await loadManifest();
    await Promise.all(
      INSTRUMENTS.map(async (spec, i) => {
        const entry = man[spec.name];
        if (!entry) return;
        const samples = await Promise.all(
          entry.notes.map(async (n) => ({ ...n, buffer: await loadBuffer(this.ctx, n.file) })),
        );
        this.instruments[i] = {
          name: spec.name,
          layers: entry.layers,
          samples,
          base: spec.base,
          level: spec.level,
        };
      }),
    );
    this.ready = true;
  }

  async start(rot: number): Promise<void> {
    void this.ctx.resume();
    this.running = true;
    await this.load();
    if (!this.running) return; // stopped while loading
    const t = this.ctx.currentTime;
    this.master.gain.cancelScheduledValues(t);
    this.master.gain.setValueAtTime(0.0001, t);
    this.master.gain.exponentialRampToValueAtTime(0.7, t + 0.8);
    this.nextBeatAt = t + 0.15;
    this.update(rot);
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
  }

  stop() {
    this.running = false;
    window.clearInterval(this.timer);
    const t = this.ctx.currentTime;
    this.master.gain.cancelScheduledValues(t);
    this.master.gain.setValueAtTime(Math.max(0.0001, this.master.gain.value), t);
    this.master.gain.exponentialRampToValueAtTime(0.0001, t + 1.2);
    window.setTimeout(() => {
      for (const ch of this.channels.values()) ch.gain.disconnect();
      this.channels.clear();
    }, 1300);
  }

  /** Called every frame: who sits near the needle, and where they sit (for panning). */
  update(rot: number) {
    this.conductor.want(Conductor.pick(this.layout, rot));
    const t = this.ctx.currentTime;
    for (const [i, ch] of this.channels) {
      const a = this.layout.artists[i]!;
      const pan = Math.max(-0.8, Math.min(0.8, Math.cos(a.angle + rot) * 1.3));
      ch.pan.pan.setTargetAtTime(pan, t, 0.15);
    }
  }

  private channel(artist: number): Channel {
    let ch = this.channels.get(artist);
    if (ch) return ch;
    const a = this.layout.artists[artist]!;
    const gain = this.ctx.createGain();
    const pan = this.ctx.createStereoPanner();
    gain.gain.value = 0.0001;
    gain.gain.exponentialRampToValueAtTime(1, this.ctx.currentTime + 0.5);
    gain.connect(pan).connect(this.bus);
    const inst = this.instOf(artist);
    // heavier voices (more records) sit an octave lower; the lightest, an octave up on piano only
    const oct = a.sourced >= 12 ? -12 : a.sourced < 4 && inst === 3 ? 12 : 0;
    const base = INSTRUMENTS[inst]!.base + oct;
    ch = { gain, pan, inst, base, detune: (Math.random() - 0.5) * 6 };
    this.channels.set(artist, ch);
    return ch;
  }

  private release(artist: number) {
    const ch = this.channels.get(artist);
    if (!ch) return;
    const t = this.ctx.currentTime;
    ch.gain.gain.setTargetAtTime(0.0001, t + 0.3, 0.6);
    this.channels.delete(artist);
    window.setTimeout(() => ch.gain.disconnect(), 4000);
  }

  private tick() {
    const ctx = this.ctx;
    while (this.nextBeatAt < ctx.currentTime + LOOKAHEAD) {
      const t = this.nextBeatAt;
      const plan = this.conductor.plan();
      this.pulse(t, plan.beat);
      for (const i of plan.left) this.release(i);
      for (const n of plan.notes) this.note(n, t);
      this.nextBeatAt += PULSE_SEC;
    }
  }

  /** Play one sample: nearest sampled pitch, shifted; layer chosen by velocity. */
  private play(
    inst: number,
    midi: number,
    vel: number,
    t: number,
    out: AudioNode,
    detuneCents = 0,
  ) {
    const ins = this.instruments[inst];
    if (!ins || ins.samples.length === 0) return;
    const layer = Math.min(ins.layers - 1, Math.floor(vel * ins.layers));
    let best: Sample | null = null;
    let bestD = Infinity;
    for (const s of ins.samples) {
      const d = Math.abs(s.midi - midi) + (s.layer === layer ? 0 : 0.5);
      if (d < bestD) {
        bestD = d;
        best = s;
      }
    }
    if (!best) return;
    const src = this.ctx.createBufferSource();
    src.buffer = best.buffer;
    src.playbackRate.value = Math.pow(2, (midi - best.midi) / 12 + detuneCents / 1200);
    const g = this.ctx.createGain();
    // velocity shapes loudness; the sample layer shapes timbre
    g.gain.value = best.gain * ins.level * (0.35 + 0.65 * vel);
    src.connect(g).connect(out);
    src.start(t);
  }

  private pulse(t: number, beat: number) {
    // the pulse: a high piano note, a touch firmer every fourth beat
    this.play(3, 96, beat % 4 === 0 ? 0.42 : 0.28, t, this.bus);
  }

  private note(n: NoteEvent, beatT: number) {
    const ch = this.channel(n.artist);
    const jitter = (Math.random() - 0.5) * 0.012;
    const t = beatT + n.at * PULSE_SEC + jitter;
    const vel = Math.min(1, n.vel * (0.92 + Math.random() * 0.16));
    this.play(ch.inst, ch.base + n.semis, vel, t, ch.gain, ch.detune);
    this.onNote({
      artist: n.artist,
      record: n.record,
      cell: n.cell,
      at: performance.now() + (t - this.ctx.currentTime) * 1000,
      dur: n.dur * PULSE_SEC,
    });
  }
}
