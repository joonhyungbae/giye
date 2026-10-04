// SPDX-License-Identifier: AGPL-3.0-only
import { fold, foldVenue } from "@/lib/record-text";

/**
 * Archival Study — layout model.
 *
 * The archive is drawn as growth rings: each ring is a year, each dot is one
 * sourced record, each artist is a radial strand of dots ending in a knot on
 * the rim. Angular position groups artists by entry generation (the year of
 * their first roster, in 5-year bins). Where each arc and each artist sits is
 * computed from the data by scripts/build_rim_order.py (data/site/rim_order.json);
 * this file only lays that order out.
 */

export type StudyArtist = {
  id: string;
  name_ko: string;
  name_en: string | null;
  frame_codes: string[];
  frame_status: string;
  verification: string;
  status: string;
  collected_at: string;
  active_since: number | null;
  medium_tags: string[];
  regions: string[];
};

export type StudyRecord = {
  /** W2. 0-based position among this artist's records in the study order. */
  ord: number;
  artist_id: string;
  venue: string | null;
  year: number;
  activity_type: string;
  /** Source host (institution or website), not the citation URL. */
  domain: string;
  source_type: string;
  collected_at: string;
};

export type StudyFrame = {
  code: string;
  name_ko: string;
  name_en: string | null;
  roster_count?: number | null;
  included_count?: number | null;
};

/** A past snapshot, as row indexes into this payload (ids no longer published are dropped). */
export type StudySnapshot = { version: string; released_at: string; rows: number[] };

export type StudyData = {
  artists: StudyArtist[];
  records: StudyRecord[];
  frames: StudyFrame[];
  /** W2. Data-stamp hash of this payload. Record ords match another call only when its stamp equals this. */
  stamp: string;
  snapshots?: StudySnapshot[];
  /** programme and artist order around the rim (data/site/rim_order.json) */
  rim?: StudyRim | null;
  version: string;
  generated_at: string | null;
};

/** Output of scripts/build_rim_order.py. */
export type StudyRim = {
  version: string;
  families: Array<{ code: string; label_ko: string; label_en: string; n: number }>;
  /** programme families (frames.yml names); with it, `also` lists every family an artist was on */
  programmes?: Array<{ code: string; label_ko: string; label_en: string }>;
  boundaries: Array<{
    a: string;
    b: string;
    shared: number;
    cos: number;
    agree: number;
    seam: boolean;
  }>;
  /** artists in rim order: home programme and edition, and the other programmes they took part in */
  artists: Array<{
    id: string;
    family: string;
    year: number | null;
    edition: string;
    also: string[];
  }>;
};

/** A programme an artist was collected through; drawn as a membership row outside the rim. */
export type Programme = { code: string; label_ko: string; label_en: string; members: number };

export type RecordNode = {
  i: number;
  artist: number;
  angle: number;
  r: number;
  x: number;
  y: number;
  year: number;
  ring: number;
  reveal: number;
  chords: number;
};

export type ArtistNode = {
  i: number;
  id: string;
  angle: number;
  r: number;
  x: number;
  y: number;
  strand: number[];
  hidden: boolean;
  group: number;
  sourced: number;
};

export type Ring = { year: number | null; r: number; label: string; count: number };
export type Group = {
  code: string;
  label_ko: string;
  label_en: string;
  a0: number;
  a1: number;
  n: number;
};
export type Chord = { a: number; b: number; venue: string };

export type Layout = {
  artists: ArtistNode[];
  records: RecordNode[];
  rings: Ring[];
  groups: Group[];
  chords: Chord[];
  revealOrder: number[];
  R: number;
  rimR: number;
  programmes: Programme[];
  /** per artist: indices into programmes they took part in (besides their home arc in an older,
   *  programme-arc rim file) */
  artistProgrammes: number[][];
  /** true when the rim file lists programmes apart from its arcs (arcs are entry generations) */
  programmesApart: boolean;
  /** angular width of one artist slot */
  unit: number;
  years: { min: number; max: number };
};

const GENERIC_VENUES = new Set([
  "",
  "참여 작가",
  "online",
  "온라인",
  "서울",
  "singapore",
  "korea",
  "한국",
]);

/* Collator built once: String#localeCompare builds one per call, which dominated the sorts. */
const KO = new Intl.Collator("ko");

