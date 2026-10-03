# Giye architecture

Giye builds a **provenance-first census archive of a creative field**: everyone named on the
field's programme rosters, every dated activity they publish, each record carrying its source,
and a public website on which every person has a permanent, citable page. It was written for
Korean media art and runs the live archive at the project domain (see README). Nothing in the
code is specific to Korea or to media art except configuration (rosters, glossaries, gazetteer).

This repository holds **software only**. No person-level data is distributed. The demo uses a
synthetic field (`examples/demo/`).

## Stages

```
 rosters (public pages)       artists' public CVs
          │                          │
   1 COLLECT  ── robots.txt check, source URL + collected_at on every row, original bytes kept
          │                          │
          │                   2 EXTRACT ── LLM structured extraction (schema-validated), cached
          ▼                          ▼
   3 LEDGER   ── CSV tables, permanent IDs, backup before every write, review queue
          │
   4 RESOLVE  ── same-person rules (E1–E4, X1), team ≠ person guard, merges retire IDs
          │
   5 NORMALIZE ── derived values with rule IDs (P1–P5), multilingual institution entities (V1–V9)
          │
   6 EXPLORE  ── feature/embedding space, clusters with a data-chosen k and stability report
          │
   7 PUBLISH  ── site snapshot (JSON), permanent URLs, redirects for retired IDs, citations
          ▼
   web/ (TanStack Start + React) reads the snapshot; no database needed to browse
```

Each stage reads the previous stage's files and writes its own. Stages 3–7 never fetch the
network, so the whole chain after collection is deterministic and can be re-run from the ledger.

## Package layout

| Module | Stage | Responsibility |
|---|---|---|
| `giye.collect` | 1 | `Fetcher` (robots.txt before every request, rate limit, contact user agent, TLS-lenient retry), `SnapshotStore` (sha256, manifest.jsonl), `RosterCollector`, evidence copies, frame registry (eligibility F1–F5) |
| `giye.extract` | 2 | CV source registry, fetch and change detection, LLM extraction to a pydantic schema, validation, cached responses for offline replay |
| `giye.ledger` | 3 | table schemas, CSV I/O with locking and backups, permanent ID assignment (`GY-000001`), retirement table, review queue |
| `giye.resolve` | 4 | same-person evidence rules, cross-script name keys (`names.py`), team detection, merge with ID retirement |
| `giye.normalize` | 5 | text normalisation, place gazetteer, institution entities and their audit, derived artist attributes |
| `giye.explore` | 6 | feature schema, optional text-embedding backend, clustering with k chosen by bootstrap stability, cluster descriptors |
| `giye.publish` | 7 | site snapshot builder, ID redirects and stubs, coverage per frame, dataset versions, citation metadata |
| `giye.cli` | all | `giye <stage>` commands and `giye run` for the whole chain |

## Stage 1 — collection

`giye.collect` is the only stage that uses the network. The demo does not: `collect.offline_roots` maps a URL prefix to a local directory, and those reads still go through that directory's `robots.txt`.

- `Fetcher` checks robots.txt (RFC 9309, `urllib.robotparser`) before every request and caches the file per host. The configured User-Agent, which must include a contact URL or email, is sent on every request including robots.txt. Requests to one host are spaced by `collect.min_delay_s`. Page fetches use `collect.timeout_s`; robots.txt uses `collect.robots_timeout_s`. A certificate failure is retried once without verification and the page is marked `tls_unverified`. A disallow raises `RobotsDisallowed` and that URL is not requested.
- `SnapshotStore` writes each distinct body once under its SHA-256 and appends one `manifest.jsonl` line per fetch (`url`, `final_url`, `status`, `fetched_at` in UTC, `sha256`, `bytes`, `content_type`, `collector`, `run_id`, `tls_unverified`).
- A `RosterCollector` subclass implements `editions()` and calls `fetch()`. `run()` returns roster rows with `source_url` and `collected_at` and writes `data/work/rosters/<frame>.csv`. Ledger upsert is the next port (see the TODO in `giye.collect.base`).
- `frames.yml` holds each programme and its F1–F5 judgement. Coverage is members recorded / roster size.
- Evidence keeps a copy of every cited URL. A gone page (HTTP 404 or 410, or a connection failure) is replaced with an existing Internet Archive capture (`via=archive.org` and the capture time). Save Page Now is never called. A robots.txt disallow is stored as `robots_disallowed` unless `evidence.archive_fallback_for_disallowed` is true (default false).

## Data model (ledger)

| Table | Key | Holds |
|---|---|---|
| `artists.csv` | `ledger_id` | names (native script and Latin), aliases, permanent `gy_id`, status, source, collected_at |
| `activities.csv` | `activity_id` | title, venue, year, type, role, source, collected_at, publishable, origin |
| `frame_membership.csv` | (`ledger_id`, `frame_code`) | who is on which roster edition, with source |
| `cv_sources.csv` | `source_id` | where an artist's public CV lives, last fetch, content hash, snapshot path |
| `links.csv` | `link_id` | external links and their last check |
| `review_queue.csv` | `queue_id` | items that need a human decision (possible same person, conflicts) |
| `gy_retired.csv` | `gy_id` | IDs retired by merges and where they now point |
| `scope.csv` | `ledger_id` | in/out-of-scope decisions with the criterion used |
| `collaborators.csv`, `collaborations.csv` | | non-artist collaborators recorded by some programmes |

## Guarantees

1. **Provenance.** A row without `source_url` and `collected_at` is never published. Cited pages are
   kept as original bytes with a hash; when the live page is gone, the snapshot remains.
2. **Permanence.** A `gy_id` is never reused or deleted. A merge retires the absorbed ID and the
   site redirects it; a hidden record keeps its URL.
3. **Rules, not hand judgement.** Every inclusion, identity and normalisation decision is made by a
   named rule (docs/RULES.md) and logged. Items no rule can decide go to the review queue.
4. **Respectful collection.** robots.txt is checked before every request; disallowed hosts are
   never fetched (rosters there must be transcribed by hand with a source URL per entry). A gone
   page may be replaced with an existing Internet Archive capture. A disallow is not sent to the
   Archive unless `evidence.archive_fallback_for_disallowed` is turned on.
5. **No bulk export.** The website is for reading. It does not offer dataset downloads or an API,
   and ships a scrape guard. Person-level data are shared on request under a data-use agreement.
6. **Equality.** Nothing ranks, recommends or features a person. Listing order is random or
   alphabetical; clusters are offered for exploration, not as rankings.
