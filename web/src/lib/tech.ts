// SPDX-License-Identifier: AGPL-3.0-only
/**
 * W4. Technology words in record titles.
 *
 * A person's first year for a technology is the earliest year of their published records whose
 * title matches that technology's pattern. The six patterns are the vocabulary of the
 * technology-diffusion study (AI, XR, data, bio, robot, NFT), copied unchanged so the exhibit and
 * the study read the same words; matching is case-insensitive, as there.
 * Reason: titles are not in the home payload (W1); one year per person and technology is coded
 * structure of the same kind as medium_tags, and it is all the exhibit needs.
 * Known over-matches of the shared vocabulary (kept for comparability): "AR" and "MR" also match
 * those letters standing alone in a title, and "생명" matches any title with that word.
 */

export const TECH_CODES = ["ai", "xr", "data", "bio", "robot", "nft"] as const;
export type TechCode = (typeof TECH_CODES)[number];

export const TECH_LABEL: Record<TechCode, [string, string]> = {
  ai: ["AI·생성", "AI · generative"],
  xr: ["XR·메타버스", "XR · metaverse"],
  data: ["데이터·알고리즘", "data · algorithm"],
  bio: ["바이오", "bio"],
  robot: ["로봇·키네틱", "robot · kinetic"],
  nft: ["NFT·블록체인", "NFT · blockchain"],
};

const TECH_PATTERN: Record<TechCode, RegExp> = {
  ai: /\bA\.?I\.?\b|artificial intelligence|machine learning|deep learning|neural|인공지능|머신\s?러닝|딥\s?러닝|\bGAN\b|generative|생성형/i,
  xr: /\bVR\b|\bAR\b|\bXR\b|\bMR\b|virtual reality|augmented reality|mixed reality|metaverse|가상\s?현실|증강\s?현실|메타버스/i,
  data: /algorithm|알고리즘|\bdata\b|데이터/i,
  bio: /\bbio|바이오|생명|\bDNA\b|microb|미생물/i,
  robot: /robot|로봇|kinetic|키네틱/i,
  nft: /\bNFT\b|blockchain|블록체인|crypto/i,
};

/** First year per technology for each artist id, from published records (W4). */
export function techFirstYears(
  rows: Array<{ artist_id: string; title: string | null; year: number }>,
): Map<string, Partial<Record<TechCode, number>>> {
  const out = new Map<string, Partial<Record<TechCode, number>>>();
  for (const r of rows) {
    if (!r.title || !Number.isFinite(r.year)) continue;
    for (const k of TECH_CODES) {
      if (!TECH_PATTERN[k].test(r.title)) continue;
      let m = out.get(r.artist_id);
      if (!m) out.set(r.artist_id, (m = {}));
      const prev = m[k];
      if (prev === undefined || r.year < prev) m[k] = r.year;
    }
  }
  return out;
}
