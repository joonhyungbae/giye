// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute, Link, notFound, redirect } from "@tanstack/react-router";
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

/** One year of activities: one line per record (title — venue · type · role [n]). */
function ActivityYear({ year, sources }: { year: PageYear; sources: PageSource[] }) {
  const { lang, t } = useLang();
  return (
    <div
      id={`y${year.year}`}
      className="grid scroll-mt-24 gap-2 border-t border-border py-5 first:border-t-0 first:pt-0 sm:grid-cols-[5rem_minmax(0,1fr)]"
    >
      <h3 className="font-mono text-xs leading-6 text-primary">{year.year}</h3>
      <ul className="space-y-1.5">
        {year.rows.map(([title, venue, type, role, roleEn, src, uncertain], i) => (
          <li key={i} data-record className="max-w-3xl text-sm leading-6">
            <span className="font-medium">{title}</span>
            {venue && <span className="text-muted-foreground"> — {venue}</span>}
            <span className="text-muted-foreground">
              {" · "}
              {t(ACTIVITY_TYPE_LABEL[type]?.[0] ?? type, ACTIVITY_TYPE_LABEL[type]?.[1] ?? type)}
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
            {title}
            {venue && <span className="text-muted-foreground"> — {venue}</span>}
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

  // Optional sections are numbered in page order after 01 profile and 02 activities.
  let sectionNo = 2;
  const nextNo = () => String(++sectionNo).padStart(2, "0");
  const backgroundNo = background.length > 0 ? nextNo() : "";
  const collaborationsNo = collaborations.length > 0 ? nextNo() : "";
  const linksNo = links.length > 0 ? nextNo() : "";
  const cvFound = artist.cv_status === "found";
  const askNo = !cvFound || sameName.length > 0 ? nextNo() : "";
  // The person row and birth year cite their pages in the header and profile; each later section
  // lists the pages its own lines cite.
  const profileSources = usedSources(
    [artist.source, artist.birth_source ?? -1].filter((i) => i >= 0),
  );
  const activitySources = usedSources(years.flatMap((y) => y.rows.map((r) => r[5])));
  const backgroundSources = usedSources(background.flatMap((s) => s.rows.map((r) => r[4])));
  const collaborationSources = usedSources(collaborations.map((c) => c.source));

  return (
    <div className="wrap py-12 pb-32 lg:py-20">
      <header className="border-b border-input pb-10">
        <p className="font-mono text-[10px] uppercase text-primary">
          [ ARTIST_RECORD / {artist.id} ]
        </p>
        <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1fr)_18rem] lg:items-end">
          <div>
            <h1 className="font-mono text-5xl font-bold italic leading-none sm:text-7xl">
              {primaryName}
            </h1>
            {secondaryName && (
              <p
                lang={secondaryName === artist.name_en ? "en" : "ko"}
                className="mt-3 text-sm font-light uppercase text-muted-foreground sm:text-base"
              >
                {secondaryName}
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
          </div>
          <div className="space-y-4 lg:border-l lg:border-border lg:pl-6">
            <p className="flex flex-wrap items-center gap-3 text-sm">
              <Button
                type="button"
                onClick={() => navigator.clipboard.writeText(artist.id)}
                variant="outline"
                size="sm"
                className="rounded-none border-input bg-transparent font-mono text-[10px] shadow-none hover:border-primary hover:bg-transparent hover:text-primary"
                title={t("ID 복사", "Copy ID")}
              >
                {artist.id}
              </Button>
              <span className="font-mono text-[10px] text-muted-foreground">
                {t(
                  VERIFICATION_LABEL[artist.verification][0],
                  VERIFICATION_LABEL[artist.verification][1],
                )}
              </span>
              <span className="font-mono text-[10px] text-muted-foreground">
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
                <SourceRef
                  index={artist.source}
                  url={sources[artist.source].url} />
              </p>
            )}
          </div>
        </div>
      </header>

      <div className="grid gap-12 py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
        <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
          01 / {t("작가 개요", "Artist profile")}
        </h2>
        <div>
          {artist.bio_short && <p className="max-w-3xl text-lg leading-8">{artist.bio_short}</p>}

          <dl className="mt-10 grid gap-x-12 gap-y-7 border-t border-border pt-7 sm:grid-cols-2">
            {artist.birth_year && (
              <div>
                <dt className="label-caps">{t("출생", "Born")}</dt>
                <dd>
                  {artist.birth_year}
                  {artist.birth_source != null && (
                    <SourceRef
                      index={artist.birth_source}
                      url={sources[artist.birth_source].url} />
                  )}
                </dd>
              </div>
            )}
            {artist.active_since && (
              <div>
                <dt className="label-caps">{t("활동 시작", "Active since")}</dt>
                <dd>
                  {artist.active_since}
                  {artist.active_since_derived && (
                    <span className="ml-2 text-xs text-muted-foreground">
                      {t("아래 기록 중 가장 이른 공개 활동", "earliest public record below")}
                    </span>
                  )}
                </dd>
              </div>
            )}
            {artist.countries.length > 0 && (
              <div>
                <dt className="label-caps">{t("활동 기반", "Based in")}</dt>
                <dd>
                  {tags(artist.countries)}
                  {artist.country_url && (
                    <a
                      href={artist.country_url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-2 text-xs text-muted-foreground"
                    >
                      {t("작가 CV의 표기", "as the artist's CV states")}
                    </a>
                  )}
                </dd>
              </div>
            )}
            <div>
              <dt className="label-caps">{t("유형", "Type")}</dt>
              <dd>
                {artist.type === "collective" ? t("집단", "Collective") : t("개인", "Individual")}
              </dd>
            </div>
            {artist.regions.length > 0 && (
              <div>
                <dt className="label-caps">{t("지역", "Regions")}</dt>
                <dd>{tags(artist.regions)}</dd>
              </div>
            )}
            {artist.medium.length > 0 && (
              <div>
                <dt className="label-caps">{t("매체", "Medium")}</dt>
                <dd>
                  {tags(artist.medium)}
                  {artist.medium_derived && (
                    <span className="ml-2 text-xs text-muted-foreground">
                      {t(
                        "아래 기록 두 건 이상이 이 매체를 말함",
                        "named by two or more records below",
                      )}
                    </span>
                  )}
                </dd>
              </div>
            )}
            {artist.technique.length > 0 && (
              <div>
                <dt className="label-caps">{t("기법", "Technique")}</dt>
                <dd>{tags(artist.technique)}</dd>
              </div>
            )}
            {artist.theme.length > 0 && (
              <div>
                <dt className="label-caps">{t("주제", "Theme")}</dt>
                <dd>{tags(artist.theme)}</dd>
              </div>
            )}
            <div>
              <dt className="label-caps">{t("표집틀", "Sampling frame")}</dt>
              <dd>
                {artist.editions.length === 0 ? (
                  t("표집틀 외", "Out of frame")
                ) : (
                  // One line per event edition: an artist can sit in several frames.
                  // Each links to that event's shelf.
                  <ul className="space-y-1">
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
                )}
              </dd>
            </div>
          </dl>
          <SourceList sources={sources} used={profileSources} />
        </div>
      </div>

      <section className="grid gap-8 border-t border-input py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
        <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
          02 / {t("활동", "Activities")}
        </h2>
        <div>
          {years.length === 0 && (
            <p className="mt-3 text-sm text-muted-foreground">
              {t("기록된 활동이 없습니다.", "No activities recorded.")}
            </p>
          )}
          {years.map((y) => (
            <ActivityYear key={y.year} year={y} sources={sources} />
          ))}
          <SourceList sources={sources} used={activitySources} />
        </div>
      </section>

      {background.length > 0 && (
        <section className="grid gap-8 border-t border-input py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
          <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
            {backgroundNo} / {t("학력·경력", "Background")}
          </h2>
          <div className="space-y-10">
            {background.map(({ section, rows }) => {
              const [ko, en] = BACKGROUND_SECTION_LABEL[section] ?? [section, section];
              return (
                <div key={section}>
                  <h3 className="label-caps">{t(ko, en)}</h3>
                  <BackgroundList rows={rows.slice(0, BACKGROUND_PREVIEW)} sources={sources} />
                  {rows.length > BACKGROUND_PREVIEW && (
                    <details className="mt-2">
                      <summary className="cursor-pointer font-mono text-[10px] text-muted-foreground transition-colors hover:text-primary">
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
        <section className="grid gap-8 border-t border-input py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
          <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
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

      {links.length > 0 && (
        <section className="grid gap-8 border-t border-input py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
          <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
            {linksNo} / {t("링크", "Links")}
          </h2>
          <ul className="space-y-3">
            {links.map((l, i) => {
              const deadNote =
                l.dead === "dead"
                  ? t(" · 연결 끊김", " · dead link")
                  : l.dead === "check"
                    ? t(" · 연결 확인 필요", " · link needs check")
                    : "";
              return (
                <li key={i} className="text-sm">
                  <a href={l.url} className="text-accent" target="_blank" rel="noreferrer">
                    {l.label}
                  </a>{" "}
                  <span className="text-muted-foreground">
                    (
                    {t(
                      LINK_TYPE_LABEL[l.type]?.[0] ?? l.type,
                      LINK_TYPE_LABEL[l.type]?.[1] ?? l.type,
                    )}
                    {deadNote})
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {askNo && (
        <section className="grid gap-8 border-t border-input py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
          <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
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

      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-background/95 backdrop-blur">
        <div className="wrap flex flex-wrap justify-end gap-2 py-3 text-sm">
          <Link
            to="/request"
            search={{ type: "correct", artist: artist.id }}
            className="border-b border-input px-2 py-1.5 no-underline transition-colors hover:border-primary hover:text-primary"
          >
            {t("수정 요청", "Request a correction")}
          </Link>
          <Link
            to="/request"
            search={{ type: "hide", artist: artist.id }}
            className="border-b border-input px-2 py-1.5 no-underline transition-colors hover:border-primary hover:text-primary"
          >
            {t("비공개 요청", "Request hiding")}
          </Link>
        </div>
      </div>
    </div>
  );
}
