# Field file and frames file

The field file is the path in `[paths] field`. `giye.field.load_field` reads it. The frames file is the path in `[paths] frames`. `giye.collect.frames.load_frames` reads it. Programme names, team words, tag lists, and ring aliases live in the field file. The package does not compile one field's programmes into the rules.

The Korean media-art file ships at `giye/fields/korean-media-art/field.toml`. An empty tag list inherits that vocabulary. Event patterns, attachment families, and ring aliases are never inherited.

## Field file

Unknown keys on a table are ignored. A value of the wrong type is an error.

### `[resolve]`

| Key | Type | Default | Required | Read by |
|---|---|---|---|---|
| `team_prefix` | string | `"팀:"` | no | Rule E4 and the ring (`giye.resolve.evidence`, `giye.resolve.service`, `giye.resolve.teams`, `giye.explore.rim`). A team credit is written `<prefix> <name>`. |
| `team_words` | string (one regex) | `""` | no | Rule T1 (`giye.resolve.teams.team_like`). Case-insensitive. A name that matches is a group. A false positive only skips a merge. Empty inherits the shipped list when `[tags] inherit` is true; with inheritance off, an empty pattern matches nothing. |

### `[resolve.events]`

Table. Key: frame-code prefix. Value: regex. First matching prefix wins. A trailing `-YYYY` is removed from the frame code before the match. Order is file order.

`giye.resolve.evidence.event_pattern` reads the merged table: this table, then `[resolve.event_patterns]` in `giye.toml`. A prefix in the config replaces the pattern for that prefix.

These rows are not inherited. A field that omits the table has no E2 phrases of its own.

```toml
[resolve.events]
EXAMPLE-RESIDENCY = '예시 ?레지던시|example residency'
```

### `[attach]`

Rule A1. A trailing `-YYYY` is removed, then the first matching prefix, then an exact base, then the base itself. `giye.field.frame_family`. Not inherited.

`[[attach.prefix]]` is an array of tables. `prefix` and `family` are required strings. A `reason` key is not read.

```toml
[[attach.prefix]]
prefix = 'APE'
family = 'APE'
```

`[attach.exact]` is a table of base code = family string.

```toml
[attach.exact]
UNFOLD-X = 'SFAC-ARTTECH'
```

### `[tags]`

| Key | Type | Default | Required | Read by |
|---|---|---|---|---|
| `inherit` | boolean | `true` when the key is absent | no | `giye.field.Field.resolved`. `false` keeps empty lists empty. The shipped file sets `false` because it is the source. |
| `home_pattern` | string (regex) | `""` | no | `giye.publish.snapshot.region_tags`. A home-country match with no finer tag becomes `fallback`. |
| `fallback` | string | `""` | no | Same function. Used when the text matched nothing, including a home-country match with no needle hit. |
| `screening_tag` | string | `""` | no | `giye.normalize.rules.medium_tags` (M1). A public `screening` row also counts as this tag. |
| `medium_min_rows` | integer ≥ 1 | `2` | no | M1. How many public rows must name a tag. When `[[tags.medium]]` is empty and inheritance is on, the shipped minimum is used with the shipped word list. A local minimum is kept only when `[[tags.medium]]` is non-empty. |

`[[tags.region]]`: `needle` and `tag`, both required strings. `giye.publish.snapshot.region_tags` appends `tag` when `needle` occurs in `country` + `region`. File order. Empty inherits the shipped list.

`[[tags.medium_guess]]`: `pattern` (regex) and `tag`, both required. `giye.publish.snapshot.guess_medium` tests the artist row's field and category. Empty inherits.

`[[tags.medium]]`: `pattern` and `tag`, both required. M1 tests title, role, and a `strand=` note. Empty inherits. Stored as `(tag, pattern)`.

When `inherit` is true, an empty value is replaced from the shipped file for: `team_words`, `region`, `home_pattern`, `fallback`, `medium_guess`, `medium`, `screening_tag`, and (only if `medium` is empty) `medium_min_rows`. One non-empty list blocks inheritance of that list only.

### `[rim]`

Not inherited.

`[[rim.alias]]`: `membership`, `frame`, and `edition` are required strings. `reason` is stored and not used by the resolver. `giye.field.edition_alias` returns `(frame, edition)` when the membership code matches and `frame` is in the registry. `giye.publish.snapshot` and `giye.explore.rim` call it. There is no programme name in the resolver code.

```toml
[[rim.alias]]
membership = 'APE-2025'
frame = 'APE-CURRENT'
edition = '2025'
reason = 'The current-year roster frame is APE-CURRENT.'
```

`[[rim.family]]`: `prefix` and `family` are required. `label_ko`, `label_en`, and `reason` are optional. `giye.field.rim_family` maps `prefix` and `prefix-…` to `family` (rule R5). `giye.field.rim_label` returns the two labels when either is set; a missing label falls back to the family code.

```toml
[[rim.family]]
prefix = 'APE'
family = 'APE'
label_ko = '에이프캠프'
label_en = 'APE CAMP'
```

