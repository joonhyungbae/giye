// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Shared folding and source-host parsing for the study payload and its search.
 *
 * The server packs a domain per record, and the search matches folded titles.
 * One implementation so the packed host and a query use the same rules as the
 * canvas used to.
 */

/** Lower case and NFC, so a query and a stored string compare the same. */
export function fold(v: string | null | undefined): string {
  return (v ?? "").toLowerCase().normalize("NFC");
}

/**
 * Venue fold used by chords and by the home payload (W3): trim, then lower case.
 * Null and blank both become "".
 */
export function foldVenue(v: string | null | undefined): string {
  return (v ?? "").trim().toLowerCase();
}

const domainCache = new Map<string, string>();

/** Host of a source URL: lower case, one leading "www." removed. Unparseable values become "—". */
export function domainOf(url: string): string {
  let d = domainCache.get(url);
  if (d === undefined) {
    d = parseDomain(url);
    domainCache.set(url, d);
  }
  return d;
}

function parseDomain(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "").toLowerCase();
  } catch {
    return (
      url
        .replace(/^https?:\/\//, "")
        .split("/")[0]!
        .toLowerCase() || "—"
    );
  }
}
