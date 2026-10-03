# Giye

**Giye** (기예, 技藝) is open-source software for building a **provenance-first census archive of a
creative field**: it collects the field's programme rosters and the public CVs of the people on
them, keeps every fact with its source, resolves who is the same person across scripts and
spellings, normalises institutions, maps the field for exploration, and publishes a website on
which every person has a permanent, citable page.

It was built for, and runs, the live archive of Korean media art: **[project domain — to be set]**.

> This repository contains software only. It distributes no person-level data. The demo runs on a
> synthetic field.

## Pipeline

collect → extract (LLM, optional) → ledger → resolve → normalize → explore → publish → website.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the stages and guarantees, and
[docs/RULES.md](docs/RULES.md) for every rule the pipeline applies.

## Status

Early port from the production archive. Ported and tested so far: cross-script name keys
(`giye.resolve.names`, rule X1). Progress is tracked in [docs/ROADMAP.md](docs/ROADMAP.md).

## Quick start (development)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
giye --help
```

## Data policy

The software publishes websites for reading, not datasets for harvesting: no bulk download, no
public API, a scrape guard on the web server. Archives built with Giye should share person-level
data only on request under a data-use agreement. See ARCHITECTURE.md, "Guarantees".

## Licence

MIT (see [LICENSE](LICENSE)). Reference data used by some rules (e.g. GeoNames) keep their own
licences and are downloaded separately.

## Citation

See [CITATION.cff](CITATION.cff).
