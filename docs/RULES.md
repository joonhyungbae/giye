# Rule registry

Every decision the pipeline makes is taken by one of these rules and can be traced to it in the
output (rule IDs are written next to derived values and in the audit files). Status: **ported**
(in this repository, tested), **planned** (exists in the Giye archive, being ported).

## Sampling frame (stage 1)

| ID | Rule | Status |
|---|---|---|
| F1 | The programme states a purpose within the field (e.g. art and technology). | planned |
| F2 | It selects a cohort of participants. | planned |
| F3 | It is held in the field's territory. | planned |
| F4 | It publishes an official roster. | planned |
| F5 | Its period can be identified. | planned |

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
