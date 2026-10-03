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

Collection policy (not an F-rule): robots.txt is checked before every request. A disallowed URL raises `RobotsDisallowed` and is not fetched. When the live page is gone (HTTP 404 or 410, or the connection fails), an existing Internet Archive capture may be kept. Giye never asks the Archive to make a new capture. If robots.txt disallows the host, this public release records `robots_disallowed` and does not use the Archive unless `evidence.archive_fallback_for_disallowed` is true. That switch defaults to false; the production archive did use the Archive in the disallow case, and the difference is waiting on the author's decision. Platforms whose terms forbid collection (Instagram, Facebook, LinkedIn, X, Threads, TikTok) are not requested.

## Identity (stage 4)

| ID | Rule | Status |
|---|---|---|
| E1 | Two records are the same person when they share a personal website. | planned |
| E2 | … when one's CV lists the other's roster appearance (same event, same year). | planned |
| E3 | … when one's CV lists a work named on the other's roster entry. | planned |
| E4 | … when both appear as members of the same team. | planned |
| T1 | A record whose name looks like a team or collective is never merged with a person. | planned |
| X1 | Hangul and Latin spellings are candidate matches when their romanized keys intersect (`giye.resolve.names`); a candidate is merged only with E1–E4 evidence. | ported |

## Derived values (stage 5)

| ID | Rule | Status |
|---|---|---|
| P1–P5 | Checks, normalisation and derived attributes (birth year from the artist's own CV, base country, active-since, medium tags); the ledger value always wins over a derived one. | planned |
| V1 | A venue fragment is a place only when the whole fragment is a place name. | planned |
| V2–V6 | Venue strings are split into fragments, classified, keyed exactly, and joined through parenthetical aliases written by two or more artists. | planned |
| V7 | Spelling rules: work titles, qualifiers and edition markers are not part of a name; Hangul names ignore spaces; Latin names with the same words are one entity. | planned |
| V8 | A part of a known entity (building, room, acronym + its city) is that entity. | planned |
| V9 | A Hangul name and a Latin name are one entity when their glossary/gazetteer/romanization readings match one-to-one. | planned |

## Exploration (stage 6)

| ID | Rule | Status |
|---|---|---|
| C1 | The number of clusters is the k with the highest bootstrap stability (mean adjusted Rand index), ties to the smaller k; stability is published with the clusters. | planned |
| C2 | Clusters are described by their most over-represented features, never by people. | planned |
