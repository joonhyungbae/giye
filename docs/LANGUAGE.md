# Language module

Rules that depend on a script pair call `LanguageModule` (`giye.normalize.language`). The default is Korean–English: `[normalize] language_module = "giye.normalize.lang.ko_en:KoEn"`. `KoEn.load` is `KoreanEnglish.load`. A test selects `tests.toy_language:Toy` the same way.

`giye.normalize.language.load_language` imports `package.module:Class` and calls `Class.load(glossary=, cities=, reference=)`. Those three arguments are the optional paths in `[normalize]`. The return value must be a `LanguageModule`. `load` is not a member of the protocol. A class without it does not load.

`language_for(config)` caches one instance per process for that spec and those paths.

Swapping the module does not remove the Korean and English assumptions listed at the end.

## Protocol

`@runtime_checkable` class `LanguageModule`. Members, in source order.

### `name`

Property. A short id string. `KoreanEnglish.name` is `"ko-en"`. `Toy.name` is `"toy-qx"`. `giye.normalize.service` writes it into `manifest.json` as `language`.

### `personal_name(name: str) -> bool`

True for a bare personal name of the kind that collides across people. Callers: `giye.resolve.teams.person_like`, and through that the attachment rules A3, A4, A6 and the team test T1. `giye.resolve.candidates.x1_candidates` uses it to choose the "Korean" side of an X1 pair.

`KoreanEnglish`: the whole string is two to four Hangul syllables (`[가-힣]{2,4}`) and the first syllable is in `KOREAN_SURNAMES` (2015 census surnames with at least 2,000 bearers, plus 라, 계, 시). `Toy`: one word matching `q[a-z]{1,5}`.

### `venue_words`

Property. A `VenueWords` value. `giye.normalize.venue_names` reads it for V7b, V7c, V7d, and V8.

| Field | Type | Korean–English | Used as |
|---|---|---|---|
| `script` | string | `"가-힣"` | Character-class body of the non-Latin script. V7c (edition marker glued to a letter) and V7d (when the name is mostly this script). |
| `qualifiers` | tuple of strings | `외`, `등`, `일대`, `일원`, `etc`, `and others` | V7b. Trailing words that are not the name. |
| `edition_lead` | tuple of regexes | `제N회`, `Nth` | V7c. Marker at the start. A four-digit year is handled in the rule, not here. |
| `edition_tail` | tuple of regexes | `제N회` | V7c. Marker at the end. |
| `unstable_spacing` | bool | `true` | V7d. Ignore spaces in a name mostly written in `script`. |
| `building_parts` | tuple of regexes | 본관, 별관, halls, floors, … | V8. A part after a name in `script`. |
| `latin_building_parts` | tuple of regexes | `main building`, `annex`, `gallery N`, … | V8. A part after a Latin name. |

`Toy` sets `qualifiers=("zz",)`, edition regexes `vol N`, and `latin_building_parts=("wing",)`. It leaves `script` empty and `unstable_spacing` false.

### `name_keys(name: str) -> set[str]`

Romanized matching keys of one personal name. Empty when the name yields none.

`KoreanEnglish` calls `giye.resolve.names.hangul_name_keys`, then `giye.resolve.names.latin_name_keys` if that set is empty. Institution names do not use this; they use `romanise`. `Toy` returns `{name.casefold()}` or an empty set.

