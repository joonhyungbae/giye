// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { Search as SearchIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fieldPeopleEn } from "@/config/site";
import { getArtistIndex, getFrameEntries, getVocabularies } from "@/lib/giye.functions";
import { filterArtists, seededShuffle, type Artist } from "@/lib/giye.types";
import { useLang, VERIFICATION_LABEL } from "@/lib/i18n";

type Search = {
  q?: string;
  medium?: string[];
  technique?: string[];
  theme?: string[];
  region?: string[];
  decade?: string[];
  verification?: string[];
  frame?: string;
  /** One event of the sampling frame, by registry code. */
  fr?: string;
  sort?: "random" | "alpha";
  group?: "medium" | "region" | "decade" | "frame";
};

const toArray = (v: unknown): string[] | undefined => {
  if (Array.isArray(v)) return v.map(String).filter(Boolean);
  if (typeof v === "string" && v) return v.split(",").filter(Boolean);
  return undefined;
};

export const Route = createFileRoute("/artists")({
  validateSearch: (search: Record<string, unknown>): Search => ({
    q: typeof search.q === "string" && search.q ? search.q : undefined,
    medium: toArray(search.medium),
    technique: toArray(search.technique),
    theme: toArray(search.theme),
    region: toArray(search.region),
    decade: toArray(search.decade),
    verification: toArray(search.verification),
    frame:
      search.frame === "IN_FRAME" || search.frame === "OUT_OF_FRAME" ? search.frame : undefined,
    fr: typeof search.fr === "string" && /^[A-Z0-9-]+$/.test(search.fr) ? search.fr : undefined,
    sort: search.sort === "alpha" ? "alpha" : undefined,
    group:
      search.group === "region" ||
      search.group === "decade" ||
      search.group === "medium" ||
      search.group === "frame"
        ? search.group
        : undefined,
  }),
  loader: async () => ({
    artists: await getArtistIndex(),
    vocabularies: await getVocabularies(),
    frames: await getFrameEntries(),
  }),
  head: () => ({
    meta: [
      { title: "GIYE" },
      {
        name: "description",
        content: `매체·기법·주제·지역별로 한국 미디어 작가 기록을 목록으로 탐색합니다. Browse ${fieldPeopleEn(false)} records as a list.`,
      },
      { property: "og:title", content: "탐색 Browse — 기예 Giye" },
      { property: "og:description", content: `Browse the Giye index of ${fieldPeopleEn(true)}.` },
      { property: "og:url", content: "/artists" },
    ],
    links: [{ rel: "canonical", href: "/artists" }],
  }),
  component: Artists,
});

