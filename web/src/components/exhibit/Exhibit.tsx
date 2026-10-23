// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Exhibit — the archive as an installation (route /exhibit).
 *
 * Three scenes loop on their own, so the page can run unattended on a projector:
 *   1. disc     — the home record disc, turning; the artists near the needle play "In C"
 *                 (conductor.ts / ensemble.ts, unchanged).
 *   2. islands  — every person as one point. They start packed in one centre (the picture a
 *                 network map of an art world draws) and move to the programmes they were
 *                 collected through: one island per programme, a person on two or more
 *                 programmes between those islands. The share on exactly one programme is
 *                 computed from the rim file, not written in.
 *   3. tech     — the disc again, for one technology at a time (lib/tech.ts, W4). Only people
 *                 whose record titles name it play, each from their first such year. In the
 *                 second half, the venue chords between two of those people are drawn in two
 *                 colours: met before either had named it, or met after both had.
 * Every number on screen is counted from the payload in the browser. No person is ranked or
 * sized by their records; every point has the same size.
 *
 * Input: a click or a key starts the sound (browsers need a gesture). The technology buttons and
 * the arrow keys jump between scenes; after a minute without input the loop resumes. `?kiosk`
 * hides the pointer and the buttons; `?scene=<n>&at=<0..1>` opens a point of one scene.
 */
import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildLayout,
  type Layout,
  type StudyData,
} from "@/components/study/model";
import { Ensemble, type SoundedNote } from "@/components/study/ensemble";
import { audioContext } from "@/components/flight/sound";
import { useLang } from "@/lib/i18n";
import { TECH_CODES, TECH_LABEL, type TechCode } from "@/lib/tech";

const TAU = Math.PI * 2;
const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const clamp = (v: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, v));
const ease = (t: number) => {
  const x = clamp(t, 0, 1);
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
};

/* A darkened room: the exhibit keeps its own palette whatever the site theme is. */
const C = {
  paper: "#0b0d10",
  ink: "#e8eaed",
  dim: "rgba(232,234,237,",
  accent: "#ffd27a",
  before: "#6fb7ff",
  after: "#ff8a5b",
};

/* Scene lengths (seconds). One loop: disc, islands, then the six technologies. */
const DISC_SEC = 80;
const ISLAND_SEC = 70;
const TECH_SEC = 32;
type Scene =
  { kind: "disc" } | { kind: "islands" } | { kind: "tech"; tech: TechCode };
const LOOP: Scene[] = [
  { kind: "disc" },
  { kind: "islands" },
  ...TECH_CODES.map((tech) => ({ kind: "tech" as const, tech })),
];
const lengthOf = (s: Scene) =>
  s.kind === "disc" ? DISC_SEC : s.kind === "islands" ? ISLAND_SEC : TECH_SEC;
const IDLE_RESUME_MS = 60_000;
/** Disc turn (radians per second): one turn in about nine minutes. */
const SPIN = 0.0116;

/* ------------------------------------------------------------------ */
/* islands                                                              */
/* ------------------------------------------------------------------ */

type Islands = {
  /** per artist: centre position and island position, in a unit frame (|x|,|y| ≤ 1) */
  cx: Float32Array;
  cy: Float32Array;
  ix: Float32Array;
  iy: Float32Array;
  /** per artist: number of programmes (0 = none listed; drawn faint, outside both counts) */
  nprog: Uint8Array;
  islands: Array<{
    x: number;
    y: number;
    r: number;
    label_ko: string;
    label_en: string;
    n: number;
  }>;
  bridges: number[];
  single: number;
  listed: number;
};

/**
 * Island layout. A programme's island holds the people listed on that programme only; its area
 * follows that count. Islands are placed largest first on a spiral, each at the first spot that
 * does not overlap one already placed. A person on two or more programmes sits at the mean of
 * those islands' centres, a little jittered by id order so bridges do not stack.
 */