export function buildLayout(data: StudyData, opts?: { R?: number }): Layout {
  const R = opts?.R ?? 380;
  const artists = data.artists;
  const n = artists.length;

  /* ---- angular slots: the order rim_order.json gives, one slot per artist ---- */
  // Arcs (entry generations) follow the file; inside one, artists follow the file (entry year,
  // team, name). Anyone published after the file was written goes to the end of the arc of their
  // generation, computed the same way from the years in their frame codes; the next pipeline run
  // places them properly.
  const rim = data.rim ?? null;
  const artistIdx0 = new Map(artists.map((a, i) => [a.id, i] as const));
  const famOrder: string[] = [];
  const labelOf = new Map<string, [string, string]>();
  const byFam = new Map<string, number[]>();
  const editionOf = new Map<number, string>();
  const alsoOf = new Map<number, string[]>();
  const addTo = (f: string, i: number) => {
    if (!byFam.has(f)) {
      byFam.set(f, []);
      if (!famOrder.includes(f)) famOrder.push(f);
    }
    byFam.get(f)!.push(i);
  };
  for (const f of rim?.families ?? []) {
    famOrder.push(f.code);
    labelOf.set(f.code, [f.label_ko, f.label_en]);
  }
  const placed = new Uint8Array(n);
  for (const r of rim?.artists ?? []) {
    const i = artistIdx0.get(r.id);
    if (i == null || placed[i]) continue;
    placed[i] = 1;
    addTo(r.family, i);
    editionOf.set(i, `${r.year ?? ""}|${r.edition}`);
    alsoOf.set(i, r.also);
  }
  const frameName = new Map(
    data.frames.map((f) => [f.code, [f.name_ko, f.name_en ?? f.name_ko]] as const),
  );
  const late = artists
    .map((_, i) => i)
    .filter((i) => !placed[i])
    .sort((p, q) => KO.compare(artists[p]!.name_ko, artists[q]!.name_ko));
  // Generation arcs are coded GEN-<first year of the 5-year bin> plus GEN-UNDATED.
  const genStarts = (rim?.families ?? [])
    .map((f) => /^GEN-(\d{4})$/.exec(f.code))
    .filter((m): m is RegExpExecArray => m != null)
    .map((m) => Number(m[1]))
    .sort((p, q) => p - q);
  const generationOf = (codes: string[]): string => {
    const years = codes
      .map((c) => /-(\d{4})$/.exec(c))
      .filter((m): m is RegExpExecArray => m != null)
      .map((m) => Number(m[1]));
    if (!years.length) return "GEN-UNDATED";
    // the same 5-year grid the builder uses (it extends before 2000 rather than clamping).
    // frame_codes do not mark staff roles, which the builder excludes, so a late artist whose
    // earliest code is a staff role may sit one arc early until the next pipeline run.
    return `GEN-${Math.floor(Math.min(...years) / 5) * 5}`;
  };
  // An older rim_order.json grouped arcs by programme: then a late artist joins the arc of
  // their first listed programme, as before.
  const byGeneration =
    genStarts.length > 0 || (rim?.families ?? []).some((f) => f.code === "GEN-UNDATED");
  const programmeOf = (codes: string[]): string => {
    const code = codes[0] ?? "";
    return (
      rim?.families.find((x) => code === x.code || code.startsWith(x.code + "-"))?.code ?? code
    );
  };
  for (const i of late) {
    const codes = artists[i]!.frame_codes;
    const f = byGeneration ? generationOf(codes) : programmeOf(codes);
    if (!labelOf.has(f)) labelOf.set(f, (frameName.get(f) as [string, string]) ?? [f, f]);
    addTo(f, i);
  }
  const famsPresent = famOrder.filter((f) => byFam.has(f));
  const famIndex = new Map(famsPresent.map((f, j) => [f, j] as const));
  // Programme membership: from rim.programmes when the file has it (arcs are then generations,
  // not memberships, and `also` lists every family); an older file's arcs were the programmes.
  const programmesApart = rim?.programmes != null;
  const programmes: Programme[] = programmesApart
    ? rim!.programmes!.map((p) => ({ ...p, members: 0 }))
    : famsPresent.map((code) => ({
        code,
        label_ko: labelOf.get(code)?.[0] ?? code,
        label_en: labelOf.get(code)?.[1] ?? code,
        members: 0,
      }));
  const progIndex = programmesApart
    ? new Map(programmes.map((p, j) => [p.code, j] as const))
    : famIndex;
  const artistProgrammes = artists.map((_, i) =>
    (alsoOf.get(i) ?? []).map((f) => progIndex.get(f)).filter((j): j is number => j != null),
  );
  for (const ps of artistProgrammes) for (const j of ps) programmes[j]!.members++;

  const GAP = 2.2; // between programmes, in slots
  const ED_GAP = 0.6; // between editions of one programme
  let edGaps = 0;
  for (const f of famsPresent) {
    const list = byFam.get(f)!;
    for (let k = 1; k < list.length; k++)
      if (editionOf.get(list[k]!) !== editionOf.get(list[k - 1]!)) edGaps++;
  }
  const totalSlots = n + famsPresent.length * GAP + edGaps * ED_GAP;
  const unit = (Math.PI * 2) / totalSlots;
  const groups: Group[] = [];
  const angleOf = new Float64Array(n);
  const groupOf = new Int16Array(n);
  // 12 o'clock is where the oldest generation begins; arcs run clockwise in rim_order.json order
  let cursor = -Math.PI / 2 + unit * (GAP / 2);
  famsPresent.forEach((f, gi) => {
    const members = byFam.get(f)!;
    const a0 = cursor;
    members.forEach((i, k) => {
      if (k && editionOf.get(i) !== editionOf.get(members[k - 1]!)) cursor += unit * ED_GAP;
      angleOf[i] = cursor + unit / 2;
      groupOf[i] = gi;
      cursor += unit;
    });
    const [ko, en] = labelOf.get(f) ?? [f, f];
    groups.push({ code: f, label_ko: ko, label_en: en, a0, a1: cursor, n: members.length });
    cursor += unit * GAP;
  });

  /* ---- rings: one per year from 2017 up; older years share the innermost ring ---- */
  const yearsAll = data.records.map((r) => r.year).filter((y) => Number.isFinite(y));
  const maxYear = Math.max(...yearsAll, new Date().getFullYear());
  const FIRST = 2017;
  const rings: Ring[] = [];
  const r0 = R * 0.19;
  const step = (R - r0) / (maxYear - FIRST + 1);
  rings.push({ year: null, r: r0 - step * 0.75, label: `≤${FIRST - 1}`, count: 0 });
  for (let y = FIRST; y <= maxYear; y++)
    rings.push({ year: y, r: r0 + (y - FIRST) * step, label: String(y), count: 0 });
  const ringIdx = (y: number) => (y < FIRST ? 0 : Math.min(rings.length - 1, 1 + (y - FIRST)));

  /* ---- records on their artist's strand ---- */
  const recs = data.records;
  const artistIdx = new Map(artists.map((a, i) => [a.id, i] as const));
  // one bucket per (artist, ring); the key is packed into a number, since tens of thousands of
  // "ai:ring" strings cost more than the layout itself
  const RING_SPAN = 1024;
  const perArtistYear = new Map<number, number[]>();
  for (let i = 0; i < recs.length; i++) {
    const ai = artistIdx.get(recs[i]!.artist_id);
    if (ai == null) continue;
    const key = ai * RING_SPAN + ringIdx(recs[i]!.year);
    const bucket = perArtistYear.get(key);
    if (bucket) bucket.push(i);
    else perArtistYear.set(key, [i]);
  }
  const records: RecordNode[] = [];
  const strandOf: number[][] = artists.map(() => []);
  for (const [key, list] of perArtistYear) {
    const ai = Math.floor(key / RING_SPAN);
    const ring = key % RING_SPAN;
    // Rows arrive in study order: artist, then title, then record id (see buildStudyData). Keep that
    // order (the index).
    if (list.length > 1) list.sort((p, q) => p - q);
    const k = list.length;
    // an artist's records of one year stay inside that artist's own slot: at most three
    // across, stacked within the year's band, so a busy year darkens its cell instead of
    // spilling over the neighbours
    const cols = Math.min(k, 3);
    const rows = Math.ceil(k / cols);
    const dA = unit * 0.24;
    const dR = Math.min(step * 0.14, (step * 0.8) / rows);
    list.forEach((ri, j) => {
      const c = j % cols;
      const rw = Math.floor(j / cols);
      const off = (c - (cols - 1) / 2) * dA;
      const dr = (rw - (rows - 1) / 2) * dR;
      const angle = angleOf[ai]! + off;
      const r = rings[ring]!.r + dr;
      // `src` (the index in data.records) is part of the literal: adding it afterwards gave every
      // one of these tens of thousands of nodes a second shape, which the engine has to re-learn
      const node: RecordNode & { src: number } = {
        i: records.length,
        artist: ai,
        angle,
        r,
        x: Math.cos(angle) * r,
        y: Math.sin(angle) * r,
        year: recs[ri]!.year,
        ring,
        reveal: 0,
        chords: 0,
        src: ri,
      };
      records.push(node);
      strandOf[ai]!.push(node.i);
      rings[ring]!.count++;
    });
  }
  for (const s of strandOf) s.sort((p, q) => records[p]!.r - records[q]!.r);

  /* ---- reveal order: as the archive was actually collected ---- */
  // collection date and year decide the order; both are packed into one number so the sort
  // compares numbers (dates repeat — a dozen distinct values over tens of thousands of rows)
  const dateRank = new Map<string, number>();
  for (const r of recs) if (!dateRank.has(r.collected_at)) dateRank.set(r.collected_at, 0);
  [...dateRank.keys()].sort().forEach((d, i) => dateRank.set(d, i));
  const rank = new Float64Array(records.length);
  for (let i = 0; i < records.length; i++) {
    const r = recs[(records[i] as RecordNode & { src: number }).src]!;
    rank[i] = (dateRank.get(r.collected_at) ?? 0) * 100000 + r.year;
  }
  // rows collected on the same day in the same year keep ledger order (Array#sort is stable),
  // which is deterministic without comparing tens of thousands of ids
  const order = records.map((nd) => nd.i).sort((p, q) => rank[p]! - rank[q]!);
  order.forEach((ri, k) => {
    records[ri]!.reveal = k;
  });

  /* ---- chords: records that share a venue, across different artists ---- */
  // Each spelling of a venue is folded once (foldVenue, the same fold the home payload uses)
  // and given a number; the grouping then works on numbers. (A venue name repeats across
  // thousands of rows.)
  const venueIdOf = new Map<string, number>(); // raw spelling → id, -1 for generic/blank
  const idOfFolded = new Map<string, number>(); // folded name → id (two spellings can fold to one)
  const venueName: string[] = [];
  const buckets: number[][] = [];
  for (const nd of records) {
    const raw = recs[(nd as RecordNode & { src: number }).src]!.venue ?? "";
    let id = venueIdOf.get(raw);
    if (id === undefined) {
      const folded = foldVenue(raw);
      if (GENERIC_VENUES.has(folded)) id = -1;
      else {
        const known = idOfFolded.get(folded);
        if (known === undefined) {
          id = venueName.length;
          venueName.push(folded);
          buckets.push([]);
          idOfFolded.set(folded, id);
        } else id = known;
      }
      venueIdOf.set(raw, id);
    }
    if (id >= 0) buckets[id]!.push(nd.i);
  }
  const byVenue = new Map<string, number[]>();
  for (let i = 0; i < venueName.length; i++) byVenue.set(venueName[i]!, buckets[i]!);
  const chords: Chord[] = [];
  for (const [venue, list] of byVenue) {
    let twoArtists = false;
    const first = records[list[0]!]!.artist;
    for (let i = 1; i < list.length; i++)
      if (records[list[i]!]!.artist !== first) {
        twoArtists = true;
        break;
      }
    if (!twoArtists) continue;
    list.sort((p, q) => records[p]!.angle - records[q]!.angle);
    const m = list.length;
    for (let k = 0; k < m; k++) {
      const a = list[k]!;
      const b = list[(k + 1) % m]!;
      if (m === 2 && k === 1) break;
      if (records[a]!.artist === records[b]!.artist) continue;
      chords.push({ a, b, venue });
      records[a]!.chords++;
      records[b]!.chords++;
    }
  }

  /* ---- artist knots on the rim ---- */
  const rimR = R + step * 0.9;
  const artistNodes: ArtistNode[] = artists.map((a, i) => ({
    i,
    id: a.id,
    angle: angleOf[i]!,
    r: rimR,
    x: Math.cos(angleOf[i]!) * rimR,
    y: Math.sin(angleOf[i]!) * rimR,
    strand: strandOf[i]!,
    hidden: a.status !== "PUBLISHED",
    group: groupOf[i]!,
    sourced: strandOf[i]!.length,
  }));

  return {
    artists: artistNodes,
    records,
    rings,
    groups,
    chords,
    revealOrder: order,
    R,
    rimR,
    programmes,
    artistProgrammes,
    programmesApart,
    unit,
    years: { min: Math.min(...yearsAll), max: maxYear },
  };
}

