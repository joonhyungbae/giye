// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Compact wire format for the home canvas's records.
 *
 * The home view needs every published record at once (tens of thousands of rows). As objects
 * through the server-function serializer, each row repeats its field names and type tags, which
 * tripled the payload. Here the rows travel as columns, and fields with few distinct values
 * (venue, source host, collected date, activity type, artist) are sent as a dictionary plus one
 * index per row. Free text and the citation URL stay off this payload (rule W1); the browser
 * rebuilds StudyRecord rows from the columns.
 *
 * W2. The home payload carries no record ids. A record is addressed by (artist id, ord): ord is its
 * position among that artist's records in the study order — published records sorted by artist id,
 * then title (Korean collator), then record id as a tie-break so the order is total. One server
 * helper computes the study order and every server function that refers to study records uses it.
 * The payload carries `stamp` (the data stamp hash already used for the ETag); getArtistRecord and
 * searchStudyRecords return the same `stamp`, and the client matches by ord only when the stamps
 * are equal (after a data update between the two calls it opens the sheet without a highlight, and
 * nothing else changes).
 * Reason: ids were 54% of the compressed payload; the canvas only needs to point back into one
 * artist's rows.
 */
import type { StudyRecord } from "@/components/study/model";

export type Packed = {
  v: 3;
  year: number[];
  artist: [string[], number[]];
  venue: [string[], number[]];
  type: [string[], number[]];
  domain: [string[], number[]];
  collected: [string[], number[]];
};

/** A row the packer accepts. Order is the study order the client will keep (W2). */
export type PackRow = {
  artist_id: string;
  venue: string | null;
  year: number;
  activity_type: string;
  domain: string;
  collected_at: string;
};

function dict(values: string[]): [string[], number[]] {
  const index = new Map<string, number>();
  const keys: string[] = [];
  const refs = values.map((v) => {
    let i = index.get(v);
    if (i === undefined) {
      i = keys.length;
      keys.push(v);
      index.set(v, i);
    }
    return i;
  });
  return [keys, refs];
}

/** venue null is sent as index -1 so it survives the round trip. */
const NO_VENUE = -1;

export function packRecords(records: PackRow[]): Packed {
  const venues = dict(records.filter((r) => r.venue != null).map((r) => r.venue as string));
  let vi = 0;
  const venueRefs = records.map((r) => (r.venue == null ? NO_VENUE : venues[1][vi++]!));
  const packed: Packed = {
    v: 3,
    year: records.map((r) => r.year),
    artist: dict(records.map((r) => r.artist_id)),
    venue: [venues[0], venueRefs],
    type: dict(records.map((r) => r.activity_type)),
    domain: dict(records.map((r) => r.domain)),
    collected: dict(records.map((r) => r.collected_at ?? "")),
  };
  return packed;
}

/**
 * Rebuild rows in received order. `ord` counts from 0 per artist: the server sent the rows in
 * study order, so the nth time an artist id appears is that record's position among their rows.
 */
export function unpackRecords(p: Packed): StudyRecord[] {
  const n = p.year.length;
  const out: StudyRecord[] = new Array(n);
  const next = new Map<string, number>();
  for (let i = 0; i < n; i++) {
    const v = p.venue[1][i]!;
    const artist_id = p.artist[0][p.artist[1][i]!]!;
    const ord = next.get(artist_id) ?? 0;
    next.set(artist_id, ord + 1);
    out[i] = {
      ord,
      artist_id,
      venue: v === NO_VENUE ? null : p.venue[0][v]!,
      year: p.year[i]!,
      activity_type: p.type[0][p.type[1][i]!]!,
      domain: p.domain[0][p.domain[1][i]!]!,
      source_type: "",
      collected_at: p.collected[0][p.collected[1][i]!]!,
    };
  }
  return out;
}
