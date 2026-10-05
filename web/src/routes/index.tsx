// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute, Link } from "@tanstack/react-router";
import { lazy, Suspense } from "react";
import { getStudyData } from "@/lib/giye.functions";
import type { StudyData } from "@/components/study/model";
import { unpackRecords, type Packed } from "@/lib/study-pack";
import { useLang } from "@/lib/i18n";

// The loader starts these imports early. It may not share module-level variables with this
// file: the router splits the loader into its own chunk, where they would be undefined. The
// browser's module map already dedupes a dynamic import of the same module, so lazy() reuses it.
function loadArchivalStudy() {
  return import("@/components/study/ArchivalStudy").then((m) => ({ default: m.ArchivalStudy }));
}

const ArchivalStudy = lazy(loadArchivalStudy);

/** getStudyData answers with a raw JSON response; rebuild the StudyRecord rows from its columns. */
async function loadStudy(): Promise<StudyData> {
  const res = (await getStudyData()) as unknown as Response;
  const raw = (await res.json()) as Omit<StudyData, "records"> & { records_packed: Packed };
  const { records_packed, ...rest } = raw;
  return { ...rest, records: unpackRecords(records_packed) };
}

type HomeData = { study: StudyData };

export const Route = createFileRoute("/")({
  // The whole ledger feeds the canvas; keep it out of the server-rendered HTML so a single
  // page fetch is not a dataset download. The browser loads it through a same-origin call.
  ssr: false,
  // The canvas writes its view into the URL hash; that must not refetch and rebuild the whole
  // ledger. Load once.
  staleTime: Infinity,
  shouldReload: false,
  loader: async (): Promise<HomeData> => {
    // Start the view chunk before the payload arrives, on the same import React.lazy uses,
    // so the download overlaps the data request instead of waiting for it.
    void import("@/components/study/ArchivalStudy");
    return { study: await loadStudy() };
  },
  head: () => ({
    meta: [
      { title: "GIYE" },
      {
        name: "description",
        content:
          "기예의 기록을 연도의 고리 위에, 처음 명단에 오른 세대별로 그린 생성 시각화. The archive drawn as rings of years, arranged by the generation in which each person first appeared on a roster.",
      },
      { property: "og:title", content: "Giye — Media Art Archive" },
      { property: "og:url", content: "/" },
    ],
    links: [{ rel: "canonical", href: "/" }],
  }),
  pendingComponent: () => <Shell />,
  component: Home,
});

function Shell({ artists, detail }: { artists?: number; detail?: string }) {
  const { t } = useLang();
  const counted = artists != null && detail != null;
  return (
    <div className="relative h-dvh min-h-[34rem] w-full overflow-hidden bg-background">
      <div className="pointer-events-none absolute inset-0 p-4 sm:p-6">
        <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          기예 Giye · {t("기록 연구", "Archival Study")}
        </p>
        {counted ? (
          <p className="mt-2 font-mono text-[11px] text-foreground/80 tabular-nums">
            {t("작가", "artists")} {artists} · {detail}
          </p>
        ) : null}
        <p className="mt-1 font-mono text-[11px] text-muted-foreground">
          {t("원장 여는 중…", "Opening the ledger…")}
        </p>
      </div>
    </div>
  );
}

function Home() {
  const data = Route.useLoaderData() as HomeData;
  const { t } = useLang();
  const artists = data.study?.artists;
  if (!artists || artists.length === 0) {
    return (
      <div className="wrap py-24">
        <h1 className="text-3xl">{t("원장이 비어 있습니다", "The ledger is empty")}</h1>
        <p className="mt-4 text-muted-foreground">
          {t(
            "data/site/artists.json이 없습니다. python3 scripts/build_site_dataset.py 를 실행하세요.",
            "data/site/artists.json is missing. Run python3 scripts/build_site_dataset.py.",
          )}
        </p>
        <p className="mt-6">
          <Link to="/artists" className="text-accent">
            {t("목록으로", "List view")}
          </Link>
        </p>
      </div>
    );
  }
  return (
    <Suspense
      fallback={
        <Shell
          artists={artists.length}
          detail={t(`기록 ${data.study.records.length}`, `${data.study.records.length} records`)}
        />
      }
    >
      <ArchivalStudy data={data.study} />
    </Suspense>
  );
}
