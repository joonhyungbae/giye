// SPDX-License-Identifier: AGPL-3.0-only
/**
 * File-backed dataset loader. Reads the snapshot from siteDir()
 * (GIYE_SITE_DIR, or data/site under the working directory).
 * Server-only — import from server functions and route server handlers.
 */
import {
  readFileSync,
  appendFileSync,
  mkdirSync,
  existsSync,
  readdirSync,
  statSync,
} from "node:fs";
import { join } from "node:path";
import { siteDir, workDir } from "./data-paths";
import type {
  Activity,
  Artist,
  ArtistLink,
  BackgroundEntry,
  Collaboration,
  DatasetVersion,
  Vocabulary,
} from "./giye.types";

export type FrameEntry = {
  id: string;
  code: string;
  name_ko: string;
  name_en: string | null;
  years_covered: string | null;
  source_url: string;
  included_count: number;
  roster_count?: number;
  coverage_pct?: number | null;
  status?: string | null;
  stage?: number | null;
  last_fetched_at?: string | null;
  published_count?: number;
  /** Registry order (frames.yml), used to lay events out in a stable sequence. */
  order?: number;
  editions?: FrameEdition[];
  eligibility?: FrameEligibility | null;
};

/** Frame eligibility verdict (F1–F5), recorded in data/frames.yml. */
export type FrameEligibility = {
  decision: "included" | "planned" | "excluded" | "no_public_roster" | string;
  judged_at?: string;
  f1_purpose?: string;
  f2_cohort?: string;
  f3_korea?: string;
  f4_roster?: string;
  f5_period?: string;
  note?: string;
};

export type FrameEdition = {
  edition: string | null;
  label: string | null;
  roster_count: number;
  published_count: number;
  /** On the roster but outside the inclusion criteria (e.g. overseas invited artists). */
  out_of_scope_count?: number;
};

export type CoverageSnapshot = {
  generated_at: string;
  published_artists: number;
  ledger_artists: number;
  frame_count_active: number;
  frame_count_total: number;
  frames: Array<{
    code: string;
    roster_count: number;
    included_count: number;
    coverage_pct: number | null;
    status?: string | null;
    last_fetched_at?: string | null;
  }>;
  cadence?: {
    weekly?: string;
    new_editions?: string;
    quarterly?: string;
  };
};

export type ContentPageRow = {
  id?: string;
  slug: string;
  title_ko: string;
  title_en: string | null;
  body_ko: string;
  body_en: string;
  updated_at: string;
};

export type ContentRevisionRow = {
  id: string;
  slug: string;
  note_ko: string;
  note_en: string | null;
  revised_at: string;
};

export type ResearchRow = {
  id: string;
  title: string;
  authors: string;
  year: number;
  venue: string | null;
  doi: string | null;
  url: string | null;
};

/* The site JSON is read on every request; activities.json alone is tens of megabytes, so parsed
   files are kept until the file on disk changes (the pipeline rewrites them). Arrays are handed
   out as shallow copies, so a caller that sorts its result does not reorder the cache. */
const jsonCache = new Map<string, { stamp: string; value: unknown }>();

/** mtime+size of a file, or "?" where the runtime cannot stat (then a cached value is kept). */
function fileStamp(path: string): string {
  try {
    const st = statSync(path);
    return `${st.mtimeMs}:${st.size}`;
  } catch {
    return "?";
  }
}

function readJson<T>(name: string, fallback: T): T {
  const path = join(siteDir(), name);
  if (!existsSync(path)) return fallback;
  const stamp = fileStamp(path);
  const hit = jsonCache.get(path);
  const value = hit && hit.stamp === stamp ? hit.value : JSON.parse(readFileSync(path, "utf8"));
  if (!hit || hit.stamp !== stamp) jsonCache.set(path, { stamp, value });
  return (Array.isArray(value) ? value.slice() : value) as T;
}

/** Changes whenever any of the named site files changes; for caching derived results. */
export function dataStamp(names: string[]): string {
  return names
    .map((n) => {
      const path = join(siteDir(), n);
      return existsSync(path) ? `${n}=${fileStamp(path)}` : `${n}=0`;
    })
    .join("|");
}

export function loadArtists(): Artist[] {
  return readJson<Artist[]>("artists.json", []).filter((a) => a.status === "PUBLISHED");
}

export function loadAllArtists(): Artist[] {
  return readJson<Artist[]>("artists.json", []);
}

export function loadActivities(): Activity[] {
  return readJson<Activity[]>("activities.json", []);
}

export function loadLinks(): ArtistLink[] {
  return readJson<ArtistLink[]>("links.json", []);
}

export function loadCollaborations(): Collaboration[] {
  return readJson<Collaboration[]>("collaborations.json", []);
}

export function loadBackground(): BackgroundEntry[] {
  return readJson<BackgroundEntry[]>("background.json", []);
}

export function loadVocabularies(): Vocabulary[] {
  return readJson<Vocabulary[]>("vocabularies.json", []).filter((v) => v.is_active !== false);
}

export function loadDatasetVersions(): DatasetVersion[] {
  return readJson<DatasetVersion[]>("dataset_versions.json", []);
}

export function loadFrames(): FrameEntry[] {
  return readJson<FrameEntry[]>("frames.json", []);
}

export function loadCoverage(): CoverageSnapshot | null {
  return readJson<CoverageSnapshot | null>("coverage.json", null);
}

export function loadEmbeddingSpace(): unknown | null {
  return readJson<unknown | null>("embedding.json", null);
}

export function loadContentPages(): ContentPageRow[] {
  return readJson<ContentPageRow[]>("content_pages.json", []);
}

export function loadContentRevisions(): ContentRevisionRow[] {
  return readJson<ContentRevisionRow[]>("content_revisions.json", []);
}

/** Issued ids not published now → their state (scripts/build_site_dataset.py, artist_stubs.json). */
export function loadArtistStubs(): Record<string, "HIDDEN_BY_REQUEST" | "WITHDRAWN"> {
  return readJson<Record<string, "HIDDEN_BY_REQUEST" | "WITHDRAWN">>("artist_stubs.json", {});
}

/** Retired GY id → the id it was merged into (scripts/build_site_dataset.py, gy_redirects.json). */
export function loadGyRedirects(): Record<string, string> {
  return readJson<Record<string, string>>("gy_redirects.json", {});
}

export function loadResearch(): ResearchRow[] {
  return readJson<ResearchRow[]>("research.json", []);
}

export function appendRequest(row: Record<string, unknown>): void {
  const dir = workDir();
  mkdirSync(dir, { recursive: true });
  const path = join(dir, "requests.jsonl");
  appendFileSync(
    path,
    `${JSON.stringify({ ...row, received_at: new Date().toISOString() })}\n`,
    "utf8",
  );
}

export type Snapshot = {
  version: string;
  released_at: string;
  record_ids: string[];
  artist_ids: string[];
};

/** Dataset snapshots written by scripts/write_snapshot.py (data/site/snapshots/<version>.json). */
export function loadSnapshots(): Snapshot[] {
  const dir = join(siteDir(), "snapshots");
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => JSON.parse(readFileSync(join(dir, f), "utf8")) as Snapshot)
    .sort((a, b) => a.released_at.localeCompare(b.released_at));
}
