// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Mutable, render-loop-friendly state shared between the R3F scene and the
 * React HUD. Never put this in React state — it changes every frame.
 */
export type FlightState = {
  /** Current forward speed in scene units / second. */
  speed: number;
  /** 0..1 normalised speed (for audio / FOV / HUD). */
  speed01: number;
  /** Camera altitude (world y). */
  altitude: number;
  /** True while following a search / click approach path. */
  flying: boolean;
  /** True after landing near an artist until the pilot moves again. */
  perched: boolean;
  /** Set true on first steering input — the HUD hides the hint. */
  interacted: boolean;
  /** Index of the bird the HUD wants to fly to (consumed by the rig). */
  pendingIndex: number;
  /** Bird the camera is currently approaching or flying alongside. */
  followIndex: number;
  /** Live bird positions (xyz per bird), written by the flock each frame. */
  live: Float32Array | null;
  /** Live bird velocities (xyz per bird). */
  liveVel: Float32Array | null;
  /** Ask the rig to leave the perch and glide again. */
  resumeRequested: boolean;
  /** Timestamp of the last landing (ms) — used for the landing ring. */
  landedAt: number;
  /** Timestamp (ms) and origin of the last call; birds nearby come to greet. */
  callAt: number;
  callX: number;
  callY: number;
  callZ: number;
  /** One wing-beat burst requested (Space / tap on empty sky). */
  flapRequested: boolean;
  /** Timestamp (ms) of the last steering input, for the idle gaze. */
  lastInputAt: number;
  /** Live flock centres (xyz per flock) for the idle gaze. */
  flockCenters: Float32Array | null;
};

export function createFlightState(): FlightState {
  return {
    speed: 0,
    speed01: 0,
    altitude: 0,
    flying: false,
    perched: false,
    interacted: false,
    pendingIndex: -1,
    followIndex: -1,
    live: null,
    liveVel: null,
    resumeRequested: false,
    landedAt: 0,
    callAt: 0,
    callX: 0,
    callY: 0,
    callZ: 0,
    flapRequested: false,
    lastInputAt: 0,
    flockCenters: null,
  };
}

export const CRUISE = 6.5;
export const MIN_SPEED = 2.2;
export const MAX_SPEED = 20;
export const BOOST = 30;

/** Deterministic PRNG so flocks look the same on every visit. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export const smoothstep = (e0: number, e1: number, x: number) => {
  const t = clamp((x - e0) / (e1 - e0), 0, 1);
  return t * t * (3 - 2 * t);
};
/** Frame-rate independent exponential approach. */
export const damp = (cur: number, target: number, lambda: number, dt: number) =>
  lerp(cur, target, 1 - Math.exp(-lambda * dt));
export const shortAngle = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));
export const dampAngle = (cur: number, target: number, lambda: number, dt: number) =>
  cur + shortAngle(target - cur) * (1 - Math.exp(-lambda * dt));
