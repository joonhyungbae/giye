// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute } from "@tanstack/react-router";
import { getContentPage } from "@/lib/giye.functions";
import { ContentPageView, type ContentPageData } from "@/components/ContentPage";

export const Route = createFileRoute("/about/criteria")({
  loader: () => getContentPage({ data: { slug: "criteria" } }),
  head: () => ({
    meta: [
      { title: "GIYE" },
      {
        name: "description",
        content:
          "기예가 작가를 등재하는 기준과 개정 이력. The criteria Giye uses to include an artist record, with its change log.",
      },
      { property: "og:title", content: "등재 기준 Inclusion criteria — 기예 Giye" },
      { property: "og:description", content: "How records enter the Giye index." },
      { property: "og:url", content: "/about/criteria" },
    ],
    links: [{ rel: "canonical", href: "/about/criteria" }],
  }),
  component: () => <ContentPageView {...(Route.useLoaderData() as ContentPageData)} />,
});
