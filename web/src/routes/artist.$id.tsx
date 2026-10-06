// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute, Link, notFound, redirect } from "@tanstack/react-router";
import { getArtistRecord, getArtistRedirect } from "@/lib/giye.functions";
import {
  ACTIVITY_TYPE_LABEL,
  LINK_TYPE_LABEL,
  SOURCE_TYPE_LABEL,
  useLang,
  VERIFICATION_LABEL,
} from "@/lib/i18n";
import { CiteDialog } from "@/components/CiteDialog";
import { absoluteUrl, pageTitle, site } from "@/config/site";
import { Button } from "@/components/ui/button";
import type { Activity, BackgroundEntry } from "@/lib/giye.types";

/** Country name in the reader's language from an ISO 3166 code (the browser's own list). */
function countryName(cc: string, lang: string): string {
  try {
    return new Intl.DisplayNames([lang === "en" ? "en" : "ko"], { type: "region" }).of(cc) ?? cc;
  } catch {
    return cc;
  }
}

export const Route = createFileRoute("/artist/$id")({
  loader: async ({ params }) => {
    const record = await getArtistRecord({ data: { id: params.id } });
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
                sameAs: a.external_ids?.wikidata
                  ? [`https://www.wikidata.org/wiki/${a.external_ids.wikidata}`]
                  : undefined,
              }),
            },
          ],
    };
  },
  component: ArtistPage,
});

function SourceDisclosure({
  url,
  type,
  collected,
}: {
  url: string;
  type: string;
  collected: string;
}) {
  const { t } = useLang();
  return (
    <details className="mt-2 text-xs text-muted-foreground">
      <summary className="cursor-pointer font-mono text-[10px] transition-colors hover:text-primary">
        {t("출처 보기", "View source")}
      </summary>
      <div className="mt-2 space-y-1 border-l border-primary pl-3">
        <p>
          <a href={url} className="break-all text-accent" target="_blank" rel="noreferrer">
            {url}
          </a>
        </p>
        <p>
          {t(SOURCE_TYPE_LABEL[type]?.[0] ?? type, SOURCE_TYPE_LABEL[type]?.[1] ?? type)} ·{" "}
          {t("수집일", "Collected")} {collected}
        </p>
      </div>
    </details>
  );
}

const BACKGROUND_SECTIONS: [BackgroundEntry["section"], string, string][] = [
  ["education", "학력", "Education"],
  ["employment", "경력", "Employment"],
  ["teaching", "강의·교육", "Teaching"],
  ["press", "언론", "Press"],
];
const BACKGROUND_PREVIEW = 8;

