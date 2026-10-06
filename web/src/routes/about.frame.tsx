// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute, Link } from "@tanstack/react-router";
import type { FrameEntry } from "@/lib/giye.data";
import { getCoverage, getFrameEntries } from "@/lib/giye.functions";
import {
  admittedProgrammesEn,
  admittedProgrammesKo,
  countFrameDecisions,
} from "@/lib/frame-population";
import { useLang } from "@/lib/i18n";
import { AboutNav } from "@/components/SiteChrome";
import { absoluteUrl, pageTitle } from "@/config/site";

/**
 * C1. A programme is running when its declared years are open-ended ("2015-"): it may still
 * publish new editions, which are collected when published. Closed ranges ("2010-2021") and lists
 * of single years are ended.
 */
function isRunning(years: string | null | undefined): boolean {
  return /-\s*$/.test(years ?? "");
}

export const Route = createFileRoute("/about/frame")({
  loader: async () => ({
    entries: await getFrameEntries(),
    coverage: await getCoverage(),
  }),
  head: () => ({
    meta: [
      { title: pageTitle("표집틀 Sampling frame") },
      {
        name: "description",
        content:
          "기예가 기록 대상을 정하는 프로그램 목록과 선정 기준, 프로그램별 수록률. The programmes whose rosters define who Giye records, the conditions for admitting them, and each programme's coverage.",
      },
      { property: "og:title", content: "표집틀 Sampling frame — 기예 Giye" },
      {
        property: "og:description",
        content: "기예의 표집틀 · The sampling frame behind the Giye archive.",
      },
      { property: "og:url", content: absoluteUrl("/about/frame") },
    ],
    links: [{ rel: "canonical", href: absoluteUrl("/about/frame") }],
  }),
  component: FramePage,
});

const VERDICT_KO: Record<string, string> = {
  included: "채택",
  planned: "예정",
  excluded: "탈락",
  no_public_roster: "명단 비공개",
  adjacent: "인접 (F1 미충족 · 공개 유지)",
};
const VERDICT_EN: Record<string, string> = {
  included: "admitted",
  planned: "planned",
  excluded: "rejected",
  no_public_roster: "no public roster",
  adjacent: "adjacent (F1 not met · kept public)",
};

/** F1–F5 verdict as one hover string, so the reason travels with the row. */
function eligibilityDetail(e: NonNullable<FrameEntry["eligibility"]>): string {
  return (
    [
      ["F1", e.f1_purpose],
      ["F2", e.f2_cohort],
      ["F3", e.f3_korea],
      ["F4", e.f4_roster],
      ["F5", e.f5_period],
    ] as [string, string | undefined][]
  )
    .filter(([, v]) => v)
    .map(([k, v]) => `${k} ${v}`)
    .join("\n");
}