function buildIslands(layout: Layout): Islands {
  const n = layout.artists.length;
  const P = layout.programmes;
  const singles: number[][] = P.map(() => []);
  const bridges: number[] = [];
  const nprog = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const ps = layout.artistProgrammes[i] ?? [];
    nprog[i] = Math.min(255, ps.length);
    if (ps.length === 1) singles[ps[0]!]!.push(i);
    else if (ps.length > 1) bridges.push(i);
  }
  const unitR = (k: number) => Math.sqrt(Math.max(k, 1)) * 0.018;
  // a wide spiral (x stretched by ASPECT) so the islands fill a landscape screen
  const ASPECT = 1.6;
  const GAP = 0.07;
  const order = P.map((_, j) => j).sort(
    (a, b) => singles[b]!.length - singles[a]!.length,
  );
  const placed: Array<{ x: number; y: number; r: number; j: number }> = [];
  for (const j of order) {
    const r = unitR(singles[j]!.length);
    let spot = { x: 0, y: 0 };
    for (let s = 0; s < 20000; s++) {
      const a = s * 0.21;
      const d = s * 0.00045;
      const x = Math.cos(a) * d * ASPECT;
      const y = Math.sin(a) * d;
      if (placed.every((p) => Math.hypot(p.x - x, p.y - y) > p.r + r + GAP)) {
        spot = { x, y };
        break;
      }
    }
    placed.push({ ...spot, r, j });
  }
  // fit: |y| + r within 0.86, |x| + r within 0.86 × ASPECT (the drawing scales by screen height)
  let ext = 0;
  for (const p of placed)
    ext = Math.max(ext, (Math.abs(p.x) + p.r) / ASPECT, Math.abs(p.y) + p.r);
  const k = ext > 0 ? 0.86 / ext : 1;
  const islands = P.map((p) => ({
    x: 0,
    y: 0,
    r: 0,
    label_ko: p.label_ko,
    label_en: p.label_en,
    n: 0,
  }));
  for (const p of placed) {
    const isl = islands[p.j]!;
    isl.x = p.x * k;
    isl.y = p.y * k;
    isl.r = p.r * k;
    isl.n = singles[p.j]!.length;
  }
  const cx = new Float32Array(n);
  const cy = new Float32Array(n);
  const ix = new Float32Array(n);
  const iy = new Float32Array(n);
  // the centre: everyone in one sunflower disc, in rim order (no one in the middle on purpose)
  for (let i = 0; i < n; i++) {
    const rr = Math.sqrt((i + 0.5) / n) * 0.62;
    cx[i] = Math.cos(i * GOLDEN) * rr;
    cy[i] = Math.sin(i * GOLDEN) * rr;
  }
  P.forEach((_, j) => {
    const list = singles[j]!;
    const isl = islands[j]!;
    list.forEach((i, q) => {
      const rr = Math.sqrt((q + 0.5) / list.length) * isl.r * 0.94;
      ix[i] = isl.x + Math.cos(q * GOLDEN) * rr;
      iy[i] = isl.y + Math.sin(q * GOLDEN) * rr;
    });
  });
  bridges.forEach((i, q) => {
    const ps = layout.artistProgrammes[i]!;
    let x = 0;
    let y = 0;
    for (const j of ps) {
      x += islands[j]!.x;
      y += islands[j]!.y;
    }
    const jit = 0.012 + (q % 7) * 0.003;
    ix[i] = x / ps.length + Math.cos(q * GOLDEN) * jit;
    iy[i] = y / ps.length + Math.sin(q * GOLDEN) * jit;
  });
  // people with no programme listed stay where they were (they fade out in the drawing)
  let none = 0;
  for (let i = 0; i < n; i++) {
    if (nprog[i]) continue;
    ix[i] = cx[i]!;
    iy[i] = cy[i]!;
    none++;
  }
  const listed = n - none;
  const single = listed - bridges.length;
  return { cx, cy, ix, iy, nprog, islands, bridges, single, listed };
}

/* ------------------------------------------------------------------ */
/* technology                                                           */
/* ------------------------------------------------------------------ */

type TechView = {
  tech: TechCode;
  /** artist index → first year */
  first: Map<number, number>;
  /** adopters in angle order, for the needle window */
  byAngle: number[];
  /** chords between two adopters: record nodes a, b and when they met relative to adoption */
  chords: Array<{ a: number; b: number; when: "before" | "after" | "between" }>;
  before: number;
  after: number;
  /** first-year counts, for the small histogram */
  years: Map<number, number>;
};

