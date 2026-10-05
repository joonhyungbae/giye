# Evaluation

Accuracy of three automatic steps, checked by one coder on a random sample.
The numbers in a paper that cites Giye come from this protocol. The commands
below run on any ledger (`giye.toml`); they do not read a private data
directory by path.

## Frames

| Frame | What is judged | Population |
|---|---|---|
| `cv` | A publishable activity the extractor wrote (`origin` `cv:<source_id>`) | One row per such activity. Roster rows and `publishable=no` rows are not in the frame. |
| `people` | A same-person merge | One row per merge marker: `merged <ledger id>`, then `merge_evidence=…` and `rule=…`, as `Ledger.merge` appends them to the survivor's note. |
| `venues` | An institution name-rule merge | One row per V7e, V8, or V9 line in section 7 of `data/processed/venue_audit.md`. V5 and the V7a–d spelling rewrites are not in this frame. |

A case is a success when the coder's label is `correct`:

- `cv` — the title, year, venue, and activity type are what that CV line says, and the row is a practice record the extractor was supposed to keep.
- `people` — the two records are the same person.
- `venues` — the two spellings are the same institution.

`incorrect` is a failure. `cannot tell` means the sheet does not show enough to decide. A blank `label` is not judged yet.

## Strata

| Frame | Stratum |
|---|---|
| `cv` | `activity_type`. A blank type is `other`. |
| `people` | The stored rule when it is `E1`, `E2`, `E3`, `E4`, `X1+E1`, `X1+E2`, `X1+E3`, or `X1+E4`. Every other rule, including a missing one, is `uncoded`. |
| `venues` | `V7e`, `V8`, or `V9`. |

`--n` is the size of the whole sheet, not a quota per stratum. Seats are Hamilton's method on integers: each stratum starts with `n * size // population`, and leftover seats go to the largest remainder (`n * size % population`), then the larger stratum, then the stratum name. A small stratum can receive no row. When `--n` is at least the population, the sheet is a census and the generator is not read.

Inside a stratum, rows are ordered by `item_id`. The draw is a partial Fisher–Yates on `random.Random(seed)` (`randrange` only). Taking every row of a stratum, or none, does not advance the generator. The same seed and the same ledger rewrite the same sheet. The seed is stored on every row.

## What the coder sees

This is a shown-evidence audit. There is no blind arm.

- `cv` shows the extracted fields and a CV excerpt: the snapshot line that contains the title (the year must be on that line when any such line exists), plus the neighbouring lines. The snapshot is `<snapshot_path>.txt` from `cv_sources`. No matching line leaves the excerpt empty.
- `people` shows both records' names, published ids, rosters, and the evidence string. The dropped row is no longer in `artists.csv`. Names and rosters are taken from the latest `data/work/backups/*-before-merge*.csv` that still contains the dropped ledger id (the artist file and the frame-membership file with the same date and sequence). If that backup is gone, the dropped name and rosters are empty and the kept rosters are the survivor's current memberships, which already mix the two people.
- `venues` shows the two spellings from the audit line and up to three activity rows for each spelling. A row matches when its venue is that spelling, or the spelling followed by a comma, slash, pipe, middle dot, or semicolon. A longer name such as a hall does not match the shorter spelling. Rows are the earliest three by year, then activity id.

The coder is one person. The sheet has one `label` column and one `note` column. There is no second coder and no adjudication column.

## Scoring

`python -m giye.audit score` reports precision per stratum and overall.

Primary denominator: `correct` + `incorrect`. `cannot tell` is counted and excluded. Blank labels and any other text are counted (`unlabeled`, `other`) and excluded, so an unfinished row or a typo is not a success.

Conservative interval: the same successes, with each `cannot tell` added to the denominator as a failure. Blanks stay out.

The 95% interval is the Wilson score interval with the fixed quantile `z = 1.959963984540054` (the 0.975 point of the standard normal). Bounds are clamped to [0, 1]. A denominator of zero has no precision and no interval.

## Commands

Draw a sheet. `label` and `note` are empty. Sampling reads the ledger CSVs in place and does not take the ledger lock. That lock is exclusive until the process holding it exits, and a sample has to be drawable while a pipeline run has the ledger open. The command does not write the ledger.

```
python -m giye.audit sample cv --config giye.toml --n 200 --seed 20261005 --out cv.csv
python -m giye.audit sample people --config giye.toml --n 200 --seed 20261005 --out people.csv
python -m giye.audit sample venues --config giye.toml --n 200 --seed 20261005 --out venues.csv
```

Score it. `--json` prints the same figures as one object (`overall` and `strata`).

```
python -m giye.audit score cv.csv --kind cv
python -m giye.audit score cv.csv --kind cv --json
```

Judging page. Keys 1, 2, and 3 set `correct`, `incorrect`, and `cannot tell` when the note box is not focused, and the page moves to the next unlabeled row.

```
python -m giye.audit page cv.csv --kind cv --out cv.html
python -m giye.audit serve cv.csv --port 5181
```

`serve` listens on `127.0.0.1` only. The page is `http://127.0.0.1:5181/`. Each button POSTs `/label`, and the process writes `label` and `note` back into the sheet immediately (a temporary file in the same directory, then a rename). There is no export step. The static file from `page` shows the cases; labels are stored only while `serve` is running.

Record the config path, the frame, `--n`, and `--seed` next to the sheet. Those four, plus the ledger, are the sample.

## Results on the reference archive

One coder, the author, judged every sample. There is no second coder and no
agreement statistic. The audit was unblinded where the evidence was shown: the
CV sheet shows the excerpt, and the `people` and `venues` sheets show the
evidence the rule used beside the pair.
Intervals are 95% Wilson intervals on the cases the coder could judge;
`cannot tell` cases are left out of the denominator, and a conservative figure
counts them as failures where stated.

| Step | Sample | Judgeable | Correct | Precision (95% Wilson) |
|---|---|---|---|---|
| CV extraction (`cv`) | 150 | 147 | 143 | 97.3% (93.2–98.9%) |
| Person merges (`people`) | 82 | 77 | 77 | 100% (95.2–100%); conservative 77/82 = 93.9% (86.5–97.4%) |
| Institution merges, round 1 (`venues`) | 85 | 83 | 81 | 97.6% (91.6–99.3%) |
| Institution merges, refined rules (`venues`) | 70 | 70 | 64 | 91.4% (82.5–96.0%) |
| Same, against the rule's own definition of a part | 70 | 70 | 67 | 95.7% (88.1–98.5%) |

Latin-only personal names: 21 joins of two Latin-only personal names were made
before the restriction of 2026-10-05 (no join on the name alone). The author
judged all 21; each was the same person.