function FramePage() {
  const { entries, coverage } = Route.useLoaderData();
  const { lang, t } = useLang();
  const { admitted, adjacent } = countFrameDecisions(entries);
  const phraseKo = admittedProgrammesKo(admitted, adjacent);
  const phraseEn = admittedProgrammesEn(admitted, adjacent);
  const refreshed = coverage?.generated_at
    ? coverage.generated_at.slice(0, 10)
    : (entries
        .map((e) => e.last_fetched_at)
        .filter(Boolean)
        .sort()
        .at(-1) ?? "—");

  return (
    <>
      <AboutNav />
      <div className="wrap max-w-3xl py-12">
        <h1 className="text-3xl">{t("표집틀", "Sampling frame")}</h1>
        {/* Both languages, whatever the page language: this is the statement of what Giye records. */}
        <p className="mt-4 leading-8">
          {`기예는 ${phraseKo}의 명단에 오른 모든 사람을 출처와 함께 기록하고, 프로그램마다 수록률을 공개합니다. 명단 밖의 작가는 포함 기준을 충족하면 추가할 수 있으며, 지금까지 추가된 사람은 없습니다. 마지막 데이터 갱신: ${refreshed}.`}
        </p>
        <p className="mt-2 leading-8">
          {`Giye records everyone on the rosters of ${phraseEn}, with the source of every fact, and publishes each programme's coverage. Artists outside these rosters can be added when they meet the inclusion criteria; none has been added so far. Last refresh: ${refreshed}.`}
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          {t(
            "매주 인용 링크를 점검하고 바뀐 CV와 요청을 반영합니다. 새 회차의 명단은 회차가 공개되면 한 번 수집하고, 끝난 회차는 다시 수집하지 않습니다.",
            "Every week, cited links are checked and changed CVs and requests are taken in. A new edition's roster is collected once, when the edition is published; ended editions are not collected again.",
          )}
        </p>
        <section className="mt-10">
          <h2 className="text-xl">
            {t("틀 자격 기준", "Frame eligibility criteria")}
          </h2>
          <p className="mt-3 text-sm leading-7 text-muted-foreground">
            {t(
              "어떤 사업을 틀로 삼을지는 후보를 보기 전에 정한 다섯 기준으로 판정하고, 채택·탈락 사유를 아래 표에 함께 공개합니다 (2026-09-20 제정).",
              "Whether a programme becomes a frame is decided by five criteria fixed before any candidate was examined; each verdict and its reason is published in the table below (adopted 2026-09-20).",
            )}
          </p>
          <dl className="mt-4 space-y-2 text-sm leading-7">
            {(
              [
                [
                  "F1",
                  // The words 미디어아트 here are one purpose term in the criterion, next to
                  // new media and digital art. The instance field name is src/config/site.ts.
                  "목적: 공개 소개·공고문에 예술과 기술(또는 과학)의 융합, 미디어아트, 뉴미디어, 디지털 아트가 목적으로 명시된다. 기관 전체의 성격이 아니라 그 프로그램의 문서가 근거다.",
                  "Purpose: the programme's own public description states art–technology (or art–science) convergence, media art, new media or digital art as its purpose — the programme's document, not the institution's reputation.",
                ],
                [
                  "F2",
                  "선정 집단: 공모·심사·선발·수상·입주처럼 참여자 집단이 정해지는 절차가 있다. 기획전·대관은 해당하지 않는다.",
                  "Cohort: participants are fixed by an open call, jury, selection, award or residency. Curated or rented exhibitions do not qualify.",
                ],
                ["F3", "한국에서 열린다.", "Held in Korea."],
                [
                  "F4",
                  "참여자 명단이 공개 기록(공식 페이지·도록·보도자료)으로 확인된다.",
                  "The participant list is verifiable in a public record (official page, catalogue, press release).",
                ],
                [
                  "F5",
                  "2010년 이후 회차가 있고 2회 이상 반복된다. 단일 회차는 그 해 분야를 대표할 때만.",
                  "Has editions since 2010 and recurs at least twice; a single edition counts only when it represents the field that year.",
                ],
              ] as [string, string, string][]
            ).map(([key, ko, en]) => (
              <div key={key} className="flex gap-3">
                <dt className="shrink-0 font-mono text-muted-foreground">
                  {key}
                </dt>
                <dd>{t(ko, en)}</dd>
              </div>
            ))}
          </dl>
        </section>
        <p className="mt-8 text-sm leading-7 text-muted-foreground">
          {t(
            "수록률은 프로그램이 스스로 밝힌 명단 규모(공식 페이지, 도록, 보도자료)가 있을 때만 계산합니다. 그런 규모가 없으면 '미상'으로 둡니다. 수집한 명단의 인원으로 나누면 언제나 100%가 되기 때문입니다.",
            "Coverage is computed only where the programme states its own roster size (official page, catalogue or press release). Where it does not, coverage is unknown: dividing by the roster as collected would always give 100%.",
          )}
        </p>
        <table className="mt-4 w-full text-sm [&_td]:pr-3 [&_th]:pr-3">
          <thead>
            <tr className="border-b border-border text-left label-caps">
              <th className="py-2">{t("코드", "Code")}</th>
              <th>{t("이름", "Name")}</th>
              <th>{t("연도", "Years")}</th>
              <th>{t("명단", "Roster")}</th>
              <th>{t("수록", "Included")}</th>
              <th>{t("수록률", "Coverage")}</th>
              <th>{t("수집", "Collected")}</th>
              <th>{t("판정", "Verdict")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => {
              const roster = e.roster_count ?? 0;
              const included = e.included_count ?? 0;
              // Coverage is computed by the snapshot only against a roster size the programme
              // states itself (F4, docs/RULES.md). No fallback here: dividing by roster_count
              // would read 100% by construction.
              const pct = e.coverage_pct ?? null;
              return (
                <tr key={e.id} className="border-b border-border align-top">
                  <td className="py-2 font-mono">{e.code}</td>
                  <td>
                    {roster > 0 ? (
                      <Link
                        to="/artists"
                        search={{ group: "frame", fr: e.code }}
                        className="underline-offset-4 hover:text-primary hover:underline"
                      >
                        {lang === "ko" ? e.name_ko : (e.name_en ?? e.name_ko)}
                      </Link>
                    ) : lang === "ko" ? (
                      e.name_ko
                    ) : (
                      (e.name_en ?? e.name_ko)
                    )}
                    {e.source_url ? (
                      <>
                        {" "}
                        <a
                          href={e.source_url}
                          className="text-accent"
                          target="_blank"
                          rel="noreferrer"
                        >
                          {t("출처", "Source")}
                        </a>
                      </>
                    ) : null}
                    {e.editions && e.editions.some((ed) => ed.edition) ? (
                      <span className="mt-1 block font-mono text-[11px] text-muted-foreground">
                        {e.editions
                          .filter((ed) => ed.edition)
                          .map((ed) => `${ed.edition} ${ed.roster_count}`)
                          .join(" · ")}
                      </span>
                    ) : null}
                    {e.eligibility?.note ? (
                      <span className="mt-1 block text-[11px] leading-5 text-muted-foreground">
                        {e.eligibility.note}
                      </span>
                    ) : null}
                  </td>
                  <td>
                    {e.years_covered ?? "—"}
                    {e.status === "active" ? (
                      <span className="mt-1 block text-[11px] text-muted-foreground">
                        {isRunning(e.years_covered)
                          ? t("진행 중", "running")
                          : t("종료", "ended")}
                      </span>
                    ) : null}
                  </td>
                  <td className="font-mono">{roster || "—"}</td>
                  <td className="font-mono">{included}</td>
                  <td className="font-mono">
                    {pct == null ? (
                      included > 0 ? (
                        t("미상", "unknown")
                      ) : (
                        "—"
                      )
                    ) : e.roster_size_source ? (
                      <a
                        href={e.roster_size_source}
                        className="hover:text-primary hover:underline"
                        target="_blank"
                        rel="noreferrer"
                      >{`${pct}%`}</a>
                    ) : (
                      `${pct}%`
                    )}
                  </td>
                  <td className="font-mono text-xs">
                    {/* An entry with a roster but no single fetch date (APE-2026: each row was
                        collected from its own public link) shows that every row carries its own date. */}
                    {e.last_fetched_at ??
                      (roster > 0 ? t("행마다 기록", "per row") : "—")}
                  </td>
                  <td className="text-xs">
                    {e.eligibility ? (
                      <span title={eligibilityDetail(e.eligibility)}>
                        {t(
                          VERDICT_KO[e.eligibility.decision] ??
                            e.eligibility.decision,
                          VERDICT_EN[e.eligibility.decision] ??
                            e.eligibility.decision,
                        )}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
