// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The conductor: a process-music engine shaped after Terry Riley's "In C"
 * (1964) but reading its material from the archive instead of a score.
 *
 * Structure borrowed (process, not notes):
 *   - a steady pulse; every voice plays short cells in the same order;
 *   - each voice repeats a cell as often as it likes, then moves on;
 *   - no voice may run more than two cells ahead of the slowest;
 *   - newcomers enter on a cell boundary; the piece ends when all arrive.
 *
 * What is ours:
 *   - the cells are the year rings of the ledger. Each year's motif is
 *     derived from that year's record count and how many artists it holds,
 *     so it is fixed for a given dataset and changes when the ledger grows;
 *   - a voice is an artist. It repeats a year's cell once per record it has
 *     in that year, and rests through years it has no record in;
 *   - which artists play is decided by the disc: those near the needle.
 *
 * Pure logic, no audio: the ensemble layer turns beats into sound.
 */
import { mulberry32 } from "@/components/flight/flightState";
import type { ArtistNode, Layout, RecordNode } from "./model";

export type CellNote = { at: number; dur: number; deg: number; vel: number };
export type Cell = {
  ring: number;
  year: number | null;
  label: string;
  beats: number;
  notes: CellNote[];
  /** semitone offsets above the voice's base pitch, low to high */
  scale: number[];
  count: number;
};

/** Harmonic drift across the years: C → lydian colour → E minor → G → back to C. */
const MODES: number[][] = [
  [0, 2, 4, 7, 9, 12, 14, 16],
  [0, 2, 4, 6, 7, 9, 11, 12],
  [4, 7, 9, 11, 14, 16, 19, 21],
  [7, 9, 11, 14, 16, 19, 21, 23],
  [0, 2, 4, 7, 9, 12, 14, 16],
];

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function buildCells(layout: Layout): Cell[] {
  const { rings, records } = layout;
  const artistsInRing = rings.map(() => new Set<number>());
  for (const r of records) artistsInRing[r.ring]?.add(r.artist);
  const n = rings.length;
  return rings.map((rg, k) => {
    const count = rg.count;
    const heads = artistsInRing[k]!.size;
    const rnd = mulberry32((rg.year ?? 2016) * 131 + count * 7 + heads * 3 + 11);
    const modeT = (k / Math.max(1, n - 1)) * (MODES.length - 1);
    const scale = MODES[Math.round(modeT)]!;
    // a busier year gets a longer cell: 1 record → 4 beats, 180 → 8
    const beats = clamp(3 + Math.floor(Math.log2(1 + count)), 3, 8);
    const notes: CellNote[] = [];
    let deg = Math.floor(rnd() * 4);
    let at = 0;
    while (at < beats) {
      const r = rnd();
      if (r < 0.16 && at > 0) {
        at += 1; // a rest
        continue;
      }
      const dur = rnd() < 0.22 && at + 2 <= beats ? 2 : 1;
      const vel = at === 0 ? 1 : at % 2 === 0 ? 0.78 : 0.58;
      notes.push({ at, dur, deg, vel });
      const s = rnd();
      const step = s < 0.14 ? 2 : s < 0.56 ? 1 : s < 0.82 ? -1 : 0;
      deg = clamp(deg + step, 0, scale.length - 1);
      at += dur;
    }
    if (notes.length === 0) notes.push({ at: 0, dur: 1, deg, vel: 1 });
    return { ring: k, year: rg.year, label: rg.label, beats, notes, scale, count };
  });
}

export type Voice = {
  artist: number;
  cell: number;
  /** repetitions left in the current cell (a silent cell counts as one) */
  repsLeft: number;
  /** record indices of this artist per ring, oldest first */
  byRing: number[][];
  cursor: number;
  nextBeat: number;
  /** beat at which the voice was asked to leave; it finishes its repetition first */
  leaving: boolean;
  atEnd: boolean;
  joinedBeat: number;
  /** beat at which the voice entered its current cell */
  cellStart: number;
};

export type NoteEvent = {
  artist: number;
  /** the record this repetition stands for, or -1 for a rest */
  record: number;
  cell: number;
  /** beat offset from the scheduled beat */
  at: number;
  dur: number;
  /** semitones above the voice base */
  semis: number;
  vel: number;
};

export type BeatPlan = {
  beat: number;
  notes: NoteEvent[];
  joined: number[];
  left: number[];
  /** the ensemble's slowest cell after this beat */
  minCell: number;
  restarted: boolean;
};

export const MAX_VOICES = 10;
const MAX_LEAD = 2;
const BREATH_BEATS = 8;
const REST_BEATS = 2;
/** minimum beats a voice spends in a cell: thin years pass, thick years hold */
const dwellFor = (c: Cell) => 8 + Math.round(2 * Math.log2(1 + c.count));
const MIN_VOICES = 4;

export class Conductor {
  readonly cells: Cell[];
  readonly voices = new Map<number, Voice>();
  private wanted = new Set<number>();
  private artists: ArtistNode[];
  private records: RecordNode[];
  beat = 0;
  minCell = 0;

