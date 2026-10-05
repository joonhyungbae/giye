# What `giye demo` shows

One offline run of the synthetic field. People are fictitious. Institution names are public
places. Nothing is fetched from the network. The clock in the golden test is
2026-01-15, so an upcoming row in 2026 stays on the page and a row in 2027 does not.

The summary (paths omitted; they depend on `--output`) is:

```
people: 20
roster rows: 23
activities: 38
merges: E1 1, E2 1, E3 1, E4 1, X1+E2 1
blocked: T1 1
queue items: 3
institution merges: V7 2, V8 1, V9 1
co-presence ties, CV listing: base 1, V7 1, V7+V8 1, V7+V8+V9 2
co-presence ties, roster independent: base 0, V7 0, V7+V8 1, V7+V8+V9 2
```

Attachment runs at collection. A later merge of two existing records is a separate step. The counts above are the published people after both steps.

## Identity

| Rule | What the fixtures do |
|---|---|
| A1 | Lee Haru (workshop 2021) and Haru Lee (workshop 2022) share the workshop family. The 2022 membership records `attach_rule` `A1`. The stored name stays Lee Haru. That person and 이하루 (workshop 2021) then share an edition code, so X1 does not queue them. |
| A2 | Kim Haneul on the forum agrees in English tokens with Haneul Kim on the workshop. The forum membership records `A2`. There is no separate Kim Haneul person. |
| A5 | The two 정다운 rows carry different identity keys (`demo:dawoon-a`, `demo:dawoon-b`). A miss does not fall through, so A6 does not attach them on the shared site. |
| E1 | Those two rows still share `dawoon.example.org`. E1 merges them, and the attachment queue item is closed. |
| E2 | 표은솔's CV names Example Workshop in 2021, the other row's roster year. The rows are different programmes, so A1 does not attach them. |
| E3 | 한별 on the residency (2019) and on the workshop (2020) both credit the bracketed work 〈푸른 신호〉. Different programmes, so A1 does not attach them. |
| E4 | Both 문지호 rows are credited `팀: 노을크루`. |
| X1+E2 | 김하늘 is on Example Residency (2019), with no English name. Haneul Kim is on Example Workshop (2021). X1's key for both is `kim/haneul`. The English CV lists Example Residency in 2019, so E2 holds and the stored rule is `X1+E2`. The kept page is `GY-000001` (김하늘, Latin name Haneul Kim). `GY-000020`, the workshop row's id, redirects there. |
| T1 | The person 배수아 and the team row 배수아 (members 김솔, 박솔) are not merged. The team row does not repeat `sua.example.org`: A6 would attach them at collection. T1 still blocks the merge because one side is a team. The summary line is `blocked: T1 1`. |
| Queue | Three open `possible_same_person` items. The two 최민수 rows are an exact same-script name with no evidence. The person 배수아 and the team 배수아 are the same kind of pair. 서지우 ~ Jiwoo Seo share a romanization key and have no evidence. Near-misses that E1–E4 later join (한별, 표은솔, 정다운, 문지호) are closed by the merge. |

`GY-000001` lists the residency, the workshop, and the forum under Roster (the forum row attached by A2) and, under Activities, those roster rows and the CV lines from both languages. The two CVs are folded in that same run: one `예시 미디어전 (2024)` is published, and the English line `Example Residency (2019)` is not, because it restates the residency roster row. Each published line links to its source. The page does not carry an open same-name review. `GY-000007` and `GY-000018` (the two 최민수 rows) each link to the other.

Membership is `<FRAME>-<YYYY>`. The roster block names the programme and that edition (`예시 레지던시 2019`). The activity title is the membership code (`2019 — EXAMPLE-RESIDENCY-2019`).

## Institutions

The Korean CV and the English CV name one museum in several ways. Normalisation writes `data/processed/venue_audit.md`.

| Count | Rule | Spellings |
|---|---|---|
| V7 2 | V7a and V7b rewrite the key, so the audit does not list them as joins. The summary counts a trimmed spelling that shares an entity with another spelling. | `서울시립미술관 《빛》` (exhibition title) and `서울시립미술관 외` (qualifier 외), both the same key as `서울시립미술관` |
| V8 1 | A hall is part of the building. | `서울시립미술관 전시실` → `서울시립미술관` |
| V9 1 | One Hangul reading matches the Latin bag. | `서울시립미술관` ↔ `Seoul Museum of Art` |

The bare spelling `서울시립미술관` is on the Korean CV so the entity's display name is the museum. Production ranks an untrimmed full name ahead of a trimmed spelling and ahead of a hall. The audit's V7e section is empty.

`GY-000001` shows the Korean lines and `Signal — Seoul Museum of Art` from the English CV.

## Co-presence ties

Two more fictitious CVs (`cvs/seoyeon-ko.html` for 박서연, `cvs/seoyeon-kim-en.html` for Kim Seoyeon, three rows in all) give `giye.explore.ties` something to count. They are why the activity count is 38, not 35. The golden file stores the whole layer report under `ties`.

| Pair | Row | CV listing | Roster independent |
|---|---|---|---|
| 김하늘 – 박서연 | Both at 예시미술관 in 2019. 박서연's line is `예시 레지던시 결과전`: her own Example Residency 2019 edition. | from `base` | dropped (it restates her roster edition) |
| 김하늘 – 박서연 | 박서연 writes the bare `서울시립미술관` in 2023; 김하늘 writes `서울시립미술관 전시실`. | already a tie | from `V7+V8` (V8 joins the hall to the museum) |
| 김하늘 – Kim Seoyeon | Kim Seoyeon writes `Seoul Museum of Art` in 2020; 김하늘 writes `서울시립미술관`. | from `V7+V8+V9` | from `V7+V8+V9` |

So the CV-listing count is 1, 1, 1, 2 and the roster-independent count is 0, 0, 1, 2. The attribution gives V9 one added tie (its one merge) and V8 none: the V8 merge joins a pair that the 2019 row has already tied. The three CV people are the population (`n_people` 3).
