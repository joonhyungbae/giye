# SPDX-License-Identifier: AGPL-3.0-only
"""Exports leave out people hidden by request unless --include-hidden, and say which."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from giye.collect.snapshot import SnapshotStore
from giye.config import load
from giye.demo import run_demo
from giye.export.privacy import EXCLUDED_NOTE, INCLUDED_NOTE
from giye.export.rocrate import export_ro_crate
from giye.export.warc import export_warc
from giye.ledger.ledger import Ledger
from giye.resolve.decide import hide_person
from tests.test_export import CLOCK, DEMO

NAME = "김하늘"
CV = "https://cv.example.org/haneul-ko"


def _hidden_demo(tmp_path: Path):
    result = run_demo(DEMO, tmp_path / "out", now=CLOCK)
    config = replace(load(DEMO), data=result.output.resolve())
    gy_id = next(row["gy_id"] for row in Ledger.open(config).read("artists") if row["name_ko"] == NAME)
    hide_person(Ledger.open(config), gy_id, reason="asked by email")
    # An evidence capture of the hidden person's CV page.
    SnapshotStore(config.raw).keep("_evidence", CV, f"<p>{NAME} CV</p>".encode(), status=200, via="direct")
    return config


def _crate_texts(crate: Path) -> dict[str, str]:
    texts = {}
    for path in crate.rglob("*"):
        if path.is_file() and "snapshots" not in path.parts:
            texts[path.relative_to(crate).as_posix()] = path.read_bytes().decode("utf-8", errors="replace")
    return texts


def test_crate_leaves_out_hidden_people_by_default(tmp_path: Path):
    config = _hidden_demo(tmp_path)
    meta = export_ro_crate(config, tmp_path / "crate", config_path=DEMO)
    texts = _crate_texts(tmp_path / "crate")
    leaking = [name for name, text in texts.items() if NAME in text or "asked by email" in text]
    assert leaking == []
    assert not any(name.startswith("data/raw/cv/") and "HANEUL" in name for name in texts)
    graph = {item["@id"]: item for item in json.loads(meta.read_text())["@graph"]}
    assert EXCLUDED_NOTE in graph["./"]["description"]
    assert CV not in graph


def test_crate_includes_hidden_people_when_asked(tmp_path: Path):
    config = _hidden_demo(tmp_path)
    meta = export_ro_crate(config, tmp_path / "crate", config_path=DEMO, include_hidden=True)
    texts = _crate_texts(tmp_path / "crate")
    assert any(NAME in text for name, text in texts.items() if name.endswith("artists.csv"))
    graph = {item["@id"]: item for item in json.loads(meta.read_text())["@graph"]}
    assert INCLUDED_NOTE in graph["./"]["description"]


def test_warc_leaves_out_a_hidden_person_cv_capture(tmp_path: Path):
    config = _hidden_demo(tmp_path)
    result = export_warc(config, tmp_path / "s.warc.gz", wacz=True)
    import gzip

    with gzip.open(result.warc) as handle:
        raw = handle.read()
    assert CV.encode() not in raw
    assert EXCLUDED_NOTE.encode() in raw
    assert result.left_out == 1
