# What `giye demo` shows

One offline run of the synthetic field. People are fictitious. Institution names are public
places. Nothing is fetched from the network. The run date is fixed at
2026-01-15, including when `giye demo` is started in a later year, so an upcoming
row in 2026 stays on the page and a row in 2027 does not. The summary's
`run date` line says so.

`giye demo --output <output>` prints this (regenerated from a run; `<output>` stands for the
directory, which is a new temporary directory when `--output` is omitted). The first line is
the publish step; the summary follows, then the files written:

```
site artists=21 activities=37 links=3 frames=4
run date: 2026-01-15 (fixed; this summary does not follow the system date)
people: 21
roster rows: 23
activities: 37
merges: E1 1, E2 1, E3 1, E4 1, X1+E2 1
blocked: T1 1
queue items: 4
institution merges: V7 2, V8 1, V9 1
co-presence ties, CV listing: base 1, V7 1, V7+V8 1, V7+V8+V9 2
co-presence ties, roster independent: base 0, V7 0, V7+V8 1, V7+V8+V9 2
output files:
  <output>/site/activities.json
  <output>/site/artist_stubs.json
  <output>/site/artists.json
  <output>/site/background.json
  <output>/site/citations.json
  <output>/site/collaborations.json
  <output>/site/content_pages.json
  <output>/site/content_revisions.json
  <output>/site/coverage.json
  <output>/site/dataset_versions.json
  <output>/site/frames.json
  <output>/site/gy_redirects.json
  <output>/site/html/GY-000001.html
  <output>/site/html/GY-000002.html
  <output>/site/html/GY-000003.html
  <output>/site/html/GY-000004.html
  <output>/site/html/GY-000005.html
  <output>/site/html/GY-000006.html
  <output>/site/html/GY-000007.html
  <output>/site/html/GY-000008.html
  <output>/site/html/GY-000009.html
  <output>/site/html/GY-000010.html
  <output>/site/html/GY-000011.html
  <output>/site/html/GY-000012.html
  <output>/site/html/GY-000013.html
  <output>/site/html/GY-000014.html
  <output>/site/html/GY-000015.html
  <output>/site/html/GY-000016.html
  <output>/site/html/GY-000017.html
  <output>/site/html/GY-000018.html
  <output>/site/html/GY-000019.html
  <output>/site/html/GY-000020.html
  <output>/site/html/GY-000021.html
  <output>/site/html/GY-000022.html
  <output>/site/html/GY-000023.html
  <output>/site/html/GY-000024.html
  <output>/site/html/GY-000025.html
  <output>/site/html/GY-000026.html
  <output>/site/html/index.html
  <output>/site/links.json
  <output>/site/research.json
  <output>/site/rim_order.json
  <output>/site/vocabularies.json
```

Attachment runs at collection. A later merge of two existing records is a separate step. The counts above are the published people after both steps.

## Identity

| Rule | What the fixtures do |
|---|---|
| A1 | Lee Haru (workshop 2021) and Haru Lee (workshop 2022) share the workshop family. The 2022 membership records `attach_rule` `A1`. The stored name stays Lee Haru. That person and 이하루 (workshop 2021) then share an edition code, so X1 does not queue them. |
| A2 (not applied) | Kim Haneul on the forum and Haneul Kim on the workshop are Latin-only personal names in different programmes. A2 does not join them on name alone: the forum row opens its own record (GY-000022) and the pair waits in the review queue with reason `latin name only`. |
| A5 | The two 정다운 rows carry different identity keys (`demo:dawoon-a`, `demo:dawoon-b`). A miss does not fall through, so A6 does not attach them on the shared site. |
| E1 | Those two rows still share `dawoon.example.org`. E1 merges them, and the attachment queue item is closed. |
| E2 | 표은솔's CV names Example Workshop in 2021, the other row's roster year. The rows are different programmes, so A1 does not attach them. |
| E3 | 한별 on the residency (2019) and on the workshop (2020) both credit the bracketed work 〈푸른 신호〉. Different programmes, so A1 does not attach them. |
| E4 | Both 문지호 rows are credited `팀: 노을크루`. |
| X1+E2 | 김하늘 is on Example Residency (2019), with no English name. Haneul Kim is on Example Workshop (2021). X1's key for both is `kim/haneul`. The English CV lists Example Residency in 2019, so E2 holds and the stored rule is `X1+E2`. The kept page is `GY-000001` (김하늘, Latin name Haneul Kim). `GY-000020`, the workshop row's id, redirects there. |
| T1 | The person 배수아 and the team row 배수아 (members 김솔, 박솔) are not merged. The team row does not repeat `sua.example.org`: A6 would attach them at collection. T1 still blocks the merge because one side is a team. The summary line is `blocked: T1 1`. |
| Queue | Four open `possible_same_person` items (`queue items: 4`). The two 최민수 rows are an exact same-script name with no evidence. The person 배수아 and the team 배수아 are the same kind of pair. 서지우 ~ Jiwoo Seo share a romanization key and have no evidence. Kim Haneul (forum, GY-000022) and 김하늘 / Haneul Kim (GY-000001) are the Latin-only pair A2 did not join (`latin name only`). Near-misses that E1–E4 later join (한별, 표은솔, 정다운, 문지호) are closed by the merge. |

