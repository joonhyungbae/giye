// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The reader's language choice as a cookie, so the server renders the chosen language and a
 * Korean reader does not see the English page first. The toggle writes both this cookie and
 * localStorage ("giye-lang"); a visitor who chose before the cookie existed still has only
 * localStorage, which LanguageProvider reads after hydration and copies into the cookie.
 * English stays the default when neither is set (user decision, 2026-10-04).
 */
import { createIsomorphicFn } from "@tanstack/react-start";
import { getCookie } from "@tanstack/react-start/server";
import type { Lang } from "./i18n";

export const LANG_COOKIE = "giye-lang";

function parse(value: string | undefined | null): Lang | null {
  return value === "ko" || value === "en" ? value : null;
}

/** The language cookie of this request (server) or this document (client), or null. */
export const readLangCookie = createIsomorphicFn()
  .server((): Lang | null => parse(getCookie(LANG_COOKIE)))
  .client((): Lang | null => {
    const hit = document.cookie.split("; ").find((c) => c.startsWith(`${LANG_COOKIE}=`));
    return parse(hit?.slice(LANG_COOKIE.length + 1));
  });

/** Remember the choice for a year; the site is one origin, so the path is "/". */
export function writeLangCookie(lang: Lang): void {
  document.cookie = `${LANG_COOKIE}=${lang}; Path=/; Max-Age=31536000; SameSite=Lax`;
}
