# Giye architecture

Giye builds a **provenance-first census archive of a creative field**: everyone named on the
field's programme rosters, every dated activity they publish, each record carrying its source,
and a public website on which every person has a permanent, citable page. It was written for
Korean media art and runs the live archive at [giye.org](https://giye.org). Nothing in the
code is specific to Korea or to media art except configuration (rosters, glossaries, gazetteer) and the
region-tag and medium lists the snapshot copies from the production builder (see SNAPSHOT.md).

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
   giye render writes one plain HTML page per person from the same snapshot
```

Each stage reads the previous stage's files and writes its own. Stages 3–7 never fetch the
network, so the whole chain after collection is deterministic and can be re-run from the ledger.

## Package layout

| Module | Stage | Responsibility |
|---|---|---|
| `giye.collect` | 1 | `Fetcher` (RFC 9309 robots.txt before every request and every redirect hop, rate limit, contact user agent, TLS-lenient retry), `SnapshotStore` (sha256, manifest.jsonl), `RosterCollector`, evidence copies, frame registry (eligibility F1–F5) |
| `giye.export` | — | WARC 1.1 of the snapshot store (optional WACZ) and an RO-Crate 1.1 description of a run |
| `giye.extract` | 2 | CV source registry, fetch through `Fetcher` (snapshot only when the content hash changes), LLM extraction to a pydantic schema, validation, replay cache keyed by content hash, prompt hash, and model |
| `giye.ledger` | 3 | table schemas, CSV I/O with locking and backups, permanent `gy_id` allocation, content-derived activity ids, merge and retirement, CV-row ownership |
| `giye.resolve` | 4 | same-person evidence (E1–E4), cross-script candidates (X1, `names.py`), team guard (T1) and member expansion, review queue, merge through `giye.ledger` |
| `giye.normalize` | 5 | text normalisation, place gazetteer, institution entities and their audit, derived artist attributes. Glossary and gazetteer are a language module |
| `giye.explore` | 6 | feature schema, optional text-embedding backend, clustering with k chosen by bootstrap stability, cluster descriptors |
| `giye.publish` | 7 | site snapshot (`<data>/site/*.json`), ID redirects and stubs, coverage per frame, dataset versions, APA/Chicago/BibTeX citations, plain HTML pages (`giye render`) |
| `giye.cli` | all | `giye <stage>`, `giye render`, `giye export`, and `giye demo` for the synthetic field |

## Stage 1 — collection

`giye.collect` is the only stage that uses the network. The demo does not: `collect.offline_roots` maps a URL prefix to a local directory, and those reads still go through that directory's `robots.txt`.

- `Fetcher` checks robots.txt before every request and before every redirect hop, and caches the outcome per origin for the process. The matcher is RFC 9309 §2.2.2 (longest match), not `urllib.robotparser`. HTTP 2xx is parsed. HTTP 4xx, including 404, is `unavailable_allowed` (the URL may be fetched). HTTP 5xx, a timeout, or a network error is `unreachable_disallowed` for this process only. A sixth redirect of robots.txt itself is treated as unavailable (allowed), which is the RFC's "may assume unavailable". A page redirect uses the session's `max_redirects` (30 when unset), not that five-hop limit. The configured User-Agent, which must include a contact URL or email, is sent on every request including robots.txt. Requests to one host are spaced by `collect.min_delay_s`. Page fetches use `collect.timeout_s`; robots.txt uses `collect.robots_timeout_s`. A certificate failure is retried once without verification. The page is marked `tls_unverified`; a robots.txt retry sets `robots_tls_unverified`. A parsed disallow raises `RobotsDisallowed`. An unreachable file raises `RobotsRefused`. The hop is not sent. The path `/robots.txt` is always allowed.
- `SnapshotStore` writes each distinct body once. A later fetch of the same bytes is found by the full sha256 on an existing manifest line, not by a short prefix in the file name. The public path is still the hash itself (`<frame>/snapshots/sha256/<sha[:2]>/<sha><ext>`). Each line records `url`, `final_url`, `status`, `fetched_at` (UTC), `sha256`, `bytes`, `content_type`, `robots`, `collector`, `run_id`, and `tls_unverified`. `robots` is the verdict for the hop whose bytes were stored. `not_checked` is only for bytes the caller already held. Manifest version 1 does not keep the original response headers. Version 2 would be the first to store them.
- A `RosterCollector` subclass implements `editions()` and calls `fetch()`. `run()` returns roster rows with `source_url` and `collected_at`, writes `data/work/rosters/<frame>.csv`, and upserts the ledger. A new person receives a permanent `gy_id`. Membership is one row per person and frame. Each roster appearance is an activity. A re-run matches `name_ko` and `name_en` exactly and keeps both ids. Spelling variants stay on separate rows until rules E1–E4 and X1 merge them.
- `frames.yml` holds each programme and its F1–F5 judgement. Coverage is members recorded / roster size.
- Evidence keeps a copy of every cited URL. A gone page (HTTP 404 or 410, or a connection failure) is replaced with an existing Internet Archive capture (`via=archive.org` and the capture time). Save Page Now is never called. A robots.txt disallow records that capture's URL and timestamp (`archive_link_only`, `direct_failure=robots`) and does not download or keep the bytes. An unreachable robots.txt is stored as `robots_disallowed` with reason `robots_unreachable` and is not sent to the Archive.

## Export

`giye export warc --config giye.toml` writes the snapshot store as WARC 1.1 (gzip). Each kept body is a `response` record with a reconstructed status line, `Content-Type`, and `Content-Length`. Each manifest line is a `metadata` record. Original response headers were not kept before manifest version 2, and the warcinfo record says so. `--wacz` also writes a WACZ 1.1.1 zip (the WARC, `pages/pages.jsonl`, a CDXJ index, `datapackage.json`).

`giye export ro-crate --config giye.toml` writes `ro-crate-metadata.json` (RO-Crate 1.1) for the run: the software version, the sha256 of `giye.toml`, roster and CV URLs as `CreativeWork` (sha256 and `dateCreated` when the bytes were kept), snapshot files, and a `CreateAction` per stage. Collect, resolve, and normalize carry their rule ids (`F1`–`F5`, `E1`–`E4`, `T1`, `X1`, `P1`–`P5`, `V1`–`V9`, and the derived-value ids). Extract, the ledger, and publish have no production letter id; the action says so.

## Stage 2 — extract

`giye extract` registers CV locations, fetches them, reads them into activity rows, and writes those rows into the ledger. It does not crawl the web for a CV link (production `discover_cv_sources.py scan`). A location is declared in `[[extract.sources]]` or is already in `cv_sources.csv`.

- The registry id is `CV-<ledger_id>-<lang>`, unless the config sets `source_id` (the demo cache names its sources ahead of the random ledger id). The same URL is not registered twice for one person. A team row is refused unless `[extract] allow_team` is true (rule T1: a member's personal CV must not be filed on the team).
- Fetch uses `giye.collect.Fetcher`, so robots.txt is checked before every request, including an offline fixture. The content hash ignores whitespace. A new snapshot is written only when that hash changes. Unchanged text is not a new CV. Failures, including a robots disallow, stay on the registry row and open `cv_pull_failed`.
- The prompt is `src/giye/extract/prompts/cv_extract_v1.txt` (the production system prompt). Its SHA-256 is stored on the cache record and on the extraction file. The model id defaults to the production model (`claude-opus-5`) and can be set in `[extract] model`.
- `Provider.complete(prompt, document)` returns the raw response text. `AnthropicProvider` imports the SDK only when it is called. `ReplayProvider` reads a JSON file keyed by the CV content hash, the prompt hash, and the model. The file stores that raw text plus `model`, `prompt_sha256`, `content_sha256`, `created_at`, and `temperature`. `--replay-only`, and a run with no API key in the environment, never call the model.
- The response is validated against the production activity schema. An invented type, a non-numeric year, or an extra field rejects the whole response. A row whose `source_id` was not one of the documents is dropped.
- Apply keeps production's decisions: private sections and scholarship-like titles stay `publishable=no`; upcoming years in the future stay hidden; this year's upcoming rows stay visible with `(예정)` on the role; a Korean/English repeat with the same normalised title and venue keeps the first row; a public record of the same event wins over the CV copy; a self-reported row is marked `superseded_by_cv`. The row belongs to the owner of the CV source. A file for a ledger id that is no longer an artist is skipped when a live artist's file already covers those sources. Activity ids come from `giye.ledger`.

`data/work/cv_extract/<ledger_id>.json` is the file `giye resolve` already reads.

## Stage 3 — ledger

`giye.ledger.Ledger.open(config)` is the file-backed ledger. Paths come from the config (`data/ledger`, backups under `data/work/backups`). There is no database.

- Tables are CSV. `write_csv` is keyword-only (`path`, `fields`, `rows`) so the production positional order cannot be swapped by mistake. `Ledger.write` copies an existing file to `backups/<file>-<YYYYMMDD>-before-<task>.csv` before replacing it. The date is UTC. A second write the same day with the same task is kept as `-2`, `-3`, and so on. A file that does not exist yet has nothing to copy.
- A lock file `.ledger.lock` is taken on the first read or write of that ledger directory and held until the process exits (`fcntl.flock`), so one run's reads and its later writes are not interleaved with another process.
- `ledger_id` (`LED-…`) is the internal key. `gy_id` (`GY-000001`, prefix from `archive.id_prefix`) is the published id. The next id is one past the highest number ever issued, retired ids included. A gap is not filled. Sorting the file does not renumber anyone. The collector issues an id when it creates a person; production issued it at site build, in Hangul dictionary order for that batch. Once issued, the id stays.
- Activity ids are uuid5 of the namespace derived from `https://giye.org/ns/activity` (the same string as production, so the same fact hashes to the same id). The key is ledger id, source, normalised title, year, activity type, venue, and — for a collector row — the frame code. A repeated key in one write gets an ordinal (0, 1, 2, …). See `giye.ledger.ids`.
- `merge(kept, dropped, evidence=…, rule=…)` moves activities, frame memberships and CV sources onto the kept row, retires the dropped `gy_id`, and rewrites older retirements so they point at the final survivor. `redirects()` maps each retired `gy_id` to the survivor's current `gy_id`. A merge without an evidence string is refused. The rule id is stored on the kept row's note.
- A row whose `origin` is `cv:<source_id>` belongs to the `ledger_id` on that CV source. After a merge the source's owner is the survivor, and a row left on the retired id follows it. The activity id is not recomputed (production leaves it).

## Stage 4 — resolve

`giye resolve` does not fetch. It reads the ledger and, when present, `data/work/cv_extract/<ledger_id>.json` (the production extraction file: an `activities` list with `title`, `venue`, `year`). `[resolve] cv_dir` may point at local HTML CVs; those fill people who have no JSON yet, matched by `data-name-ko` and `data-name-en`. The match has to be unique.

Team rows are expanded first: each name in `members=` (or a `; group;` credit) gets its own roster row, credited `팀: <team>`. The team stays. A second run adds nothing.

Then, in order:

1. **E1.** Rows that share a website key and an overlapping name. A team is not merged with a person (T1). Two team rows may merge. The kept row is the one with more activities.
2. **E2–E4** on a same-script exact name, unless a collector pinned the rows apart (`identity=<collector>:<key>`) or T1 applies. Both rows must be on a roster.
3. **X1.** A Hangul personal name and a Latin-only row whose romanized keys meet. E1–E4 still have to hold (`X1+E2`, and the same for the others). Otherwise the pair is queued. Sharing a frame code drops the pair.
4. Same-script pairs no rule decided are queued (`possible_same_person`). They are not merged.

Production's collectors queued a same-name pair before this script ran, and the script merged E2–E4 only for pairs already in that queue. This collector does not write that queue, so a pair the evidence rules accept is merged here. The evidence strings, the ±1 year window, the bracket rule, and the team-role prefix are the production ones.

## Stage 5 — normalize

`giye normalize` does not fetch and does not edit the ledger. It reads the ledger and writes `data/processed/` under the configured data directory: `activities.csv`, `artist_attributes.csv`, `venues.csv`, `venue_audit.md`, `manifest.json`, and `report.md`.

- P1 flags a missing year, a year outside 1900 … this year + 2, and a year that is a period named in the title. Nothing is deleted.
- P2 normalises title and venue text and records a script (`ko` / `en` / `mixed`).
- P4 links a roster row to its frame edition, and a CV row to that edition when the title or venue names the frame's event in the same year.
- P5 writes birth year (B1), base country and Korean region (L1), active-since (A1), and medium tags (M1). A value already on the artist row wins. A team CV is not that row's birth year.
- V1–V9 resolve venue strings into institution and funder entities. V2 splits, V4 keys exactly, V5 joins parenthetical aliases, V7 strips titles, qualifiers, editions, and unstable Hangul spaces and joins Latin names with the same words, V8 joins a building or an acronym plus its city, V9 joins a Hangul name to a Latin name when the glossary, the gazetteer, and romanisation give one shared reading. `venue_audit.md` lists every merge with its rule id.

Generic words and place names are a `LanguageModule`: `name_keys` (personal names, wrapping `giye.resolve.names` for Korean), `glossary`, `gazetteer`, and `romanise`. The Korean–English module is the default and reads packaged files. `[normalize] glossary` and `[normalize] gazetteer` replace those files. `[normalize] reference` points at a GeoNames tree (`geonames/` and `countries/`); that dump is CC BY 4.0 and is not in this repository. `[normalize] venue_name_rules` (or `giye normalize --venue-name-rules`) keeps a subset of V7, V8, V9, or `none`.

## Stage 7 — publish

`giye publish` does not fetch and does not crawl. It reads the ledger and, when present, `data/processed/`, and writes the site snapshot under `data/site/`. Shapes, the publish filter, tombstones, and redirects are documented in [SNAPSHOT.md](SNAPSHOT.md).

A published activity and a published person each keep `source_url` and `collected_at`. A `gy_id` that is no longer a public page stays in `artist_stubs.json` (`HIDDEN_BY_REQUEST` or `WITHDRAWN`) with no name. `gy_redirects.json` sends a retired id to the survivor. Frame coverage (F4) is `round(100 * included / roster, 1)`. The dataset version defaults to `0.2`. `citations.json` holds APA, Chicago, and BibTeX for the dataset and for each person; the sentences match the production cite dialog, with the author and the site origin taken from `[publish]`.

`giye render` writes one plain HTML page per person (and a tombstone or redirect page per other id) so the snapshot can be read without the web front-end. `giye demo` runs collect, extract in replay mode, resolve, normalize, publish, and render on `examples/demo/` into a separate output directory. It does not open a network connection. Ledger ids in that run are a fixed sequence so the snapshot can be compared with `tests/golden/demo_snapshot.json`. The summary prints merges per rule (`E1`, `E2`, …, `X1+E2`), `blocked: T1`, open queue items, and institution merges `V7`, `V8`, `V9`. V7a–d rewrite a venue key before a join is logged, so the V7 count also includes a qualifier or an exhibition title that shares an entity with another spelling. What the synthetic field is arranged to show is listed in `examples/demo/EXPECTED.md`.

The home-page ring (`rim_order.json`; production orders it by entry generation) and the embedding flight file are not built. Both feed the web front-end, which is still planned.

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
4. **Respectful collection.** robots.txt is checked before every request and every redirect hop;
   a disallowed or unreachable URL is never fetched (rosters there must be transcribed by hand
   with a source URL per entry). A gone page may be replaced with an existing Internet Archive
   capture. A disallow records that capture's URL and time and does not keep the bytes. An
   unreachable robots.txt is not sent to the Archive.
5. **No bulk export.** The website is for reading. It does not offer dataset downloads or an API,
   and ships a scrape guard. Person-level data are shared on request under a data-use agreement.
6. **Equality.** Nothing ranks, recommends or features a person. Listing order is random or
   alphabetical; clusters are offered for exploration, not as rankings.
