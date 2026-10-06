// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute } from "@tanstack/react-router";
import {
  getContentPage,
  getCoverage,
  getFrameEntries,
} from "@/lib/giye.functions";
import { AboutNav } from "@/components/SiteChrome";
import {
  ContentPageView,
  type ContentPageData,
} from "@/components/ContentPage";
import {
  admittedProgrammesEn,
  admittedProgrammesKo,
  countFrameDecisions,
} from "@/lib/frame-population";
import { useLang } from "@/lib/i18n";
import { absoluteUrl, pageTitle } from "@/config/site";

export const Route = createFileRoute("/about/methodology")({
  loader: async () => ({
    content: await getContentPage({ data: { slug: "methodology" } }),
    coverage: await getCoverage(),
    frames: await getFrameEntries(),
  }),
  head: () => ({
    meta: [
      { title: pageTitle("구축 방법론 Methodology") },
      {
        name: "description",
        content:
          "자료 수집, 중복 제거, 출처 기록 절차. How Giye collects, de-duplicates and sources its records.",
      },
      { property: "og:title", content: "구축 방법론 Methodology — 기예 Giye" },
      { property: "og:description", content: "How the Giye dataset is built." },
      { property: "og:url", content: absoluteUrl("/about/methodology") },
    ],
    links: [{ rel: "canonical", href: absoluteUrl("/about/methodology") }],
  }),
  component: MethodologyPage,
});

function MethodologyPage() {
  const { content, coverage, frames } = Route.useLoaderData();
  const { t } = useLang();
  const refreshed = coverage?.generated_at?.slice(0, 10) ?? "—";
  const { admitted, adjacent } = countFrameDecisions(frames);
  const phraseKo = admittedProgrammesKo(admitted, adjacent);
  const phraseEn = admittedProgrammesEn(admitted, adjacent);
  const published = coverage?.published_artists ?? 0;

  return (
    <div>
      <AboutNav />
      <div className="wrap max-w-3xl pt-6">
        <p className="border border-border bg-card px-4 py-3 text-sm leading-7">
          {t(
            `${phraseKo} · 공개 작가 ${published}명 · 마지막 갱신 ${refreshed}. 매주 링크를 점검하고 바뀐 CV와 요청을 반영하며, 새 회차의 명단은 공개되면 수집합니다.`,
            `${phraseEn} · published artists: ${published} · last refresh: ${refreshed}. Weekly link, CV and request intake; a new edition's roster is collected when it is published.`,
          )}
        </p>
      </div>
      <ContentPageView {...(content as ContentPageData)} />
    </div>
  );
}
