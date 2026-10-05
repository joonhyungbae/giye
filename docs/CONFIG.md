# `giye.toml`

One archive is one file. `giye.config.load` reads it. Paths are relative to the file's directory and are resolved to absolute paths. A copied file that sets `frames` to an absolute path and leaves `field` as a bare filename is looked up beside `frames.yml` when that filename is not found from the config directory.

Only `[archive] name` is required. Every other key has a default or may be omitted. Unknown top-level tables are stored on `Config.extra` and nothing reads them.

`[evidence]` is accepted and type-checked. Keys inside it are not read. A disallowed host stays link-only (`giye.collect.evidence`).

## `[archive]`

| Key | Type | Default | Required | Read by | Example |
|---|---|---|---|---|---|
| `name` | string | — | yes | `giye.publish.snapshot` (dataset title when `dataset_title` is empty), `giye.publish.html`, `giye.export.warc`, `giye.export.rocrate` | `"Synthetic media-art field (demo)"` |
| `id_prefix` | string | `"GY"` | no | `giye.ledger` (person ids), `giye.publish.snapshot` (public ids). An empty string is treated as `GY` there. | `"GY"` |
| `territory` | string | `""` | no | `giye.config` stores it. No other module reads it. Frame rule F3 is a sentence in `frames.yml`, not a check against this value. The comment in `giye.config` calls it an ISO 3166-1 alpha-2 code. | `"KR"` |
| `languages` | array of strings | `["en"]` | no | `giye.config` stores it. No other module reads it. | `["ko", "en"]` |

## `[paths]`

| Key | Type | Default | Required | Read by | Example |
|---|---|---|---|---|---|
| `data` | path | `"data"` | no | Every stage, as `data/ledger`, `data/raw`, `data/processed`, `data/site`, and `data/work`. | `"data"` |
| `frames` | path | `"frames.yml"` | no | `giye.collect.frames.load_frames`, then `giye.publish.snapshot` and `giye.collect.evidence`. | `"frames.yml"` |
| `field` | path | unset | no | `giye.field.load_field`. Unset uses an empty field that inherits tag lists from the shipped Korean media-art file. See `docs/FIELD.md`. | `"field.toml"` |

## `[collect]`

| Key | Type | Default | Required | Read by | Example |
|---|---|---|---|---|---|
| `user_agent` | string | `"GiyeArchive/0.1 (+https://example.org/contact)"` | no | `giye.collect.fetch`. It must contain `http://`, `https://`, or `@`. | `"GiyeDemo/0.1 (+https://example.org/contact)"` |
| `min_delay_s` | number | `2.0` | no | `giye.collect.fetch` (per-host delay) | `0.0` |
| `timeout_s` | number | `45.0` | no | `giye.collect.fetch` | `20` |
| `robots_timeout_s` | number | `20.0` | no | `giye.collect.fetch` | `10` |
| `collector_modules` | array of paths, or one path string | `[]` | no | `giye.collect.base` imports each path relative to the config file. Empty means `giye collect` exits 2. | `["collectors.py"]` |

### `[collect.offline_roots]`

Optional table. Key: URL prefix. Value: directory. `giye.collect.fetch` reads that directory instead of opening a socket, and still checks `robots.txt` in the directory. Prefixes are matched longest first. A trailing slash on the prefix is stripped.

```toml
[collect.offline_roots]
"https://example.org" = "fixtures"
```

### Re-collection from kept snapshots

`giye collect --from-snapshots` and `giye run --from-snapshots` are command-line flags, not keys in this file. The fetcher answers each `get(url)` from `<data>/raw/*/snapshots/manifest.jsonl`: the newest servable line whose `url` or `final_url` equals the requested URL. A withdrawn line is not a body. When the collector's own frame also kept the URL, that frame's newest line wins over a newer copy in another frame. The same `fetched_at` keeps the later line. The stored status and content type are returned. A URL with no servable line is HTTP 404 with reason `not kept`. No socket is opened. robots.txt is not read again; the manifest line already holds the verdict from fetch time. `RosterCollector.fetch` does not append a snapshot line in this mode. `collected_at` on the rows written is the UTC date (`YYYY-MM-DD`) of that line's `fetched_at`, not the date of the re-run, so a register rebuilt from the same pages keeps the ledger those pages already produced.

A collector may also POST (`RosterCollector.fetch(url, data=...)`, or `method="POST"`). A form given as a dict is form-encoded in the order given. The manifest line of a POST adds `method` and `body_sha256` (SHA-256 of the bytes sent); a GET line has neither, as before. Replay matches on URL, method and body hash, so two POSTs to one URL replay their own answers and a GET never receives a POST answer. A 301, 302 or 303 after a POST is followed as a GET; 307 and 308 repeat the POST. Older manifest lines written before the package keep the status under `http_status`, which replay reads when `status` is absent; the oldest lines have neither key and no `manifest_version`, and their kept body replays as status 200 (those collectors kept a body only from a successful response).

