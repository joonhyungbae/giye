// SPDX-License-Identifier: AGPL-3.0-only
import { createFileRoute } from "@tanstack/react-router";
import { site } from "@/config/site";
import { loadArtists } from "@/lib/giye.data";

const STATIC_PATHS = [
  "/",
  "/artists",
  "/data",
  "/about/criteria",
  "/about/frame",
  "/about/governance",
  "/about/methodology",
  "/research",
  "/request",
  "/privacy",
];

export const Route = createFileRoute("/sitemap.xml")({
  server: {
    handlers: {
      GET: async () => {
        // The configured public origin, not the request's: behind the tunnel the request
        // arrives over http, and robots.txt and canonical links say https.
        const origin = site.origin;
        const artists = loadArtists();
        const urls = [
          ...STATIC_PATHS.map((p) => ({
            loc: `${origin}${p}`,
            lastmod: undefined as string | undefined,
          })),
          ...artists.map((a) => ({
            loc: `${origin}/artist/${a.id}`,
            lastmod: a.updated_at.slice(0, 10),
          })),
        ];
        const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls
  .map(
    (u) => `  <url><loc>${u.loc}</loc>${u.lastmod ? `<lastmod>${u.lastmod}</lastmod>` : ""}</url>`,
  )
  .join("\n")}
</urlset>`;
        return new Response(xml, {
          headers: { "content-type": "application/xml; charset=utf-8" },
        });
      },
    },
  },
});