function buildTech(layout: Layout, data: StudyData, tech: TechCode): TechView {
  const first = new Map<number, number>();
  data.artists.forEach((a, i) => {
    const y = a.tech?.[tech];
    if (y != null && !layout.artists[i]!.hidden) first.set(i, y);
  });
  const byAngle = [...first.keys()].sort(
    (p, q) => layout.artists[p]!.angle - layout.artists[q]!.angle,
  );
  const chords: TechView["chords"] = [];
  let before = 0;
  let after = 0;
  for (const c of layout.chords) {
    const ra = layout.records[c.a]!;
    const rb = layout.records[c.b]!;
    const fa = first.get(ra.artist);
    const fb = first.get(rb.artist);
    if (fa == null || fb == null) continue;
    // they met (were recorded at the same venue) by the later of the two record years
    const met = Math.max(ra.year, rb.year);
    const when =
      met < Math.min(fa, fb)
        ? "before"
        : met >= Math.max(fa, fb)
          ? "after"
          : "between";
    if (when === "before") before++;
    else if (when === "after") after++;
    chords.push({ a: c.a, b: c.b, when });
  }
  const years = new Map<number, number>();
  for (const y of first.values()) years.set(y, (years.get(y) ?? 0) + 1);
  return { tech, first, byAngle, chords, before, after, years };
}

/* ------------------------------------------------------------------ */
/* component                                                            */
/* ------------------------------------------------------------------ */

