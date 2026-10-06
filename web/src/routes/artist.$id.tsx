// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute, Link, notFound, redirect } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { getArtistPage, getArtistRedirect } from "@/lib/giye.functions";
import { ACTIVITY_TYPE_LABEL, LINK_TYPE_LABEL, useLang, VERIFICATION_LABEL } from "@/lib/i18n";
import { CiteDialog } from "@/components/CiteDialog";
import { absoluteUrl, pageTitle, site } from "@/config/site";
import { Button } from "@/components/ui/button";
import type { Bi, PageBackground, PagePerson, PageSource, PageYear } from "@/lib/artist-page";

export const Route = createFileRoute("/artist/$id")({
  loader: async ({ params }) => {
    const record = await getArtistPage({ data: { id: params.id } });
    if (!record) {
      const into = await getArtistRedirect({ data: { id: params.id } });
      if (into) throw redirect({ to: "/artist/$id", params: { id: into }, statusCode: 301 });
      throw notFound();
    }
    return record;
  },
  head: ({ params, loaderData }) => {
    if (!loaderData) {
      return {
        meta: [{ title: pageTitle(params.id) }, { name: "robots", content: "noindex" }],
      };
    }
    const a = loaderData.artist;
    const hidden = a.status !== "PUBLISHED"; // hidden or withdrawn: no name in the title
    // name_ko falls back to name_en in the snapshot; do not print the same name twice.
    const name =
      a.name_en && a.name_en !== a.name_ko ? `${a.name_ko} ${a.name_en}` : a.name_ko;
    const tabTitle = pageTitle(hidden ? params.id : name);
    const title = hidden ? `${params.id} — 기예 Giye` : `${name} (${a.id}) — 기예 Giye`;
    const url = absoluteUrl(`/artist/${params.id}`);
    const description = hidden
      ? a.status === "WITHDRAWN"
        ? "더 이상 공개하지 않는 기록입니다. / This record is no longer published."
        : "요청에 따라 비공개 처리된 기록입니다. / Hidden at the artist's request."
      : (a.bio_short ??
        `${a.name_ko}${a.name_en ? ` / ${a.name_en}` : ""} — 기예 Giye 작가 기록 ${a.id}.`);
    return {
      meta: [
        { title: tabTitle },
        { name: "description", content: description },
        { property: "og:title", content: title },
        { property: "og:description", content: description },
        { property: "og:type", content: "profile" },
        { property: "og:url", content: url },
        ...(hidden ? [{ name: "robots", content: "noindex" }] : []),
      ],
      links: [{ rel: "canonical", href: url }],
      scripts: hidden
        ? []
        : [
            {
              type: "application/ld+json",
              children: JSON.stringify({
                "@context": "https://schema.org",
                "@type": a.type === "collective" ? "Organization" : "Person",
                name: a.name_ko,
                alternateName: [a.name_en, ...a.aliases].filter(Boolean),
                identifier: a.id,
                description: a.bio_short ?? undefined,
                url,
                sameAs: a.wikidata ? [`https://www.wikidata.org/wiki/${a.wikidata}`] : undefined,
                member: loaderData.members.length
                  ? loaderData.members.map((m) => ({
                      "@type": "Person",
                      name: m.name_ko,
                      url: absoluteUrl(`/artist/${m.id}`),
                    }))
                  : undefined,
                memberOf: loaderData.memberOf.length
                  ? loaderData.memberOf.map((m) => ({
                      "@type": "Organization",
                      name: m.name_ko,
                      url: absoluteUrl(`/artist/${m.id}`),
                    }))
                  : undefined,
              }),
            },
          ],
    };
  },
  component: ArtistPage,
});

/**
 * A record's source as a numbered reference: "[n]" links straight to the cited page, and the
 * section's source list below gives the full URL and collection dates once per distinct page.
 * Numbers are page-wide (the order the page first cites each page), so the same page keeps its
 * number in every section.
 */