### `[extract]`

| Key | Type | Default | Required | Read by |
|---|---|---|---|---|
| `prompt` | path, relative to the field file | packaged `giye/extract/prompts/cv_extract_v1.txt` | no | `giye.extract.prompt`. The file must exist. Its SHA-256 is part of the extraction cache key. |

## Frames file

YAML. Top level is a mapping. `frames` must be a list. `giye.collect.frames.load_frames` checks that each judgement is complete. It does not re-decide F1–F5. The sentences in the file are the record.

| Key | Type | Default | Required | Read by |
|---|---|---|---|---|
| `version` | integer | `1` | no | Stored on the registry. |
| `updated_at` | string | `""` | no | Stored on the registry. |
| `frames` | list | — | yes | One programme per entry. Duplicate `code` is an error. |

`giye.publish.snapshot` validates with the loader, then reads the raw YAML again so keys the dataclass drops still publish (`stage`, `last_fetched_at`, `edition_labels`).

### One frame

| Key | Type | Default | Required | Notes |
|---|---|---|---|---|
| `code` | string | — | yes | Non-empty. The roster and E2 use this code. |
| `name_en` | string | — | yes | |
| `name_ko` | string | `""` | no | |
| `source_url` | string | — | yes | Must start with `http://` or `https://`. |
| `roster_count` | integer | unset | no | The roster size the site displays (the greater of this and the membership count). Not a coverage denominator: in practice it is written from what was collected. |
| `roster_size_declared` | integer | unset | no | A roster size the programme states itself (its own page, catalogue, or press release). Coverage is members recorded / max(this, members recorded). Unset or zero: coverage is null (unknown). |
| `roster_size_source` | string | `""` | with `roster_size_declared` | The http(s) page that states the declared size. A declared size without it is refused. |
| `roster_size_declared_by_edition` | mapping | unset | no | Edition (the year in a membership code such as `CODE-2024`) → `{size, source}`: a size one edition states itself and the http(s) page that states it. `size` must be a positive integer and `source` is required. That edition's coverage is its members / max(size, members). Edition sizes are not summed into `roster_size_declared`: the frame counts a person once across editions, edition sizes count a returning person in each. |
| `included_count` | integer | unset | no | At collect time, the number of roster rows. Publish overwrites the snapshot count from membership. |
| `years_covered` | string | `""` | no | A single `YYYY` is the edition when a membership code equals the frame code. |
| `status` | string | `""` | no | Passed through to the snapshot. The loader does not interpret it. |
| `eligibility` | mapping | — | yes | F1–F5. |

Optional keys the loader drops and the snapshot still copies when present: `stage`, `last_fetched_at`, `edition_labels` (mapping of edition → label).

### `eligibility`

| Key | Type | Required | Rule |
|---|---|---|---|
| `decision` | string | yes | One of `included`, `excluded`, `adjacent`, `planned`, `no_public_roster`. The loader checks the word and does not re-decide F1–F5. Collection and publication admit only `included` and `adjacent`. |
| `f1_purpose` | string | yes | The programme's own public text states a purpose in the field. The document is the evidence, not the institution's reputation. |
| `f2_cohort` | string | yes | Participants are fixed by an open call, jury, selection, award, or residency. A curated or rented exhibition does not qualify. |
| `f3_territory` | string | yes | The programme is held in the archive's territory. `f3_korea` is accepted as an alias and stored as `f3_territory`. The sentence is not compared to `[archive] territory`. |
| `f4_roster` | string | yes | The participant list is in a public record (official page, catalogue, or press release). |
| `f5_period` | string | yes | The programme recurs. The production census said editions since 2010, at least twice, with a single edition only when it represents the field that year. That bound is written in this sentence. It is not hard-coded. |
| `note` | string | no | Why the decision is what it is. The demo uses it when a frame is adjacent or excluded. |
| `judged_at` | string | no | Stored. Not checked. |

`included` and `adjacent` are collected and published. An `adjacent` frame is published as an adjacent strand: its memberships stay on the site, and `eligibility.decision` stays `adjacent`. `excluded`, `planned`, `no_public_roster`, and any other value are not collected. A collector for that frame is skipped with one notice and does not fetch. Publish omits that frame's memberships. `frames.json` and `coverage.json` still list every frame; `frames.json` keeps `eligibility.decision`.

`planned` and `no_public_roster` are accepted because the production sampling-frame page publishes them. They are not a third admission state.

The five criteria were fixed before any candidate programme was examined. Inclusion is the text recorded here, not a decision the software makes after seeing who was selected. The admission set is which of those recorded words are collected.

### Re-collection

Collecting a frame again, live or with `giye collect --from-snapshots`, is idempotent against the register those pages built. It adds rows that did not exist and fills empty fields. Nothing else changes (`Ledger.apply_roster`).

