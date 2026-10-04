// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Co-participation network: who took part in the same event.
 *
 * An event is either a frame edition (a published roster such as Example Workshop 2021)
 * or an activity line that several artists' CVs share (same title after normalisation, same year).
 * A CV line that names a frame, using that frame's registry name and the edition year,
 * is folded into that edition so one event is never counted twice.
 *
 * Programme names come from the frame registry the caller passes in. They are not
 * compiled into this file. Family folds live in the field file and are applied
 * when the rim is built; here a code keeps its registry identity with a trailing
 * year removed.
 *
 * Pure: no file access. giye.functions.ts feeds it the loaded ledger.
 */
import type { Activity, Artist } from "./giye.types";
import type { FrameEntry } from "./giye.data";

export type NetArtist = {
  id: string;
  name_ko: string;
  name_en: string | null;
  /** distinct activity rows on file (duplicates folded) */
  records: number;
};

export type NetEvent = {
  id: string;
  label_ko: string;
  label_en: string;
  /** edition title or the most common venue */
  detail: string | null;
  year: number | null;
  /** registry frame family when the event is a frame edition */
  frame: string | null;
  /** artist indices listed on the published roster of this edition */
  roster: number[];
  /** artist indices whose CV names this event */
  cv: number[];
};

export type NetworkData = {
  artists: NetArtist[];
  events: NetEvent[];
  version: string;
  generated_at: string | null;
};

export function normTitle(s: string | null | undefined): string {
  return (s ?? "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/["'“”‘’「」『』《》〈〉<>()[\]{}:;,.·•\-–—_/\\!?|~*#&+]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/* Titles that name a kind of activity, not an event: two CVs saying "artist talk" in 2024 are not
   one talk. A title made only of these words never links artists. */
const GENERIC_WORDS = new Set(
  (
    "artist artists talk talks group solo exhibition exhibitions show collection guest lecture lectures " +
    "workshop workshops excellence award awards prize grant residency open studio studios performance " +
    "screening presentation panel conference symposium seminar selected finalist honorable mention " +
    "the a of and in 개인전 단체전 기획전 초대전 워크숍 강연 특강 아티스트 토크 전시 레지던시 소장 " +
    "수상 선정 우수상 대상 참여 작가 공연 상영 세미나 심포지엄 발표 오픈 스튜디오 입주"
  ).split(" "),
);

function isGeneric(norm: string): boolean {
  if (norm.replace(/\s/g, "").length < 3) return true;
  return norm.split(" ").every((w) => GENERIC_WORDS.has(w) || /^\d+$/.test(w));
}

/* Running an event is not taking part in it: a facilitator of a workshop is not tied to its
   participants. Creative roles (director, 음악감독, producer) still count. */
const STAFF_ROLE =
  /기획|총괄|퍼실리테이터|facilitat|연구위원|엮음|curat|큐레이|organi[sz]|운영|심사|judge|jury|mentor|멘토|moderat|coordinator|코디네이|자문|advis|위원|컨설턴트|consultant/i;

/* A trailing edition year is not a different programme. Folds declared in the field file
   are applied when the rim is built; this view keeps the registry code. */
const familyOf = (code: string) => code.replace(/-\d{4}$/, "");

function frameKeywords(frames: FrameEntry[]): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const frame of frames) {
    const words = [frame.name_ko, frame.name_en]
      .map((name) => (name || "").toLowerCase().replace(/\s+/g, " ").trim())
      .filter((name) => name.length >= 4);
    if (words.length) out.set(familyOf(frame.code), words);
  }
  return out;
}

const mostCommon = (xs: string[]): string | null => {
  const c = new Map<string, number>();
  for (const x of xs) if (x) c.set(x, (c.get(x) ?? 0) + 1);
  let best: string | null = null;
  let n = 0;
  for (const [k, v] of c)
    if (v > n) {
      best = k;
      n = v;
    }
  return best;
};

