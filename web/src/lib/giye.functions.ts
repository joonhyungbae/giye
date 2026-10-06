// SPDX-License-Identifier: AGPL-3.0-only
import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";
import type {
  Activity,
  Artist,
  ArtistLink,
  BackgroundEntry,
  Collaboration,
  DatasetVersion,
  Vocabulary,
} from "./giye.types";
import {
  appendRequest,
  dataStamp,
  loadActivities,
  loadBackground,
  loadCollaborations,
  loadAllArtists,
  loadArtists,
  loadContentPages,
  loadArtistStubs,
  loadGyRedirects,
  loadContentRevisions,
  loadCoverage,
  loadCitationMeta,
  loadDatasetVersions,
  loadFrames,
  loadLinks,
  loadResearch,
  loadSnapshots,
  loadVocabularies,
} from "./giye.data";
import { loadRimOrder } from "./giye.rim";
import { domainOf, fold, foldVenue } from "./record-text";
import { packRecords } from "./study-pack";
import { countFrameDecisions } from "./frame-population";

/**
 * W1. A response that covers many people carries only coded structure: ids, years, type codes, and
 * indexes into small name tables of programmes, venues and source domains (institutions and
 * websites, not persons), plus person names. Free text of a record (title) and its full source URL
 * are served one person at a time (`getArtistRecord`), where the scrape guard's per-visitor limit
 * applies. A list endpoint sends only the fields its page reads.
 * Reason: the overview views need the structure of every record to draw, but the citable record
 * (what, where, which source) is the dataset; serving it per person makes bulk copying cost one
 * counted request per person.
 */

/** The /artists list: only the fields its filters, search and rows read (keeps the page light). */
export const getArtistIndex = createServerFn({ method: "GET" }).handler(async () => {
  return loadArtists()
    .sort((a, b) => a.name_ko.localeCompare(b.name_ko, "ko"))
    .map((a) => ({
      id: a.id,
      name_ko: a.name_ko,
      name_en: a.name_en,
      aliases: a.aliases,
      medium_tags: a.medium_tags,
      technique_tags: a.technique_tags,
      theme_tags: a.theme_tags,
      regions: a.regions,
      verification: a.verification,
      frame_status: a.frame_status,
      frame_editions: (a.frame_editions ?? []).map((fe) => ({
        frame: fe.frame,
        edition: fe.edition ?? null,
      })),
      active_since: a.active_since,
    }));
});

export const getVocabularies = createServerFn({ method: "GET" }).handler(async () => {
  return loadVocabularies() as Vocabulary[];
});

export const getDatasetVersions = createServerFn({ method: "GET" }).handler(async () => {
  return loadDatasetVersions().sort((a, b) =>
    b.released_at.localeCompare(a.released_at),
  ) as DatasetVersion[];
});

/** An issued id that is not published now answers with its state and nothing else. */
function stubArtist(id: string, status: "HIDDEN_BY_REQUEST" | "WITHDRAWN"): Artist {
  return {
    id,
    name_ko: "",
    name_en: null,
    aliases: [],
    type: "individual",
    bio_short: null,
    birth_year: null,
    birth_year_source_url: null,
    active_since: null,
    regions: [],
    medium_tags: [],
    technique_tags: [],
    theme_tags: [],
    frame_status: "OUT_OF_FRAME",
    frame_codes: [],
    verification: "UNVERIFIED",
    status,
    source_url: "",
    source_type: "PUBLIC_RECORD",
    collected_at: "",
    external_ids: {},
    created_at: "",
    updated_at: "",
  };
}

/** A merged record's id stays citable: the id of the record it went into, or null. */
export const getArtistRedirect = createServerFn({ method: "GET" })
  .inputValidator((d: unknown) => z.object({ id: z.string() }).parse(d))
  .handler(async ({ data }) => loadGyRedirects()[data.id] ?? null);