Replay dates a row from the kept page whose URL is the row's source: `Person.source_url`, then `Edition.fetched_from` (the page actually fetched when the citation is another URL), then `Edition.source_url`. A date the collector states (`Person.collected_at`, then `Edition.collected_at`) wins in both live and replay runs.

Re-collection never deletes an activity row and never blanks a field (`Ledger.apply_roster`). Each row a collector produces is matched against the person's rows with the same origin (edition code): same activity id, else same title and year, else, for the appearance row, the placeholder titled with the edition code. A matched row takes the new values only when the new row fills every column the old one fills; otherwise only its empty columns are filled, and a placeholder never overwrites a titled row. An existing person gains only missing values (a Latin name never fills `name_ko`), note segments are added once with a single pipe-separated `members=` list, and `updated_at` moves only when a field changed.

## `[resolve]`

| Key | Type | Default | Required | Read by | Example |
|---|---|---|---|---|---|
| `cv_dir` | path | unset | no | `giye.resolve.cv`. HTML under that directory fills people who have no `data/work/cv_extract/<ledger_id>.json`. An `<article data-name-ko data-name-en>` matches one ledger row. A `<li data-year data-venue>` is one activity. The match must be unique. The JSON file wins when both exist. | `"fixtures/cv"` |

### `[resolve.event_patterns]`

Optional table. Key: frame-code prefix. Value: regex for rule E2. `giye.resolve.evidence.pattern_table` takes the field file's `[resolve.events]` first, then this table. A prefix listed here replaces that prefix's pattern and keeps the field file's order for the rest. Also read by `giye.resolve.service`, `giye.normalize.service`, and `giye.explore.ties`.

```toml
[resolve.event_patterns]
EXAMPLE-RESIDENCY = "example residency"
```

The demo puts those phrases in `field.toml` and leaves this table out.

## `[normalize]`

| Key | Type | Default | Required | Read by | Example |
|---|---|---|---|---|---|
| `language_module` | string `package.module:Class` | `"giye.normalize.lang.ko_en:KoEn"` | no | `giye.normalize.language.load_language`. The class must provide `load(glossary=, cities=, reference=)` and return a `LanguageModule`. See `docs/LANGUAGE.md`. | `"tests.toy_language:Toy"` |
| `reference` | path | unset | no | Passed to `load` as `reference`. A directory with `geonames/` and `countries/`. GeoNames is not shipped. When set, it replaces the packaged city table. | `"reference"` |
| `glossary` | path | unset | no | Passed to `load` as `glossary`. Replaces the packaged Korean–English glossary YAML. | `"glossary.yaml"` |
| `gazetteer` | path | unset | no | Passed to `load` as `cities`. Replaces the packaged city table. Ignored when `reference` is set. | `"cities.tsv"` |
| `venue_name_rules` | string or array of strings | `""` | no | `giye.normalize.service.parse_name_rules`. Empty means V7, V8, and V9. `none`, `off`, or `base` means none of them. A comma-separated string or a list is a subset of `V7`, `V8`, `V9`. The `giye normalize` CLI can override the file. | `"V7,V8"` |

## `[extract]`

CV extraction. `giye.extract.service` reads these keys. `giye extract` can override `provider`, `base_url`, and `model` on the command line. If the file omitted `chunk_chars`, a CLI `--provider` recomputes that default.

| Key | Type | Default | Required | Read by | Example |
|---|---|---|---|---|---|
| `provider` | string | `"anthropic"` | no | `giye.extract.service`, `giye.extract.provider`. `anthropic` or `openai_compatible`. | `"openai_compatible"` |
| `base_url` | string | `"http://localhost:11434/v1"` | no | `openai_compatible` posts to `<base_url>/chat/completions`. A trailing slash is stripped. Unused by the Anthropic provider. | `"http://localhost:11434/v1"` |
| `model` | string | `"claude-opus-5"` | no | Sent as the model id and stored on the cache record. | `"claude-opus-5"` |
| `temperature` | number | omit | no | Sent only when the key is present. Absent means the request has no temperature parameter. | `0` |
| `reasoning_effort` | string | omit | no | Sent to an OpenAI-compatible server only when set. One of `none`, `low`, `medium`, `high`. | `"none"` |
| `chunk_chars` | integer ≥ 0 | `0` for `anthropic`; `8000` for `openai_compatible` when the key is absent | no | `giye.extract.chunk`. `0` sends the CV whole. An explicit value wins over the provider default, including `0` on a local provider. | `8000` |
| `api_key_env` | string, no whitespace | `"GIYE_LLM_API_KEY"` | no | Name of an environment variable, not the secret. When that variable is set, `openai_compatible` sends it as a Bearer token. A local server does not need one. The Anthropic path uses `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`, not this name. | `"GIYE_LLM_API_KEY"` |
| `cache` | path | `<data>/work/cv_cache` when unset | no | `giye.extract.service` | `"cache"` |
| `allow_team` | boolean | `false` | no | `giye.extract.registry`. A team row (rule T1) is skipped unless this is true. | `false` |

