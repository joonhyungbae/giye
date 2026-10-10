# Dataset release

`giye release` writes two deposits for the data paper. It does not upload them.

```bash
giye release --config deploy/giye.production.toml --version 2026-10-10
```

The version is the directory name. Output is `<data>/release/<version>/`.
`<data>/release/latest` points at that directory. The pseudonym map is
`<data>/work/release/<version>_key.csv`. That file is not inside the release
directory and is not part of either deposit.

Settings are the `[release]` table (docs/CONFIG.md). `open_names` (default false)
drops `name_ko` and `name_en` from the open roster. `pseudonym` is `per_release`
(a new token each build) or `stable_hmac` (HMAC of the ledger id with
`pseudonym_secret`, a file that must not live under `<data>/release/`).
`licence` defaults to `CC-BY-4.0` (the open tier). `commercial_use` defaults to
false. `k` defaults to 10. `creator_family` and `creator_given` default to
`Example` and `Archive`. `creator_orcid` defaults to empty: `CITATION.cff`
then has no ORCID line, and the Zenodo creator entry keeps `TODO`.
`repository_url` defaults to empty. `site_url` defaults to `[publish] site_url`.

## Two records

`open/` is Zenodo record 1 (open, CC BY 4.0). `restricted/` is Zenodo record 2
(restricted, access on request). `zenodo/open.zenodo.json` and
`zenodo/restricted.zenodo.json` are the deposit metadata. They are not files of
either record. `language` is `eng` because Zenodo takes one code; `notes`
records both dataset languages as eng/kor. Replace the placeholder DOIs and
the creator ORCID before upload.

`open/stats.json` is the file a data paper should cite for headline counts.
The Journal of Open Humanities Data asks authors not to copy repository text
into the paper. Paraphrase `open/README.md` and the Zenodo descriptions.

```
<version>/
  open/                  record 1
    README.md            datasheet
    LICENSE              CC BY 4.0 short notice
    CITATION.cff
    CODEBOOK.md
    DUA.md               full agreement, public so a requester can read it first
    MANIFEST.json        sha256 of every file in both records except this manifest
    stats.json
    roster_facts.csv
    programmes.csv
    institutions.csv
    edition_year.csv
    entry_generation.csv
    activity_kind_year.csv
    venue_country_period.csv
    record_depth.csv
  restricted/            record 2
    README.md
    CODEBOOK.md          restricted columns, and a pointer to the open codebook
    MANIFEST.json        same bytes as open/MANIFEST.json
    people.csv
    activities.csv
    roster_names.csv
    roster_sources_withheld.csv
  zenodo/
    open.zenodo.json
    restricted.zenodo.json
```

CSV files are UTF-8, with a header row, snake_case columns, and no index column.
Dates are ISO 8601 (`YYYY-MM-DD`).

## Who is included

Who is included is the set the site publishes (`published_ids`): not out of
scope, on an admitted roster (`included` or `adjacent`), with an http(s) source
and a collection date, status empty, `PUBLISHED` or `STAGED`. People hidden by
request are in neither tier.

No participants were recruited and nobody was contacted. The rows are
information programmes and artists published themselves.

## Open record

- `roster_facts.csv` — one row per published person and admitted roster edition.
  Columns: `gy_id`, `frame_code`, `programme`, `year`, `source_url`,
  `source_withheld`, `collected_at`. Names are omitted. `gy_id` stays so a
  correction can still name the row; the name that goes with it is only in
  the restricted record. REL-URL: when the membership URL, after
  percent-decoding, lower-casing and removing every non-letter, contains that
  person's normalised name (`name_ko`, `name_en`, each alias; Hangul names of
  two or more syllables, Latin names of four or more letters, and the Latin
  name with its parts reversed), or a host label or path segment equals any
  published person's token, `source_url` becomes the programme's own http(s)
  page from the frame registry, or empty, and `source_withheld` is `yes`.
  Otherwise `source_withheld` is `no`. The withheld URL is not in this file.
