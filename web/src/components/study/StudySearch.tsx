// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { searchStudyRecords } from "@/lib/giye.functions";
import { searchStudy, type Layout, type StudyData } from "./model";

type T = (ko: string, en: string) => string;

type RecordHit = {
  id: string;
  ord: number;
  artist_id: string;
  year: number;
  title: string;
  venue: string | null;
};

type Option =
  | { kind: "artist"; idx: number }
  | { kind: "record"; hit: RecordHit }
  | { kind: "frame"; idx: number };

/** Top search: one field, results grouped by kind in a dropdown. */
export function StudySearch({
  data,
  layout,
  lang,
  t,
  nameOf,
  onArtist,
  onRecord,
  onFrame,
}: {
  data: StudyData;
  layout: Layout;
  lang: "ko" | "en";
  t: T;
  nameOf: (i: number) => string;
  onArtist: (artist: number) => void;
  onRecord: (artist: number, ord: number | null) => void;
  onFrame: (group: number) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [recordHits, setRecordHits] = useState<{ q: string; stamp: string; hits: RecordHit[] }>({
    q: "",
    stamp: "",
    hits: [],
  });
  const inputRef = useRef<HTMLInputElement>(null);

  const artistIndex = useMemo(() => new Map(data.artists.map((a, i) => [a.id, i])), [data.artists]);
  const results = useMemo(() => searchStudy(data, layout, query), [data, layout, query]);
  // Names are in the payload. Record text is not (W1): ask the server, and only once the
  // query is long enough that a one-letter scan would not walk the ledger.
  const qTrim = query.trim();
  useEffect(() => {
    const q = qTrim.slice(0, 80);
    if (q.length < 2) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      searchStudyRecords({ data: { q } })
        .then((res) => {
          if (alive) setRecordHits({ q, stamp: res.stamp, hits: res.hits });
        })
        .catch(() => {
          if (alive) setRecordHits({ q, stamp: "", hits: [] });
        });
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [qTrim]);
  const shownHits = recordHits.q === qTrim.slice(0, 80) && qTrim.length >= 2 ? recordHits.hits : [];
  const options: Option[] = useMemo(
    () => [
      ...results.artists.map((idx) => ({ kind: "artist" as const, idx })),
      ...shownHits.map((hit) => ({ kind: "record" as const, hit })),
      ...results.frames.map((idx) => ({ kind: "frame" as const, idx })),
    ],
    [results, shownHits],
  );
  const showList = open && qTrim.length > 0;

  const choose = (o: Option | undefined) => {
    if (!o) return;
    if (o.kind === "artist") onArtist(o.idx);
    else if (o.kind === "record") {
      const a = artistIndex.get(o.hit.artist_id);
      if (a != null) onRecord(a, recordHits.stamp === data.stamp ? o.hit.ord : null);
    } else onFrame(o.idx);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => (options.length ? (a + 1) % options.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (options.length ? (a - 1 + options.length) % options.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(options[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (query) setQuery("");
      else inputRef.current?.blur();
      setOpen(false);
    }
  };

  const optionId = (n: number) => `study-q-opt-${n}`;
  let n = -1;
  const row = (o: Option, main: string, sub: string) => {
    n += 1;
    const k = n;
    return (
      <li
        key={o.kind === "record" ? `record-${o.hit.id}` : `${o.kind}-${o.idx}`}
        id={optionId(k)}
        role="option"
        aria-selected={k === active}
        onMouseDown={(e) => e.preventDefault()}
        onMouseEnter={() => setActive(k)}
        onClick={() => choose(o)}
        className={`cursor-pointer px-3 py-1.5 ${k === active ? "bg-foreground/[0.07]" : ""}`}
      >
        <span className="block truncate text-[12.5px] text-foreground">{main}</span>
        {sub ? (
          <span className="block truncate font-mono text-[10px] text-muted-foreground">{sub}</span>
        ) : null}
      </li>
    );
  };
  const heading = (label: string) => (
    <li
      role="presentation"
      className="border-t border-foreground/15 px-3 pb-0.5 pt-2 font-mono text-[9.5px] uppercase tracking-[0.14em] text-muted-foreground first:border-t-0"
    >
      {label}
    </li>
  );

  return (
    <div className="relative">
      <div className="flex items-center gap-2 border-b border-foreground/30 py-1 focus-within:border-foreground">
        <label htmlFor="study-q" className="sr-only">
          {t("검색", "Search")}
        </label>
        <input
          ref={inputRef}
          id="study-q"
          role="combobox"
          aria-expanded={showList}
          aria-controls="study-q-list"
          aria-autocomplete="list"
          aria-activedescendant={showList && options.length ? optionId(active) : undefined}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
          placeholder={t("검색", "Search")}
          autoComplete="off"
          spellCheck={false}
          className="min-w-0 flex-1 bg-transparent py-1 text-[13px] text-foreground outline-none placeholder:text-muted-foreground/70"
        />
        <span aria-hidden="true" className="font-mono text-[10px] text-muted-foreground/70">
          /
        </span>
      </div>

      {showList ? (
        <ul
          id="study-q-list"
          role="listbox"
          aria-label={t("검색 결과", "Search results")}
          className="absolute inset-x-0 top-full z-30 mt-1 max-h-[min(60vh,28rem)] overflow-y-auto border border-foreground/40 bg-background py-1 shadow-[0_6px_24px_rgba(0,0,0,0.1)]"
        >
          {options.length === 0 ? (
            <li className="px-3 py-2 text-[12px] text-muted-foreground">
              {t("찾는 기록이 없습니다", "No matching records")}
            </li>
          ) : null}
          {results.artists.length ? heading(t("작가", "Artists")) : null}
          {results.artists.map((i) => {
            const a = data.artists[i]!;
            const other = lang === "ko" ? a.name_en : a.name_ko;
            const recs = layout.artists[i]!.sourced;
            return row(
              { kind: "artist", idx: i },
              nameOf(i),
              [other, t(`기록 ${recs}`, `${recs} records`)].filter(Boolean).join(" · "),
            );
          })}
          {shownHits.length ? heading(t("전시 · 활동", "Exhibitions · activities")) : null}
          {shownHits.map((hit) => {
            const a = artistIndex.get(hit.artist_id);
            return row(
              { kind: "record", hit },
              hit.title,
              [a != null ? nameOf(a) : "", hit.venue, String(hit.year)].filter(Boolean).join(" · "),
            );
          })}
          {results.frames.length ? heading(t("표집틀", "Frames")) : null}
          {results.frames.map((gi) => {
            const g = layout.groups[gi]!;
            return row(
              { kind: "frame", idx: gi },
              lang === "ko" ? g.label_ko : g.label_en,
              t(`작가 ${g.n}`, `${g.n} artists`),
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