function Artists() {
  const { artists, vocabularies, frames } = Route.useLoaderData();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/artists" });
  const { lang, t } = useLang();
  // Seed 0 during SSR/first render keeps server and client HTML identical;
  // the per-session shuffle is applied once after mount.
  const [seed, setSeed] = useState(0);
  useEffect(() => {
    setSeed(Math.floor(Math.random() * 1_000_000) + 1);
  }, []);
  const [query, setQuery] = useState(search.q ?? "");

  const set = (patch: Partial<Search>) =>
    navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true });

  const toggle = (key: keyof Search, value: string) => {
    const current = (search[key] as string[] | undefined) ?? [];
    const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
    set({ [key]: next.length ? next : undefined } as Partial<Search>);
  };

  const results = useMemo(() => {
    const filtered = filterArtists(artists as Artist[], {
      q: search.q,
      medium: search.medium,
      technique: search.technique,
      theme: search.theme,
      region: search.region,
      decade: search.decade,
      verification: search.verification,
      frame: search.frame,
      frameCode: search.fr,
    });
    return search.sort === "alpha"
      ? [...filtered].sort((a, b) =>
          lang === "ko"
            ? a.name_ko.localeCompare(b.name_ko, "ko")
            : (a.name_en ?? a.name_ko).localeCompare(b.name_en ?? b.name_ko, "en"),
        )
      : seededShuffle(filtered, seed);
  }, [artists, search, seed, lang]);

  const terms = (category: string) =>
    vocabularies
      .filter((v) => v.category === category)
      .map((v) => (lang === "ko" ? v.term_ko : (v.term_en ?? v.term_ko)));

  const decades = Array.from(
    new Set(
      (artists as Artist[])
        .filter((a) => a.active_since)
        .map((a) => String(Math.floor((a.active_since ?? 0) / 10) * 10)),
    ),
  ).sort();

  const groupAxis = search.group ?? "medium";

  // Shelf view: filtered results are distributed over the chosen axis
  // (medium / region / decade). An artist with several tags appears in
  // each matching group; artists without a value fall into "기타/Other".
  // Events of the sampling frame that actually have a roster, in registry order.
  const frameRows = useMemo(() => frames.filter((f) => (f.roster_count ?? 0) > 0), [frames]);
  const frameName = useCallback(
    (code: string) => {
      const f = frames.find((x) => x.code === code);
      return f ? (lang === "ko" ? f.name_ko : (f.name_en ?? f.name_ko)) : code;
    },
    [frames, lang],
  );
  const otherFilters = Boolean(
    search.q ||
    search.medium ||
    search.technique ||
    search.theme ||
    search.region ||
    search.decade ||
    search.verification ||
    search.frame,
  );

  const groups = useMemo(() => {
    if (groupAxis === "frame") {
      // One shelf per event edition. Each shelf also says how
      // many people the public roster lists, so a mostly unpublished edition reads as
      // "being confirmed" rather than as small.
      const shelves: {
        key: string;
        label: string;
        note?: string;
        artists: Artist[];
      }[] = [];
      for (const f of frameRows) {
        if (search.fr && f.code !== search.fr) continue;
        const editions = f.editions?.length
          ? f.editions
          : [
              {
                edition: null,
                label: null,
                roster_count: f.roster_count ?? 0,
                published_count: 0,
                out_of_scope_count: 0,
              },
            ];
        for (const ed of editions) {
          const members = (results as Artist[]).filter((a) =>
            (a.frame_editions ?? []).some(
              (fe) => fe.frame === f.code && (fe.edition ?? null) === (ed.edition ?? null),
            ),
          );
          if (members.length === 0 && otherFilters) continue;
          const name = frameName(f.code);
          shelves.push({
            key: `${f.code}|${ed.edition ?? ""}`,
            label: ed.edition && !name.includes(ed.edition) ? `${name} ${ed.edition}` : name,
            note: [
              ed.label,
              t(
                `공개 ${ed.published_count} / 명단 ${ed.roster_count}`,
                `${ed.published_count} published of ${ed.roster_count} on the roster`,
              ),
              ed.out_of_scope_count
                ? t(
                    `포함 기준 외 ${ed.out_of_scope_count}`,
                    `${ed.out_of_scope_count} outside inclusion criteria`,
                  )
                : null,
            ]
              .filter(Boolean)
              .join(" · "),
            artists: members,
          });
        }
      }
      if (!search.fr) {
        const outside = (results as Artist[]).filter((a) => !(a.frame_editions ?? []).length);
        if (outside.length > 0) {
          shelves.push({ key: "__out__", label: t("표집틀 외", "Out of frame"), artists: outside });
        }
      }
      return shelves;
    }
    const vocabOptions = (category: string) =>
      vocabularies
        .filter((v) => v.category === category)
        .map((v) => ({
          key: v.term_ko,
          label: lang === "ko" ? v.term_ko : (v.term_en ?? v.term_ko),
        }));
    const axisOptions: { key: string; label: string }[] =
      groupAxis === "medium"
        ? vocabOptions("medium")
        : groupAxis === "region"
          ? vocabOptions("region")
          : decades.map((d) => ({ key: d, label: `${d}s` }));
    const keysOf = (a: Artist): string[] =>
      groupAxis === "medium"
        ? a.medium_tags
        : groupAxis === "region"
          ? a.regions
          : a.active_since != null
            ? [String(Math.floor(a.active_since / 10) * 10)]
            : [];
    const byKey = new Map<string, Artist[]>();
    const other: Artist[] = [];
    for (const a of results as Artist[]) {
      const keys = keysOf(a);
      if (keys.length === 0) {
        other.push(a);
        continue;
      }
      for (const k of keys) {
        const bucket = byKey.get(k);
        if (bucket) bucket.push(a);
        else byKey.set(k, [a]);
      }
    }
    const ordered = axisOptions
      .filter((o) => byKey.has(o.key))
      .map((o) => ({ key: o.key, label: o.label, artists: byKey.get(o.key)! }));
    for (const [key, artistsInGroup] of byKey) {
      if (!axisOptions.some((o) => o.key === key)) {
        ordered.push({ key, label: key, artists: artistsInGroup });
      }
    }
    if (other.length > 0) {
      ordered.push({ key: "__other__", label: t("기타", "Other"), artists: other });
    }
    return ordered as { key: string; label: string; note?: string; artists: Artist[] }[];
  }, [
    results,
    groupAxis,
    vocabularies,
    decades,
    lang,
    t,
    frameRows,
    frameName,
    search.fr,
    otherFilters,
  ]);

  // Rows are drawn a page at a time per shelf (the default shelf can hold most of the archive);
  // reaching a shelf's end draws the next page. A new filter or grouping starts over.
  const [shown, setShown] = useState<Record<string, number>>({});
  const resetKey = JSON.stringify([search, seed, lang]);
  useEffect(() => setShown({}), [resetKey]);
  const more = useCallback(
    (key: string) => setShown((m) => ({ ...m, [key]: (m[key] ?? PAGE) + PAGE })),
    [],
  );

  const facet = (title: string, key: keyof Search, options: { value: string; label: string }[]) => (
    <fieldset className="min-w-0 border-t border-border pt-5">
      <legend className="font-mono text-[10px] uppercase text-muted-foreground">{title}</legend>
      <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2.5 md:grid-cols-1">
        {options.map((o) => (
          <label
            key={o.value}
            className="flex cursor-pointer items-center gap-2.5 text-xs text-muted-foreground transition-colors hover:text-primary"
          >
            <input
              type="checkbox"
              checked={((search[key] as string[] | undefined) ?? []).includes(o.value)}
              onChange={() => toggle(key, o.value)}
              className="size-3.5 shrink-0 appearance-none border border-input checked:border-primary checked:bg-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            />
            {o.label}
          </label>
        ))}
      </div>
    </fieldset>
  );

  return (
    <div className="wrap py-12 lg:py-20">
      <div className="grid items-start gap-14 md:grid-cols-[13rem_minmax(0,1fr)] md:gap-16 lg:gap-24">
        <aside className="border-t border-input pt-6 md:sticky md:top-8">
          <div className="mb-12">
            <p className="font-mono text-[10px] uppercase text-muted-foreground">Index / 분류</p>
            <h1 className="mt-5 font-mono text-xl font-bold leading-snug">
              {t("작가 탐색", "Browse artists")}
            </h1>
            <p lang="en" className="mt-1 text-sm font-light text-muted-foreground">
              Artist Index
            </p>
          </div>
          <h2 className="mb-5 font-mono text-[10px] uppercase text-muted-foreground">
            {t("검색과 필터", "Search & filter")}
          </h2>
          <form
            className="relative"
            onSubmit={(e) => {
              e.preventDefault();
              set({ q: query || undefined });
            }}
          >
            <label htmlFor="q" className="sr-only">
              {t("검색", "Search")}
            </label>
            <input
              id="q"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("이름, 별칭, 키워드", "Name, alias, keyword")}
              className="h-11 w-full border-b border-input bg-transparent px-0 pr-10 text-sm placeholder:text-muted-foreground focus:border-primary focus:outline-none"
            />
            <Button
              type="submit"
              variant="ghost"
              size="icon"
              className="absolute bottom-0 right-0 size-10 rounded-none hover:bg-transparent hover:text-primary"
              aria-label={t("검색", "Search")}
            >
              <SearchIcon className="size-4" />
            </Button>
          </form>

          <div className="mt-7 space-y-6">
            {facet(
              t("매체", "Medium"),
              "medium",
              terms("medium").map((v) => ({ value: v, label: v })),
            )}
            {facet(
              t("기법", "Technique"),
              "technique",
              terms("technique").map((v) => ({ value: v, label: v })),
            )}
            {facet(
              t("주제", "Theme"),
              "theme",
              terms("theme").map((v) => ({ value: v, label: v })),
            )}
            {facet(
              t("지역", "Region"),
              "region",
              terms("region").map((v) => ({ value: v, label: v })),
            )}
            {facet(
              t("활동 시작", "Active since"),
              "decade",
              decades.map((d) => ({ value: d, label: `${d}s` })),
            )}
            {facet(
              t("확인 상태", "Verification"),
              "verification",
              Object.keys(VERIFICATION_LABEL).map((k) => ({
                value: k,
                label: t(VERIFICATION_LABEL[k][0], VERIFICATION_LABEL[k][1]),
              })),
            )}
            <fieldset className="min-w-0 border-t border-border pt-5">
              <legend className="font-mono text-[10px] uppercase text-muted-foreground">
                {t("표집틀", "Sampling frame")}
              </legend>
              <select
                value={search.fr ? `code:${search.fr}` : (search.frame ?? "")}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v.startsWith("code:")) set({ fr: v.slice(5), frame: undefined });
                  else set({ frame: v || undefined, fr: undefined });
                }}
                className="mt-3 h-10 w-full border-b border-input bg-transparent px-0 pr-8 text-xs text-muted-foreground focus:border-primary focus:outline-none"
              >
                <option value="">{t("전체", "All")}</option>
                <option value="IN_FRAME">{t("표집틀 내", "In frame")}</option>
                <option value="OUT_OF_FRAME">{t("표집틀 외", "Out of frame")}</option>
                <optgroup label={t("행사별", "By event")}>
                  {frameRows.map((f) => (
                    <option key={f.code} value={`code:${f.code}`}>
                      {frameName(f.code)}
                    </option>
                  ))}
                </optgroup>
              </select>
            </fieldset>
          </div>
        </aside>

        <section className="min-w-0 max-w-3xl">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3 border-b border-input pb-4">
            <div className="flex min-w-0 items-baseline gap-3">
              <h2 className="truncate font-mono text-sm font-bold">[ BROWSE_ARTIST_INDEX ]</h2>
              <span className="shrink-0 font-mono text-xs text-muted-foreground">
                {String(results.length).padStart(3, "0")} {t("기록", "records")}
              </span>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-1">
              <span className="mr-1 font-mono text-[10px] uppercase text-muted-foreground">
                {t("묶음", "Group")}
              </span>
              {(
                [
                  ["medium", t("매체", "Medium")],
                  ["region", t("지역", "Region")],
                  ["decade", t("연대", "Decade")],
                  ["frame", t("표집틀", "Frame")],
                ] as const
              ).map(([g, label]) => (
                <Button
                  key={g}
                  variant="ghost"
                  size="sm"
                  onClick={() => set({ group: g === "medium" ? undefined : g })}
                  aria-pressed={groupAxis === g}
                  className={`h-9 rounded-none px-3 font-mono text-xs shadow-none ${groupAxis === g ? "bg-primary text-primary-foreground" : "bg-transparent text-muted-foreground hover:bg-transparent hover:text-primary"}`}
                >
                  {label}
                </Button>
              ))}
              <span className="mx-1 hidden h-4 w-px bg-border sm:inline-block" aria-hidden />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => set({ sort: undefined })}
                aria-pressed={!search.sort}
                className={`h-9 rounded-none px-4 font-mono text-xs shadow-none ${!search.sort ? "bg-primary text-primary-foreground" : "bg-transparent text-muted-foreground hover:bg-transparent hover:text-primary"}`}
              >
                {t("무작위", "Random")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => set({ sort: "alpha" })}
                aria-pressed={search.sort === "alpha"}
                className={`h-9 rounded-none px-4 font-mono text-xs shadow-none ${search.sort === "alpha" ? "bg-primary text-primary-foreground" : "bg-transparent text-muted-foreground hover:bg-transparent hover:text-primary"}`}
              >
                {t("가나다순", "A–Z")}
              </Button>
            </div>
          </div>

          {groups.length === 0 && (
            <p className="py-8 text-sm text-muted-foreground">
              {t("조건에 맞는 기록이 없습니다.", "No records match these filters.")}
            </p>
          )}
          {groups.map((g) => (
            <section key={g.key} aria-label={g.label} className="border-b border-input pb-2">
              <h3 className="flex items-baseline gap-3 pb-1 pt-10 font-mono text-base font-bold">
                <span className="text-primary">{g.label}</span>
                <span className="text-xs font-normal text-muted-foreground">
                  {String(g.artists.length).padStart(2, "0")} {t("기록", "records")}
                </span>
              </h3>
              {g.note ? (
                <p className="pb-2 font-mono text-[11px] text-muted-foreground">{g.note}</p>
              ) : null}
              {g.artists.length === 0 ? (
                <p className="py-4 text-xs text-muted-foreground">
                  {t(
                    "아직 공개된 기록이 없습니다. 명단의 작가들은 포트폴리오·이력 출처를 확인하는 중입니다.",
                    "No published records yet; artists on this roster are awaiting source confirmation.",
                  )}
                </p>
              ) : null}
              <ul>
                {g.artists.slice(0, shown[g.key] ?? PAGE).map((a) => (
                  <li
                    key={a.id}
                    className="group relative border-b border-border transition-colors duration-150 last:border-b-0 hover:bg-card focus-within:bg-card"
                  >
                    <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2 py-5 sm:py-6">
                      <span className="shrink-0 font-mono text-xs text-muted-foreground transition-colors group-hover:text-primary">
                        {a.id}
                      </span>
                      <Link
                        to="/artist/$id"
                        params={{ id: a.id }}
                        className="shrink-0 font-display text-2xl font-bold leading-tight no-underline transition-colors before:absolute before:inset-0 group-hover:text-primary sm:text-3xl"
                      >
                        {a.name_ko}
                      </Link>
                      {a.name_en ? (
                        <span
                          lang="en"
                          className="shrink-0 font-sans text-base font-light uppercase tracking-wide text-muted-foreground transition-colors group-hover:text-foreground"
                        >
                          {a.name_en}
                        </span>
                      ) : null}
                      <span
                        className="hidden shrink-0 font-mono text-xs text-muted-foreground sm:inline"
                        aria-hidden
                      >
                        ·
                      </span>
                      <p className="flex shrink-0 flex-wrap gap-x-2 gap-y-1 font-mono text-xs text-foreground">
                        {a.medium_tags.slice(0, 3).map((m) => (
                          <span key={m}>{m}</span>
                        ))}
                      </p>
                      <span className="shrink-0 whitespace-nowrap font-mono text-xs text-foreground/70">
                        {t(
                          VERIFICATION_LABEL[a.verification][0],
                          VERIFICATION_LABEL[a.verification][1],
                        )}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
              {g.artists.length > (shown[g.key] ?? PAGE) ? (
                <MoreRows
                  remaining={g.artists.length - (shown[g.key] ?? PAGE)}
                  onMore={() => more(g.key)}
                />
              ) : null}
            </section>
          ))}
        </section>
      </div>
    </div>
  );
}

const PAGE = 60;

/** End of a shelf's drawn rows: draws the next page when it scrolls into view, or on click. */
function MoreRows({ remaining, onMore }: { remaining: number; onMore: () => void }) {
  const { t } = useLang();
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) onMore();
      },
      { rootMargin: "600px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [onMore]);
  return (
    <button
      ref={ref}
      type="button"
      onClick={onMore}
      className="w-full py-5 text-left font-mono text-xs text-muted-foreground transition-colors hover:text-primary"
    >
      {t(`${remaining}명 더 보기`, `Show ${remaining} more`)}
    </button>
  );
}
