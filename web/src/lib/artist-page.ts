// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The person page's view model, built on the server from the snapshot rows.
 *
 * The page receives only what it draws. Rows carry no ids, no artist id, no build stamps and no
 * source type (every record is drawn the same). Each distinct source URL is listed once in
 * `sources`, and a row points at it by index; a person's records usually cite a few pages many
 * times (one CV page for 130 rows). Language labels (tag terms, country names, edition subtitles,
 * the "(예정)" role marker) are resolved here once for both languages, so the client only picks one
 * and the server's and browser's ICU data cannot disagree during hydration.
 */
import type {
  Activity,
  Artist,
  ArtistLink,
  BackgroundEntry,
  CitationMeta,
  Collaboration,
} from "./giye.types";
import type { FrameEntry } from "./giye.data";

/** A Korean and an English form of one label. */
export type Bi = { ko: string; en: string };

/** One distinct source page and the dates rows citing it were collected (sorted, unique). */
export type PageSource = { url: string; collected: string[] };

/** [title, venue, activity_type, role (ko), role (en) or null when equal, source index, year uncertain]. */
export type PageActivity = [string, string | null, string, string | null, string | null, number, 0 | 1];

/** One year of activities, newest year first. */
export type PageYear = { year: number; rows: PageActivity[] };

/** [title, venue, year, role, source index]. */
export type PageBackground = [string, string | null, number, string | null, number];

export type PagePerson = { id: string; name_ko: string; name_en: string | null };

export type ArtistPageData = {
  citation: CitationMeta;
  artist: {
    id: string;
    name_ko: string;
    name_en: string | null;
    /** Alternative spellings. A team's member names are listed under `members`, not here. */
    aliases: string[];
    type: Artist["type"];
    bio_short: string | null;
    status: Artist["status"];
    verification: Artist["verification"];
    cv_status: NonNullable<Artist["cv_status"]>;
    updated: string;
    wikidata: string | null;
    /** Index into `sources` of the person row's own source, or -1 for a stub. */
    source: number;
    birth_year: number | null;
    birth_source: number | null;
    active_since: number | null;
    active_since_derived: boolean;
    countries: Bi[];
    country_url: string | null;
    regions: Bi[];
    medium: Bi[];
    medium_derived: boolean;
    technique: Bi[];
    theme: Bi[];
    /** Roster editions, newest first (the reason the person is in the register). */
    editions: { frame: string; name: Bi; label: { ko: string | null; en: string | null } }[];
  };
  sources: PageSource[];
  years: PageYear[];
  background: { section: BackgroundEntry["section"]; rows: PageBackground[] }[];
  collaborations: {
    name: Bi;
    where: string | null;
    year: number | null;
    topic: string | null;
    source: number;
  }[];
  /** The person's own links: websites first, then social profiles, then the rest. */
  links: { host: string; url: string; type: string; dead: "" | "dead" | "check" }[];
  members: PagePerson[];
  memberOf: PagePerson[];
  sameName: (PagePerson & { editions: Bi[] })[];
};

const HANGUL = /[가-힣]/;

/**
 * L2. On the English page an edition subtitle shows only its Latin-script part: the segments
 * between ":" and parentheses that contain Latin letters and no Hangul ("Blue Signal: 기술이 …" →
 * "Blue Signal"). A subtitle with no such part is left out. The Korean page shows it whole.
 */
export function latinPart(label: string): string | null {
  if (!HANGUL.test(label)) return label;
  const parts = label
    .split(/[:()（）]/)
    .map((p) => p.trim())
    .filter((p) => p && /[A-Za-z]/.test(p) && !HANGUL.test(p));
  return parts.length ? parts.join(": ") : null;
}

/**
 * The "(예정)" marker on a role is added by scripts/apply_cv_extractions.py for a CV entry still
 * labelled upcoming in the current year; the English page shows it as "(upcoming)". Applied to
 * activity and background roles alike.
 */
