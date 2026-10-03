# Demo field

A synthetic field used to reproduce the pipeline without real people.

```bash
giye demo
```

That runs the whole chain offline and writes the snapshot and HTML pages to a temporary directory.
The steps below write into `examples/demo/data/` instead, which is useful when inspecting one stage.

Roster collection runs offline (local HTML fixtures, robots.txt still checked) and writes the
ledger (people, frame membership, one activity per appearance) as well as a roster CSV:

```bash
giye collect --config examples/demo/giye.toml
```

`fixtures/` holds fictitious programme pages in Korean and English, including spelling variants.
`collectors.py` has two `RosterCollector` subclasses. `frames.yml` records one included frame,
one excluded frame, and one adjacent frame (rules F1–F5).

Same-person resolution runs on that ledger and on the HTML CVs in `fixtures/cv/` (no network):

```bash
giye resolve --config examples/demo/giye.toml
```

The fixtures are arranged so each identity rule fires once: a shared website (E1), a CV line
naming the other row's roster edition in the same year ±1 (E2), a bracketed work title (E3),
a shared `팀:` credit (E4). A same-name pair with none of that evidence is queued. A team row
that shares a name and a website with a person is left unmerged (T1). Spelling variants that
share a frame are not treated as evidence.

CV extraction replays a hand-written cache (no network, no API key):

```bash
giye extract --config examples/demo/giye.toml --replay-only
```

Run `giye collect` first so the roster rows exist. `cvs/` holds three fictitious CVs (Korean,
English, and mixed). The English page repeats one Korean event; the Korean page has an education
section. `cache/` holds synthetic raw responses that stand in for model output. See `cache/README.md`.
