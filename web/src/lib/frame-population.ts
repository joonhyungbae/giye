// SPDX-License-Identifier: AGPL-3.0-only
/**
 * How many programmes the sampling frame admits.
 *
 * `coverage.json` `frame_count_active` counts every row with status "active".
 * That number mixes admitted programmes (`eligibility.decision == "included"`)
 * with an adjacent strand (`"adjacent"`, kept public, not admitted). The pages
 * that state the size of the frame use these two counts instead.
 */

export function countFrameDecisions(
  frames: readonly { eligibility?: { decision?: string } | null }[],
): { admitted: number; adjacent: number } {
  let admitted = 0;
  let adjacent = 0;
  for (const frame of frames) {
    const decision = frame.eligibility?.decision;
    if (decision === "included") admitted += 1;
    else if (decision === "adjacent") adjacent += 1;
  }
  return { admitted, adjacent };
}

/** "채택된 프로그램 23개와 인접 갈래 1개" */
export function admittedProgrammesKo(admitted: number, adjacent: number): string {
  return `채택된 프로그램 ${admitted}개와 인접 갈래 ${adjacent}개`;
}

/** "23 admitted programmes and 1 adjacent strand" */
export function admittedProgrammesEn(admitted: number, adjacent: number): string {
  const programmes = admitted === 1 ? "programme" : "programmes";
  const strands = adjacent === 1 ? "strand" : "strands";
  return `${admitted} admitted ${programmes} and ${adjacent} adjacent ${strands}`;
}