### `[[extract.sources]]`

Optional array of tables. `giye.extract.service` registers each row, then pulls and extracts it. `giye.export.rocrate` lists the same URLs. Each table needs `url` and `lang`. `lang` is `ko`, `en`, or `mixed`.

Match to a ledger person: `ledger_id` if set, otherwise `name_ko` and `name_en` as exact strings (a key that is set must match; a key left empty is not checked). Exactly one row must match. Zero or several is a skip (`no artist for source`).

`source_id` empty means the registry assigns `CV-<ledger_id>-<lang>`, then `-2`, `-3`, when that id is taken. Set `source_id` when a replay file must name the document before a ledger id exists. A duplicate `source_id` is an error. The same `url` for the same person is not added twice.

`fetch_url` empty means pull downloads `url`. Set it when the published page is not the bytes to download. `kind` empty means `giye.extract.text.detect_kind` on `fetch_url` or `url`. `note` is stored on the `cv_sources` row.

| Key | Type | Default | Required |
|---|---|---|---|
| `url` | string | — | yes |
| `lang` | `ko`, `en`, or `mixed` | — | yes |
| `ledger_id` | string | `""` | no |
| `name_ko` | string | `""` | no |
| `name_en` | string | `""` | no |
| `kind` | string | detected from the URL | no |
| `note` | string | `""` | no |
| `fetch_url` | string | `""` | no |
| `source_id` | string | `CV-<ledger_id>-<lang>` | no |

```toml
[[extract.sources]]
name_ko = "김하늘"
lang = "ko"
url = "https://cv.example.org/haneul-ko"
source_id = "CV-DEMO-HANEUL-ko"
note = "fictitious Korean CV"
```

The model must copy `source_id` onto each activity row. A row whose `source_id` was not in the documents just sent is dropped. See `docs/FIELD.md` (extraction prompt).

## `[ledger]`

| Key | Type | Default | Required | Read by | Example |
|---|---|---|---|---|---|
| `keep_backups_days` | integer ≥ 1 | unset (keep every backup) | no | `giye.ledger.io.prune_backups`, through `Ledger.write`. Once per run, after the run's first backup, backups in `<data>/work/backups` dated (by the UTC day in the file name) more than this many days ago are deleted. Only names the package writes (`<file>-<YYYYMMDD>-before-<task>[-<n>].csv[.gz]`) are touched, and every backup on the newest day of each ledger file is kept whatever its age. The demo leaves it unset. Production sets 90. | `90` |

Backups themselves are not configurable: each run copies a ledger file once per task before its first write, gzip-compressed, to `<file>-<YYYYMMDD>-before-<task>.csv.gz` (docs/RULES.md, ledger invariants).

## `[publish]`

| Key | Type | Default | Required | Read by | Example |
|---|---|---|---|---|---|
| `site_url` | string | — | yes, for `giye publish` | `giye.publish.snapshot` and `giye.publish.cite`. Citations use `<site_url>/artist/<id>` and `<site_url>/data`. A trailing slash is stripped. Unset or empty is a config error at publish: there is no default origin. The demo sets `https://example.org`. | `"https://example.org"` |
| `dataset_version` | string | `"0.2"` | no | `giye.publish.snapshot` (`dataset_versions.json` and the citation) | `"0.2"` |
| `dataset_title` | string | `""` (the archive `name`) | no | `giye.publish.snapshot` | `"Synthetic media-art field (demo)"` |
| `citation_author` | string | `"기예 Giye"` | no | `giye.publish.snapshot`, `giye.publish.cite` | `"Example Archive"` |

### `[publish.cadence]`

Optional table. Key: a label. Value: a string saying what runs. `giye.publish.snapshot` writes it to `coverage.json` only when the table is non-empty. The package does not schedule anything.

```toml
[publish.cadence]
weekly = "link check, site rebuild"
```

## Re-check

Keys below are the `.get("…")` names in `src/giye/config.py`, plus the three keys tested with `in` / `not in` (`name`, `url`, `lang`, `chunk_chars`, `temperature`). Re-run:

```bash
python3 - <<'PY'
import ast
from pathlib import Path
tree = ast.parse(Path("src/giye/config.py").read_text(encoding="utf-8"))
gets, ins = [], []
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            gets.append((node.lineno, node.args[0].value))
    if isinstance(node, ast.Compare):
        for op, _comp in zip(node.ops, node.comparators):
            if isinstance(op, (ast.In, ast.NotIn)) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
                ins.append((node.lineno, type(op).__name__, node.left.value))
print("GET")
for line, key in gets:
    print(f"{line}\t{key}")
print("IN")
for line, op, key in ins:
    print(f"{line}\t{op}\t{key}")
PY
```
