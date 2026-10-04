// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Where the site snapshot and self-reports live.
 *
 * GIYE_SITE_DIR is the directory `giye publish` writes (`artists.json` and the
 * other snapshot files). GIYE_WORK_DIR receives self-reports (`requests.jsonl`).
 * Unset, both sit under the process working directory: `<cwd>/data/site` and
 * `<cwd>/data/work`. A blank value is treated as unset.
 */
import { join } from "node:path";

function fromEnv(name: string, fallback: string): string {
  const value = process.env[name]?.trim();
  return value ? value : fallback;
}

/** Snapshot directory. `GIYE_SITE_DIR`, or `<cwd>/data/site`. */
export function siteDir(): string {
  return fromEnv("GIYE_SITE_DIR", join(process.cwd(), "data", "site"));
}

/** Self-report directory. `GIYE_WORK_DIR`, or `<cwd>/data/work`. */
export function workDir(): string {
  return fromEnv("GIYE_WORK_DIR", join(process.cwd(), "data", "work"));
}
