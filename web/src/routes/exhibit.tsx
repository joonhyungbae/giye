// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute } from "@tanstack/react-router";
import { lazy, Suspense } from "react";
import { getStudyData } from "@/lib/giye.functions";
import type { StudyData } from "@/components/study/model";
import { unpackRecords, type Packed } from "@/lib/study-pack";
import { useLang } from "@/lib/i18n";
import { absoluteUrl, pageTitle } from "@/config/site";

// Same payload as the home study (getStudyData, W1 + W4); see routes/index.tsx for why the
// loader starts the view import itself.
function loadExhibit() {
  return import("@/components/exhibit/Exhibit").then((m) => ({
    default: m.Exhibit,
  }));
}

const Exhibit = lazy(loadExhibit);

async function loadStudy(): Promise<StudyData> {
  const res = (await getStudyData()) as unknown as Response;
  const raw = (await res.json()) as Omit<StudyData, "records"> & {
    records_packed: Packed;
  };
  const { records_packed, ...rest } = raw;
  return { ...rest, records: unpackRecords(records_packed) };
}

export const Route = createFileRoute("/exhibit")({
  ssr: false,
  staleTime: Infinity,
  shouldReload: false,
  loader: async (): Promise<{ study: StudyData }> => {
    void import("@/components/exhibit/Exhibit");
    return { study: await loadStudy() };
  },
  head: () => ({
    meta: [
      { title: pageTitle("전시 Exhibit") },
      {
        name: "description",
        content:
          "기예의 기록을 설치 작품으로: 기록의 원반, 중심에서 섬들로, 기술의 성부. The archive as an installation: the record disc, from a centre to islands, and the voices of six technologies.",
      },
      { property: "og:title", content: "전시 Exhibit — 기예 Giye" },
      { property: "og:url", content: absoluteUrl("/exhibit") },
    ],
    links: [{ rel: "canonical", href: absoluteUrl("/exhibit") }],
  }),
  pendingComponent: () => <Opening />,
  component: ExhibitPage,
});

function Opening() {
  const { t } = useLang();
  return (
    <div className="h-dvh w-full" style={{ background: "#0b0d10" }}>
      <p
        className="p-6 font-mono text-[11px]"
        style={{ color: "#e8eaed", opacity: 0.6 }}
      >
        {t("원장 여는 중…", "Opening the ledger…")}
      </p>
    </div>
  );
}

function ExhibitPage() {
  const data = Route.useLoaderData() as { study: StudyData };
  return (
    <Suspense fallback={<Opening />}>
      <Exhibit data={data.study} />
    </Suspense>
  );
}
