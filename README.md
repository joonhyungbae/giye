# Giye

**Giye** (기예, 技藝) is open-source software for building a **provenance-first census archive of a
creative field**: it collects the field's programme rosters and the public CVs of the people on
them, keeps every fact with its source, resolves who is the same person across scripts and
spellings, normalises institutions, and publishes a page for every person, with a permanent id
and a citation.

It was built for Korean media art. The reference deployment is **[giye.org](https://giye.org)**.

> This repository contains software only. It distributes no person-level data. The demo runs on a
> synthetic field of fictitious people and `example.org` URLs.

## Install

Python 3.10 or newer.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
```

The optional `llm` extra is the live extraction client. The demo does not need it.

## One-command demo

From the repository root:

```bash
giye demo
```

That runs collect → extract (replay, no API key) → resolve → normalize → publish → render on
`examples/demo/` and writes the ledger, the snapshot, and one HTML page per person into a new
temporary directory. Nothing is fetched from the network: fixture pages are read locally, and
`robots.txt` is still checked. The command prints how many people, roster rows, and activities
were published, how many merges each identity rule made, how many review-queue items are open,
how many institution merges each venue rule made, and the output paths.

```bash
giye demo --output /tmp/giye-demo
```

Open `/tmp/giye-demo/site/html/index.html` in a browser. Each fact on a person page links to its
source. Docker runs the same command:

```bash
docker build -t giye .
docker run --rm giye
```

The image is `python:3.11-slim`. Its default command is `giye demo`.

## Pipeline

collect → extract (LLM, optional) → ledger → resolve → normalize → explore → publish → website.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the stages and guarantees,
[docs/RULES.md](docs/RULES.md) for every rule, and [docs/SNAPSHOT.md](docs/SNAPSHOT.md) for the
JSON the site reads.

```bash
giye collect --config examples/demo/giye.toml
giye extract --config examples/demo/giye.toml --replay-only
giye resolve --config examples/demo/giye.toml
giye normalize --config examples/demo/giye.toml
giye publish --config examples/demo/giye.toml
giye render --config examples/demo/giye.toml
```

`giye collect` on the demo config writes under `examples/demo/data/`. `giye demo` does not: it
uses a separate output directory so the fixtures stay clean.

## Configuration

An archive is a `giye.toml` next to its `frames.yml`. Paths in the file are relative to the file,
not to the process. See `examples/demo/giye.toml`.

- `[archive]` — name, `id_prefix` (default `GY`), territory (frame rule F3), languages
- `[paths]` — `data` (ledger, processed, site) and `frames`
- `[collect]` — contact user agent, delay, timeouts, collector modules, offline fixtures
- `[extract]` — model, replay cache, CV locations
- `[resolve]` — local CV directory, extra event patterns for rule E2
- `[normalize]` — glossary, gazetteer, GeoNames tree, which of V7–V9 to apply
- `[publish]` — `site_url`, `dataset_version` (default `0.2`), `dataset_title`, `citation_author`

The default citation author is `기예 Giye` and the default site origin is `https://giye.org`,
matching the reference deployment. A demo or another field sets its own.

## Extending to another field

1. **Frame.** Add a programme to `frames.yml` with a code, names, a public `source_url`, and a
   sentence for each of F1–F5 (`eligibility.decision` plus `f1_purpose` … `f5_period`). The
   loader checks that the sentences are present. It does not re-decide them. F3 reads
   `archive.territory`.
2. **Collector.** Subclass `giye.collect.RosterCollector`, set `frame` to that code, and yield
   `Edition`s from `editions()` after `self.fetch(url)`. Point `[collect] collector_modules` at
   the module. `fetch` checks `robots.txt` before every request.
3. **Language.** Person-name keys, institution glossaries, and the place gazetteer are a
   language module (`giye.normalize.language`). The Korean–English module is the default.
   `[normalize] glossary` and `[normalize] gazetteer` replace its tables. `[resolve.event_patterns]`
   adds the phrases rule E2 looks for in a CV.

Identity rules E1–E4, the team guard T1, and the cross-script keys X1 stay as they are. Derived
values P1–P5 and institution rules V1–V9 read those tables.

## Data policy

The software publishes websites for reading, not datasets for harvesting: no bulk download and no
public API. Person-level data are not in this repository and should be shared only on request
under a data-use agreement. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), "Guarantees".

## Status

Ported and tested: collection (F1–F5, robots.txt), the ledger, CV extraction with a replay cache,
same-person resolution (E1–E4, T1, X1), normalisation (P1–P5, V1–V9), the site snapshot, and the
offline demo. Exploration and the web front-end are still planned. Progress is tracked in
[docs/ROADMAP.md](docs/ROADMAP.md).

## Licence

MIT (see [LICENSE](LICENSE)). Reference data used by some rules (e.g. GeoNames) keep their own
licences and are downloaded separately.

## Citation

See [CITATION.cff](CITATION.cff). To cite a record built with Giye, use the APA, Chicago, or
BibTeX text in the snapshot's `citations.json`.
