# Rule registry

Every decision the pipeline makes is taken by one of these rules and can be traced to it in the
output (rule IDs are written next to derived values and in the audit files). Status: **ported**
(in this repository, tested), **planned** (exists in the Giye archive, being ported).

## Sampling frame (stage 1)

| ID | Rule | Status |
|---|---|---|
| F1 | Purpose: the programme's own public description states a purpose within the field. In the Korean media-art archive that is art–technology (or art–science) convergence, media art, new media, or digital art. The programme's document is the evidence, not the institution's reputation. | ported |
| F2 | Cohort: participants are fixed by an open call, jury, selection, award, or residency. A curated or rented exhibition does not qualify. | ported |
| F3 | Territory: the programme is held in the archive's configured territory (`archive.territory`). Production recorded the sentence as `f3_korea`; the loader accepts that key as an alias of `f3_territory`. | ported |
| F4 | Roster: the participant list is verifiable in a public record (official page, catalogue, or press release). Coverage is members recorded / roster size. | ported |
| F5 | Period: the programme has editions since 2010 and recurs at least twice. A single edition counts only when it represents the field that year. The 2010 bound is the production census rule and is stored in the frame's own sentence; the software does not hard-code a year. | ported |

The five criteria were fixed before any candidate programme was examined (adopted 2026-09-20), so inclusion is not a judgement made after seeing who was selected. `giye.collect.frames` checks that each `frames.yml` entry records a decision (`included`, `excluded`, `adjacent`, and the production verdicts `planned` and `no_public_roster`) and a sentence for F1–F5. It does not re-decide them.

Collection policy (not an F-rule): robots.txt is checked before every request and before every redirect hop (RFC 9309). The matcher is the longest matching Allow or Disallow (§2.2.2), on the product token in the User-Agent (§2.2.1). HTTP 2xx is parsed. HTTP 4xx is `unavailable_allowed` (the URL may be fetched). HTTP 5xx, a timeout, or a network error is `unreachable_disallowed` for this process only; the next process fetches robots.txt again. A parsed disallow raises `RobotsDisallowed`. An unreachable file raises `RobotsRefused`. The hop is not sent. `/robots.txt` is always allowed. When the live page is gone (HTTP 404 or 410, or the connection fails), an existing Internet Archive capture may be kept. Giye never asks the Archive to make a new capture. If robots.txt disallows the URL, the existing capture's URL and timestamp are recorded (`archive_link_only`, `direct_failure=robots`) and the bytes are not downloaded or kept. That is the only behaviour for a disallowed host. An unreachable robots.txt is recorded as `robots_disallowed` with reason `robots_unreachable` and is not sent to the Archive. Platforms whose terms forbid collection (Instagram, Facebook, LinkedIn, X, Threads, TikTok) are not requested.

## Extraction (stage 2)

These decisions come from `extract_cvs_llm.py` and `apply_cv_extractions.py`. Production did not give them E/F/P/V ids. `giye.extract` keeps the same outcomes. A team is still refused at registration by T1 (`team_like`): a member's personal CV is not filed on the team unless `[extract] allow_team` is set.

| Decision | Rule |
|---|---|
| Schema | One response is a list of activities (`title`, `venue`, `year`, `activity_type`, `role`, `cv_section`, `upcoming`, `source_id`). An extra field, a type outside the production enums, or a year that is not an integer rejects the whole response. |
| Unknown source | A row whose `source_id` is not one of the CV documents just read is dropped. Production does not also check that the year or the venue string occurs in the CV text. |
| Private rows | `publishable=no` for sections education, employment, teaching, press, scholarship, and service, and for a title or role that matches the production scholarship / peer-review pattern. The row stays in the ledger. |
| Upcoming | A future year marked upcoming is `publishable=no`. This year's upcoming row stays visible and the role gains `(예정)`. A past year marked upcoming is shown without that suffix: the label has gone stale. |
| Korean / English repeat | Inside the rows one extraction is adding, two lines with the same year, the same normalised venue, and titles that match or contain each other (Hangul floor 4 characters, Latin floor 6) collapse to the first row. |
| Public record wins | If another origin already records the same title and year and that row is not self-reported, the CV copy is skipped. A self-reported row (`SELF_SUBMITTED`, or a note starting `from_arko_career_text`) is set `publishable=no` and noted `superseded_by_cv`. |
| Owner | A `cv:<source_id>` row belongs to the `ledger_id` on that CV source, not to the id written on the extraction file. |
| Superseded file | An extraction file whose id is not an artist is skipped when a live artist's file already covers the same sources. |
| Content id | The activity id is the ledger uuid5 (`giye.ledger.ids`). The same CV line keeps its id across applies. A repeated key in one write gets an ordinal. A dropped line is not given a new id. |
| Re-read | The CV is extracted again when a source content hash, the prompt SHA-256, or the model no longer matches the extraction file. The replay cache is keyed by the content hash of the CV texts (source id order), the prompt hash, and the model. |

