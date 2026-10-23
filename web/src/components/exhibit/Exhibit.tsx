// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Exhibit — the archive as an installation (route /exhibit).
 *
 * The home study (components/study/ArchivalStudy) is the stage: the exhibit drives it through a
 * StudyDirector and draws nothing of its own over it except one word at the start of a scene.
 * Scenes loop on their own, so the page can run unattended on a projector:
 *   1. records — in the site's own paper tone, the study assembles and unfolds through all its
 *                views (RECORD_CUES). The artists near the needle play "In C"
 *                (conductor.ts / ensemble.ts).
 *   2. tech    — the room goes to the night palette, one technology at a time (lib/tech.ts, W4):
 *                the records of people whose titles name it are ringed from their first such
 *                year on, appearing as the period grows; only those people play, each from that
 *                year. Then the shaft splits the ties among them in two (TIES, below): ties made
 *                before both had named it bend under the axis, ties made after bend over it;
 *                then the parts of two of them (TECH_CUES).
 * No person is ranked or sized by their records. `?notes` adds the count behind a technology
 * scene (computed in the browser) for documentation.
 *
 * Input: a click or a key starts the sound (browsers need a gesture); dragging turns the study as
 * at home. The buttons and the arrow keys jump between scenes; after a minute without input the
 * loop resumes. `?kiosk` hides the pointer and the buttons; `?scene=<n>&at=<0..1>` opens a point
 * of one scene, and `&hold` keeps it there (for stills).
 */
import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArchivalStudy,
  type StudyDirector,
  type StudyTies,
} from "@/components/study/ArchivalStudy";
import {
  buildLayout,
  GENERIC_VENUES,
  srcIndex,
  type Layout,
  type StudyData,
} from "@/components/study/model";
import { foldVenue } from "@/lib/record-text";
import { useLang } from "@/lib/i18n";
import { TECH_CODES, TECH_LABEL, type TechCode } from "@/lib/tech";

const clamp = (v: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, v));

/* Scene lengths (seconds). One loop: the records, then the six technologies. */
const RECORDS_SEC = 150;
const TECH_SEC = 48;
type Scene = { kind: "records" } | { kind: "tech"; tech: TechCode };
const LOOP: Scene[] = [
  { kind: "records" },
  ...TECH_CODES.map((tech) => ({ kind: "tech" as const, tech })),
];
const lengthOf = (s: Scene) => (s.kind === "records" ? RECORDS_SEC : TECH_SEC);
const IDLE_RESUME_MS = 60_000;
/** The steady turn of the structure (radians per second), stopped while parts are out. */
const SPIN = 0.02;

type Api = NonNullable<StudyDirector["api"]>;
/** A cue runs once when the scene reaches `at` (a fraction of its length). */
type Cue = {
  at: number;
  run: (
    api: Api,
    part: (k: number) => number | null,
    /** this technology's ties, with the sides asked for shown (tech scenes) */
    tie: (below: boolean, above: boolean) => StudyTies | null,
  ) => void;
};
/** A span over which the shown period grows from the first year ring to the last. */
type Grow = { from: number; to: number };

/**
 * The records scene uses every view the study has, in the order the home's stage list gives:
 * the assembly (records fly in in the order they were collected), the years opening while the
 * period grows one ring at a time, the exploded layers, the year plates, the generation
 * segments, and the record parts of one artist after another; then it closes.
 */
const RECORD_CUES: Cue[] = [
  { at: 0, run: (a) => (a.reassemble(), a.setSpin(SPIN)) },
  { at: 0.07, run: (a) => (a.setPeriod(0, 0), a.openYears(true)) },
  { at: 0.26, run: (a) => (a.setPeriod(0, a.rings - 1), a.setStage(1)) },
  { at: 0.4, run: (a) => a.setStage(2) },
  { at: 0.54, run: (a) => a.setStage(3) },
  {
    at: 0.68,
    run: (a, part) => (a.setSpin(0), a.setStage(4), a.focus(part(0))),
  },
  { at: 0.76, run: (a, part) => a.focus(part(1)) },
  { at: 0.84, run: (a, part) => a.focus(part(2)) },
  {
    at: 0.92,
    run: (a) => (
      a.focus(null),
      a.setStage(0),
      a.openStack(false),
      a.setSpin(SPIN)
    ),
  },
];
const RECORD_GROW: Grow = { from: 0.08, to: 0.24 };

