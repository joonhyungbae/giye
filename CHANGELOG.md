# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-10-06

First version of the package: a data-free toolkit that builds a provenance-first person register of a creative field from public programme rosters and CVs, and the website that serves it.

- Collects programme rosters through a fetcher that checks robots.txt before every request and every redirect, and stores original bytes in a content-addressed snapshot.
- Extracts public CVs into a validated activity schema, with a replay cache so a run can be repeated without calling a model.
- Resolves the same person across scripts and spellings, refuses a team CV as a person, and retires merged ids instead of renumbering them.
- Normalises text, places and institutions with recorded rules, including a Korean–English language module for glossaries and the gazetteer.
- Explores an entry-generation rim order and scores a division of the field (coverage, adjusted Rand, lift, AUC).
- Publishes a site snapshot with permanent ids, redirects for retired ids, coverage, dataset versions, and APA, Chicago and BibTeX citations.
- Renders one plain HTML page per person, and serves the same snapshot from the TanStack Start site in `web/`.
- Draws the home visualization's heavy layers (record marks, chords, threads, piers) with WebGL2 and keeps text in Canvas 2D; the WebGL output is matched to Chrome's GPU Canvas 2D (same order, alpha quantisation and one-paint-per-call union), and browsers without WebGL2, or a lost context, fall back to Canvas 2D. `?renderer=2d|gl` forces either for comparison.
- Measures the home canvas with `web/scripts/perf/` (calls and frame times per phase, deterministic screenshots, pixel diffs between renderers).
- Exports the snapshot store as WARC (optional WACZ) and writes an RO-Crate description of a run.
- Reproduces the pipeline on a synthetic field with `giye demo`, without fetching the network.
- Runs on Linux and macOS with Python 3.10 to 3.13. Windows is not supported.
