// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Instance configuration. Another field sets these and leaves the components.
 *
 * Vite inlines `import.meta.env.VITE_*` when `bun run dev` starts and when
 * `bun run build` runs, on the server bundle and the client bundle together.
 * Unset origin and contact use neutral placeholders on example.org. A
 * deployment sets `VITE_GIYE_ORIGIN` and `VITE_GIYE_CONTACT_EMAIL` before
 * `bun run dev` or `bun run build` (see `.env.production.example`). The
 * running `node .output/server/index.mjs` process does not re-read them.
 */

function vite(name: keyof ImportMetaEnv, fallback: string): string {
  const raw = import.meta.env[name];
  if (typeof raw === "string" && raw.trim() !== "") return raw.trim();
  return fallback;
}

export const site = {
  /** Public origin, no trailing slash. `VITE_GIYE_ORIGIN`. */
  origin: vite("VITE_GIYE_ORIGIN", "https://example.org").replace(/\/$/, ""),
  /** Contact address. `VITE_GIYE_CONTACT_EMAIL`. */
  contactEmail: vite("VITE_GIYE_CONTACT_EMAIL", "contact@example.org"),
  /**
   * Field name in Korean, the "미디어아트" in "한국 미디어아트 분야".
   * `VITE_GIYE_FIELD_KO`.
   */
  fieldKo: vite("VITE_GIYE_FIELD_KO", "미디어아트"),
  /**
   * Field name in English, the "Korean media art" in "the Korean media art field".
   * `VITE_GIYE_FIELD_EN`.
   */
  fieldEn: vite("VITE_GIYE_FIELD_EN", "Korean media art"),
};

export function contactHref(): string {
  return `mailto:${site.contactEmail}`;
}

/** First sentence of the archive statement, Korean. */
export function archiveSentenceKo(): string {
  return `기예(Giye)는 한국 ${site.fieldKo} 분야의 공개 명단에 오른 모든 사람을 출처와 함께 기록하는 독립 연구 아카이브입니다.`;
}

/** First sentence of the archive statement, English. */
export function archiveSentenceEn(): string {
  return `Giye is an independent research archive that records everyone on the public rosters of the ${site.fieldEn} field, with the source of every fact.`;
}

/**
 * "Korean media artist" / "Korean media artists" from the English field name.
 * A name ending in "art" becomes "artist(s)"; any other name is followed by
 * " artist(s)". The giye.org default stays "Korean media artist(s)".
 */
export function fieldPeopleEn(plural: boolean): string {
  if (/art$/i.test(site.fieldEn)) {
    return site.fieldEn.replace(/art$/i, plural ? "artists" : "artist");
  }
  return plural ? `${site.fieldEn} artists` : `${site.fieldEn} artist`;
}

/** "Korean Media Artists Index" from the English field name. */
export function datasetIndexTitle(): string {
  const people = fieldPeopleEn(true).replace(/(^|\s)\S/g, (word) => word.toUpperCase());
  return `${people} Index`;
}