/**
 * A technology scene: the years open with the period at the first ring and grow, so the rings
 * of that technology appear year by year; then the year plates; then the parts of two of the
 * people who named it.
 */
const TECH_CUES: Cue[] = [
  {
    at: 0,
    run: (a) => (
      a.focus(null),
      a.setStage(0),
      a.setPeriod(0, 0),
      a.openYears(true),
      a.setSpin(SPIN)
    ),
  },
  { at: 0.42, run: (a) => (a.setPeriod(0, a.rings - 1), a.setStage(2)) },
  { at: 0.47, run: (a, _p, tie) => a.setTies(tie(true, false)) },
  { at: 0.55, run: (a, _p, tie) => a.setTies(tie(true, true)) },
  {
    at: 0.66,
    run: (a, part) => (
      a.setTies(null),
      a.setSpin(0),
      a.setStage(4),
      a.focus(part(0))
    ),
  },
  { at: 0.83, run: (a, part) => a.focus(part(1)) },
];
const TECH_GROW: Grow = { from: 0.03, to: 0.4 };

/**
 * How much more often than other pairs the adopters of a technology formed a new tie, from the
 * technology-diffusion study (year-stratified Mantel–Haenszel odds ratios against pairs where
 * neither ever adopts; all ties): `before` — both adopt it, but only later than the tie;
 * `after` — both had adopted it before the tie. An odds ratio near 1 is no closer than anyone.
 * Reason: the page cannot run that comparison (it needs every pair of active artists, every
 * year); it draws real ties among the adopters and lets the study's number set their strength.
 */
const TIE_ODDS: Record<TechCode, { before: number; after: number }> = {
  ai: { before: 1.03, after: 1.51 },
  xr: { before: 1.0, after: 2.15 },
  data: { before: 1.73, after: 1.59 },
  bio: { before: 1.37, after: 1.15 },
  robot: { before: 1.66, after: 1.44 },
  nft: { before: 1.71, after: 1.32 },
};
/** Arc strength for an odds ratio: about invisible at 1, full near the strongest (XR after). */
const tieAlpha = (or: number) => 0.04 + 0.56 * clamp((or - 1) / 1.15, 0, 1);
/** Each side draws the same number of ties, so a side differs only by its strength. */
const TIE_SAMPLE = 180;

/** Parts need a few records to read as parts; below this a strand is too short to pull out. */
const MIN_PARTS = 4;
/**
 * Whose parts are shown: a golden-ratio walk over the eligible people in rim order, advanced
 * by the loop count, so every loop shows others. Record counts only gate (MIN_PARTS); they do
 * not rank — no one is picked for having more.
 */
function pickPart(pool: number[], loop: number, k: number): number | null {
  if (!pool.length) return null;
  const u = ((loop * 3 + k + 1) * 0.6180339887) % 1;
  return pool[Math.floor(u * pool.length)]!;
}

type TechView = {
  tech: TechCode;
  /** artist index → first year */
  first: Map<number, number>;
  /** adopters in angle order, for the needle window */
  byAngle: number[];
  /** record nodes from each adopter's first year on (ringed in the study) */
  ringed: Set<number>;
  /** adopters with enough ringed records to pull out as parts */
  parts: number[];
  /** ties among adopters: record pair, and -1 made before both adopted / +1 after */
  ties: Array<[number, number, -1 | 1]>;
  /** how many ties each side had before the sample, [before, after] */
  tieCount: [number, number];
};

/**
 * A tie is a pair of adopters on the same venue in the same year (the co-presence of the
 * study), dated by the first year they share. It counts as before when both adopt after that
 * year and as after when both adopted before it; a tie in either one's adoption year is neither.
 */
