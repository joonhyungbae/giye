# Giye career evidence (MCP): design draft

Status: draft for the author's review, 2026-10-07. Nothing here is implemented yet.
Open questions are collected in the last section; none of them is decided in this document.

## 1. What it is

Giye's career service is an MCP server. A practitioner (or the AI assistant they already use) gives it a career
at some point in time, and the server answers with **evidence about where that career stands in the
field and what careers in a similar position did next**. The evidence comes from a Giye archive:
the roster layer (everyone on the field's admitted programme rosters) and the CV layer (people
whose public CVs were extracted).

It carries the Giye name and lives in this repository. The archive site records; the career
server interprets. They share the name, the preprocessing and the deployment, but not the data
they can reach: the site reads the site snapshot, the career server reads only the career bundle
(section 4), and neither reads the other's files.

### What it can answer (covered)

| Question a practitioner asks | Evidence |
|---|---|
| Where does my career stand? | Career age, generation, activity mix, domestic/overseas share, region, funding share, compared with the distribution of CV-layer careers of the same career age and generation |
| Which programmes took people with a career like mine? | Each programme's entry profile: career age at entry, activity mix before entry, overseas share before entry, team acceptance, access mode |
| What did careers in a similar position do in the next years? | Base rates of the kinds of activity, kinds of institution and regions that follow, among reference careers at the same career age |
| How do people move between programmes? | Roster-layer transitions with at-risk denominators |
| How is the field changing? | Generation-level trends (overseas share, mix of forms, funding share) |

### What it cannot answer (not covered)

Contract clauses, fees, salaries, admissions to degree programmes, chances of being selected,
"what will advance my career". The archive holds no applications, no rejections, no outcome that
defines success, and no contract or money data. `map_question` returns `not_covered` for these
and says what kind of source would answer instead. The server never falls back to the model's general
knowledge under its own name.

## 2. Rules (inherited from AGENTS.md, made executable)

1. **Descriptive, never prescriptive or causal.** The archive shows that selection is not followed
   by a measurable change in CV activity once a placebo baseline is used, and that the CV layer is
   the people who are still publishing CVs. So: "careers like yours did X" is allowed;
   "do X to advance", "X increases your chances", "the successful path" are not. Models are
   trained to distrust instructions found inside tool results (Wallace et al. 2024,
   arXiv:2404.13208), and LLM summaries drop qualifiers (Peters & Chin-Yee 2025, arXiv:2504.00025).
   So the first text of every result is the finished descriptive sentence, with layer and
   denominator, which is the cheapest thing for a client to quote; the same rules are carried in
   the server instructions, every tool description and a user-selected prompt (section 5), and
   `must_say` / `forbidden_paraphrase` remain as data.
2. **Aggregates only.** The server holds only the career bundle (section 4): tables of counts and
   distributions. It holds no ledger row, no `gy_id`, no name, no title, no URL of any person.
   Disclosure control follows the output-checking conventions of statistical agencies (section 4):
   `k = 10` distinct people per cell, quantiles only with at least `k` people on each side, no
   minimum or maximum, a bound instead of a share when 90% of a group share one value, counts
   rounded to 5, and a build test for recovery by differencing. No answer can describe one
   identifiable career. The user's career only selects a pre-built cell, so repeated queries can
   reveal at most the bundle itself; protection therefore lives in the build.
3. **No person is named, compared to or recommended.** There is no "artists like you" tool.
   Programmes and institutions are public bodies and may be named.
4. **The input career is not stored.** A request's entries live only for that request. Optional
   usage logging (section 7) records the tool name and outcome flags, never the entries.
5. **Every number names its layer and denominator.** Roster layer = census of admitted rosters.
   CV layer = people with an extracted public CV, a minority biased toward people active across
   several programmes; CV-layer estimates are shown with and without inverse-probability weights.
6. **Rules, not hand lists.** Every grouping used in the bundle (career age, generation,
   art-tech institution, activity kind, region) is a data rule with an id, as in `giye.normalize`.

## 3. Architecture

