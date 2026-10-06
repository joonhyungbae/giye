// SPDX-License-Identifier: AGPL-3.0-only
import { useState } from "react";
import { useLang } from "@/lib/i18n";
import { Button } from "@/components/ui/button";

export type CiteInput = {
  title: string;
  id?: string;
  /** From citations.json (loadCitationMeta): [publish] citation_author. */
  author: string;
  version: string;
  /** released_at of the cited dataset_versions.json row; part of the version label. */
  released: string | null;
  url: string;
  year: number;
};

/** The same sentences as giye.publish.cite.citation_texts. */
function formats({ title, id, author, version, released, url, year }: CiteInput, accessed: string) {
  const label = released ? `v${version} of ${released}` : `v${version}`;
  const bracket = id ? `[Artist record ${id}, Dataset ${label}]` : `[Dataset ${label}]`;
  return {
    APA: `${author}. (${year}). ${title} ${bracket}. Retrieved ${accessed}, from ${url}`,
    Chicago: `${author}. "${title}." ${bracket} ${year}. Accessed ${accessed}. ${url}.`,
    BibTeX: `@misc{giye_${(id ?? "dataset").replace(/-/g, "_")},
  author       = {{${author}}},
  title        = {${title}},
  note         = {${bracket.slice(1, -1)}},
  year         = {${year}},
  howpublished = {\\url{${url}}},
  urldate      = {${accessed}}
}`,
  };
}

export function CiteDialog(props: CiteInput) {
  const [open, setOpen] = useState(false);
  const { t } = useLang();
  const accessed = new Date().toISOString().slice(0, 10);
  const all = formats(props, accessed);

  return (
    <>
      <Button
        type="button"
        onClick={() => setOpen(true)}
        variant="outline"
        className="rounded-none border-input bg-transparent px-4 shadow-none hover:border-primary hover:bg-transparent hover:text-primary"
      >
        {t("인용하기", "Cite")}
      </Button>
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={t("인용하기", "Cite")}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-foreground/40 p-4"
          onClick={() => setOpen(false)}
        >
          <div
            className="mt-16 w-full max-w-2xl border border-border bg-card p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-baseline justify-between">
              <h2 className="text-xl">{t("인용하기", "Cite")} / Cite</h2>
              <Button variant="ghost" size="sm" onClick={() => setOpen(false)} className="rounded-none text-muted-foreground hover:bg-transparent hover:text-primary">
                {t("닫기", "Close")}
              </Button>
            </div>
            <div className="mt-4 space-y-5">
              {Object.entries(all).map(([name, text]) => (
                <div key={name}>
                  <div className="flex items-center justify-between">
                    <span className="label-caps">{name}</span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => navigator.clipboard.writeText(text)}
                      className="h-7 rounded-none border-input bg-transparent px-2 text-xs shadow-none hover:border-primary hover:bg-transparent hover:text-primary"
                    >
                      {t("복사", "Copy")}
                    </Button>
                  </div>
                  <pre className="mt-1 overflow-x-auto whitespace-pre-wrap border border-border bg-background p-3 font-mono text-xs">
                    {text}
                  </pre>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
