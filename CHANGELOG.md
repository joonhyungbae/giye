# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-10-06

First version of the package: a data-free toolkit that builds a provenance-first person register of a creative field from public programme rosters and CVs, and the website that serves it.

- Collects programme rosters through a fetcher that checks robots.txt before every request and every redirect, and stores original bytes in a content-addressed snapshot.
- Extracts public CVs into a validated activity schema, with a replay cache so a run can be repeated without calling a model.
- Resolves the same person across scripts and spellings, refuses a team CV as a person, and retires merged ids instead of renumbering them.
- Normalises text, places and institutions with recorded rules, including a Korean–English language module for glossaries and the gazetteer.
- Explores an entry-generation rim order and scores a division of the field (coverage, adjusted Rand, lift, AUC).
- Publishes a site snapshot with permanent ids, redirects for retired ids, coverage, dataset versions, and APA, Chicago and BibTeX citations.
- Renders one plain HTML page per person, and serves the same snapshot from the TanStack Start site in `web/`.
- Draws the home visualization's heavy layers (record marks, chords, threads, piers) with WebGL2 and keeps text in Canvas 2D; the WebGL output is matched to Chrome's GPU Canvas 2D (same order, alpha quantisation and one-paint-per-call union), and browsers without WebGL2, or a lost context, fall back to Canvas 2D. `?renderer=2d|gl` forces either for comparison.
- Measures the home canvas with `web/scripts/perf/` (calls and frame times per phase, deterministic screenshots, pixel diffs between renderers).
- Exports the snapshot store as WARC (optional WACZ) and writes an RO-Crate description of a run.
- Reproduces the pipeline on a synthetic field with `giye demo`, without fetching the network.
- Runs on Linux and macOS with Python 3.10 to 3.13. Windows is not supported.
- Decides identity only on written rules or a named person: a manual merge's H evidence names who judged and may not predate the records it joins, and E3/E4 compare a bracketed title or team with the shared one.
- Queues a Korean and an English copy of one CV event (X2) for a person instead of folding it; `giye queue decide` records the decision, which every apply honours.
- Hides and queues every CV row whose year or venue is not in its CV text (grounding), including rows of stale readings.
- Re-applies a person's own correction of a CV line after every extraction (rule S1, renamed from C1 so that C1 stays the planned cluster rule).
- Adds `giye extract --strict`, a stratum-weighted estimate and refusal of unknown labels in the audit score, an opt-in TLS fallback, atomic JSON writes, and an RO-Crate author typed as a person or an organization.
- Reads every name in Unicode NFC wherever identity reads names (attachment A1–A6, X1, the E-rule name checks), so Hangul written in decomposed form neither gets around the differing-Hangul rule nor splits one name into two records.
- Reads a comma-inverted Latin name (`LEE, Ann`) and one with a trailing bracketed qualifier (`Ann Lee (KR)`) as a Latin-only personal name, so A2 no longer joins such rows across programmes on the Latin string alone.
- Lists under "Not checked, by design" an invented venue name with the real other-script name in brackets, and a year and venue taken from different lines; rule 4 stays as it is, because asking the two forms to read alike would hide correct translated names.
- Makes the export metadata true about hiding: shared roster pages that also list a hidden person are kept unredacted as evidence for everyone else on them, and counted; `giye export … --leave-out-shared-pages` leaves them out as well.
- Makes `giye queue decide … merge` keep the record with the lower `gy_id`, as the author's judgement scripts do, instead of the item's own (usually newer) record, and documents which record each merge path keeps.