```
home machine (private)                                     VPS (public edge)
 ledger ─ preprocess ─ data/processed
                         │
             giye.career build  ──>  data/career/<ledger version>/   ──rsync──>  /srv/giye/career/
             (aggregates, k-suppressed,                                           │
              rule ids, manifest)                                       giye MCP server 
                                                                        (streamable HTTP, 127.0.0.1)
                                                                                  │
                                                       cloudflared ── https://mcp.giye.org/mcp
                                                                                  │
                                                       the practitioner's MCP client (Claude, Cursor, …)
```

- **`src/giye/career/`** (public, AGPL): the bundle builder. Field-agnostic like the rest of the
  package; it reads `data/processed/` and the ledger's activity sections and writes the bundle.
  It runs on the demo field too, so the public repository can build a synthetic bundle.
- **`src/giye/mcp/`** (public, AGPL): the MCP server, started with `giye mcp serve`. It reads one
  bundle directory and nothing else. `mcp` becomes an optional extra (`giye[mcp]`), like `llm`;
  no pandas at serve time (bundle tables are small JSON or CSV, loaded once).
- **Bundle** (private for the reference instance): `data/career/<version>/`. Only the bundle goes
  to the VPS. The ledger, processed data and evidence stay on the home machine, as for the site.
- **Transport**: remote streamable HTTP for the reference instance (the person-level evidence must
  not ship in a package, unlike Tekneh). `giye mcp serve --stdio --bundle <demo bundle>` for development and
  tests on the demo bundle.
- **Deployment**: a second systemd unit (`deploy/giye-mcp.service`) beside `giye-web.service`, same
  hardening (`ProtectSystem=strict`, read-only bundle), its own hostname on the existing tunnel.
- **Endpoint and access**: `https://mcp.giye.org/mcp`, HTTPS without redirect, `Origin` checked,
  speaking MCP revisions 2025-11-25 and 2026-07-28 (stateless) during the transition. Open and
  authless: the MCP specification makes authorization optional, and authless is the only mode that
  reaches claude.ai custom connectors and ChatGPT without running an OAuth server (static API-key
  headers are a limited beta in claude.ai). A global budget and a per-IP limit protect load; the
  per-IP limit allows for Anthropic's shared outbound range, which every claude.ai user comes from.
  An optional key header only raises limits for clients that can send one. Access control protects
  load, not secrecy: the bundle must be safe to read in full.

## 4. The career bundle

Built by `python -m giye.career build --out data/career/<version>`. One manifest records the
ledger version, rule ids, `k`, the IPW model version and the build time. Every table has a grain,
a population and a layer.

| Table | Grain | Layer | Content |
|---|---|---|---|
| `reference_position` | career age (0–30, 1-year) × generation (5-year) × measure | CV | Quantiles (those the quantile rule allows) and weighted quantiles of: activity-kind shares, overseas share, Seoul share among domestic, funding share, art-tech institution share, distinct institutions per year |
| `next_window` | career age band × generation × current-mix bucket × outcome kind | CV | Share of reference careers whose next 3 years contain each activity kind, institution kind, region; right-censored careers excluded and counted |
| `programme_profile` | programme | roster + CV | Access mode, years, edition sizes, returners share, team share, non-entrant comparisons |
| `programme_entry` | programme × measure | CV | Quantiles at entry: career age, activity mix before entry, overseas share before entry, institutions before entry |
| `programme_transitions` | programme × programme | roster | Movers, at-risk denominator, median gap, right-censoring note |
| `field_trend` | generation × measure | CV (+ roster where possible) | Generation-level trends with intervals |
| `vocab` | — | — | Activity kinds, institution kinds, regions, programmes, generations, rule ids, `not_covered` topics |

Definitions (each becomes a rule id in `giye.career.rules`):

- **Activity kind** (`activity_kind`, rule K1, on the processed activity row): the extraction's
  `cv_section` refines `activity_type`. `other` splits into education, teaching, talk/workshop,
  publication/press, grant/scholarship (funding), collection, commission, employment, and service.
  `award`×`grant` is funding. `award`×`project` stays `other`. A private section wins, except two
  bare-prize titles on the scholarship×award cell (kind `award`, channel hidden). Kind shares in
  `reference_position`, `next_window`, `programme_entry`, and `field_trend` count rows whose origin
  starts with `cv:` (`career_cv_kind_only`): the CV teaching denominator is those CV rows, and a
  roster row labelled `teaching` is only the site channel. Edition size, returners, team share, and
  `programme_transitions` count a person in a programme edition and do not read `activity_kind` or
  `activity_type` (`career_roster_membership`). `list_vocab` publishes the 18 kind names and the
  three channel names.
