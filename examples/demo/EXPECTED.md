# What `giye demo` shows

One offline run of the synthetic field. People are fictitious. Institution names are public
places. Nothing is fetched from the network. The clock in the golden test is
2026-01-15, so an upcoming row in 2026 stays on the page and a row in 2027 does not.

The summary (paths omitted; they depend on `--output`) is:

```
people: 22
roster rows: 23
activities: 37
merges: E1 1, E2 1, E3 1, E4 1, X1+E2 1
blocked: T1 1
queue items: 3
institution merges: V7 2, V8 1, V9 1
```

## Identity

| Rule | What the fixtures do |
|---|---|
| E1 | 정다운 on both rosters shares `dawoon.example.org`. |
| E2 | 표은솔's CV names Example Workshop in 2021, the other row's roster year. |
| E3 | Both 한별 rows credit the bracketed work 〈푸른 신호〉. |
| E4 | Both 문지호 rows are credited `팀: 노을크루`. |
| X1+E2 | 김하늘 is on Example Residency (2019). Haneul Kim is on Example Workshop (2021). X1's key for both is `kim/haneul`. The English CV lists Example Residency in 2019, so E2 holds and the stored rule is `X1+E2`. The kept page is `GY-000001` (김하늘, Latin name Haneul Kim). `GY-000020`, the workshop row's id, redirects there. |
| T1 | The person 배수아 and the team row 배수아 (members 김솔, 박솔, same site `sua.example.org`) are not merged. The summary line is `blocked: T1 1`. |
| Queue | Three open `possible_same_person` items. Kim Haneul (`GY-000023`, Example Forum) shares `kim/haneul` and has no evidence. 서지우 ~ Jiwoo Seo is the same kind of pair. The two 최민수 rows are an exact same-script name with no evidence. |

`GY-000001` lists both programmes under Roster and, under Activities, the residency row, the workshop row, and the CV lines from both languages. Each line links to its source. The page also says `Open same-name review: GY-000023`. Kim Haneul's page says `Open same-name review: GY-000001`. The queue holds that pair, and both records stay in the ledger.

The roster block names the programme. The edition year is on the activity row (`2019 — EXAMPLE-RESIDENCY`, `2021 — EXAMPLE-WORKSHOP`). A frame code that does not end in a year is published that way.

## Institutions

The Korean CV and the English CV name one museum in several ways. Normalisation writes `data/processed/venue_audit.md`.

| Count | Rule | Spellings |
|---|---|---|
| V7 2 | V7a and V7b rewrite the key, so the audit does not list them as joins. The summary counts a trimmed spelling that shares an entity with another spelling. | `서울시립미술관 《빛》` (exhibition title) and `서울시립미술관 외` (qualifier 외), both the same key as `서울시립미술관` |
| V8 1 | A hall is part of the building. | `서울시립미술관 전시실` → `서울시립미술관` |
| V9 1 | One Hangul reading matches the Latin bag. | `서울시립미술관` ↔ `Seoul Museum of Art` |

The bare spelling `서울시립미술관` is on the Korean CV so the entity's display name is the museum. Production ranks an untrimmed full name ahead of a trimmed spelling and ahead of a hall. The audit's V7e section is empty.

`GY-000001` shows the Korean lines and `Signal — Seoul Museum of Art` from the English CV.
