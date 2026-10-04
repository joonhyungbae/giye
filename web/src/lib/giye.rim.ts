// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Where programmes and artists sit on the home ledger ring (scripts/build_rim_order.py →
 * rim_order.json in the site directory). Server-only. Absent file → null (the rim then falls
 * back to each artist's first listed programme).
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { StudyRim } from "@/components/study/model";
import { siteDir } from "./data-paths";

export function loadRimOrder(): StudyRim | null {
  const path = join(siteDir(), "rim_order.json");
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8")) as StudyRim;
  } catch {
    return null;
  }
}
