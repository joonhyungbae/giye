// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute } from "@tanstack/react-router";
import { getContentPage } from "@/lib/giye.functions";
import { AboutNav } from "@/components/SiteChrome";
import {
  ContentPageView,
  type ContentPageData,
} from "@/components/ContentPage";
import { absoluteUrl, pageTitle } from "@/config/site";

export const Route = createFileRoute("/about/governance")({
  loader: () => getContentPage({ data: { slug: "governance" } }),
  head: () => ({
    meta: [
      { title: pageTitle("운영 원칙 Governance") },
      {
        name: "description",
        content:
          "독립성 선언, 이해충돌 정책, 요청 처리 원칙(30일 내 답변). Independence, conflict-of-interest policy and request handling at Giye.",
      },
      { property: "og:title", content: "운영 원칙 Governance — 기예 Giye" },
      {
        property: "og:description",
        content: "Independence and request-handling policy.",
      },
      { property: "og:url", content: absoluteUrl("/about/governance") },
    ],
    links: [{ rel: "canonical", href: absoluteUrl("/about/governance") }],
  }),
  component: () => (
    <>
      <AboutNav />
      <ContentPageView {...(Route.useLoaderData() as ContentPageData)} />
    </>
  ),
});
