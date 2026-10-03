# Demo field

A synthetic field used to reproduce the pipeline without real people.

Roster collection runs offline (local HTML fixtures, robots.txt still checked):

```bash
giye collect --config examples/demo/giye.toml
```

`fixtures/` holds fictitious programme pages in Korean and English, including spelling variants.
`collectors.py` has two `RosterCollector` subclasses. `frames.yml` records one included frame,
one excluded frame, and one adjacent frame (rules F1–F5).

Generated CVs and cached LLM responses for later stages are still planned (docs/ROADMAP.md, item 13).
