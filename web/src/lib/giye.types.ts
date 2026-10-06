// SPDX-License-Identifier: AGPL-3.0-only
export type Artist = {
  id: string;
  name_ko: string;
  name_en: string | null;
  aliases: string[];
  type: "individual" | "collective";
  bio_short: string | null;
  birth_year: number | null;
  birth_year_source_url: string | null;
  active_since: number | null;
  regions: string[];
  medium_tags: string[];
  /** The ledger's field keywords in the order written (fields of research or practice, not media). */
  field_keywords?: string[];
  technique_tags: string[];
  theme_tags: string[];
  frame_status: string;
  frame_codes: string[];
  /** Membership resolved against the frame registry: which event, which edition (year). */
  frame_editions?: { frame: string; edition: string | null; role?: string }[];
  verification: "UNVERIFIED" | "VERIFIED_BY_ARTIST" | "SELF_SUBMITTED";
  /** ISO 3166 codes of where the artist's own CV says they are based (scripts/preprocess, rule L1). */
  countries?: string[];
  /** Fields filled by preprocessing rather than read from a source row: rule and evidence URL. */
  derived?: Record<string, { rule: string; url?: string }>;
  /** found: the artist's own CV has been read · pending: a location is known, not read yet · none. */
  cv_status?: "found" | "pending" | "none";
  /** Other records with the same name that no evidence has joined or told apart yet. */
  same_name?: string[];
  /** On a team: published ids of its members (snapshot, from the ledger's team-expansion markers). */
  members?: string[];
  /** On a member: published ids of the teams that credit them. */
  member_of?: string[];
  /** WITHDRAWN: an issued id whose record is no longer published (e.g. no longer on any roster). */
  status: "PUBLISHED" | "HIDDEN_BY_REQUEST" | "STAGED" | "WITHDRAWN";
  source_url: string;
  source_type: string;
  collected_at: string;
  external_ids: Record<string, string>;
  created_at: string;
  updated_at: string;
};

export type Activity = {
  id: string;
  artist_id: string;
  title: string;
  venue: string | null;
  year: number;
  activity_type: string;
  role: string | null;
  source_url: string;
  source_type: string;
  collected_at: string;
  /** Preprocessing checks shown to readers, e.g. year_from_title (the year may be the title's period). */
  flags?: string[];
};

export type ArtistLink = {
  id: string;
  artist_id: string;
  label: string;
  url: string;
  link_type: string;
  last_checked_at: string | null;
  http_status?: string | null;
  is_dead: boolean;
};

/** A scientist or engineer paired with an artist inside a frame. */
export type Collaboration = {
  id: string;
  artist_id: string;
  collaborator_id: string;
  name_ko: string | null;
  name_en: string | null;
  affiliation: string | null;
  lab: string | null;
  role: string | null;
  frame_code: string | null;
  year: number | null;
  topic: string | null;
  source_url: string;
  collected_at: string;
};

/** A line from the artist's own public CV that is background, not practice: education, jobs, teaching, press. */
export type BackgroundEntry = {
  id: string;
  artist_id: string;
  section: "education" | "employment" | "teaching" | "press";
  title: string;
  venue: string | null;
  year: number;
  role: string | null;
  source_url: string;
  source_type: string;
  collected_at: string;
};

export type Vocabulary = {
  id: string;
  category: "medium" | "technique" | "theme" | "region";
  term_ko: string;
  term_en: string | null;
  definition: string | null;
  is_active: boolean;
};

export type DatasetVersion = {
  id: string;
  version: string;
  released_at: string;
  doi: string | null;
  notes: string | null;
  artist_count: number;
  /** sha256 of the published content; a new row is appended when it changes. */
  content_digest?: string;
};

/** The author and the snapshot every citation names (citations.json dataset entry). */
export type CitationMeta = {
  author: string;
  version: string;
  /** Date the cited content was first published; part of the version label. */
  released_at: string | null;
  year: number;
};

export type ArtistFilters = {
  q?: string;
  medium?: string[];
  technique?: string[];
  theme?: string[];
  region?: string[];
  decade?: string[];
  verification?: string[];
  frame?: string;
  /** Registry frame code: keep artists listed in that event. */
  frameCode?: string;
};

export function filterArtists(artists: Artist[], f: ArtistFilters): Artist[] {
  const has = (arr: string[] | undefined) => arr && arr.length > 0;
  const q = f.q?.trim().toLowerCase();
  return artists.filter((a) => {
    if (q) {
      const hay = [
        a.name_ko,
        a.name_en ?? "",
        ...a.aliases,
        a.bio_short ?? "",
        ...a.medium_tags,
        ...a.technique_tags,
        ...a.theme_tags,
        ...a.regions,
      ]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (has(f.medium) && !f.medium!.some((m) => a.medium_tags.includes(m))) return false;
    if (has(f.technique) && !f.technique!.some((m) => a.technique_tags.includes(m))) return false;
    if (has(f.theme) && !f.theme!.some((m) => a.theme_tags.includes(m))) return false;
    if (has(f.region) && !f.region!.some((m) => a.regions.includes(m))) return false;
    if (has(f.verification) && !f.verification!.includes(a.verification)) return false;
    if (f.frame && a.frame_status !== f.frame) return false;
    if (f.frameCode && !(a.frame_editions ?? []).some((fe) => fe.frame === f.frameCode))
      return false;
    if (has(f.decade)) {
      if (a.active_since == null) return false;
      const bucket = String(Math.floor(a.active_since / 10) * 10);
      if (!f.decade!.includes(bucket)) return false;
    }
    return true;
  });
}

// Deterministic shuffle so server and client agree within one session.
export function seededShuffle<T>(items: T[], seed: number): T[] {
  const out = [...items];
  let s = seed || 1;
  for (let i = out.length - 1; i > 0; i--) {
    s = (s * 1664525 + 1013904223) % 4294967296;
    const j = s % (i + 1);
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}