  constructor(layout: Layout, cells = buildCells(layout)) {
    this.cells = cells;
    this.artists = layout.artists;
    this.records = layout.records;
  }

  /** Choose the artists sitting near the needle (angle 0 = twelve o'clock). */
  static pick(layout: Layout, rot: number, windowSlots = 6): number[] {
    const all: Array<[number, number]> = [];
    const win = layout.unit * windowSlots;
    for (const a of layout.artists) {
      if (a.hidden || a.strand.length === 0) continue;
      let d = a.angle + rot + Math.PI / 2;
      d = Math.atan2(Math.sin(d), Math.cos(d));
      all.push([Math.abs(d), a.i]);
    }
    all.sort((p, q) => p[0] - q[0]);
    const near = all.filter((p) => p[0] < win).slice(0, MAX_VOICES);
    // across an empty stretch of the rim, the nearest few keep playing
    const out = near.length >= MIN_VOICES ? near : all.slice(0, MIN_VOICES);
    return out.map((p) => p[1]);
  }

  want(artistIdxs: number[]) {
    this.wanted = new Set(artistIdxs);
  }

  private repsFor(v: Voice, cell: number) {
    return Math.max(1, v.byRing[cell]?.length ?? 0);
  }

  private makeVoice(artist: number, cell: number, nextBeat: number): Voice {
    const a = this.artists[artist]!;
    const byRing: number[][] = this.cells.map(() => []);
    const strand = [...a.strand].sort(
      (p, q) => this.records[p]!.year - this.records[q]!.year || p - q,
    );
    for (const ri of strand) byRing[this.records[ri]!.ring]?.push(ri);
    const v: Voice = {
      artist,
      cell,
      repsLeft: 0,
      byRing,
      cursor: 0,
      nextBeat,
      leaving: false,
      atEnd: false,
      joinedBeat: nextBeat,
      cellStart: nextBeat,
    };
    v.repsLeft = this.repsFor(v, cell);
    return v;
  }

  /** Plan one beat. Call once per beat, in order. */
  plan(): BeatPlan {
    const b = this.beat;
    const plan: BeatPlan = {
      beat: b,
      notes: [],
      joined: [],
      left: [],
      minCell: this.minCell,
      restarted: false,
    };

    // arrivals and departures are decided on cell boundaries of the slowest cell
    const grid = this.cells[this.minCell]?.beats ?? 4;
    for (const [i, v] of this.voices) if (!this.wanted.has(i)) v.leaving = true;
    for (const i of this.wanted) {
      if (this.voices.has(i)) {
        this.voices.get(i)!.leaving = false;
        continue;
      }
      const entry = Math.ceil(b / grid) * grid;
      this.voices.set(i, this.makeVoice(i, this.minCell, entry));
      plan.joined.push(i);
    }

    for (const [i, v] of this.voices) {
      if (v.nextBeat !== b) continue;
      const cell = this.cells[v.cell]!;
      const recs = v.byRing[v.cell]!;
      const record = recs.length ? recs[v.cursor % recs.length]! : -1;
      if (record >= 0) {
        for (const n of cell.notes) {
          plan.notes.push({
            artist: i,
            record,
            cell: v.cell,
            at: n.at,
            dur: n.dur,
            semis: cell.scale[n.deg] ?? 0,
            vel: n.vel,
          });
        }
      }
      v.cursor += 1;
      // a year without a record is a short rest, not a full cell of silence
      v.nextBeat = b + (record >= 0 ? cell.beats : REST_BEATS);
      v.repsLeft -= 1;
      if (v.leaving) {
        // a departing voice finishes the repetition it is in, then goes
        this.voices.delete(i);
        plan.left.push(i);
        continue;
      }
      if (v.repsLeft > 0) continue;
      if (v.cell >= this.cells.length - 1) {
        v.atEnd = true;
        v.repsLeft = 1; // keep the last cell going until everyone arrives
        continue;
      }
      const dwelt = v.nextBeat - v.cellStart >= dwellFor(cell);
      if (dwelt && v.cell - this.minCell < MAX_LEAD) {
        v.cell += 1;
        v.cursor = 0;
        v.cellStart = v.nextBeat;
        v.repsLeft = this.repsFor(v, v.cell);
      } else {
        v.repsLeft = 1; // wait by repeating
      }
    }

    // ensemble position
    let min = Infinity;
    let staying = 0;
    let allEnd = true;
    for (const v of this.voices.values()) {
      if (v.leaving) continue;
      staying++;
      min = Math.min(min, v.cell);
      if (!v.atEnd) allEnd = false;
    }
    if (Number.isFinite(min)) this.minCell = min;
    if (staying > 0 && allEnd) {
      // the piece is over: a breath, then everyone begins again from the first year
      for (const v of this.voices.values()) {
        v.cell = 0;
        v.cursor = 0;
        v.atEnd = false;
        v.repsLeft = this.repsFor(v, 0);
        v.nextBeat = b + BREATH_BEATS;
        v.cellStart = v.nextBeat;
      }
      this.minCell = 0;
      plan.restarted = true;
    }
    plan.minCell = this.minCell;
    this.beat += 1;
    return plan;
  }
}
