# Evaluation

Accuracy of the automatic steps, checked by one coder on a random sample.
The numbers in a paper that cites Giye come from this protocol. The commands
below run on any ledger (`giye.toml`); they do not read a private data
directory by path.

## Frames

| Frame | What is judged | Population |
|---|---|---|
| `cv` | A publishable activity the extractor wrote (`origin` `cv:<source_id>`) | One row per such activity. Roster rows and `publishable=no` rows are not in the frame. |
| `people` | A same-person merge | One row per merge marker: `merged <ledger id>`, then `merge_evidence=…` and `rule=…`, as `Ledger.merge` appends them to the survivor's note. |
| `venues` | An institution name-rule merge | One row per V7e, V8, or V9 line in section 7 of `data/processed/venue_audit.md`. V5 and the V7a–d spelling rewrites are not in this frame. |
| `attach` | A roster row an attachment rule joined to an existing person | One row per membership whose `attach_rule` is the rule given by `--rule` (default `A2`). The record's other memberships, with their rules, are shown beside it. |
| `splink` | A pair a probabilistic linker matches and the evidence rules did not merge | One row per pair at or above the threshold (see "Comparison with a probabilistic linker"). Not sampled: the command writes every pair. |

A case is a success when the coder's label is `correct`:

- `cv` — the title, year, venue, and activity type are what that CV line says, and the row is a practice record the extractor was supposed to keep.
- `people` — the two records are the same person.
- `venues` — the two spellings are the same institution.
- `attach` — the roster row is the person the record already describes.
- `splink` — the two records are the same person.

`incorrect` is a failure. `cannot tell` means the sheet does not show enough to decide. A blank `label` is not judged yet.

## Strata

| Frame | Stratum |
|---|---|
| `cv` | `activity_type`. A blank type is `other`. |
| `people` | The stored rule when it is `E1`, `E2`, `E3`, `E4`, `X1+E1`, `X1+E2`, `X1+E3`, or `X1+E4`. Every other rule, including a missing one, is `uncoded`. |
| `venues` | `V7e`, `V8`, or `V9`. |
| `attach` | The attachment rule (one stratum). |
| `splink` | `name_only` when the pair shares no edition and no website, otherwise `shared`. |

`--n` is the size of the whole sheet, not a quota per stratum. Seats are Hamilton's method on integers: each stratum starts with `n * size // population`, and leftover seats go to the largest remainder (`n * size % population`), then the larger stratum, then the stratum name. A small stratum can receive no row. When `--n` is at least the population, the sheet is a census and the generator is not read.

`people` also has a census-plus-sample design, `--design census-coded`: every merge in a coded stratum is on the sheet, and `--n` rows are drawn from `uncoded` alone. The census strata carry no sampling error; the drawn rows stand for the whole `uncoded` stratum, so a pooled precision should be weighted by stratum size (see the results below).

Inside a stratum, rows are ordered by `item_id`. The draw is a partial Fisher–Yates on `random.Random(seed)` (`randrange` only). Taking every row of a stratum, or none, does not advance the generator. The same seed and the same ledger rewrite the same sheet. The seed is stored on every row.

## What the coder sees

This is a shown-evidence audit. There is no blind arm.

- `cv` shows the extracted fields and a CV excerpt: the snapshot line that contains the title (the year must be on that line when any such line exists), plus the neighbouring lines. The snapshot is `<snapshot_path>.txt` from `cv_sources`. No matching line leaves the excerpt empty.
- `people` shows both records' names, published ids, rosters, and the evidence string. The dropped row is no longer in `artists.csv`. Names and rosters are taken from the latest `data/work/backups/*-before-merge*.csv` or `.csv.gz` that still contains the dropped ledger id (the artist file and the frame-membership file with the same date and sequence). If that backup is gone, the dropped name and rosters are empty and the kept rosters are the survivor's current memberships, which already mix the two people.
- `venues` shows the two spellings from the audit line and up to three activity rows for each spelling. A row matches when its venue is that spelling, or the spelling followed by a comma, slash, pipe, middle dot, or semicolon. A longer name such as a hall does not match the shorter spelling. Rows are the earliest three by year, then activity id.

The coder is one person. The sheet has one `label` column and one `note` column. There is no second coder and no adjudication column.

## Scoring

`python -m giye.audit score` reports precision per stratum and overall.

Primary denominator: `correct` + `incorrect`. `cannot tell` is counted and excluded. A blank label is counted (`unlabeled`) and excluded, so an unfinished row is not a success. Any other text is refused: the command names the sheet line, the item and the label and exits 2, because a typo left out silently would shrink the denominator and raise the precision.

Conservative interval: the same successes, with each `cannot tell` added to the denominator as a failure. Blanks stay out.

