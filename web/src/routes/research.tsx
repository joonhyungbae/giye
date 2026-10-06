// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute } from "@tanstack/react-router";
import { getResearch } from "@/lib/giye.functions";
import { useLang } from "@/lib/i18n";
import { absoluteUrl, pageTitle } from "@/config/site";

export const Route = createFileRoute("/research")({
  loader: () => getResearch(),
  head: () => ({
    meta: [
      { title: pageTitle("연구 Research") },
      {
        name: "description",
        content:
          "기예 데이터를 사용한 논문과 보고서 목록. Publications and reports that use Giye data.",
      },
      { property: "og:title", content: "연구 Research — 기예 Giye" },
      { property: "og:description", content: "Publications that cite the Giye dataset." },
      { property: "og:url", content: absoluteUrl("/research") },
    ],
    links: [{ rel: "canonical", href: absoluteUrl("/research") }],
  }),
  component: ResearchPage,
});

function ResearchPage() {
  const items = Route.useLoaderData();
  const { t } = useLang();
  return (
    <div className="wrap max-w-3xl py-12">
      <h1 className="text-3xl">{t("연구", "Research")}</h1>
      <p className="mt-4 text-muted-foreground">
        {t(
          "기예 데이터를 인용한 연구를 기록합니다. 누락된 연구가 있다면 요청 폼으로 알려 주세요.",
          "Work that cites Giye data is recorded here. Tell us about anything missing through the request form.",
        )}
      </p>
      <ul className="mt-8 divide-y divide-border">
        {items.map((p) => (
          <li key={p.id} className="py-4">
            <p className="font-serif text-lg">{p.title}</p>
            <p className="text-sm text-muted-foreground">
              {p.authors} · {p.year}
              {p.venue ? ` · ${p.venue}` : ""}
            </p>
            {(p.doi || p.url) && (
              <p className="mt-1 text-sm">
                <a
                  href={p.doi ? `https://doi.org/${p.doi}` : (p.url as string)}
                  className="text-accent"
                  target="_blank"
                  rel="noreferrer"
                >
                  {p.doi ? `doi:${p.doi}` : p.url}
                </a>
              </p>
            )}
          </li>
        ))}
        {items.length === 0 && (
          <li className="py-6 text-sm text-muted-foreground">
            {t("아직 기록된 연구가 없습니다.", "No publications recorded yet.")}
          </li>
        )}
      </ul>
    </div>
  );
}
