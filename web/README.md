# Web front-end

TanStack Start + React + Tailwind site. It reads a site snapshot (`<data>/site/*.json` from
`giye publish`) and needs no database to browse. Self-reports from `/request` are appended to
`requests.jsonl` under the work directory.

## Install, dev, build, run

From this directory:

```bash
bun install
bun run dev          # Vite, host ::, port 8080 (override with --host and --port)
bun run build        # writes .output/ (Nitro node-server)
node .output/server/index.mjs
```

The built server listens on `HOST` and `PORT` (Nitro's defaults are all interfaces and port 3000).
Point it at a snapshot with `GIYE_SITE_DIR`:

```bash
HOST=127.0.0.1 PORT=3000 GIYE_SITE_DIR=/path/to/site node .output/server/index.mjs
```

## Run on the demo

The synthetic field is fictitious people and example.org URLs. From the package root
(the repository root, where `giye` is installed):

```bash
giye demo --output /tmp/giye-demo-data
```

That writes the snapshot to `/tmp/giye-demo-data/site`. Then, from this directory:

```bash
GIYE_SITE_DIR=/tmp/giye-demo-data/site bun run dev
```

A published person on that snapshot is `GY-000001` (`/artist/GY-000001`). `giye demo` without
`--output` uses a fresh temporary directory and prints the path.

## Environment

Read by the server process (`bun run dev` and `node .output/server/index.mjs`). A blank value
is the same as unset.

| Variable | Default | Meaning |
|---|---|---|
| `GIYE_SITE_DIR` | `<cwd>/data/site` | Snapshot directory |
| `GIYE_WORK_DIR` | `<cwd>/data/work` | Self-reports (`requests.jsonl`) |
| `GIYE_TRUSTED_IP_HEADER` | unset | The one header that carries the visitor address (production: `cf-connecting-ip`). Unset: no per-IP limits, only the user-agent rule |
| `GIYE_GUARD_HUMAN_PER_MIN` | `120` | Requests per minute per IP |
| `GIYE_GUARD_CRAWLER_PER_MIN` | `60` | Same, for allowed crawlers |
| `GIYE_GUARD_ENUM_LIMIT` | `80` | Distinct pages one IP may open before it is treated as a harvester |
| `GIYE_GUARD_ENUM_WINDOW_S` | `600` | Window for that count, in seconds |
| `GIYE_GUARD_BLOCK_S` | `900` | How long a blocked IP stays blocked, in seconds |

A guard value that is missing, blank, not an integer, or below 1 keeps the default. The
per-minute window stays 60 seconds.

Inlined by Vite when `bun run dev` starts and when `bun run build` runs. Set them before that
command. The running Node process does not re-read them. Unset, the sentences are the giye.org
deployment.

| Variable | Default |
|---|---|
| `VITE_GIYE_ORIGIN` | `https://giye.org` |
| `VITE_GIYE_CONTACT_EMAIL` | `jh.bae@kaist.ac.kr` |
| `VITE_GIYE_FIELD_KO` | `미디어아트` |
| `VITE_GIYE_FIELD_EN` | `Korean media art` |

`VITE_GIYE_FIELD_KO` is the name in "한국 … 분야". `VITE_GIYE_FIELD_EN` is the name in "the …
field". An English name ending in "art" is also used as "artist" / "artists" on the list page
and the dataset citation title, so the default stays "Korean media artist(s)".

`public/robots.txt` names `https://giye.org/sitemap.xml` and disallows the same training and
bulk crawlers the scrape guard refuses. The sitemap route itself uses the request origin.

## Snapshot files

What this app reads, and whether `giye publish` (and therefore `giye demo`) writes it.

| File | Loader | In the demo snapshot |
|---|---|---|
| `artists.json` | `loadArtists`, `loadAllArtists` | yes |
| `activities.json` | `loadActivities` | yes |
| `links.json` | `loadLinks` | yes |
| `collaborations.json` | `loadCollaborations` | yes |
| `background.json` | `loadBackground` | yes |
| `frames.json` | `loadFrames` | yes |
| `coverage.json` | `loadCoverage` | yes |
| `dataset_versions.json` | `loadDatasetVersions` | yes |
| `artist_stubs.json` | `loadArtistStubs` | yes |
| `gy_redirects.json` | `loadGyRedirects` | yes |
| `vocabularies.json` | `loadVocabularies` | yes, `[]` when the file is absent; an existing file is left as it is |
| `content_pages.json` | `loadContentPages` | yes, same rule. An empty file shows "not written yet" on the criteria, governance, and methodology pages. `/privacy` is written in the route, not in this file |
| `content_revisions.json` | `loadContentRevisions` | yes, same rule |
| `research.json` | `loadResearch` | yes, same rule |
| `citations.json` | not read (the cite dialog builds the sentences) | yes |
| `embedding.json` | `loadEmbeddingSpace` | no. A missing file is null. No route draws the flight view |
| `snapshots/*.json` | `loadSnapshots` | no. A missing directory is an empty list |
| `rim_order.json` | `loadRimOrder` | no. A missing file is null. The home ring then places each person from the years in their frame codes (five-year generations), or from their first listed programme when the file is an older programme-arc order |

`giye render` also writes `html/` next to these files. This app does not read it.
