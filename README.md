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

Python 3.10 to 3.13 on Linux or macOS; CI tests Python 3.10–3.13 on Ubuntu and 3.10 and 3.13 on macOS.
Windows is not supported: the ledger lock uses `fcntl`, so every ledger operation, including
`giye demo`, fails there (WSL is untested). The package is not on PyPI; install it from a clone.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests tools
```

The optional `llm` extra is the Anthropic client. A local OpenAI-compatible server uses `requests`, which is already installed. The demo does not need either.

## One-command demo

From the root of a repository checkout (the demo reads `examples/demo/`, which is not part of the
installed package; `pip install .` and `pip install -e .` both work):

```bash
giye demo
```

That runs collect → extract (replay, no API key) → resolve → normalize → publish → render on
`examples/demo/` and writes the ledger, the snapshot, and one HTML page per person into a new
temporary directory. Nothing is fetched from the network: fixture pages are read locally, and
`robots.txt` is still checked. The command prints how many people, roster rows, and activities
were published, how many merges each identity rule made, how many pairs T1 blocked, how many
review-queue items are open, how many institution merges V7, V8 and V9 made, how many
co-presence ties each name-rule layer gives, and the output paths. After the publish step's
one line (`site artists=…`), the summary starts with the run date, fixed at 2026-01-15 (the date the golden test uses). The synthetic CVs treat 2026 as
the current year, so those counts do not change with the system date. `examples/demo/EXPECTED.md`
lists the cases and the summary lines.

```bash
giye demo --output /tmp/giye-demo
```

Open `/tmp/giye-demo/site/html/index.html` in a browser. Each fact on a person page links to its
source.

## Pipeline

collect → extract (LLM, optional) → ledger → resolve → normalize → publish → explore → website.
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
giye explore ties --config examples/demo/giye.toml --out ties.json --layers
giye render --config examples/demo/giye.toml
giye run --config examples/demo/giye.toml
giye export warc --config examples/demo/giye.toml
giye export warc --config examples/demo/giye.toml --wacz
giye export ro-crate --config examples/demo/giye.toml
```

`giye explore` writes `<data>/site/rim_order.json`; `giye publish` rewrites it when it is already there, so a person hidden or unpublished since drops out of the ring without a new `explore`. With `--assignment` (a JSON object of person id to group) it also prints coverage, group count, lift, AUC, and stability against `--ties` (a JSON list of pairs), or, when `--ties` is omitted, against the roster-independent co-presence ties computed from the ledger. `giye explore ties` writes those ties (`--out`, a JSON list of ledger-id pairs; `--kind cv-listing` for the CV-listing definition) and, with `--layers [PATH]`, prints the institution entities and both tie counts with V7–V9 off and cumulatively on, plus the ties each rule added (V7a–d, V7e, V8, V9); a path also writes them as JSON. See [docs/EXPLORE.md](docs/EXPLORE.md). `giye run` runs collect, extract, resolve, normalize, publish, and explore. The ledger command only prints counts, so it is not one of those steps. Extract with no API key reads the replay cache.

`giye collect --from-snapshots` and `giye run --from-snapshots` re-run every configured roster collector against the bodies already kept under `data/raw/*/snapshots/` instead of the network. No socket is opened, robots.txt is not fetched again, and no new snapshot line is written. Each roster row's `collected_at` is the UTC date of that page's `fetched_at` in the manifest, not the date of the re-run, so a register rebuilt from the same pages matches the ledger those pages already produced. Every kept body is checked against the SHA-256 on its manifest line and in its file name when it is read back (re-collection, WARC and RO-Crate export); a body that no longer matches stops the command with an error naming the file. A kept CV text (`data/raw/cv/<ledger id>/<source id>/<date>-<hash8>.txt`) is checked the same way against its `cv_sources.content_sha256` (the whitespace-insensitive fingerprint written when it was pulled) whenever extraction, grounding, normalisation, or an audit sample reads it. A body that the manifest lists but that is gone from disk stops these commands too (exit 2, naming the missing files); `--allow-missing` on `collect --from-snapshots`, `export warc` and `export ro-crate` goes on without it and reports it. This is a self-consistency check: it catches corruption and an edit that does not also rewrite the manifest line and rename the file, not a deliberate rewrite of all three, which only a copy kept elsewhere (the digests in an earlier WARC or RO-Crate export, or an off-machine backup) can show.

`giye export warc` writes the snapshot store as WARC 1.1. Each kept body is a response
record with a reconstructed status line and `Content-Type`. Original response headers were
not stored before manifest version 2; the file says so. Each kept CV is a response record
(the page as fetched), a conversion record (the text the pipeline read, checked against
`content_sha256` first) and a metadata record (its `cv_sources` row). `--wacz` also writes a WACZ package. Both exports leave out people hidden by request and CVs of people who are not
published, and say so in their metadata; `--include-hidden` includes hidden people.
`giye export ro-crate` writes RO-Crate 1.1 metadata for the run (software version, config
hash, roster and CV inputs, snapshot files, and one action per stage with its rule ids).
The root dataset has `name`, `description`, and `datePublished`. The software entity is
licensed AGPL-3.0-only. The dataset's `license` is the run's data licence when
`[publish] data_license` or `[archive] data_license` is set (`data_licence` is the same
key; `[publish]` wins). Otherwise it points to `#no-data-licence`, a statement that no
data licence is granted (the software licence does not cover the data). The root's `author`
and `publisher` are an `Organization` named by `[publish] citation_author` (with `site_url`),
and every file entity has an `encodingFormat`, so the crate also passes the RO-Crate
validator's RECOMMENDED level.
A file entity's `@id` is a path inside the crate directory: files outside it are copied in
(`data/<path under the data directory>`, `config/<config file>`), so the crate is
self-contained and carries no local absolute path.

