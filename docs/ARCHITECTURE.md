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
| `giye.collect` | 1 | `Fetcher` (robots.txt enforced on every request, rate limit, user agent), `keep()` snapshot store (sha256, manifest.jsonl), roster collector base class, frame registry (eligibility F1–F5) |
| `giye.extract` | 2 | CV source registry, fetch and change detection, LLM extraction to a pydantic schema, validation, cached responses for offline replay |
| `giye.ledger` | 3 | table schemas, CSV I/O with locking and backups, permanent ID assignment (`GY-000001`), retirement table, review queue |
| `giye.resolve` | 4 | same-person evidence rules, cross-script name keys (`names.py`), team detection, merge with ID retirement |
| `giye.normalize` | 5 | text normalisation, place gazetteer, institution entities and their audit, derived artist attributes |
| `giye.explore` | 6 | feature schema, optional text-embedding backend, clustering with k chosen by bootstrap stability, cluster descriptors |
| `giye.publish` | 7 | site snapshot builder, ID redirects and stubs, coverage per frame, dataset versions, citation metadata |
| `giye.cli` | all | `giye <stage>` commands and `giye run` for the whole chain |

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
   never fetched (rosters there must be transcribed by hand with a source URL per entry).
5. **No bulk export.** The website is for reading. It does not offer dataset downloads or an API,
   and ships a scrape guard. Person-level data are shared on request under a data-use agreement.
6. **Equality.** Nothing ranks, recommends or features a person. Listing order is random or
   alphabetical; clusters are offered for exploration, not as rankings.