function SourceRef({ index, url }: { index: number; url: string }) {
  const { t } = useLang();
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      aria-label={t(`출처 ${index + 1} (새 창)`, `Source ${index + 1} (opens in a new tab)`)}
      className="ml-1.5 whitespace-nowrap font-mono text-[11px] text-accent no-underline hover:underline"
    >
      [{index + 1}]
    </a>
  );
}

/** The distinct pages a section cites, each once, with its number. */
function SourceList({ sources, used }: { sources: PageSource[]; used: number[] }) {
  const { t } = useLang();
  if (used.length === 0) return null;
  return (
    <div className="mt-8 max-w-3xl border-t border-border pt-4 text-xs text-muted-foreground">
      <h3 className="font-mono text-[11px] uppercase">{t("출처", "Sources")}</h3>
      <ol className="mt-2 space-y-1.5">
        {used.map((i) => (
          <li key={i} id={`source-${i + 1}`} className="grid grid-cols-[2.25rem_minmax(0,1fr)] gap-1">
            <span className="font-mono">[{i + 1}]</span>
            <span>
              <a
                href={sources[i].url}
                className="break-all text-accent"
                target="_blank"
                rel="noreferrer"
              >
                {sources[i].url}
              </a>
              <span>
                {" "}
                · {t("수집일", "Collected")} {sources[i].collected.join(", ")}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** Source numbers in first-use order. */
function usedSources(indexes: number[]): number[] {
  return [...new Set(indexes)];
}

/**
 * A record title as shown. Titles may themselves contain " — " or 〈…〉, and some end in a dash
 * where the source ran title and venue together, so the page never joins title and venue with a
 * dash (it would double); it uses " · ". Trailing dashes and spaces are dropped here at render
 * time only; the data keep the title as collected.
 */
function displayTitle(title: string): string {
  return title.replace(/[\s–—-]+$/u, "");
}

/** One year of activities: one line per record (title · venue · type · role [n]; type "other" omitted). */
function ActivityYear({ year, sources }: { year: PageYear; sources: PageSource[] }) {
  const { lang, t } = useLang();
  return (
    <div
      id={`y${year.year}`}
      className="grid scroll-mt-24 gap-2 border-t border-border py-5 first:border-t-0 first:pt-0 sm:grid-cols-[2.75rem_minmax(0,1fr)] sm:gap-x-4"
    >
      <h3 className="font-mono text-xs leading-6 text-primary">{year.year}</h3>
      <ul className="space-y-1.5">
        {year.rows.map(([title, venue, type, role, roleEn, src, uncertain], i) => (
          <li key={i} data-record className="max-w-3xl text-sm leading-6">
            <span className="font-medium">{displayTitle(title)}</span>
            {venue && <span className="text-muted-foreground"> · {venue}</span>}
            {/* The type "other" (기타) says nothing on a record line, so it is not shown there
                (author's decision, 2026-10-06); every other type is. */}
            <span className="text-muted-foreground">
              {type !== "other" &&
                ` · ${t(ACTIVITY_TYPE_LABEL[type]?.[0] ?? type, ACTIVITY_TYPE_LABEL[type]?.[1] ?? type)}`}
              {role ? ` · ${lang === "en" ? (roleEn ?? role) : role}` : ""}
            </span>
            {uncertain === 1 && (
              <span
                className="ml-1 text-xs text-muted-foreground"
                title={t(
                  "연도가 제목 속 시기(예: after 1945)와 같아, 기록의 날짜가 아닐 수 있습니다.",
                  "The year equals a period the title names (e.g. after 1945); it may not be the date of this entry.",
                )}
              >
                {t("· 연도 확인 필요", "· year uncertain")}
              </span>
            )}
            <SourceRef index={src} url={sources[src].url} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Years of records shown open; older years sit behind one "show N earlier records" control.
 * The same rule for every person (author's decision, 2026-10-06): the most recent five years
 * that have records stay open, and a person whose records fall in five years or fewer has nothing
 * folded. Folded records stay in the HTML (details/summary), so search engines, citation and
 * find-in-page still reach them; nothing is loaded later.
 */
const OPEN_YEARS = 5;

const BACKGROUND_SECTION_LABEL: Record<string, [string, string]> = {
  education: ["학력", "Education"],
  employment: ["경력", "Employment"],
  teaching: ["강의·교육", "Teaching"],
  press: ["언론", "Press"],
};
const BACKGROUND_PREVIEW = 8;

function BackgroundList({ rows, sources }: { rows: PageBackground[]; sources: PageSource[] }) {
  return (
    <ul className="mt-3 space-y-1.5">
      {rows.map(([title, venue, year, role, src], i) => (
        <li key={i} data-record className="grid max-w-3xl grid-cols-[3.5rem_minmax(0,1fr)] gap-3 text-sm">
          <span className="font-mono text-xs leading-5 text-primary">{year}</span>
          <span>
            {displayTitle(title)}
            {venue && <span className="text-muted-foreground"> · {venue}</span>}
            {role && <span className="text-muted-foreground"> · {role}</span>}
            <SourceRef index={src} url={sources[src].url} />
          </span>
        </li>
      ))}
    </ul>
  );
}

/** A labelled line of linked people: a team's members, or the teams a member belongs to. */
function PeopleLine({ label, people }: { label: string; people: PagePerson[] }) {
  const { lang } = useLang();
  return (
    <p className="mt-1 text-sm text-muted-foreground">
      {label}:{" "}
      {people.map((p, i) => (
        <span key={p.id}>
          {i > 0 && ", "}
          <Link
            to="/artist/$id"
            params={{ id: p.id }}
            className="text-accent underline-offset-4 hover:underline"
          >
            {lang === "en" ? p.name_en || p.name_ko : p.name_ko}
          </Link>
        </span>
      ))}
    </p>
  );
}

/**
 * A small bar with the way home and the request links. It shows only while the reader scrolls,
 * once the header is out of view, and hides a moment after scrolling stops, so at rest it covers
 * neither the header nor a record. The same links sit inline at the end of the page.
 */
function ScrollBar({ id }: { id: string }) {
  const { t } = useLang();
  const [shown, setShown] = useState(false);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onScroll = () => {
      if (window.scrollY < 320) {
        setShown(false);
        return;
      }
      setShown(true);
      clearTimeout(timer);
      timer = setTimeout(() => setShown(false), 1400);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      clearTimeout(timer);
    };
  }, []);
  const item = "px-2 py-1 no-underline transition-colors hover:text-primary";
  return (
    <nav
      aria-label={t("빠른 이동", "Quick links")}
      aria-hidden={!shown}
      className={`fixed bottom-3 right-3 z-40 flex gap-1 border border-border bg-background/95 font-mono text-[11px] shadow-sm backdrop-blur transition-opacity duration-200 ${
        shown ? "opacity-100" : "pointer-events-none opacity-0"
      }`}
    >
      <Link to="/" className={item} tabIndex={shown ? 0 : -1}>
        ← {t("홈", "Home")}
      </Link>
      <Link
        to="/request"
        search={{ type: "correct", artist: id }}
        className={item}
        tabIndex={shown ? 0 : -1}
      >
        {t("수정 요청", "Correction")}
      </Link>
      <Link
        to="/request"
        search={{ type: "hide", artist: id }}
        className={item}
        tabIndex={shown ? 0 : -1}
      >
        {t("비공개 요청", "Hide")}
      </Link>
    </nav>
  );
}

function ArtistPage() {
  const {
    artist,
    citation,
    sources,
    years,
    collaborations,
    links,
    background,
    sameName,
    members,
    memberOf,
  } = Route.useLoaderData();
  const { lang, t } = useLang();
  const bi = (v: Bi) => (lang === "en" ? v.en : v.ko);
  const tags = (vs: Bi[]) => vs.map(bi).join(", ");
  // English page leads with the romanised name when the record has one; Korean stays beside it.
  const primaryName = lang === "en" && artist.name_en ? artist.name_en : artist.name_ko;
  const secondaryName = primaryName === artist.name_ko ? artist.name_en : artist.name_ko;

  const activityCount = years.reduce((n, y) => n + y.rows.length, 0);
  const lastYear = years[0]?.year;
  const firstYear = years[years.length - 1]?.year;
  const recentYears = years.slice(0, OPEN_YEARS);
  const olderYears = years.slice(OPEN_YEARS);
  const olderCount = olderYears.reduce((n, y) => n + y.rows.length, 0);
  const olderRef = useRef<HTMLDetailsElement>(null);
  // A year index link to a folded year opens the fold first, then lets the browser jump.
  const openYear = useCallback((e: MouseEvent<HTMLAnchorElement>) => {
    const id = e.currentTarget.hash.slice(1);
    const fold = olderRef.current;
    if (fold && !fold.open && fold.querySelector(`#${id}`)) fold.open = true;
  }, []);
  useEffect(() => {
    // Arriving with #yYYYY for a folded year: open it and scroll there.
    const id = window.location.hash.slice(1);
    const fold = olderRef.current;
    if (!/^y\d{4}$/.test(id) || !fold) return;
    const target = fold.querySelector<HTMLElement>(`#${id}`);
    if (target) {
      fold.open = true;
      target.scrollIntoView();
    }
  }, []);

  if (artist.status !== "PUBLISHED") {
    return (
      <div className="wrap py-20 lg:py-28">
        <p className="label-caps text-primary">PERMANENT RECORD / {artist.id}</p>
        <div className="mt-8 max-w-3xl border-t border-foreground pt-8">
          {artist.status === "WITHDRAWN" ? (
            <>
              <h1 className="text-4xl font-medium sm:text-6xl">
                더 이상 공개하지 않는 기록입니다.
              </h1>
              <p className="mt-4 text-xl text-muted-foreground">
                This record is no longer published.
              </p>
              <p className="mt-6 max-w-2xl text-sm leading-7 text-muted-foreground">
                이 번호는 한 번 공개된 기록의 영구 번호라 다시 쓰지 않습니다. 근거가 된 명단에서 더
                이상 확인되지 않거나 포함 기준에서 빠져 내렸습니다. / This number was issued to a
                published record and is never reused. The record was taken down because its source
                roster no longer lists it or it fell outside the inclusion criteria.
              </p>
            </>
          ) : (
            <>
              <h1 className="text-4xl font-medium sm:text-6xl">
                요청에 따라 비공개 처리되었습니다.
              </h1>
              <p className="mt-4 text-xl text-muted-foreground">Hidden at the artist's request.</p>
            </>
          )}
        </div>
      </div>
    );
  }

  // Sections are numbered in page order after 01 activities.
  let sectionNo = 1;
  const nextNo = () => String(++sectionNo).padStart(2, "0");
  const backgroundNo = background.length > 0 ? nextNo() : "";
  const collaborationsNo = collaborations.length > 0 ? nextNo() : "";
  const cvFound = artist.cv_status === "found";
  const askNo = !cvFound || sameName.length > 0 ? nextNo() : "";
  const activitySources = usedSources(years.flatMap((y) => y.rows.map((r) => r[5])));
  const backgroundSources = usedSources(background.flatMap((s) => s.rows.map((r) => r[4])));
  const collaborationSources = usedSources(collaborations.map((c) => c.source));
  // One summary line in place of the profile table: active since · type · regions · medium,
  // then technique and theme when present. A derived value says so in its tooltip.
  const summary: { text: string; note?: string }[] = [
    ...(artist.active_since
      ? [
          {
            text: `${t("활동 시작", "Active since")} ${artist.active_since}`,
            note: artist.active_since_derived
              ? t("아래 기록 중 가장 이른 공개 활동", "Earliest public record below")
              : undefined,
          },
        ]
      : []),
    { text: artist.type === "collective" ? t("집단", "Collective") : t("개인", "Individual") },
    ...(artist.regions.length ? [{ text: tags(artist.regions) }] : []),
    ...(artist.medium.length
      ? [
          {
            text: tags(artist.medium),
            note: artist.medium_derived
              ? t("아래 기록 두 건 이상이 이 매체를 말함", "Named by two or more records below")
              : undefined,
          },
        ]
      : []),
    ...(artist.technique.length ? [{ text: tags(artist.technique) }] : []),
    ...(artist.theme.length ? [{ text: tags(artist.theme) }] : []),
  ];
  const section =
    "grid grid-cols-[minmax(0,1fr)] gap-6 border-t border-input py-10 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-16";
  const sectionTitle = "font-mono text-[11px] uppercase text-muted-foreground";

  return (
    <div className="wrap pb-16 pt-16 lg:pt-20">
      <header className="border-b border-input pb-6">
        <p className="font-mono text-[10px] uppercase text-primary">
          [ ARTIST_RECORD / {artist.id} ]
        </p>
        <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_18rem] lg:items-end lg:gap-8">
          <div>
            {/* With leading-none the display face's descenders reach below the line box; pb-3
                keeps whatever line follows the name (members, aliases, links) clear of them. */}
            <h1 className="pb-3 font-mono text-5xl font-bold italic leading-none sm:text-7xl">
              {primaryName}
            </h1>
            {secondaryName && (
              <p
                lang={secondaryName === artist.name_en ? "en" : "ko"}
                className="text-sm font-light text-muted-foreground sm:text-base"
              >
                {secondaryName}
              </p>
            )}
            {/* The person's own links, shown by domain. Display only; nothing is fetched. */}
            {links.length > 0 && (
              <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs">
                {links.map((l) => (
                  <a
                    key={l.url}
                    href={l.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-accent no-underline hover:underline"
                    title={t(
                      LINK_TYPE_LABEL[l.type]?.[0] ?? l.type,
                      LINK_TYPE_LABEL[l.type]?.[1] ?? l.type,
                    )}
                  >
                    {l.host}
                    {l.dead === "dead" && (
                      <span className="text-muted-foreground"> {t("(연결 끊김)", "(dead link)")}</span>
                    )}
                    {l.dead === "check" && (
                      <span className="text-muted-foreground">
                        {" "}
                        {t("(연결 확인 필요)", "(link needs check)")}
                      </span>
                    )}
                  </a>
                ))}
              </p>
            )}
            {artist.aliases.length > 0 && (
              <p className="mt-1 text-sm text-muted-foreground">
                {t("별칭", "Also known as")}: {artist.aliases.join(", ")}
              </p>
            )}
            {/* Team membership from the snapshot (members / member_of), linked both ways. */}
            {members.length > 0 && (
              <PeopleLine label={t("구성원", "Members")} people={members} />
            )}
            {memberOf.length > 0 && <PeopleLine label={t("팀", "Team")} people={memberOf} />}
            {artist.bio_short && <p className="mt-4 max-w-3xl leading-7">{artist.bio_short}</p>}
            <p className="mt-4 text-sm">
              {summary.map((s, i) => (
                <span key={i} title={s.note}>
                  {i > 0 && <span className="text-muted-foreground"> · </span>}
                  {s.text}
                  {s.note && <span className="text-muted-foreground">*</span>}
                </span>
              ))}
            </p>
            {(artist.birth_year || artist.countries.length > 0) && (
              <p className="mt-1 text-sm text-muted-foreground">
                {artist.birth_year && (
                  <span>
                    {t("출생", "Born")} {artist.birth_year}
                    {artist.birth_source != null && (
                      <SourceRef
                        index={artist.birth_source}
                        url={sources[artist.birth_source].url}
                      />
                    )}
                  </span>
                )}
                {artist.birth_year && artist.countries.length > 0 && " · "}
                {artist.countries.length > 0 && (
                  <span>
                    {t("활동 기반", "Based in")} {tags(artist.countries)}
                    {artist.country_url && (
                      <a
                        href={artist.country_url}
                        target="_blank"
                        rel="noreferrer"
                        className="ml-1.5 text-xs text-accent"
                      >
                        {t("작가 CV의 표기", "as the artist's CV states")}
                      </a>
                    )}
                  </span>
                )}
              </p>
            )}
            {summary.some((s) => s.note) && (
              <p className="mt-1 text-xs text-muted-foreground">
                * {t("아래 기록에서 도출한 값", "Derived from the records below")}
              </p>
            )}
          </div>
          <div className="space-y-3 lg:border-l lg:border-border lg:pl-6">
            <p className="flex flex-wrap items-center gap-3 text-sm">
              <Button
                type="button"
                onClick={() => navigator.clipboard.writeText(artist.id)}
                variant="outline"
                size="sm"
                className="rounded-none border-input bg-transparent font-mono text-[11px] shadow-none hover:border-primary hover:bg-transparent hover:text-primary"
                title={t("ID 복사", "Copy ID")}
              >
                {artist.id}
              </Button>
              <span className="font-mono text-[11px] text-muted-foreground">
                {t(
                  VERIFICATION_LABEL[artist.verification][0],
                  VERIFICATION_LABEL[artist.verification][1],
                )}
              </span>
              <span className="font-mono text-[11px] text-muted-foreground">
                {t("최종 수정", "Last updated")} {artist.updated}
              </span>
              <CiteDialog
                title={artist.name_ko}
                id={artist.id}
                author={citation.author}
                version={citation.version}
                released={citation.released_at}
                url={`${site.origin}/artist/${artist.id}`}
                year={citation.year}
              />
            </p>
            {artist.source >= 0 && (
              <p className="text-xs text-muted-foreground">
                {t("기록 출처", "Record source")}
                <SourceRef index={artist.source} url={sources[artist.source].url} />
                {sources[artist.source].collected.length > 0 && (
                  <span>
                    {" · "}
                    {t("수집일", "Collected")} {sources[artist.source].collected.join(", ")}
                  </span>
                )}
              </p>
            )}
          </div>
        </div>
      </header>

      <section className={section}>
        <div className="lg:sticky lg:top-16 lg:self-start">
          <h2 className={sectionTitle}>01 / {t("활동", "Activities")}</h2>
          {years.length > 0 && (
            <>
              <p className="mt-2 text-sm">
                {t(`기록 ${activityCount}건`, `${activityCount} record${activityCount === 1 ? "" : "s"}`)}
                {" · "}
                {firstYear === lastYear ? lastYear : `${firstYear}–${lastYear}`}
              </p>
              {years.length > 1 && (
                <nav
                  aria-label={t("연도 바로가기", "Jump to year")}
                  className="-mx-1 mt-3 flex gap-x-1 gap-y-1 overflow-x-auto pb-1 font-mono text-xs lg:mx-0 lg:grid lg:max-h-[calc(100vh-10rem)] lg:grid-cols-3 lg:overflow-y-auto lg:overflow-x-visible"
                >
                  {years.map((y) => (
                    <a
                      key={y.year}
                      href={`#y${y.year}`}
                      onClick={openYear}
                      className="shrink-0 px-1 py-0.5 text-accent no-underline hover:underline"
                    >
                      {y.year}
                    </a>
                  ))}
                </nav>
              )}
            </>
          )}
        </div>
        <div>
          {years.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {t("기록된 활동이 없습니다.", "No activities recorded.")}
            </p>
          )}
          {recentYears.map((y) => (
            <ActivityYear key={y.year} year={y} sources={sources} />
          ))}
          {olderYears.length > 0 && (
            <details ref={olderRef} className="border-t border-border pt-5">
              <summary className="cursor-pointer font-mono text-xs text-accent">
                {t(
                  `이전 기록 ${olderCount}건 보기 (${olderYears[olderYears.length - 1].year}–${olderYears[0].year})`,
                  `Show ${olderCount} earlier record${olderCount === 1 ? "" : "s"} (${olderYears[olderYears.length - 1].year}–${olderYears[0].year})`,
                )}
              </summary>
              <div className="mt-5">
                {olderYears.map((y) => (
                  <ActivityYear key={y.year} year={y} sources={sources} />
                ))}
              </div>
            </details>
          )}
          <SourceList sources={sources} used={activitySources} />
        </div>
      </section>

      {background.length > 0 && (
        <section className={section}>
          <h2 className={sectionTitle}>
            {backgroundNo} / {t("학력·경력", "Background")}
          </h2>
          <div className="space-y-8">
            {background.map(({ section: key, rows }) => {
              const [ko, en] = BACKGROUND_SECTION_LABEL[key] ?? [key, key];
              return (
                <div key={key}>
                  <h3 className="label-caps">{t(ko, en)}</h3>
                  <BackgroundList rows={rows.slice(0, BACKGROUND_PREVIEW)} sources={sources} />
                  {rows.length > BACKGROUND_PREVIEW && (
                    <details className="mt-2">
                      <summary className="cursor-pointer font-mono text-[11px] text-muted-foreground transition-colors hover:text-primary">
                        {t(
                          `${rows.length - BACKGROUND_PREVIEW}건 더 보기`,
                          `Show ${rows.length - BACKGROUND_PREVIEW} more`,
                        )}
                      </summary>
                      <BackgroundList rows={rows.slice(BACKGROUND_PREVIEW)} sources={sources} />
                    </details>
                  )}
                </div>
              );
            })}
            <SourceList sources={sources} used={backgroundSources} />
          </div>
        </section>
      )}

      {collaborations.length > 0 && (
        <section className={section}>
          <h2 className={sectionTitle}>
            {collaborationsNo} / {t("협업 과학자·공학자", "Science & engineering collaborators")}
          </h2>
          <div>
            <ul className="space-y-1.5">
              {collaborations.map((c, i) => (
                <li key={i} data-record className="max-w-3xl text-sm leading-6">
                  {c.year && <span className="mr-3 font-mono text-xs text-primary">{c.year}</span>}
                  <span className="font-medium">{bi(c.name)}</span>
                  {c.where && <span className="text-muted-foreground"> — {c.where}</span>}
                  {c.topic && <span className="text-muted-foreground"> · {c.topic}</span>}
                  <SourceRef index={c.source} url={sources[c.source].url} />
                </li>
              ))}
            </ul>
            <SourceList sources={sources} used={collaborationSources} />
          </div>
        </section>
      )}

      {askNo && (
        <section className={section}>
          <h2 className={sectionTitle}>
            {askNo} / {t("아직 확인하지 못한 것", "Still open")}
          </h2>
          <div className="max-w-3xl space-y-8 text-sm leading-7">
            {!cvFound && (
              <div>
                <p>
                  {artist.cv_status === "pending"
                    ? t(
                        "이 작가의 CV 위치는 알고 있지만 아직 읽어 오지 못했습니다. 위의 이력은 공개 명단에서 가져온 것입니다.",
                        "We know where this artist's CV is but have not been able to read it yet. The records above come from public rosters.",
                      )
                    : t(
                        "이 작가가 직접 공개한 CV를 아직 찾지 못했습니다. 위의 이력은 공개 명단에서 가져온 것입니다.",
                        "We have not yet found a CV this artist publishes. The records above come from public rosters.",
                      )}
                </p>
                <p className="mt-2 text-muted-foreground">
                  {t(
                    "본인이시라면 CV나 웹사이트 주소를 알려 주세요. 알려 주신 주소에서 CV를 읽어 이력을 채웁니다.",
                    "If this is you, tell us where your CV or website is. We read the CV there and fill in the record.",
                  )}
                </p>
                <Link
                  to="/request"
                  search={{ type: "self", artist: artist.id }}
                  className="mt-3 inline-block border-b border-input no-underline hover:border-primary hover:text-primary"
                >
                  {t("본인입니다 — CV·웹사이트 알려 주기", "This is me — share my CV or website")}
                </Link>
              </div>
            )}
            {sameName.length > 0 && (
              <div>
                <p>
                  {t(
                    "같은 사람일 수 있는 기록이 따로 있습니다(같은 이름이거나 한글·영문 표기가 맞는 기록). 한 사람인지 가를 근거를 아직 찾지 못해 나누어 두었습니다.",
                    "There are separate records that may be the same person (the same name, or a Korean name and an English spelling that match). We have not found evidence that tells whether they are one person, so they are kept apart.",
                  )}
                </p>
                <ul className="mt-3 space-y-3">
                  {sameName.map((o) => (
                    <li key={o.id}>
                      <Link to="/artist/$id" params={{ id: o.id }} className="text-accent">
                        {lang === "en" ? o.name_en || o.name_ko : o.name_ko}{" "}
                        <span className="font-mono text-xs">{o.id}</span>
                      </Link>
                      {o.editions.length > 0 && (
                        <span className="text-muted-foreground">
                          {" "}
                          — {o.editions.map(bi).join(", ")}
                        </span>
                      )}
                      <Link
                        to="/request"
                        search={{ type: "same", artist: artist.id, other: o.id }}
                        className="ml-3 border-b border-input text-xs no-underline hover:border-primary hover:text-primary"
                      >
                        {t("같은 사람입니다", "Same person")}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Why this person is in the register: the roster editions that list them. One line,
          opening to the editions; the reason, not an introduction, so it sits at the end. */}
      <section className="border-t border-input py-8 text-sm">
        {artist.editions.length === 0 ? (
          <p>
            <Link to="/about/frame" className="text-accent">
              {t("수록 근거: 표집틀 외", "Out of frame")} →
            </Link>
          </p>
        ) : (
          <details>
            <summary className="cursor-pointer">
              <Link to="/about/frame" className="text-accent">
                {t(
                  `수록 근거: 프로그램 ${artist.editions.length}회차`,
                  `Included through ${artist.editions.length} programme edition${artist.editions.length === 1 ? "" : "s"}`,
                )}{" "}
                →
              </Link>
            </summary>
            <ul className="mt-3 space-y-1 pl-4">
              {artist.editions.map((fe, i) => {
                const label = lang === "en" ? fe.label.en : fe.label.ko;
                return (
                  <li key={`${fe.frame}-${i}`}>
                    <Link
                      to="/artists"
                      search={{ group: "frame", fr: fe.frame }}
                      className="underline-offset-4 hover:text-primary hover:underline"
                    >
                      {bi(fe.name)}
                    </Link>
                    {label ? (
                      <span className="ml-2 text-xs text-muted-foreground">{label}</span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </details>
        )}
      </section>

      <p className="flex flex-wrap gap-x-5 gap-y-2 border-t border-input pt-6 text-sm">
        <Link
          to="/request"
          search={{ type: "correct", artist: artist.id }}
          className="border-b border-input no-underline transition-colors hover:border-primary hover:text-primary"
        >
          {t("수정 요청", "Request a correction")}
        </Link>
        <Link
          to="/request"
          search={{ type: "hide", artist: artist.id }}
          className="border-b border-input no-underline transition-colors hover:border-primary hover:text-primary"
        >
          {t("비공개 요청", "Request hiding")}
        </Link>
      </p>

      <ScrollBar id={artist.id} />
    </div>
  );
}