export const getArtistRecord = createServerFn({ method: "GET" })
  .inputValidator((d: unknown) => z.object({ id: z.string() }).parse(d))
  .handler(async ({ data }) => {
    let artist = loadAllArtists().find((a) => a.id === data.id) ?? null;
    const stub = loadArtistStubs()[data.id];
    if (!artist && stub) artist = stubArtist(data.id, stub);
    if (!artist) return null;
    // Names of the events this artist is listed in, so memberships read as
    // "Example Workshop 2021" rather than registry codes.
    const memberCodes = new Set((artist.frame_editions ?? []).map((fe) => fe.frame));
    const frames = loadFrames()
      .filter((f) => memberCodes.has(f.code))
      .map((f) => ({
        code: f.code,
        name_ko: f.name_ko,
        name_en: f.name_en,
        order: f.order ?? 999,
        labels: Object.fromEntries(
          (f.editions ?? []).filter((e) => e.edition).map((e) => [e.edition as string, e.label]),
        ),
      }));
    // English labels for this person's region and practice tags (vocabularies.json term_en), so the
    // English page shows "Seoul" rather than the Korean term. Tags without an entry stay as written.
    const tagged = new Set([
      ...artist.regions,
      ...artist.medium_tags,
      ...artist.technique_tags,
      ...artist.theme_tags,
    ]);
    const terms_en: Record<string, string> = Object.fromEntries(
      (loadVocabularies() as Vocabulary[])
        .filter((v) => tagged.has(v.term_ko) && v.term_en)
        .map((v) => [v.term_ko, v.term_en as string]),
    );
    const stamp = await studyStampHash();
    const citation = loadCitationMeta();
    if (artist.status !== "PUBLISHED") {
      return {
        stamp,
        citation,
        artist,
        terms_en,
        activities: [] as Activity[],
        links: [] as ArtistLink[],
        collaborations: [] as Collaboration[],
        background: [] as BackgroundEntry[],
        frames,
        sameName: [] as {
          id: string;
          name_ko: string;
          name_en: string | null;
          editions: { ko: string; en: string }[];
        }[],
      };
    }
    const order = studyOrder();
    const activities = loadActivities()
      .filter((a) => a.artist_id === artist.id)
      .sort((a, b) => b.year - a.year || a.title.localeCompare(b.title, "ko"))
      .map((a) => {
        const pos = order.byId.get(a.id);
        // Not in the study order: leave the row without ord (W2).
        return pos ? { ...a, ord: pos.ord } : a;
      });
    const links = loadLinks().filter((l) => l.artist_id === artist.id);
    const collaborations = loadCollaborations().filter((c) => c.artist_id === artist.id);
    const background = loadBackground().filter((b) => b.artist_id === artist.id);
    // The same-name records, with the events each is listed in, so a reader can tell whether
    // they are one person.
    const others = new Set(artist.same_name ?? []);
    const frameName = new Map(loadFrames().map((f) => [f.code, [f.name_ko, f.name_en]]));
    const sameName = loadArtists()
      .filter((a) => others.has(a.id))
      .map((a) => ({
        id: a.id,
        name_ko: a.name_ko,
        name_en: a.name_en,
        editions: (a.frame_editions ?? []).map((fe) => {
          const [ko, en] = frameName.get(fe.frame) ?? [fe.frame, fe.frame];
          return {
            ko: [ko, fe.edition].filter(Boolean).join(" "),
            en: [en || ko, fe.edition].filter(Boolean).join(" "),
          };
        }),
      }));
    return {
      stamp,
      citation,
      artist,
      terms_en,
      activities,
      links,
      collaborations,
      background,
      frames,
      sameName,
    };
  });

