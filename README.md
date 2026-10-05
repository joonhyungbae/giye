# Giye

**Giye** (기예, 技藝) is open-source software for building an **evidence-gated, provenance-first archive of a
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

The optional `llm` extra is the Anthropic client. A local OpenAI-compatible server uses `requests`, which is already installed. The demo does not need either.

## One-command demo

From the repository root:

```bash
giye demo
```

That runs collect → extract (replay, no API key) → resolve → normalize → publish → render on
`examples/demo/` and writes the ledger, the snapshot, and one HTML page per person into a new
temporary directory. Nothing is fetched from the network: fixture pages are read locally, and
`robots.txt` is still checked. The command prints how many people, roster rows, and activities
were published, how many merges each identity rule made, how many pairs T1 blocked, how many
review-queue items are open, how many institution merges V7, V8 and V9 made, and the output
paths. `examples/demo/EXPECTED.md` lists the cases and the summary lines.

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
giye explore --config examples/demo/giye.toml
giye render --config examples/demo/giye.toml
giye run --config examples/demo/giye.toml
giye export warc --config examples/demo/giye.toml
giye export warc --config examples/demo/giye.toml --wacz
giye export ro-crate --config examples/demo/giye.toml
```

`giye explore` writes `<data>/site/rim_order.json`. With both `--assignment` (a JSON object of person id to group) and `--ties` (a JSON list of pairs) it also prints coverage, group count, lift, AUC, and stability. `giye run` runs collect, extract, resolve, normalize, publish, and explore. The ledger command only prints counts, so it is not one of those steps. Extract with no API key reads the replay cache.

`giye export warc` writes the snapshot store as WARC 1.1. Each kept body is a response
record with a reconstructed status line and `Content-Type`. Original response headers were
not stored before manifest version 2; the file says so. `--wacz` also writes a WACZ package.
`giye export ro-crate` writes RO-Crate 1.1 metadata for the run (software version, config
hash, roster and CV inputs, snapshot files, and one action per stage with its rule ids).

`giye collect` on the demo config writes under `examples/demo/data/`. `giye demo` does not: it
uses a separate output directory so the fixtures stay clean.

## Configuration

An archive is a `giye.toml` next to its `frames.yml`. Paths in the file are relative to the file,
not to the process. See `examples/demo/giye.toml`.

- `[archive]` — name, `id_prefix` (default `GY`), territory (frame rule F3), languages
- `[paths]` — `data` (ledger, processed, site), `frames`, and `field` (the field file: event patterns, team words, tag lists, rim aliases)
- `[collect]` — contact user agent, delay, timeouts, collector modules, offline fixtures
- `[extract]` — `provider` (`anthropic` or `openai_compatible`), `model`, `base_url`, `api_key_env`, `chunk_chars`, `reasoning_effort`, replay cache, CV locations
- `[resolve]` — local CV directory, extra event patterns for rule E2 (they replace a field-file key of the same code)
- `[normalize]` — `language_module` (default `giye.normalize.lang.ko_en:KoEn`), glossary, gazetteer, GeoNames tree, which of V7–V9 to apply
- `[publish]` — `site_url`, `dataset_version` (default `0.2`), `dataset_title`, `citation_author`

The default citation author is `기예 Giye` and the default site origin is `https://giye.org`,
matching the reference deployment. A demo or another field sets its own.

## Running extraction on a local model

CV text can stay on the machine. Ollama, vLLM, and llama.cpp each serve an OpenAI-compatible
chat endpoint. `provider = "openai_compatible"` posts the same prompt and CV there, and asks
for the extraction JSON schema. No API key is required, and the `llm` extra is not installed
for this path. When the environment variable named by `[extract] api_key_env` is set (default
`GIYE_LLM_API_KEY`), that value is sent as a Bearer token. The default `[extract] provider`
is `anthropic`, so an existing config keeps the hosted call.

```bash
ollama pull qwen2.5:14b
giye extract --config giye.toml --provider openai_compatible \
  --base-url http://localhost:11434/v1 --model qwen2.5:14b
```

`[extract] base_url` defaults to `http://localhost:11434/v1`. `[extract] model` is the id sent
to that server, or to Anthropic when the provider is `anthropic`. The flags override the file.
The replay cache key is the CV hash, the prompt hash, and that model string as given.

`[extract] chunk_chars` splits a CV into pieces of at most that many characters before the call, because a long CV and its JSON reply do not fit a local context and fewer rows survive as the file grows. It defaults to 0 (off) for `anthropic` and 8000 for `openai_compatible`; a value in the file wins, and changing it extracts again. `[extract] reasoning_effort` (`none`, `low`, `medium`, `high`) is sent to an OpenAI-compatible server only when set; `none` stops a thinking model (Qwen3.5, Gemma 4) from writing its reasoning before the JSON, which otherwise multiplies the time per call.

## Extending to another field

1. **Frame.** Add a programme to `frames.yml` with a code, names, a public `source_url`, and a
   sentence for each of F1–F5 (`eligibility.decision` plus `f1_purpose` … `f5_period`). The
   loader checks that the sentences are present. It does not re-decide them. F3 reads
   `archive.territory`.
2. **Collector.** Subclass `giye.collect.RosterCollector`, set `frame` to that code, and yield
   `Edition`s from `editions()` after `self.fetch(url)`. Point `[collect] collector_modules` at
   the module. `fetch` checks `robots.txt` before every request.
3. **Language and field.** Person-name keys, institution glossaries, and the place gazetteer are a
   language module selected by `[normalize] language_module`. The Korean–English module is the default.
   `[normalize] glossary` and `[normalize] gazetteer` replace its tables. Programme phrases, the team
   prefix, team words, tag lists, and rim aliases live in the field file (`[paths] field`), not in the
   package. `[resolve.event_patterns]` replaces a phrase for one frame code.
4. **Website.** `web/` reads the snapshot. Origin, contact address, and the field name in both
   languages are `VITE_GIYE_*` (see `web/README.md`). Point `GIYE_SITE_DIR` at `<data>/site`.

Identity is two steps. A new roster row joins an existing person only under attachment rules A1–A6, and the membership row records the rule. Two existing records merge only on written evidence (E1–E4, X1, with the team guard T1). Derived values P1–P5 and institution rules V1–V9 read the language module and the field file.

## Data policy

The software publishes websites for reading, not datasets for harvesting: no bulk download and no
public API. Person-level data are not in this repository and should be shared only on request
under a data-use agreement. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), "Guarantees".

## Status

Ported and tested: collection (F1–F5, RFC 9309 robots.txt, attachment A1–A6), WARC and RO-Crate export, the ledger, CV extraction with a replay cache,
same-person resolution (E1–E4, T1, X1), normalisation (P1–P5, V1–V9), the site snapshot, the entry-generation rim and division scores (`giye explore`), the
offline demo, and the web front-end (`web/`). Feature embeddings and cluster descriptors (C1, C2) are not ported. Progress is tracked in
[docs/ROADMAP.md](docs/ROADMAP.md).

## Licence

GNU Affero General Public License v3.0 only (AGPL-3.0-only; see [LICENSE](LICENSE)). Anyone may use, study and change
the code, including to run their own archive; if you run a modified version as a network service, you must offer its users
the source of that version under the same licence. The archive's data are not part of the software and are not released.
Reference data used by some rules (e.g. GeoNames) keep their own
licences and are downloaded separately.

## Citation

See [CITATION.cff](CITATION.cff). To cite a record built with Giye, use the APA, Chicago, or
BibTeX text in the snapshot's `citations.json`.