export function Exhibit({ data }: { data: StudyData }) {
  const { lang, t } = useLang();
  const layout = useMemo(() => buildLayout(data, { R: 380 }), [data]);
  const islands = useMemo(() => buildIslands(layout), [layout]);
  const techs = useMemo(
    () =>
      new Map(TECH_CODES.map((k) => [k, buildTech(layout, data, k)] as const)),
    [layout, data],
  );
  const kiosk = useMemo(
    () =>
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).has("kiosk"),
    [],
  );
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sources = useMemo(
    () => new Set(data.records.map((r) => r.domain)).size,
    [data.records],
  );

  const st = useRef({
    rot: Math.random() * TAU,
    scene: 0,
    sceneStart: 0,
    lastInput: -Infinity,
    music: null as Ensemble | null,
    notes: [] as SoundedNote[],
    base: null as HTMLCanvasElement | null,
    baseKey: "",
    w: 0,
    h: 0,
    dpr: 1,
  }).current;

  const [hud, setHud] = useState({
    scene: 0,
    sound: false,
    cell: "",
    reading: "",
  });

  /* ---- sound ---- */
  const voicesFor = useCallback(
    (rot: number): number[] | null => {
      const sc = LOOP[st.scene]!;
      if (sc.kind === "islands") {
        // the people between islands play, a few at a time, changing every 20 seconds
        const b = islands.bridges;
        if (!b.length) return [];
        const k = Math.floor(performance.now() / 20000);
        const out: number[] = [];
        for (let q = 0; q < 8; q++) out.push(b[(k * 8 + q * 53) % b.length]!);
        return out;
      }
      if (sc.kind === "tech") {
        // the adopters nearest the needle
        const tv = techs.get(sc.tech)!;
        const d = (i: number) => {
          const a = layout.artists[i]!.angle + rot + Math.PI / 2;
          return Math.abs(Math.atan2(Math.sin(a), Math.cos(a)));
        };
        return [...tv.byAngle].sort((p, q) => d(p) - d(q)).slice(0, 10);
      }
      return null;
    },
    [st, islands, techs, layout],
  );

  const startSound = useCallback(() => {
    if (st.music) {
      void audioContext()?.resume();
      return;
    }
    try {
      const ens = new Ensemble(layout, (n) => {
        st.notes.push(n);
        if (st.notes.length > 400) st.notes.splice(0, st.notes.length - 400);
      });
      ens.chooser = voicesFor;
      st.music = ens;
      void ens.start(st.rot);
      setHud((h) => ({ ...h, sound: true }));
    } catch {
      // no audio in this browser: the picture runs on its own
    }
  }, [layout, st, voicesFor]);

  const goScene = useCallback(
    (k: number, byUser = true) => {
      st.scene = ((k % LOOP.length) + LOOP.length) % LOOP.length;
      st.sceneStart = performance.now();
      if (byUser) st.lastInput = performance.now();
      const sc = LOOP[st.scene]!;
      const m = st.music;
      if (m) {
        // technology voices play from their first year; other scenes play everything
        m.conductor.from =
          sc.kind === "tech"
            ? (i) => techs.get(sc.tech)!.first.get(i) ?? Infinity
            : null;
        // voices built for the previous scene leave on their next boundary and come back rebuilt
        for (const v of m.conductor.voices.values()) v.leaving = true;
      }
      setHud((h) => ({ ...h, scene: st.scene }));
    },
    [st, techs],
  );

  /* ---- input ---- */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      startSound();
      if (e.key === "ArrowRight") goScene(st.scene + 1);
      else if (e.key === "ArrowLeft") goScene(st.scene - 1);
    };
    const onPointer = () => startSound();
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointer);
    };
  }, [goScene, startSound, st]);

  useEffect(() => () => st.music?.stop(), [st]);

  /* ---- drawing ---- */
  useEffect(() => {
    const cv = canvasRef.current!;
    const ctx = cv.getContext("2d")!;
    let raf = 0;
    let last = performance.now();
    st.sceneStart = last;
    let hudAt = 0;
    // `?scene=<n>&at=<0..1>` opens one scene at one point of its timeline (for documentation shots
    // and recordings); the loop then holds there for a minute like after any input.
    const q = new URLSearchParams(window.location.search);
    if (q.has("scene")) {
      const k = clamp(
        Math.round(Number(q.get("scene")) || 0),
        0,
        LOOP.length - 1,
      );
      goScene(k);
      const at = clamp(Number(q.get("at")) || 0, 0, 1);
      st.sceneStart = last - at * lengthOf(LOOP[k]!) * 1000;
    }

    const resize = () => {
      st.dpr = Math.min(2, window.devicePixelRatio || 1);
      st.w = cv.clientWidth;
      st.h = cv.clientHeight;
      cv.width = Math.round(st.w * st.dpr);
      cv.height = Math.round(st.h * st.dpr);
      st.baseKey = "";
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(cv);

    // the caption column on the left (capW at padding pad) is kept clear of the disc
    const padOf = () => Math.max(20, st.w * 0.025);
    const capOf = () => Math.min(520, st.w * 0.3);
    const free = () => st.w - (capOf() + padOf() * 2);
    const discR = () => Math.min(st.h * 0.44, (free() / 2) * 0.96);
    const discCx = () => Math.max(st.w / 2, capOf() + padOf() * 2 + discR());
    const worldK = () => discR() / (layout.rimR * 1.04);

    /** The still disc: rings and every record, drawn once per size into an offscreen canvas. */
    const baseDisc = () => {
      const key = `${st.w}x${st.h}@${st.dpr}`;
      if (st.base && st.baseKey === key) return st.base;
      const R = discR();
      const size = Math.ceil(R * 2.1 * st.dpr);
      const off = st.base ?? document.createElement("canvas");
      off.width = size;
      off.height = size;
      const o = off.getContext("2d")!;
      o.setTransform(st.dpr, 0, 0, st.dpr, size / 2, size / 2);
      o.clearRect(-size, -size, size * 2, size * 2);
      const k = worldK();
      o.strokeStyle = C.dim + "0.08)";
      o.lineWidth = 0.6;
      for (const rg of layout.rings) {
        o.beginPath();
        o.arc(0, 0, rg.r * k, 0, TAU);
        o.stroke();
      }
      o.fillStyle = C.dim + "0.42)";
      const p = new Path2D();
      const dot = Math.max(0.55, k * 0.9);
      for (const nd of layout.records) {
        if (layout.artists[nd.artist]!.hidden) continue;
        p.moveTo(nd.x * k + dot, nd.y * k);
        p.arc(nd.x * k, nd.y * k, dot, 0, TAU);
      }
      o.fill(p);
      o.fillStyle = C.dim + "0.7)";
      const knots = new Path2D();
      for (const a of layout.artists) {
        if (a.hidden) continue;
        knots.moveTo(a.x * k + 1.1, a.y * k);
        knots.arc(a.x * k, a.y * k, 1.1, 0, TAU);
      }
      o.fill(knots);
      st.base = off;
      st.baseKey = key;
      return off;
    };

    const text = (
      s: string,
      x: number,
      y: number,
      size: number,
      color: string,
      align: CanvasTextAlign = "left",
    ) => {
      ctx.font = `${size}px ui-monospace, SFMono-Regular, Menlo, monospace`;
      ctx.fillStyle = color;
      ctx.textAlign = align;
      ctx.fillText(s, x, y);
    };
    const wrapText = (
      s: string,
      x: number,
      y: number,
      size: number,
      color: string,
      maxW: number,
    ) => {
      ctx.font = `${size}px ui-monospace, SFMono-Regular, Menlo, monospace`;
      let line = "";
      let yy = y;
      for (const word of s.split(" ")) {
        const next = line ? `${line} ${word}` : word;
        if (ctx.measureText(next).width > maxW && line) {
          text(line, x, yy, size, color);
          line = word;
          yy += size * 1.5;
        } else line = next;
      }
      if (line) text(line, x, yy, size, color);
      return yy;
    };

    const nameOf = (i: number) => {
      const a = data.artists[i]!;
      return (
        (lang === "ko" ? a.name_ko : a.name_en) ||
        a.name_ko ||
        a.name_en ||
        a.id
      );
    };

    const drawDisc = (now: number, tv: TechView | null, phase: number) => {
      const cx = discCx();
      const cy = st.h / 2;
      const k = worldK();
      const base = baseDisc();
      const half = base.width / st.dpr / 2;
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(st.rot);
      ctx.globalAlpha = tv ? 0.28 : 1;
      ctx.drawImage(base, -half, -half, half * 2, half * 2);
      ctx.globalAlpha = 1;
      if (tv) {
        // chords between adopters, second half of the scene
        const show = clamp((phase - 0.45) / 0.12, 0, 1);
        if (show > 0) {
          for (const kind of ["before", "after"] as const) {
            const path = new Path2D();
            for (const c of tv.chords) {
              if (c.when !== kind) continue;
              const a = layout.records[c.a]!;
              const b = layout.records[c.b]!;
              path.moveTo(a.x * k, a.y * k);
              path.quadraticCurveTo(0, 0, b.x * k, b.y * k);
            }
            ctx.strokeStyle = kind === "before" ? C.before : C.after;
            ctx.globalAlpha = 0.16 * show;
            ctx.lineWidth = 0.8;
            ctx.stroke(path);
          }
          ctx.globalAlpha = 1;
        }
        // adopters' strands: faint before the first year, lit from it on
        const lit = new Path2D();
        const pre = new Path2D();
        const firsts = new Path2D();
        for (const [i, y0] of tv.first) {
          const a = layout.artists[i]!;
          for (const ri of a.strand) {
            const nd = layout.records[ri]!;
            const target = nd.year >= y0 ? lit : pre;
            target.moveTo(nd.x * k + 1.3, nd.y * k);
            target.arc(nd.x * k, nd.y * k, 1.3, 0, TAU);
          }
          const ring =
            layout.rings.find((r) => r.year === y0) ??
            layout.rings[y0 < 2017 ? 0 : layout.rings.length - 1]!;
          const fx = Math.cos(a.angle) * ring.r * k;
          const fy = Math.sin(a.angle) * ring.r * k;
          firsts.moveTo(fx + 2.6, fy);
          firsts.arc(fx, fy, 2.6, 0, TAU);
        }
        ctx.fillStyle = C.dim + "0.35)";
        ctx.fill(pre);
        ctx.fillStyle = C.accent;
        ctx.globalAlpha = 0.75;
        ctx.fill(lit);
        ctx.globalAlpha = 1;
        ctx.strokeStyle = C.accent;
        ctx.lineWidth = 1;
        ctx.stroke(firsts);
      }
      // notes sounding now
      const glow = new Path2D();
      st.notes = st.notes.filter((n) => now < n.at + n.dur * 1000 + 600);
      for (const n of st.notes) {
        if (now < n.at || n.record < 0) continue;
        const nd = layout.records[n.record]!;
        const age = (now - n.at) / (n.dur * 1000 + 600);
        const r = 2 + 7 * (1 - age);
        glow.moveTo(nd.x * k + r, nd.y * k);
        glow.arc(nd.x * k, nd.y * k, r, 0, TAU);
      }
      ctx.fillStyle = tv ? C.accent : C.ink;
      ctx.globalAlpha = 0.55;
      ctx.fill(glow);
      ctx.globalAlpha = 1;
      ctx.restore();

      // the needle at twelve o'clock, and the name it reads
      const R = discR();
      ctx.strokeStyle = C.accent;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx, cy - R * 1.06);
      ctx.lineTo(cx, cy - R * 0.2);
      ctx.stroke();
      let best = -1;
      let bestD = Infinity;
      const pool = tv ? tv.byAngle : null;
      const consider = (i: number) => {
        const a = layout.artists[i]!;
        if (a.hidden || !a.strand.length) return;
        const d = Math.abs(
          Math.atan2(
            Math.sin(a.angle + st.rot + Math.PI / 2),
            Math.cos(a.angle + st.rot + Math.PI / 2),
          ),
        );
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      };
      if (pool) pool.forEach(consider);
      else for (let i = 0; i < layout.artists.length; i++) consider(i);
      if (best >= 0) {
        const extra = tv ? ` · ${tv.first.get(best)}` : "";
        text(nameOf(best) + extra, cx, cy - R * 1.09, 15, C.ink, "center");
      }
      return best;
    };

    const drawIslands = (phase: number) => {
      const k = Math.min((st.w * 0.5) / 1.6, st.h * 0.5) * 0.98;
      const ox = st.w / 2;
      const oy = st.h / 2 + 10;
      // 0–0.28 the centre; 0.28–0.55 moving; then the islands
      const m = ease((phase - 0.28) / 0.27);
      const n = layout.artists.length;
      const path = new Path2D();
      const bridgePath = new Path2D();
      const nonePath = new Path2D();
      for (let i = 0; i < n; i++) {
        if (layout.artists[i]!.hidden) continue;
        const stagger = clamp(m * 1.25 - ((i * 0.61803) % 1) * 0.25, 0, 1);
        const x =
          ox +
          (islands.cx[i]! + (islands.ix[i]! - islands.cx[i]!) * stagger) * k;
        const y =
          oy +
          (islands.cy[i]! + (islands.iy[i]! - islands.cy[i]!) * stagger) * k;
        const target =
          islands.nprog[i] === 0
            ? nonePath
            : islands.nprog[i]! > 1
              ? bridgePath
              : path;
        target.moveTo(x + 1.6, y);
        target.arc(x, y, 1.6, 0, TAU);
      }
      // threads from each person between islands to the islands they belong to
      if (m > 0.6) {
        const th = new Path2D();
        for (const i of islands.bridges) {
          const x = ox + islands.ix[i]! * k;
          const y = oy + islands.iy[i]! * k;
          for (const j of layout.artistProgrammes[i]!) {
            const isl = islands.islands[j]!;
            th.moveTo(x, y);
            th.lineTo(ox + isl.x * k, oy + isl.y * k);
          }
        }
        ctx.strokeStyle = C.accent;
        ctx.globalAlpha = 0.07 * ((m - 0.6) / 0.4);
        ctx.lineWidth = 0.7;
        ctx.stroke(th);
        ctx.globalAlpha = 1;
      }
      ctx.fillStyle = C.dim + "0.75)";
      ctx.fill(path);
      ctx.fillStyle = m > 0.5 ? C.accent : C.dim + "0.75)";
      ctx.fill(bridgePath);
      // people with no programme listed belong to no island: they fade out as the islands form
      ctx.fillStyle = C.dim + `${0.75 * (1 - m)})`;
      ctx.fill(nonePath);
      if (m > 0.85) {
        ctx.globalAlpha = (m - 0.85) / 0.15;
        for (const isl of islands.islands) {
          if (!isl.n) continue;
          ctx.strokeStyle = C.dim + "0.18)";
          ctx.beginPath();
          ctx.arc(ox + isl.x * k, oy + isl.y * k, isl.r * k + 6, 0, TAU);
          ctx.stroke();
          const label = lang === "ko" ? isl.label_ko : isl.label_en;
          text(
            label,
            ox + isl.x * k,
            oy + (isl.y - isl.r) * k - 12,
            isl.r * k > 40 ? 13 : 11,
            C.ink,
            "center",
          );
        }
        ctx.globalAlpha = 1;
      }
      return m;
    };

    const frame = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      // the loop moves on by itself unless someone chose a scene within the last minute
      const sc = LOOP[st.scene]!;
      const len = lengthOf(sc) * 1000;
      if (now - st.sceneStart > len && now - st.lastInput > IDLE_RESUME_MS)
        goScene(st.scene + 1, false);
      const phase = clamp((now - st.sceneStart) / len, 0, 1);
      st.rot += SPIN * dt;
      st.music?.update(st.rot);

      ctx.setTransform(st.dpr, 0, 0, st.dpr, 0, 0);
      ctx.fillStyle = C.paper;
      ctx.fillRect(0, 0, st.w, st.h);

      const pad = padOf();
      const capW = capOf();
      const ko = lang === "ko";
      let reading = -1;
      if (sc.kind === "disc") {
        reading = drawDisc(now, null, phase);
        text(t("기록의 원반", "The record disc"), pad, pad + 18, 20, C.ink);
        wrapText(
          t(
            `한국 미디어아트 프로그램 명단에 오른 ${layout.artists.filter((a) => !a.hidden).length.toLocaleString()}명, 출처가 붙은 기록 ${layout.records.length.toLocaleString()}건, 출처 ${sources.toLocaleString()}곳. 고리 하나가 한 해, 점 하나가 기록 하나다. 바늘 곁의 작가들이 자기 기록의 해마다 한 음형을 연주한다.`,
            `${layout.artists.filter((a) => !a.hidden).length.toLocaleString()} people on Korean media-art programme rosters, ${layout.records.length.toLocaleString()} sourced records from ${sources.toLocaleString()} sources. One ring is a year, one dot a record. The artists near the needle play one cell for each of their records.`,
          ),
          pad,
          pad + 50,
          13,
          C.dim + "0.7)",
          capW,
        );
      } else if (sc.kind === "islands") {
        const m = drawIslands(phase);
        const pct = (
          (islands.single / Math.max(1, islands.listed)) *
          100
        ).toFixed(1);
        if (m < 0.5) {
          text(t("중심", "A centre"), pad, pad + 18, 20, C.ink);
          wrapText(
            t(
              "2016년 뮌의 〈아트솔라리스〉는 미술계 인사들의 전시 이력으로 하나의 중심을 그렸다. 여기 모인 점은 모두 같은 크기다.",
              "In 2016 Mioon's Art Solaris drew an art world around one centre from its people's exhibition histories. Every point gathered here is the same size.",
            ),
            pad,
            pad + 50,
            13,
            C.dim + "0.7)",
            capW,
          );
        } else {
          text(t("섬들", "Islands"), pad, pad + 18, 20, C.ink);
          wrapText(
            t(
              `출처가 붙은 명단으로 다시 놓으면 ${islands.listed.toLocaleString()}명 가운데 ${islands.single.toLocaleString()}명(${pct}%)은 한 프로그램에만 있다. 섬 사이에 선 ${islands.bridges.length.toLocaleString()}명이 지금 연주한다.`,
              `Placed again by sourced rosters, ${islands.single.toLocaleString()} of ${islands.listed.toLocaleString()} people (${pct}%) are on one programme only. The ${islands.bridges.length.toLocaleString()} standing between islands are the ones playing now.`,
            ),
            pad,
            pad + 50,
            13,
            C.dim + "0.7)",
            capW,
          );
        }
      } else {
        const tv = techs.get(sc.tech)!;
        reading = drawDisc(now, tv, phase);
        const [lk, le] = TECH_LABEL[sc.tech];
        text(ko ? lk : le, pad, pad + 18, 20, C.accent);
        let y = wrapText(
          t(
            `기록 제목에 이 기술이 처음 나온 해부터 연주한다. ${tv.first.size.toLocaleString()}명. 동그라미는 그 첫해.`,
            `Each plays from the first year a record title names this technology. ${tv.first.size.toLocaleString()} people. A circle marks that first year.`,
          ),
          pad,
          pad + 50,
          13,
          C.dim + "0.7)",
          capW,
        );
        // first-year histogram
        const ys = [...tv.years.keys()].sort((a, b) => a - b);
        if (ys.length) {
          const y0 = Math.max(2000, ys[0]!);
          const y1 = Math.max(...ys);
          const maxN = Math.max(...tv.years.values());
          const bw = Math.max(3, Math.min(10, capW / (y1 - y0 + 1) - 1));
          const top = y + 24;
          const hh = 48;
          for (let yr = y0; yr <= y1; yr++) {
            const v =
              yr === y0
                ? ys
                    .filter((q) => q <= y0)
                    .reduce((s, q) => s + tv.years.get(q)!, 0)
                : (tv.years.get(yr) ?? 0);
            const h = (v / maxN) * hh;
            ctx.fillStyle = C.accent;
            ctx.globalAlpha = 0.7;
            ctx.fillRect(pad + (yr - y0) * (bw + 1), top + hh - h, bw, h);
          }
          ctx.globalAlpha = 1;
          text(
            `${y0 <= ys[0]! ? y0 : "≤" + y0}`,
            pad,
            top + hh + 14,
            10,
            C.dim + "0.6)",
          );
          text(
            String(y1),
            pad + (y1 - y0) * (bw + 1) + bw,
            top + hh + 14,
            10,
            C.dim + "0.6)",
            "right",
          );
          y = top + hh + 20;
        }
        if (phase > 0.45) {
          ctx.globalAlpha = clamp((phase - 0.45) / 0.12, 0, 1);
          y = wrapText(
            t(
              "선은 같은 장소에 기록된 두 사람이다.",
              "A line joins two of them recorded at the same venue.",
            ),
            pad,
            y + 22,
            13,
            C.dim + "0.7)",
            capW,
          );
          text(
            t(
              `둘 다 쓰기 전에 만남 ${tv.before.toLocaleString()}`,
              `met before either named it ${tv.before.toLocaleString()}`,
            ),
            pad,
            y + 22,
            13,
            C.before,
          );
          text(
            t(
              `둘 다 쓴 뒤에 만남 ${tv.after.toLocaleString()}`,
              `met after both had ${tv.after.toLocaleString()}`,
            ),
            pad,
            y + 42,
            13,
            C.after,
          );
          text(
            t(
              "보정하지 않은 단순 집계 (기록은 해마다 늘어난다)",
              "raw counts, not adjusted (records grow every year)",
            ),
            pad,
            y + 64,
            11,
            C.dim + "0.5)",
          );
          ctx.globalAlpha = 1;
        }
      }

      // the year the ensemble is in
      const m = st.music;
      if (m?.running) {
        const cell = m.conductor.cells[m.conductor.minCell]?.label ?? "";
        text(
          t(`연주 중인 해 ${cell}`, `playing the year ${cell}`),
          st.w - pad,
          st.h - pad,
          12,
          C.dim + "0.6)",
          "right",
        );
      }
      if (now - hudAt > 500) {
        hudAt = now;
        const name = reading >= 0 ? nameOf(reading) : "";
        setHud((h) => (h.reading === name ? h : { ...h, reading: name }));
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [layout, islands, techs, data, lang, t, st, goScene, sources]);

  // Recover from a lost GPU or a stalled tab: an installation reloads itself once an hour.
  useEffect(() => {
    if (!kiosk) return;
    const id = window.setTimeout(
      () => window.location.reload(),
      60 * 60 * 1000,
    );
    return () => window.clearTimeout(id);
  }, [kiosk]);

  const sc = LOOP[hud.scene]!;
  return (
    <div
      className="relative h-dvh w-full overflow-hidden"
      style={{ background: C.paper, cursor: kiosk ? "none" : undefined }}
    >
      <canvas
        ref={canvasRef}
        className="absolute inset-0 h-full w-full"
        aria-label={t("기예 전시 화면", "Giye exhibit")}
      />
      {!hud.sound && (
        <p
          className="pointer-events-none absolute bottom-6 left-1/2 -translate-x-1/2 font-mono text-[12px]"
          style={{ color: C.ink, opacity: 0.6 }}
        >
          {t("화면을 누르면 소리가 납니다", "Touch the screen for sound")}
        </p>
      )}
      {!kiosk && (
        <>
          <nav className="absolute bottom-5 left-5 flex flex-wrap gap-2 font-mono text-[11px]">
            {LOOP.map((s, k) => {
              const label =
                s.kind === "disc"
                  ? t("원반", "disc")
                  : s.kind === "islands"
                    ? t("섬들", "islands")
                    : TECH_LABEL[s.tech][lang === "ko" ? 0 : 1];
              const on = k === hud.scene;
              return (
                <button
                  key={k}
                  type="button"
                  onClick={() => goScene(k)}
                  className="border px-2 py-1"
                  style={{
                    color: on ? C.paper : C.ink,
                    background: on ? C.accent : "transparent",
                    borderColor: on ? C.accent : "rgba(232,234,237,0.3)",
                  }}
                >
                  {label}
                </button>
              );
            })}
          </nav>
          <Link
            to="/"
            className="absolute right-5 top-5 font-mono text-[11px] no-underline"
            style={{ color: C.ink, opacity: 0.6 }}
          >
            {t("기예 홈", "Giye home")}
          </Link>
        </>
      )}
      <span className="sr-only" aria-live="polite">
        {sc.kind === "tech" ? TECH_LABEL[sc.tech][lang === "ko" ? 0 : 1] : ""}{" "}
        {hud.reading}
      </span>
    </div>
  );
}
