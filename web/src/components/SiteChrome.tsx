// SPDX-License-Identifier: AGPL-3.0-only
import { Link, useRouterState } from "@tanstack/react-router";
import { archiveSentenceEn, archiveSentenceKo, contactHref, site } from "@/config/site";
import { useLang } from "@/lib/i18n";

export const SITE_NAV = [
  { to: "/artists", ko: "목록", en: "List" },
  { to: "/data", ko: "데이터", en: "Data" },
  { to: "/about/criteria", ko: "등재 기준", en: "Criteria" },
  { to: "/about/frame", ko: "표집틀", en: "Frame" },
  { to: "/about/governance", ko: "운영 원칙", en: "Governance" },
  { to: "/research", ko: "연구", en: "Research" },
  { to: "/request", ko: "요청", en: "Request" },
] as const;

/** Small floating way back home on every page that is not the home itself. */
export function HomeReturn() {
  const { t } = useLang();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  if (pathname === "/") return null;
  return (
    <Link
      to="/"
      className="fixed left-4 top-4 z-50 border border-border bg-background/85 px-3 py-1.5 font-mono text-[11px] text-foreground no-underline backdrop-blur-sm transition-colors hover:border-primary hover:text-primary"
    >
      ← {t("홈", "Home")}
    </Link>
  );
}

export function SiteFooter() {
  const { lang, setLang, t } = useLang();
  return (
    <footer className="mt-28 border-t border-border py-10 text-xs text-muted-foreground">
      <div className="wrap grid gap-6 md:grid-cols-[13rem_1fr] md:gap-12">
        <div>
          <p className="font-display text-base font-medium text-foreground">기예 Giye / 技藝</p>
          <button
            type="button"
            onClick={() => setLang(lang === "ko" ? "en" : "ko")}
            className="mt-3 border border-input px-2.5 py-1 font-mono text-[11px] text-foreground transition-colors hover:border-primary hover:text-primary"
            aria-label={t("언어 전환", "Switch language")}
          >
            {lang === "ko" ? "EN" : "한국어"}
          </button>
        </div>
        <div className="space-y-4">
          <nav
            aria-label={t("사이트 메뉴", "Site navigation")}
            className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-[11px]"
          >
            <Link to="/" className="text-foreground no-underline hover:text-primary">
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
            {archiveSentenceKo()} 어떠한 공공기관이나 지원사업의 공식 프로젝트가 아닙니다.
          </p>
          <p className="max-w-3xl">
            {archiveSentenceEn()} It is not an official project of any public institution or
            programme.
          </p>
          <p>
            {t("자동 수집·대량 복제 금지", "No automated collection or bulk copying")}
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
