# Roadmap to the SoftwareX submission

Source: the production archive's scripts (private repository). Each item is ported without data,
translated to English, made path-independent (configuration instead of fixed paths), and tested.

| # | Work | From (production) | To | Status |
|---|---|---|---|---|
| 1 | Repository skeleton, licence, CI, docs | – | – | done |
| 2 | Cross-script name keys (X1) | `scripts/name_utils.py` | `giye.resolve.names` | done |
| 3 | Configuration (`giye.toml`): paths, field name, territory, frames file, glossaries | `ledger_lib.py` constants | `giye.config` | started |
| 4 | Ledger schemas, CSV I/O, lock, backups, permanent IDs, retirement | `ledger_lib.py`, `merge_artists.py`, `build_site_dataset.py` (ID part) | `giye.ledger` | done |
| 5 | Fetcher with robots.txt on every request; snapshot store; collector base; frame registry | `collectors/base.py`, `collectors/snapshot.py`, `archive_evidence.py`, `frames.yml` | `giye.collect` | done |
| 6 | Example collector on a local HTML fixture (no live site) | one production collector, simplified | `examples/demo/` | done |
| 7 | CV sources, fetch, LLM extraction with schema, cached replay | `discover_cv_sources.py`, `pull_cv_sources.py`, `extract_cvs_llm.py`, `apply_cv_extractions.py` | `giye.extract` | planned |
| 8 | Same-person rules E1–E4, team guard, merge | `resolve_same_person.py`, `expand_team_members.py`, `merge_artists.py` | `giye.resolve` | done |
| 9 | Normalisation P1–P5, gazetteer, institutions V1–V9 with audit | `preprocess/` | `giye.normalize` | planned |
| 10 | Exploration: features, optional text embeddings, k by stability (C1), descriptors (C2) | `feature_schema.py`, `build_embedding_space.py` | `giye.explore` | planned |
| 11 | Site snapshot, redirects, coverage, versions, citations | `build_site_dataset.py`, `build_rim_order.py` | `giye.publish` | planned |
| 12 | Web front-end reading the snapshot; admin/database optional | `src/` (TanStack Start) | `web/` | planned |
| 13 | Synthetic demo field and one-command reproduction (Docker) | – | `examples/demo/`, `Dockerfile` | planned |
| 14 | Accuracy audit: extraction, same-person merges, institution merges (sampled, one coder) | – | `docs/EVALUATION.md` | planned |
| 15 | Zenodo DOI, SoftwareX manuscript and code-metadata table | – | `paper/` | planned |