The 95% interval is the Wilson score interval with the fixed quantile `z = 1.959963984540054` (the 0.975 point of the standard normal). Bounds are clamped to [0, 1]. A denominator of zero has no precision and no interval.

Stratum-weighted estimate: `--weights W` (a CSV with columns `stratum` and `weight`, or `size`, or a JSON object of stratum to weight) adds a pooled precision weighted by stratum population, for a sheet whose strata were not drawn in proportion to their size (the census-plus-sample person-merge sheet). With `W_s = w_s / Σ w`, the estimate is `Σ W_s p_s` over the primary denominators, and its bounds are `Σ W_s low_s` and `Σ W_s high_s`. A stratum whose sheet holds at least `w_s` rows is a census: it has no sampling error and contributes `p_s` to both bounds; a sampled stratum contributes its Wilson bounds. Summing per-stratum 95% bounds is conservative compared with a pooled variance, and unlike a normal approximation it does not give a zero-width interval when a sampled stratum is all correct. The same is reported with `cannot tell` as failures (`conservative_*`). Every sheet stratum needs a weight and every weighted stratum must be on the sheet; a weight of 0 leaves a stratum out. With weights 42 (census, 40 of 42 decided) and 117 (sampled, 37 of 40 decided) this gives the person-merge figures below: lower bound 93.1%, and 93.2% (lower bound 84.1%) with the undecided rows as errors.

## Commands

Draw a sheet. `label` and `note` are empty. Sampling reads the ledger CSVs in place and does not take the ledger lock. That lock is exclusive until the process holding it exits, and a sample has to be drawable while a pipeline run has the ledger open. The command does not write the ledger.

```
python -m giye.audit sample cv --config giye.toml --n 200 --seed 20261005 --out cv.csv
python -m giye.audit sample people --config giye.toml --n 200 --seed 20261005 --out people.csv
python -m giye.audit sample venues --config giye.toml --n 200 --seed 20261005 --out venues.csv
python -m giye.audit sample people --config giye.toml --design census-coded --n 40 --seed S --out people.csv
python -m giye.audit sample attach --config giye.toml --rule A2 --n 40 --seed 20261006 --out a2.csv
```

The linker comparison needs the optional extra (`pip install -e ".[splink]"`):

