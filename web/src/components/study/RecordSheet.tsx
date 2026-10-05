// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The record sheet: a detail card that opens over the study when a record
 * or an artist is clicked. Drawn like a detail view on an engineering sheet
 * (hairline frame, registration marks, a title block), it shows the artist's
 * archive facts and every record with its source, and offers the full page.
 */
import { Link } from "@tanstack/react-router";
import {
  memo,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { getArtistRecord } from "@/lib/giye.functions";
import { ACTIVITY_TYPE_LABEL, LINK_TYPE_LABEL, useLang } from "@/lib/i18n";
import type { ArtistLink } from "@/lib/giye.types";
import { verLabel } from "./labels";

const host = (url: string) => {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
};

type Record_ = Awaited<ReturnType<typeof getArtistRecord>>;

/** One activity row. Title and source URL arrive with getArtistRecord; the preview omits them. */
type SheetActivity = {
  id?: string;
  /** W2. Position among this artist's study-order rows. Absent when the row is not in that order. */
  ord?: number;
  venue: string | null;
  year: number;
  activity_type: string;
  collected_at: string;
  title?: string;
  source_url?: string;
  role?: string | null;
  /** Source host, present on the preview before the citation URL arrives. */
  domain?: string;
};

/** What the study already knows about an artist: enough to fill the sheet on the first frame.
 *  The server record adds the bio, titles, sources, roles and links when it arrives. */
export type SheetPreview = {
  artist: {
    name_ko: string;
    name_en: string | null;
    verification: string;
    medium_tags: string[];
    bio_short?: string | null;
  };
  activities: SheetActivity[];
};

/** placeholder widths (%) for titles still loading, chosen by a row's ord so they stay put */
const BAR_WIDTHS = [74, 58, 86, 64, 49, 79, 68];

/** where the floating dialog was last dragged to; kept across artists while the page lives */
let floatOffset = { x: 0, y: 0 };

export const RecordSheet = memo(function RecordSheet({
  id,
  frameKo,
  frameEn,
  preview,
  highlightOrd,
  stamp,
  partners,
  placement = "side",
  legend,
  onClose,
}: {
  id: string;
  frameKo: string;
  frameEn: string;
  preview: SheetPreview;
  /** W2. ord of the clicked record. Matched only when `stamp` equals the server record's stamp. */
  highlightOrd?: number | null;
  /** W2. Home payload stamp. A server record with a different stamp is shown without a highlight. */
  stamp: string;
  /** other artists the archive shows at that record's venue, when a record was clicked */
  partners?: string[];
  /** "side": a tall card beside the upright disc; its width and slide are written by the study's
   *  frame loop straight onto the element (no React state), so it moves with the canvas.
   *  "float": a small draggable window in the lower left over the horizontal diagram */
  placement?: "side" | "float";
  /** how to read the lines around this artist; shown under the facts */
  legend?: ReactNode;
  onClose: () => void;
}) {
  const floating = placement === "float";
  const [offset, setOffset] = useState(floatOffset);
  const drag = useRef<{ px: number; py: number; ox: number; oy: number; box: DOMRect } | null>(
    null,
  );
  // side: parked just off the right edge until the frame loop slides it in
  const boxStyle: CSSProperties = floating
    ? { transform: `translate3d(${offset.x}px, ${offset.y}px, 0)` }
    : { transform: "translate3d(calc(100% + 60px), 0, 0)" };
  const onDragStart = (e: ReactPointerEvent<HTMLElement>) => {
    if (!floating || (e.target as HTMLElement).closest("button")) return;
    const el = box.current;
    if (!el) return;
    drag.current = {
      px: e.clientX,
      py: e.clientY,
      ox: offset.x,
      oy: offset.y,
      box: el.getBoundingClientRect(),
    };
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    e.preventDefault();
  };
  const onDragMove = (e: ReactPointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (!d) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // keep the whole dialog on screen
    const nx = Math.min(
      Math.max(d.ox + e.clientX - d.px, -(d.box.left - d.ox) + 4),
      vw - d.box.width - (d.box.left - d.ox) - 4,
    );
    const ny = Math.min(
      Math.max(d.oy + e.clientY - d.py, -(d.box.top - d.oy) + 4),
      vh - d.box.height - (d.box.top - d.oy) - 4,
    );
    floatOffset = { x: nx, y: ny };
    setOffset(floatOffset);
  };
  const onDragEnd = () => {
    drag.current = null;
  };
  const { lang, t } = useLang();
  const [rec, setRec] = useState<Record_ | undefined>(undefined);
  const [failed, setFailed] = useState(false);
  // bumped by the Retry button to ask the server again
  const [attempt, setAttempt] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // the sheet is keyed by artist, so its state starts fresh; no reset render needed
    let alive = true;
    getArtistRecord({ data: { id } })
      .then((r) => {
        if (alive) setRec(r);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [id, attempt]);

  // The study's frame loop writes the side sheet's width straight onto this element. React never
  // owns that property, so it survives a switch to "float" (stage 0–1 → 2–4) and stretches the
  // small window to the side sheet's width. Drop it before paint whenever the sheet floats.
  useLayoutEffect(() => {
    if (floating && box.current?.style.width) box.current.style.width = "";
  }, [floating]);

  // Preview rows carry ord from this payload. Once getArtistRecord answers, match that ord only
  // when its stamp is still this payload's (W2): a data update in between opens the sheet with
  // no highlight.
  const markOrd =
    highlightOrd != null && (rec == null || rec.stamp === stamp) ? highlightOrd : null;
  // Centre the marked record inside the list. Re-run when the server record arrives: it adds the
  // bio and roles above the list, which pushes the row out of a short floating window. Scroll only
  // the list itself — scrollIntoView would also scroll the study's overflow-hidden frame.
  useLayoutEffect(() => {
    if (markOrd == null) return;
    const list = listRef.current;
    const el = list?.querySelector<HTMLElement>(`[data-rec="${markOrd}"]`);
    if (!list || !el) return;
    const lr = list.getBoundingClientRect();
    const er = el.getBoundingClientRect();
    list.scrollTop += er.top - lr.top - (lr.height - er.height) / 2;
  }, [markOrd, rec]);

  const artist = rec?.artist ?? preview.artist;
  const acts: SheetPreview["activities"] = rec?.activities ?? preview.activities;
  const links: ArtistLink[] = rec?.links ?? [];
  const loading = rec == null && !failed;
  // what the server record adds (titles, degrees, links) fades in rather than popping in
  const arrive = "motion-safe:animate-in motion-safe:fade-in motion-safe:duration-500";
  // degrees first: the newest at the top, as a CV lists them
  const education = (rec?.background ?? [])
    .filter((b) => b.section === "education")
    .sort((a, b) => b.year - a.year);
  const byYear = new Map<number, SheetPreview["activities"]>();
  for (const a of acts) byYear.set(a.year, [...(byYear.get(a.year) ?? []), a]);
  const years = [...byYear.keys()].sort((a, b) => b - a);
  const domains = new Set(
    acts.flatMap((a) => {
      const d = a.source_url ? host(a.source_url) : a.domain;
      return d ? [d] : [];
    }),
  );
  // what the click landed on: a record (a dot) or the artist (a knot). The card leads with it.
  const picked = markOrd != null ? (acts.find((a) => a.ord === markOrd) ?? null) : null;
  const typeName = (activityType: string) =>
    t(
      ACTIVITY_TYPE_LABEL[activityType]?.[0] ?? activityType,
      ACTIVITY_TYPE_LABEL[activityType]?.[1] ?? activityType,
    );
  // Preview rows have no title yet. Until getArtistRecord arrives, read venue, year and type.
  const cite = (a: SheetActivity) =>
    [a.venue, String(a.year), typeName(a.activity_type)].filter(Boolean).join(" · ");
  const pickedHeading = picked ? (picked.title ?? cite(picked)) : "";
  const name = artist ? (lang === "ko" ? artist.name_ko : artist.name_en) || artist.name_ko : "";
  const otherRaw = artist ? (lang === "ko" ? artist.name_en : artist.name_ko) : "";
  const other = otherRaw && otherRaw !== name ? otherRaw : "";
  const ver = artist ? verLabel(artist.verification) : null;
  const mark =
    "before:absolute before:size-2 before:border-foreground/60 after:absolute after:size-2 after:border-foreground/60";

  return (
    <section
      ref={box}
      role="dialog"
      aria-modal="false"
      aria-label={t("기록 카드", "Record sheet")}
      data-sheet={floating ? "float" : "side"}
      style={boxStyle}
      className={`pointer-events-auto absolute z-20 flex flex-col overflow-hidden border border-foreground/45 bg-background/95 text-foreground shadow-[0_1px_0_rgba(0,0,0,0.04)] backdrop-blur-[2px] inset-x-4 top-[7.5rem] bottom-[7.5rem] ${
        floating
          ? "sm:inset-auto sm:left-[26px] sm:bottom-[4.75rem] sm:h-[min(42vh,28rem)] sm:w-[20rem] sm:shadow-[0_8px_24px_rgba(0,0,0,0.08)]"
          : "sm:inset-auto sm:right-[26px] sm:top-[5.5rem] sm:bottom-[4.75rem] sm:w-[20rem]"
      } ${mark} before:left-1 before:top-1 before:border-l before:border-t after:bottom-1 after:right-1 after:border-b after:border-r`}
    >
      <div className={"contents"}>
        <div className={"contents"}>
          {/* title strip */}
          <header
            onPointerDown={onDragStart}
            onPointerMove={onDragMove}
            onPointerUp={onDragEnd}
            onPointerCancel={onDragEnd}
            className={`relative flex items-start justify-between gap-3 border-b border-foreground/30 px-4 pb-3 pt-3 ${floating ? "cursor-move select-none touch-none" : ""}`}
          >
            {/* while the record loads, a short segment runs along the header's bottom rule */}
            {loading ? (
              <span
                aria-hidden
                className="sheet-sweep pointer-events-none absolute inset-x-0 -bottom-px h-px overflow-hidden"
              >
                <span className="absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-transparent via-primary/80 to-transparent" />
              </span>
            ) : null}
            <div className="min-w-0">
              <p className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground">
                {picked
                  ? t(`기록 / 작가 ${id}`, `Record / artist ${id}`)
                  : t(`작가 / ${id}`, `Artist / ${id}`)}
              </p>
              <h2
                className={`mt-1 text-lg font-medium leading-tight ${picked ? "line-clamp-2" : "truncate"}`}
                title={picked ? pickedHeading : undefined}
              >
                {picked
                  ? pickedHeading
                  : artist
                    ? name
                    : failed
                      ? t("불러오지 못함", "Could not load")
                      : "…"}
              </h2>
              {picked ? (
                <p className="truncate font-mono text-[11px] text-muted-foreground">
                  {name}
                  {other ? ` · ${other}` : ""}
                </p>
              ) : other ? (
                <p className="truncate font-mono text-[11px] text-muted-foreground">{other}</p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={onClose}
              className="grid size-6 shrink-0 place-items-center border border-input font-mono text-[11px] leading-none text-foreground/70 hover:border-primary hover:text-primary"
              aria-label={t("닫기", "Close")}
              title={t("닫기 (Esc)", "Close (Esc)")}
            >
              ×
            </button>
          </header>

          {/* the clicked record, read out in full: the dot itself carries no words */}
          {picked ? (
            <dl className="grid grid-cols-[4.25rem_1fr] gap-x-3 border-b border-foreground/30 px-4 py-2 font-mono text-[10.5px] leading-5">
              <dt className="text-muted-foreground">{t("연도", "year")}</dt>
              <dd className="tabular-nums">{picked.year}</dd>
              <dt className="text-muted-foreground">{t("형식", "type")}</dt>
              <dd>
                {typeName(picked.activity_type)}
                {picked.role ? ` · ${picked.role}` : ""}
              </dd>
              {picked.venue ? (
                <>
                  <dt className="text-muted-foreground">{t("장소", "venue")}</dt>
                  <dd className="min-w-0 break-words">{picked.venue}</dd>
                </>
              ) : null}
              {picked.source_url ? (
                <>
                  <dt className="text-muted-foreground">{t("출처", "source")}</dt>
                  <dd className="min-w-0 truncate">
                    <a
                      href={picked.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="underline decoration-foreground/30 underline-offset-2 hover:text-primary"
                    >
                      {host(picked.source_url)}
                    </a>
                    {picked.collected_at ? (
                      <span className="text-muted-foreground">
                        {" · "}
                        {t("수집", "collected")} {picked.collected_at.slice(0, 10)}
                      </span>
                    ) : null}
                  </dd>
                </>
              ) : null}
              {partners && partners.length ? (
                <>
                  <dt className="text-muted-foreground">{t("함께", "with")}</dt>
                  <dd className="min-w-0 break-words">
                    {partners.slice(0, 6).join(" · ")}
                    {partners.length > 6 ? ` +${partners.length - 6}` : ""}
                  </dd>
                </>
              ) : null}
            </dl>
          ) : null}

          {/* facts */}
          {artist ? (
            <table className="w-full table-fixed border-b border-foreground/30 font-mono text-[10.5px] leading-5">
              {/* fixed columns: a long degree title truncates instead of widening the table */}
              <colgroup>
                <col className="w-[4.25rem]" />
                <col />
                <col className="w-[4.25rem]" />
                <col />
              </colgroup>
              <tbody>
                {/* in the small floating window a picked record already fills the top; the
                    artist's degrees give way so the ledger below stays readable */}
                {education.length && !(floating && picked) ? (
                  <tr className={`border-b border-foreground/15 align-top ${arrive}`}>
                    <td className="border-r border-foreground/15 px-3 py-0.5 text-muted-foreground">
                      {t("학력", "education")}
                    </td>
                    <td className="overflow-hidden px-3 py-0.5" colSpan={3}>
                      <ul>
                        {education.slice(0, 3).map((e) => {
                          const line = [e.title, e.venue].filter(Boolean).join(" · ");
                          return (
                            <li key={e.id} className="flex gap-2" title={line}>
                              <span className="shrink-0 tabular-nums text-muted-foreground">
                                {e.year}
                              </span>
                              <span className="min-w-0 truncate">{line}</span>
                            </li>
                          );
                        })}
                      </ul>
                      {education.length > 3 ? (
                        <span className="text-muted-foreground">+{education.length - 3}</span>
                      ) : null}
                    </td>
                  </tr>
                ) : null}
                <tr className="border-b border-foreground/15">
                  <td className="border-r border-foreground/15 px-3 text-muted-foreground">
                    {t("기록", "records")}
                  </td>
                  <td className="px-3 tabular-nums">{acts.length}</td>
                  <td className="border-l border-r border-foreground/15 px-3 text-muted-foreground">
                    {t("출처", "sources")}
                  </td>
                  <td className="px-3 tabular-nums">{domains.size}</td>
                </tr>
                <tr className="border-b border-foreground/15">
                  <td className="border-r border-foreground/15 px-3 text-muted-foreground">
                    {t("표집틀", "frame")}
                  </td>
                  <td className="px-3" colSpan={3}>
                    {lang === "ko" ? frameKo : frameEn}
                  </td>
                </tr>
                <tr>
                  <td className="border-r border-foreground/15 px-3 text-muted-foreground">
                    {t("상태", "state")}
                  </td>
                  <td className="px-3" colSpan={3}>
                    {ver ? (lang === "ko" ? ver[0] : ver[1]) : artist.verification}
                    {artist.medium_tags.length
                      ? ` · ${artist.medium_tags.slice(0, 3).join(" · ")}`
                      : ""}
                  </td>
                </tr>
              </tbody>
            </table>
          ) : null}
        </div>
        {legend ? <div className="border-b border-foreground/30 px-4 py-2.5">{legend}</div> : null}
        {/* the ledger of records */}
        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {artist && artist.bio_short ? (
            <p className="mb-4 text-[12.5px] leading-6 text-foreground/85">{artist.bio_short}</p>
          ) : null}
          {artist && years.length === 0 ? (
            <p className="font-mono text-[11px] text-muted-foreground">
              {t("기록된 활동이 없습니다.", "No activities recorded.")}
            </p>
          ) : null}
          {years.map((y) => (
            <div
              key={y}
              className="grid grid-cols-[3rem_1fr] gap-2 border-t border-foreground/15 py-3 first:border-t-0 first:pt-0"
            >
              <p className="font-mono text-[11px] text-primary">{y}</p>
              <ul className="space-y-3">
                {(byYear.get(y) ?? []).map((a) => {
                  const hot = markOrd != null && a.ord === markOrd;
                  const recKey = a.ord != null ? `o${a.ord}` : a.id!;
                  return (
                    <li
                      key={recKey}
                      data-rec={a.ord != null ? String(a.ord) : undefined}
                      className={`-mx-1 px-1 ${hot ? "bg-primary/10 outline outline-1 outline-primary/50" : ""}`}
                    >
                      {a.title != null ? (
                        <div className={arrive}>
                          <p className="text-[12.5px] leading-5">
                            {a.title}
                            {a.venue ? (
                              <span className="text-muted-foreground"> — {a.venue}</span>
                            ) : null}
                          </p>
                          <p className="font-mono text-[10px] leading-4 text-muted-foreground">
                            {typeName(a.activity_type)}
                            {a.role ? ` · ${a.role}` : ""}
                            {a.source_url ? (
                              <>
                                {" · "}
                                <a
                                  href={a.source_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="underline decoration-foreground/30 underline-offset-2 hover:text-primary"
                                >
                                  {host(a.source_url)}
                                </a>
                              </>
                            ) : null}
                            {a.collected_at
                              ? ` · ${t("수집", "collected")} ${a.collected_at.slice(0, 10)}`
                              : ""}
                          </p>
                        </div>
                      ) : loading ? (
                        // a placeholder where the title will stand (widths vary so the column
                        // reads like text), with what the preview already knows beneath it
                        <>
                          <p className="flex h-5 items-center" aria-hidden>
                            <span
                              className="h-2 bg-foreground/[0.09] motion-safe:animate-pulse"
                              style={{ width: `${BAR_WIDTHS[(a.ord ?? 0) % BAR_WIDTHS.length]}%` }}
                            />
                          </p>
                          <p className="font-mono text-[10px] leading-4 text-muted-foreground">
                            {/* the year already heads this group */}
                            {[a.venue, typeName(a.activity_type)].filter(Boolean).join(" · ")}
                          </p>
                        </>
                      ) : (
                        <p className="text-[12.5px] leading-5">{cite(a)}</p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
          {links.length > 0 ? (
            <div className={`mt-3 border-t ${arrive} border-foreground/15 pt-3`}>
              <p className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground">
                {t("링크", "Links")}
              </p>
              <ul className="mt-1 space-y-1 font-mono text-[10.5px]">
                {links.map((l) => (
                  <li key={l.id} className={l.is_dead ? "text-muted-foreground line-through" : ""}>
                    <a
                      href={l.url}
                      target="_blank"
                      rel="noreferrer"
                      className="underline decoration-foreground/30 underline-offset-2 hover:text-primary"
                    >
                      {l.label || host(l.url)}
                    </a>{" "}
                    <span className="text-muted-foreground">
                      {t(
                        LINK_TYPE_LABEL[l.link_type]?.[0] ?? l.link_type,
                        LINK_TYPE_LABEL[l.link_type]?.[1] ?? l.link_type,
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </div>
      {/* footer */}
      <footer className="flex items-center justify-between gap-3 border-t border-foreground/30 px-4 py-2 font-mono text-[10.5px]">
        <Link
          to="/artist/$id"
          params={{ id }}
          className="underline decoration-foreground/30 underline-offset-2 hover:text-primary"
        >
          {t("전체 기록 페이지 →", "Full record →")}
        </Link>
        {loading ? (
          <span
            role="status"
            className="text-muted-foreground motion-safe:animate-in motion-safe:fade-in motion-safe:delay-300 motion-safe:fill-mode-backwards"
          >
            {t("기록 불러오는 중", "Loading records")}
          </span>
        ) : failed ? (
          <span className="flex items-center gap-2 text-muted-foreground">
            {t("불러오지 못함", "Could not load")}
            <button
              type="button"
              onClick={() => {
                setFailed(false);
                setAttempt((n) => n + 1);
              }}
              className="border border-input px-1.5 leading-5 text-foreground/80 hover:border-primary hover:text-primary"
            >
              {t("다시 시도", "Retry")}
            </button>
          </span>
        ) : null}
      </footer>
    </section>
  );
});
