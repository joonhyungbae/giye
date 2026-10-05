# Contributing

Create a virtual environment and install the package with its development tools:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Check the public package with `pytest -q && ruff check src tests tools`. The site is checked with `cd web && bunx tsc --noEmit -p . && bun run build`.

Follow `AGENTS.md`: English in code and docs, ledger changes only through scripts, and no person-level data in the public tree. When a specification leaves a design decision open, stop and ask the author.

Report a bug or a data correction through the contact address in `docs/DEPLOY.md`, or open a GitHub issue on this repository. Include the command you ran and the output.
