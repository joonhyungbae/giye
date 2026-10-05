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

The fixtures are arranged so each merge rule fires once, and so attachment records its rule.
Lee Haru and Haru Lee share the workshop family, so the later edition attaches by A1.
Kim Haneul on the forum agrees in English with Haneul Kim on the workshop, so that row
attaches by A2. A shared website (E1, the two 정다운 rows, pinned apart by identity keys so
A6 does not attach them at collection), a CV line naming the other row's roster edition
(E2 for 표은솔, and X1+E2 for 김하늘 / Haneul Kim), a bracketed work title on two programmes
(E3), and a shared `팀:` credit (E4) each merge one pair. The person 배수아 and the team
row of the same name are left unmerged (T1); the team row does not repeat the person's
site, because A6 would attach them. A same-name pair that no rule attaches is queued.
Spelling variants that share an edition code (`<FRAME>-<YYYY>`) are not treated as merge
evidence. The field phrases and team words are `examples/demo/field.toml`.

CV extraction replays a hand-written cache (no network, no API key):

```bash
giye extract --config examples/demo/giye.toml --replay-only
```

Run `giye collect` first so the roster rows exist. `cvs/` holds five fictitious CVs (Korean,
English, and mixed for the identity cases, and two short ones for co-presence ties). The Korean CV writes 서울시립미술관 with a qualifier, an exhibition
title, and a gallery hall. The English CV lists Example Residency and Seoul Museum of Art,
and repeats one Korean event. The Korean page has an education section. `cache/` holds
synthetic raw responses that stand in for model output. See `cache/README.md`.

Co-presence ties (two people at one institution in one year) come from the CV rows:

```bash
giye explore ties --config examples/demo/giye.toml --layers
```

`EXPECTED.md` is the list of what one `giye demo` run shows, including the summary lines.