export function srcIndex(node: RecordNode): number {
  return (node as RecordNode & { src: number }).src;
}

export type SearchResults = {
  /** artist indices (hidden artists never appear) */
  artists: number[];
  /** frame-family (rim arc) indices */
  frames: number[];
};

const squash = (v: string) => v.replace(/\s+/g, "");

/* A search index per dataset: folding (lowercase + NFC) every artist on every keystroke was
   the cost, so each name is folded once and kept. Space-stripped copies are made the first
   time a query needs them. Record titles are not in this payload; that search is a server call. */
type Field = { f: string[]; sq: (string | undefined)[]; w: number };
type SearchIndex = { artistFields: Field[] };
const searchIndexes = new WeakMap<StudyData, SearchIndex>();

function field(values: string[], w: number): Field {
  return { f: values, sq: new Array<string | undefined>(values.length), w };
}

function searchIndex(data: StudyData): SearchIndex {
  const hit = searchIndexes.get(data);
  if (hit) return hit;
  const A = data.artists;
  const idx: SearchIndex = {
    artistFields: [
      field(
        A.map((a) => fold(a.name_ko)),
        3,
      ),
      field(
        A.map((a) => fold(a.name_en ?? "")),
        3,
      ),
      field(
        A.map((a) => fold(a.id)),
        2,
      ),
      field(
        A.map((a) => fold(a.medium_tags.join(" "))),
        1,
      ),
      field(
        A.map((a) => fold(a.regions.join(" "))),
        1,
      ),
    ],
  };
  searchIndexes.set(data, idx);
  return idx;
}

