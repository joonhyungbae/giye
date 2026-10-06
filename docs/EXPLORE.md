# Explore

Stage 6 is the part of the archive that asks what the roster rows add up to, after they have been collected and resolved. Three pieces ship here: the rim order, co-presence ties, and division scores. Feature embeddings, a data-chosen number of clusters (C1) and cluster descriptors (C2) do not: the archive measured those candidates and did not keep them. The measurements that decision used are what `giye.explore.ties` and `giye.explore.evaluate` compute.

Nothing in this stage reads the network. Nothing in it names a real person. The synthetic field in `examples/demo/` is the fixture the tests run.

## Rim order

`build_rim_order(source, now=None)` returns the document `scripts/build_rim_order.py` writes as `rim_order.json`. `write_rim_order(document, site_dir)` writes it to `<site_dir>/rim_order.json` with the production bytes: UTF-8, no ASCII escaping, no extra spaces, no trailing newline. The website reads that file (`StudyRim` in the page model): `version`, `computed_at`, `method`, `programmes`, `families`, `boundaries`, `artists`.

`source` is a `Config`, or a mapping with `artists`, `activities`, `frame_membership` and `frames` (and optional `scope`). Frame rows are the `frames.yml` objects. `now` fixes `version` and `computed_at`; without it the clock is the current UTC time. Passing the same `now` makes two calls return the same dict.

The ring lists the people the site builder publishes: in scope, an http(s) source, on a roster or `cv_link_ok=yes`, status empty, `PUBLISHED` or `STAGED`, and who already have a `gy_id`. A publishable row with no `gy_id` is omitted. The collector or the site builder issues that id and writes it; this function does not, and it does not take the ledger lock.

### Rules

The production script states these in its docstring. The letters are the public names.

| Id | Rule |
|---|---|
| R1 | Entry year is the earliest four-digit year on a roster membership code ending `-YYYY`. A staff role on that code is not an entry (the programme-rim pattern: curator, juror, mentor, organiser, and the same words in Korean). A year that exists only as `years_covered`, or only on an activity row, is not an entry year. |
| R2 | Arcs are five-year bins anchored at 2000. `GEN-2000` is 2000–2004. The same step runs forward and, by floor division, backward (1995 is `GEN-1995`). `GEN-UNDATED` is last. An empty bin is omitted. |
| R3 | Arc order is chronological, oldest first, undated last. The seam is the wrap from that last arc back to the oldest. `shared`, `cos` and `agree` are 0: the order is not a similarity. |
| R4 | Inside an arc: entry year, then each team followed by its members, then Unicode order of the primary name, then id. `year` and `edition` are both that entry year (`edition` is `""` when undated). A team is the activity role `팀: X`. The row whose name is the team name comes first. Record counts and programme counts are not sort keys. |
| R5 | `also` lists every programme family the person took part in, staff excluded. A prefix declared in the field file folds that code and its year-editions into one family. Codes are sorted. |
| R6 | `programmes` is one row per family that appears in `also`, in code order, with no counts. Labels drop a parenthetical and a subtitle after ` · ` or ` — `; the first registry row wins. A label declared for a folded family in the field file replaces that. |
| R7 | One slot per artist. `participants` equals `n`. `linked` is false only for `GEN-UNDATED`. |

The Korean and English arc labels are the strings the page already displays (`2000–2004 진입`, `Entered 2000–2004`, `진입 연도 미상`, `Entry year unknown`). They are data values, not prose.

R1 does not read an activity year. A yearless code stays undated rather than inheriting a span that covers other people. The demo collectors write membership as `<FRAME>-<YYYY>`, the same convention as the production ledger, so the demo ring has one arc per occupied five-year bin. A code without `-YYYY` is `GEN-UNDATED`.

## Co-presence ties

