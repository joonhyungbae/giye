// SPDX-License-Identifier: AGPL-3.0-only
import { useLang } from "@/lib/i18n";

/**
 * Content pages store Markdown-like text. Blank lines are paragraphs. A single
 * line break stays inside the paragraph. The snapshot files store the two
 * characters "\" and "n" rather than a newline, so those are turned into
 * newlines first. The text is rendered as text nodes, so a raw HTML tag in
 * the body cannot run.
 */
function contentParagraphs(body: string): string[] {
  const text = body.replace(/\\r\\n/g, "\n").replace(/\\n/g, "\n").replace(/\r\n/g, "\n");
  return text
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter((block) => block.length > 0);
}

export type ContentPageData = {
  page: {
    slug: string;
    title_ko: string;
    title_en: string | null;
    body_ko: string;
    body_en: string;
    updated_at: string;
  } | null;
  revisions: { id: string; note_ko: string; note_en: string | null; revised_at: string }[];
};

export function ContentPageView({ page, revisions }: ContentPageData) {
  const { lang, t } = useLang();
  if (!page) {
    return (
      <div className="wrap max-w-3xl py-20">
        <p className="text-muted-foreground">
          {t("아직 작성되지 않은 문서입니다.", "This page has not been written yet.")}
        </p>
      </div>
    );
  }
  const body = lang === "ko" ? page.body_ko : page.body_en || page.body_ko;
  const blocks = contentParagraphs(body);
  return (
    <div className="wrap max-w-3xl py-12">
      <h1 className="text-3xl">{lang === "ko" ? page.title_ko : (page.title_en ?? page.title_ko)}</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        {t("최종 수정", "Last updated")} {page.updated_at.slice(0, 10)}
      </p>
      <div className="mt-8 space-y-4 leading-8">
        {blocks.map((block, i) => (
          <p key={i}>
            {block.split("\n").map((line, j) => (
              <span key={j}>
                {j > 0 ? <br /> : null}
                {line}
              </span>
            ))}
          </p>
        ))}
      </div>
      {revisions.length > 0 && (
        <section className="mt-12">
          <h2 className="border-b border-border pb-2 text-xl">{t("개정 이력", "Change log")}</h2>
          <ul className="mt-4 space-y-2 text-sm">
            {revisions.map((r) => (
              <li key={r.id}>
                <span className="font-mono text-muted-foreground">{r.revised_at}</span>{" "}
                {lang === "ko" ? r.note_ko : (r.note_en ?? r.note_ko)}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