function roleEn(role: string | null): string | null {
  if (!role) return null;
  const en = role.replace("(예정)", "(upcoming)");
  return en === role ? null : en;
}

/** Country name from an ISO 3166 code, in both languages, resolved once on the server. */
function country(cc: string): Bi {
  const name = (lang: string) => {
    try {
      return new Intl.DisplayNames([lang], { type: "region" }).of(cc) ?? cc;
    } catch {
      return cc;
    }
  };
  return { ko: name("ko"), en: name("en") };
}

/** A link's host without "www.", its label on the page. */
function hostOf(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Comparison key for names: case-folded, letters and digits only. */
function nameKey(value: string | null | undefined): string {
  return (value ?? "").toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
}

/** Numbered source table: each distinct URL once, in the order the page first cites it. */
class SourceTable {
  readonly list: PageSource[] = [];
  private readonly index = new Map<string, number>();

  add(url: string, collected: string): number {
    let i = this.index.get(url);
    if (i === undefined) {
      i = this.list.length;
      this.index.set(url, i);
      this.list.push({ url, collected: [] });
    }
    const day = (collected ?? "").slice(0, 10);
    const dates = this.list[i].collected;
    if (day && !dates.includes(day)) {
      dates.push(day);
      dates.sort();
    }
    return i;
  }
}

export function shapeArtistPage(input: {
  citation: CitationMeta;
  artist: Artist;
  termsEn: Record<string, string>;
  frames: FrameEntry[];
  activities: Activity[];
  background: BackgroundEntry[];
  collaborations: Collaboration[];
  links: ArtistLink[];
  people: Map<string, Artist>;
}): ArtistPageData {
  const { artist, termsEn, people } = input;
  const published = artist.status === "PUBLISHED";
  const sources = new SourceTable();
  const tag = (vs: string[]): Bi[] => vs.map((v) => ({ ko: v, en: termsEn[v] ?? v }));
  const person = (id: string): PagePerson | null => {
    const p = people.get(id);
    return p ? { id: p.id, name_ko: p.name_ko, name_en: p.name_en } : null;
  };
  const members = (artist.members ?? []).map(person).filter((p): p is PagePerson => p != null);
  const memberOf = (artist.member_of ?? [])
    .map(person)
    .filter((p): p is PagePerson => p != null);
  // A team's aliases often hold its members' names (T2 reads members from them). Those are listed
  // as members with links; an alias that is not one of a linked member's names stays an alias.
  const memberNames = new Set(
    members.flatMap((m) => {
      const p = people.get(m.id);
      return [p?.name_ko, p?.name_en, ...(p?.aliases ?? [])].map(nameKey);
    }),
  );
  memberNames.delete("");
  const aliases = artist.aliases.filter((a) => !memberNames.has(nameKey(a)));

  const source = published && artist.source_url ? sources.add(artist.source_url, artist.collected_at) : -1;
  const birthSource =
    published && artist.birth_year_source_url
      ? sources.add(artist.birth_year_source_url, artist.collected_at)
      : null;

  const frameByCode = new Map(input.frames.map((f) => [f.code, f]));
  const editions = [...(artist.frame_editions ?? [])]
    // Newest edition first; an edition without a year goes last; registry order breaks ties.
    .sort((x, y) => {
      const yx = Number.parseInt(x.edition ?? "", 10) || 0;
      const yy = Number.parseInt(y.edition ?? "", 10) || 0;
      const ox = frameByCode.get(x.frame)?.order ?? 999;
      const oy = frameByCode.get(y.frame)?.order ?? 999;
      return yy - yx || ox - oy;
    })
    .map((fe) => {
      const f = frameByCode.get(fe.frame);
      const base = f ? { ko: f.name_ko, en: f.name_en ?? f.name_ko } : { ko: fe.frame, en: fe.frame };
      const withEdition = (n: string) =>
        fe.edition && !n.includes(fe.edition) ? `${n} ${fe.edition}` : n;
      const raw = fe.edition
        ? (f?.editions ?? []).find((e) => e.edition === fe.edition)?.label ?? null
        : null;
      return {
        frame: fe.frame,
        name: { ko: withEdition(base.ko), en: withEdition(base.en) },
        label: { ko: raw || null, en: raw ? latinPart(raw) : null },
      };
    });

  const sorted = [...input.activities].sort(
    (a, b) => b.year - a.year || a.title.localeCompare(b.title, "ko"),
  );
  const years: PageYear[] = [];
  for (const a of sorted) {
    const row: PageActivity = [
      a.title,
      a.venue || null,
      a.activity_type,
      a.role || null,
      roleEn(a.role),
      sources.add(a.source_url, a.collected_at),
      a.flags?.includes("year_from_title") ? 1 : 0,
    ];
    const last = years[years.length - 1];
    if (last && last.year === a.year) last.rows.push(row);
    else years.push({ year: a.year, rows: [row] });
  }

  const sections: BackgroundEntry["section"][] = ["education", "employment", "teaching", "press"];
  const background = sections
    .map((section) => ({
      section,
      rows: input.background
        .filter((b) => b.section === section)
        .map(
          (b): PageBackground => [
            b.title,
            b.venue || null,
            b.year,
            b.role ? (roleEn(b.role) ?? b.role) : null,
            sources.add(b.source_url, b.collected_at),
          ],
        ),
    }))
    .filter((s) => s.rows.length > 0);

  const collaborations = input.collaborations.map((c) => ({
    name: {
      ko: c.name_ko || c.name_en || "",
      en: c.name_en || c.name_ko || "",
    },
    where: [c.affiliation, c.lab].filter(Boolean).join(" · ") || null,
    year: c.year,
    topic: c.topic,
    source: sources.add(c.source_url, c.collected_at),
  }));

  // Websites first, then social profiles, then other kinds; the snapshot's order within a kind.
  const rank: Record<string, number> = { website: 0, social: 1 };
  const linkRank = (type: string) => rank[type] ?? 2;
  const links = [...input.links]
    .sort((a, b) => linkRank(a.link_type) - linkRank(b.link_type))
    .map((l) => ({
      host: hostOf(l.url),
      url: l.url,
      type: l.link_type,
      dead: (!l.is_dead
        ? ""
        : l.http_status === "404" || l.http_status === "410"
          ? "dead"
          : "check") as "" | "dead" | "check",
    }));

  const frameName = new Map(input.frames.map((f) => [f.code, [f.name_ko, f.name_en]]));
  const sameName = (artist.same_name ?? [])
    .map((id) => people.get(id))
    .filter((a): a is Artist => a != null && a.status === "PUBLISHED")
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
    citation: input.citation,
    artist: {
      id: artist.id,
      name_ko: artist.name_ko,
      name_en: artist.name_en,
      aliases,
      type: artist.type,
      bio_short: artist.bio_short,
      status: artist.status,
      verification: artist.verification,
      cv_status: artist.cv_status ?? "none",
      updated: (artist.updated_at ?? "").slice(0, 10),
      wikidata: artist.external_ids?.wikidata ?? null,
      source,
      birth_year: artist.birth_year,
      birth_source: birthSource,
      active_since: artist.active_since,
      active_since_derived: Boolean(artist.derived?.active_since),
      countries: (artist.countries ?? []).map(country),
      country_url: artist.derived?.country?.url ?? null,
      regions: tag(artist.regions),
      medium: tag(artist.medium_tags),
      medium_derived: Boolean(artist.derived?.medium),
      technique: tag(artist.technique_tags),
      theme: tag(artist.theme_tags),
      editions,
    },
    sources: sources.list,
    years,
    background,
    collaborations,
    links,
    members: published ? members : [],
    memberOf: published ? memberOf : [],
    sameName,
  };
}
