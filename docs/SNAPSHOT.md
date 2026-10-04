# Site snapshot

`giye publish` writes `<data>/site/*.json` from the ledger and from `<data>/processed`
when that directory exists. The shapes match `scripts/build_site_dataset.py` in the
production archive. The web front-end reads these files and needs no database.

`citations.json` is not one of those production files. The live site builds the same
APA, Chicago, and BibTeX sentences in the browser (`CiteDialog`). They are stored
here so a static page can show them. Keys on the other files are unchanged.

Timestamps (`created_at`, `generated_at`) are the UTC time of the build. `collected_at`
is the calendar date on the ledger row.

## Who is published

A person is published when all of these hold:

- not listed as `scope=out` in `scope.csv`
- `cv_link_ok` is `yes`, or the person has a frame membership
- `source_url` starts with `http` (a roster membership or the frame's own URL can fill an empty one)
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
those four. A future year marked `upcoming` is dropped. Korean and English repeats of
the same line collapse.

## Files

### `artists.json`

One object per published person, in name order (`name_ko`, then `name_en`).

| Key | Meaning |
|---|---|
| `id` | Permanent `gy_id` |
| `name_ko`, `name_en`, `aliases` | Names. A missing `name_ko` falls back to `name_en`, then the production placeholder `이름 미상` |
| `type` | Always `individual`, including a team row |
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
| `status` | Always `PUBLISHED` in this file |
| `source_url`, `source_type`, `collected_at` | Provenance of the person row |
| `external_ids.ledger_id` | Internal id |
| `created_at`, `updated_at` | Build time, or the ledger's `updated_at` |

`frame_editions` follows the registry. An edition alias in the field file maps a membership code to a frame and an edition when that frame is registered. A code equal to a registry row uses `years_covered` when that cell is a single year. Otherwise the longest matching registry code wins, and a trailing `-YYYY` is the edition. `role` is the roster activity's role for that membership code (the last such row wins).

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
- `coverage_pct`: `round(100 * included_count / roster_count, 1)`, or null when the roster size is 0 (F4)

`editions` breaks membership into edition years. `eligibility` is the F1–F5 judgement
as written in `frames.yml`. `status`, `stage`, and `last_fetched_at` are copied when present.

`giye publish` does not write these counts back into `frames.yml`. Production did.

### `coverage.json`

`generated_at`, `published_artists`, `ledger_artists`, `frame_count_active` (status
`active`), `frame_count_total`, a `frames` array with the same coverage numbers, and
the production `cadence` sentences (weekly link check, monthly collectors, quarterly intake).

### `dataset_versions.json`

One object. `version` comes from `[publish] dataset_version` (default `0.2`, the
production label). `id` is a uuid5 of `site-version` and that label. `released_at` is
the build date. `doi` is null until a release sets one. `artist_count` is the published
people. `notes` includes the build timestamp.

### `artist_stubs.json`

Object: `gy_id` → `HIDDEN_BY_REQUEST` or `WITHDRAWN`.

### `gy_redirects.json`

Object: retired `gy_id` → survivor `gy_id`.

### `citations.json`

```json
{
  "dataset": {"title": "", "version": "", "url": "", "year": 0, "accessed": "", "apa": "", "chicago": "", "bibtex": ""},
  "artists": [{"id": "", "title": "", "url": "", "year": 0, "accessed": "", "apa": "", "chicago": "", "bibtex": ""}]
}
```

The dataset URL is `<site_url>/data`. A person URL is `<site_url>/artist/<gy_id>`.
The author, title, and origin come from `[publish]` (`citation_author`, `dataset_title`,
`site_url`). The default author is `기예 Giye` and the default origin is `https://giye.org`,
matching the live cite dialog. The BibTeX key is `giye_<id with hyphens turned to underscores>`,
or `giye_dataset`. The version in both citations is the dataset version. The artist page
in the production web app hard-codes version `1.0`; this file uses the dataset version.

`accessed` and the dataset year are the UTC build date. A person citation's year is the
UTC year of that row's `updated_at`. The browser dialog uses the viewer's local clock.

### Placeholders

`vocabularies.json`, `content_pages.json`, `content_revisions.json`, and `research.json`
are written as `[]` when missing, and left alone when they already exist. The production
script does the same so the front-end always has the files.

## Not in this snapshot

- `rim_order.json` (home-page entry-generation order in `build_rim_order.py`:
  five-year bins from the earliest roster year). It is input to the web
  visualisation and is left with that front-end.
- The embedding flight file. `build_site_dataset.py` calls it at the end; stage 6 is not ported, so publish does not.

## Static pages

`giye render` writes `<data>/site/html/index.html` and `<gy_id>.html` for each published
person, each stub, and each retired id. A fact that has a `source_url` gets a link.
A stub page has the id and the state, not the name. A retired id links to the survivor.