## Identity (stage 4)

| ID | Rule | Status |
|---|---|---|
| E1 | Two records are the same person when they share a personal website. The key is the host, or host plus path on a shared platform (a blog host, a portfolio host, a video host). `www` and a trailing slash do not make a second site. Social links are not a personal site. The rows also need an overlapping name (Hangul with spaces removed, otherwise the lower-cased string). | ported |
| E2 | … when one's CV lists the other's roster appearance: the CV text matches that frame's event pattern and the CV year is within one year of the edition. A frame code ending in `-YYYY` uses that year (`DAVINCI-2014`). A code with no year uses the years on that frame's roster activities. Patterns are the production programme list plus `[resolve.event_patterns]`. | ported |
| E3 | … when a bracketed work title (`〈…〉`, `<…>`, `《…》`, and the same family) on one roster row also appears on the other's roster row or CV, in the same year ±1. The normalised title is at least 3 characters. An unbracketed title is not a work (that is E2). | ported |
| E4 | … when both roster rows credit the same team in the role, written `팀: <name>`. The normalised team name is at least 2 characters. This joins two rows of one candidate pair; it does not collapse every member of a team into one person. | ported |
| T1 | A record whose name looks like a team or collective is never merged with a person. A team row has `members=` or `rep=` in the note, two or more person-shaped aliases, or a team word in the name (`스튜디오`, `collective`, `lab`, …). Exactly one side being a team blocks the merge. Two team rows may still merge with each other on E1. X1 drops a pair when either side is a team. | ported |
| X1 | Hangul and Latin spellings are candidate matches when their romanized keys intersect (`giye.resolve.names`). A candidate is merged only with E1–E4 evidence, recorded as `X1+E1` … `X1+E4`. No evidence queues the pair (`possible_same_person`). A shared frame code, a collector identity pin, or a team row drops the pair. A Korean row that already has a matching Latin name is not paired again. | ported |

A same-script exact name (Unicode NFC, spaces removed, case folded) that no rule accepts is queued the same way, reason `possible_same_person`, detail `rule=same_script_exact`. It is not merged. A pair already named by an open item is not queued again. A done item whose pair was never actually joined is reopened (`reopened=wrong_close`) unless a person recorded `decided=same` or `decided=different`.

Every merge stores `merge_evidence=` (the rule and the concrete website key, CV line, work title, or team) and `rule=` on the kept row, and retires the dropped `gy_id` through `giye.ledger.merge`.

The synthetic demo fires each of these once. E1–E4 are same-script. X1 finds `kim/haneul` for 김하늘 on one programme and Haneul Kim on another; the English CV names the first programme in the same year, so the stored rule is `X1+E2`. A different Kim Haneul, on a third programme and with no evidence, is queued. T1 leaves the person 배수아 and the team row of the same name unmerged. See `examples/demo/EXPECTED.md`.

## Ledger invariants (stage 3)

These are not sampling or identity rules. They are how the ledger keeps a fact attached to a source and to a permanent id. `giye.ledger` implements them.

| Invariant | Why |
|---|---|
| A `gy_id` is never reused and never renumbered from the row's position. The next id is one past the highest ever issued, retired ids included. | A published URL must keep working. Filling a gap, or numbering by the current sort order, would give someone else's page to a new row. |
| A merge retires the dropped `gy_id` into `gy_retired.csv` (`gy_id` → `merged_into_ledger_id`) and rewrites older retirements to the final survivor. | A chain of merges still redirects in one step. `Ledger.redirects()` maps each retired id to the survivor's current `gy_id`. |
| A merge requires an evidence string and a rule id (E1–E4 or whichever rule decided). | A retired id without a reason cannot be audited. The note on the kept row stores `merge_evidence=` and `rule=`. |
| An activity id is uuid5 of a fixed key (person, source, normalised title, year, type, venue, and the frame code for a collector row). The same key in one write gets an ordinal. | The same fact must keep its id across runs. Case and punctuation in a title are not a different fact; a different venue spelling is, because a tour has one row per stop. |
| A CV-derived row (`origin` `cv:<source_id>`) belongs to the owner of that CV source. | The extraction file keeps the ledger id it was made for. A merge updates `cv_sources.ledger_id` and can leave the activity on the retired id. The source's owner is the person the row belongs to. |
| Every ledger write of an existing file is copied to `data/work/backups/<file>-<YYYYMMDD>-before-<task>.csv` first. | A script must be able to put the previous bytes back. The task name says which write replaced them. |