export const getHomeStats = createServerFn({ method: "GET" }).handler(async () => {
  const artists = loadArtists();
  const versions = loadDatasetVersions().sort((a, b) => b.released_at.localeCompare(a.released_at));
  const coverage = loadCoverage();
  // Admitted vs adjacent, from each frame's eligibility decision. The active
  // count in coverage.json adds those two together.
  const population = countFrameDecisions(loadFrames());
  return {
    artistCount: artists.length,
    version: versions[0]?.version ?? "0.1",
    admittedProgrammes: population.admitted,
    adjacentStrands: population.adjacent,
    lastRefreshedAt: coverage?.generated_at ?? versions[0]?.released_at ?? null,
    cadence: coverage?.cadence ?? null,
    citation: loadCitationMeta(),
  };
});

export const getCoverage = createServerFn({ method: "GET" }).handler(async () => {
  return loadCoverage();
});

export const getContentPage = createServerFn({ method: "GET" })
  .inputValidator((d: unknown) => z.object({ slug: z.string() }).parse(d))
  .handler(async ({ data }) => {
    const page = loadContentPages().find((p) => p.slug === data.slug) ?? null;
    const revisions = loadContentRevisions()
      .filter((r) => r.slug === data.slug)
      .sort((a, b) => b.revised_at.localeCompare(a.revised_at));
    return { page, revisions };
  });

export const getFrameEntries = createServerFn({ method: "GET" }).handler(async () => {
  // Registry order is the frame's order, then its code.
  return loadFrames().sort(
    (a, b) => (a.order ?? 999) - (b.order ?? 999) || a.code.localeCompare(b.code),
  );
});

export const getResearch = createServerFn({ method: "GET" }).handler(async () => {
  return loadResearch().sort((a, b) => b.year - a.year);
});

