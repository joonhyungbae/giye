// SPDX-License-Identifier: AGPL-3.0-only
import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { SITE_NAV } from "@/components/SiteChrome";
import { playTick } from "@/components/flight/sound";
import { Ensemble, type SoundedNote } from "./ensemble";
import { RecordSheet, type SheetPreview } from "./RecordSheet";
import { StudySearch } from "./StudySearch";
import { LinkLegend, type LegendKey, type LinkStats } from "./LinkLegend";
import { verLabel } from "./labels";
import { readTheme, withAlpha } from "./theme";
import { mulberry32 } from "@/components/flight/flightState";
import { ACTIVITY_TYPE_LABEL, useLang } from "@/lib/i18n";
import {
  buildLayout,
  buildStrata,
  srcIndex,
  warmSearch,
  type Layout,
  type Strata,
  type StudyData,
} from "./model";

/* ------------------------------------------------------------------ */
/* helpers                                                              */
/* ------------------------------------------------------------------ */

const TAU = Math.PI * 2;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/* Draw batching: marks that share a style go into one path and are painted with one call.
   Tens of thousands of separate beginPath/fill calls a frame are what made the canvas stall. */
type Batch = Map<string, Path2D>;
/** Alpha rounded to half-percent steps, so a batch key (and the colour cache) stays small. */
const qa = (a: number) => Math.round(a * 200) / 200;
/** Subpaths per Path2D. One huge path is slow to rasterise (overlap/winding work grows with it),
    so a batch key rolls over to a fresh path every CHUNK marks. */
const CHUNK = 192;
const chunkCount = new WeakMap<Batch, Map<string, number>>();
const pathIn = (b: Batch, key: string): Path2D => {
  let counts = chunkCount.get(b);
  if (!counts) {
    counts = new Map();
    chunkCount.set(b, counts);
  }
  const n = counts.get(key) ?? 0;
  counts.set(key, n + 1);
  const k = n < CHUNK ? key : `${key}\u0000${Math.floor(n / CHUNK)}`;
  let p = b.get(k);
  if (!p) {
    p = new Path2D();
    b.set(k, p);
  }
  return p;
};
const styleOf = (k: string) => {
  const cut = k.indexOf("\u0000");
  return cut < 0 ? k : k.slice(0, cut);
};
const addDot = (b: Batch, style: string, x: number, y: number, r: number) => {
  const p = pathIn(b, style);
  p.moveTo(x + r, y);
  p.arc(x, y, r, 0, TAU);
};
const fillBatch = (ctx: CanvasRenderingContext2D, b: Batch) => {
  for (const [k, p] of b) {
    ctx.fillStyle = styleOf(k);
    ctx.fill(p);
  }
  b.clear();
  chunkCount.delete(b);
};
/** Stroke batch keys are `${lineWidth}|${style}`, optionally prefixed `${lane}#`.
    A single path paints overlaps once, so faint lines that are meant to add up where they
    cross are spread over lanes: each lane is one call, and lanes still stack on each other. */
const strokeKey = (lw: number, style: string, lane = -1) =>
  lane < 0 ? `${lw}|${style}` : `${lane}#${lw}|${style}`;
const LANES = 256;
/* Text width is measured for every visible label on every frame; the same few hundred strings
   repeat, so widths are kept per (font, text). Cleared when the sheet is redrawn at a new size. */
const textWidths = new Map<string, number>();
const measure = (ctx: CanvasRenderingContext2D, text: string): number => {
  const key = `${ctx.font}\u0000${text}`;
  let wv = textWidths.get(key);
  if (wv === undefined) {
    wv = ctx.measureText(text).width;
    if (textWidths.size > 4000) textWidths.clear();
    textWidths.set(key, wv);
  }
  return wv;
};

/** Most record→source lines drawn per frame (see the sources layer); fewer while the view moves. */
const SOURCE_LINE_BUDGET = 8000;
const SOURCE_LINE_BUDGET_MOVING = 3000;
/**
 * V1. A bundle is one year plate, one source pier, and one of 16 equal sectors of 2π,
 * measured in the disc's own frame so membership does not change while the disc turns.
 * One line per bundle stands for every record it carries: tens of thousands of record
 * lines become a few thousand threads, and the picture shows the flow instead of overdraw.
 */
const BUNDLE_SECTORS = 16;
/**
 * V3. Density normalisation. Overlap grows with the number of lines, so the veil should
 * stay a veil at any data size, by a rule rather than a hand-picked alpha. In stages 2–4
 * the base alpha of a background line set is multiplied by min(1, K/sqrt(N)), where N is
 * the number of lines drawn in that set this frame. K = 40 for bundled source threads and
 * K = 25 for the per-artist top-to-frame lines. Kept after the stage-2 shots: the densest
 * patch of the cone is no longer solid black, and paper shows between the threads.
 */
const BUNDLE_DENSITY_K = 40;
const ARTIST_LINE_DENSITY_K = 25;
const strokeBatch = (ctx: CanvasRenderingContext2D, b: Batch) => {
  for (const [k, p] of b) {
    const key = styleOf(k);
    const hash = key.indexOf("#");
    const bar = key.indexOf("|", hash + 1);
    ctx.lineWidth = Number(key.slice(hash + 1, bar));
    ctx.strokeStyle = key.slice(bar + 1);
    ctx.stroke(p);
  }
  b.clear();
  chunkCount.delete(b);
};
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const damp = (cur: number, target: number, lambda: number, dt: number) =>
  cur + (target - cur) * (1 - Math.exp(-lambda * dt));
const shortAngle = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));
const dampAngle = (cur: number, target: number, lambda: number, dt: number) =>
  cur + shortAngle(target - cur) * (1 - Math.exp(-lambda * dt));
const smooth = (e0: number, e1: number, x: number) => {
  const t = clamp((x - e0) / (e1 - e0), 0, 1);
  return t * t * (3 - 2 * t);
};
const easeOutCubic = (t: number) => 1 - Math.pow(1 - clamp(t, 0, 1), 3);
const easeInOut = (t: number) => {
  const x = clamp(t, 0, 1);
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
};
const easeOutBack = (t: number) => {
  const x = clamp(t, 0, 1);
  const c1 = 1.25;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
};

const truncate = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

/** Canvas labels have no record title (W1). Show the structured fields that are present. */
function recordMark(
  r: { venue: string | null; year: number; activity_type: string },
  lang: "ko" | "en",
  venueCap = 26,
): string {
  const pair = ACTIVITY_TYPE_LABEL[r.activity_type];
  // "other" says nothing on a label; it was left out before titles were removed too
  const type =
    r.activity_type === "other" ? "" : pair ? pair[lang === "ko" ? 0 : 1] : r.activity_type;
  const venue = r.venue ? truncate(r.venue, venueCap) : "";
  return [venue, String(r.year), type].filter(Boolean).join(" · ");
}

type Hover = {
  kind: "record" | "artist" | "source" | "ring" | "chord";
  idx: number;
} | null;

type Msg = { id: number; role: "you" | "guide"; text: string; chips?: number[] };

/** Copy to the clipboard; falls back to execCommand where the async API is missing (plain-http access by IP). */
async function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // fall through to the legacy path
    }
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "0";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  ta.setSelectionRange(0, text.length);
  const ok = document.execCommand("copy");
  document.body.removeChild(ta);
  if (!ok) throw new Error("copy failed");
}

/* Assembly timeline (seconds). */
const A_PLATES = [0.1, 1.1] as const;
const A_SOURCES = [0.4, 1.6] as const;
const A_RECORDS = [1.2, 6.0] as const;
const A_CLOSE = [6.1, 7.8] as const;
const RECORD_FLIGHT = 0.75;

type ViewState = { r: number; t: number; y: number; p: string | null; a: string | null };

const STAGES = 4;
const STAGE_NAMES: Array<[string, string]> = [
  ["조립", "assembled"],
  ["층", "layers"],
  ["연도 판", "year plates"],
  ["진입 세대 조각", "generation segments"],
  ["기록 부품", "record parts"],
];

function PartsList({
  stage,
  layout,
  strata,
  data,
  parts,
  picked,
  lang,
  t,
  nameOf,
}: {
  stage: number;
  layout: Layout;
  strata: Strata;
  data: StudyData;
  parts: number;
  /** record node index the reader clicked, so the list marks the same part the disc does */
  picked: number | null;
  lang: "ko" | "en";
  t: (ko: string, en: string) => string;
  nameOf: (i: number) => string;
}) {
  const rows: Array<[string, string, string, boolean?]> = [];
  if (stage >= 1) {
    rows.push(["1", t("기록 원반", "record disc"), String(layout.records.length)]);
    rows.push(["2", t("출처 기둥", "source piers"), String(strata.sources.length)]);
    rows.push(["3", t("진입 세대 판", "generation plates"), String(layout.groups.length)]);
  }
  if (stage >= 2) {
    rows.push(["—", "", ""]);
    let n = 0;
    for (const rg of layout.rings) {
      if (rg.count === 0 && rg.year !== null) continue;
      n++;
      rows.push([String(n), `${t("연도 판", "year plate")} ${rg.label}`, String(rg.count)]);
    }
  }
  if (stage >= 3) {
    rows.push(["—", "", ""]);
    layout.groups.forEach((g, gi) =>
      rows.push([
        String.fromCharCode(65 + gi),
        lang === "ko" ? g.label_ko : g.label_en,
        String(g.n),
      ]),
    );
  }
  if (stage >= 4 && parts >= 0) {
    rows.push(["—", "", ""]);
    const a = layout.artists[parts]!;
    rows.push(["", nameOf(parts) + " · " + a.id, ""]);
    // the strand already holds this artist's record nodes; their index is the key into sourceOf
    // same order as the numbered parts on the disc (year, then node), so the numbers agree
    const list = a.strand
      .map((ri) => ({ ri, r: data.records[srcIndex(layout.records[ri]!)]! }))
      .sort((p, q) => p.r.year - q.r.year || p.ri - q.ri);
    list.forEach(({ ri, r }, j) =>
      rows.push([
        String(j + 1),
        recordMark(r, lang),
        truncate(strata.sources[strata.sourceOf[ri]!]!.domain, 18),
        ri === picked,
      ]),
    );
  }
  return (
    <table className="w-full border-collapse">
      <tbody>
        {rows.map(([n, name, qty, hot], i) =>
          n === "—" ? (
            <tr key={i}>
              <td colSpan={3} className="border-t border-input pt-1" />
            </tr>
          ) : (
            <tr key={i} className={`align-top ${hot ? "bg-primary/10 text-primary" : ""}`}>
              <td className="w-6 pr-1 text-muted-foreground tabular-nums">{n}</td>
              <td className="pr-2">{name}</td>
              <td className="text-right text-muted-foreground tabular-nums">{qty}</td>
            </tr>
          ),
        )}
      </tbody>
    </table>
  );
}

