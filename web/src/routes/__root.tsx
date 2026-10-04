// SPDX-License-Identifier: AGPL-3.0-only
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  useRouterState,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";
import { type ReactNode } from "react";

import appCss from "../styles.css?url";
import { archiveSentenceEn, archiveSentenceKo } from "@/config/site";
import { LanguageProvider } from "@/lib/i18n";
import { HomeReturn, SiteFooter } from "@/components/SiteChrome";
import { Toaster } from "@/components/ui/sonner";

function NotFoundComponent() {
  return (
    <div className="wrap py-24">
      <h1 className="text-3xl">404</h1>
      <p className="mt-3 text-muted-foreground">
        요청하신 페이지를 찾을 수 없습니다. / The page you requested was not found.
      </p>
      <p className="mt-6">
        <Link to="/" className="text-accent">
          처음으로 / Home
        </Link>
      </p>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();

  return (
    <div className="wrap py-24">
      <h1 className="text-2xl">페이지를 불러오지 못했습니다 / This page didn't load</h1>
      <div className="mt-6 flex gap-3">
        <button
          onClick={() => {
            router.invalidate();
            reset();
          }}
          className="border border-border px-3 py-1.5 text-sm hover:bg-secondary"
        >
          다시 시도 / Try again
        </button>
        <a href="/" className="border border-border px-3 py-1.5 text-sm hover:bg-secondary">
          처음으로 / Home
        </a>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "GIYE" },
      {
        name: "description",
        content: `${archiveSentenceKo()} ${archiveSentenceEn()}`,
      },
      { property: "og:site_name", content: "Giye 기예" },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "preconnect", href: "https://fonts.gstatic.com", crossOrigin: "anonymous" },
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=Rubik:wght@300;400;500&family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Nanum+Gothic+Coding:wght@400;700&family=IBM+Plex+Sans+KR:wght@300;400;500;600&display=swap",
      },
      { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" },
      { rel: "icon", href: "/favicon.ico", sizes: "any" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  const pathname = useRouterState({ select: (st) => st.location.pathname });
  const isHome = pathname === "/";

  return (
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:m-2 focus:bg-background focus:p-2"
        >
          본문 바로가기 / Skip to content
        </a>
        <HomeReturn />
        <main id="main">
          {/* Required: nested routes render here. Removing <Outlet /> breaks all child routes. */}
          <Outlet />
        </main>
        {!isHome && <SiteFooter />}
        <Toaster />
      </LanguageProvider>
    </QueryClientProvider>
  );
}