- An existing activity or membership row keeps its `collected_at`. A replayed page's fetch date does not re-date it.
- An existing activity row keeps its `activity_id` and every non-empty value (`publishable`, `reviewer_note`, `source_url` and the rest). Only its empty columns are filled.
- A new appearance row is matched to the person's existing rows by frame and edition (`origin`), person and normalised title, after an exact `activity_id` match. When one person appears twice in one edition with the same credit, the result is one row.
- `Person(activity=False)` records the membership and no frame-coded activity row, for example a creator listed without a cohort year. Extra activity rows are still written.
- A `source_url` stated on an activity, even an empty one, is not replaced by the roster page.
- `Person.person_note` is written on a person the row creates. An existing person's note is changed only when `person_note_existing` is true, and then only segments not already present are appended (exact match after whitespace normalisation). A `members=` list is written on a record the row creates, or extended on one that already has a list; it gains missing names and keeps its spelling when it gains none. An existing record without a list is left alone.
- A collector's `website` and `websites` are typed as the production collectors typed them: a host in `SOCIAL_HOSTS` (`giye.collect.fetch`) is `social`, YouTube and Vimeo are `video`, GitHub is `repository`, anything else `website`.
- `expand_members` expands only the teams of the editions the collector wrote (`expand_teams(frames=...)`). Team expansion writes the member credit note (`팀 구성원: …`) only on a member record it creates; an existing person's note is left as it is.
- A legacy snapshot line whose `url` is `"POST <url>"` and that has no `method` replays as a POST to `<url>` with an unrecorded body.

## Writing an extraction prompt

Point `[extract] prompt` at a UTF-8 text file next to the field file. The whole file replaces `cv_extract_v1.txt`. The model reply is parsed by `giye.extract.schema.parse_extraction`. A reply that fails validation is rejected as a whole. That artist is skipped.

### What the schema requires

The reply is one JSON object. The only key is `activities`, an array. Extra keys are rejected.

Each activity has exactly these fields, all required:

| Field | Type | Constraint |
|---|---|---|
| `title` | string | |
| `venue` | string | Empty string when the line names no place. |
| `year` | integer | Not a string. A line with no year cannot be a row. |
| `activity_type` | string | One of `solo_exhibition`, `group_exhibition`, `screening`, `performance`, `festival`, `online_release`, `award`, `residency`, `other`. Any other value rejects the reply. |
| `role` | string | Empty string when the line names no role. |
| `cv_section` | string | One of `exhibition`, `performance`, `screening`, `festival`, `award`, `grant`, `residency`, `commission`, `collection`, `project`, `workshop`, `talk`, `publication`, `press`, `education`, `employment`, `teaching`, `scholarship`, `service`, `other`. |
| `upcoming` | boolean | |
| `source_id` | string | Must be an id that was sent with the documents (`--- CV document source_id=… ---`). |

`giye.extract.schema.without_unknown_sources` drops a row whose `source_id` was not in that set. The schema does not check that the year or the venue string occurs in the CV text.

`giye.extract.apply` treats `cv_section` values `education`, `employment`, `teaching`, `press`, `scholarship`, and `service` as private (`publishable=no`). A replacement prompt has to keep using those names for those kinds. Otherwise those rows become public.

### No invention, and one event

The default prompt says: copy facts only from the documents; never invent venues or years; do not emit a heading, a biography sentence, or an undated list item. Those rules are prompt text. The schema enforces the field set, the enums, an integer year, and (after validation) a known `source_id`. It does not read the CV to prove a venue.

The default prompt also says: a Korean CV and an English CV often repeat one event; output that event once, preferring the Korean wording. The schema does not collapse duplicates. `giye.extract.apply.same_activity` later folds two rows in the same year when the normalised titles are equal, or one contains the other. The shorter title must be at least 4 characters if it contains Hangul, otherwise 6. A replacement prompt should still ask for one row per event. The preferred language is the field's choice.

A year range is one row. The default prompt uses the start year. Skip a line with no year, because `year` has to be an integer.

### What in `cv_extract_v1.txt` is Korean media art

These parts describe that field. A replacement may rewrite them:

- The opening line ("public archive of Korean media / art-technology artists").
- "preferring the Korean wording" as the choice of which language to keep. Keep the one-row rule; change which wording wins.
- Bracket examples `<>` and `《》`, and the instruction to keep a work title after ` / `.
- The parenthetical glosses on `activity_type` (two-person show, concerts, grants won, what counts as `other`). The enum tokens stay.
- Korean role examples (`작곡`, `기술 협업`, `심사위원`).
- The word `예정` as an example of upcoming. The field is a boolean.

These parts are the schema and the apply step. A replacement has to preserve them:

- The eight field names and their types.
- The `activity_type` and `cv_section` tokens listed above.
- `source_id` copied from the document header, not invented.
- No extra JSON keys.
- One integer year; no undated row.
- Private sections named `education`, `employment`, `teaching`, `press`, `scholarship`, `service`.
- Facts taken from the documents given in the call.

`giye.extract.apply` also appends `(예정)` to the role when `upcoming` is true and the year is the current year. That spelling is in code, not in the prompt file.
