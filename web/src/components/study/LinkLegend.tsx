// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The link legend: what each line around a chosen artist means, in the artist's own numbers.
 * Hovering a row lifts that kind of line on the canvas; partner names open their sheets.
 */
import { memo, type ReactNode } from "react";

export type LegendKey = "strand" | "chords" | "knot" | "sources" | "plates" | null;

export type LinkStats = {
  name: string;
  records: number;
  y0: number | null;
  y1: number | null;
  frame: string;
  chords: number;
  partners: Array<{ i: number; name: string; shared: number }>;
  venues: string[];
  domains: string[];
};

const ink = "currentColor";

function Glyph({ kind }: { kind: Exclude<LegendKey, null> }) {
  const common = { width: 28, height: 14, viewBox: "0 0 28 14", "aria-hidden": true } as const;
  switch (kind) {
    case "strand":
      return (
        <svg {...common}>
          <line x1="2" y1="7" x2="26" y2="7" stroke={ink} strokeWidth="1" />
          {[6, 12, 18].map((x) => (
            <circle key={x} cx={x} cy="7" r="1.8" fill={ink} />
          ))}
        </svg>
      );
    case "chords":
      return (
        <svg {...common}>
          <circle cx="3" cy="11" r="1.8" fill={ink} />
          <path d="M3 11 Q14 -3 25 11" fill="none" stroke={ink} strokeWidth="1" />
          <circle cx="25" cy="11" r="2.6" fill="none" stroke={ink} strokeWidth="1" />
        </svg>
      );
    case "knot":
      return (
        <svg {...common}>
          <path
            d="M2 12 A 24 24 0 0 1 26 12"
            fill="none"
            stroke={ink}
            strokeWidth="0.8"
            opacity="0.5"
          />
          <circle cx="14" cy="5" r="2.8" fill="none" stroke={ink} strokeWidth="1" />
        </svg>
      );
    case "sources":
      return (
        <svg {...common}>
          <ellipse cx="14" cy="11" rx="7" ry="2.4" fill="none" stroke={ink} strokeWidth="1" />
          <line x1="8" y1="2" x2="12" y2="10" stroke={ink} strokeWidth="0.8" />
          <line x1="20" y1="2" x2="16" y2="10" stroke={ink} strokeWidth="0.8" />
        </svg>
      );
    case "plates":
      return (
        <svg {...common}>
          <path d="M3 10 Q14 3 25 10" fill="none" stroke={ink} strokeWidth="3" opacity="0.35" />
        </svg>
      );
  }
}

export const LinkLegend = memo(function LinkLegend({
  stats,
  stage,
  t,
  onFocus,
  onOpen,
}: {
  stats: LinkStats;
  stage: number;
  t: (ko: string, en: string) => string;
  onFocus: (k: LegendKey) => void;
  onOpen: (artist: number) => void;
}) {
  const span =
    stats.y0 == null ? "" : stats.y0 === stats.y1 ? `${stats.y0}` : `${stats.y0}–${stats.y1}`;
  const rows: Array<{
    k: Exclude<LegendKey, null>;
    title: string;
    body: string;
    extra?: ReactNode;
  }> = [
    {
      k: "strand",
      title: t("가닥", "Strand"),
      body: t(
        `이 작가의 기록 ${stats.records}건${span ? ` · ${span}` : ""}. 안쪽 고리가 오래된 해, 바깥이 최근입니다.`,
        `${stats.records} records by this artist${span ? ` · ${span}` : ""}. Inner rings are older years.`,
      ),
    },
    {
      k: "chords",
      title: t("같은 장소", "Same venue"),
      body:
        stats.chords > 0
          ? t(
              `곡선 ${stats.chords}개. 다른 작가 ${stats.partners.length}명이 같은 장소에서 활동한 기록과 이어집니다.`,
              `${stats.chords} curves to ${stats.partners.length} other artists who worked at the same venue.`,
            )
          : t(
              "같은 장소를 공유한 다른 작가의 기록이 아직 없습니다.",
              "No other artist in the archive shares a venue yet.",
            ),
      extra:
        stats.chords > 0 ? (
          <>
            {stats.venues.length ? (
              <span className="block text-muted-foreground">{stats.venues.join(" · ")}</span>
            ) : null}
            <span className="mt-1 flex flex-wrap gap-1">
              {stats.partners.slice(0, 6).map((p) => (
                <button
                  key={p.i}
                  type="button"
                  onClick={() => onOpen(p.i)}
                  className="border border-primary/40 px-1 leading-4 text-primary hover:bg-primary hover:text-primary-foreground"
                  title={t(`같은 장소 ${p.shared}건`, `${p.shared} shared`)}
                >
                  {p.name}
                </button>
              ))}
              {stats.partners.length > 6 ? (
                <span className="text-muted-foreground">+{stats.partners.length - 6}</span>
              ) : null}
            </span>
          </>
        ) : undefined,
    },
    {
      k: "knot",
      title: t("매듭", "Knot"),
      body: t(
        `테두리 위 이 작가의 자리. 테두리는 진입 세대(처음 명단에 오른 해를 5년 단위로 묶은 것)별로 나뉘고, 12시 방향부터 시계 방향으로 오래된 세대가 먼저 옵니다. 세대 안에서는 진입 연도 순입니다. 이웃은 같은 시기에 들어왔을 뿐 작업이 닮았다는 뜻이 아닙니다 · ${stats.frame}`,
        `This artist's place on the rim: arcs are entry generations (the year of their first roster, in 5-year bins), oldest first, clockwise from 12 o'clock; inside an arc, by entry year. Neighbours entered at the same time, which says nothing about their work · ${stats.frame}`,
      ),
    },
  ];
  if (stage >= 1) {
    rows.push(
      {
        k: "sources",
        title: t("출처 기둥", "Source piers"),
        body: t(
          `기록이 인용한 출처 ${stats.domains.length}곳${stats.domains.length ? ` · ${stats.domains.slice(0, 2).join(", ")}` : ""}`,
          `${stats.domains.length} sources cited${stats.domains.length ? ` · ${stats.domains.slice(0, 2).join(", ")}` : ""}`,
        ),
      },
      {
        k: "plates",
        title: t("진입 세대 판", "Generation plates"),
        body: t(
          `이 작가가 속한 진입 세대 · ${stats.frame}`,
          `The artist's entry generation · ${stats.frame}`,
        ),
      },
    );
  }
  return (
    <div
      className="font-mono text-[10.5px] leading-[1.45] text-foreground/85"
      onMouseLeave={() => onFocus(null)}
    >
      <p className="mb-1 border-b border-foreground/30 pb-1 text-[9.5px] uppercase tracking-[0.16em] text-muted-foreground">
        {t("연결 읽기", "Reading the links")} · {stats.name}
      </p>
      <ul className="space-y-1.5">
        {rows.map((r) => (
          <li
            key={r.k}
            onMouseEnter={() => onFocus(r.k)}
            className="grid grid-cols-[28px_1fr] gap-2 rounded-none px-1 py-0.5 hover:bg-primary/5"
          >
            <span className={`pt-0.5 ${r.k === "chords" ? "text-primary" : "text-foreground/70"}`}>
              <Glyph kind={r.k} />
            </span>
            <span>
              <span className="font-semibold">{r.title}</span>{" "}
              <span className="text-foreground/75">{r.body}</span>
              {r.extra}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
});