## Derived values (stage 5)

Nothing in this stage edits the ledger. Checks become flags. Derivations become rows in `artist_attributes.csv`, each with the rule and the evidence. A value the artist row already holds wins (P5): `country` and `region` are derived only when both are empty, `active_since` only when that cell is empty, medium tags only when both `field` and `category` are empty. Birth year has no ledger column; it is only a derived row.

Place names, generic institution words, and romanisation come from a language module (`giye.normalize.language`). The Korean–English module is the default. Its glossary and compact gazetteer are files. A GeoNames tree is optional (`[normalize] reference`) and is not shipped.

| ID | Rule | Status |
|---|---|---|
| P1 | Year checks, flags only. Y0 `year_missing`: no year. Y1 `year_range`: outside 1900 … this year + 2. Y2 `year_from_title`: the year is a period the title names ("Art After 1945"). A start year of a range is not flagged. | ported |
| P2 | Text normalisation: NFC, control characters removed, single spaces. A comparison key is case-folded letters and digits. `lang` is ko / en / mixed by the share of Hangul and Latin letters. | ported |
| P3 | Place of a venue (V1) and institution entities (V2–V9). | ported |
| P4 | A roster row is its frame edition. A CV row links to an edition the artist is on when the title or venue names that frame's event in that edition's year. Rows are not merged. Production's comment calls this check E1; that id is also the same-person website rule. The link stores the frame code. | ported |
| P5 | Derived artist attributes. The ledger value wins, as above. | ported |
| B1 | Birth year: exactly one plausible year (1900 … this year − 15) among birth phrases in the first 4,000 characters of the artist's own CV. Two years, or a team row, leave it empty. | ported |
| L1 | Base country (and Korean region) from a "based in" / "lives and works" phrase, or the Korean equivalents, resolved through the gazetteer. The first CV that names a place wins. | ported |
| A1 | `active_since`: earliest year among public practice rows (exhibition, screening, performance, festival, award, residency, release) that carry no year flag and are not marked upcoming. | ported |
| M1 | A medium tag when at least two public rows name it in the title, role, or `strand=` note. A screening counts as 영상. The word list is the production media-art vocabulary. | ported |
| V1 | A venue fragment is a place only when the whole fragment is a place name. A town inside an institution name is not a place. | ported |
| V2 | Split on commas, slashes, pipes, middots, semicolons, and spaced hyphens. A parenthetical, or a Hangul name followed by an acronym, is an alias candidate. | ported |
| V3 | First match: place, city plus its own country code, online, funder, institution (at least two letters), otherwise unclassified. | ported |
| V4 | Entity key: case-folded, quotes and bracket characters removed, a leading or trailing "the" removed. No fuzzy match. | ported |
| V5a | A parenthetical Hangul/Latin pair written by two or more artists joins those two institution keys. | ported |
| V5d | An acronym that is a subsequence of a Latin name's initials (function words dropped) joins them, when two or more artists write the pair. A mixed-case house style is read by its capitals (V5d′: SeMA → SMA). | ported |
| V5e | If a candidate component contains two or more distinct Hangul keys, the whole component stays unmerged. | ported |
| V5f | One artist is enough for a Hangul/Latin pair when both names are specific (a proper word, not a date). Applied after V5e, and never into a group that would then hold two Hangul names. | ported |
| V7a | A work title in 《》〈〉<>「」『』 is not part of the name. V4 would drop the brackets and keep the title. | ported |
| V7b | A trailing qualifier (외, 등, 일대, 일원, etc., and others) is not the name. | ported |
| V7c | An edition marker (leading 제N회 or Nth, a glued or separate 19xx/20xx year) is not the name. A series is one entity; co-presence is still year-bound. | ported |
| V7d | A mostly-Hangul name ignores spaces. Korean spacing in names is not stable. | ported |
| V7e | Two Latin names with the same bag of words are one entity when the bag holds a proper word. Order is free only when a place anchors the bag. of/the/and are dropped; centre/center; a plural -s only on generic words. Generic words alone name no place, so Museum of Modern Art and Modern Art Museum stay apart. The romanisation-tolerant skeleton is not used here: it would join ACC/AAS, BUG/Book, and MMCA/MCA. | ported |
| V8 | A part of a known entity is that entity: a Hangul name plus a building or room word (본관, 서울관, 창고동, 전시실, …), a Latin name plus main building/annex/lobby/floor, or an acronym plus a place when that acronym's own rows are in that city (ZKM Karlsruhe, not a chain whose rows sit elsewhere). | ported |
| V9 | A Hangul name and a Latin name are one entity when the Hangul name, read with the glossary, the gazetteer, and syllable romanisation for the rest, gives the same bag as the Latin name, the bag holds a proper word, and every link in the connected component uses that one reading. Two different readings in one component are ambiguous and are not merged. The first reading that matches anything is the one used (현대 = contemporary before modern). | ported |
| G1 | An ISO alpha-2 or alpha-3 code is a country when the fragment is that code (`KR`, `KOR`). | ported |
| G3 | An uppercase two- or three-letter token after a place inherits that place's country (and, for Korea, its region). `Los Angeles, CA` stays in the US. | ported |
| G6 | A two-letter US postal abbreviation that is not itself a country code (`NY`) is the US, when G3 had no previous place. | ported |