function BackgroundList({ rows }: { rows: BackgroundEntry[] }) {
  return (
    <ul className="mt-3 space-y-2">
      {rows.map((b) => (
        <li key={b.id} className="grid max-w-3xl grid-cols-[3.5rem_minmax(0,1fr)] gap-3 text-sm">
          <span className="font-mono text-xs text-primary">{b.year}</span>
          <span>
            {b.title}
            {b.venue && <span className="text-muted-foreground"> — {b.venue}</span>}
            {b.role && <span className="text-muted-foreground"> · {b.role}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Background lines come from the artist's CV pages; list each distinct page once. */
function BackgroundSources({ rows }: { rows: BackgroundEntry[] }) {
  const { t } = useLang();
  const sources = [...new Map(rows.map((b) => [b.source_url, b])).values()];
  return (
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer font-mono text-[10px] transition-colors hover:text-primary">
        {t("출처 보기", "View source")}
      </summary>
      <div className="mt-2 space-y-1 border-l border-primary pl-3">
        {sources.map((b) => (
          <p key={b.source_url}>
            <a
              href={b.source_url}
              className="break-all text-accent"
              target="_blank"
              rel="noreferrer"
            >
              {b.source_url}
            </a>{" "}
            ·{" "}
            {t(
              SOURCE_TYPE_LABEL[b.source_type]?.[0] ?? b.source_type,
              SOURCE_TYPE_LABEL[b.source_type]?.[1] ?? b.source_type,
            )}{" "}
            · {t("수집일", "Collected")} {b.collected_at}
          </p>
        ))}
      </div>
    </details>
  );
}

/**
 * L2. On the English page an edition subtitle shows only its Latin-script part: the segments between
 * ":" and parentheses that contain Latin letters and no Hangul ("Blue Signal: 기술이 …" →
 * "Blue Signal"). A subtitle with no such part is left out. The Korean page shows it whole.
 */
// The "(예정)" marker on a role is added by scripts/apply_cv_extractions.py for a CV entry
// still labelled upcoming in the current year; the English page shows it as "(upcoming)".

function latinPart(label: string): string | null {
  if (!/[\uac00-\ud7a3]/.test(label)) return label;
  const parts = label
    .split(/[:()（）]/)
    .map((p) => p.trim())
    .filter((p) => p && /[A-Za-z]/.test(p) && !/[\uac00-\ud7a3]/.test(p));
  return parts.length ? parts.join(": ") : null;
}

function ArtistPage() {
  const { artist, citation, terms_en, activities, links, collaborations, background, frames, sameName } =
    Route.useLoaderData();
  const { lang, t } = useLang();
  // English page: tags read through vocabularies.json (L1); a tag with no English term stays as written.
  const tag = (v: string) => (lang === "en" ? (terms_en[v] ?? v) : v);
  const tags = (vs: string[]) => vs.map(tag).join(", ");
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

  const byYear = new Map<number, Activity[]>();
  for (const act of activities) {
    byYear.set(act.year, [...(byYear.get(act.year) ?? []), act]);
  }
  const years = [...byYear.keys()].sort((a, b) => b - a);

  // Optional sections are numbered in page order after 01 profile and 02 activities.
  let sectionNo = 2;
  const nextNo = () => String(++sectionNo).padStart(2, "0");
  const backgroundNo = background.length > 0 ? nextNo() : "";
  const collaborationsNo = collaborations.length > 0 ? nextNo() : "";
  const linksNo = links.length > 0 ? nextNo() : "";
  const cvFound = artist.cv_status === "found";
  const askNo = !cvFound || sameName.length > 0 ? nextNo() : "";

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
                {t("최종 수정", "Last updated")} {artist.updated_at.slice(0, 10)}
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
            <SourceDisclosure
              url={artist.source_url}
              type={artist.source_type}
              collected={artist.collected_at}
            />
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
                  {artist.birth_year_source_url && (
                    <SourceDisclosure
                      url={artist.birth_year_source_url}
                      type={artist.source_type}
                      collected={artist.collected_at}
                    />
                  )}
                </dd>
              </div>
            )}
            {artist.active_since && (
              <div>
                <dt className="label-caps">{t("활동 시작", "Active since")}</dt>
                <dd>
                  {artist.active_since}
                  {artist.derived?.active_since && (
                    <span className="ml-2 text-xs text-muted-foreground">
                      {t("아래 기록 중 가장 이른 공개 활동", "earliest public record below")}
                    </span>
                  )}
                </dd>
              </div>
            )}
            {(artist.countries ?? []).length > 0 && (
              <div>
                <dt className="label-caps">{t("활동 기반", "Based in")}</dt>
                <dd>
                  {(artist.countries ?? []).map((cc) => countryName(cc, lang)).join(", ")}
                  {artist.derived?.country?.url && (
                    <a
                      href={artist.derived.country.url}
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
            {artist.medium_tags.length > 0 && (
              <div>
                <dt className="label-caps">{t("매체", "Medium")}</dt>
                <dd>
                  {tags(artist.medium_tags)}
                  {artist.derived?.medium && (
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
            {artist.technique_tags.length > 0 && (
              <div>
                <dt className="label-caps">{t("기법", "Technique")}</dt>
                <dd>{tags(artist.technique_tags)}</dd>
              </div>
            )}
            {artist.theme_tags.length > 0 && (
              <div>
                <dt className="label-caps">{t("주제", "Theme")}</dt>
                <dd>{tags(artist.theme_tags)}</dd>
              </div>
            )}
            <div>
              <dt className="label-caps">{t("표집틀", "Sampling frame")}</dt>
              <dd>
                {(artist.frame_editions ?? []).length === 0 ? (
                  t("표집틀 외", "Out of frame")
                ) : (
                  // One line per event edition: an artist can sit in several frames
                  // Two editions of different programmes; each links to that event's shelf.
                  <ul className="space-y-1">
                    {[...(artist.frame_editions ?? [])]
                      .sort((x, y) => {
                        const ox = frames.find((f) => f.code === x.frame)?.order ?? 999;
                        const oy = frames.find((f) => f.code === y.frame)?.order ?? 999;
                        return ox - oy || (y.edition ?? "").localeCompare(x.edition ?? "");
                      })
                      .map((fe) => {
                        const f = frames.find((x) => x.code === fe.frame);
                        const name = f
                          ? lang === "ko"
                            ? f.name_ko
                            : (f.name_en ?? f.name_ko)
                          : fe.frame;
                        const raw = fe.edition ? f?.labels[fe.edition] : null;
                        const label = raw ? (lang === "en" ? latinPart(raw) : raw) : null;
                        return (
                          <li key={`${fe.frame}-${fe.edition ?? ""}`}>
                            <Link
                              to="/artists"
                              search={{ group: "frame", fr: fe.frame }}
                              className="underline-offset-4 hover:text-primary hover:underline"
                            >
                              {fe.edition && !name.includes(fe.edition)
                                ? `${name} ${fe.edition}`
                                : name}
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
            <div
              key={y}
              className="grid gap-3 border-t border-border py-7 first:border-t-0 first:pt-0 sm:grid-cols-[5rem_1fr]"
            >
              <h3 className="font-mono text-xs text-primary">{y}</h3>
              <ul className="space-y-7">
                {(byYear.get(y) ?? []).map((act) => (
                  <li key={act.id} className="max-w-3xl">
                    <p className="font-medium">
                      {act.title}
                      {act.venue && <span className="text-muted-foreground"> — {act.venue}</span>}
                      {act.flags?.includes("year_from_title") && (
                        <span
                          className="ml-2 text-xs text-muted-foreground"
                          title={t(
                            "연도가 제목 속 시기(예: after 1945)와 같아, 기록의 날짜가 아닐 수 있습니다.",
                            "The year equals a period the title names (e.g. after 1945); it may not be the date of this entry.",
                          )}
                        >
                          {t("· 연도 확인 필요", "· year uncertain")}
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {t(
                        ACTIVITY_TYPE_LABEL[act.activity_type]?.[0] ?? act.activity_type,
                        ACTIVITY_TYPE_LABEL[act.activity_type]?.[1] ?? act.activity_type,
                      )}
                      {act.role
                        ? ` · ${lang === "en" ? act.role.replace("(예정)", "(upcoming)") : act.role}`
                        : ""}
                    </p>
                    <SourceDisclosure
                      url={act.source_url}
                      type={act.source_type}
                      collected={act.collected_at}
                    />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      {background.length > 0 && (
        <section className="grid gap-8 border-t border-input py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
          <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
            {backgroundNo} / {t("학력·경력", "Background")}
          </h2>
          <div className="space-y-10">
            {BACKGROUND_SECTIONS.map(([key, ko, en]) => {
              const rows = background.filter((b) => b.section === key);
              if (rows.length === 0) return null;
              return (
                <div key={key}>
                  <h3 className="label-caps">{t(ko, en)}</h3>
                  <BackgroundList rows={rows.slice(0, BACKGROUND_PREVIEW)} />
                  {rows.length > BACKGROUND_PREVIEW && (
                    <details className="mt-2">
                      <summary className="cursor-pointer font-mono text-[10px] text-muted-foreground transition-colors hover:text-primary">
                        {t(
                          `${rows.length - BACKGROUND_PREVIEW}건 더 보기`,
                          `Show ${rows.length - BACKGROUND_PREVIEW} more`,
                        )}
                      </summary>
                      <BackgroundList rows={rows.slice(BACKGROUND_PREVIEW)} />
                    </details>
                  )}
                </div>
              );
            })}
            <BackgroundSources rows={background} />
          </div>
        </section>
      )}

      {collaborations.length > 0 && (
        <section className="grid gap-8 border-t border-input py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
          <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
            {collaborationsNo} / {t("협업 과학자·공학자", "Science & engineering collaborators")}
          </h2>
          <ul className="space-y-5">
            {collaborations.map((c) => (
              <li key={c.id} className="max-w-3xl">
                <p className="font-medium">
                  {lang === "en" ? c.name_en || c.name_ko : c.name_ko || c.name_en}
                  {(c.affiliation || c.lab) && (
                    <span className="text-muted-foreground">
                      {" "}
                      — {[c.affiliation, c.lab].filter(Boolean).join(" · ")}
                    </span>
                  )}
                </p>
                <p className="text-xs text-muted-foreground">
                  {[c.year, c.topic].filter(Boolean).join(" · ")}
                </p>
                <SourceDisclosure
                  url={c.source_url}
                  type="PUBLIC_RECORD"
                  collected={c.collected_at}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      {links.length > 0 && (
        <section className="grid gap-8 border-t border-input py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
          <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
            {linksNo} / {t("링크", "Links")}
          </h2>
          <ul className="space-y-3">
            {links.map((l) => {
              const deadNote = !l.is_dead
                ? ""
                : l.http_status === "404" || l.http_status === "410"
                  ? t(" · 연결 끊김", " · dead link")
                  : t(" · 연결 확인 필요", " · link needs check");
              return (
                <li key={l.id} className="text-sm">
                  <a href={l.url} className="text-accent" target="_blank" rel="noreferrer">
                    {l.label}
                  </a>{" "}
                  <span className="text-muted-foreground">
                    (
                    {t(
                      LINK_TYPE_LABEL[l.link_type]?.[0] ?? l.link_type,
                      LINK_TYPE_LABEL[l.link_type]?.[1] ?? l.link_type,
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
                          — {o.editions.map((e) => (lang === "en" ? e.en : e.ko)).join(", ")}
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
