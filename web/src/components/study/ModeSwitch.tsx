// SPDX-License-Identifier: AGPL-3.0-only
import { Link } from "@tanstack/react-router";
import { useLang } from "@/lib/i18n";

export type HomeMode = "search" | "network";

/** The home's two readings of the archive: records on growth rings, or who shared an event. */
export function ModeSwitch({ mode }: { mode: HomeMode }) {
  const { t } = useLang();
  const item =
    "px-2 py-0.5 no-underline transition hover:text-primary aria-[current=page]:bg-foreground aria-[current=page]:text-background aria-[current=page]:hover:text-background";
  return (
    <nav
      aria-label={t("보기 방식", "View mode")}
      className="flex border border-input font-mono text-[10px] leading-4 text-foreground/75"
    >
      <Link
        to="/"
        search={{}}
        aria-current={mode === "search" ? "page" : undefined}
        className={item}
      >
        {t("검색", "Search")}
      </Link>
      <Link
        to="/"
        search={{ mode: "network" }}
        aria-current={mode === "network" ? "page" : undefined}
        className={`${item} border-l border-input`}
      >
        {t("네트워크", "Network")}
      </Link>
    </nav>
  );
}