`giye.explore.ties` computes the ties the division scores and the paper's impact numbers use, from the ledger and the institution resolver (`giye.normalize.venues.build`, run with `write=False`, so neither the ledger nor `processed/` changes). A tie is an unordered pair of ledger ids, counted once however many institution-years the two share. Institution means a row whose resolved `venue_kind` is `institution` and whose `venue_id` is set; a funder is not a place where people meet.

| Definition | Rows | Population |
|---|---|---|
| CV listing (`cv_listing_ties`) | `publishable=yes`, numeric year, not flagged `year_from_title` (P1), `origin` starting `cv:` | everyone with one such row (`cv_population`) |
| Roster independent (`roster_independent_ties`) | `origin` starting `cv:`, a four-digit year; `publishable` and the year flags are not read. A row is dropped when it restates the person's own roster edition: the membership code ends `-YYYY`, that year is the row's year, and the edition's E2 event pattern matches the normalised title and venue. E2 itself allows a year either way; this filter does not. | everyone; `people=` limits both ends |

The roster-independent definition is the flock evaluation's primary outcome. A tie built from a CV line that only repeats the programme the person is already on would score a roster division against itself. The event patterns are the field file's `[resolve.events]` merged with `[resolve.event_patterns]` in the config, as in E2. The functions take ledger rows with resolver annotations, or rows of `processed/activities.csv`, which already carry `venue_id`, `venue_kind`, `title_norm` and `venue_norm` (pass `annotations=None`).

### Rule layers

`resolve_layers` runs the resolver four times, cumulatively: `base` (V7–V9 off, the resolver before 2026-09-27), `V7` (spelling V7a–d and the Latin word-bag merge V7e), `V7+V8` (subordinate spaces) and `V7+V8+V9` (the Hangul–Latin bag merge, the production setting). `layer_report` gives, per layer, the entity count and the entities shared by two or more people (funders included, then by kind), and both tie counts. It then attributes the CV-listing ties to rules. V7a–d rewrite a key before any join is recorded, so their share is the post-spelling tie count minus the base count. `attribute_merges` replays V7e, V8 and V9 in the recorded order, one merge at a time; a merge adds the pairs that first become ties when its two components join, and a pair already tied is not counted again. The replay must end on the V7, V7+V8 and V7+V8+V9 counts of the separate runs; `layer_report` raises otherwise.

```bash
giye explore ties --config giye.toml --out ties.json          # roster-independent pairs
giye explore ties --config giye.toml --kind cv-listing --out cv.json
giye explore ties --config giye.toml --layers layers.json     # per layer, plus the attribution
giye explore --config giye.toml --assignment groups.json      # scores against the computed ties
```

`--layers` prints one tab-separated line per layer (`entities`, `shared`, `cv_listing`, `roster_independent`) and one `added` line per rule (ties added, merges, merges that added ties). The ties file is the list `giye explore --assignment … --ties` reads. Without `--ties`, `giye explore --assignment` computes the roster-independent ties itself. The evaluator drops pairs whose ends are not in the assignment, so the assignment fixes the population.

The resolver needs the archive's gazetteer. The packaged compact city table resolves fewer place fragments than the GeoNames tree the production archive uses (`[normalize] reference`), so more fragments stay institutions and the counts differ. The paper's numbers are the production configuration.

## Division evaluation

`evaluate(assignment, ties, seed=0, n_boot=100, n_metric_boot=500, refit=None)` takes an assignment `{person_id: group}` and unordered pairs of person ids. A pair is an outcome the caller has already filtered. The flock evaluation's primary outcome is two people at the same institution in the same year, after dropping a CV row that restates that person's own roster edition in that edition's year (`roster_independent_ties` above). This function does not apply that filter. It trusts the pairs it is given, so the same scores can be computed on any field.

`None` as a group means the person is unplaced: counted in coverage, left out of the pairs.

