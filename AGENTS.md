# Giye rules (for every coding agent working here)

Giye is a provenance-first census archive of the Korean media art field, published at **giye.org**, and the
software that builds it. Design decisions are made by Claude; implementation is handed over as written specs
(often to Cursor CLI). When a spec does not cover a design decision, stop and leave it as a question in your
report. Do not decide it yourself.

## Repository layout: one working copy, two git directories (since 2026-10-05)
- This working copy is the checkout of the **public** repository github.com/joonhyungbae/giye (`.git`): the Python
  package `src/giye`, the website `web/` (giye.org is built from it), `tests/`, `docs/`, `examples/`, `deploy/` (the default field file ships in `src/giye/fields/`).
- Private paths stay in the same tree and are versioned by the **private** repository (`.git-private`, remote
  giye-archive, never made public): `data/`, `research/`, `ref/`, `scripts/` (production pipeline and collectors not yet
  ported to the package), `tools/annotate/`, `tools/regress*`, `anno.sh`, `archive/`. They are listed in `.gitignore`.
- Use `git` for public paths and `./gitp` (= `git --git-dir=.git-private --work-tree=.`) for private ones. New private
  files need `./gitp add -f <path>` because `.gitignore` lists them. Never force-add a private path with plain `git`.
- Nothing from `data/`, no real person's name and no real artist URL may enter the public repository. Tests and the demo
  use fictitious people and example.org. Before every public push, scan the tree against `data/ledger/artists.csv` and
  `links.csv` (names as exact tokens, artist URLs).
- Verify the public package with `pytest -q && ruff check src tests tools` (venv `.venv`) and the site with
  `cd web && bunx tsc --noEmit -p . && bun run build`. No releases or tags until the author asks for the final version.
- Licence: GNU AGPL-3.0-only.

## Language
- Everything in this repository is written in **English**: code comments, docstrings, commit messages, README
  and other docs, user-facing notes in scripts. The website stays bilingual (Korean/English) through `t("…", "…")`.
- Existing Korean text is translated when a file is touched. Korean data values (names, titles, glossaries) stay as they are.

## Data
- The ledger `data/ledger/*.csv` is the source of truth. Change it only through scripts, and copy the file to
  `data/work/backups/<file>-<YYYYMMDD>-before-<task>.csv` before writing.
- A row without a source is never published. Every fact row has `source_url` and `collected_at`. Original bytes of
  cited URLs are kept (`scripts/archive_evidence.py`).
- Person IDs (`gy_id`, GY-000001) are permanent: never renumbered or deleted. Merges only through
  `scripts/merge_artists.py` (retired IDs stay in `gy_retired.csv` and redirect).
- Everyone on an admitted roster is published (a CV is not a condition). No person is ranked, recommended or featured.
- No cap, weight or sampling on a person's number of activities. Skew is handled in analysis methods.
- Every design and method choice is a reproducible data rule. No hand-picked exception lists; write the rule and its
  reason in code comments and docs.
- Derived values that do not edit the ledger go through `scripts/preprocess/` → `data/processed/` with the rule id
  and its reason.
- Never fetch hosts that robots.txt disallows or platforms whose terms forbid collection (e.g. Instagram).
  No public API and no bulk export on the website (`src/server.ts` scrape guard). Person-level data are shared
  only on request under a data-use agreement.
- `data/`, `research/` data folders and `ref/` are never committed (see `.gitignore`).

## Code
- Follow the surrounding style. Python scripts start with a docstring saying what, why and how to run; comments
  explain why. The website is TanStack Start + React + Tailwind and reads the file snapshot in `data/site/`.
- There is no database and no hosted editor: Supabase and Lovable have been removed. Do not reintroduce them.
- Verify: Python by running it; the website by `bunx tsc --noEmit -p .` and `bun run build`. After data changes run
  `python3 scripts/preprocess/run.py && python3 scripts/build_site_dataset.py`.
- Python interpreters: research scripts that need statsmodels run with `/usr/bin/python3` (the shell's anaconda
  `python3` cannot import statsmodels.api); embeddings, torch, igraph, leidenalg use `~/.venvs/giye-vec/bin/python`.
  Name the interpreter in the docstring's usage line.
- Commits and pushes are allowed once the checks above pass (user decision, 2026-10-04). One change per commit,
  English message saying what and why. Never force-push or rewrite pushed history.
- When done, end with a report: files changed, checks run and their results, open problems and questions.
