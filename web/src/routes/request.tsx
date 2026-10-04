// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { useState } from "react";
import { contactHref, site } from "@/config/site";
import { submitRequest } from "@/lib/giye.functions";
import { REQUEST_TYPE_LABEL, useLang } from "@/lib/i18n";

const TYPES = ["add", "correct", "hide", "self", "same"] as const;
type Kind = (typeof TYPES)[number];
type Search = { type?: Kind; artist?: string; other?: string };

export const Route = createFileRoute("/request")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    type: TYPES.includes(s.type as Kind) ? (s.type as Kind) : undefined,
    artist: typeof s.artist === "string" && s.artist ? s.artist : undefined,
    other: typeof s.other === "string" && s.other ? s.other : undefined,
  }),
  head: () => ({
    meta: [
      { title: "GIYE" },
      {
        name: "description",
        content:
          "등재 제안, 수정, 비공개 요청을 보냅니다. 계정은 필요하지 않습니다. Submit an addition, correction or hide request. No account needed.",
      },
      { property: "og:title", content: "요청 Request — 기예 Giye" },
      { property: "og:description", content: "Submit a request to the Giye archive." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { property: "og:url", content: "/request" },
    ],
    links: [{ rel: "canonical", href: "/request" }],
  }),
  component: RequestPage,
});