- **Career age**: years since the first public activity, education rows excluded. Generation:
  5-year bin of that first year (the same bins as the site's rim).
- **Overseas / domestic / region**: `venue_country` and `venue_region` from P3 (V1, G1–G6).
  Rows with an unresolved country are a separate bucket, never dropped silently.
- **Art-tech institution**: from the frame registry and declared operators, not from titles
  (titles are what adoption analyses read; using them here would be circular).
- **Funding**: `venue_kind=funder` or the funding kinds above.
- **Programme entry**: the first dated roster edition of that programme.

Disclosure control (rule ids `D1`–`D5`), after the output-checking guidelines of the ESSnet /
Eurostat (≥ 10 units per cell, percentiles treated as one respondent's value, no min/max, group
disclosure above 90%), UK secure-research practice (threshold 10) and published-outcome statistics
such as HESA's (rounding to 5):

- `D1` every cell carries `n_people`; a cell with `n_people < k` (`k = 10`) is written as suppressed,
  never as 0;
- `D2` a quantile q is published only when at least `k` people lie on each side of it
  (`n ≥ k / min(q, 1 − q)`: the median needs 20, the quartiles 40, P10 and P90 100); otherwise the
  cell reports fewer quantiles; minimum and maximum are never published;
- `D3` when 90% or more of a group share one value, the cell reports that bound instead of quantiles
  or an exact share;
- `D4` published counts are rounded to the nearest 5 and percentages to whole numbers, computed
  from unrounded counts;
- `D5` a build test fails when a suppressed cell can be recovered from published margins or
  overlapping tables, when any cell under `k` carries a value, or when any column could hold an
  identifier.

## 5. Tools

All tools take `lang` (`en` | `ko`, default from `GIYE_LANG`). The first `content` text of every
result is the finished descriptive sentence (`claim_template` filled in). Every tool declares an
`outputSchema`. The reading rules are in the server instructions, in each tool description, and in
an MCP prompt `read_my_career` that the user selects (prompts are a user-level channel; tool-result
text is not). Every success response also carries the reading contract as data:

```
layer            "roster" | "cv" | "both"
population       who is counted, with n
report_value     the number to cite; weighted and unweighted when CV layer
interval         bootstrap or Wilson interval where defined
suppressed       cells withheld under k
must_say         sentences the answer has to include (layer, bias, descriptive nature)
forbidden_paraphrase   phrasings the answer must not use (causal verbs, "chance", "recommend", "successful path")
generalization_allowed false when n is small or the layer does not support it
claim_template   a ready sentence with the denominator
bundle_version   ledger version the bundle was built from
```

| Tool | Input | What it returns |
|---|---|---|
| `about` | — | Boundary of the evidence, populations, bundle version, reading rules. Call first |
| `career_schema` | — | The `CareerEntry` schema and instructions for the client to structure a CV (year, kind, venue name, city/country, programme if any). The client's model does the structuring; the server never sees the CV document |
| `position` | `entries: list[CareerEntry]` | Career age, generation, and for each measure the user's value beside the reference quantiles at the same career age and generation. Bands, not scores |
| `programme_fit` | `entries` | Only if the author keeps it (section 10). For each programme with an unsuppressed entry profile: whether the user's measures fall inside the central 50% / 80% of that programme's entrants at entry. Ordered by programme code, not by fit, so it is not a ranking |
| `programme_profile` | `programme` | The programme's profile and entry profile, transitions to and from it |
| `next_steps` | `entries` or `career_age` + `generation` | Base rates of what followed in reference careers at the same career age and mix |
| `field_trend` | `measure` | Generation-level trend with intervals |
| `map_question` | `text` | Routes a question to tools, or returns `not_covered` / `partially_covered` with `ask_instead` |
| `list_vocab` | — | Every accepted value: kinds, regions, programmes, measures |

`CareerEntry`: `year` (int), `kind` (closed vocabulary from `list_vocab`), `venue` (str, optional),
`city` / `country` (optional), `programme` (optional code), `role` (optional). Venue names are
classified on the server with `giye.normalize` (country, region, institution kind, art-tech) and the
classification is echoed back so the user can correct it.

## 6. Evaluation

- **Unit and privacy tests** on the demo bundle: suppression, no identifiers, reading contract on
  every response, `not_covered` routing.
- **Scenario set**: synthetic careers (early, mid, late; domestic, overseas; single-form,
  mixed-form) with expected bands, built from the demo field. Run in CI.
- **Contract compliance**: transcripts from two or three MCP clients answering a fixed question
  set; count answers that drop `must_say` or use a forbidden phrasing. This is what tells whether
  the contract works without the server's control over the client.
- **Holdout check of `next_steps`**: on the reference instance only, build the bundle from careers
  up to year T and compare predicted base rates with what followed after T, to report calibration
  as a property of the base rates, not as a forecast for one person.

## 7. Usage logging (opt-in)

As in Tekneh: off by default. When the client sets a header or environment flag, the server
appends tool name, time, bundle version and outcome flags (covered / not covered, suppressed
count). Never the entries or the question text unless the user opts into that separately.

## 8. Data work before the first bundle

One preprocessing serves everything: the fixes went into the shared layer (`giye.normalize`), so
the site, the research analyses and the career bundle read the same values. The career builder
aggregates `data/processed/` and adds no preprocessing of its own. Each rule was agreed in an
adversarial critic/author review before it was implemented (rules and counts in docs/RULES.md).

| Item | Status (2026-10-09) |
|---|---|
| Venue entities split across names, languages, acronyms | V4n, V7f, V9u (`3622283`), V12 same-row alias attestation (`bea38ec`); a Wikidata-based rule for pairs with no in-data evidence is under review |
| Institutions with an empty country | G7–G11 (`76e613c`); Korean-language CV rows unresolved 44.2% → 35.9% |
| City names standing as institutions | one case found; gazetteer alternate names |
| `activity_type=other` | K1 `activity_kind` (`2681ddb`); `other` 30.9% → 4.7% of CV rows |
| P4 edition links | `[resolve.edition_only]` and three tighter shared patterns (`a3dba8a`); 110 misses need re-extraction, not patterns |
| Record-depth IPW model | to be recomputed with the first bundle |

## 9. Phases

| Phase | Output | Done when |
|---|---|---|
| 0 | Data fixes of section 8 (done 2026-10-08, deployed) | preprocess and site rebuild pass; before/after counts reported |
| 1 | `giye.career` builder, rules, demo bundle, privacy tests | `pytest -q` and `ruff` pass; demo bundle has no cell under `k` |
| 2 | `giye mcp` server (stdio) with `about`, `career_schema`, `position`, `programme_profile`, `map_question`, `list_vocab` | scenario set passes on the demo bundle |
| 3 | `programme_fit`, `next_steps`, `field_trend`; reference bundle | holdout check reported |
| 4 | Remote deployment, rate limit, opt-in logging | smoke test from a real MCP client |
| 5 | Contract-compliance study | report in `docs/` |

The bundle tables are the same aggregates the second paper (field analysis) reports, so phase 1
also gives that paper its tables.

## 10. Decisions

Settled by published evidence (sources in sections 2–5; web research of 2026-10-09):
hostname `https://mcp.giye.org/mcp`; open, authless, rate-limited access; `k = 10` with rules
D1–D5; descriptive sentence first, rules in instructions, descriptions and a user prompt.

Still the author's value judgement:

1. Whether a fully readable bundle through an open endpoint is compatible with "no bulk export".
   If not, only OAuth prevents reading it all.
2. Whether percentages need a higher threshold than counts (graduate-outcome statistics use about 20).
3. Whether `programme_fit` exists at all, or only `programme_profile`.
4. Whether people may opt out of the aggregates while staying in the archive, whether people with a
   listed contact are told directly, and the legal-basis wording of the notice (a Korean and English
   draft exists; Korean PIPA art. 20 and Supreme Court 2014다235080, GDPR art. 14(5)(b)).
5. Whether usage logging is offered at all on the remote server.
6. The compliance rate below which the public launch waits (section 6).