```
python -m giye.audit splink --config giye.toml --threshold 0.9 --out splink.csv
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
| A2 name attachments | 40 | 40 | 40 | 100% (91.2–100%) |
| Institution merges, round 1 (`venues`) | 85 | 83 | 81 | 97.6% (91.6–99.3%) |
| Institution merges, refined rules (`venues`) | 70 | 70 | 64 | 91.4% (82.5–96.0%) |
| Same, against the rule's own definition of a part | 70 | 70 | 67 | 95.7% (88.1–98.5%) |

The CV-extraction row measures the reference extraction, not `giye extract`.
The reference register's CVs were read by Claude Opus 5.5 running in Claude
Code from a written prompt, outside the package, and the readings were
imported into the replay cache (model string `claude-code/claude-opus-5.5
(agent, reference)` in `deploy/giye.production.toml`). The package's own
extractor sends `src/giye/extract/prompts/cv_extract_v1.txt` to the
configured model (default `claude-opus-5-5`); the 97.3% figure does not
validate that path. The `cv` frame and the scoring below apply unchanged to
rows `giye extract` writes.

The person-merge sheet was not drawn with the `--n` allocation described
under Strata (Hamilton seats in proportion to stratum size). It is a census
plus a sample: all 42 merges whose stored rule names an evidence rule
(`E1`–`E4` or `X1+E1`–`X1+E4`), and a seeded random draw of 40 of the 117
merges with no stated rule (the `uncoded` stratum), 82 rows in all. The
census strata carry no sampling error; the 40 uncoded rows stand for 117. The
pooled precision in the table treats the 82 rows as one sample and is not
weighted by stratum. Weighted by stratum size, with the census strata taken as
fixed (40 of 42 decided) and the Wilson lower bound on the sampled stratum (37
of 37 decided, 90.6%; 117 of the 159 merges), the lower bound is 93.1%: this is
the figure the manuscript gives beside the unweighted 95.2%. Counting the five
undecided rows as errors gives 93.2% weighted (lower bound 84.1%). Both
figures were judged from names, rosters and stored evidence, so neither tests
homonymy. This is the `--design census-coded` sheet with `--n 40`; see
"Regenerating each figure" for what the command can and cannot reproduce.

A2 name attachments: a seeded random draw (seed 20261006) of 40 of the 270
memberships attached by A2. The author judged each attached roster row against
the record's other editions; all 40 were the same person. Like the merge audit,
this was judged from names and rosters and is not a test of homonymy.

Latin-only personal names: 21 joins of two Latin-only personal names were made
before the restriction of 2026-10-05 (no join on the name alone). The author
judged all 21; each was the same person.

## Comparison with a probabilistic linker (Splink)

`python -m giye.audit splink` (module `giye.audit.linker`) fixes the model so
the comparison can be rebuilt from any ledger. One record per person carries
the Korean and Latin names, the programme editions (memberships and the
origin of every roster activity) and the personal websites (host and path).
The comparisons are: name (exact Korean name, exact Latin name, then
Jaro–Winkler at 0.9 on either, else); programme editions (at least one in
common); websites (at least one in common). The prior is estimated from the
exact-name rule at an assumed recall of 0.7, `u` from a seeded random sample
of pairs, and `m` by expectation–maximisation in two sessions (blocked on an
exact name, then on the first website). There is no term-frequency
adjustment. Prediction blocks on an exact Korean name, an exact Latin name,
or the same first website, and keeps pairs with a match probability of 0.9
or more. Records the rules already merged are one record, so every pair on
the sheet is one the rules did not merge. A ledger with no same-name or
same-website pairs cannot train the model, and the command stops with an
error.

The evidence rules were compared with Splink 5.0.0 on the reference archive.
The Splink model compared name similarity, agreement on a programme edition,
and agreement on a personal website. Its parameters were trained with
expectation–maximisation, and a pair was called a match at a match
probability of 0.9 or more. Term-frequency adjustment, Splink's remedy for
common names, was not used; this is a limit of the comparison, since a few
surnames and many given names cover much of the Korean population.

Splink's matches include 131 pairs that rest on the name alone: no evidence
rule (E1–E4) holds for them. The author judged these 131 pairs from the two
careers, the frequency of the name (judged without a reference list), and
whether the programme years fit one career. 110 pairs were judged one person (recorded as
96 merges, because several pairs fall into one group), 13 pairs two people,
and 8 were left undecided. These judgements are the author's, unblinded and
by one coder, as for the other audits; they are not a second measurement of
precision. The 13 pairs measure disagreement between Splink at 0.9 and this
coder, not composite people against ground truth. The 96 merges recorded from
the 110 pairs (rule H) rest on the author's recorded judgement, not on a
document, and are not marked for review.

## Regenerating each figure

Every command reads the ledger named by the config; none reads a private
path. Run them against the ledger as it stood when the figure was measured
(a pipeline rerun under the rule changes below gives different populations).

| Figure | Command | Then |
|---|---|---|
| CV extraction, 150 rows | `python -m giye.audit sample cv --config giye.toml --n 150 --seed S --out cv.csv` | `score cv.csv --kind cv` |
| Person merges, 42 + 40 rows | `python -m giye.audit sample people --config giye.toml --design census-coded --n 40 --seed S --out people.csv` | `score people.csv --kind people --weights sizes.csv` (stratum sizes; see Scoring) |
| A2 attachments, 40 of 270 | `python -m giye.audit sample attach --config giye.toml --rule A2 --n 40 --seed 20261006 --out a2.csv` | `score a2.csv --kind attach` |
| Institution merges | `python -m giye.audit sample venues --config giye.toml --n 85 --seed S --out venues.csv` (70 for the refined rules) | `score venues.csv --kind venues` |
| Splink pairs on the name alone (131) | `python -m giye.audit splink --config giye.toml --out splink.csv` | the `name_only` stratum; `score splink.csv --kind splink` gives the coder's same-person share |
| Latin-only joins (21) | no command | a census of joins made before 2026-10-05; the current A2 no longer makes them |

`S` is the seed recorded on each sheet's `seed` column. The sheets behind
the reported person-merge, A2 and Splink figures were drawn by scripts
outside the package before these commands existed. The commands implement
the design described on this page, so on the same ledger they rebuild the
same population, but a rerun is not guaranteed to pick the same rows. The Splink figures
were produced with Splink 5.0.0; the model in `giye.audit.linker` states
the settings the comparison reports (name, edition and website agreement,
EM, no term-frequency adjustment, threshold 0.9), and its prior and name
levels are this module's choices. The coder's labels are not part of the
package, so precision is regenerated by judging the sheet again.

## Rules changed after these audits

The figures above were measured on the ledger as it stood before the rule
changes of 2026-10-06 (software review, round 6): A6 needs overlapping names
for personal names, A2 does not join a Latin-only personal name on either
side, X1 also pairs a Korean record whose own Latin name meets the Latin-only
record (`rule=x1_own_en`), manual E-code merges must meet the resolver's name
and candidate conditions, E2 ignores an edition both records are on, and E3
and CV grounding match whole words with generic venue words grounding
nothing. A rerun of the pipeline changes the attachment, queue and grounding
counts; the audited samples are not redrawn by that rerun.