export function buildNetwork(
  allArtists: Artist[],
  activities: Activity[],
  frames: FrameEntry[],
  meta: { version: string; generated_at: string | null },
): NetworkData {
  const published = allArtists.filter((a) => a.status === "PUBLISHED");
  const idx = new Map(published.map((a, i) => [a.id, i] as const));

  // distinct activity rows per artist (the ledger repeats some lines)
  const seen = new Set<string>();
  const acts: Activity[] = [];
  const recordCount = new Array<number>(published.length).fill(0);
  for (const r of activities) {
    const ai = idx.get(r.artist_id);
    if (ai == null) continue;
    const key = `${r.artist_id}|${normTitle(r.title)}|${normTitle(r.venue)}|${r.year}`;
    if (seen.has(key)) continue;
    seen.add(key);
    acts.push(r);
    recordCount[ai]! += 1;
  }

  /* ---- frame editions ---- */
  const frameByCode = new Map(frames.map((f) => [f.code, f] as const));
  const keywords = frameKeywords(frames);
  type Draft = {
    id: string;
    label_ko: string;
    label_en: string;
    detail: string | null;
    year: number | null;
    frame: string | null;
    roster: Set<number>;
    cv: Set<number>;
  };
  const drafts = new Map<string, Draft>();
  const editionLabels = new Map<string, string>(); // edition id → normalised edition title
  published.forEach((a, ai) => {
    for (const fe of a.frame_editions ?? []) {
      const fam = familyOf(fe.frame);
      const id = `F:${fam}:${fe.edition ?? ""}`;
      let d = drafts.get(id);
      if (!d) {
        const f = frameByCode.get(fe.frame);
        const ko = f?.name_ko ?? fam;
        const en = f?.name_en ?? f?.name_ko ?? fam;
        const edLabel =
          f?.editions?.find((e) => (e.edition ?? null) === (fe.edition ?? null))?.label ?? null;
        const year = fe.edition && /^\d{4}$/.test(fe.edition) ? Number(fe.edition) : null;
        d = {
          id,
          label_ko: year ? `${ko} ${year}` : ko,
          label_en: year ? `${en} ${year}` : en,
          detail: edLabel,
          year,
          frame: fam,
          roster: new Set(),
          cv: new Set(),
        };
        drafts.set(id, d);
        if (edLabel && normTitle(edLabel).length >= 6) editionLabels.set(id, normTitle(edLabel));
      }
      // A mentor or consultant shares the edition with its mentees, but that is a teaching
      // relation, not co-participation — the same reason CV lines filter STAFF_ROLE above.
      if (!STAFF_ROLE.test(fe.role ?? "")) d.roster.add(ai);
    }
  });

  /** the frame edition a CV line names, if any. The registry name alone is not enough:
   *  the line must carry the edition's year or title, or the artist must already be
   *  on that edition's roster. */
  const editionOf = (norm: string, year: number, ai: number): string | null => {
    for (const [id, label] of editionLabels) {
      if (!id.endsWith(`:${year}`)) continue;
      if (norm.includes(label) || (norm.length >= 8 && label.startsWith(norm))) return id;
    }
    for (const [fam, words] of keywords) {
      if (!words.some((w) => norm.includes(w))) continue;
      const d = drafts.get(`F:${fam}:${year}`);
      if (d && (norm.includes(String(year)) || d.roster.has(ai))) return d.id;
    }
    return null;
  };

  /* ---- CV lines ---- */
  const cvGroups = new Map<string, { ai: Set<number>; titles: string[]; venues: string[] }>();
  for (const r of acts) {
    const norm = normTitle(r.title);
    if (isGeneric(norm) || !Number.isFinite(r.year) || STAFF_ROLE.test(r.role ?? "")) continue;
    const ai = idx.get(r.artist_id)!;
    const ed = editionOf(norm, r.year, ai);
    if (ed) {
      drafts.get(ed)!.cv.add(ai);
      continue;
    }
    const key = `${norm}|${r.year}`;
    let g = cvGroups.get(key);
    if (!g) {
      g = { ai: new Set(), titles: [], venues: [] };
      cvGroups.set(key, g);
    }
    g.ai.add(ai);
    g.titles.push(r.title);
    if (r.venue) g.venues.push(r.venue.trim());
  }
  for (const [key, g] of cvGroups) {
    if (g.ai.size < 2) continue;
    const year = Number(key.slice(key.lastIndexOf("|") + 1));
    const title = mostCommon(g.titles) ?? key;
    drafts.set(`C:${key}`, {
      id: `C:${key}`,
      label_ko: title,
      label_en: title,
      detail: mostCommon(g.venues),
      year,
      frame: null,
      roster: new Set(),
      cv: g.ai,
    });
  }

  const events: NetEvent[] = [...drafts.values()]
    .map((d) => ({
      id: d.id,
      label_ko: d.label_ko,
      label_en: d.label_en,
      detail: d.detail,
      year: d.year,
      frame: d.frame,
      roster: [...d.roster].sort((p, q) => p - q),
      cv: [...d.cv].sort((p, q) => p - q),
    }))
    .filter((e) => new Set([...e.roster, ...e.cv]).size >= 2)
    .sort((p, q) => (q.year ?? 0) - (p.year ?? 0) || p.label_ko.localeCompare(q.label_ko, "ko"));

  return {
    artists: published.map((a, i) => ({
      id: a.id,
      name_ko: a.name_ko,
      name_en: a.name_en,
      records: recordCount[i]!,
    })),
    events,
    ...meta,
  };
}
