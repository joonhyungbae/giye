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
          <p className="mt-3">
            {t(
              "사람 페이지에는 기록에서 규칙으로 끌어낸 값도 나옵니다. 출생 연도(그 사람의 CV 첫머리에 출생 문구로 적힌 연도가 하나뿐일 때만, 출처 링크와 함께), 활동 시작 연도(가장 이른 공개 기록), 활동 기반 국가(기록의 장소에서 추정, 근거 링크와 함께)입니다.",
              "A person's page also shows values derived from the records by rule: a birth year (only when the opening of that person's own CV states exactly one, with a link to it), the year active since (the earliest public record), and the country they are based in (inferred from the places of their records, with a link to the evidence).",
            )}
          </p>
        </section>
        <section>
          <h2 className="text-xl">{t("첫 화면이 보내는 것", "What one home page load sends")}</h2>
          <p className="mt-3">
            {t(
              "첫 화면의 그림은 공개된 모든 사람과 모든 기록을 한 번에 받아 그립니다. 한 번의 로드는 다음을 보냅니다. 공개된 사람마다: 식별자, 한글·영문 이름, 참여한 프로그램 회차 코드, CV 확인 여부와 상태, 수집일, 활동 시작 연도, 매체 태그, 지역. 공개된 기록마다: 연도, 유형 코드, 수집일, 출처 URL의 도메인(개인 웹사이트라면 그 사람의 사이트 주소), 그리고 두 사람 이상의 기록에 나오는 장소일 때만 그 장소명. 그 밖에 프로그램 목록과 인원, 데이터셋 버전 목록, 첫 화면의 배치 순서가 갑니다. 기록의 제목과 출처 URL 전체는 보내지 않습니다.",
              "The home image is drawn from every published person and every published record, received at once. One load sends: for each published person, the identifier, Korean and English names, the programme edition codes they took part in, whether a CV was found, status, collection date, the year active since, medium tags and regions; for each published record, its year, type code, collection date, the domain of its source URL (for a personal website, that person's site address), and the venue only when that venue occurs on records of two or more people. It also sends the programme list with counts, the list of dataset versions and the order of the home ring. It does not send record titles or full source URLs.",
            )}
          </p>
        </section>
        <section>
          <h2 className="text-xl">{t("하지 않는 일", "What is never done")}</h2>
          <p className="mt-3">
            {t(
              "데이터셋을 내려받거나 대량으로 내보내는 기능은 없고, 공개 API도 없습니다. 여러 사람을 아우르는 응답에는 위의 '첫 화면이 보내는 것'에 적은 항목만 담깁니다. 기록의 제목과 출처 URL 전체는 한 사람씩 제공합니다.",
              "There is no dataset download, no bulk export, and no public API. A response that covers many people carries only the fields listed under “What one home page load sends” above. A record's title and its full source URL are served one person at a time.",
            )}
          </p>
        </section>
        <section>
          <h2 className="text-xl">{t("요청 한도", "Request limits")}</h2>
          <p className="mt-3">
            {t(
              "대량 복제를 막기 위해 서버는 방문자 IP마다 요청을 셉니다. 1분에 120회를 넘거나, 10분 안에 서로 다른 주소 80개를 넘게 열면 그 IP는 15분 동안 사이트 전체에서 429 응답을 받습니다. 서로 다른 주소에는 작가 페이지, 첫 화면에서 연 기록 한 장, 검색어 하나, 없는 페이지가 각각 하나로 셉니다. 같은 IP를 쓰는 사람들(학교·학회 무선망)은 한도를 함께 씁니다. 검색 엔진 크롤러는 1분에 60회입니다. 사용자 에이전트가 없거나 HTTP 라이브러리·헤드리스 브라우저인 요청은 403을 받으므로, 자동 접근성 검사는 일반 브라우저의 사용자 에이전트로 해야 합니다. 이 IP 계수는 서버 메모리에만 있고 기록으로 남기지 않습니다.",
              "To stop bulk copying, the server counts requests per visitor IP. More than 120 requests in one minute, or more than 80 distinct addresses within 10 minutes, blocks that IP from the whole site for 15 minutes (HTTP 429). Each artist page, each record opened on the home ring, each search query and each missing page counts as one distinct address. People behind one IP (a university or conference network) share these limits. Search crawlers may make 60 requests a minute. A request with no user agent, or from an HTTP library or a headless browser, gets 403, so automated accessibility checks need a normal browser user agent. The counters live only in the server's memory and are not logged.",
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
