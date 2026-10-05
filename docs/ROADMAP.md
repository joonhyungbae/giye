# Roadmap to the SoftwareX submission

Source: the production archive's scripts (private repository). Each item is ported without data,
translated to English, made path-independent (configuration instead of fixed paths), and tested.

| # | Work | From (production) | To | Status |
|---|---|---|---|---|
| 1 | Repository skeleton, licence, CI, docs | – | – | done |
| 2 | Cross-script name keys (X1) | `scripts/name_utils.py` | `giye.resolve.names` | done |
| 3 | Configuration (`giye.toml`): paths, field file, territory, frames file, glossaries, language-module entry point | `ledger_lib.py` constants | `giye.config`, `fields/` | done |
| 4 | Ledger schemas, CSV I/O, lock, backups, permanent IDs, retirement | `ledger_lib.py`, `merge_artists.py`, `build_site_dataset.py` (ID part) | `giye.ledger` | done |
| 5 | Fetcher with robots.txt on every request; snapshot store; collector base; frame registry | `collectors/base.py`, `collectors/snapshot.py`, `archive_evidence.py`, `frames.yml` | `giye.collect` | done |
| 5b | RFC 9309 robots parity (status, longest match, refuse before send, every redirect hop, manifest verdict, full-hash lookup); WARC/WACZ and RO-Crate export | `collectors/robots.py`, `collectors/snapshot.py` | `giye.collect`, `giye.export` | done |
| 6 | Example collector on a local HTML fixture (no live site) | one production collector, simplified | `examples/demo/` | done |
| 7 | CV sources, fetch, LLM extraction with schema, cached replay | `discover_cv_sources.py`, `pull_cv_sources.py`, `extract_cvs_llm.py`, `apply_cv_extractions.py` | `giye.extract` | done |
| 8 | Attachment A1–A6, same-person rules E1–E4, team guard, merge | `collectors/base.py`, `resolve_same_person.py`, `expand_team_members.py`, `merge_artists.py` | `giye.resolve`, `giye.ledger` | done |
| 9 | Normalisation P1–P5, gazetteer, institutions V1–V9 with audit | `preprocess/` | `giye.normalize` | done |
| 10 | Exploration: entry-generation rim order; division evaluation (coverage, bootstrap adjusted Rand, lift with a person-level 95% CI, AUC) | `build_rim_order.py`, `research/flocks/evaluate.py`, `research/tendency/groups.py` | `giye.explore` | done for the rim and the evaluation (`giye explore`). Feature embeddings, k by stability (C1) and descriptors (C2) are not ported |
| 11 | Site snapshot, redirects, coverage, versions, citations | `build_site_dataset.py`, `build_rim_order.py` | `giye.publish` | done: snapshot, tombstones, redirects, coverage, versions, citations, and plain HTML. The rim file is written by `giye explore`, not by `giye publish` |
| 12 | Web front-end reading the snapshot; admin/database optional | `src/` (TanStack Start) | `web/` | done for browsing (`web/`). The rim file is produced by `giye explore`. The embedding flight is not built. There is no admin UI |
| 13 | Synthetic demo field and one-command reproduction (`giye demo`). The summary prints merges per rule, T1 blocks, and V7–V9 | – | `examples/demo/` | done |
| 14 | Accuracy audit: extraction, same-person merges, institution merges (sampled, one coder) | – | `docs/EVALUATION.md` | planned |
| 15 | Zenodo DOI, SoftwareX manuscript and code-metadata table | – | `paper/` | planned |
