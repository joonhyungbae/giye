// SPDX-License-Identifier: AGPL-3.0-only
import { VERIFICATION_LABEL } from "@/lib/i18n";

/** Verification words for the study: a self-submitted entry is the artist confirming
 *  their own record, so it reads as "작가 확인"; the source is never named. */
export const verLabel = (v: string) =>
  VERIFICATION_LABEL[v === "SELF_SUBMITTED" ? "VERIFIED_BY_ARTIST" : v];