| Piece | What it returns |
|---|---|
| Coverage | `placed_share`, `n_groups`, `largest_share`, `n_singletons`, `sizes_desc`. |
| Stability | Mean adjusted Rand of `n_boot` draws of people with replacement, against the full assignment, on the people the draw contains. Also the 5th and 95th percentiles and the mean share omitted. |
| Lift | Share of ties that fall inside a group, divided by the share of all pairs that fall inside a group. 1 is chance. `None` when there are no ties, no non-ties, or nobody shares a group. |
| AUC | `(TPR + TNR) / 2` for the binary score "same group". |
| Intervals | 2.5 and 97.5 percentiles, linear, of the draws whose lift and AUC are defined. A draw is a multiset: two copies of one person are not a pair; people `i` and `j` count `count[i] × count[j]` times. |

Stability and the metric interval each construct their own `numpy.random.Generator(seed)`. One stream does not depend on the other. Without `refit`, a draw keeps the labels it was given, so the adjusted Rand of a fixed assignment is 1. That is the right answer for a rule that does not look at the other people (a person's group is a function of that person alone). `refit(ids, draw)` may label the draw again; the index is then how often that refit recovers the full assignment. Adjusted Rand is implemented in this module. It is the Hubert–Arabie index, including the convention that a perfect agreement scores 1 when the chance term is undefined.

`pair_scores`, `coverage`, `adjusted_rand` and `bootstrap_stability` are the same pieces, separately.

The package ships no clustering stack. The archive once built candidate divisions with `scikit-learn`, `igraph` and `leidenalg`; the scores do not import them. A caller who refits k-means or Leiden does that in `refit` and installs those libraries themselves.

## How the dropped divisions were scored

Both decisions use an assignment, roster-independent co-presence ties, and these functions. The thresholds were fixed before the numbers were read.

**Embedding k-means (G0).** Six clusters, the published embedding. The outcome was roster-independent institution co-presence: 6363 ties among 499 people with a CV, on the 3 October 2026 ledger (`roster_independent_ties`, limited to the embedded people; the ledger has grown since). `evaluate(..., seed=0)` with 500 person-draws gave lift 1.020, 95% interval [1.004, 1.041], AUC 0.5105. The interval sits above 1, and the lift is 1.02 with an AUC of 0.51. That is chance for any use that needs the groups to recover who spends time together. The division was not kept. Reproducing the decision is running `evaluate` on that assignment and those ties and applying the same reading: a lift this close to 1, with AUC this close to 0.5, is not a division of the field. The module does not hide that comparison behind a cutoff, because the interval alone (it excludes 1) would have kept a result the archive rejected.

**Practice groups.** Medium, format and theme labels, scored against the same co-presence ties and against a split of the evidence that built them. A candidate was valid only when all of these held, and was kept only when it was also better than roster entry and the roster communities:

- mean bootstrap adjusted Rand at least 0.5
- volume eta-squared under 0.06 (Cohen's medium cut; not computed here)
- both held-out lifts have a 95% interval entirely above 1 (`lift_ci95[0] > 1` on each block)
- co-presence lift, and both held-out lifts, higher than roster entry and every roster-community resolution

No practice candidate cleared every bar. Eta-squared and the split-half refit are not in this module. The co-presence lift, its interval, the AUC and the adjusted Rand are.

## Where the demo output lives

The tests are the demo output. `tests/test_explore_rim.py` builds the ring from small synthetic ledgers (binning, the pre-2000 grid, staff exclusion, the team block, a field-file family fold) and from `examples/demo/` after `run_demo`. `tests/test_explore_ties.py` locks both tie definitions on synthetic rows (the publishable, year-flag and funder filters, the same-year restatement rule), one tie per name-rule layer from the four spellings of one museum, the replay's pair counting, and the CLI. The demo summary prints the demo's ties per layer (see `examples/demo/EXPECTED.md`). `tests/test_explore_evaluate.py` locks the lift 2.5 / AUC 0.875 algebra, the adjusted Rand values, a planted division whose interval excludes 1, a draw of ties whose interval covers 1, and stability 1 for an assignment compared with itself. No `rim_order.json` of real people is shipped. `giye explore` writes the demo ring; `giye explore ties` writes the demo ties; `giye explore --assignment` prints the scores.