`giye collect` on the demo config writes under `examples/demo/data/`. `giye demo` does not: it
uses a separate output directory so the fixtures stay clean.

## Configuration

An archive is a `giye.toml` next to its `frames.yml`. Paths in the file are relative to the file,
not to the process. See `examples/demo/giye.toml`.

- `[archive]` — name, `id_prefix` (default `GY`), territory (frame rule F3), languages
- `[paths]` — `data` (ledger, processed, site), `frames`, and `field` (the field file: event patterns, team words, tag lists, rim aliases, and optionally the CV extraction prompt as `[extract] prompt`)
- `[collect]` — contact user agent, delay, timeouts, collector modules, offline fixtures
- `[extract]` — `provider` (`anthropic` or `openai_compatible`), `model`, `base_url`, `api_key_env`, `chunk_chars`, `reasoning_effort`, replay cache, CV locations
- `[resolve]` — local CV directory, extra event patterns for rule E2 (they replace a field-file key of the same code)
- `[normalize]` — `language_module` (default `giye.normalize.lang.ko_en:KoEn`), glossary, gazetteer, GeoNames tree, which of V7–V9 to apply
- `[publish]` — `site_url` (required for `giye publish`), `dataset_version` (default `0.2`), `dataset_title`, `citation_author`, `data_license`

The default citation author is `기예 Giye`, matching the reference deployment. There is no default
site origin: `giye publish` stops when `[publish] site_url` is unset. A demo or another field sets its own
(the demo uses `https://example.org`). See [docs/CONFIG.md](docs/CONFIG.md) for every key.

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

## Decisions a person makes

The pipeline queues a pair it will not merge, and a page can be hidden. These commands record that decision. Each ledger write copies the file into `data/work/backups/` first.

```bash
giye queue list --config giye.toml
giye queue list --config giye.toml --kind possible_same_person --status open
giye queue decide QUEUE_ID --decision merge --evidence "E1 https://example.org/studio both rows list this site"
giye queue decide QUEUE_ID --decision distinct
giye queue decide QUEUE_ID --decision dismiss --note "not this edition"
giye merge --config giye.toml KEEP_ID DROP_ID --evidence "H same person in both catalogues, checked by the editor 2026-01-15"
giye split --config giye.toml --membership LEDGER_ID@FRAME_CODE --evidence "H the 2022 fellow is another person; 2026-01-20" --dry-run
giye split --config giye.toml --from splits.csv
giye hide GY-000010 --reason "asked to be removed"
giye unhide GY-000010
giye evidence --config giye.toml
```

Every command reads `giye.toml` in the current directory when `--config` is omitted. `KEEP_ID` and `DROP_ID` are a ledger id or a `gy_id`. A merge a person makes needs the same kind of evidence as an automatic one: an E-code (E1–E4, or X1+E1 … X1+E4) followed by a citation (an http(s) URL, a CV source id, or a roster edition code), or `H` (a person's judgement) followed by the reason (at least three words) and a date no later than today. Free text alone is refused, and the cited evidence must hold on the ledger: an E1 URL must be a website of both records, an E2 CV source must belong to one record and list a roster edition of the other, an E3 or E4 citation must name the shared work or team, and X1+ also needs the name keys to meet (docs/RULES.md). A pair decided `distinct` is not merged unless the library call passes `override_distinct=True`, and the evidence then records the decision it overrides. `merge` refuses a team paired with a person (T1) and retires the dropped `gy_id` with a redirect, the same way an automatic merge does. `split` undoes a wrong attachment: the roster membership `LEDGER_ID@FRAME_CODE` and that edition's roster activity rows move to a new record with a new `gy_id`, named from the roster line (or `--name-ko` / `--name-en`), while CV rows stay with the CV's owner and the old `gy_id` keeps its page. It needs `H` evidence (a reason and a date no later than today), refuses a hidden record and a record's only membership, and records the pair as decided distinct so `resolve` does not join them again. `--from` applies a CSV with columns `membership_id`, `evidence` and optional `name_ko`, `name_en`, all rows under one backup or none (docs/RULES.md, Splits). `hide` sets `HIDDEN_BY_REQUEST`; publish then writes a tombstone with no name. `evidence` keeps a copy of every URL the ledger cites.

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

Identity is two steps. A new roster row joins an existing person only under attachment rules A1–A6, and the membership row records the rule. Two existing records merge only on written evidence (E1–E4, X1, with the team guard T1). Derived values P1–P6 and institution rules V1–V9 read the language module and the field file.

## Data policy

The software publishes websites for reading, not datasets for harvesting: no bulk download and no
public API. Person-level data are not in this repository and should be shared only on request
under a data-use agreement. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), "Guarantees".

## Status

Ported and tested: collection (F1–F5, RFC 9309 robots.txt, attachment A1–A6), WARC and RO-Crate export, the ledger, CV extraction with a replay cache,
same-person resolution (E1–E4, T1, X1), normalisation (P1–P6, V1–V9), the site snapshot, the entry-generation rim, co-presence ties and division scores (`giye explore`), the
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