`GY-000001` lists the residency (`예시 레지던시 2019`) and the workshop (`예시 워크숍 2021`) under Roster. The forum row is not there: A2 did not attach it, so it is its own record, `GY-000022`. Under Activities the page lists those two roster rows and the CV lines from both languages. The two CVs are folded in that same run: one `예시 미디어전 (2024)` is published, and the English line `Example Residency (2019)` is not, because it restates the residency roster row. The 2022 exhibition written in both CVs (`신호 — 서울시립미술관 외` and `Signal — Seoul Museum of Art`) is published once: rule X2 reads the two venues as one institution (V7b, V9), the pair is the only one for that person, year, and type, and the archive's first language is Korean, so the English row is kept in the ledger with `publishable=no` and `superseded_by=<id of the Korean row>; rule=X2`. `work/cv_folds.csv` lists the fold. Each published line links to its source. The page ends with `Open same-name review: GY-000022`, the open Latin-only pair, and `GY-000022` links back. The two 최민수 rows (`GY-000007`, `GY-000018`), the person and the team 배수아 (`GY-000008`, `GY-000019`), and 서지우 and Jiwoo Seo (`GY-000010`, `GY-000021`) each link to the other the same way.

Membership is `<FRAME>-<YYYY>`. The roster block names the programme and that edition (`예시 레지던시 2019`). The activity title is the membership code (`2019 — EXAMPLE-RESIDENCY-2019`).

## Institutions

The Korean CV and the English CV name one museum in several ways. Normalisation writes `data/processed/venue_audit.md`.

| Count | Rule | Spellings |
|---|---|---|
| V7 2 | V7a and V7b rewrite the key, so the audit does not list them as joins. The summary counts a trimmed spelling that shares an entity with another spelling. | `서울시립미술관 《빛》` (exhibition title) and `서울시립미술관 외` (qualifier 외), both the same key as `서울시립미술관` |
| V8 1 | A hall is part of the building. | `서울시립미술관 전시실` → `서울시립미술관` |
| V9 1 | One Hangul reading matches the Latin bag. | `서울시립미술관` ↔ `Seoul Museum of Art` |

The bare spelling `서울시립미술관` is on the Korean CV so the entity's display name is the museum. Production ranks an untrimmed full name ahead of a trimmed spelling and ahead of a hall. The audit's V7e section is empty.

`GY-000001` shows the Korean lines. `Signal — Seoul Museum of Art` from the English CV is folded into `신호` by X2 and is not on the page; the institution table still reads its spelling, because venue resolution reads every ledger row.

## Co-presence ties

Two more fictitious CVs (`cvs/seoyeon-ko.html` for 박서연, `cvs/seoyeon-kim-en.html` for Kim Seoyeon, three rows in all) give `giye.explore.ties` something to count. They are why the activity count is 37, not 34. The golden file stores the whole layer report under `ties`.

| Pair | Row | CV listing | Roster independent |
|---|---|---|---|
| 김하늘 – 박서연 | Both at 예시미술관 in 2019. 박서연's line is `예시 레지던시 결과전`: her own Example Residency 2019 edition. | from `base` | dropped (it restates her roster edition) |
| 김하늘 – 박서연 | 박서연 writes the bare `서울시립미술관` in 2023; 김하늘 writes `서울시립미술관 전시실`. | already a tie | from `V7+V8` (V8 joins the hall to the museum) |
| 김하늘 – Kim Seoyeon | Kim Seoyeon writes `Seoul Museum of Art` in 2020; 김하늘 writes `서울시립미술관`. | from `V7+V8+V9` | from `V7+V8+V9` |

So the CV-listing count is 1, 1, 1, 2 and the roster-independent count is 0, 0, 1, 2. The attribution gives V9 one added tie (its one merge) and V8 none: the V8 merge joins a pair that the 2019 row has already tied. The three CV people are the population (`n_people` 3).