/** Build the search index ahead of the first keystroke (call when the page is idle). */
export function warmSearch(data: StudyData): void {
  searchIndex(data);
}

/** One query across artists (names, tags, regions, codes) and frames. Record text is searched on the server. */
export function searchStudy(data: StudyData, layout: Layout, q: string): SearchResults {
  const s = fold(q).trim();
  if (!s) return { artists: [], frames: [] };
  const toks = s.split(/\s+/).filter(Boolean);
  const multi = toks.length > 1;
  const tight = squash(s);
  const idx = searchIndex(data);

  /** score of one already-folded value; `sq` caches its space-stripped copy */
  const scoreOne = (f: string, w: number, sq: (string | undefined)[] | null, at: number) => {
    if (!f) return 0;
    let sc = 0;
    if (f === s) sc += 6 * w;
    else if (f.startsWith(s)) sc += 4 * w;
    else {
      let hit = f.includes(s);
      if (!hit) {
        let stripped = sq ? sq[at] : undefined;
        if (stripped === undefined) {
          stripped = squash(f);
          if (sq) sq[at] = stripped;
        }
        hit = stripped.includes(tight);
      }
      if (hit) sc += 3 * w;
    }
    if (multi) for (const t of toks) if (f.includes(t)) sc += w;
    return sc;
  };
  const scoreRow = (fields: Field[], at: number) => {
    let sc = 0;
    for (const fl of fields) sc += scoreOne(fl.f[at]!, fl.w, fl.sq, at);
    return sc;
  };
  /** the n best rows, highest score first; ties keep row order */
  const best = (n: number, count: number, sc: (i: number) => number) => {
    const out: { i: number; sc: number }[] = [];
    for (let i = 0; i < count; i++) {
      const v = sc(i);
      if (v > 0) out.push({ i, sc: v });
    }
    out.sort((a, b) => b.sc - a.sc);
    return out.slice(0, n).map((x) => x.i);
  };

  const artists = best(6, data.artists.length, (i) =>
    layout.artists[i]!.hidden ? 0 : scoreRow(idx.artistFields, i),
  );
  const frames = best(3, layout.groups.length, (i) => {
    const g = layout.groups[i]!;
    return (
      scoreOne(fold(g.label_ko), 2, null, 0) +
      scoreOne(fold(g.label_en), 2, null, 0) +
      scoreOne(fold(g.code), 1, null, 0)
    );
  });

  return { artists, frames };
}