- `programmes.csv` — one row per admitted frame. `APE-2026`, `APE-ARCHIVE` and
  `APE-CURRENT` stay separate rows. Access mode is the F2 sentence. When the
  frame has no http(s) source, the source is the earliest accepted row of
  `declared_roster_sizes.csv` for that frame. Operator credits are name, role
  and source. The quote and the snapshot path are not copied.
- `edition_year.csv` — `first_timers` only (first non-staff year in that
  programme). `edition_size` is not written: it is a count of `roster_facts`.
- `entry_generation.csv` — field entry generation (R1–R2, five-year bins
  anchored at 2000) by programme.
- `activity_kind_year.csv` — CV-origin activity rows by kind and year.
- `venue_country_period.csv` — those same rows by KR / abroad / unresolved and
  five-year period.
- `record_depth.csv` — P6 levels.
- `institutions.csv` — venues with at least `k` people. `n_artists` is rounded
  to 5. The threshold uses the unrounded count.

`first_timers` and `entry_generation` are a staff-adjusted group-by of the
published roster. Staff is `_is_staff` (the first non-CV role). A cell under
`k` is omitted. A cell at or above `k` is rounded to 5 with `suppressed` = no.
The share is an integer percent of that programme's entrants, or `>=90`. The
token `suppressed` is not written. A build fails when a suppressed cell equals
that group-by (D5 in docs/CAREER.md).

CV kind × year cells under `k` stay the token `suppressed`. That table is not
a group-by of `roster_facts`. External-coverage suppression is unchanged.
Headline `people` and `cv_rows` in `stats.json` stay exact when they are at
least `k`. `cv_rows_suppressed` is the row mass in unpublished kind × year
cells. `cv_rows_published_rounded` is the sum of the rounded published cells.

## Restricted record

- `people.csv` — pseudonym, entry year, entry generation, record depth,
  programme codes, team flag. No names, no `gy_id`, no URLs.
- `activities.csv` — activity-channel rows: pseudonym, year, kind, channel,
  venue id and funder id only when that entity is in `institutions.csv`,
  country, region, venue kind, event-link frame code, `cv` or `roster`.
  No titles, no free text, no source URLs, no background or hidden rows.
- `roster_names.csv` — `gy_id`, `name_ko`, `name_en`, `aliases` for published
  people. Hidden people are absent. This file is not scanned by the
  pseudonymised-file leak check, which would reject every name. It is checked
  on its own: no URL, no pseudonym token, no `ledger_id` column, and the
  `gy_id` set matches `artists.csv`.
- `roster_sources_withheld.csv` — `gy_id`, `frame_code`, `source_url` for each
  open roster row whose per-person URL was withheld (REL-URL). This is the
  file that still holds that URL. It is not in the open record.

The pseudonym key stays out of both tiers. `people.csv` and `activities.csv`
stay pseudonymised.

## `publishable`

The open CV tables and `restricted/activities.csv` use one rule. A row whose
`publishable` is `no` is dropped from both. A missing or blank `publishable`
is not treated as `no` and is kept in both. The open tables also require a
`cv:` origin and channel `activity`. The restricted file requires channel
`activity` and does not require a CV origin.

## What the build refuses

The build stops if a cell under `k` would carry a value, a suppressed cell is
recoverable from another published aggregate or from a published total, a
suppressed cell equals a staff-adjusted group-by of published `roster_facts`,
`people.csv` or `activities.csv` holds a ledger name, `gy_id`, URL or title,
an open file holds a CV row (`cv:` origin, a CV-only URL, or a title column),
or a person hidden by request appears anywhere in the release. The build also
stops when an open file contains a published person's name token under the
REL-URL normalisation. The licence and citation author lines are the
exception, and so are institution and programme name fields: those are not
person names. A single Latin word of four letters in prose is not treated as
a hit, because that token collides with ordinary English (`date`). The same
four letters still withhold a source URL and still fail the build when a URL
label equals them.
