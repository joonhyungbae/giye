// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute } from "@tanstack/react-router";
import { CiteDialog } from "@/components/CiteDialog";
import { contactHref, datasetIndexTitle, fieldPeopleEn, site } from "@/config/site";
import { admittedProgrammesEn, admittedProgrammesKo } from "@/lib/frame-population";
import { getDatasetVersions, getHomeStats } from "@/lib/giye.functions";
import { useLang } from "@/lib/i18n";

export const Route = createFileRoute("/data")({
  loader: async () => ({
    versions: await getDatasetVersions(),
    stats: await getHomeStats(),
  }),
  head: () => ({
    meta: [
      { title: "GIYE" },
      {
        name: "description",
        content:
          "기예 데이터는 이 웹사이트에서 열람할 수 있습니다. 자동화된 수집과 대량 복제는 허용하지 않으며, 연구용 사람 단위 데이터는 이용 약정 아래 요청 시 공유합니다. Giye data can be read on this website; automated collection and bulk copying are not permitted, and person-level data for research are shared on request under a data-use agreement.",
      },
      { property: "og:title", content: "인용 Citation — 기예 Giye" },
      {
        property: "og:description",
        content: `${fieldPeopleEn(true)} index — versions and citation.`,
      },
      { property: "og:url", content: "/data" },
    ],
    links: [{ rel: "canonical", href: "/data" }],
  }),
  component: DataPage,
});

function DataPage() {
  const { versions, stats } = Route.useLoaderData();
  const { t } = useLang();

  return (
    <div className="wrap py-12 lg:py-20">
      <header className="border-b border-input pb-10">
        <p className="font-mono text-[10px] uppercase text-primary">[ DATA ]</p>
        <div className="mt-5 grid gap-8 lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-end">
          <h1 className="font-mono text-5xl font-bold italic leading-none sm:text-7xl">
            {t("인용과 버전", "Citation and versions")}
          </h1>
          <p className="max-w-xl leading-7 text-muted-foreground">
            {t(
              "기예의 기록은 이 웹사이트에서 읽을 수 있습니다. 크롤러·스크립트를 이용한 자동 수집과 대량 복제·재배포는 허용하지 않습니다. 연구에 사람 단위 데이터가 필요하면 이용 약정을 맺고 ",
              "Giye's records can be read on this website. Automated collection by crawlers or scripts, and bulk copying or redistribution, are not permitted. Researchers who need person-level data can request it under a data-use agreement by writing to ",
            )}
            <a
              className="underline decoration-input underline-offset-4 hover:decoration-primary"
              href={contactHref()}
            >
              {site.contactEmail}
            </a>
            {t(
              "로 요청할 수 있습니다. 인용은 아래 인용 정보를 사용하세요.",
              ". To cite, use the citation below.",
            )}
          </p>
        </div>
      </header>

      <section className="grid gap-8 py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
        <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
          01 / {t("인용", "Citation")}
        </h2>
        <div>
          <p className="flex flex-wrap gap-3 text-sm">
            <CiteDialog
              title={`기예 Giye — ${datasetIndexTitle()}`}
              author={stats.citation.author}
              version={stats.citation.version}
              released={stats.citation.released_at}
              url={`${site.origin}/data`}
              year={stats.citation.year}
            />
          </p>
          <div className="mt-8 grid max-w-xl grid-cols-2 border-t border-border pt-5 sm:grid-cols-4">
            <p>
              <span className="label-caps block">{t("현재 버전", "Current version")}</span>
              <strong className="mt-1 block font-display text-2xl font-medium">
                v{stats.version}
              </strong>
            </p>
            <p>
              <span className="label-caps block">{t("공개 기록", "Published records")}</span>
              <strong className="mt-1 block font-display text-2xl font-medium">
                {stats.artistCount}
              </strong>
            </p>
            <p>
              <span className="label-caps block">{t("표집틀", "Sampling frame")}</span>
              <strong className="mt-1 block text-base font-medium leading-6">
                {t(
                  admittedProgrammesKo(stats.admittedProgrammes, stats.adjacentStrands),
                  admittedProgrammesEn(stats.admittedProgrammes, stats.adjacentStrands),
                )}
              </strong>
            </p>
            <p>
              <span className="label-caps block">{t("마지막 갱신", "Last refresh")}</span>
              <strong className="mt-1 block font-display text-lg font-medium">
                {stats.lastRefreshedAt ? String(stats.lastRefreshedAt).slice(0, 10) : "—"}
              </strong>
            </p>
          </div>
          <p className="mt-5 text-xs text-muted-foreground">
            {t("공개 기록만 포함됩니다", "Published records only")} · {stats.artistCount}{" "}
            {t("건", "records")}
            {stats.cadence ? (
              <>
                {" · "}
                {t("주간 링크 검사 / 새 회차는 공개 시 수집", "weekly link check / new editions collected on publication")}
              </>
            ) : null}
          </p>
        </div>
      </section>

      <section className="grid gap-8 border-t border-input py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
        <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
          02 / {t("버전", "Versions")}
        </h2>
        <div className="min-w-0 max-w-full overflow-x-auto">
          <table className="w-full min-w-xl text-sm">
            <thead>
              <tr className="border-b border-border text-left label-caps">
                <th className="py-2">{t("버전", "Version")}</th>
                <th>{t("공개일", "Released")}</th>
                <th>DOI</th>
                <th>{t("건수", "Count")}</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr
                  key={v.id}
                  className="border-b border-border transition-colors hover:border-primary"
                >
                  <td className="py-4 font-mono text-primary">v{v.version}</td>
                  <td>{v.released_at}</td>
                  <td>{v.doi ?? "—"}</td>
                  <td className="font-mono">{v.artist_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="grid gap-8 border-t border-input py-12 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-24">
        <h2 className="font-mono text-[10px] uppercase text-muted-foreground">
          03 / {t("소리", "Sound")}
        </h2>
        <div className="min-w-0 text-sm leading-7">
          <p>
            {t(
              "첫 화면의 원장 합주는 아카이브의 기록만을 악보로 삼습니다. 연도 고리가 세포, 작가가 목소리, 기록 하나가 반복 한 번입니다. 진행 방식은 테리 라일리의 《In C》(1964)에서 과정만 빌렸고 악보의 음은 쓰지 않았습니다.",
              "The ledger ensemble on the front page reads only the archive: year rings are cells, artists are voices, one record is one repetition. The process is borrowed from Terry Riley's In C (1964); none of its material is used.",
            )}
          </p>
          <p className="mt-3 text-muted-foreground">
            {t("악기 샘플", "Instrument samples")}:{" "}
            <a
              className="underline decoration-input underline-offset-4 hover:decoration-primary"
              href="https://github.com/sgossner/VCSL"
            >
              Versilian Community Sample Library
            </a>{" "}
            ({t("비브라폰·마림바·글로켄슈필, CC0", "vibraphone, marimba, glockenspiel; CC0")}) ·{" "}
            <a
              className="underline decoration-input underline-offset-4 hover:decoration-primary"
              href="https://archive.org/details/SalamanderGrandPianoV3"
            >
              Salamander Grand Piano v3
            </a>{" "}
            {t("(Alexander Holm, CC BY 3.0)", "(Alexander Holm, CC BY 3.0)")}
          </p>
        </div>
      </section>
    </div>
  );
}