function parseHash(): ViewState | null {
  if (typeof window === "undefined") return null;
  const h = window.location.hash.replace(/^#/, "");
  if (!h) return null;
  const q = new URLSearchParams(h);
  if (q.get("v") !== "1") return null;
  const num = (k: string, def: number) => {
    const v = Number(q.get(k));
    return Number.isFinite(v) && q.get(k) !== null ? v : def;
  };
  return {
    r: num("r", 0),
    t: num("t", 0),
    y: num("y", 0),
    p: q.get("p"),
    a: q.get("a"),
  };
}

/* ------------------------------------------------------------------ */
/* component                                                            */
/* ------------------------------------------------------------------ */

export function ArchivalStudy({ data, modeSwitch }: { data: StudyData; modeSwitch?: ReactNode }) {
  const { lang, setLang, t } = useLang();
  const layout = useMemo<Layout>(() => buildLayout(data, { R: 380 }), [data]);
  const strata = useMemo<Strata>(() => buildStrata(layout, data), [layout, data]);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const initial = useMemo(() => parseHash(), []);

  const reduced = useMemo(
    () =>
      typeof window !== "undefined" &&
      !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  /* previous snapshot */
  const prevSnapshot = useMemo(() => {
    const list = data.snapshots ?? [];
    return list.length ? list[list.length - 1]! : null;
  }, [data.snapshots]);
  const newSince = useMemo(() => {
    if (!prevSnapshot) return null;
    const known = new Set(prevSnapshot.rows);
    const s = new Set<number>();
    layout.records.forEach((nd) => {
      if (!known.has(srcIndex(nd))) s.add(nd.i);
    });
    return s;
  }, [prevSnapshot, layout.records]);

  // the first query would otherwise pay for folding every row; do it while the page is idle
  useEffect(() => {
    const w = window as unknown as {
      requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    const run = () => warmSearch(data);
    const id = w.requestIdleCallback
      ? w.requestIdleCallback(run, { timeout: 4000 })
      : window.setTimeout(run, 1500);
    return () => {
      if (w.cancelIdleCallback) w.cancelIdleCallback(id);
      else window.clearTimeout(id);
    };
  }, [data]);

  const total = layout.records.length;
  const lastRing = layout.rings.length - 1;
  const ringOfYear = (y: number) => {
    const k = layout.rings.findIndex((r) => r.year === y);
    return k >= 0 ? k : y < (layout.rings[1]?.year ?? y) ? 0 : lastRing;
  };
  const initialPeriod = ((): [number, number] => {
    const m = /^(\d{4})-(\d{4})$/.exec(initial?.p ?? "");
    if (!m) return [0, lastRing];
    const a = ringOfYear(Number(m[1]));
    const b = ringOfYear(Number(m[2]));
    return [Math.min(a, b), Math.max(a, b)];
  })();

  /* Mutable render state — never React state. */
  const st = useRef({
    // Without a shared view in the URL, the disc starts at a random turn so the needle reads a
    // different person on each visit (author, 2026-10-05); a permalink keeps its own turn.
    rot: initial?.r ?? Math.random() * Math.PI * 2,
    vel: 0,
    zoom: 1,
    panX: 0,
    /** width the record sheet takes on the right; the disc yields it smoothly */
    sideW: 0,
    /** true while a zoomed focus wants the rim top framed near the top edge */
    framing: false,
    /** the viewer opened the layers on purpose (drag, keys, stage); only then is tilt shared */
    userTilt: false,
    /** the emphasised artist's venue links, recomputed when the artist changes */
    links: { artist: -1, chords: [] as number[], partners: [] as number[] },
    /** which legend row the pointer is on; the canvas lifts that kind of line */
    legendFocus: null as LegendKey,
    /** geometry of the last drawn frame (canvas px), for the pointer handlers */
    geo: { cx: 0, cy: 0, rimPx: 1, cosT: 1, rc: 1, rs: 0, diagram: 0 },
    /** screen box of the chosen name above the needle (click target for the record sheet) */
    nameBox: null as { x: number; y: number; w: number; h: number } | null,
    panY: 0,
    tilt: initial?.t ?? 0,
    spread: initial?.y ?? 0,
    /** the activity period shown, as ring indices (inclusive); the full span by default */
    yr0: initialPeriod[0],
    yr1: initialPeriod[1],
    /** fractional year steps accumulated while winding outside the rim */
    windAcc: 0,
    stage: 0,
    stageT: 0,
    partsArtist: -1,
    userZoomed: false,
    targetPan: null as { x: number; y: number } | null,
    targetRot: null as number | null,
    targetZoom: null as number | null,
    targetTilt: null as number | null,
    targetSpread: null as number | null,
    gesture: "none" as "none" | "rotate" | "roll" | "tilt" | "spread" | "dial",
    gx: 0,
    gy: 0,
    lastAngle: 0,
    lastX: 0,
    lastY: 0,
    lastT: 0,
    pointer: { x: -1, y: -1, inside: false, shift: false },
    pointers: new Map<number, { x: number; y: number }>(),
    pinchDist: 0,
    pinchZoom: 1,
    asmStart: 0,
    asmDone: !!initial || false,
    revealCount: 0,
    hover: null as Hover,
    focus: -1,
    reading: -1,
    lastReading: -1,
    needleOn: true,
    tickOn: false,
    music: null as Ensemble | null,
    notes: [] as SoundedNote[],
    versions: false,
    lastInput: 0,
    /** last pointer movement over the canvas (hover), which does not count as input for the idle spin */
    pointerMovedAt: 0,
    lastTurnAt: 0,
    sx: new Float32Array(layout.records.length),
    sy: new Float32Array(layout.records.length),
    kx: new Float32Array(layout.artists.length),
    ky: new Float32Array(layout.artists.length),
    px: new Float32Array(strata.sources.length),
    py: new Float32Array(strata.sources.length),
    ringLabel: [] as Array<{ k: number; x: number; y: number; w: number; h: number }>,
    theme: { paper: "#f1f2ef", ink: "#181a1c", accent: "#5b3fd6", dark: false },
    w: 0,
    h: 0,
    dpr: 1,
  }).current;

  const [hud, setHud] = useState({
    revealed: 0,
    date: "",
    done: false,
    hint: true,
    open: false,
    spread: false,
    yr0: 0,
    yr1: lastRing,
    tick: false,
    music: false,
    cell: "",
    versions: false,
    stage: 0,
    parts: -1,
    zoom: 1,
  });
  // the site menu fades in; its links only take clicks once they are fully shown
  const [menu, setMenu] = useState<"closed" | "opening" | "open">("closed");
  const menuTimer = useRef(0);
  const openMenu = useCallback((on: boolean) => {
    window.clearTimeout(menuTimer.current);
    if (!on) {
      setMenu("closed");
      return;
    }
    setMenu((m) => (m === "open" ? m : "opening"));
    menuTimer.current = window.setTimeout(() => setMenu("open"), 320);
  }, []);
  useEffect(() => () => window.clearTimeout(menuTimer.current), []);
  const [sheet, setSheet] = useState<{ artist: number; ord: number | null } | null>(null);
  const sheetRef = useRef(sheet);
  sheetRef.current = sheet;
  // the first frame of the sheet comes from data the page already holds
  const sheetPreview = useMemo<SheetPreview | null>(() => {
    if (!sheet) return null;
    const a = data.artists[sheet.artist]!;
    // this artist's rows come from their strand, not from a scan of every record in the ledger
    const acts = layout.artists[sheet.artist]!.strand.map((ri) => {
      const src = srcIndex(layout.records[ri]!);
      return { src, rec: data.records[src]! };
    })
      .sort((p, q) => q.rec.year - p.rec.year || p.src - q.src)
      .map(({ rec }) => rec);
    return {
      artist: {
        name_ko: a.name_ko,
        name_en: a.name_en,
        verification: a.verification,
        medium_tags: a.medium_tags,
      },
      activities: acts,
    };
    // only when a different artist is opened
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sheet?.artist, data, layout]);
  // when the click landed on a record: who else the archive shows at that same venue and year
  // (the chords are exactly those ties), so the card can say what the mark stands in the middle of
  /** the record node the reader clicked, or null when they clicked the artist itself */
  const pickedNode = useMemo<number | null>(() => {
    if (sheet?.ord == null) return null;
    const strand = layout.artists[sheet.artist]?.strand ?? [];
    const want = sheet.ord;
    return strand.find((k) => data.records[srcIndex(layout.records[k]!)]!.ord === want) ?? null;
  }, [sheet?.artist, sheet?.ord, layout, data.records]);
  const sheetPartners = useMemo<string[]>(() => {
    const ri = pickedNode;
    if (ri == null) return [];
    const names = new Set<string>();
    for (const c of layout.chords) {
      const other = c.a === ri ? c.b : c.b === ri ? c.a : -1;
      if (other < 0) continue;
      const ai = layout.records[other]!.artist;
      const a = data.artists[ai]!;
      names.add((lang === "ko" ? a.name_ko : a.name_en || a.name_ko) ?? a.name_ko);
    }
    return [...names];
  }, [pickedNode, layout, data, lang]);

  const [log, setLog] = useState<Msg[]>([]);
  const msgId = useRef(0);
  const say = useCallback((m: Omit<Msg, "id">) => {
    msgId.current += 1;
    const id = msgId.current;
    setLog((prev) => [...prev, { ...m, id }].slice(-3));
  }, []);

  const nameOf = useCallback(
    (i: number) => {
      const a = data.artists[i]!;
      return (lang === "ko" ? a.name_ko : a.name_en) || a.name_ko || a.name_en || a.id;
    },
    [data.artists, lang],
  );
  const linkStats = useMemo<LinkStats | null>(() => {
    if (!sheet) return null;
    const ai = sheet.artist;
    const a = layout.artists[ai]!;
    const years = a.strand.map((ri) => layout.records[ri]!.year);
    const dom = new Map<string, number>();
    for (const ri of a.strand) {
      const d = strata.sources[strata.sourceOf[ri]!]!.domain;
      dom.set(d, (dom.get(d) ?? 0) + 1);
    }
    const shared = new Map<number, number>();
    const venues = new Map<string, number>();
    let chordN = 0;
    for (const c of layout.chords) {
      const aa = layout.records[c.a]!.artist;
      const ab = layout.records[c.b]!.artist;
      if (aa !== ai && ab !== ai) continue;
      chordN++;
      const other = aa === ai ? ab : aa;
      shared.set(other, (shared.get(other) ?? 0) + 1);
      const v = data.records[srcIndex(layout.records[c.a]!)]!.venue ?? c.venue;
      venues.set(v, (venues.get(v) ?? 0) + 1);
    }
    const g = layout.groups[a.group]!;
    return {
      name: nameOf(ai),
      records: a.strand.length,
      y0: years.length ? Math.min(...years) : null,
      y1: years.length ? Math.max(...years) : null,
      frame: lang === "ko" ? g.label_ko : g.label_en,
      chords: chordN,
      partners: [...shared.entries()]
        .sort((p, q) => q[1] - p[1])
        .map(([i, n]) => ({ i, name: nameOf(i), shared: n })),
      venues: [...venues.entries()]
        .sort((p, q) => q[1] - p[1])
        .slice(0, 2)
        .map(([v]) => truncate(v, 22)),
      domains: [...dom.entries()].sort((p, q) => q[1] - p[1]).map(([d]) => d),
    };
  }, [sheet, layout, strata, data, nameOf, lang]);
  const onLegendFocus = useCallback(
    (k: LegendKey) => {
      st.legendFocus = k;
    },
    [st],
  );

  /* ---------------------------------------------------------------- */
  /* commands                                                          */
  /* ---------------------------------------------------------------- */
  const touch = useCallback(() => {
    st.lastInput = performance.now();
  }, [st]);
  const openStack = useCallback(
    (open: boolean) => {
      st.targetTilt = open ? 0.82 : 0;
      touch();
    },
    [st, touch],
  );
  const openYears = useCallback(
    (open: boolean) => {
      st.targetSpread = open ? 1 : 0;
      if (open && st.tilt < 0.45) st.targetTilt = 0.6;
      touch();
    },
    [st, touch],
  );
  const setStage = useCallback(
    (n: number) => {
      const k = clamp(Math.round(n), 0, STAGES);
      st.stage = k;
      st.userTilt = k > 0;
      st.targetTilt = k >= 2 ? 1.05 : k >= 1 ? 1.0 : 0;
      st.targetZoom = k >= 4 ? 0.7 : k >= 2 ? 0.8 : k >= 1 ? 0.92 : 1;
      st.targetPan = { x: 0, y: 0 };
      st.targetSpread = k >= 2 ? 1 : 0;
      st.userZoomed = false;
      touch();
    },
    [st, touch],
  );
  const reassemble = useCallback(() => {
    st.asmStart = performance.now();
    st.asmDone = false;
    st.focus = -1;
    st.targetRot = null;
    st.targetZoom = 1;
    st.targetPan = { x: 0, y: 0 };
    st.targetSpread = 0;
    st.yr0 = 0;
    st.yr1 = lastRing;
    st.stage = 0;
    touch();
  }, [st, lastRing, touch]);
  const [citeNotice, setCiteNotice] = useState<{ id: number; text: string } | null>(null);
  const copyView = useCallback(async () => {
    // The view is written into a link only here, when the reader asks to cite it. The address
    // bar is left alone while they turn the disc, so a reload always returns to the home view.
    const rings = layout.rings;
    const a = st.focus >= 0 ? layout.artists[st.focus]!.id : null;
    // a focus tilt is a camera courtesy, not a view the reader chose: share it only when opened on purpose
    const tShared = st.userTilt || st.stage > 0 ? st.tilt : 0;
    const yearOf = (i: number) => rings[i]!.year ?? (rings[1]?.year ?? 2017) - 1;
    const period = st.yr0 > 0 || st.yr1 < lastRing ? `&p=${yearOf(st.yr0)}-${yearOf(st.yr1)}` : "";
    const hash = `v=1&r=${st.rot.toFixed(3)}&t=${tShared.toFixed(2)}&y=${st.spread.toFixed(2)}${period}${a ? `&a=${a}` : ""}`;
    const url = `${window.location.origin}${window.location.pathname}${window.location.search}#${hash}`;
    let text: string;
    let notice: string;
    try {
      await copyText(url);
      const full = st.yr0 === 0 && st.yr1 === lastRing;
      const span = `${layout.rings[st.yr0]!.label}–${layout.rings[st.yr1]!.label}`;
      text = full
        ? t(
            "이 화면의 원장 주소를 복사했습니다. 그대로 인용할 수 있습니다.",
            "Copied the address of this view. It can be cited as is.",
          )
        : t(
            `이 화면의 원장 주소를 복사했습니다 (기간 ${span}, 기록 ${st.revealCount}건). 그대로 인용할 수 있습니다.`,
            `Copied the address of this view (period ${span}, ${st.revealCount} records). It can be cited as is.`,
          );
      notice = t("복사됨!", "Copied!");
    } catch {
      text = t(`주소: ${url}`, `Address: ${url}`);
      notice = text;
    }
    say({ role: "guide", text });
    setCiteNotice((prev) => ({ id: (prev?.id ?? 0) + 1, text: notice }));
  }, [st, lastRing, layout, say, t]);

  const focusArtist = useCallback(
    (i: number, announce = true) => {
      // same as a click: the strand turns to the needle and the record sheet opens; no zoom, no tilt
      const a = layout.artists[i]!;
      st.focus = i;
      st.targetRot = -Math.PI / 2 - a.angle;
      st.vel = 0;
      touch();
      setSheet({ artist: i, ord: null });
      if (!announce) return;
      const art = data.artists[i]!;
      const recs = a.strand.map((ri) => data.records[srcIndex(layout.records[ri]!)]!);
      const years = recs.map((r) => r.year);
      const span = years.length ? `${Math.min(...years)}–${Math.max(...years)}` : "";
      const frame = layout.groups[a.group]!;
      const domains = new Set(a.strand.map((ri) => strata.sources[strata.sourceOf[ri]!]!.domain));
      const ver = verLabel(art.verification);
      say({
        role: "guide",
        text: t(
          `${nameOf(i)} — 기록 ${recs.length}건${span ? ` (${span})` : ""}, 출처 ${domains.size}곳 위에 서 있습니다. ${frame.label_ko}. ${ver ? ver[0] : art.verification}.`,
          `${nameOf(i)} — ${recs.length} records${span ? ` (${span})` : ""}, standing on ${domains.size} source${domains.size === 1 ? "" : "s"}. ${frame.label_en}. ${ver ? ver[1] : art.verification}.`,
        ),
      });
    },
    [layout, strata, data, say, t, nameOf, st, touch],
  );

  /** Open the record sheet for an artist (already selected, or selected now). */
  const openSheet = useCallback(
    (i: number, ord: number | null = null) => {
      if (st.focus !== i) {
        const a = layout.artists[i]!;
        st.focus = i;
        st.targetRot = -Math.PI / 2 - a.angle;
        st.vel = 0;
      }
      touch();
      setSheet({ artist: i, ord });
    },
    [layout, st, touch],
  );
  const closeSheet = useCallback(() => {
    setSheet(null);
    st.focus = -1;
    if (!st.userZoomed && st.stage === 0) st.targetZoom = 1;
  }, [st]);

  useEffect(() => {
    if (initial?.a) {
      // a shared view: turn the strand to the needle and open its sheet, no zoom
      const i = data.artists.findIndex((a) => a.id === initial.a);
      if (i >= 0) openSheet(i);
    }
    // The shared view is restored; drop it from the address so a reload returns to the home view.
    if (initial) {
      try {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      } catch {
        /* ignore */
      }
    }
    // once, from the permalink
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const focusFrame = useCallback(
    (g: number) => {
      const grp = layout.groups[g]!;
      st.focus = -1;
      st.targetRot = -Math.PI / 2 - (grp.a0 + grp.a1) / 2;
      st.vel = 0;
      touch();
      setSheet(null);
    },
    [layout, st, touch],
  );

  const toggleMusic = useCallback(() => {
    st.lastInput = performance.now();
    if (st.music) {
      st.music.stop();
      st.music = null;
      say({ role: "guide", text: t("합주를 멈췄습니다.", "The ensemble rests.") });
      return;
    }
    try {
      const ens = new Ensemble(layout, (n) => {
        st.notes.push(n);
      });
      st.music = ens;
      st.tickOn = true;
      say({ role: "guide", text: t("악기를 준비합니다…", "Tuning up…") });
      ens
        .start(st.rot)
        .then(() => {
          if (st.music !== ens) return;
          const n = ens.conductor.voices.size || Math.min(10, layout.artists.length);
          say({
            role: "guide",
            text: t(
              `합주를 시작합니다. 바늘 근처 ${n}명의 작가가 연도 세포를 기록 수만큼 반복합니다. 원반을 돌리면 연주자가 바뀝니다.`,
              `The ensemble begins. ${n} artists near the needle repeat each year's cell once per record. Turn the disc to change who plays.`,
            ),
          });
        })
        .catch(() => {
          if (st.music === ens) {
            ens.stop();
            st.music = null;
          }
          say({
            role: "guide",
            text: t(
              "악기 샘플을 불러오지 못했습니다.",
              "The instrument samples could not be loaded.",
            ),
          });
        });
    } catch {
      say({
        role: "guide",
        text: t("이 브라우저에서는 소리를 낼 수 없습니다.", "This browser cannot play sound."),
      });
    }
  }, [layout, say, st, t]);

  /* ---------------------------------------------------------------- */
  /* canvas                                                            */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    // The frame's bitmap is snapshotted for the compositor after the draw. With the software
    // rasterizer that copy sat on the main thread (~40 ms) and made every assembly frame a long
    // task. Desynchronizing the paint cycle takes that copy off the task; the bitmap is the same.
    // Not desynchronized: with a 2x device pixel ratio a desynchronized canvas left headless Chrome
    // unable to produce a frame (screenshots timed out), a risk for high-DPI screens that is not worth
    // the main-thread time it saves.
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    // offscreen copy of the chord/strand/dot layers (see "Layer cache" in draw)
    const layer = document.createElement("canvas");
    const lctx = layer.getContext("2d")!;
    // reference state the cached layer was painted in (see "Layer cache" in draw)
    const lc = { sig: "", rot: 0, cx: 0, cy: 0, at: 0, s: 1, cosT: 1, rc: 1, rs: 0 };
    // opening: records that have landed are stamped into the same layer once each (see draw)
    const asm = {
      key: "",
      stamped: new Uint8Array(0),
      rot: 0,
      cx: 0,
      cy: 0,
      s: 1,
      cosT: 1,
      rc: 1,
      rs: 0,
    };

    const resize = () => {
      const r = wrap.getBoundingClientRect();
      st.w = Math.max(1, Math.floor(r.width));
      st.h = Math.max(1, Math.floor(r.height));
      st.dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.floor(st.w * st.dpr);
      canvas.height = Math.floor(st.h * st.dpr);
      canvas.style.width = `${st.w}px`;
      canvas.style.height = `${st.h}px`;
      st.theme = readTheme();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onTheme = () => {
      st.theme = readTheme();
    };
    mq.addEventListener?.("change", onTheme);
    const mo = new MutationObserver(onTheme);
    mo.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "data-theme"],
    });

    st.asmStart = performance.now();
    if (reduced) st.asmDone = true;

    const { records, artists, rings, groups, chords, revealOrder, rimR, R, unit } = layout;
    const { sources, sourceOf, plateR, depthSources, depthFrames } = strata;
    const recSrc = records.map((nd) => data.records[srcIndex(nd)]!);
    const nS = sources.length;
    let ringStep = R * 0.085; // distance between year plates when spread (grows in diagram mode)
    const font = (px: number, weight = 400) =>
      `${weight} ${px}px "Space Mono", "Nanum Gothic Coding", ui-monospace, monospace`;
    const fontKo = (px: number, weight = 400) =>
      `${weight} ${px}px "IBM Plex Sans KR", "Noto Sans KR", system-ui, sans-serif`;
    const letter = (v: string) => {
      (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = v;
    };

    // Paper grain: two tiles (ink on light paper, chalk on blueprint), tiled at low alpha.
    const makeGrain = (dark: boolean) => {
      const tile = document.createElement("canvas");
      tile.width = 240;
      tile.height = 240;
      const tc = tile.getContext("2d")!;
      const img = tc.createImageData(240, 240);
      const rnd = mulberry32(dark ? 11 : 7);
      for (let i = 0; i < img.data.length; i += 4) {
        const v = rnd();
        const a = v > 0.93 ? (v - 0.93) * 700 : 0; // sparse, fine fibres
        img.data[i] = dark ? 255 : 0;
        img.data[i + 1] = dark ? 255 : 0;
        img.data[i + 2] = dark ? 255 : 0;
        img.data[i + 3] = Math.min(255, a);
      }
      tc.putImageData(img, 0, 0);
      return ctx.createPattern(tile, "repeat");
    };
    const grainLight = makeGrain(false);
    const grainDark = makeGrain(true);
    const rx = new Float32Array(records.length);
    const ry = new Float32Array(records.length);
    const rz = new Float32Array(records.length);
    const ra = new Float32Array(records.length);
    const spx = new Float32Array(nS);
    const spy = new Float32Array(nS);
    const spz = new Float32Array(nS);
    const spa = new Float32Array(nS);
    // depth and perspective scale of each visible record / source this frame (P1, P2, P3)
    const rd = new Float32Array(records.length);
    const rf = new Float32Array(records.length);
    const sd = new Float32Array(nS);
    const ssf = new Float32Array(nS);
    const ringZ = new Float32Array(rings.length);
    // V1. Bundle slot = (ring, source, sector). The sector is the record's angle on its
    // plate, not the turned view, so the slot is fixed for the life of the layout.
    const nRings = rings.length;
    const nBundles = nRings * nS * BUNDLE_SECTORS;
    const bundleOf = new Int32Array(records.length);
    const sectorSpan = TAU / BUNDLE_SECTORS;
    for (let ri = 0; ri < records.length; ri++) {
      const nd = records[ri]!;
      let ang = nd.angle % TAU;
      if (ang < 0) ang += TAU;
      let sector = (ang / sectorSpan) | 0;
      if (sector < 0 || sector >= BUNDLE_SECTORS) sector = 0;
      bundleOf[ri] = (nd.ring * nS + sourceOf[ri]!) * BUNDLE_SECTORS + sector;
    }
    const bSumX = new Float32Array(nBundles);
    const bSumY = new Float32Array(nBundles);
    const bSumZ = new Float32Array(nBundles);
    const bCount = new Uint32Array(nBundles);
    const bundleUsed = new Int32Array(records.length);
    let nBundleUsed = 0;

    let raf = 0;
    let last = performance.now();
    let lastPaint = 0;
    // settled record positions, reused while spread, stage and the year window stay put
    let posHold = "";
    // last pointer/geometry the hit test ran for; a still pointer over a still disc is the same pick
    let hitHold = "";
    const draw = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const { w, h, dpr } = st;
      const ink = st.theme.ink;
      const paper = st.theme.paper;
      const accent = st.theme.accent;
      const T = (now - st.asmStart) / 1000;
      const assembling = !st.asmDone;

      /* -- motion -- */
      if (st.targetRot != null) {
        st.rot = dampAngle(st.rot, st.targetRot, 3.2, dt);
        if (Math.abs(shortAngle(st.targetRot - st.rot)) < 0.002) st.targetRot = null;
      } else if (st.gesture !== "rotate") {
        st.rot += st.vel * dt;
        st.vel *= Math.exp(-2.6 * dt);
        if (Math.abs(st.vel) < 1e-4) st.vel = 0;
        if (!reduced && st.focus < 0 && st.tilt < 0.05 && st.asmDone && now - st.lastInput > 4000)
          st.rot += 0.012 * dt;
      }
      st.music?.update(st.rot);
      if (st.targetZoom != null) {
        st.zoom = damp(st.zoom, st.targetZoom, 3.2, dt);
        if (Math.abs(st.targetZoom - st.zoom) < 0.003) st.targetZoom = null;
      }
      if (st.targetTilt != null && !assembling) {
        st.tilt = damp(st.tilt, st.targetTilt, 3.4, dt);
        if (Math.abs(st.targetTilt - st.tilt) < 0.003) st.targetTilt = null;
      }
      if (st.targetSpread != null) {
        st.spread = damp(st.spread, st.targetSpread, 3, dt);
        if (Math.abs(st.targetSpread - st.spread) < 0.004) st.targetSpread = null;
      }
      st.stageT = damp(st.stageT, st.stage, 2.4, dt);

      // Quiet frames: after 4 s without input the disc only turns by itself (0.7 degrees a second)
      // while every record is redrawn, which kept the page busy at full frame rate. With no input,
      // no pointer movement, no camera target in flight and no sounded record, paint at most 12
      // times a second; motion state above still advances every frame, so nothing jumps.
      const quiet =
        st.asmDone &&
        st.gesture === "none" &&
        now - st.lastInput > 4000 &&
        now - st.pointerMovedAt > 600 &&
        st.targetRot == null &&
        st.targetZoom == null &&
        st.targetTilt == null &&
        st.targetSpread == null &&
        st.vel === 0 &&
        Math.abs(st.stageT - st.stage) < 1e-3 &&
        st.notes.length === 0;
      if (quiet && now - lastPaint < 1000 / 12) {
        raf = requestAnimationFrame(draw);
        return;
      }
      lastPaint = now;
      const diagram = smooth(0, 1, st.stageT); // exploded-diagram look: diagonal axis
      const subSplit = smooth(2, 3, st.stageT); // year plates split into frame segments
      const partsOut = smooth(3, 4, st.stageT); // one artist's records pulled out as numbered parts
      const horiz = smooth(1, 2, st.stageT); // stages 2+: the shaft lies fully horizontal
      const screenRot = -0.42 * diagram - (Math.PI / 2 - 0.42) * horiz;
      const rc = Math.cos(screenRot);
      const rs = Math.sin(screenRot);
      let tilt = st.tilt;
      let e = clamp((tilt - 0.08) / 0.8, 0, 1) * (1 - 0.5 * partsOut);
      if (assembling) {
        const closeU = easeInOut((T - A_CLOSE[0]) / (A_CLOSE[1] - A_CLOSE[0]));
        tilt = lerp(lerp(0.92, 0.8, easeOutCubic(T / 1.5)), 0, closeU);
        e = 1 - closeU;
        st.tilt = tilt;
        if (T >= A_CLOSE[1]) {
          st.asmDone = true;
          st.tilt = 0;
          st.yr0 = 0;
          st.yr1 = lastRing;
        }
      }
      const spread = st.spread;
      const cosT = Math.cos(tilt);
      const sinT = Math.sin(tilt);
      ringStep = R * (0.085 + 0.1 * diagram + 0.09 * horiz);
      for (let k = 0; k < rings.length; k++) ringZ[k] = spread * k * ringStep;
      const zTop = ringZ[rings.length - 1]!;
      const gStep = ringStep * 0.22; // extra axis offset per frame group when split
      const gz = (group: number) => subSplit * group * gStep;
      const maxGz = gz(groups.length - 1);

      /* -- geometry -- */
      let partsArtist = -1;
      if (partsOut > 0.001) {
        partsArtist = st.focus >= 0 ? st.focus : st.reading >= 0 ? st.reading : -1;
        if (partsArtist < 0) {
          let bestN = -1;
          for (let i = 0; i < artists.length; i++)
            if (artists[i]!.strand.length > bestN) {
              bestN = artists[i]!.strand.length;
              partsArtist = i;
            }
        }
      }
      const fit = (Math.min(w, h - 150) * 0.5 * 0.94) / (rimR + 46);
      const panelW = w >= 768 && st.stage >= 1 ? 300 : 0;
      /* the parts tray: the chosen artist's records sorted into year bins, in the free space
         left of the structure; the structure fits what remains on the right */
      const tray = new Map<number, [number, number]>();
      const trayYears: Array<{ year: number; x: number; w: number }> = [];
      let trayLeft = 0;
      let trayTop = 0;
      let trayW = 0;
      let trayH = 0;
      let trayCell = 22;
      if (partsArtist >= 0 && partsOut > 0.001) {
        const order = artists[partsArtist]!.strand.slice().sort(
          (p, q) => records[p]!.year - records[q]!.year || p - q,
        );
        const bins: Array<{ year: number; list: number[] }> = [];
        for (const ri of order) {
          const y = records[ri]!.year;
          const last = bins[bins.length - 1];
          if (last && last.year === y) last.list.push(ri);
          else bins.push({ year: y, list: [ri] });
        }
        trayLeft = (panelW > 0 ? 316 : 26) + 30;
        const HEAD = 34;
        const GAP = 14;
        const availH = h - 96 - 150 - HEAD;
        const maxW = Math.max(180, w * 0.52 - trayLeft);
        const widthAt = (cell: number) => {
          const rows = Math.max(1, Math.floor(availH / cell));
          return (
            bins.reduce((sum, b) => sum + Math.ceil(b.list.length / rows) * cell, 0) +
            GAP * (bins.length - 1)
          );
        };
        trayCell = 22;
        if (widthAt(trayCell) > maxW)
          trayCell = Math.max(16, trayCell * (maxW / widthAt(trayCell)));
        const rows = Math.max(1, Math.floor(availH / trayCell));
        const maxN = Math.max(...bins.map((b) => b.list.length));
        trayH = HEAD + Math.min(maxN, rows) * trayCell;
        trayW = widthAt(trayCell);
        trayTop = Math.max(96, Math.round((h - 96) / 2 + 58 - trayH / 2));
        let xCur = trayLeft;
        for (const b of bins) {
          const cols = Math.ceil(b.list.length / rows);
          b.list.forEach((ri, j) => {
            const c = Math.floor(j / rows);
            const r = j % rows;
            tray.set(ri, [
              xCur + c * trayCell + trayCell / 2,
              trayTop + HEAD + r * trayCell + trayCell / 2,
            ]);
          });
          trayYears.push({ year: b.year, x: xCur, w: cols * trayCell });
          xCur += cols * trayCell + GAP;
        }
      }
      const regionLeft = trayLeft + trayW + 56;
      const regionRight = w - 40;
      // The structure keeps its size; the record sheet takes what is left.
      // Upright stages: the sheet stands on the right of the disc. Horizontal stages: it lies
      // along the bottom, under the diagram. Only when the sheet is at its minimum does the
      // structure give way.
      const sheetOpen = w >= 640 && !!sheetRef.current;
      const SHEET_MIN_W = 300;
      const SHEET_MAX_W = 51 * 16;
      let sideTarget = 0;
      if (horiz > 0.5 && !st.userZoomed && st.asmDone) {
        const zTopW = spread * (rings.length - 1) * ringStep;
        const axisLen = zTopW + maxGz + e * depthFrames;
        const need = fit * (axisLen * sinT + 2.3 * rimR * cosT);
        // lying flat, the sheet is a small floating window and takes no room from the diagram;
        // with the parts tray out, the structure fits the field to the right of the tray
        const avail = lerp(
          w - panelW - 90,
          regionRight - regionLeft - 20,
          tray.size ? partsOut : 0,
        );
        st.targetZoom = clamp(avail / Math.max(1, need), 0.35, 1.0);
      } else if (sheetOpen && horiz < 0.5 && st.asmDone) {
        const zNat = st.userZoomed ? st.zoom : st.stage >= 1 ? 0.92 : 1;
        // rim plus its labels; the layered view also carries its callouts out to the right
        const discK = st.stage >= 1 ? 3.1 : 2.6;
        const discW = discK * rimR * fit * zNat;
        const room = w - discW - 60;
        if (room >= SHEET_MIN_W) {
          sideTarget = Math.min(SHEET_MAX_W, room) + 26;
          if (!st.userZoomed && Math.abs(st.zoom - zNat) > 0.01) st.targetZoom = zNat;
        } else {
          sideTarget = SHEET_MIN_W + 26;
          if (!st.userZoomed)
            st.targetZoom = clamp(
              (w - SHEET_MIN_W - 86) / Math.max(1, discK * rimR * fit),
              0.45,
              zNat,
            );
        }
      }
      st.sideW = damp(st.sideW, sideTarget, 4, dt);
      if (Math.abs(st.sideW - sideTarget) < 0.5) st.sideW = sideTarget;
      // the side sheet is laid out once at its final width and slides in with the disc;
      // written straight onto the element each frame, so it never waits for React
      const sheetEl = wrapRef.current?.querySelector<HTMLElement>('[data-sheet="side"]');
      if (sheetEl) {
        if (w >= 640 && sideTarget > 0) {
          const tgt = Math.round(sideTarget - 26);
          const wpx = `${tgt}px`;
          if (sheetEl.style.width !== wpx) sheetEl.style.width = wpx;
          const off = Math.max(0, tgt + 26 - st.sideW);
          const tf = off < 0.5 ? "none" : `translate3d(${off.toFixed(1)}px, 0, 0)`;
          if (sheetEl.style.transform !== tf) sheetEl.style.transform = tf;
        } else if (w < 640) {
          if (sheetEl.style.width) sheetEl.style.width = "";
          if (sheetEl.style.transform !== "none") sheetEl.style.transform = "none";
        }
      }
      // before the first layout the canvas has no size and fit goes negative
      const s = Math.max(1e-3, fit * st.zoom);
      const baseCy = (h - 96) / 2 + 58;
      if (
        st.framing &&
        st.focus >= 0 &&
        (st.targetRot != null || st.targetZoom != null || st.targetTilt != null)
      ) {
        st.targetPan = { x: 0, y: 84 + rimR * cosT * s - baseCy };
      } else st.framing = false;
      if (st.targetPan) {
        st.panX = damp(st.panX, st.targetPan.x, 3.2, dt);
        st.panY = damp(st.panY, st.targetPan.y, 3.2, dt);
        if (Math.abs(st.panX - st.targetPan.x) < 0.3 && Math.abs(st.panY - st.targetPan.y) < 0.3)
          st.targetPan = null;
      }
      const zSrc = -e * depthSources;
      const zFrm = -e * depthFrames;
      // Flat: the disc sits at the base centre. Diagram: the axis midpoint sits there instead.
      const zMid = (zFrm + zTop + maxGz) / 2;
      const pyMid = -zMid * sinT * s;
      const flatCy = baseCy - e * depthFrames * sinT * s * 0.32 + spread * zTop * sinT * s * 0.45;
      const trayShift = tray.size
        ? partsOut * ((regionLeft + regionRight) / 2 - (w / 2 + horiz * (panelW / 2)))
        : 0;
      const cx =
        w / 2 + st.panX + diagram * (pyMid * rs) + horiz * (panelW / 2) - st.sideW / 2 + trayShift;
      const cy = lerp(flatCy, baseCy - pyMid * rc, diagram) + st.panY;
      st.geo = { cx, cy, rimPx: rimR * s, cosT, rc, rs, diagram };
      const cr = Math.cos(st.rot);
      const sr = Math.sin(st.rot);
      // P1. Perspective. The painter draws the frame plate first and the record disc last; on
      // the stage-2 shaft that later end is the high-z mouth, so d = Y·sinT + z·cosT grows
      // toward the viewer. The camera stands CAM rim radii in front of the nearest part of the
      // structure (dNear, below). Screen offsets from (cx, cy) scale
      // by f = 1 / (1 + w·(dNear − d) / (CAM·rimR)), w = diagram: the near end keeps about the size
      // the layout fitted, farther parts shrink, so the structure does not outgrow the fitted frame
      // (an earlier version scaled about d = 0 and pushed the near mouth off the screen).
      // w ≤ 0.001 (stage 0 and the opening assembly) leaves f = 1, the orthographic picture.
      const CAM = 4;
      const camR = CAM * rimR;
      // dNear is the depth of the near mouth's centre: that plate keeps its fitted size on
      // average (its front rim grows a little, its back rim shrinks), so the picture keeps the
      // height the layout fitted. f is bounded to [0.3, 1.35] so nothing nears the camera.
      const dNear = (zTop + maxGz) * cosT;
      const scaleOf = (d: number) => {
        if (diagram <= 0.001) return 1;
        const f = 1 / (1 + (diagram * (dNear - d)) / camR);
        return f > 1.35 || f < 0 ? 1.35 : f < 0.3 ? 0.3 : f;
      };
      const projectXY = (X: number, Y: number, z: number): [number, number] => {
        const px = X * s;
        const py = (Y * cosT - z * sinT) * s;
        let ox = px * rc - py * rs;
        let oy = px * rs + py * rc;
        if (diagram > 0.001) {
          const f = scaleOf(Y * sinT + z * cosT);
          ox *= f;
          oy *= f;
        }
        return [cx + ox, cy + oy];
      };
      const proj = (x: number, y: number, z: number): [number, number] => {
        const X = x * cr - y * sr;
        const Y = x * sr + y * cr;
        return projectXY(X, Y, z);
      };
      const depthOf = (x: number, y: number, z: number) => {
        const Y = x * sr + y * cr;
        return Y * sinT + z * cosT;
      };
      const centerAt = (z: number): [number, number] => {
        const py = -z * sinT * s;
        return [cx - py * rs, cy + py * rc];
      };
      // P2. A native ellipse is orthographic, so once the diagram is open a plate, ring or
      // segment is a polyline through the same projection as everything else: 48 segments on
      // a full turn, and the same spacing on an arc. At w ≤ 0.001 the two drawings match and
      // the native ellipse is cheaper. Angles here are world angles (callers add st.rot);
      // projectXY does not rotate them again.
      const ell = (r: number, z: number, a0 = 0, a1 = TAU) => {
        if (diagram <= 0.001) {
          const [ex, ey] = centerAt(z);
          ctx.ellipse(ex, ey, r * s + 0.001, r * s * cosT, screenRot, a0, a1);
          return;
        }
        let span = a1 - a0;
        if (span < 0) span += TAU;
        const n = Math.max(1, Math.round((48 * span) / TAU));
        for (let i = 0; i <= n; i++) {
          const a = a0 + (span * i) / n;
          const [ex, ey] = projectXY(Math.cos(a) * r, Math.sin(a) * r, z);
          if (i === 0) ctx.moveTo(ex, ey);
          else ctx.lineTo(ex, ey);
        }
      };

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = paper;
      ctx.fillRect(0, 0, w, h);
      /* -- the sheet: grain, ledger rules, vignette, frame -- */
      const grain = st.theme.dark ? grainDark : grainLight;
      if (grain) {
        ctx.globalAlpha = st.theme.dark ? 0.1 : 0.09;
        ctx.fillStyle = grain;
        ctx.fillRect(0, 0, w, h);
        ctx.globalAlpha = 1;
      }
      {
        // 5 mm ledger rules; the disc is a plate laid on the sheet, so no rules show through it.
        const step = 19;
        ctx.lineWidth = 1;
        ctx.strokeStyle = withAlpha(ink, st.theme.dark ? 0.07 : 0.045);
        ctx.beginPath();
        for (let y = 18.5 + (step - (18.5 % step)); y < h - 18; y += step) {
          ctx.moveTo(18, y);
          ctx.lineTo(w - 18, y);
        }
        ctx.stroke();
        if (diagram < 0.98) {
          ctx.beginPath();
          ell(rimR + 14, zTop);
          ctx.fillStyle = withAlpha(paper, 1 - diagram);
          ctx.fill();
        }
        const vg = ctx.createRadialGradient(
          w / 2,
          h / 2,
          Math.min(w, h) * 0.35,
          w / 2,
          h / 2,
          Math.hypot(w, h) * 0.6,
        );
        vg.addColorStop(0, withAlpha(ink, 0));
        vg.addColorStop(1, withAlpha(ink, st.theme.dark ? 0.18 : 0.055));
        ctx.fillStyle = vg;
        ctx.fillRect(0, 0, w, h);
        // sheet frame + registration marks
        ctx.strokeStyle = withAlpha(ink, 0.3);
        ctx.strokeRect(16.5, 16.5, w - 33, h - 33);
        const m = 7;
        for (const [mx, my, sx2, sy2] of [
          [16.5, 16.5, 1, 1],
          [w - 16.5, 16.5, -1, 1],
          [16.5, h - 16.5, 1, -1],
          [w - 16.5, h - 16.5, -1, -1],
        ] as const) {
          ctx.beginPath();
          ctx.moveTo(mx - sx2 * m, my);
          ctx.lineTo(mx - sx2 * 1, my);
          ctx.moveTo(mx, my - sy2 * m);
          ctx.lineTo(mx, my - sy2 * 1);
          ctx.strokeStyle = withAlpha(ink, 0.5);
          ctx.stroke();
        }
      }
      ctx.lineCap = "round";
      ctx.lineJoin = "round";

      /* -- animated positions -- */
      for (let i = 0; i < nS; i++) {
        const sn = sources[i]!;
        let z = zSrc;
        let a = e;
        let x = sn.x;
        let y = sn.y;
        if (assembling) {
          const d0 = A_SOURCES[0] + (A_SOURCES[1] - A_SOURCES[0] - 0.7) * (i / Math.max(1, nS - 1));
          const u = easeOutBack((T - d0) / 0.7);
          const uu = clamp((T - d0) / 0.7, 0, 1);
          z = lerp(-depthSources - R * 1.6, zSrc, u);
          x = sn.x * lerp(1.45, 1, u);
          y = sn.y * lerp(1.45, 1, u);
          a = uu * e;
        }
        spx[i] = x;
        spy[i] = y;
        spz[i] = z;
        spa[i] = a;
      }
      st.partsArtist = partsArtist;
      // Once assembly is over, a record's place is fixed by the spread, the stage and the year
      // window. Recomputing sixty thousand of them on a frame that changed none of those is the
      // same picture as last time, so the arrays are kept.
      const posHoldKey = assembling ? "" : `${st.yr0}|${st.yr1}|${spread}|${subSplit}`;
      let revealCount = st.revealCount;
      if (!(posHoldKey && posHoldKey === posHold)) {
      revealCount = 0;
      for (let k = 0; k < records.length; k++) {
        const ri = revealOrder[k]!;
        const nd = records[ri]!;
        let x = nd.x;
        let y = nd.y;
        let z = ringZ[nd.ring]! + gz(artists[nd.artist]!.group);
        let a = 1;
        if (assembling) {
          const t0 =
            A_RECORDS[0] +
            (A_RECORDS[1] - A_RECORDS[0] - RECORD_FLIGHT) * (k / Math.max(1, records.length - 1));
          const u = (T - t0) / RECORD_FLIGHT;
          if (u <= 0) {
            ra[ri] = 0;
            continue;
          }
          // Flight over: the eased position is exactly the record's own place. Skip the
          // trigonometry; the picture matches the branch below at u = 1.
          if (u >= 1) {
            rx[ri] = nd.x;
            ry[ri] = nd.y;
            rz[ri] = 0;
            ra[ri] = 1;
            revealCount++;
            continue;
          }
          const si = sourceOf[ri]!;
          const ux = easeOutCubic(u);
          const uz = easeOutBack(u);
          const swirl = (1 - ux) * (1 - ux) * 0.22 * (k % 2 === 0 ? 1 : -1);
          const ca = Math.cos(swirl);
          const sa = Math.sin(swirl);
          const tx = nd.x * ca - nd.y * sa;
          const ty = nd.x * sa + nd.y * ca;
          x = lerp(spx[si]!, tx, ux);
          y = lerp(spy[si]!, ty, ux);
          z = lerp(spz[si]!, 0, uz);
          a = clamp(u * 2, 0, 1);
        } else if (nd.ring < st.yr0 || nd.ring > st.yr1) {
          ra[ri] = 0;
          continue;
        }
        revealCount++;
        rx[ri] = x;
        ry[ri] = y;
        rz[ri] = z;
        ra[ri] = a;
      }
      st.revealCount = revealCount;
      posHold = posHoldKey;
      }

      /* -- the needle reads whoever sits at twelve o'clock -- */
      let reading = -1;
      if (st.needleOn && st.asmDone) {
        let best = unit * 0.55;
        for (let i = 0; i < artists.length; i++) {
          const d = Math.abs(shortAngle(artists[i]!.angle + st.rot + Math.PI / 2));
          if (d < best) {
            best = d;
            reading = i;
          }
        }
      }
      st.reading = reading;
      if (reading !== st.lastReading) {
        if (reading >= 0 && st.tickOn && st.lastReading >= 0) {
          const a = artists[reading]!;
          const yr = a.strand.length ? Math.min(...a.strand.map((ri) => records[ri]!.year)) : 2020;
          playTick(520 + (clamp(yr, 2011, 2026) - 2011) * 42, 0.03);
        }
        st.lastReading = reading;
      }

      /* -- emphasis -- */
      const hov = st.hover;
      const emphasisArtist =
        hov?.kind === "artist"
          ? hov.idx
          : hov?.kind === "record"
            ? records[hov.idx]!.artist
            : st.focus;
      const emphasisSource = hov?.kind === "source" ? hov.idx : -1;
      const lensRing = hov?.kind === "ring" ? hov.idx : -1;
      const emphasised = emphasisArtist >= 0 || emphasisSource >= 0;
      const isEmphRec = (ri: number) =>
        (emphasisArtist >= 0 && records[ri]!.artist === emphasisArtist) ||
        (emphasisSource >= 0 && sourceOf[ri] === emphasisSource);
      const isEmphArtist = (ai: number) => emphasisArtist === ai;
      if (st.links.artist !== emphasisArtist) {
        const mine: number[] = [];
        const count = new Map<number, number>();
        if (emphasisArtist >= 0) {
          chords.forEach((c, ci) => {
            const aa = records[c.a]!.artist;
            const ab = records[c.b]!.artist;
            if (aa !== emphasisArtist && ab !== emphasisArtist) return;
            mine.push(ci);
            const other = aa === emphasisArtist ? ab : aa;
            count.set(other, (count.get(other) ?? 0) + 1);
          });
        }
        st.links = {
          artist: emphasisArtist,
          chords: mine,
          partners: [...count.entries()].sort((p, q) => q[1] - p[1]).map((e) => e[0]),
        };
      }
      const chordHov = hov?.kind === "chord" ? hov.idx : -1;
      const liftChords = st.legendFocus === "chords" || chordHov >= 0;
      const lensA = (ring: number) => (lensRing < 0 ? 1 : ring === lensRing ? 1 : 0.12);

      /* ============================ layer: frames ============================ */
      const plateU = assembling ? easeOutBack((T - A_PLATES[0]) / (A_PLATES[1] - A_PLATES[0])) : 1;
      const plateA = (assembling ? clamp((T - A_PLATES[0]) / 0.5, 0, 1) : 1) * e;
      const zPlate = assembling ? lerp(-depthFrames - R * 2.2, zFrm, plateU) : zFrm;
      // P3. Depth cue. Far parts recede and near parts stay legible, by one rule: fog(d)
      // fades from 1 at the nearest visible depth to FAR at the farthest, then weighted by
      // w so stage 0 is unchanged. The range is the structure drawn this frame (plates,
      // piers, rings, the rim). Text, callouts, the parts list, the info card and accent
      // marks pass emph and are not fogged — no per-layer hand tuning.
      let dLo = Infinity;
      let dHi = -Infinity;
      const accD = (d: number) => {
        if (d < dLo) dLo = d;
        if (d > dHi) dHi = d;
      };
      const accDisc = (r: number, z: number) => {
        const c = z * cosT;
        const ext = Math.abs(r * sinT);
        accD(c + ext);
        accD(c - ext);
      };
      if (plateA > 0.01) accDisc(rimR * plateR, zPlate);
      for (let i = 0; i < nS; i++) {
        if (spa[i]! > 0.01) accD(depthOf(spx[i]!, spy[i]!, spz[i]!));
      }
      for (let k = 0; k < rings.length; k++) {
        accDisc(rings[k]!.r, ringZ[k]!);
        if (subSplit > 0.01) {
          for (let gi = 0; gi < groups.length; gi++) accDisc(rings[k]!.r, ringZ[k]! + gz(gi));
        }
      }
      for (let gi = 0; gi < groups.length; gi++) accDisc(rimR + 24, zTop + gz(gi));
      const dSpan = dHi - dLo;
      const FAR = 0.35;
      const fogAt = (d: number, emph: boolean) => {
        if (emph || diagram <= 0.001 || !(dSpan > 1e-4)) return 1;
        const t = clamp((dHi - d) / dSpan, 0, 1);
        return lerp(1, lerp(1, FAR, t), diagram);
      };
      if (plateA > 0.01) {
        const pr = rimR * plateR;
        groups.forEach((g, gi) => {
          const emph = emphasisArtist >= 0 && artists[emphasisArtist]!.group === gi;
          const midA = (g.a0 + g.a1) / 2 + st.rot;
          const plateFog = fogAt(depthOf(Math.cos(midA) * pr, Math.sin(midA) * pr, zPlate), emph);
          ctx.beginPath();
          ell(pr, zPlate, g.a0 + st.rot, g.a1 + st.rot);
          ctx.lineWidth = 7;
          ctx.strokeStyle = withAlpha(
            emph ? accent : ink,
            plateA * (emph ? 0.22 : 0.09) * plateFog,
          );
          ctx.stroke();
          ctx.lineWidth = 1;
          ctx.strokeStyle = withAlpha(emph ? accent : ink, plateA * (emph ? 0.6 : 0.3) * plateFog);
          ctx.stroke();
          const [lx, ly] = proj(Math.cos(midA) * (pr + 16), Math.sin(midA) * (pr + 16), zPlate);
          ctx.font = font(9.5, 700);
          letter("0.12em");
          const plateLabel = (lang === "ko" ? g.label_ko : g.label_en).toUpperCase();
          ctx.textAlign = Math.cos(midA) < 0 ? "right" : "left";
          ctx.textBaseline = "middle";
          ctx.fillStyle = withAlpha(ink, plateA * 0.6);
          const tw = measure(ctx, plateLabel);
          const lxc = ctx.textAlign === "left" ? Math.min(lx, w - 16 - tw) : Math.max(lx, 16 + tw);
          ctx.fillText(plateLabel, lxc, ly);
          letter("0em");
        });
        ctx.lineWidth = 0.8;
        // V3. Per-artist lines from the top plate to the frame plate. N is how many of
        // those lines are drawn this frame; stages 2–4 scale their background alpha by
        // min(1, K/sqrt(N)) so a few thousand strands stay a veil. `horiz` blends that
        // factor in, so stages 0–1 (horiz = 0) keep the previous alpha. An emphasised
        // artist keeps the accent and is left out of the veil.
        let artistScale = 1;
        if (horiz > 0) {
          let artistN = 0;
          for (let i = 0; i < artists.length; i++) {
            if (!artists[i]!.strand.some((ri) => ra[ri]! > 0)) continue;
            artistN++;
          }
          if (artistN > 0) {
            const den = Math.min(1, ARTIST_LINE_DENSITY_K / Math.sqrt(artistN));
            artistScale = 1 - horiz + horiz * den;
          }
        }
        // Flat and unemphasised, every line is the same ink, so lane batching (one stroke per
        // lane) stacks crossings the same way as a stroke per artist. An open diagram fogs each
        // line on its own, and an emphasised artist is an accent, so those stay one stroke each.
        const batchArtistLines = !emphasised && diagram <= 0.001;
        const artistLines: Batch = new Map();
        const artistInk = batchArtistLines
          ? withAlpha(ink, qa(plateA * 0.1 * artistScale))
          : "";
        for (let i = 0; i < artists.length; i++) {
          const a = artists[i]!;
          if (!a.strand.some((ri) => ra[ri]! > 0)) continue;
          const emph = isEmphArtist(i);
          const [x0, y0] = proj(a.x, a.y, zTop + gz(a.group));
          const [x1, y1] = proj(Math.cos(a.angle) * pr, Math.sin(a.angle) * pr, zPlate);
          if (batchArtistLines && !emph) {
            const p = pathIn(artistLines, strokeKey(0.8, artistInk, i % LANES));
            p.moveTo(x0, y0);
            p.lineTo(x1, y1);
            continue;
          }
          const lineFog = fogAt(
            (depthOf(a.x, a.y, zTop + gz(a.group)) +
              depthOf(Math.cos(a.angle) * pr, Math.sin(a.angle) * pr, zPlate)) *
              0.5,
            emph,
          );
          ctx.strokeStyle = withAlpha(
            emph ? accent : ink,
            qa(plateA * (emph ? 0.7 : (emphasised ? 0.03 : 0.1) * artistScale) * lineFog),
          );
          ctx.beginPath();
          ctx.moveTo(x0, y0);
          ctx.lineTo(x1, y1);
          ctx.stroke();
        }
        if (batchArtistLines) strokeBatch(ctx, artistLines);
      }

      /* ============================ layer: sources ============================ */
      for (let i = 0; i < nS; i++) {
        const d = depthOf(spx[i]!, spy[i]!, spz[i]!);
        sd[i] = d;
        ssf[i] = scaleOf(d);
        const [x, y] = proj(spx[i]!, spy[i]!, spz[i]!);
        st.px[i] = x;
        st.py[i] = y;
      }
      if (e > 0.01 || assembling) {
        const base: Batch = new Map();
        const top: Batch = new Map();
        // Budget: these faint lines only read as a veil where they bundle towards a source, so past
        // SOURCE_LINE_BUDGET only every `stride`-th background line is drawn, each darkened to stand
        // for the ones it replaces (1-(1-a)^stride: the same ink where `stride` lines would overlap).
        // The pick is by record index, so it does not flicker. Emphasised lines are always drawn.
        // while the reader is turning or tilting the disc, thin the veil further: it is read as a
        // density, and the full set is drawn again as soon as the motion stops
        const moving =
          st.gesture !== "none" ||
          Math.abs(st.vel) > 0.02 ||
          st.targetTilt != null ||
          st.targetZoom != null ||
          st.targetRot != null;
        const budget = moving ? SOURCE_LINE_BUDGET_MOVING : SOURCE_LINE_BUDGET;
        const stride = Math.max(1, Math.ceil(revealCount / budget));
        // V4. Per-record threads fade out with (1 − horiz) and bundled threads fade in with
        // horiz, so stages 0–1 are unchanged and the step into stage 2 is a crossfade.
        // Emphasised records (focused artist, hovered source) keep their own accent lines
        // on top in every stage, including while the plates are apart.
        for (let ri = 0; ri < records.length; ri++) {
          if (ra[ri]! <= 0) continue;
          const si = sourceOf[ri]!;
          const emph = isEmphRec(ri);
          if (!emph && horiz > 0.999) continue;
          if (!emph && stride > 1 && ri % stride !== 0) continue;
          const flying = assembling && rz[ri]! < -0.5;
          let alpha =
            (emph ? 0.55 : emphasised ? 0.015 : 0.055) *
            Math.max(e, flying ? 0.6 : 0) *
            spa[si]! *
            lensA(records[ri]!.ring);
          if (!emph && stride > 1) alpha = 1 - Math.pow(1 - alpha, stride);
          if (!emph) alpha *= 1 - horiz;
          if (alpha < 0.005) continue;
          alpha *= fogAt((depthOf(rx[ri]!, ry[ri]!, rz[ri]!) + sd[si]!) * 0.5, emph);
          const [x0, y0] = proj(rx[ri]!, ry[ri]!, rz[ri]!);
          const p = pathIn(
            emph ? top : base,
            strokeKey(0.7, withAlpha(emph ? accent : ink, qa(alpha)), ri % LANES),
          );
          p.moveTo(x0, y0);
          p.lineTo(st.px[si]!, st.py[si]!);
        }
        // V1. While the plates are apart, background threads are one line per non-empty
        // bundle, from the mean current position of its visible members to the pier.
        // Sums live in typed arrays; the hot loop does not allocate. Reveal, the year
        // lens, source alpha, emphasis dimming, assembly flight and the moving/static
        // budget are the same gates the per-record loop uses. Emphasised records are
        // left out of the bundle: their accent lines are already drawn above.
        if (horiz > 0.001) {
          nBundleUsed = 0;
          for (let ri = 0; ri < records.length; ri++) {
            if (ra[ri]! <= 0) continue;
            if (isEmphRec(ri)) continue;
            const bi = bundleOf[ri]!;
            if (bCount[bi] === 0) bundleUsed[nBundleUsed++] = bi;
            bCount[bi] = bCount[bi]! + 1;
            bSumX[bi] = bSumX[bi]! + rx[ri]!;
            bSumY[bi] = bSumY[bi]! + ry[ri]!;
            bSumZ[bi] = bSumZ[bi]! + rz[ri]!;
          }
          let nMax = 1;
          for (let u = 0; u < nBundleUsed; u++) {
            const n = bCount[bundleUsed[u]!]!;
            if (n > nMax) nMax = n;
          }
          // Bundles are already few (plates × sources × 16 sectors, ~5k): they take the static
          // budget even while moving. The lying-shaft stages re-aim the zoom every frame, so the
          // moving budget would otherwise halve the bundles for as long as the stage is open.
          const strideB = Math.max(1, Math.ceil(nBundleUsed / SOURCE_LINE_BUDGET));
          const nDrawn = strideB <= 1 ? nBundleUsed : Math.ceil(nBundleUsed / strideB);
          // V3. Same rule as the artist lines, with the bundle K. N is the lines this
          // frame will stroke (after the budget stride), so the veil tracks data size.
          const density = nDrawn > 0 ? Math.min(1, BUNDLE_DENSITY_K / Math.sqrt(nDrawn)) : 1;
          const logDen = Math.log(1 + nMax);
          for (let u = 0; u < nBundleUsed; u++) {
            const bi = bundleUsed[u]!;
            const n = bCount[bi]!;
            const sx = bSumX[bi]!;
            const sy = bSumY[bi]!;
            const sz = bSumZ[bi]!;
            bCount[bi] = 0;
            bSumX[bi] = 0;
            bSumY[bi] = 0;
            bSumZ[bi] = 0;
            if (strideB > 1 && u % strideB !== 0) continue;
            const packed = (bi / BUNDLE_SECTORS) | 0;
            const si = packed % nS;
            const ring = (packed / nS) | 0;
            const flying = assembling && sz / n < -0.5;
            // V2. Width and alpha grow with log(n), not with n, so a pier that holds
            // 700+ records on the innermost plate cannot bury the bundles of one or two.
            // A_b is the old background alpha (including emphasis dimming, flight, the
            // source and the year lens), times the V3 density and the V4 fade-in.
            const lw = Math.min(3, 0.6 + 0.45 * Math.log2(n));
            let alpha =
              (emphasised ? 0.015 : 0.055) *
              Math.max(e, flying ? 0.6 : 0) *
              spa[si]! *
              lensA(ring) *
              density *
              horiz *
              (0.3 + (0.7 * Math.log(1 + n)) / logDen);
            if (strideB > 1) alpha = 1 - Math.pow(1 - Math.min(1, alpha), strideB);
            if (alpha < 0.005) continue;
            alpha *= fogAt((depthOf(sx / n, sy / n, sz / n) + sd[si]!) * 0.5, false);
            const [x0, y0] = proj(sx / n, sy / n, sz / n);
            const p = pathIn(
              base,
              strokeKey(
                Math.round(lw * 100) / 100,
                withAlpha(ink, qa(Math.min(1, alpha))),
                bi % LANES,
              ),
            );
            p.moveTo(x0, y0);
            p.lineTo(st.px[si]!, st.py[si]!);
          }
        }
        strokeBatch(ctx, base);
        strokeBatch(ctx, top);
      }
      for (let i = 0; i < nS; i++) {
        const sn = sources[i]!;
        const a = spa[i]!;
        if (a <= 0.01) continue;
        const emph =
          emphasisSource === i ||
          (emphasisArtist >= 0 && sn.records.some((ri) => records[ri]!.artist === emphasisArtist));
        const x = st.px[i]!;
        const y = st.py[i]!;
        // P2. A pier keeps its ellipse; the radius takes f at its centre so it matches the plate.
        const f = ssf[i]!;
        const pierFog = fogAt(sd[i]!, emph);
        const rad = sn.size * Math.sqrt(st.zoom) * f;
        ctx.beginPath();
        ctx.ellipse(x, y, rad, rad * Math.max(0.35, cosT), 0, 0, TAU);
        ctx.fillStyle = withAlpha(paper, 0.9 * a * pierFog);
        ctx.fill();
        ctx.lineWidth = emph ? 1.4 : 1;
        ctx.strokeStyle = withAlpha(
          emph ? accent : ink,
          a * (emph ? 0.95 : emphasised ? 0.18 : 0.55) * pierFog,
        );
        ctx.stroke();
        if (sn.records.length >= 6 && rad > 6) {
          ctx.font = font(9);
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillStyle = withAlpha(emph ? accent : ink, a * 0.7);
          ctx.fillText(String(sn.records.length), x, y);
        }
        const labelOk =
          emph ||
          (!emphasised &&
            e > 0.55 &&
            (sn.records.length >= 8 || (st.zoom > 1.4 && sn.records.length >= 3)));
        if (labelOk) {
          ctx.font = font(9.5);
          ctx.textAlign = "left";
          ctx.textBaseline = "middle";
          ctx.fillStyle = withAlpha(emph ? accent : ink, a * (emph ? 0.95 : 0.55));
          ctx.fillText(truncate(sn.domain, 28), x + rad + 5, y);
        }
      }

      /* ============================ layer: records ============================ */
      const ringA = assembling ? clamp((T - 0.2) / 0.8, 0, 1) : 1;
      ctx.lineWidth = 1;
      for (let k = 0; k < rings.length; k++) {
        const rg = rings[k]!;
        const base =
          ringA *
          (k === rings.length - 1 ? 0.16 : 0.085) *
          (lensRing < 0 ? 1 : k === lensRing ? 2.2 : 0.35);
        ctx.beginPath();
        ell(rg.r, ringZ[k]!);
        ctx.strokeStyle = withAlpha(
          ink,
          base * (1 - subSplit * 0.75) * fogAt(ringZ[k]! * cosT, false),
        );
        ctx.stroke();
        if (subSplit > 0.01 && (rg.count > 0 || rg.year === null)) {
          for (let gi = 0; gi < groups.length; gi++) {
            const g = groups[gi]!;
            const zSeg = ringZ[k]! + gz(gi);
            const midA = (g.a0 + g.a1) / 2 + st.rot;
            ctx.beginPath();
            ell(rg.r, zSeg, g.a0 + st.rot, g.a1 + st.rot);
            ctx.strokeStyle = withAlpha(
              ink,
              base *
                2.2 *
                subSplit *
                fogAt(depthOf(Math.cos(midA) * rg.r, Math.sin(midA) * rg.r, zSeg), false),
            );
            ctx.stroke();
          }
        }
      }
      // year labels stand on the left axis (rotated, so neighbours never collide); they are also the year lens
      st.ringLabel = [];
      const ringGap = (rings[2]!.r - rings[1]!.r) * s;
      if (ringGap > 9 && ringA > 0.5 && diagram < 0.5) {
        ctx.font = font(10);
        for (let k = 0; k < rings.length; k++) {
          const rg = rings[k]!;
          if (rg.count === 0 && rg.year !== null) continue;
          const [lx, ly0] = proj(-rg.r, 0, ringZ[k]!);
          const ly = ly0 + 6;
          const on = lensRing === k;
          ctx.save();
          ctx.translate(lx, ly);
          ctx.rotate(-Math.PI / 2);
          ctx.textAlign = "right";
          ctx.textBaseline = "middle";
          ctx.fillStyle = withAlpha(on ? accent : ink, on ? 1 : 0.5 * ringA);
          ctx.fillText(rg.label, 0, 0);
          const tw = measure(ctx, rg.label);
          ctx.restore();
          st.ringLabel.push({ k, x: lx - 7, y: ly - tw - 2, w: 14, h: tw + 6 });
        }
      }
      // programmes an artist moved between: one dot each, stacked outside their knot. With
      // generation arcs every artist has at least one programme, so a single dot says nothing the
      // card does not; the row marks only people on two or more programmes (as the programme-arc
      // rim did with "also in"), which also keeps the per-frame dot count near the old one.
      const minProgs = layout.programmesApart ? 2 : 1;
      const progRows = layout.artistProgrammes.some((ps) => ps.length >= minProgs) ? 1 : 0;
      const arcRw = rimR + 18 + (progRows ? 6 : 0);
      if (progRows && diagram < 0.3 && ringA > 0.3) {
        const rowA = ringA * (1 - diagram / 0.3);
        // Dots are batched into one path per colour and alpha step (1/20), so a frame issues a few
        // fills instead of one per dot.
        const batches = new Map<string, Path2D>();
        for (let i = 0; i < artists.length; i++) {
          const ps = layout.artistProgrammes[i]!;
          if (ps.length < minProgs) continue;
          const emph = isEmphArtist(i);
          const zDot = zTop + gz(artists[i]!.group);
          const ang = artists[i]!.angle;
          for (let k = 0; k < Math.min(ps.length, 4); k++) {
            const rr = rimR + 8 + k * 2.4;
            const dDot = depthOf(Math.cos(ang) * rr, Math.sin(ang) * rr, zDot);
            const [px, py] = proj(Math.cos(ang) * rr, Math.sin(ang) * rr, zDot);
            const alpha = (emph ? 0.95 : emphasised ? 0.15 : 0.45) * rowA * fogAt(dDot, emph);
            const key = `${emph ? 1 : 0}|${Math.round(alpha * 20)}`;
            let path = batches.get(key);
            if (!path) batches.set(key, (path = new Path2D()));
            const r = (emph ? 1.6 : 0.9) * scaleOf(dDot);
            path.moveTo(px + r, py);
            path.arc(px, py, r, 0, TAU);
          }
        }
        for (const [key, path] of batches) {
          const [em, step] = key.split("|");
          ctx.fillStyle = withAlpha(em === "1" ? accent : ink, Number(step) / 20);
          ctx.fill(path);
        }
      }
      const rimLabelA = ringA * (1 - e * 0.85) * (1 - diagram);
      ctx.lineWidth = 1.2;
      groups.forEach((g, gi) => {
        const zArc = zTop + gz(gi);
        const midArc = (g.a0 + g.a1) / 2 + st.rot;
        ctx.strokeStyle = withAlpha(
          ink,
          0.26 *
            ringA *
            fogAt(depthOf(Math.cos(midArc) * arcRw, Math.sin(midArc) * arcRw, zArc), false),
        );
        ctx.beginPath();
        ell(arcRw, zArc, g.a0 + st.rot, g.a1 + st.rot);
        ctx.stroke();
        if (rimLabelA > 0.05) {
          const mid = (g.a0 + g.a1) / 2 + st.rot;
          const lr = (rimR + 30 + (progRows ? 6 : 0)) * s;
          const lx = cx + Math.cos(mid) * lr;
          const ly = cy - zTop * sinT * s + Math.sin(mid) * lr * cosT;
          const upsideDown = Math.sin(mid) > 0;
          ctx.save();
          ctx.translate(lx, ly);
          ctx.rotate(
            Math.atan2(Math.sin(mid) * cosT, Math.cos(mid)) +
              Math.PI / 2 +
              (upsideDown ? Math.PI : 0),
          );
          letter("0.14em");
          ctx.textAlign = "center";
          ctx.textBaseline = upsideDown ? "top" : "bottom";
          ctx.fillStyle = withAlpha(ink, 0.62 * rimLabelA);
          const label = (lang === "ko" ? g.label_ko : g.label_en).toUpperCase();
          const arcLen = (g.a1 - g.a0) * lr;
          // Never squeeze the glyphs (fillText's maxWidth compresses them sideways). Generation arcs
          // all use the short form ("2005–09 · 190"; the legend says these are entry generations),
          // so arcs read alike; a label that still does not fit shrinks evenly down to 7.5 px and
          // is left out if it still does not fit.
          const gen = /^GEN-(\d{4})$/.exec(g.code);
          const short = gen
            ? `${gen[1]}–${String(Number(gen[1]) + 4).slice(2)} · ${g.n}`
            : `${label} · ${g.n}`;
          const room = arcLen - 8;
          const text = gen ? short : `${label} · ${g.n}`;
          let size = 10;
          ctx.font = font(size, 700);
          const wAt10 = measure(ctx, text);
          if (wAt10 > room) {
            size = Math.max(7.5, (10 * room) / wAt10);
            ctx.font = font(size, 700);
          }
          if (arcLen > 40 && measure(ctx, text) <= room)
            ctx.fillText(text, 0, upsideDown ? 4 : -4);
          letter("0em");
          ctx.restore();
        }
      });

      /* chords — chordA is part of the layer-cache key, which decides whether screen
         positions of every record are needed this frame */
      const chordA =
        (assembling ? clamp((T - A_CLOSE[0]) / (A_CLOSE[1] - A_CLOSE[0]), 0, 1) : 1) *
        (1 - spread * 0.7) *
        (1 - diagram * 0.6);
      // Layer cache validity, computed before the record projection so a frame that only
      // re-stamps the cached chords, strands and record dots can skip that projection.
      // Invalidated by spread, stage, diagram, parts, tilt past the threshold below, zoom
      // or scale stretch, theme, period, emphasis (not which artist), version marks,
      // chord alpha, and window size / dpr. Hover overlays are painted live on top.
      const cacheable =
        st.asmDone &&
        spread < 1e-4 &&
        st.stageT < 1e-3 &&
        diagram <= 0.001 &&
        partsOut < 1e-3 &&
        cosT > 0.25;
      const sig = cacheable
        ? [
            w,
            h,
            dpr,
            ink,
            paper,
            accent,
            // which artist is emphasised is deliberately NOT part of the key: the cached copy
            // holds the unemphasised picture (dimmed while something is emphasised), so moving
            // the pointer from artist to artist costs one small live pass, not a full repaint
            emphasised ? 1 : 0,
            lensRing,
            liftChords ? 1 : 0,
            st.yr0,
            st.yr1,
            st.versions ? 1 : 0,
            chordA.toFixed(3),
          ].join("|")
        : "";
      const dRot = st.rot - lc.rot;
      const dScale = s / lc.s;
      const dTilt = cosT / lc.cosT;
      // "settled" means the reader stopped, not the slow idle spin: that never settles, and
      // repainting the layer against it every 700 ms is a stutter for no gain (the rotated copy
      // is repainted anyway once the turn passes the 0.5 rad limit below).
      const settled =
        st.gesture === "none" &&
        Math.abs(st.vel) < 0.02 &&
        st.targetRot == null &&
        now - st.lastInput < 4000;
      const stretched = dScale > 1.15 || dScale < 0.87 || dTilt > 1.15 || dTilt < 0.87;
      const reuse =
        cacheable &&
        lc.sig === sig &&
        Math.abs(dRot) < 0.5 &&
        !stretched &&
        !(
          settled &&
          now - lc.at > 700 &&
          (Math.abs(dRot) > 1e-4 || cx !== lc.cx || cy !== lc.cy || dScale !== 1 || dTilt !== 1)
        );
      // screen positions for every visible record. The projection is inlined (a call and a
      // two-element array per record costs more than the arithmetic at this count). P1 is
      // the same f as proj, so hit testing (st.sx) matches the drawn dot. Records outside
      // the chosen years, which are not drawn or hovered, are skipped. A reused link layer
      // already holds the dots. While the reader is turning the disc the pointer is not
      // picking, so the per-record projection can wait; a resting pointer still needs it
      // for hit testing and the info card.
      const trayOn = tray.size > 0;
      const needRecordPx =
        !reuse ||
        emphasised ||
        chordHov >= 0 ||
        st.notes.length > 0 ||
        assembling ||
        partsOut > 0.001 ||
        st.gesture === "none";
      if (needRecordPx)
      for (let i = 0; i < records.length; i++) {
        if (ra[i]! <= 0) continue;
        // Landed dots are already in the opening layer. With the pointer off the disc their
        // screen position is not read (no hit test, no card), so only records still moving
        // or still pulsing are projected. The frame that freezes the flat layer cache does
        // need every dot, so the skip stays off while that snapshot is being taken.
        if (
          assembling &&
          !cacheable &&
          !st.pointer.inside &&
          !(st.versions && newSince) &&
          ra[i]! >= 0.999
        ) {
          const k = records[i]!.reveal;
          const t0 =
            A_RECORDS[0] +
            ((A_RECORDS[1] - A_RECORDS[0] - RECORD_FLIGHT) * k) /
              Math.max(1, records.length - 1);
          const since = T - t0 - RECORD_FLIGHT;
          if (since < 0 || since >= 0.6) continue;
        }
        const X = rx[i]! * cr - ry[i]! * sr;
        const Y = rx[i]! * sr + ry[i]! * cr;
        const px = X * s;
        const py = (Y * cosT - rz[i]! * sinT) * s;
        let ox = px * rc - py * rs;
        let oy = px * rs + py * rc;
        const d = Y * sinT + rz[i]! * cosT;
        rd[i] = d;
        const f = scaleOf(d);
        rf[i] = f;
        if (f !== 1) {
          ox *= f;
          oy *= f;
        }
        const x = cx + ox;
        const y = cy + oy;
        if (trayOn) {
          const tp = tray.get(i);
          st.sx[i] = tp ? lerp(x, tp[0], partsOut) : x;
          st.sy[i] = tp ? lerp(y, tp[1], partsOut) : y;
        } else {
          st.sx[i] = x;
          st.sy[i] = y;
        }
      }
      for (let i = 0; i < artists.length; i++) {
        const a = artists[i]!;
        const [x, y] = proj(a.x, a.y, zTop + gz(a.group));
        st.kx[i] = x;
        st.ky[i] = y;
      }

      const dotBase = records.length > 2000 ? 1.2 : 1.55;
      const dotR = clamp(dotBase * Math.sqrt(st.zoom), 1.05, 3.4);
      // how many pixels one artist gets on the rim; below ~9px the rim switches to ticks
      const slotPx = unit * rimR * s;
      // chords, strands and record dots: the heaviest layers (tens of thousands of marks)
      /* phase: "all" paints what the frame needs; "base" paints every mark as unemphasised (so the
         cached copy does not depend on which artist is under the pointer); "emph" paints only the
         emphasised artist's marks, live, over that copy. */
      type Phase = "all" | "base" | "emph" | "flying";
      const paintLinks = (g: CanvasRenderingContext2D, phase: Phase = "all") => {
        const baseOnly = phase === "base";
        const emphOnly = phase === "emph";
        // during the opening, records that have landed are already in the layer below
        const flyingOnly = phase === "flying";
        const isEmphRecP = (ri: number) => !baseOnly && isEmphRec(ri);
        const isEmphArtistP = (ai: number) => !baseOnly && isEmphArtist(ai);
        const emphasisedP = baseOnly ? true : emphasised;
        const chordHovP = baseOnly ? -1 : chordHov;
        if (chordA > 0.01) {
          const chordThin = Math.min(1, 320 / Math.max(1, chords.length));
          const base: Batch = new Map();
          const top: Batch = new Map();
          for (let ci = 0; ci < chords.length; ci++) {
            const c = chords[ci]!;
            if (ra[c.a]! <= 0 || ra[c.b]! <= 0) continue;
            const rel = isEmphRecP(c.a) || isEmphRecP(c.b);
            const hot = ci === chordHovP;
            if (emphOnly && !rel && !hot) continue;
            const alpha =
              (hot ? 1 : rel ? (liftChords ? 0.8 : 0.42) : emphasisedP ? 0.01 : 0.07 * chordThin) *
              chordA *
              Math.min(lensA(records[c.a]!.ring), lensA(records[c.b]!.ring)) *
              fogAt((rd[c.a]! + rd[c.b]!) * 0.5, rel || hot);
            // background chords are very faint (they add up); round finely so they are not lost
            const style = withAlpha(rel ? accent : ink, Math.round(alpha * 5000) / 5000);
            const p = pathIn(
              rel || hot ? top : base,
              strokeKey(hot ? 1.8 : 0.8, style, hot ? -1 : ci % LANES),
            );
            p.moveTo(st.sx[c.a]!, st.sy[c.a]!);
            const mx = (st.sx[c.a]! + st.sx[c.b]!) / 2;
            const my = (st.sy[c.a]! + st.sy[c.b]!) / 2;
            p.quadraticCurveTo(
              cx + (mx - cx) * 0.45,
              cy + (my - cy) * 0.45,
              st.sx[c.b]!,
              st.sy[c.b]!,
            );
          }
          strokeBatch(g, base);
          strokeBatch(g, top);
        }

        /* strands: straight when flat; a column through the year plates when spread */
        g.lineWidth = 1;
        const strandBase: Batch = new Map();
        const strandTop: Batch = new Map();
        const TICK_BUDGET = 12000;
        const tickStride = Math.max(
          1,
          Math.round(records.length / Math.max(1, artists.length) / (TICK_BUDGET / artists.length)),
        );
        for (let i = 0; i < artists.length; i++) {
          const a = artists[i]!;
          const emph = isEmphArtistP(i);
          if (emphOnly && !emph) continue;
          const ringsHit: number[] = [];
          for (const ri of a.strand) {
            if (ra[ri]! >= 0.999 && rz[ri]! > -0.01) {
              const rk = records[ri]!.ring;
              if (!ringsHit.includes(rk)) ringsHit.push(rk);
            }
          }
          if (ringsHit.length === 0) continue;
          ringsHit.sort((p, q) => p - q);
          let dSum = depthOf(a.x, a.y, zTop + gz(a.group));
          for (const rk of ringsHit) {
            const rr = rings[rk]!.r;
            dSum += depthOf(
              Math.cos(a.angle) * rr,
              Math.sin(a.angle) * rr,
              ringZ[rk]! + gz(a.group),
            );
          }
          const alpha =
            (emph ? 0.85 : emphasisedP ? 0.04 : artists.length > 120 ? 0.11 : 0.17) *
            fogAt(dSum / (ringsHit.length + 1), emph);
          const style = withAlpha(emph ? accent : ink, emph ? alpha : qa(alpha));
          const bt = emph ? strandTop : strandBase;
          const sp = pathIn(bt, strokeKey(1, style));
          ringsHit.forEach((rk, j) => {
            const [x, y] = proj(
              Math.cos(a.angle) * rings[rk]!.r,
              Math.sin(a.angle) * rings[rk]!.r,
              ringZ[rk]! + gz(a.group),
            );
            if (j === 0) sp.moveTo(x, y);
            else sp.lineTo(x, y);
          });
          sp.lineTo(st.kx[i]!, st.ky[i]!);
          // ticks from a record to its year ring: always for the emphasised artist, otherwise
          // only while they stay within a budget (zoomed in over a large archive this is tens of
          // thousands of short lines, and they read as texture rather than as single ties)
          if (emph || ((st.zoom > 1.6 || spread > 0.3) && i % tickStride === 0)) {
            const tp = pathIn(bt, strokeKey(emph ? 0.9 : 0.6, style));
            for (const ri of a.strand) {
              if (ra[ri]! < 0.999) continue;
              if (tray.has(ri) && partsOut > 0.4) continue;
              const rk = records[ri]!.ring;
              const [tx, ty] = proj(
                Math.cos(a.angle) * rings[rk]!.r,
                Math.sin(a.angle) * rings[rk]!.r,
                ringZ[rk]! + gz(a.group),
              );
              tp.moveTo(tx, ty);
              tp.lineTo(st.sx[ri]!, st.sy[ri]!);
            }
          }
        }
        strokeBatch(g, strandBase);
        strokeBatch(g, strandTop);
        g.lineWidth = 1;

        /* records */
        {
          const dotsBase: Batch = new Map();
          const dotsTop: Batch = new Map();
          const rings2: Batch = new Map();
          // landing pulses: about records × 0.6 s / landing window are alive at once; keep it near 1500
          const liveLandings =
            (records.length * 0.6) / (A_RECORDS[1] - A_RECORDS[0] - RECORD_FLIGHT);
          const landingStride = Math.max(1, Math.round(liveLandings / 1500));
          // styles by (emphasis, alpha in half-percent steps): a number key, no string building per dot
          const dotStyle = new Map<number, string>();
          const styleFor = (emph: boolean, alpha: number) => {
            const q = Math.round(alpha * 200);
            const key = emph ? q + 1000 : q;
            let st2 = dotStyle.get(key);
            if (st2 === undefined) {
              st2 = withAlpha(emph ? accent : ink, q / 200);
              dotStyle.set(key, st2);
            }
            return st2;
          };
          // During the opening the landed dots are already in the layer. Only the records still
          // in flight, and the landing pulses (alpha and radius still easing), are walked here.
          // The index ranges are widened by two so a record on the boundary is not dropped; the
          // per-record tests below are unchanged.
          const denomK = Math.max(1, records.length - 1);
          const spanK = A_RECORDS[1] - A_RECORDS[0] - RECORD_FLIGHT;
          const kAt = (t0: number) => ((t0 - A_RECORDS[0]) * denomK) / spanK;
          let kStart = 0;
          let kEnd = records.length - 1;
          if (flyingOnly && !(st.versions && newSince)) {
            const flyLo = Math.floor(kAt(T - 0.5 * RECORD_FLIGHT)) - 2;
            const flyHi = Math.ceil(kAt(T)) + 2;
            const pulseLo = Math.floor(kAt(T - RECORD_FLIGHT - 0.6)) - 2;
            const pulseHi = Math.ceil(kAt(T - RECORD_FLIGHT)) + 2;
            kStart = Math.max(0, Math.min(flyLo, pulseLo));
            kEnd = Math.min(records.length - 1, Math.max(flyHi, pulseHi));
          }
          for (let k = kStart; k <= kEnd; k++) {
            const ri = revealOrder[k]!;
            const a = ra[ri]!;
            if (a <= 0) continue;
            const nd = records[ri]!;
            const emph = isEmphRecP(ri);
            const isHov = !baseOnly && hov?.kind === "record" && hov.idx === ri;
            if (emphOnly && !emph && !isHov) continue;
            const x = st.sx[ri]!;
            const y = st.sy[ri]!;
            const flying = assembling && a < 1;
            const emphMark = emph || isHov;
            let alpha = (emph ? 1 : emphasisedP ? 0.16 : 0.88) * a * lensA(nd.ring);
            if (alpha < 0.01) continue;
            // P2. A dot keeps its shape. f is taken at its centre; a part that has left
            // the shaft for the parts list eases back to the unscaled radius, and is not
            // fogged once it is listed (the list is not a depth mark).
            const inTray = trayOn && tray.has(ri);
            const f = inTray ? lerp(rf[ri]!, 1, partsOut) : rf[ri]!;
            const fog = inTray
              ? lerp(fogAt(rd[ri]!, emphMark), 1, partsOut)
              : fogAt(rd[ri]!, emphMark);
            alpha *= fog;
            if (!(flyingOnly && a >= 0.999)) {
              const rr = (isHov ? dotR + 1.6 : dotR) * (flying ? 1.6 - 0.6 * a : 1) * f;
              addDot(emph || isHov ? dotsTop : dotsBase, styleFor(emph, alpha), x, y, rr);
            }
            if (!emphOnly && st.versions && newSince && newSince.has(ri)) {
              const p = pathIn(rings2, strokeKey(1, withAlpha(accent, 0.7)));
              const ringR = (dotR + 3) * f;
              p.moveTo(x + ringR, y);
              p.arc(x, y, ringR, 0, TAU);
            }
            if (!emphOnly && assembling && a >= 0.999 && k % landingStride === 0) {
              const t0 =
                A_RECORDS[0] +
                (A_RECORDS[1] - A_RECORDS[0] - RECORD_FLIGHT) *
                  (k / Math.max(1, records.length - 1));
              const since = T - t0 - RECORD_FLIGHT;
              if (since >= 0 && since < 0.6) {
                const fu = since / 0.6;
                const r2 = (dotR + 2 + fu * 10) * f;
                const p = pathIn(rings2, strokeKey(1, withAlpha(ink, qa((1 - fu) * 0.4))));
                p.moveTo(x + r2, y);
                p.arc(x, y, r2, 0, TAU);
              }
            }
          }
          fillBatch(g, dotsBase);
          fillBatch(g, dotsTop);
          strokeBatch(g, rings2);
        }
      };
      /* Opening: every landed record is stamped into the layer once, in the projection the layer
         was started in, and the whole layer is then re-stamped through the affine map above (the
         disc is still tilting, but landed marks all sit at z = 0, so one map covers them). Only the
         records still in flight are drawn per frame.
         P1/P4. The stamp is orthographic. It only runs at w ≤ 0.001; perspective is not an affine
         map, so the layer is not reused once the diagram opens. */
      const asmLayer =
        assembling &&
        !cacheable &&
        T > A_RECORDS[0] &&
        spread < 1e-4 &&
        st.stageT < 1e-3 &&
        diagram <= 0.001 &&
        cosT > 0.25;
      if (asmLayer) {
        const key = [w, h, dpr, ink, paper, accent, dotR.toFixed(2), st.yr0, st.yr1].join("|");
        const dR = st.rot - asm.rot;
        const dS = s / asm.s;
        const dT = cosT / asm.cosT;
        const stale =
          asm.key !== key || Math.abs(dR) > 0.4 || dS > 1.12 || dS < 0.9 || dT > 1.12 || dT < 0.9;
        if (stale) {
          if (layer.width !== canvas.width || layer.height !== canvas.height) {
            layer.width = canvas.width;
            layer.height = canvas.height;
          }
          lctx.setTransform(1, 0, 0, 1, 0, 0);
          lctx.clearRect(0, 0, layer.width, layer.height);
          lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          lctx.lineCap = "round";
          lctx.lineJoin = "round";
          if (asm.stamped.length !== records.length) asm.stamped = new Uint8Array(records.length);
          else asm.stamped.fill(0);
          asm.key = key;
          asm.rot = st.rot;
          asm.cx = cx;
          asm.cy = cy;
          asm.s = s;
          asm.cosT = cosT;
          asm.rc = rc;
          asm.rs = rs;
          lc.sig = ""; // the post-opening cache must repaint from scratch
        }
        // stamp whatever landed since the last frame, in the layer's own projection
        const fresh: Batch = new Map();
        const acr = Math.cos(asm.rot);
        const asr = Math.sin(asm.rot);
        const asinT = Math.sqrt(Math.max(0, 1 - asm.cosT * asm.cosT));
        for (let ri = 0; ri < records.length; ri++) {
          if (asm.stamped[ri] || ra[ri]! < 0.999) continue;
          const nd = records[ri]!;
          const X = nd.x * acr - nd.y * asr;
          const Y = nd.x * asr + nd.y * acr;
          const px = X * asm.s;
          const py = Y * asm.cosT * asm.s;
          const x = asm.cx + px * asm.rc - py * asm.rs;
          const y = asm.cy + px * asm.rs + py * asm.rc;
          addDot(fresh, withAlpha(ink, qa(0.88 * lensA(nd.ring))), x, y, dotR);
          asm.stamped[ri] = 1;
        }
        void asinT;
        fillBatch(lctx, fresh);
        // re-stamp the layer through turn, tilt and zoom, then draw what is still in flight
        const c0 = Math.cos(dR);
        const s0 = Math.sin(dR);
        const k00 = (s * c0) / asm.s,
          k01 = (-s * s0) / (asm.s * asm.cosT),
          k10 = (s * cosT * s0) / asm.s,
          k11 = (s * cosT * c0) / (asm.s * asm.cosT);
        const m00 = rc * k00 - rs * k10,
          m01 = rc * k01 - rs * k11;
        const m10 = rs * k00 + rc * k10,
          m11 = rs * k01 + rc * k11;
        const l00 = m00 * asm.rc - m01 * asm.rs,
          l01 = m00 * asm.rs + m01 * asm.rc;
        const l10 = m10 * asm.rc - m11 * asm.rs,
          l11 = m10 * asm.rs + m11 * asm.rc;
        const tx = cx - (l00 * asm.cx + l01 * asm.cy);
        const ty = cy - (l10 * asm.cx + l11 * asm.cy);
        ctx.setTransform(dpr * l00, dpr * l10, dpr * l01, dpr * l11, dpr * tx, dpr * ty);
        ctx.drawImage(layer, 0, 0, w, h);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        paintLinks(ctx, "flying");
      }
      /* Layer cache. While the disc lies flat (no spread, no exploded diagram, every mark at z=0),
         turning it is one affine map of the whole picture, so the chord, strand and record-dot
         layers are painted once into an offscreen canvas and re-stamped with a transform.
         Repainted when anything they depend on changes, when the turn gets large, and shortly
         after motion settles (so it stays crisp). The validity test is `reuse`, above.
         P4. Perspective is not affine, so this copy is never reused while w > 0.001.
         Scale and tilt are not in the key either: they only stretch the same picture, so the copy
         is re-stamped through them and repainted once the stretch grows enough to show. */
      if (reuse) {
        /* S = C + L (S_ref − C_ref) with
           L = Rscreen · D · R(dRot) · D_ref⁻¹ · Rscreen_ref⁻¹,  D = diag(s, s·cosT).
           One affine map takes the copy from the turn, tilt and zoom it was painted at to now. */
        const c0 = Math.cos(dRot);
        const s0 = Math.sin(dRot);
        // D · R(dRot) · D_ref⁻¹
        const k00 = (s * c0) / lc.s,
          k01 = (-s * s0) / (lc.s * lc.cosT),
          k10 = (s * cosT * s0) / lc.s,
          k11 = (s * cosT * c0) / (lc.s * lc.cosT);
        // Rscreen · K
        const m00 = rc * k00 - rs * k10,
          m01 = rc * k01 - rs * k11;
        const m10 = rs * k00 + rc * k10,
          m11 = rs * k01 + rc * k11;
        // (Rscreen · K) · Rscreen_ref⁻¹, Rscreen_ref⁻¹ = [[rc_ref, rs_ref], [−rs_ref, rc_ref]]
        const l00 = m00 * lc.rc - m01 * lc.rs,
          l01 = m00 * lc.rs + m01 * lc.rc;
        const l10 = m10 * lc.rc - m11 * lc.rs,
          l11 = m10 * lc.rs + m11 * lc.rc;
        const tx = cx - (l00 * lc.cx + l01 * lc.cy);
        const ty = cy - (l10 * lc.cx + l11 * lc.cy);
        ctx.setTransform(dpr * l00, dpr * l10, dpr * l01, dpr * l11, dpr * tx, dpr * ty);
        ctx.drawImage(layer, 0, 0, w, h);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (emphasised || chordHov >= 0) paintLinks(ctx, "emph");
      } else if (cacheable) {
        if (layer.width !== canvas.width || layer.height !== canvas.height) {
          layer.width = canvas.width;
          layer.height = canvas.height;
        }
        lctx.setTransform(1, 0, 0, 1, 0, 0);
        lctx.clearRect(0, 0, layer.width, layer.height);
        lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        lctx.lineCap = "round";
        lctx.lineJoin = "round";
        paintLinks(lctx, "base");
        lc.sig = sig;
        lc.rot = st.rot;
        lc.cx = cx;
        lc.cy = cy;
        lc.at = now;
        lc.s = s;
        lc.cosT = cosT;
        lc.rc = rc;
        lc.rs = rs;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.drawImage(layer, 0, 0);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (emphasised || chordHov >= 0) paintLinks(ctx, "emph");
      } else if (!asmLayer) {
        lc.sig = "";
        paintLinks(ctx);
      }

      /* -- sounded records: a ring spreads from each record as it is played -- */
      if (st.notes.length) {
        const FL = 720;
        let keep = 0;
        for (let q = 0; q < st.notes.length; q++) {
          const n = st.notes[q]!;
          const age = now - n.at;
          if (age > FL) continue;
          st.notes[keep++] = n;
          if (age < 0 || n.record < 0 || ra[n.record]! <= 0) continue;
          const u = age / FL;
          const x = st.sx[n.record]!;
          const y = st.sy[n.record]!;
          const fN = rf[n.record]!;
          ctx.beginPath();
          ctx.arc(x, y, (dotR + 1.5 + u * 15) * fN, 0, TAU);
          ctx.lineWidth = 1;
          ctx.strokeStyle = withAlpha(accent, (1 - u) * 0.6);
          ctx.stroke();
          if (u < 0.25) {
            ctx.beginPath();
            ctx.arc(x, y, (dotR + 0.6) * fN, 0, TAU);
            ctx.fillStyle = withAlpha(accent, (1 - u * 4) * 0.9);
            ctx.fill();
          }
        }
        st.notes.length = keep;
      }

      /* knots */
      const showNames = st.zoom > 1.75;
      // Flat disc, no names: every ordinary knot is the same stroke, and one path of those
      // ticks matches a stroke per artist (measured: no pixel differs). Emphasis, the needle's
      // reading, hidden dashes and an open diagram stay individual strokes on top.
      const batchKnots = diagram <= 0.001 && !showNames;
      const knotTicks: Batch = new Map();
      const knotRings: Batch = new Map();
      const knotLate: number[] = [];
      const paintKnotMark = (i: number) => {
        const a = artists[i]!;
        const x = st.kx[i]!;
        const y = st.ky[i]!;
        const emph = isEmphArtist(i);
        const isRead = reading === i;
        const alpha = emph ? 1 : emphasised ? 0.2 : isRead ? 0.95 : 0.72;
        const col = emph ? accent : ink;
        const ang = Math.atan2(Math.sin(a.angle + st.rot) * cosT, Math.cos(a.angle + st.rot));
        const roomy = slotPx >= 9 || emph || isRead;
        const dK = depthOf(a.x, a.y, zTop + gz(a.group));
        const fK = scaleOf(dK);
        const fogK = fogAt(dK, emph);
        const knotStyle = withAlpha(col, (roomy ? alpha : alpha * 0.7) * fogK);
        const ca = Math.cos(ang);
        const sa = Math.sin(ang);
        const t0 = roomy ? 4 : -2;
        const t1 = roomy ? 9 : 5;
        ctx.lineWidth = 1;
        ctx.strokeStyle = knotStyle;
        if (roomy) {
          ctx.beginPath();
          if (a.hidden) ctx.setLineDash([2, 2]);
          const kr = (emph || isRead ? 3.6 : 2.6) * fK;
          ctx.arc(x, y, kr, 0, TAU);
          ctx.stroke();
          ctx.setLineDash([]);
        }
        ctx.beginPath();
        ctx.moveTo(x + ca * t0, y + sa * t0);
        ctx.lineTo(x + ca * t1, y + sa * t1);
        ctx.stroke();
        if ((showNames || emph || (isRead && !st.needleOn)) && !a.hidden) {
          ctx.save();
          if (emph && diagram < 0.5) {
            // the chosen artist reads upright, set just outside the knot;
            // under the needle it steps aside to the right so the needle stays clear
            const atNeedle =
              st.needleOn &&
              diagram < 0.3 &&
              Math.abs(shortAngle(a.angle + st.rot + Math.PI / 2)) < 0.22;
            if (atNeedle && st.focus === i) {
              ctx.restore();
              return;
            }
            const cx0 = ca;
            let nx = x + ca * 15;
            let ny = y + sa * 15;
            ctx.font = fontKo(12.5, 600);
            ctx.textAlign = atNeedle
              ? "left"
              : cx0 > 0.35
                ? "left"
                : cx0 < -0.35
                  ? "right"
                  : "center";
            ctx.textBaseline = atNeedle ? "middle" : sa < 0 ? "bottom" : "top";
            if (atNeedle) {
              nx = x + 12;
              ny = y - 1;
            }
            const full = nameOf(i);
            const label = full.length > 24 ? `${full.slice(0, 23)}…` : full;
            const tw = measure(ctx, label);
            const bx =
              ctx.textAlign === "left" ? nx : ctx.textAlign === "right" ? nx - tw : nx - tw / 2;
            const by =
              ctx.textBaseline === "bottom" ? ny - 16 : ctx.textBaseline === "middle" ? ny - 9 : ny;
            ctx.fillStyle = withAlpha(paper, 0.82);
            ctx.fillRect(bx - 3, by - 1, tw + 6, 18);
            ctx.fillStyle = col;
            ctx.fillText(label, nx, ny);
          } else {
            const upside = ca < 0;
            ctx.translate(x + ca * 13, y + sa * 13);
            ctx.rotate(ang + (upside ? Math.PI : 0));
            ctx.font = fontKo(10.5, 500);
            ctx.textAlign = upside ? "right" : "left";
            ctx.textBaseline = "middle";
            ctx.fillStyle = withAlpha(col, 0.6);
            ctx.fillText(nameOf(i), 0, 0);
          }
          ctx.restore();
        }
      };
      for (let i = 0; i < artists.length; i++) {
        const a = artists[i]!;
        const anyArrived = a.strand.some((ri) => ra[ri]! >= 0.999);
        if (!anyArrived && a.strand.length > 0) continue;
        const x = st.kx[i]!;
        const y = st.ky[i]!;
        const emph = isEmphArtist(i);
        const isRead = reading === i;
        const alpha = emph ? 1 : emphasised ? 0.2 : isRead ? 0.95 : 0.72;
        const col = emph ? accent : ink;
        const ang = Math.atan2(Math.sin(a.angle + st.rot) * cosT, Math.cos(a.angle + st.rot));
        const roomy = slotPx >= 9 || emph || isRead;
        // A knot is a centre mark, so it takes f and fog like a dot. The name is a label.
        const dK = depthOf(a.x, a.y, zTop + gz(a.group));
        const fK = scaleOf(dK);
        const fogK = fogAt(dK, emph);
        const knotStyle = withAlpha(col, (roomy ? alpha : alpha * 0.7) * fogK);
        const ca = Math.cos(ang);
        const sa = Math.sin(ang);
        const t0 = roomy ? 4 : -2;
        const t1 = roomy ? 9 : 5;
        if (batchKnots && !emph && !isRead && !a.hidden) {
          const tp = pathIn(knotTicks, strokeKey(1, knotStyle));
          tp.moveTo(x + ca * t0, y + sa * t0);
          tp.lineTo(x + ca * t1, y + sa * t1);
          if (roomy) {
            const kr = 2.6 * fK;
            const rp = pathIn(knotRings, strokeKey(1, knotStyle));
            rp.moveTo(x + kr, y);
            rp.arc(x, y, kr, 0, TAU);
          }
          continue;
        }
        if (batchKnots) {
          knotLate.push(i);
          continue;
        }
        paintKnotMark(i);
      }
      if (batchKnots) {
        strokeBatch(ctx, knotTicks);
        strokeBatch(ctx, knotRings);
        for (const i of knotLate) paintKnotMark(i);
      }

      /* -- venue partners: where the chosen artist's curves land -- */
      if (emphasisArtist >= 0 && st.links.chords.length && diagram < 0.3) {
        const la = liftChords ? 1 : 0.75;
        ctx.lineWidth = 1;
        for (const ci of st.links.chords) {
          const c = chords[ci]!;
          const far = records[c.a]!.artist === emphasisArtist ? c.b : c.a;
          if (ra[far]! <= 0) continue;
          ctx.beginPath();
          ctx.arc(st.sx[far]!, st.sy[far]!, (dotR + 2.2) * rf[far]!, 0, TAU);
          ctx.strokeStyle = withAlpha(accent, 0.6 * la);
          ctx.stroke();
        }
        const partners = st.links.partners;
        const placed: Array<[number, number, number, number]> = [];
        partners.forEach((pi, n) => {
          const kx = st.kx[pi]!;
          const ky = st.ky[pi]!;
          const fP = scaleOf(
            depthOf(artists[pi]!.x, artists[pi]!.y, zTop + gz(artists[pi]!.group)),
          );
          ctx.beginPath();
          ctx.arc(kx, ky, 3.2 * fP, 0, TAU);
          ctx.strokeStyle = withAlpha(accent, 0.85 * la);
          ctx.stroke();
          const hotPartner =
            chordHov >= 0 &&
            (records[chords[chordHov]!.a]!.artist === pi ||
              records[chords[chordHov]!.b]!.artist === pi);
          if (!(liftChords && (n < 8 || hotPartner))) return;
          const ang = Math.atan2(ky - cy, kx - cx);
          const nx = kx + Math.cos(ang) * 12;
          const ny = ky + Math.sin(ang) * 12;
          const label = truncate(nameOf(pi), 16);
          ctx.font = fontKo(hotPartner ? 11.5 : 10.5, hotPartner ? 600 : 500);
          ctx.textAlign = Math.cos(ang) > 0.3 ? "left" : Math.cos(ang) < -0.3 ? "right" : "center";
          ctx.textBaseline =
            Math.sin(ang) > 0.3 ? "top" : Math.sin(ang) < -0.3 ? "bottom" : "middle";
          const tw = measure(ctx, label);
          const bx =
            ctx.textAlign === "left" ? nx : ctx.textAlign === "right" ? nx - tw : nx - tw / 2;
          const by =
            ctx.textBaseline === "top" ? ny : ctx.textBaseline === "bottom" ? ny - 14 : ny - 7;
          const clash = placed.some(
            ([x0, y0, w0, h0]) =>
              bx < x0 + w0 + 4 && bx + tw + 4 > x0 && by < y0 + h0 && by + 16 > y0,
          );
          if (clash && !hotPartner) return;
          placed.push([bx - 2, by - 1, tw + 4, 16]);
          ctx.fillStyle = withAlpha(paper, 0.85);
          ctx.fillRect(bx - 2, by - 1, tw + 4, 16);
          ctx.fillStyle = withAlpha(accent, hotPartner ? 1 : 0.85);
          ctx.fillText(label, nx, ny);
        });
      }

      /* -- hub plate: only the name of the archive -- */
      if (st.asmDone && diagram < 0.6 && st.zoom < 2.2) {
        const [hx, hy] = proj(0, 0, 0);
        const ha = (1 - diagram / 0.6) * (1 - spread) * 0.9;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.font = font(9.5, 700);
        letter("0.16em");
        ctx.fillStyle = withAlpha(ink, 0.75 * ha);
        ctx.fillText("GIYE", hx, hy);
        letter("0em");
      }

      /* -- the needle and its reading -- */
      st.nameBox = null;
      if (st.needleOn && st.asmDone && !assembling && diagram < 0.3) {
        const topY = cy - zTop * sinT * s - (rimR + 26) * s * cosT;
        const innerY = cy - rings[1]!.r * s * cosT;
        ctx.beginPath();
        ctx.moveTo(cx, topY - 10);
        ctx.lineTo(cx, innerY);
        ctx.lineWidth = 1;
        ctx.strokeStyle = withAlpha(ink, 0.55);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(cx - 4, topY - 14);
        ctx.lineTo(cx + 4, topY - 14);
        ctx.lineTo(cx, topY - 8);
        ctx.closePath();
        ctx.fillStyle = withAlpha(ink, 0.7);
        ctx.fill();
        const turning =
          Math.abs(st.vel) > 0.02 || now - st.lastTurnAt < 2200 || st.targetRot != null;
        // the chosen artist's name stands above the needle head
        if (st.focus >= 0) {
          const full = nameOf(st.focus);
          const label = full.length > 28 ? `${full.slice(0, 27)}…` : full;
          ctx.font = fontKo(14, 600);
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          const tw = measure(ctx, label);
          ctx.fillStyle = withAlpha(paper, 0.85);
          ctx.fillRect(cx - tw / 2 - 5, topY - 40, tw + 10, 20);
          ctx.fillStyle = accent;
          ctx.fillText(label, cx, topY - 22);
          // a thin rule under the name marks it as a handle
          ctx.fillStyle = withAlpha(accent, 0.45);
          ctx.fillRect(cx - tw / 2, topY - 19, tw, 1);
          st.nameBox = { x: cx - tw / 2 - 8, y: topY - 44, w: tw + 16, h: 28 };
        }
        // (the reading panel that used to sit above the needle while turning was removed)
      }

      /* -- period mark when the view is narrowed to some years -- */
      if (st.asmDone && (st.yr0 > 0 || st.yr1 < rings.length - 1)) {
        const span = `${rings[st.yr0]!.label}–${rings[st.yr1]!.label}`;
        ctx.font = font(10);
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillStyle = withAlpha(accent, 0.9);
        const [dxp, dyp] = proj(0, rimR + 40, 0);
        ctx.fillText(
          t(`기간 ${span} · 기록 ${revealCount}`, `period ${span} · ${revealCount} records`),
          dxp,
          dyp + 6,
        );
      }

      /* -- exploded-diagram furniture: centreline, callouts, numbered parts -- */
      if (diagram > 0.02 && st.asmDone) {
        const da = diagram;
        const [ax0, ay0] = proj(0, 0, zFrm - R * 0.18);
        const [ax1, ay1] = proj(0, 0, zTop + maxGz + R * 0.22);
        ctx.setLineDash([6, 5]);
        ctx.lineWidth = 0.8;
        ctx.strokeStyle = withAlpha(ink, 0.35 * da);
        ctx.beginPath();
        ctx.moveTo(ax0, ay0);
        ctx.lineTo(ax1, ay1);
        ctx.stroke();
        ctx.setLineDash([]);
        const callout = (
          x: number,
          y: number,
          label: string,
          lx: number,
          ly: number,
          alpha: number,
          emph = false,
        ) => {
          // keep the balloon inside the sheet frame
          lx = clamp(lx, 30, w - 30);
          ly = clamp(ly, 30, h - 30);
          ctx.beginPath();
          ctx.moveTo(x, y);
          ctx.lineTo(lx, ly);
          ctx.lineWidth = 0.8;
          ctx.strokeStyle = withAlpha(emph ? accent : ink, 0.5 * alpha);
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(lx, ly, 7.5, 0, TAU);
          ctx.fillStyle = withAlpha(paper, 0.95 * alpha);
          ctx.fill();
          ctx.strokeStyle = withAlpha(emph ? accent : ink, 0.8 * alpha);
          ctx.stroke();
          ctx.font = font(8.5, 700);
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillStyle = withAlpha(emph ? accent : ink, alpha);
          ctx.fillText(label, lx, ly + 0.5);
        };
        const layerA = da * (1 - spread);
        if (layerA > 0.05) {
          const lay: Array<[number, string]> = [
            [zTop, "1"],
            [zSrc, "2"],
            [zFrm, "3"],
          ];
          lay.forEach(([z, lab]) => {
            const [x, y] = proj(rimR * 0.98, 0, z);
            const [lx, ly] = proj(rimR * 1.28, 0, z);
            callout(x, y, lab, lx, ly, layerA);
          });
        }
        if (spread > 0.05) {
          let n = 0;
          for (let k = 0; k < rings.length; k++) {
            const rg = rings[k]!;
            if (rg.count === 0 && rg.year !== null) continue;
            n++;
            const [x, y] = proj(-rg.r, 0, ringZ[k]!);
            const [lx, ly] = proj(-rimR - 26, 0, ringZ[k]!);
            callout(
              x,
              y,
              String(n),
              lx,
              ly,
              da * spread * (lensRing < 0 || lensRing === k ? 1 : 0.35),
              lensRing === k,
            );
          }
        }
        if (subSplit > 0.05) {
          groups.forEach((g, gi) => {
            const mid = (g.a0 + g.a1) / 2 + st.rot;
            const [x, y] = proj(
              Math.cos(mid) * rimR * 0.92,
              Math.sin(mid) * rimR * 0.92,
              zTop + gz(gi),
            );
            const [lx, ly] = proj(
              Math.cos(mid) * (rimR + 34),
              Math.sin(mid) * (rimR + 34),
              zTop + gz(gi),
            );
            callout(x, y, String.fromCharCode(65 + gi), lx, ly, da * subSplit);
          });
        }
        if (partsOut > 0.05 && partsArtist >= 0 && tray.size) {
          const pa = da * partsOut;
          const order = artists[partsArtist]!.strand.slice().sort(
            (p, q) => records[p]!.year - records[q]!.year || p - q,
          );
          // header: whose parts, how many
          ctx.textAlign = "left";
          ctx.textBaseline = "alphabetic";
          ctx.font = fontKo(12.5, 600);
          ctx.fillStyle = withAlpha(accent, pa);
          const title = nameOf(partsArtist);
          ctx.fillText(title, trayLeft, trayTop + 2);
          const tw = measure(ctx, title);
          ctx.font = font(10);
          ctx.fillStyle = withAlpha(ink, 0.55 * pa);
          ctx.fillText(
            t(`기록 부품 ${order.length}`, `${order.length} record parts`),
            trayLeft + tw + 10,
            trayTop + 2,
          );
          // year bins
          ctx.font = font(10, 600);
          for (const yb of trayYears) {
            ctx.fillStyle = withAlpha(ink, 0.7 * pa);
            ctx.fillText(String(yb.year), yb.x + 3, trayTop + 24);
            ctx.fillStyle = withAlpha(ink, 0.35 * pa);
            ctx.fillRect(yb.x, trayTop + 28, yb.w, 1);
          }
          // one leader from the tray to the artist's knot on the structure
          const kx = st.kx[partsArtist]!;
          const ky = st.ky[partsArtist]!;
          const ex = trayLeft + trayW + 12;
          ctx.setLineDash([3, 3]);
          ctx.strokeStyle = withAlpha(accent, 0.55 * pa);
          ctx.lineWidth = 0.8;
          ctx.beginPath();
          ctx.moveTo(ex, trayTop + 30);
          ctx.lineTo(ex, trayTop + trayH);
          ctx.moveTo(ex, trayTop + 30 + (trayH - 30) / 2);
          ctx.lineTo(kx, ky);
          ctx.stroke();
          ctx.setLineDash([]);
          // numbered parts, oldest first; the hovered one is filled
          const br = Math.min(8, trayCell * 0.4);
          ctx.font = font(trayCell < 19 ? 7.5 : 8.5, 700);
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          order.forEach((ri, j) => {
            if (ra[ri]! <= 0) return;
            const x = st.sx[ri]!;
            const y = st.sy[ri]!;
            const hot = hov?.kind === "record" && hov.idx === ri;
            ctx.beginPath();
            ctx.arc(x, y, br, 0, TAU);
            ctx.fillStyle = hot ? withAlpha(accent, pa) : withAlpha(paper, 0.96 * pa);
            ctx.fill();
            ctx.lineWidth = 0.8;
            ctx.strokeStyle = withAlpha(accent, 0.8 * pa);
            ctx.stroke();
            ctx.fillStyle = hot ? withAlpha(paper, pa) : withAlpha(accent, pa);
            ctx.fillText(String(j + 1), x, y + 0.5);
          });
        }
      }

      /* -- info card target -- */
      // Only what the reader points at. The page once showed a random record every few seconds
      // while idle; on arrival that read as a selection nobody made, so nothing opens by itself.
      const cardTarget: Hover = hov;

      /* -- info card -- */
      if (cardTarget && cardTarget.kind !== "ring") {
        const lines: Array<[string, number]> = [];
        let px = 0;
        let py = 0;
        if (cardTarget.kind === "record") {
          const nd = records[cardTarget.idx]!;
          const src = recSrc[cardTarget.idx]!;
          const art = data.artists[nd.artist]!;
          lines.push([recordMark(src, lang, 46), 1]);
          lines.push([`${nameOf(nd.artist)} · ${art.id}`, 0.85]);
          lines.push([`${t("수집", "collected")} ${src.collected_at}`, 0.55]);
          lines.push([sources[sourceOf[cardTarget.idx]!]!.domain, 0.45]);
          px = st.sx[cardTarget.idx]!;
          py = st.sy[cardTarget.idx]!;
          ctx.beginPath();
          ctx.arc(px, py, (dotR + 4) * rf[cardTarget.idx]!, 0, TAU);
          ctx.lineWidth = 1;
          ctx.strokeStyle = withAlpha(hov ? accent : ink, 0.6);
          ctx.stroke();
        } else if (cardTarget.kind === "artist") {
          const a = artists[cardTarget.idx]!;
          const art = data.artists[cardTarget.idx]!;
          const g = groups[a.group]!;
          const ver = verLabel(art.verification);
          lines.push([nameOf(cardTarget.idx), 1]);
          lines.push([art.id, 0.6]);
          if (layout.artistProgrammes[cardTarget.idx]!.length) {
            lines.push([
              `${layout.programmesApart ? t("프로그램", "programmes") : t("다른 프로그램", "also in")} · ${layout.artistProgrammes[
                cardTarget.idx
              ]!.map((j) =>
                lang === "ko" ? layout.programmes[j]!.label_ko : layout.programmes[j]!.label_en,
              ).join(" · ")}`,
              0.6,
            ]);
          }
          lines.push([
            `${t("기록", "records")} ${a.sourced} · ${lang === "ko" ? g.label_ko : g.label_en} · ${ver ? (lang === "ko" ? ver[0] : ver[1]) : art.verification}`,
            0.62,
          ]);
          px = st.kx[cardTarget.idx]!;
          py = st.ky[cardTarget.idx]!;
        } else if (cardTarget.kind === "chord") {
          const c = chords[cardTarget.idx]!;
          const ra_ = recSrc[c.a]!;
          const rb_ = recSrc[c.b]!;
          const venue = ra_.venue || rb_.venue || c.venue;
          lines.push([`${t("같은 장소", "Same venue")} · ${truncate(venue, 34)}`, 1]);
          lines.push([`${nameOf(records[c.a]!.artist)} · ${recordMark(ra_, lang)}`, 0.8]);
          lines.push([`${nameOf(records[c.b]!.artist)} · ${recordMark(rb_, lang)}`, 0.8]);
          lines.push([t("클릭하면 상대 작가의 기록 카드", "Click to open the other artist"), 0.5]);
          const ax = st.sx[c.a]!;
          const ay = st.sy[c.a]!;
          const bx2 = st.sx[c.b]!;
          const by2 = st.sy[c.b]!;
          const qx = cx + ((ax + bx2) / 2 - cx) * 0.45;
          const qy = cy + ((ay + by2) / 2 - cy) * 0.45;
          px = 0.25 * ax + 0.5 * qx + 0.25 * bx2;
          py = 0.25 * ay + 0.5 * qy + 0.25 * by2;
        } else if (cardTarget.kind === "source") {
          const sn = sources[cardTarget.idx]!;
          lines.push([sn.domain, 1]);
          lines.push([
            t(
              `기록 ${sn.records.length}건 · 작가 ${sn.artists}명`,
              `${sn.records.length} records · ${sn.artists} artists`,
            ),
            0.7,
          ]);
          px = st.px[cardTarget.idx]!;
          py = st.py[cardTarget.idx]!;
        }
        const pad = 9;
        const lh = 15;
        let width = 0;
        lines.forEach(([text], i) => {
          ctx.font = i === 0 ? fontKo(12.5, 600) : font(10.5);
          width = Math.max(width, measure(ctx, text));
        });
        const bw = width + pad * 2;
        const bh = lines.length * lh + pad * 2 - 2;
        let bx = px + 14;
        let by = py + 12;
        if (bx + bw > w - 12) bx = px - 14 - bw;
        if (by + bh > h - 110) by = py - 12 - bh;
        ctx.fillStyle = withAlpha(paper, 0.94);
        ctx.fillRect(bx, by, bw, bh);
        ctx.lineWidth = 1;
        ctx.strokeStyle = withAlpha(ink, 0.35);
        ctx.strokeRect(bx + 0.5, by + 0.5, bw - 1, bh - 1);
        ctx.beginPath();
        ctx.moveTo(px, py);
        ctx.lineTo(bx + (bx > px ? 0 : bw), by + (by > py ? 0 : bh));
        ctx.strokeStyle = withAlpha(ink, 0.25);
        ctx.stroke();
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        lines.forEach(([text, a], i) => {
          ctx.font = i === 0 ? fontKo(12.5, 600) : font(10.5);
          ctx.fillStyle = withAlpha(ink, a);
          ctx.fillText(text, bx + pad, by + pad + i * lh);
        });
      }

      /* -- hit test -- */
      if (st.pointer.inside && st.gesture === "none") {
        const pxp = st.pointer.x;
        const pyp = st.pointer.y;
        // A pointer that has not moved, over a disc that has not moved, picks the same mark.
        const hitKey = `${pxp}|${pyp}|${st.rot}|${st.zoom}|${tilt}|${spread}|${diagram}|${st.yr0}|${st.yr1}`;
        if (hitKey !== hitHold) {
        hitHold = hitKey;
        let best: Hover = null;
        // nearest record: squared distances (no square root per record), and a box test first
        let bestD = 10;
        const trayOut = tray.size > 0;
        let bestD2 = bestD * bestD;
        for (let ri = 0; ri < records.length; ri++) {
          if (ra[ri]! < 0.999) continue;
          const dx = st.sx[ri]! - pxp;
          if (dx > bestD || dx < -bestD) continue;
          const dy = st.sy[ri]! - pyp;
          if (dy > bestD || dy < -bestD) continue;
          if (trayOut && tray.has(ri)) {
            const d = Math.hypot(dx, dy) - 4;
            if (d < bestD) {
              bestD = d;
              bestD2 = d > 0 ? d * d : 0;
              best = { kind: "record", idx: ri };
            }
            continue;
          }
          const d2 = dx * dx + dy * dy;
          if (d2 < bestD2) {
            bestD2 = d2;
            bestD = Math.sqrt(d2);
            best = { kind: "record", idx: ri };
          }
        }
        let bestK = 12;
        for (let i = 0; i < artists.length; i++) {
          const d = Math.hypot(st.kx[i]! - pxp, st.ky[i]! - pyp);
          if (d < bestK) {
            bestK = d;
            best = { kind: "artist", idx: i };
          }
        }
        if (e > 0.3) {
          for (let i = 0; i < nS; i++) {
            const d = Math.hypot(st.px[i]! - pxp, st.py[i]! - pyp);
            if (d < sources[i]!.size * Math.sqrt(st.zoom) * ssf[i]! + 4) {
              best = { kind: "source", idx: i };
              break;
            }
          }
        }
        if (!best) {
          for (const rl of st.ringLabel) {
            if (pxp >= rl.x && pxp <= rl.x + rl.w && pyp >= rl.y && pyp <= rl.y + rl.h) {
              best = { kind: "ring", idx: rl.k };
              break;
            }
          }
        }
        if (!best && st.links.chords.length && diagram < 0.3) {
          let bestC = 5;
          for (const ci of st.links.chords) {
            const c = chords[ci]!;
            if (ra[c.a]! <= 0 || ra[c.b]! <= 0) continue;
            const ax = st.sx[c.a]!;
            const ay = st.sy[c.a]!;
            const bx = st.sx[c.b]!;
            const by = st.sy[c.b]!;
            const qx = cx + ((ax + bx) / 2 - cx) * 0.45;
            const qy = cy + ((ay + by) / 2 - cy) * 0.45;
            for (let k = 1; k < 16; k++) {
              const u = k / 16;
              const x = (1 - u) * (1 - u) * ax + 2 * (1 - u) * u * qx + u * u * bx;
              const y = (1 - u) * (1 - u) * ay + 2 * (1 - u) * u * qy + u * u * by;
              const d = Math.hypot(x - pxp, y - pyp);
              if (d < bestC) {
                bestC = d;
                best = { kind: "chord", idx: ci };
              }
            }
          }
        }
        st.hover = best;
        const nb = st.nameBox;
        const overName =
          !!nb && pxp >= nb.x && pxp <= nb.x + nb.w && pyp >= nb.y && pyp <= nb.y + nb.h;
        canvas.style.cursor = overName || (best && best.kind !== "ring") ? "pointer" : "grab";
        }
      } else if (!st.pointer.inside) {
        st.hover = null;
        hitHold = "";
      }

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);

    const hudTimer = window.setInterval(() => {
      const n = Math.min(st.revealCount, total);
      const lastRec = n > 0 ? recSrc[revealOrder[n - 1]!]!.collected_at : "";
      setHud((h) => {
        const next = {
          revealed: n,
          date: lastRec,
          done: st.asmDone,
          hint: st.lastInput === 0 && performance.now() - st.asmStart < 15000,
          open: st.tilt > 0.35,
          spread: st.spread > 0.5,
          yr0: st.yr0,
          yr1: st.yr1,
          tick: st.tickOn,
          music: !!st.music?.running,
          cell: st.music?.running
            ? (st.music.conductor.cells[st.music.conductor.minCell]?.label ?? "")
            : "",
          versions: st.versions,
          stage: st.stage,
          parts: st.partsArtist,
          zoom: Math.round(st.zoom * 100) / 100,
        };
        const same = (Object.keys(next) as Array<keyof typeof next>).every((k) => h[k] === next[k]);
        return same ? h : next;
      });
    }, 120);

    return () => {
      cancelAnimationFrame(raf);
      window.clearInterval(hudTimer);
      st.music?.stop();
      st.music = null;
      ro.disconnect();
      mq.removeEventListener?.("change", onTheme);
      mo.disconnect();
    };
  }, [layout, strata, data, lang, nameOf, reduced, st, t, total, lastRing, newSince, toggleMusic]);

  /* ---------------------------------------------------------------- */
  /* pointer                                                           */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const center = () => {
      const r = canvas.getBoundingClientRect();
      return { x: r.left + st.geo.cx, y: r.top + st.geo.cy, left: r.left, top: r.top };
    };
    /** pointer position in the disc's own plane (undoing screen rotation and tilt), in rim radii.
        P4. Perspective is not inverted: f stays positive, so roll and dial keep their direction. */
    const discLocal = (x: number, y: number) => {
      const g = st.geo;
      const c = center();
      const dx = x - c.x;
      const dy = y - c.y;
      const ux = dx * g.rc + dy * g.rs;
      const uy = (-dx * g.rs + dy * g.rc) / Math.max(0.2, g.cosT);
      return { ux: ux / g.rimPx, uy: uy / g.rimPx };
    };
    const angleAt = (x: number, y: number) => {
      const { ux, uy } = discLocal(x, y);
      return Math.atan2(uy, ux);
    };
    /** laid open (layers, plates, parts): the disc turns like a roller under any drag */
    const rolling = () => st.geo.diagram > 0.3;

    const onDown = (e: PointerEvent) => {
      canvas.setPointerCapture?.(e.pointerId);
      st.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      st.lastInput = performance.now();
      if (st.pointers.size === 1) {
        st.gesture = "none";
        st.gx = e.clientX;
        st.gy = e.clientY;
        st.lastAngle = angleAt(e.clientX, e.clientY);
        st.lastX = e.clientX;
        st.lastY = e.clientY;
        st.lastT = performance.now();
        st.targetRot = null;
        if (!rolling()) st.targetTilt = null;
        // Outside the rim of the upright disc: the time dial.
        const { ux, uy } = discLocal(e.clientX, e.clientY);
        if (st.asmDone && !rolling() && Math.hypot(ux, uy) > 1.06) st.gesture = "dial";
      } else if (st.pointers.size === 2) {
        const [p, q] = [...st.pointers.values()];
        st.pinchDist = Math.hypot(p!.x - q!.x, p!.y - q!.y);
        st.pinchZoom = st.zoom;
        st.gesture = "rotate";
      }
    };
    const onMove = (e: PointerEvent) => {
      const c = center();
      st.pointer = { x: e.clientX - c.left, y: e.clientY - c.top, inside: true, shift: e.shiftKey };
      st.pointerMovedAt = performance.now();
      if (!st.pointers.has(e.pointerId)) return;
      st.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (st.pointers.size >= 2) {
        const [p, q] = [...st.pointers.values()];
        const d = Math.hypot(p!.x - q!.x, p!.y - q!.y);
        if (st.pinchDist > 0) st.zoom = clamp(st.pinchZoom * (d / st.pinchDist), 0.3, 5);
        return;
      }
      if (st.gesture === "none") {
        const dx = e.clientX - st.gx;
        const dy = e.clientY - st.gy;
        if (Math.hypot(dx, dy) < 6) return;
        if (rolling()) st.gesture = "roll";
        else {
          const vertical = Math.abs(dy) > Math.abs(dx) * 1.2;
          st.gesture = vertical ? (e.shiftKey ? "spread" : "tilt") : "rotate";
          if (vertical) st.userTilt = true;
        }
        canvas.style.cursor = "grabbing";
      }
      const now = performance.now();
      if (st.gesture === "roll") {
        // the rim point under the finger follows it: drag across the axis turns the disc,
        // drag along the axis turns it too (half as fast) so no direction is dead.
        // P4. This stays on the screen axes in st.geo. f is clamped above 0, so the
        // perspective scale does not reverse a drag, a roll, or the dial.
        const g = st.geo;
        const ddx = e.clientX - st.lastX;
        const ddy = e.clientY - st.lastY;
        const across = ddx * g.rc + ddy * g.rs;
        const along = ddx * g.rs - ddy * g.rc;
        const dA = -(across + 0.5 * along) / Math.max(40, g.rimPx);
        const dtR = Math.max(1e-3, (now - st.lastT) / 1000);
        st.rot += dA;
        st.vel = dA / dtR;
        st.lastTurnAt = now;
        st.lastX = e.clientX;
        st.lastY = e.clientY;
        st.lastT = now;
        return;
      }
      if (st.gesture === "tilt") {
        if (!st.asmDone) return;
        st.tilt = clamp(st.tilt + (e.clientY - st.lastY) * 0.0045, 0, 1.0);
        st.lastY = e.clientY;
        return;
      }
      if (st.gesture === "spread") {
        st.targetSpread = null;
        st.spread = clamp(st.spread + (e.clientY - st.lastY) * -0.004, 0, 1);
        if (st.spread > 0.05 && st.tilt < 0.45) st.targetTilt = 0.6;
        st.lastY = e.clientY;
        return;
      }
      const a = angleAt(e.clientX, e.clientY);
      const dA = shortAngle(a - st.lastAngle);
      const dt = Math.max(1e-3, (now - st.lastT) / 1000);
      if (st.gesture === "dial") {
        st.windAcc += (dA / TAU) * layout.rings.length;
        const steps = Math.trunc(st.windAcc);
        if (steps !== 0) {
          st.windAcc -= steps;
          st.yr1 = clamp(st.yr1 + steps, st.yr0, layout.rings.length - 1);
        }
      } else {
        st.rot += dA;
        st.vel = dA / dt;
        st.lastTurnAt = now;
      }
      st.lastAngle = a;
      st.lastT = now;
    };
    const onUp = (e: PointerEvent) => {
      // pointer-ups that did not start on the canvas (buttons, the record sheet) are not ours
      if (!st.pointers.delete(e.pointerId)) return;
      try {
        canvas.releasePointerCapture?.(e.pointerId);
      } catch {
        /* not captured */
      }
      if (st.pointers.size > 0) return;
      const wasClick =
        st.gesture === "none" ||
        (st.gesture === "dial" && Math.hypot(e.clientX - st.gx, e.clientY - st.gy) < 6);
      st.gesture = "none";
      canvas.style.cursor = "grab";
      if (wasClick && st.nameBox && st.focus >= 0) {
        const nb = st.nameBox;
        const box = canvas.getBoundingClientRect();
        const px = e.clientX - box.left;
        const py = e.clientY - box.top;
        if (px >= nb.x && px <= nb.x + nb.w && py >= nb.y && py <= nb.y + nb.h) {
          openSheet(st.focus);
          return;
        }
      }
      if (wasClick && !st.hover) st.focus = -1; // a click on open paper releases the selection
      if (wasClick && st.hover) {
        if (st.hover.kind === "source") {
          const sn = strata.sources[st.hover.idx]!;
          say({
            role: "guide",
            text: t(
              `${sn.domain} — 이 출처 위에 기록 ${sn.records.length}건, 작가 ${sn.artists}명이 서 있습니다.`,
              `${sn.domain} — ${sn.records.length} records by ${sn.artists} artists stand on this source.`,
            ),
          });
          return;
        }
        if (st.hover.kind === "ring") return;
        if (st.hover.kind === "chord") {
          const c = layout.chords[st.hover.idx]!;
          const aa = layout.records[c.a]!.artist;
          const ab = layout.records[c.b]!.artist;
          openSheet(aa === st.focus ? ab : aa);
          return;
        }
        const artistIdx =
          st.hover.kind === "artist" ? st.hover.idx : layout.records[st.hover.idx]!.artist;
        const ord =
          st.hover.kind === "record"
            ? data.records[srcIndex(layout.records[st.hover.idx]!)]!.ord
            : null;
        openSheet(artistIdx, ord);
      }
      if (Math.abs(st.vel) > 3) st.vel = Math.sign(st.vel) * 3;
    };
    const onLeave = () => {
      st.pointer = { x: -1, y: -1, inside: false, shift: false };
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      st.lastInput = performance.now();
      st.targetZoom = null;
      st.targetPan = null;
      st.userZoomed = true;
      const before = st.zoom;
      st.zoom = clamp(st.zoom * Math.exp(-e.deltaY * 0.0012), 0.3, 5);
      const k = st.zoom / before;
      const c = center();
      st.panX += (e.clientX - c.x) * (1 - k);
      st.panY += (e.clientY - c.y) * (1 - k);
    };
    const reset = () => {
      st.targetZoom = 1;
      st.targetRot = 0;
      st.targetTilt = 0;
      st.targetSpread = 0;
      st.targetPan = { x: 0, y: 0 };
      st.yr0 = 0;
      st.yr1 = lastRing;
      st.focus = -1;
      st.stage = 0;
    };
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      const k = e.key.toLowerCase();
      if (k === "arrowleft") st.vel = 0.6;
      else if (k === "arrowright") st.vel = -0.6;
      else if (k === "arrowdown") {
        st.targetTilt = clamp(st.tilt + 0.3, 0, 1.0);
        st.userTilt = true;
      } else if (k === "arrowup") {
        st.targetTilt = clamp(st.tilt - 0.3, 0, 1.0);
        st.userTilt = true;
      } else if (k === "+" || k === "=") st.zoom = clamp(st.zoom * 1.15, 0.7, 5);
      else if (k === "-") st.zoom = clamp(st.zoom / 1.15, 0.7, 5);
      else if (k === "x") setStage(st.stage >= 1 ? 0 : 1);
      else if (k === "y") setStage(st.stage >= 2 ? 1 : 2);
      else if (k === "]") setStage(st.stage + 1);
      else if (k === "[") setStage(st.stage - 1);
      else if (/^[0-4]$/.test(k)) setStage(Number(k));
      else if (k === "n") st.needleOn = !st.needleOn;
      else if (k === "m") toggleMusic();
      else if (k === "r") reassemble();
      else if (k === "escape") {
        if (sheetRef.current) setSheet(null);
        else reset();
      } else if (k === "/") {
        e.preventDefault();
        document.getElementById("study-q")?.focus();
      } else return;
      e.preventDefault();
      st.lastInput = performance.now();
    };
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("dblclick", reset);
    window.addEventListener("keydown", onKey);
    return () => {
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("dblclick", reset);
      window.removeEventListener("keydown", onKey);
    };
  }, [
    layout,
    lastRing,
    strata,
    st,
    say,
    t,
    openStack,
    openYears,
    reassemble,
    total,
    setStage,
    toggleMusic,
    openSheet,
    data,
  ]);

  // play bar: the activity period, one slot per year ring; a histogram shows where the records are
  const yearSlots = useMemo(() => {
    const counts = layout.rings.map(() => 0);
    for (const nd of layout.records) counts[nd.ring]! += 1;
    // scale to the single years; the "≤2016" slot gathers decades and is capped at full height
    const max = Math.max(1, ...counts.slice(1));
    return layout.rings.map((r, k) => ({
      label: r.label,
      count: counts[k]!,
      h: Math.min(1, counts[k]! / max),
    }));
  }, [layout]);
  const periodFull = hud.yr0 === 0 && hud.yr1 === lastRing;
  const periodLabel =
    hud.yr0 === hud.yr1
      ? layout.rings[hud.yr0]!.label
      : `${layout.rings[hud.yr0]!.label}–${layout.rings[hud.yr1]!.label}`;
  const setPeriod = (a: number, b: number) => {
    const y0 = clamp(Math.min(a, b), 0, lastRing);
    const y1 = clamp(Math.max(a, b), 0, lastRing);
    st.yr0 = y0;
    st.yr1 = y1;
    st.lastInput = performance.now();
    setHud((h) => ({ ...h, yr0: y0, yr1: y1 }));
  };
  const periodDrag = useRef<{ handle: 0 | 1; el: HTMLElement } | null>(null);
  const slotAt = (el: HTMLElement, clientX: number) => {
    const r = el.getBoundingClientRect();
    return clamp(
      Math.floor(((clientX - r.left) / Math.max(1, r.width)) * (lastRing + 1)),
      0,
      lastRing,
    );
  };
  const btn =
    "grid size-6 place-items-center border border-input text-[11px] leading-none text-foreground/75 transition hover:border-primary hover:text-primary disabled:opacity-30 aria-pressed:bg-foreground aria-pressed:text-background";

  return (
    <div
      ref={wrapRef}
      className="relative h-dvh min-h-[34rem] w-full select-none overflow-hidden bg-background"
    >
      <canvas
        ref={canvasRef}
        className="block h-full w-full touch-none"
        aria-label={t("기록 연구 시각화", "Archival study visualisation")}
      />

      {sheet ? (
        <RecordSheet
          key={sheet.artist}
          id={layout.artists[sheet.artist]!.id}
          frameKo={layout.groups[layout.artists[sheet.artist]!.group]!.label_ko}
          frameEn={layout.groups[layout.artists[sheet.artist]!.group]!.label_en}
          preview={sheetPreview!}
          highlightOrd={sheet.ord}
          stamp={data.stamp}
          partners={sheetPartners}
          placement={hud.stage >= 2 ? "float" : "side"}
          legend={
            linkStats && hud.stage < 2 ? (
              <LinkLegend
                stats={linkStats}
                stage={hud.stage}
                t={t}
                onFocus={onLegendFocus}
                onOpen={openSheet}
              />
            ) : undefined
          }
          onClose={closeSheet}
        />
      ) : null}

      {citeNotice ? (
        <div
          key={citeNotice.id}
          aria-hidden="true"
          onAnimationEnd={() => setCiteNotice(null)}
          className="cite-notice pointer-events-none absolute left-1/2 top-1/2 z-30 max-w-[min(26rem,calc(100%-2rem))] break-words border border-foreground bg-background px-6 py-3 text-center font-mono text-[15px] leading-6 text-foreground shadow-[0_6px_24px_rgba(0,0,0,0.12)]"
        >
          {citeNotice.text}
        </div>
      ) : null}

      {/* Parts list — only while the diagram is open */}
      {hud.stage >= 1 && hud.done ? (
        <aside
          className={`pointer-events-auto absolute left-5 top-16 z-10 hidden w-64 overflow-auto font-mono text-[10.5px] leading-5 text-foreground/80 md:block ${sheet && hud.stage >= 2 ? "max-h-[34vh]" : "max-h-[66vh]"}`}
          aria-label={t("부품표", "Parts list")}
        >
          {linkStats && hud.stage >= 2 ? (
            <div className="mb-3">
              <LinkLegend
                stats={linkStats}
                stage={hud.stage}
                t={t}
                onFocus={onLegendFocus}
                onOpen={openSheet}
              />
            </div>
          ) : null}
          <p className="mb-1 border-b border-foreground/30 pb-1 text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground">
            {t("부품표", "Parts list")} · {STAGE_NAMES[hud.stage]![lang === "ko" ? 0 : 1]}
          </p>
          <PartsList
            stage={hud.stage}
            layout={layout}
            strata={strata}
            data={data}
            parts={hud.parts}
            picked={pickedNode}
            lang={lang}
            t={t}
            nameOf={nameOf}
          />
        </aside>
      ) : null}

      <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-[26px] pb-[112px] sm:pb-[72px]">
        {/* top: wordmark · language · menu */}
        <div className="relative">
          <div className="flex items-start justify-between">
            <div className="pointer-events-auto flex items-center gap-3">
              <p className="whitespace-nowrap font-mono text-[10px] uppercase tracking-[0.22em] text-foreground/70">
                기예 Giye
              </p>
              {modeSwitch}
            </div>
            <div
              className="pointer-events-auto flex items-center gap-3 whitespace-nowrap font-mono text-[10px] text-muted-foreground"
              onMouseEnter={() => openMenu(true)}
              onMouseLeave={() => openMenu(false)}
            >
              <nav
                aria-label={t("사이트 메뉴", "Site navigation")}
                aria-hidden={menu === "closed"}
                className={`flex flex-wrap justify-end gap-x-3 gap-y-1 transition-opacity duration-300 ${
                  menu === "closed" ? "opacity-0" : "opacity-100"
                } ${menu === "open" ? "" : "pointer-events-none"}`}
              >
                {SITE_NAV.map((n) => (
                  <Link
                    key={n.to}
                    to={n.to}
                    tabIndex={menu === "open" ? 0 : -1}
                    className="text-foreground/70 no-underline underline-offset-2 hover:text-primary hover:underline"
                  >
                    {t(n.ko, n.en)}
                  </Link>
                ))}
              </nav>
              <button
                type="button"
                className="text-foreground/60"
                onClick={() => openMenu(menu === "closed")}
                aria-expanded={menu !== "closed"}
                aria-label={t("메뉴 열기", "Open menu")}
              >
                {t("메뉴", "menu")}
              </button>
              <button
                type="button"
                onClick={() => setLang(lang === "ko" ? "en" : "ko")}
                className="border border-input px-1.5 py-0.5 text-[10px] text-foreground/70 transition hover:border-primary hover:text-primary"
                aria-label={t("언어 전환", "Switch language")}
              >
                {lang === "ko" ? "EN" : "KO"}
              </button>
            </div>
          </div>
          <div className="pointer-events-auto mt-3 w-full sm:absolute sm:left-1/2 sm:top-[-7px] sm:mt-0 sm:w-[min(24rem,34vw)] sm:-translate-x-1/2">
            <StudySearch
              data={data}
              layout={layout}
              lang={lang}
              t={t}
              nameOf={nameOf}
              onArtist={(i) => focusArtist(i, true)}
              onRecord={(i, ord) => openSheet(i, ord)}
              onFrame={focusFrame}
            />
          </div>
        </div>

        {/* bottom: gesture hint, tools at the right edge */}
        <div className="flex flex-wrap items-end justify-between gap-3 sm:flex-nowrap sm:gap-4">
          <p
            className={`pointer-events-none hidden max-w-[16rem] font-mono text-[9.5px] leading-4 text-muted-foreground transition-opacity duration-1000 sm:block ${hud.hint ? "opacity-100" : "opacity-0"}`}
          >
            {t(
              "가로로 끌면 읽고 · 세로로 끌면 층이 열립니다 · ] [ 분해 단계 · 테두리 밖은 시간",
              "Drag sideways to read · down to open layers · ] [ stages · outside the rim winds time",
            )}
          </p>

          {/* guide replies reach screen readers only; nothing is drawn around the search */}
          <p className="sr-only" aria-live="polite">
            {log
              .filter((m) => m.role === "guide")
              .slice(-1)
              .map((m) =>
                m.chips?.length ? `${m.text} ${m.chips.map(nameOf).join(", ")}` : m.text,
              )}
          </p>

          {/* play bar — the sheet's title block laid out along the bottom edge */}
          <div className="pointer-events-auto absolute inset-x-4 bottom-4 border-t border-foreground/40 bg-background/80 font-mono text-[10px] text-foreground/80 backdrop-blur-[2px]">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-2.5 py-2 sm:flex-nowrap sm:gap-x-4">
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setStage(hud.stage - 1)}
                  className={btn}
                  aria-label={t("한 단계 조립", "Assemble one step")}
                  title={t("조립 [", "Assemble [")}
                  disabled={hud.stage === 0}
                >
                  ◀
                </button>
                <button
                  type="button"
                  onClick={() => setStage(hud.stage + 1)}
                  className={btn}
                  aria-label={t("한 단계 분해", "Explode one step")}
                  title={t("분해 ]", "Explode ]")}
                  disabled={hud.stage === STAGES}
                >
                  ▶
                </button>
                <button
                  type="button"
                  onClick={reassemble}
                  className={btn}
                  aria-label={t("다시 조립", "Reassemble")}
                  title={t("다시 조립 R", "Reassemble R")}
                >
                  ↻
                </button>
              </div>

              <span className="w-[9rem] shrink-0 truncate whitespace-nowrap tabular-nums text-foreground/70">
                {hud.done
                  ? hud.music
                    ? t(`합주 · ${hud.cell}`, `ensemble · ${hud.cell}`)
                    : `${STAGE_NAMES[hud.stage]![lang === "ko" ? 0 : 1]} ${hud.stage}/${STAGES}`
                  : t("조립 중", "assembling")}
              </span>

              {/* period: which years of activity are drawn — drag either end, or click a year */}
              <div className="order-first flex w-full min-w-0 flex-col gap-1 sm:order-none sm:flex-1 sm:flex-row sm:items-center sm:gap-3">
                <div
                  className={`relative h-9 w-full min-w-0 touch-none select-none sm:w-auto sm:flex-1 ${hud.done ? "cursor-pointer" : "opacity-60"}`}
                  onPointerDown={(e) => {
                    if (!hud.done) return;
                    const el = e.currentTarget;
                    const k = slotAt(el, e.clientX);
                    // the nearer end follows the pointer; inside the range the closer edge wins
                    const handle: 0 | 1 =
                      k < hud.yr0 ? 0 : k > hud.yr1 ? 1 : k - hud.yr0 <= hud.yr1 - k ? 0 : 1;
                    periodDrag.current = { handle, el };
                    el.setPointerCapture(e.pointerId);
                    if (handle === 0) setPeriod(k, hud.yr1);
                    else setPeriod(hud.yr0, k);
                  }}
                  onPointerMove={(e) => {
                    const d = periodDrag.current;
                    if (!d) return;
                    const k = slotAt(d.el, e.clientX);
                    if (d.handle === 0) setPeriod(Math.min(k, st.yr1), st.yr1);
                    else setPeriod(st.yr0, Math.max(k, st.yr0));
                  }}
                  onPointerUp={() => (periodDrag.current = null)}
                  onPointerCancel={() => (periodDrag.current = null)}
                  onDoubleClick={() => setPeriod(0, lastRing)}
                >
                  {/* records per year */}
                  {yearSlots.map((y, k) => (
                    <div
                      key={`h${y.label}`}
                      aria-hidden="true"
                      className={`absolute bottom-[17px] ${k >= hud.yr0 && k <= hud.yr1 ? "bg-foreground/35" : "bg-foreground/10"}`}
                      style={{
                        left: `calc(${(k / (lastRing + 1)) * 100}% + 2px)`,
                        width: `calc(${100 / (lastRing + 1)}% - 4px)`,
                        height: `${Math.max(y.count ? 1 : 0, Math.round(y.h * 14))}px`,
                      }}
                    />
                  ))}
                  <div className="absolute inset-x-0 bottom-[16px] h-px bg-foreground/30" />
                  <div
                    aria-hidden="true"
                    className="absolute bottom-[15px] h-[3px] bg-foreground"
                    style={{
                      left: `${(hud.yr0 / (lastRing + 1)) * 100}%`,
                      width: `${((hud.yr1 - hud.yr0 + 1) / (lastRing + 1)) * 100}%`,
                    }}
                  />
                  {/* year ticks and labels */}
                  {yearSlots.map((y, k) => (
                    <div
                      key={`t${y.label}`}
                      aria-hidden="true"
                      className="absolute bottom-[12px] h-[5px] w-px bg-foreground/45"
                      style={{ left: `${(k / (lastRing + 1)) * 100}%` }}
                    />
                  ))}
                  <div
                    aria-hidden="true"
                    className="absolute bottom-[12px] right-0 h-[5px] w-px bg-foreground/45"
                  />
                  {yearSlots.map((y, k) => (
                    <span
                      key={`l${y.label}`}
                      aria-hidden="true"
                      className={`absolute bottom-0 -translate-x-1/2 whitespace-nowrap text-[8.5px] leading-none tabular-nums ${
                        k >= hud.yr0 && k <= hud.yr1
                          ? "text-foreground/80"
                          : "text-muted-foreground/60"
                      } ${k === 0 || k === lastRing || k === hud.yr0 || k === hud.yr1 ? "" : "hidden md:inline"}`}
                      style={{ left: `${((k + 0.5) / (lastRing + 1)) * 100}%` }}
                    >
                      {y.label.length === 4 && k !== 0 && k !== lastRing
                        ? `'${y.label.slice(2)}`
                        : y.label}
                    </span>
                  ))}
                  {/* the two ends, as sliders for the keyboard */}
                  {([0, 1] as const).map((h) => {
                    const k = h === 0 ? hud.yr0 : hud.yr1;
                    return (
                      <button
                        key={`e${h}`}
                        type="button"
                        role="slider"
                        disabled={!hud.done}
                        aria-label={
                          h === 0 ? t("기간 시작", "Period start") : t("기간 끝", "Period end")
                        }
                        aria-valuemin={0}
                        aria-valuemax={lastRing}
                        aria-valuenow={k}
                        aria-valuetext={layout.rings[k]!.label}
                        onPointerDown={(e) => e.preventDefault()}
                        onKeyDown={(e) => {
                          const dk =
                            e.key === "ArrowLeft" || e.key === "ArrowDown"
                              ? -1
                              : e.key === "ArrowRight" || e.key === "ArrowUp"
                                ? 1
                                : 0;
                          if (!dk) return;
                          e.preventDefault();
                          e.stopPropagation();
                          if (h === 0) setPeriod(Math.min(hud.yr0 + dk, hud.yr1), hud.yr1);
                          else setPeriod(hud.yr0, Math.max(hud.yr1 + dk, hud.yr0));
                        }}
                        className="absolute bottom-[10px] h-[15px] w-[7px] -translate-x-1/2 border border-foreground bg-background focus-visible:outline-2"
                        style={{ left: `${((h === 0 ? k : k + 1) / (lastRing + 1)) * 100}%` }}
                      />
                    );
                  })}
                </div>
                {/* fixed-width readout: changing numbers never move the track or the buttons */}
                <span className="flex shrink-0 items-baseline whitespace-nowrap tabular-nums">
                  <span className="text-muted-foreground">{t("기간", "period")}</span>
                  <span className="ml-1.5 inline-block w-[6.5rem]">{periodLabel}</span>
                  <span className="inline-block w-[6.5rem] text-muted-foreground">
                    {t(
                      `기록 ${Math.min(hud.revealed, total)}`,
                      `${Math.min(hud.revealed, total)} records`,
                    )}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPeriod(0, lastRing)}
                    tabIndex={!periodFull && hud.done ? 0 : -1}
                    aria-hidden={periodFull || !hud.done}
                    className={`inline-block w-[2rem] text-left text-muted-foreground underline underline-offset-2 hover:text-primary ${
                      !periodFull && hud.done ? "" : "invisible"
                    }`}
                  >
                    {t("전체", "all")}
                  </button>
                </span>
              </div>

              <span className="hidden w-[6.5rem] shrink-0 whitespace-nowrap text-right tabular-nums text-muted-foreground md:inline">
                {hud.zoom.toFixed(2)}× · v{data.version}
              </span>

              <div className="ml-auto flex items-center gap-1 sm:ml-0">
                <button
                  type="button"
                  onClick={toggleMusic}
                  className={btn}
                  aria-pressed={hud.music}
                  aria-label={t("원장 합주", "Ledger ensemble")}
                  title={t("원장 합주 (M)", "Ledger ensemble (M)")}
                >
                  ♪
                </button>
                <button
                  type="button"
                  onClick={() => void copyView()}
                  className={btn}
                  aria-label={t("이 시점 인용", "Cite this view")}
                  title={t("이 시점 인용", "Cite this view")}
                >
                  ❝
                </button>
                {prevSnapshot ? (
                  <button
                    type="button"
                    onClick={() => {
                      st.versions = !st.versions;
                      if (st.versions && newSince)
                        say({
                          role: "guide",
                          text: t(
                            `v${prevSnapshot.version} (${prevSnapshot.released_at}) 이후 새로 들어온 기록 ${newSince.size}건에 테를 둘렀습니다.`,
                            `${newSince.size} records added since v${prevSnapshot.version} (${prevSnapshot.released_at}) are ringed.`,
                          ),
                        });
                    }}
                    className={btn}
                    aria-pressed={hud.versions}
                    aria-label={t("판본 비교", "Compare versions")}
                    title={t(
                      `판본 v${prevSnapshot.version} 대비`,
                      `Since v${prevSnapshot.version}`,
                    )}
                  >
                    Δ
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