export const submitRequest = createServerFn({ method: "POST" })
  .inputValidator((d: unknown) =>
    z
      .object({
        request_type: z.enum(["add", "correct", "hide", "self", "same"]),
        artist_id: z.string().optional().nullable(),
        other_id: z.string().optional().nullable(),
        requester_email: z.union([z.string().email(), z.literal("")]).default(""),
        name_ko: z.string().trim().max(200).default(""),
        name_en: z.string().trim().max(200).default(""),
        sns_url: z
          .union([
            z
              .string()
              .url()
              .regex(/^https?:\/\//),
            z.literal(""),
          ])
          .default(""),
        website_url: z
          .union([
            z
              .string()
              .url()
              .regex(/^https?:\/\//),
            z.literal(""),
          ])
          .default(""),
        cv_url: z
          .union([
            z
              .string()
              .url()
              .regex(/^https?:\/\//),
            z.literal(""),
          ])
          .default(""),
        message: z.string().max(4000).default(""),
      })
      .superRefine((v, ctx) => {
        if (v.request_type === "add") {
          if (!v.name_ko && !v.name_en) ctx.addIssue({ code: "custom", message: "Name required" });
          if (!v.sns_url && !v.website_url && !v.cv_url)
            ctx.addIssue({ code: "custom", message: "Source link required" });
        } else if (v.request_type === "self") {
          // The URL is the evidence (we read the CV there); no email needed.
          if (!v.artist_id) ctx.addIssue({ code: "custom", message: "Artist ID required" });
          if (!v.cv_url && !v.website_url && !v.sns_url)
            ctx.addIssue({ code: "custom", message: "A CV or website link required" });
        } else if (v.request_type === "same") {
          if (!v.artist_id || !v.other_id || v.artist_id === v.other_id)
            ctx.addIssue({ code: "custom", message: "Two artist IDs required" });
        } else if (!v.requester_email || v.message.length < 5) {
          ctx.addIssue({ code: "custom", message: "Email and message required" });
        }
      })
      .parse(d),
  )
  .handler(async ({ data }) => {
    appendRequest({
      ...data,
      artist_id: data.request_type === "add" ? null : data.artist_id || null,
      other_id: data.request_type === "same" ? data.other_id || null : null,
      status: "open",
    });
    return { ok: true };
  });

/**
 * Mixed into the home ETag with the data stamp: the time this server process started. Every code
 * deploy restarts the process (deploy/push.sh), so a new payload shape never revalidates against
 * a body cached from older code; a data-only push changes the stamp instead.
 */
const STUDY_PAYLOAD_BUILD = String(Date.now());

/** The home canvas payload (packing ~60k rows is not free); held until the data changes. */
let studyCache: { stamp: string; json: string; etag: string } | null = null;
const STUDY_FILES = [
  "artists.json",
  "activities.json",
  "frames.json",
  "rim_order.json",
  "coverage.json",
  "dataset_versions.json",
];

let stampHashCache: { data: string; hash: string } | null = null;

/**
 * SHA-256 hex of the data stamp and this process's payload build. Quoted, this is the home ETag.
 * The home payload, getArtistRecord and searchStudyRecords all send the same hex as `stamp` (W2).
 */
async function studyStampHash(data = dataStamp(STUDY_FILES)): Promise<string> {
  if (stampHashCache?.data === data) return stampHashCache.hash;
  const bytes = new TextEncoder().encode(`${data}\n${STUDY_PAYLOAD_BUILD}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hash = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  stampHashCache = { data, hash };
  return hash;
}

/** If-None-Match uses weak comparison: a listed tag matches with or without the W/ prefix. */
function ifNoneMatch(header: string | null, etag: string): boolean {
  if (!header) return false;
  const want = etag.replace(/^W\//, "").replaceAll('"', "");
  return header.split(",").some((part) => {
    const token = part.trim();
    if (token === "*") return true;
    return token.replace(/^W\//, "").replaceAll('"', "") === want;
  });
}

export const getStudyData = createServerFn({ method: "GET" }).handler(async () => {
  const stamp = dataStamp(STUDY_FILES);
  if (studyCache?.stamp !== stamp) {
    const hash = await studyStampHash(stamp);
    const json = JSON.stringify(buildStudyData(hash));
    studyCache = { stamp, json, etag: `"${hash}"` };
  }
  // A raw response: the payload skips the server-function serializer (which escaped every string
  // of this multi-megabyte body) and the browser parses it with JSON.parse. Compression is left to
  // the host (the deploy target compresses application/json at the edge); doing it here would need
  // node:zlib, which the worker runtime does not have.
  // Repeat visits revalidate. Cache-Control private, no-cache stores the body in the browser but
  // requires a check. The server-function client calls fetch with no cache mode, so the browser
  // default applies and turns a matching 304 into the cached 200 before the loader reads res.json().
  const headers: Record<string, string> = {
    ETag: studyCache.etag,
    "Cache-Control": "private, no-cache",
  };
  const { getRequest } = await import("@tanstack/react-start/server");
  if (ifNoneMatch(getRequest().headers.get("if-none-match"), studyCache.etag)) {
    return new Response(null, { status: 304, headers });
  }
  headers["content-type"] = "application/json";
  return new Response(studyCache.json, { headers });
});

/** Title order inside one artist, so the client can keep received order (see buildStudyData). */
const RECORD_TITLE = new Intl.Collator("ko");

type StudyPos = { artist_id: string; ord: number };

type StudyOrder = {
  /** Published activities in study order. */
  rows: ReturnType<typeof loadActivities>;
  /** Record id → address. An id absent from this map is not in the study order. */
  byId: Map<string, StudyPos>;
};

let studyOrderCache: { stamp: string; value: StudyOrder } | null = null;

/**
 * W2. The home payload carries no record ids. A record is addressed by (artist id, ord): ord is its
 * position among that artist's records in the study order — published records sorted by artist id,
 * then title (Korean collator), then record id as a tie-break so the order is total. One server
 * helper computes the study order and every server function that refers to study records uses it.
 * The payload carries `stamp` (the data stamp hash already used for the ETag); getArtistRecord and
 * searchStudyRecords return the same `stamp`, and the client matches by ord only when the stamps
 * are equal (after a data update between the two calls it opens the sheet without a highlight, and
 * nothing else changes).
 * Reason: ids were 54% of the compressed payload; the canvas only needs to point back into one
 * artist's rows.
 *
 * `ord` counts from 0. Artist ids and record ids compare as strings; titles use the Korean collator.
 * Cached on the artists and activities stamp. getStudyData, getArtistRecord and searchStudyRecords
 * all read this.
 */
function studyOrder(): StudyOrder {
  const stamp = dataStamp(["artists.json", "activities.json"]);
  if (studyOrderCache?.stamp === stamp) return studyOrderCache.value;
  const published = new Set(loadArtists().map((a) => a.id));
  const rows = loadActivities()
    .filter((r) => published.has(r.artist_id))
    .sort((a, b) => {
      if (a.artist_id !== b.artist_id) return a.artist_id < b.artist_id ? -1 : 1;
      const byTitle = RECORD_TITLE.compare(a.title, b.title);
      if (byTitle !== 0) return byTitle;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
  const byId = new Map<string, StudyPos>();
  const seen = new Map<string, number>();
  for (const r of rows) {
    const ord = seen.get(r.artist_id) ?? 0;
    seen.set(r.artist_id, ord + 1);
    byId.set(r.id, { artist_id: r.artist_id, ord });
  }
  const value = { rows, byId };
  studyOrderCache = { stamp, value };
  return value;
}

/** Everything the home "Archival Study" needs: every published artist (artists.json holds no hidden one), every sourced record, the frames. The fields are listed on /privacy and in docs/DEPLOY.md W1; change them together. */
function buildStudyData(stamp: string) {
  const artists = loadAllArtists().map((a) => ({
    id: a.id,
    name_ko: a.name_ko,
    name_en: a.name_en,
    frame_codes: a.frame_codes,
    frame_status: a.frame_status,
    verification: a.verification,
    status: a.status,
    collected_at: a.collected_at,
    active_since: a.active_since,
    medium_tags: a.medium_tags,
    regions: a.regions,
  }));
  // ~60k rows: sent as one packed column string (see study-pack.ts), unpacked in the home loader.
  // Study order (artist, title, record id) before packing: every subset of one artist's rows (a year,
  // or the shared ring before 2017) is then in that order, as the canvas used to sort it, without
  // the title or the record id travelling to the browser.
  const ordered = studyOrder();
  const indexOf = new Map<string, number>();
  const rows = ordered.rows.map((r, i) => {
    indexOf.set(r.id, i);
    return {
      artist_id: r.artist_id,
      venue: r.venue,
      year: r.year,
      activity_type: r.activity_type,
      domain: domainOf(r.source_url),
      collected_at: r.collected_at,
    };
  });
  // W3. A record's venue goes into the home payload only when its folded form (foldVenue:
  // trim and lower case — the same fold the chord code uses) occurs on records of 2+ different
  // published artists. Otherwise the venue is sent as null.
  // Reason: an unshared venue cannot form a chord (chords join records of different artists at
  // the same venue), and a venue that only one person used is record text of that person (W1).
  // The record sheet still shows every venue from getArtistRecord.
  const artistsAtVenue = new Map<string, Set<string>>();
  for (const r of rows) {
    const folded = foldVenue(r.venue);
    let at = artistsAtVenue.get(folded);
    if (!at) artistsAtVenue.set(folded, (at = new Set()));
    at.add(r.artist_id);
  }
  const sharedVenue = new Set<string>();
  for (const [folded, at] of artistsAtVenue) if (at.size >= 2) sharedVenue.add(folded);
  const records_packed = packRecords(
    rows.map((r) => (sharedVenue.has(foldVenue(r.venue)) ? r : { ...r, venue: null })),
  );
  const frames = loadFrames().map((f) => ({
    code: f.code,
    name_ko: f.name_ko,
    name_en: f.name_en,
    roster_count: f.roster_count ?? null,
    included_count: f.included_count ?? null,
  }));
  const versions = loadDatasetVersions().sort((a, b) => b.released_at.localeCompare(a.released_at));
  // Snapshot files store record ids. This payload addresses rows by index; an id that is not in
  // the current study order is dropped (W2).
  const snapshots = loadSnapshots().map((sn) => ({
    version: sn.version,
    released_at: sn.released_at,
    rows: sn.record_ids.flatMap((id) => {
      const i = indexOf.get(id);
      return i === undefined ? [] : [i];
    }),
  }));
  return {
    artists,
    stamp,
    records_packed,
    frames,
    snapshots,
    rim: loadRimOrder(),
    version: versions[0]?.version ?? "0.1",
    generated_at: loadCoverage()?.generated_at ?? null,
  };
}

const squash = (v: string) => v.replace(/\s+/g, "");

type RecordHit = {
  id: string;
  /** W2. Position of this record among the artist's rows in the study order. */
  ord: number;
  artist_id: string;
  year: number;
  title: string;
  venue: string | null;
};

/** Folded published rows for title/venue search. Titles are not in the home payload (W1). */
type SearchRow = RecordHit & {
  titleKey: string;
  venueKey: string;
  titleSq: string;
  venueSq: string;
};

let recordSearchCache: { stamp: string; rows: SearchRow[] } | null = null;

function publishedSearchRows(): SearchRow[] {
  const stamp = dataStamp(["artists.json", "activities.json"]);
  if (recordSearchCache?.stamp === stamp) return recordSearchCache.rows;
  const order = studyOrder();
  // The ledger repeats one activity; keep the first in study order (artist, title, venue, year),
  // as the canvas search used to. Keys are folded so case and NFC do not split a duplicate.
  const seen = new Set<string>();
  const rows: SearchRow[] = [];
  for (const r of order.rows) {
    const titleKey = fold(r.title);
    const venueKey = fold(r.venue);
    const key = `${r.artist_id}|${titleKey}|${venueKey}|${r.year}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const pos = order.byId.get(r.id);
    if (!pos) continue;
    rows.push({
      id: r.id,
      ord: pos.ord,
      artist_id: r.artist_id,
      year: r.year,
      title: r.title,
      venue: r.venue,
      titleKey,
      venueKey,
      titleSq: squash(titleKey),
      venueSq: squash(venueKey),
    });
  }
  recordSearchCache = { stamp, rows };
  return rows;
}

function scoreField(
  folded: string,
  stripped: string,
  weight: number,
  query: string,
  tight: string,
  tokens: string[],
): number {
  if (!folded) return 0;
  let score = 0;
  if (folded === query) score += 6 * weight;
  else if (folded.startsWith(query)) score += 4 * weight;
  else if (folded.includes(query) || stripped.includes(tight)) score += 3 * weight;
  if (tokens.length > 1) for (const token of tokens) if (folded.includes(token)) score += weight;
  return score;
}

/**
 * Title and venue search for the home canvas. Names stay in the bulk payload and are matched
 * in the browser; a record's free text is matched here, at most 30 hits.
 */
export const searchStudyRecords = createServerFn({ method: "GET" })
  .inputValidator((d: unknown) => z.object({ q: z.string().trim().min(1).max(80) }).parse(d))
  .handler(async ({ data }) => {
    const stamp = await studyStampHash();
    const query = fold(data.q).trim();
    if (!query) return { stamp, hits: [] as RecordHit[] };
    const tokens = query.split(/\s+/).filter(Boolean);
    const tight = squash(query);
    const ranked: { row: SearchRow; score: number; i: number }[] = [];
    const rows = publishedSearchRows();
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i]!;
      const score =
        scoreField(row.titleKey, row.titleSq, 2, query, tight, tokens) +
        scoreField(row.venueKey, row.venueSq, 1.5, query, tight, tokens);
      if (score > 0) ranked.push({ row, score, i });
    }
    ranked.sort((a, b) => b.score - a.score || a.i - b.i);
    return {
      stamp,
      hits: ranked.slice(0, 30).map(({ row }) => ({
        id: row.id,
        ord: row.ord,
        artist_id: row.artist_id,
        year: row.year,
        title: row.title,
        venue: row.venue,
      })),
    };
  });
