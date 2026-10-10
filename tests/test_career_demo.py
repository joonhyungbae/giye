# SPDX-License-Identifier: AGPL-3.0-only
"""The career builder runs on the offline demo and publishes no identifier."""

from __future__ import annotations

import csv
import json
import re
import socket
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from giye.career.build import build
from giye.career.population import load_population
from giye.config import load
from giye.demo import run_demo

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo" / "giye.toml"
CLOCK = datetime(2026, 1, 15, tzinfo=timezone.utc)
_GY = re.compile(r"^GY-\d{6}$")
_CAND = re.compile(r"^CAND-")
_CV = re.compile(r"^cv:")
_URL = re.compile(r"https?://", re.IGNORECASE)
_PS = re.compile(r"^ps[0-9a-f]{32}$")
_TABLES = (
    "reference_position.csv",
    "next_window.csv",
    "programme_profile.csv",
    "programme_entry.csv",
    "programme_transitions.csv",
    "field_trend.csv",
)


def _bad(value: str) -> bool:
    if _GY.fullmatch(value) or _PS.fullmatch(value):
        return True
    if _CAND.match(value) or _CV.match(value):
        return True
    return bool(_URL.search(value))


def test_demo_bundle(tmp_path: Path, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("career demo build tried to use the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    result = run_demo(DEMO, tmp_path / "out", now=CLOCK)
    config = replace(load(DEMO), data=result.output.resolve())
    out = tmp_path / "bundle"
    manifest = build(config, out)
    population = load_population(config, None, k=10)
    assert manifest["counts"]["published"] == population.published_n
    assert manifest["counts"]["cv_layer"] == population.cv_layer_n
    assert manifest["counts"]["cv_no_first_year"] == population.cv_no_first_year
    # The demo's published set is the site's. Cells under k are suppressed; a
    # numeric people-count is a multiple of 5 and at least k.
    for name in _TABLES:
        with (out / name).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                for column, value in row.items():
                    assert not _bad(value), (name, column, value)
                    if column == "n_people" and value not in {"suppressed", ">=90", ""}:
                        number = int(value)
                        assert number >= manifest["k"]
                        assert number % 5 == 0
    vocab = (out / "vocab.json").read_text(encoding="utf-8")
    assert not _URL.search(vocab)
    document = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert document["roster_facts_check"] == "skipped"
    assert document["bundle_format"] == 1
