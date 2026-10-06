# Site snapshot

`giye publish` writes `<data>/site/*.json` from the ledger and from `<data>/processed`
when that directory exists. The shapes are those the reference archive's former private
builder (`scripts/build_site_dataset.py`) wrote; the site is now rebuilt by the package
stages only (docs/DEPLOY.md). The web front-end reads these files and needs no database.

`citations.json` is not one of those production files. The live site builds the same
APA, Chicago, and BibTeX sentences in the browser (`CiteDialog`). They are stored
here so a static page can show them. Keys on the other files are unchanged.

Timestamps (`created_at`, `generated_at`) are the UTC time of the build. `collected_at`
is the calendar date on the ledger row. An activity, background, or collaboration row
without a `source_url` or a `collected_at` is left out of the snapshot.

## Who is published

A person is published when all of these hold:

- not listed as `scope=out` in `scope.csv`
- the person has a membership of an admitted frame (decision `included` or `adjacent`); `cv_link_ok` does not admit anyone
- `source_url` starts with `http` (a roster membership or the frame's own URL can fill an empty one)
- `collected_at` is not empty (no date is filled in at publish time)
- `status` is empty, `PUBLISHED`, or `STAGED`

Everyone else who already has a `gy_id` is a stub in `artist_stubs.json`:

| Ledger status | Stub value |
|---|---|
| `HIDDEN_BY_REQUEST` | `HIDDEN_BY_REQUEST` |
| anything else unpublished | `WITHDRAWN` |

The stub is the id and that state. It has no name and no records. The URL still answers.

`gy_redirects.json` maps a retired `gy_id` to the survivor's current `gy_id`. The ledger
already points each retirement at the final survivor, so the file is one lookup.

A roster member who is neither published nor out of scope stops the build. Production
refuses that case the same way: every roster row has to reach the site.

An activity is published when `publishable` is `yes`, the title is non-empty, the year
parses as 19xx or 20xx, and `source_url` is http(s). The title is cut at 500 characters.
A type outside the production list is stored as `other`. Rows sort by year descending,
then title.

`background.json` lists CV lines whose note contains `cv_section=` of education,
employment, teaching, or press. Scholarship and service are not listed. A title that
matches the scholarship / peer-review pattern is dropped even if the section is one of
those four. A future year marked `upcoming` is dropped. A row whose note holds
`ungrounded=` (it failed grounding, docs/RULES.md) or `suppressed=<reason>` (a curator took
it off, for example on a correction request) is dropped: `publishable` is `no` on every
background row as a section marker, so it cannot carry those decisions. Lines of one person,
section and year whose titles are identical after lower-casing and removing punctuation and
spaces collapse to the first. A Korean line and its English translation are different titles
and are both listed.

## Files

### `artists.json`

One object per published person, in name order (`name_ko`, then `name_en`).

| Key | Meaning |
|---|---|
| `id` | Permanent `gy_id` |
| `name_ko`, `name_en`, `aliases` | Names. A missing `name_ko` falls back to `name_en`, then the production placeholder `이름 미상` |
| `type` | `collective` when the T1 team test (`giye.resolve.teams.team_like`) marks the row, otherwise `individual` |
| `bio_short` | Always null |
| `birth_year`, `birth_year_source_url` | From `artist_attributes.csv` when normalize has run |
| `active_since` | Ledger value, else the derived one |
| `regions`, `countries`, `medium_tags` | Ledger text, else derived rows. Region tags and the field/category medium guess come from the field file |
| `derived` | `{field: {rule, url?}}` for each derived value |
| `technique_tags`, `theme_tags` | Always empty lists |
| `frame_status`, `frame_codes` | Membership |
| `frame_editions` | `{frame, edition, role?}` resolved against `frames.yml` |
| `verification`, `cv_status` | `found` / `pending` / `none` |
| `same_name` | Other published ids in an open `possible_same_person` item |
| `members` | On a team: the published ids the ledger credits as its members, sorted. Empty otherwise |
| `member_of` | On a member: the published ids of the teams that credit them, sorted. Empty otherwise |
| `status` | Always `PUBLISHED` in this file |
| `source_url`, `source_type`, `collected_at` | Provenance of the person row |
| `external_ids.ledger_id` | Internal id |
| `created_at`, `updated_at` | Build time, or the ledger's `updated_at` |

`frame_editions` follows the registry. An edition alias in the field file maps a membership code to a frame and an edition when that frame is registered. A code equal to a registry row uses `years_covered` when that cell is a single year. Otherwise the longest matching registry code wins, and a trailing `-YYYY` is the edition. `role` is the roster activity's role for that membership code (the last such row wins).

`members` and `member_of` read the markers team expansion (rule T2, `giye.resolve.teams.expand_teams`) writes in the ledger, through `giye.resolve.teams.team_credits`: a `팀 구성원: <team> (<team ledger id>)` note on a record it created, a membership whose `attach_rule` is `team:<team ledger id>`, and an activity whose role is `<team prefix> <team name>` on one of the team's own editions. A name alone links nobody. Only published records are linked, and the link is written on both sides.

### `activities.json`

`id`, `artist_id`, `title`, `venue`, `year`, `activity_type`, `role`, `source_url`,
`source_type`, `collected_at`, `created_at`. `flags` is present only when preprocessing
flagged `year_from_title`. Other year flags stay in `processed/activities.csv`.

### `links.json`

One object per `(artist_id, url)`. `id` is the ledger `link_id`, or a uuid5 of
`site-link`, the ledger id, and the URL when the cell is empty. `is_dead` is true
when the cell is `1`, `true`, or `yes`.

### `collaborations.json`

Scientists and engineers paired with a published person. Empty when the ledger has none.

### `background.json`

`id`, `artist_id`, `section`, `title`, `venue`, `year`, `role`, `source_url`,
`source_type`, `collected_at`.

### `frames.json`

One object per registry row, in file order. `id` is a uuid5 of `site-frame` and the
code, so a rebuild of the same registry does not mint a new id. Counts:

- `included_count`: distinct members whose membership resolves to this frame
- `published_count`: how many of those are in the published set
- `roster_count`: the greater of the declared `roster_count` and `included_count`
  (production's comment says the declared size is used when set; the code uses the maximum)
- `roster_size_declared`, `roster_size_source`: the size the programme states itself and the page
  that states it (`frames.yml`), or null when none is declared
- `coverage_pct`: `round(100 * included_count / max(roster_size_declared, included_count), 1)`, or
  null (unknown) when no size is declared (F4). `roster_count` is not used as the denominator,
  because it is written from what was collected and would read 100% by construction

`editions` breaks membership into edition years. An edition that declares its own size
(`roster_size_declared_by_edition` in `frames.yml`) also carries `roster_size_declared`,
`roster_size_source`, and `coverage_pct` = `round(100 * roster_count / max(roster_size_declared, roster_count), 1)`
for that edition; editions without a declared size carry none of these keys. `eligibility` is the F1–F5 judgement
as written in `frames.yml`. `status`, `stage`, and `last_fetched_at` are copied when present.

`giye publish` does not write these counts back into `frames.yml`. Production did.

### `coverage.json`

`generated_at`, `published_artists`, `ledger_artists`, `frame_count_active` (status
`active`), `frame_count_total`, a `frames` array with the same coverage numbers (`roster_count`, `included_count`,
`roster_size_declared`, `coverage_pct`), and
a `cadence` table only when `[publish.cadence]` declares one (label → what runs). Without it the
key is absent and the site states no maintenance schedule.

### `dataset_versions.json`

A list, oldest first: the version history. `version` comes from `[publish] dataset_version`
(default `0.2`, the production label). `content_digest` is the sha256 of the published
content (artists, activities, links, collaborations, background, frames, stubs, redirects)
with the build stamp removed. Publish reads the existing file, keeps every row as it is, and
appends a row only when the version string or the content digest differs from the last row.
So `released_at` is the UTC date the content first appeared, and a weekly rebuild of an
unchanged ledger leaves the file unchanged. `id` is a uuid5 of `site-version`, the label and
the digest. `doi` is null until a release sets one. `artist_count` is the published people.
`notes` includes the build timestamp of the build that added the row. Why: one version label
covers every weekly rebuild, so only the release date tells a reader which content a
citation names. Deleting the file starts a new history.

### `artist_stubs.json`

Object: `gy_id` → `HIDDEN_BY_REQUEST` or `WITHDRAWN`.

### `gy_redirects.json`

Object: retired `gy_id` → survivor `gy_id`.

### `citations.json`

```json
{
  "dataset": {"title": "", "author": "", "version": "", "released_at": "", "url": "", "year": 0, "accessed": "", "apa": "", "chicago": "", "bibtex": ""},
  "artists": [{"id": "", "title": "", "url": "", "year": 0, "accessed": "", "apa": "", "chicago": "", "bibtex": ""}]
}
```

The dataset URL is `<site_url>/data`. A person URL is `<site_url>/artist/<gy_id>`.
The author, title, and origin come from `[publish]` (`citation_author`, `dataset_title`,
`site_url`). The default author is `기예 Giye`. Publish fails when `site_url` is unset;
the reference archive sets `https://giye.org`. The BibTeX key is `giye_<id with hyphens turned to underscores>`,
or `giye_dataset`. Both citations name the current row of `dataset_versions.json`:
`[Dataset v0.2 of 2026-01-15]` and `[Artist record GY-000001, Dataset v0.2 of 2026-01-15]`,
where the date is that row's `released_at`. The year is the year of `released_at`. `accessed`
is the UTC build date. The site's cite dialog reads `author`, `version` and `released_at`
from this file (the artist page and `/data`) and uses the viewer's local clock for the
access date.

### Placeholders

`vocabularies.json`, `content_pages.json`, `content_revisions.json`, and `research.json`
are written as `[]` when missing, and left alone when they already exist. The production
script does the same so the front-end always has the files.

## Not in this snapshot

- `rim_order.json` (home-page entry-generation order: five-year bins from the
  earliest roster year). `giye publish` does not write it; `giye explore` writes it
  into the same directory (docs/EXPLORE.md), and the site rebuild runs both.
- The embedding flight file. `build_site_dataset.py` calls it at the end; stage 6 is not ported, so publish does not.

## Golden demo snapshot

`tests/test_demo_golden.py` runs `giye demo` offline on `examples/demo/` and compares
the site JSON, the processed venue tables, and the co-presence report with
`tests/golden/demo_snapshot.json`, after replacing timestamps and dates. When a change to
the snapshot is intended, regenerate the file with the test's own run and normalisation
and review the diff before committing:

```
GIYE_UPDATE_GOLDEN=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_demo_golden.py
```

Without `GIYE_UPDATE_GOLDEN=1` the test only compares.

## Static pages

`giye render` writes `<data>/site/html/index.html` and `<gy_id>.html` for each published
person, each stub, and each retired id. A fact that has a `source_url` gets a link.
A stub page has the id and the state, not the name. A retired id links to the survivor.