function buildTies(
  layout: Layout,
  data: StudyData,
  first: Map<number, number>,
): Pick<TechView, "ties" | "tieCount"> {
  const at = new Map<string, Map<number, number>>(); // venue|year → artist → record node
  for (const [i] of first)
    for (const ri of layout.artists[i]!.strand) {
      const nd = layout.records[ri]!;
      const v = foldVenue(data.records[srcIndex(nd)]!.venue);
      if (GENERIC_VENUES.has(v)) continue;
      const key = `${v}|${nd.year}`;
      let m = at.get(key);
      if (!m) at.set(key, (m = new Map()));
      if (!m.has(i)) m.set(i, ri);
    }
  const firstTie = new Map<string, [number, number, number]>(); // pair → year, nodes
  for (const [key, m] of at) {
    if (m.size < 2) continue;
    const year = Number(key.slice(key.lastIndexOf("|") + 1));
    const who = [...m.keys()].sort((p, q) => p - q);
    for (let x = 0; x < who.length; x++)
      for (let y = x + 1; y < who.length; y++) {
        const pk = `${who[x]}-${who[y]}`;
        const had = firstTie.get(pk);
        if (!had || year < had[0])
          firstTie.set(pk, [year, m.get(who[x]!)!, m.get(who[y]!)!]);
      }
  }
  const sides: Array<Array<[number, number, -1 | 1]>> = [[], []];
  for (const [pk, [year, p, q]] of firstTie) {
    const [i, j] = pk.split("-").map(Number) as [number, number];
    const yi = first.get(i)!;
    const yj = first.get(j)!;
    if (yi > year && yj > year) sides[0]!.push([p, q, -1]);
    else if (yi < year && yj < year) sides[1]!.push([p, q, 1]);
  }
  const n = Math.min(TIE_SAMPLE, sides[0]!.length, sides[1]!.length);
  // an even walk over each side (pairs in a fixed order), not the first n
  const pick = (list: Array<[number, number, -1 | 1]>) => {
    list.sort((u, v) => u[0] - v[0] || u[1] - v[1]);
    return Array.from({ length: n }, (_, k) => list[Math.floor((k * list.length) / n)]!);
  };
  return {
    ties: [...pick(sides[0]!), ...pick(sides[1]!)],
    tieCount: [sides[0]!.length, sides[1]!.length],
  };
}

function buildTech(layout: Layout, data: StudyData, tech: TechCode): TechView {
  const first = new Map<number, number>();
  data.artists.forEach((a, i) => {
    const y = a.tech?.[tech];
    if (y != null && !layout.artists[i]!.hidden) first.set(i, y);
  });
  const byAngle = [...first.keys()].sort(
    (p, q) => layout.artists[p]!.angle - layout.artists[q]!.angle,
  );
  const ringed = new Set<number>();
  for (const [i, y0] of first)
    for (const ri of layout.artists[i]!.strand)
      if (layout.records[ri]!.year >= y0) ringed.add(ri);
  const parts = byAngle.filter((i) => {
    let n = 0;
    for (const ri of layout.artists[i]!.strand) if (ringed.has(ri)) n++;
    return n >= MIN_PARTS;
  });
  return { tech, first, byAngle, ringed, parts, ...buildTies(layout, data, first) };
}

