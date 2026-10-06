// SPDX-License-Identifier: AGPL-3.0-only
import { Link, useRouterState } from "@tanstack/react-router";
import {
  archiveSentenceEn,
  archiveSentenceKo,
  contactHref,
  site,
} from "@/config/site";
import { useLang } from "@/lib/i18n";

// /data holds the citation, versions and counts; no dataset is downloadable, so it is labelled
// Citation. The four documents on how the archive is built share one entry (About) and a
// sub-navigation (AboutNav). /research is not listed: no published work cites Giye data yet.
export const SITE_NAV = [
  { to: "/artists", ko: "목록", en: "List" },
  { to: "/about/methodology", ko: "소개", en: "About" },
  { to: "/data", ko: "인용", en: "Citation" },
  { to: "/request", ko: "요청", en: "Request" },
] as const;

const ABOUT_PAGES = [
  { to: "/about/methodology", ko: "구축 방법론", en: "Methodology" },
  { to: "/about/criteria", ko: "등재 기준", en: "Criteria" },
  { to: "/about/frame", ko: "표집틀", en: "Sampling frame" },
  { to: "/about/governance", ko: "운영 원칙", en: "Governance" },
] as const;

/** Tabs across the About documents; the current page is marked and not a link. */
export function AboutNav() {
  const { t } = useLang();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  return (
    <nav
      aria-label={t("소개 문서", "About pages")}
      className="wrap max-w-3xl flex flex-wrap gap-x-5 gap-y-2 pt-10 font-mono text-[11px]"
    >
      {ABOUT_PAGES.map((p) =>
        pathname === p.to ? (
          <span key={p.to} aria-current="page" className="text-primary">
            {t(p.ko, p.en)}
          </span>
        ) : (
          <Link
            key={p.to}
            to={p.to}
            className="text-muted-foreground no-underline hover:text-primary"
          >
            {t(p.ko, p.en)}
          </Link>
        ),
      )}
    </nav>
  );
}

/**
 * Way back home at the top left of every page that is not the home itself. It sits at the top
 * of the page and scrolls away with it, so it never covers the header or a record while reading.
 */
export function HomeReturn() {
  const { t } = useLang();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  if (pathname === "/") return null;
  return (
    <Link
      to="/"
      className="absolute left-4 top-4 z-50 border border-border bg-background/85 px-3 py-1.5 font-mono text-[11px] text-foreground no-underline backdrop-blur-sm transition-colors hover:border-primary hover:text-primary"
    >
      ← {t("홈", "Home")}
    </Link>
  );
}

/**
 * The language switch, at the top right of every page. Choosing writes a cookie, so the server
 * renders the next page in that language (see lib/lang-cookie.ts). It scrolls away with the page
 * like the home link. The home draws its own switch at the top right of the study.
 */
export function LangToggle() {
  const { lang, setLang, t } = useLang();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  if (pathname === "/") return null;
  return (
    <button
      type="button"
      onClick={() => setLang(lang === "ko" ? "en" : "ko")}
      className="absolute right-4 top-4 z-50 border border-border bg-background/85 px-3 py-1.5 font-mono text-[11px] text-foreground backdrop-blur-sm transition-colors hover:border-primary hover:text-primary"
      aria-label={t("언어 전환: English", "Switch language: 한국어")}
      lang={lang === "ko" ? "en" : "ko"}
    >
      {lang === "ko" ? "EN" : "한국어"}
    </button>
  );
}

export function SiteFooter() {
  const { t } = useLang();
  return (
    <footer className="mt-28 border-t border-border py-10 text-xs text-muted-foreground">
      <div className="wrap grid gap-6 md:grid-cols-[13rem_1fr] md:gap-12">
        <div>
          <p className="font-display text-base font-medium text-foreground">
            기예 Giye / 技藝
          </p>
        </div>
        <div className="space-y-4">
          <nav
            aria-label={t("사이트 메뉴", "Site navigation")}
            className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-[11px]"
          >
            <Link
              to="/"
              className="text-foreground no-underline hover:text-primary"
            >
              {t("홈", "Home")}
            </Link>
            {SITE_NAV.map((n) => (
              <Link
                key={n.to}
                to={n.to}
                className="text-foreground no-underline hover:text-primary"
              >
                {t(n.ko, n.en)}
              </Link>
            ))}
          </nav>
          {/* The statement of what Giye is stays in both languages whatever the page language. */}
          <p className="max-w-3xl">
            {archiveSentenceKo()} 어떠한 공공기관이나 지원사업의 공식 프로젝트가
            아닙니다.
          </p>
          <p className="max-w-3xl">
            {archiveSentenceEn()} It is not an official project of any public
            institution or programme.
          </p>
          <p>
            {t(
              "자동 수집·대량 복제 금지",
              "No automated collection or bulk copying",
            )}
            {" · "}
            <a href={contactHref()} className="text-accent">
              {site.contactEmail}
            </a>
            {" · "}
            <Link to="/privacy" className="text-accent">
              {t("개인정보 처리방침", "Privacy")}
            </Link>
            {" · "}
            <Link to="/about/methodology" className="text-accent">
              {t("구축 방법론", "Methodology")}
            </Link>
          </p>
        </div>
      </div>
    </footer>
  );
}