/* ------------------------------------------------------------------ */
/* Lower layers: sources (URL domains) and generation plates (rim arcs) */
/* ------------------------------------------------------------------ */

export type SourceNode = {
  i: number;
  domain: string;
  records: number[];
  artists: number;
  angle: number;
  r: number;
  x: number;
  y: number;
  size: number;
};

export type Strata = {
  sources: SourceNode[];
  /** record index → source index */
  sourceOf: Int32Array;
  /** generation plate radius (fraction of rimR) */
  plateR: number;
  /** depth of the source layer and the frame layer when fully exploded (world units) */
  depthSources: number;
  depthFrames: number;
};

/** Every record stands on a source host. Group them by that host and lay the hosts out under the disc. */
export function buildStrata(layout: Layout, data: StudyData): Strata {
  const byDomain = new Map<string, number[]>();
  layout.records.forEach((nd) => {
    const r = data.records[srcIndex(nd)]!;
    const d = r.domain;
    if (!byDomain.has(d)) byDomain.set(d, []);
    byDomain.get(d)!.push(nd.i);
  });
  const sources: SourceNode[] = [];
  const sourceOf = new Int32Array(layout.records.length);
  const R = layout.R;
  for (const [domain, list] of byDomain) {
    let cx = 0;
    let cy = 0;
    const artists = new Set<number>();
    for (const ri of list) {
      const nd = layout.records[ri]!;
      cx += Math.cos(nd.angle);
      cy += Math.sin(nd.angle);
      artists.add(nd.artist);
    }
    const angle = Math.atan2(cy, cx);
    // Domains with many records sit closer to the centre, like load-bearing piers.
    const r = R * (0.72 - 0.42 * Math.min(1, Math.log1p(list.length) / Math.log1p(80)));
    const node: SourceNode = {
      i: sources.length,
      domain,
      records: list,
      artists: artists.size,
      angle,
      r,
      x: Math.cos(angle) * r,
      y: Math.sin(angle) * r,
      size: 2.2 + Math.sqrt(list.length) * 1.1,
    };
    for (const ri of list) sourceOf[ri] = node.i;
    sources.push(node);
  }
  /* Relax so piers never overlap. Hundreds of piers × 90 passes is the heaviest part of opening
     the study, so the pass runs on flat arrays, compares squared distances, drops pairs that are
     too far apart on one axis before any square root, and stops once a whole pass moves every
     pier less than a twentieth of a pixel (invisible, and later passes would move even less). */
  const n = sources.length;
  const sx = new Float64Array(n);
  const sy = new Float64Array(n);
  const ss = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    sx[i] = sources[i]!.x;
    sy[i] = sources[i]!.y;
    ss[i] = sources[i]!.size;
  }
  const rMin = R * 0.14;
  const rMax = R * 0.86;
  const SETTLED = 0.05;
  for (let it = 0; it < 90; it++) {
    let shift = 0;
    for (let a = 0; a < n; a++) {
      const ax = sx[a]!;
      const ay = sy[a]!;
      const sa = ss[a]! + 9;
      for (let b = a + 1; b < n; b++) {
        const dx = sx[b]! - ax;
        const dy = sy[b]! - ay;
        const min = sa + ss[b]!;
        if (dx >= min || -dx >= min || dy >= min || -dy >= min) continue;
        const d2 = dx * dx + dy * dy;
        if (d2 >= min * min) continue;
        const d = Math.sqrt(d2) || 0.001;
        const push = ((min - d) / d) * 0.5;
        const px = dx * push;
        const py = dy * push;
        sx[a] = sx[a]! - px;
        sy[a] = sy[a]! - py;
        sx[b] = sx[b]! + px;
        sy[b] = sy[b]! + py;
        const m = Math.abs(px) + Math.abs(py);
        if (m > shift) shift = m;
      }
    }
    for (let i = 0; i < n; i++) {
      const rr = Math.hypot(sx[i]!, sy[i]!) || 0.001;
      const clamped = rr < rMin ? rMin : rr > rMax ? rMax : rr;
      if (clamped !== rr) {
        const k = clamped / rr;
        const before = Math.abs(sx[i]!) + Math.abs(sy[i]!);
        sx[i] = sx[i]! * k;
        sy[i] = sy[i]! * k;
        const m = Math.abs(before - (Math.abs(sx[i]!) + Math.abs(sy[i]!)));
        if (m > shift) shift = m;
      }
    }
    if (shift < SETTLED) break;
  }
  for (let i = 0; i < n; i++) {
    const nd = sources[i]!;
    nd.x = sx[i]!;
    nd.y = sy[i]!;
    nd.angle = Math.atan2(nd.y, nd.x);
    nd.r = Math.hypot(nd.x, nd.y);
  }
  return {
    sources,
    sourceOf,
    plateR: 0.9,
    depthSources: R * 0.52,
    depthFrames: R * 0.96,
  };
}
