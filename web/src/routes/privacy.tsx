// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute, Link } from "@tanstack/react-router";
import { contactHref, site } from "@/config/site";
import { useLang } from "@/lib/i18n";

export const Route = createFileRoute("/privacy")({
  head: () => ({
    meta: [
      { title: "GIYE" },
      {
        name: "description",
        content:
          "기예가 읽는 공개 기록, 페이지에 나오는 항목, 하지 않는 일, 수정·비공개 요청. What Giye reads, what a page shows, what it does not do, and how to ask for a correction or to hide a record.",
      },
      { property: "og:title", content: "개인정보 처리방침 Privacy — 기예 Giye" },
      { property: "og:description", content: "Privacy policy of the Giye archive." },
      { property: "og:url", content: "/privacy" },
    ],
    links: [{ rel: "canonical", href: "/privacy" }],
  }),
  component: PrivacyPage,
});

function PrivacyPage() {
  const { t } = useLang();
  return (
    <div className="wrap max-w-3xl py-12">
      <h1 className="text-3xl">{t("개인정보 처리방침", "Privacy")}</h1>
      <div className="mt-8 space-y-8 leading-8">
        <section>
          <h2 className="text-xl">{t("수집", "What is collected")}</h2>
          <p className="mt-3">
            {t(
              "기예는 공개된 프로그램 명단과 공개된 CV 페이지만 읽습니다. 수집은 그 호스트의 robots.txt를 따르고, 이용약관이 자동 수집을 금지하는 플랫폼은 건너뜁니다.",
              "Giye reads public programme rosters and public CV pages only. Collection follows that host's robots.txt, and skips platforms whose terms forbid automated collection.",
            )}
          </p>
        </section>
        <section>
          <h2 className="text-xl">{t("표시", "What is shown")}</h2>
          <p className="mt-3">
            {t(
              "공개된 기록에는 출처가 붙습니다. CV의 학력과 경력은 그 사람의 페이지에 나옵니다. 같은 CV의 강의·교육과 언론 항목도 그 페이지에 나옵니다.",
              "A published record carries its source. Education and employment entries from a CV are shown on that person's page, as are teaching and press lines from the same CV.",
            )}
          </p>
        </section>
        <section>
          <h2 className="text-xl">{t("하지 않는 일", "What is never done")}</h2>
          <p className="mt-3">
            {t(
              "데이터셋을 내려받거나 대량으로 내보내는 기능은 없고, 공개 API도 없습니다. 여러 사람을 아우르는 응답에는 식별자, 연도, 유형 코드로 된 구조와 이름만 담깁니다. 제목과 출처 URL은 한 사람씩 제공합니다.",
              "There is no dataset download, no bulk export, and no public API. A response that covers many people carries only coded structure — identifiers, years and type codes — and names. A title and its source URL are served one person at a time.",
            )}
          </p>
        </section>
        <section>
          <h2 className="text-xl">{t("수정과 비공개", "Correction and hiding")}</h2>
          <p className="mt-3">
            {t("수정과 비공개는 ", "A correction, or a request to hide a record, is sent through ")}
            <Link to="/request" className="text-accent">
              /request
            </Link>
            {t(
              "로 보냅니다. 비공개된 기록은 이름 없는 표시로 그 주소에 남습니다. 합쳐져 폐기된 식별자는 남은 식별자로 리다이렉트됩니다.",
              ". A hidden record stays at its URL as a tombstone with no name. An identifier retired by a merge redirects to the surviving one.",
            )}
          </p>
        </section>
        <section>
          <h2 className="text-xl">{t("자기 신고", "Self-reports")}</h2>
          <p className="mt-3">
            {t(
              "자기 신고는 서버의 requests.jsonl에만 기록되고, deploy/pull_requests.sh가 그 파일을 가져갈 때까지 그곳에 있습니다.",
              "A self-report is written only to requests.jsonl on the server, and stays there until deploy/pull_requests.sh pulls that file.",
            )}
          </p>
        </section>
        <section>
          <h2 className="text-xl">{t("CV와 언어 모델", "CV text and language models")}</h2>
          <p className="mt-3">
            {t(
              "CV 본문은 언어 모델이 처리합니다. 참조 아카이브의 추출은 대화가 학습에서 제외되도록 설정된 계정의 Claude Code로 했습니다(",
              "CV text is processed by a language model. In the reference archive, extraction was done with Claude Code under an account set to exclude its conversations from training (",
            )}
            <a
              href="https://www.anthropic.com/legal/privacy"
              className="text-accent"
              target="_blank"
              rel="noreferrer"
            >
              {t("Anthropic 개인정보 처리방침", "Anthropic privacy policy")}
            </a>
            {t(
              "). 패키지는 제공자 인터페이스로 모델을 부르며, 로컬 모델도 그 인터페이스에 있습니다.",
              "). The package calls models through a provider interface, which includes local models.",
            )}
          </p>
        </section>
        <section>
          <h2 className="text-xl">{t("연락", "Contact")}</h2>
          <p className="mt-3">
            <a href={contactHref()} className="text-accent">
              {site.contactEmail}
            </a>
          </p>
        </section>
      </div>
    </div>
  );
}