function RequestPage() {
  const search = Route.useSearch();
  const { t } = useLang();
  const [kind, setKind] = useState<Kind>(search.type ?? "add");
  const report = kind === "self" || kind === "same";
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (sent) {
    return (
      <div className="wrap max-w-2xl py-20">
        <h1 className="text-2xl">{t("접수되었습니다", "Request received")}</h1>
        <p className="mt-4 leading-8">
          {kind === "add"
            ? t(
                "관리자가 검토한 뒤 등재 여부를 결정합니다.",
                "Your submission will be reviewed before publication.",
              )
            : report
              ? t(
                  "알려 주신 주소에서 CV를 읽어 확인합니다. 다음 주간 갱신 때 기록에 반영됩니다. 제보만으로 기록을 바꾸지는 않고, 그 주소의 CV가 근거가 됩니다.",
                  "We read the CV at the address you gave and check it; the record changes with the next weekly update. A report alone does not change a record — the CV at that address is the evidence.",
                )
              : t(
                  "30일 내에 입력하신 이메일로 답변드립니다.",
                  "You will receive a reply within 30 days.",
                )}
        </p>
      </div>
    );
  }

  return (
    <div className="wrap max-w-2xl py-12">
      <h1 className="text-3xl">{t("요청", "Request")}</h1>
      <p className="mt-3 text-muted-foreground">
        {t(
          "계정 없이 보낼 수 있습니다. 30일 내 답변합니다. 문의: ",
          "No account needed. We reply within 30 days. Contact: ",
        )}
        <a
          className="underline decoration-input underline-offset-4 hover:decoration-primary"
          href={contactHref()}
        >
          {site.contactEmail}
        </a>
      </p>
      <form
        className="mt-8 space-y-5"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          const fd = new FormData(e.currentTarget);
          try {
            if (
              kind === "self" &&
              !fd.get("cv_url") &&
              !fd.get("website_url") &&
              !fd.get("sns_url")
            ) {
              setError(
                t(
                  "CV나 웹사이트 주소를 하나 이상 입력해 주세요.",
                  "Enter at least one CV or website address.",
                ),
              );
              return;
            }
            if (
              kind === "add" &&
              ((!fd.get("name_ko")?.toString().trim() && !fd.get("name_en")?.toString().trim()) ||
                (!fd.get("sns_url") && !fd.get("website_url") && !fd.get("cv_url")))
            ) {
              setError(
                t(
                  "이름을 한 언어 이상, 출처 링크를 하나 이상 입력해 주세요.",
                  "Enter a name in at least one language and at least one source link.",
                ),
              );
              return;
            }
            await submitRequest({
              data: {
                request_type: String(fd.get("request_type")) as Kind,
                artist_id: String(fd.get("artist_id") || "") || null,
                other_id: String(fd.get("other_id") || "") || null,
                requester_email: String(fd.get("requester_email") || ""),
                name_ko: String(fd.get("name_ko") || ""),
                name_en: String(fd.get("name_en") || ""),
                sns_url: String(fd.get("sns_url") || ""),
                website_url: String(fd.get("website_url") || ""),
                cv_url: String(fd.get("cv_url") || ""),
                message: String(fd.get("message") || ""),
              },
            });
            setSent(true);
          } catch (err) {
            setError(
              t(
                "전송에 실패했습니다. 잠시 후 다시 시도해 주세요.",
                "Could not send. Please try again.",
              ),
            );
            console.error(err);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div>
          <label htmlFor="request_type" className="label-caps">
            {t("요청 종류", "Request type")}
          </label>
          <select
            id="request_type"
            name="request_type"
            value={kind}
            onChange={(e) => setKind(e.target.value as Kind)}
            className="mt-2 w-full border border-border bg-card px-2 py-2"
          >
            {Object.entries(REQUEST_TYPE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {t(v[0], v[1])}
              </option>
            ))}
          </select>
        </div>
        {kind === "add" ? (
          <fieldset className="space-y-5">
            <legend className="mb-4 text-sm text-muted-foreground">
              {t(
                "이름은 한글·영문 중 하나 이상, 링크는 하나 이상 입력해 주세요.",
                "Provide a Korean or English name (or both) and at least one link.",
              )}
            </legend>
            {[
              ["name_ko", "이름 (한글)", "Name (Korean)"],
              ["name_en", "이름 (영문)", "Name (English)"],
              ["sns_url", "SNS URL", "SNS URL"],
              ["website_url", "웹사이트 URL", "Website URL"],
              ["cv_url", "CV URL", "CV URL"],
            ].map(([name, ko, en]) => (
              <div key={name}>
                <label htmlFor={name} className="label-caps">
                  {t(ko, en)}
                </label>
                <input
                  id={name}
                  name={name}
                  type={name.endsWith("_url") ? "url" : "text"}
                  className="mt-2 w-full border border-border bg-card px-2 py-2"
                />
              </div>
            ))}
          </fieldset>
        ) : report ? (
          <fieldset className="space-y-5">
            <legend className="mb-4 text-sm text-muted-foreground">
              {kind === "self"
                ? t(
                    "CV를 공개해 둔 주소를 알려 주세요(웹사이트의 CV 페이지, PDF, 링크 공유된 구글 드라이브·문서). 그 CV가 이 기록의 근거가 됩니다.",
                    "Tell us where your CV is published (a CV page on your site, a PDF, a shared Google Drive file or Doc). That CV becomes the source for this record.",
                  )
                : t(
                    "두 기록이 한 사람이라면 알려 주세요. 두 기록에 나오는 행사가 함께 적힌 CV나 웹사이트 주소가 있으면 그것이 근거가 되어 바로 합칠 수 있습니다.",
                    "Tell us if two records are one person. A CV or website that lists the events of both records is the evidence that lets us join them.",
                  )}
            </legend>
            {(
              [
                ["artist_id", "작가 ID", "Artist ID", search.artist],
                ...(kind === "same"
                  ? [["other_id", "같은 사람의 다른 작가 ID", "The other artist ID", search.other]]
                  : []),
              ] as [string, string, string, string | undefined][]
            ).map(([name, ko, en, value]) => (
              <div key={name}>
                <label htmlFor={name} className="label-caps">
                  {t(ko, en)}
                </label>
                <input
                  id={name}
                  name={name}
                  required
                  defaultValue={value ?? ""}
                  placeholder="GY-000000"
                  className="mt-2 w-full border border-border bg-card px-2 py-2 font-mono"
                />
              </div>
            ))}
            {[
              ["cv_url", "CV 주소", "CV URL"],
              ["website_url", "웹사이트 주소", "Website URL"],
              ["sns_url", "SNS 주소", "SNS URL"],
              ["requester_email", "이메일 (선택, 확인이 필요할 때만 연락)", "Email (optional, only if we need to ask)"],
              ["message", "메모 (선택)", "Note (optional)"],
            ].map(([name, ko, en]) => (
              <div key={name}>
                <label htmlFor={name} className="label-caps">
                  {t(ko, en)}
                </label>
                <input
                  id={name}
                  name={name}
                  type={name.endsWith("_url") ? "url" : name === "requester_email" ? "email" : "text"}
                  className="mt-2 w-full border border-border bg-card px-2 py-2"
                />
              </div>
            ))}
          </fieldset>
        ) : (
          <>
            <div>
              <label htmlFor="artist_id" className="label-caps">
                {t("작가 ID (선택)", "Artist ID (optional)")}
              </label>
              <input
                id="artist_id"
                name="artist_id"
                defaultValue={search.artist ?? ""}
                placeholder="GY-000000"
                className="mt-2 w-full border border-border bg-card px-2 py-2 font-mono"
              />
            </div>
            <div>
              <label htmlFor="requester_email" className="label-caps">
                {t("이메일", "Email")}
              </label>
              <input
                id="requester_email"
                name="requester_email"
                type="email"
                required
                className="mt-2 w-full border border-border bg-card px-2 py-2"
              />
            </div>
            <div>
              <label htmlFor="message" className="label-caps">
                {t("내용", "Message")}
              </label>
              <textarea
                id="message"
                name="message"
                required
                rows={7}
                className="mt-2 w-full border border-border bg-card px-2 py-2"
              />
            </div>
          </>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button
          type="submit"
          disabled={busy}
          className="border border-border bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50"
        >
          {busy ? t("전송 중…", "Sending…") : t("보내기", "Send")}
        </Button>
      </form>
    </div>
  );
}