Callers: rule X1 (`giye.resolve.candidates.x1_candidates`: the Korean side's `name_ko` and the English side's `name_en`, and a Korean row's own Latin name), the X1 check of a manual `X1+E*` merge (`giye.resolve.decide.verify_merge_evidence`), and `giye name-keys`. X1 still chooses its two sides by script: the Korean side has Hangul in `name_ko` and passes `personal_name`; the English side has no Hangul in `name_ko` and Latin letters in `name_en`. So a new module's keys are used on that path, but a pair is only formed between a Hangul row and a Latin row. Attachment (A1–A4) does not use `name_keys`; it has its own roster keys (`giye.resolve.attach.name_keys`).

### `glossary`

Property. `dict[str, tuple[tuple[str, ...], ...]]`. A word in the source script maps to readings. Each reading is an ordered tuple of tokens in the other script. `[]` in the YAML is an empty reading (the word is dropped). File order is try order. V9 walks this dict (`giye.normalize.venue_names.hangul_bags`).

`load_glossary` reads the YAML. `KoreanEnglish.load` uses the `glossary` path from config, or `giye/normalize/data/ko_en/glossary.yaml`. `Toy` ignores the file and uses `{"qx": (("kwa",),)}`.

### `gazetteer`

Property. A `giye.normalize.gazetteer.Gazetteer`. Place names. V1 and L1 call `resolve` / `resolve_fragments`. V9 calls `place_en` and `is_place_token`.

`KoreanEnglish.load`: if `reference` is set, `Gazetteer.from_geonames`. Otherwise `Gazetteer.from_tables` on the `cities` path (or the packaged `cities.tsv`) plus packaged `countries/`, `admin1.tsv`, and `us_postal.txt`. `Toy` builds a one-city gazetteer and ignores the files.

The gazetteer type itself returns a "Korean region" for country `KR` and knows Hangul admin suffixes. That behaviour is in `giye.normalize.gazetteer`, not in the protocol. See below.

### `romanise(token: str) -> str`

One token, read in the other script. V9 uses it for the part of a Hangul name that the glossary and the gazetteer did not take (`giye.normalize.venue_names`).

`KoreanEnglish`: Revised Romanization, one Hangul syllable at a time (`giye.resolve.names.syllable_rr`), no sound change across syllables. Other characters stay. `Toy`: `casefold` and replace `q` with `k`.

The call site only romanises a character that matches `[가-힣]`. A non-Hangul script never reaches `romanise` on the V9 path, because `hangul_bags` returns nothing unless the string is mostly Hangul.

### `generic_titles` (optional, not in the protocol)

A frozenset of work-title bases that name no particular work, in the E3 normal form (`giye.resolve.evidence.norm_title` then `title_base`). The resolver reads it with `getattr`, so a module without it has none. E3 never takes a listed title as evidence. `KoreanEnglish`: `giye/normalize/data/ko_en/generic_titles.txt` (`untitled`, `무제`, `제목 없음`, `sans titre`, `無題` and other forms of "no title"). `Toy`: none.

## `tests/toy_language.py`

`Toy` implements every protocol member and `load`. `load` drops `glossary`, `cities`, and `reference`. It is enough for the language-module tests. It does not exercise X1, roster attachment keys, or a non-Hangul V9.

## What still assumes Korean and English

Counted by searching `src/giye` for `[가-힣]` and for `name_ko` / `name_en`, then reading the hit. `giye.normalize.language` and `giye.normalize.lang.ko_en` are the module. Everything else is a cost of porting.

### The name columns are `name_ko` and `name_en`

The ledger schema (`giye.ledger.schemas`) has those two columns, not a script-neutral pair. The same names are the match keys in:

- `giye.collect.base.Person` — Hangul in `name` is stored as `name_ko`; anything else as `name_en`.
- `giye.ledger.ledger._stored_name` — a missing `name_ko` is copied from `name_en`, or `"unknown"`.
- `giye.extract.service` — `[[extract.sources]]` matches on `name_ko` / `name_en`. `lang` on a source is only `ko`, `en`, or `mixed` (`giye.config`).
- `giye.resolve.cv` — HTML attributes `data-name-ko` and `data-name-en`.
- `giye.resolve.teams`, `giye.resolve.attach`, `giye.resolve.candidates`, `giye.resolve.service` — the pair is the person.
- `giye.publish.snapshot` and `giye.explore.rim` — sort and display `name_ko`, then `name_en`. A missing name becomes the Korean string `이름 미상`.
- `giye.collect.frames` — `name_en` is required on a frame; `name_ko` is optional.
- `giye.field` — ring labels are `label_ko` and `label_en`. Default `team_prefix` is `팀:`.

A third script has nowhere to go without a schema change.

### Hangul and Korean wording outside the module

| Module | What is fixed |
|---|---|
| `giye.resolve.names` | Revised Romanization tables, surname spellings, and given-name spelling sets. `hangul_name_keys` / `latin_name_keys` are what the Korean–English `name_keys` returns, so what X1 compares under the default module. |
| `giye.resolve.candidates` | `script_of` is Hangul or Latin. X1 treats a row as English-only when `name_ko` has no Hangul and `name_en` has Latin letters, and as Korean when `name_ko` has Hangul; the keys come from `language.name_keys`. |
| `giye.resolve.attach` | Roster keys are `hk:` plus the Hangul syllables, or Latin tokens. Not `language.name_keys`. |
| `giye.resolve.service` | `_names` keeps the Hangul syllables of a string, or the lower-cased string when there are none. |
| `giye.resolve.teams` | Splits a credit into `name_ko` / `name_en` with `[가-힣]`. A single member of two to four Hangul syllables is taken as the person even when `personal_name` is false. The member note is the Korean string `팀 구성원:`. |
| `giye.resolve.evidence` | Default team prefix `팀:`. Work titles use brackets that include `〈《「`. Shared-host list includes `blog.naver.com` and `tistory.com`. |
| `giye.normalize.rules` | `lang_of` returns `ko` / `en` / `mixed` from Hangul vs Latin letter counts. Birth phrases include `년생` and `출생`. Base phrases include `기반으로`, `거주`, `활동`. Year-in-title includes `년 이후`. Venue split includes `그리고` and `및`. L1's docstring says it returns a Korean region. |
| `giye.normalize.gazetteer` | First-level regions are Korean names (`서울`, `경기`, …). Incheon folds into `경기`. Other provinces become `기타`. Lookup strips `특별시`, `광역시`, `시`, `도`, `군`, `구`. A Hangul token is a place candidate. |
| `giye.normalize.venues` | V2 splits a Hangul name plus an acronym (`국립현대미술관 MMCA`). Online and funder regexes include Korean words (`온라인`, `재단`, `문화체육관광부`, …). When two spellings tie, the Hangul one sorts first. |
| `giye.normalize.venue_names` | `mostly_hangul` is a Hangul majority. V9 (`hangul_bags`) runs only on a mostly Hangul string and refuses a string that contains a Latin letter. A place token ending in `시·도·군·구` is not consumed when the next character is `립` (`서울시립`). Date fragments match `월`, `일`, `년`. `romanise` is called only on Hangul characters. |
| `giye.extract.apply` | Title containment uses a floor of 4 characters when the shorter title has Hangul, otherwise 6. Upcoming roles in the current year get `(예정)`. Private-title regex includes `장학금` and `최우수생`. |
| `giye.extract.text` | `looks_garbled` counts `[가-힣A-Za-z0-9]` as readable. |
| `giye.explore.rim` | Staff-role regex mixes Korean words (`기획`, `심사`, `멘토`, …) with English. Undated generation label is `진입 연도 미상` / `Entry year unknown`. Dated bins use `진입`. |
| `giye.config` | Default `citation_author` is `기예 Giye`. |
| `giye/fields/korean-media-art/field.toml` | The inherited tag lists, team-word regex, and event patterns are that field's vocabulary. `[tags] inherit = false` on a new file drops the tag lists. It does not drop the rows in this table. |
| `giye/normalize/data/ko_en/` | Packaged glossary, city table, and country names. Replaced only when `[normalize]` sets `glossary`, `gazetteer`, or `reference`. |

`giye.normalize.venue_names` does take qualifiers, edition markers, spacing, and building parts from `venue_words`. The Hangul tests around them stay in that file.