export function Exhibit({ data }: { data: StudyData }) {
  const { lang, t } = useLang();
  // the same layout the study builds (buildLayout is deterministic), so record and artist
  // indices here address the study's nodes
  const layout = useMemo(() => buildLayout(data, { R: 380 }), [data]);
  const techs = useMemo(
    () =>
      new Map(TECH_CODES.map((k) => [k, buildTech(layout, data, k)] as const)),
    [layout, data],
  );
  const params = useMemo(
    () =>
      typeof window === "undefined"
        ? new URLSearchParams()
        : new URLSearchParams(window.location.search),
    [],
  );
  const kiosk = params.has("kiosk");
  // `&hold` stops the scene clock at `at`
  const hold =
    params.has("hold") && params.has("scene")
      ? clamp(Number(params.get("at")) || 0, 0, 1)
      : null;
  // The installation shows no explanation by default: the picture and the sound carry it, and the
  // words go on the wall label. `?notes` brings back the count (for documentation).
  const notes = params.has("notes");
  const director = useRef<StudyDirector>({ api: null }).current;

  const st = useRef({
    scene: 0,
    sceneStart: 0,
    lastInput: -Infinity,
    cue: 0,
    loop: 0,
    started: false,
  }).current;
  const [hud, setHud] = useState({ scene: 0, sound: false, word: 0 });

  // The technology scenes switch the page to the night palette; the study re-reads its colours
  // when the root class changes (theme.ts). Whatever the reader had is restored on leaving.
  const hadDark = useRef<boolean | null>(null);
  const setNight = useCallback((on: boolean) => {
    const el = document.documentElement;
    if (hadDark.current === null)
      hadDark.current = el.classList.contains("dark");
    el.classList.toggle("dark", on);
  }, []);
  useEffect(
    () => () => {
      if (hadDark.current !== null)
        document.documentElement.classList.toggle("dark", hadDark.current);
    },
    [],
  );

  /* ---- sound ---- */
  const voicesFor = useCallback(
    (rot: number): number[] | null => {
      const sc = LOOP[st.scene]!;
      if (sc.kind !== "tech") return null; // the needle's own choice
      // the adopters nearest the needle
      const tv = techs.get(sc.tech)!;
      const d = (i: number) => {
        const a = layout.artists[i]!.angle + rot + Math.PI / 2;
        return Math.abs(Math.atan2(Math.sin(a), Math.cos(a)));
      };
      return [...tv.byAngle].sort((p, q) => d(p) - d(q)).slice(0, 10);
    },
    [st, techs, layout],
  );

  /** Point the ensemble at the current scene: who may play, and from which year. */
  const tuneMusic = useCallback(() => {
    const m = director.api?.music();
    if (!m) return;
    m.chooser = voicesFor;
    const sc = LOOP[st.scene]!;
    m.conductor.from =
      sc.kind === "tech"
        ? (i) => techs.get(sc.tech)!.first.get(i) ?? Infinity
        : null;
    // voices built for the previous scene leave on their next boundary and come back rebuilt
    for (const v of m.conductor.voices.values()) v.leaving = true;
  }, [director, voicesFor, st, techs]);

  const startSound = useCallback(() => {
    director.api?.startMusic();
    setHud((h) => (h.sound ? h : { ...h, sound: true }));
  }, [director]);

  // people whose parts the records scene may pull out (rim order; see pickPart)
  const recordParts = useMemo(
    () =>
      layout.artists
        .filter((a) => !a.hidden && a.strand.length >= MIN_PARTS)
        .map((a) => a.i),
    [layout],
  );

  /** Run the cues the scene has reached, and grow the period over its span. */
  const runScript = useCallback(
    (phase: number) => {
      const api = director.api;
      if (!api) return;
      const sc = LOOP[st.scene]!;
      const cues = sc.kind === "records" ? RECORD_CUES : TECH_CUES;
      const grow = sc.kind === "records" ? RECORD_GROW : TECH_GROW;
      const pool = sc.kind === "tech" ? techs.get(sc.tech)!.parts : recordParts;
      const part = (k: number) => pickPart(pool, st.loop, k);
      const tie = (below: boolean, above: boolean): StudyTies | null => {
        if (sc.kind !== "tech") return null;
        const odds = TIE_ODDS[sc.tech];
        return {
          pairs: techs.get(sc.tech)!.ties,
          alpha: [below ? tieAlpha(odds.before) : 0, above ? tieAlpha(odds.after) : 0],
        };
      };
      while (st.cue < cues.length && cues[st.cue]!.at <= phase)
        cues[st.cue++]!.run(api, part, tie);
      if (phase >= grow.from && phase < grow.to) {
        const u = (phase - grow.from) / (grow.to - grow.from);
        api.setPeriod(0, u * (api.rings - 1));
      }
    },
    [director, st, techs, recordParts],
  );

  const goScene = useCallback(
    (k: number, byUser = true) => {
      st.scene = ((k % LOOP.length) + LOOP.length) % LOOP.length;
      st.sceneStart = performance.now();
      st.cue = 0;
      if (st.scene === 0) st.loop++;
      if (byUser) st.lastInput = performance.now();
      const sc = LOOP[st.scene]!;
      director.api?.setTies(null);
      director.api?.setRinged(
        sc.kind === "tech" ? techs.get(sc.tech)!.ringed : null,
        sc.kind === "tech" ? sc.tech : "",
      );
      setNight(sc.kind === "tech");
      tuneMusic();
      setHud((h) => ({ ...h, scene: st.scene, word: h.word + 1 }));
    },
    [st, director, techs, tuneMusic, setNight],
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

  /* ---- the director's clock ---- */
  useEffect(() => {
    let raf = 0;
    let tuned: unknown = null;
    const tick = (now: number) => {
      // the study mounts after this effect: start the loop once its director is there
      if (!st.started && director.api) {
        st.started = true;
        const k0 = params.has("scene")
          ? clamp(
              Math.round(Number(params.get("scene")) || 0),
              0,
              LOOP.length - 1,
            )
          : 0;
        goScene(k0, params.has("scene"));
        if (params.has("scene")) {
          const at = clamp(Number(params.get("at")) || 0, 0, 1);
          st.sceneStart = now - at * lengthOf(LOOP[k0]!) * 1000;
        }
      }
      if (st.started && hold != null)
        st.sceneStart = now - hold * lengthOf(LOOP[st.scene]!) * 1000;
      // the ensemble exists only after the first gesture: tune it when it appears
      const m = director.api?.music() ?? null;
      if (m && m !== tuned) {
        tuned = m;
        tuneMusic();
      }
      if (st.started) {
        const sc = LOOP[st.scene]!;
        const len = lengthOf(sc) * 1000;
        if (now - st.sceneStart > len && now - st.lastInput > IDLE_RESUME_MS)
          goScene(st.scene + 1, false);
        const phase = clamp((now - st.sceneStart) / len, 0, 1);
        runScript(phase);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [st, director, params, hold, goScene, tuneMusic, runScript]);

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
  const sceneWord =
    sc.kind === "records"
      ? t("기록", "records")
      : TECH_LABEL[sc.tech][lang === "ko" ? 0 : 1];
  const tv = sc.kind === "tech" ? techs.get(sc.tech)! : null;
  return (
    <div
      className="relative h-dvh w-full overflow-hidden bg-background text-foreground"
      style={{ cursor: kiosk ? "none" : undefined }}
    >
      <ArchivalStudy data={data} director={director} />
      {/* one word at the start of a scene; it fades out (the key restarts the animation) */}
      <p
        key={hud.word}
        className={`pointer-events-none absolute left-6 top-6 font-mono text-[15px] ${notes ? "" : "exhibit-word"}`}
        style={{ color: sc.kind === "tech" ? "#ffd27a" : undefined }}
      >
        {sceneWord}
        {notes && tv ? (
          <span className="ml-3 text-[12px] opacity-70">
            {t(
              `${tv.first.size.toLocaleString()}명 · 테 = 제목에 처음 나온 해부터의 기록 · 축 아래 = 채택 전 연결(오즈비 ${TIE_ODDS[tv.tech].before.toFixed(2)}), 위 = 채택 뒤(${TIE_ODDS[tv.tech].after.toFixed(2)}) · 각 ${Math.min(TIE_SAMPLE, ...tv.tieCount)}개`,
              `${tv.first.size.toLocaleString()} people · ringed: records from the first year a title names it · below the axis: ties before adopting (odds ratio ${TIE_ODDS[tv.tech].before.toFixed(2)}), above: after (${TIE_ODDS[tv.tech].after.toFixed(2)}) · ${Math.min(TIE_SAMPLE, ...tv.tieCount)} each`,
            )}
          </span>
        ) : null}
      </p>
      {!hud.sound && (
        <p className="pointer-events-none absolute bottom-6 left-1/2 -translate-x-1/2 font-mono text-[12px] opacity-60">
          {t("화면을 누르면 소리가 납니다", "Touch the screen for sound")}
        </p>
      )}
      {!kiosk && (
        <>
          <nav className="absolute bottom-5 left-5 flex flex-wrap gap-2 font-mono text-[11px]">
            {LOOP.map((s, k) => {
              const label =
                s.kind === "records"
                  ? t("기록", "records")
                  : TECH_LABEL[s.tech][lang === "ko" ? 0 : 1];
              const on = k === hud.scene;
              return (
                <button
                  key={k}
                  type="button"
                  onClick={() => goScene(k)}
                  className={`border px-2 py-1 ${on ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background/70"}`}
                >
                  {label}
                </button>
              );
            })}
          </nav>
          <Link
            to="/"
            className="absolute right-5 top-5 font-mono text-[11px] text-foreground no-underline opacity-60"
          >
            {t("기예 홈", "Giye home")}
          </Link>
        </>
      )}
      <style>{`
        .exhibit-word { animation: exhibit-word 6s ease forwards; }
        @keyframes exhibit-word { 0% { opacity: 0 } 18% { opacity: .85 } 70% { opacity: .85 } 100% { opacity: 0 } }
      `}</style>
    </div>
  );
}