The synthetic demo's venue strings exercise V7a (an exhibition title in 《》), V7b (the qualifier 외 on 서울시립미술관), V8 (서울시립미술관 전시실, a hall of that museum), and V9 (that name with Seoul Museum of Art). The audit records the V8 and V9 joins. V7a–d do not, because they change the key before the join; the demo summary still counts those trimmed spellings under V7. See `examples/demo/EXPECTED.md`.

G2, G4, and G5 are not named in the production source. The audit header says G1–G6. The remaining lookup steps, in order, are a Korean first-level region in Hangul, a city (the more populous row wins), an admin1 name, then a country name. A Korean administrative suffix is stripped before the second try (서울시 → 서울). The packaged gazetteer is a compact table. The full GeoNames city list is CC BY 4.0 and is not shipped; `[normalize] reference` loads it when it is present.

## Publication (stage 7)

These are the production site builder's decisions. They do not have E/F/P/V ids of their own. F4 is the coverage ratio the snapshot prints.

| Decision | Rule |
|---|---|
| Who is a page | In scope, http(s) source, on a roster or `cv_link_ok=yes`, status empty / `PUBLISHED` / `STAGED`. A roster row is the evidence of participation. |
| Tombstone | A `gy_id` that is not on a page stays at that URL as `HIDDEN_BY_REQUEST` or `WITHDRAWN`, with no name and no records. |
| Redirect | A retired `gy_id` points at the survivor's current `gy_id`. The ledger already collapsed the chain. |
| Activity on the page | `publishable=yes`, a title, a year in 19xx/20xx, an http(s) source. Other types become `other`. |
| Background | CV sections education, employment, teaching, and press, minus scholarship-like titles and a future `upcoming` year. Not counted as practice. |
| F4 on the site | `coverage_pct = round(100 * included / roster, 1)`. `roster` is the greater of the declared size and the membership count. Null when that size is 0. |
| Citation | APA, Chicago, and BibTeX in the production dialog's shape. Author, title, version, and origin come from `[publish]`. Default author `기예 Giye`, default origin `https://giye.org`, default version `0.2`. |
| Same-name note | Open `possible_same_person` items are listed on both pages as other ids. They are not merges. |
| Gap | A missing number up to the highest issued id is a warning. The snapshot is still written. |
| Roster must publish | A member who is not published and not out of scope stops the build. |

`giye publish` does not rewrite `frames.yml`. Production saved the new counts back into that file. The counts are in `frames.json` and `coverage.json`.

Region tags (`서울`, `경기`, …) and the medium guess from `field` / `category` are the production Korean media-art lists, copied as written. Incheon is tagged `경기` because that script did so. Another field's tags belong in a later language module; they are not re-decided here. Membership code `APE-2025` still resolves to frame `APE-CURRENT` at edition 2025 when that frame is registered. That special case is one live programme, kept so the same ledger builds the same editions.

## Exploration (stage 6)

| ID | Rule | Status |
|---|---|---|
| C1 | The number of clusters is the k with the highest bootstrap stability (mean adjusted Rand index), ties to the smaller k; stability is published with the clusters. | planned |
| C2 | Clusters are described by their most over-represented features, never by people. | planned |
