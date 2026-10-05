# SPDX-License-Identifier: AGPL-3.0-only
"""Accuracy-audit sheets on the offline demo ledger.

The ledger is built the same way as ``tests/test_demo_golden.py``: ``run_demo``
into ``tmp_path``, clock frozen, sockets refused. Sampling, the Wilson
arithmetic, and the serve endpoint are checked against that ledger.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from giye.audit.page import render_page
from giye.audit.sample import parse_markers, sample_sheet, stratum_for
from giye.audit.score import score_sheet, wilson
from giye.audit.serve import serve_sheet
from giye.audit.sheet import read_sheet, write_sheet
from giye.config import load
from giye.demo import run_demo

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo" / "giye.toml"
CLOCK = datetime(2026, 1, 15, tzinfo=timezone.utc)

# Wilson score interval at z = 1.959963984540054. Computed independently of score.py.
WILSON_8_OF_10 = (0.8, 0.49016247153664183, 0.9433178485456247)
WILSON_8_OF_11 = (0.7272727272727273, 0.43435469882387084, 0.902539407099751)
WILSON_6_OF_8 = (0.75, 0.40927543031016883, 0.9285207872478909)
WILSON_6_OF_9 = (0.6666666666666666, 0.3542021355803963, 0.879416181613089)
WILSON_1_OF_1 = (1.0, 0.20654931437723745, 1.0)


def _refuse(*_args, **_kwargs):
    raise AssertionError("giye demo tried to use the network")


@pytest.fixture(scope="module")
def demo_config(tmp_path_factory) -> Path:
    """A giye.toml whose data directory is one frozen demo run."""
    root = tmp_path_factory.mktemp("audit-demo")
    data = root / "data"
    create_connection = socket.create_connection
    connect = socket.socket.connect
    socket.create_connection = _refuse
    socket.socket.connect = _refuse
    try:
        run_demo(DEMO, data, now=CLOCK)
    finally:
        socket.create_connection = create_connection
        socket.socket.connect = connect
    path = root / "giye.toml"
    path.write_text(
        f'[archive]\nname = "audit demo"\n\n[paths]\ndata = "{data.resolve().as_posix()}"\n',
        encoding="utf-8",
    )
    return path


def _rows(path: Path) -> list[dict[str, str]]:
    _fields, rows = read_sheet(path)
    return rows


def test_uncoded_rule_is_its_own_stratum():
    assert stratum_for("E2") == "E2"
    assert stratum_for("X1+E4") == "X1+E4"
    assert stratum_for("hand") == "uncoded"
    note = "identity=demo:a; merged LED-1; merged LED-2; merge_evidence=seen together; rule=hand"
    assert parse_markers(note) == [("LED-1", "seen together", "hand"), ("LED-2", "seen together", "hand")]


def test_sample_is_deterministic_for_a_seed(demo_config: Path, tmp_path: Path):
    config = load(demo_config)
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    other = tmp_path / "c.csv"
    sample_sheet(config, "cv", 4, 1, first)
    sample_sheet(config, "cv", 4, 1, second)
    sample_sheet(config, "cv", 4, 2, other)
    assert first.read_bytes() == second.read_bytes()
    left = [row["item_id"] for row in _rows(first)]
    right = [row["item_id"] for row in _rows(other)]
    assert left != right
    assert all(row["label"] == "" and row["note"] == "" for row in _rows(first))
    assert all(row["seed"] == "1" for row in _rows(first))
    assert {row["stratum"] for row in _rows(first)} == {"group_exhibition"}
    assert any(row["excerpt"] and row["title"] and row["title"].strip("〈〉") in row["excerpt"] for row in _rows(first))


def test_people_sheet_has_both_records(demo_config: Path, tmp_path: Path):
    config = load(demo_config)
    path = tmp_path / "people.csv"
    sample_sheet(config, "people", 100, 1, path)
    rows = _rows(path)
    assert {row["stratum"] for row in rows} == {"E1", "E2", "E3", "E4", "X1+E2"}
    paired = next(row for row in rows if row["stratum"] == "X1+E2")
    assert paired["kept_name_ko"] == "김하늘"
    assert "Haneul Kim" in (paired["dropped_name_ko"], paired["dropped_name_en"])
    assert "EXAMPLE-RESIDENCY-2019" in paired["kept_rosters"]
    assert "EXAMPLE-WORKSHOP-2021" in paired["dropped_rosters"]
    assert paired["evidence"].startswith("X1+E2")
    # n below the population: equal remainders fill E1, E2, E3 and do not draw.
    small_a = tmp_path / "p1.csv"
    small_b = tmp_path / "p2.csv"
    sample_sheet(config, "people", 3, 1, small_a)
    sample_sheet(config, "people", 3, 9, small_b)
    assert [row["item_id"] for row in _rows(small_a)] == [row["item_id"] for row in _rows(small_b)]
    assert {row["stratum"] for row in _rows(small_a)} == {"E1", "E2", "E3"}


def test_venue_sheet_uses_the_audit(demo_config: Path, tmp_path: Path):
    config = load(demo_config)
    path = tmp_path / "venues.csv"
    sample_sheet(config, "venues", 10, 1, path)
    rows = _rows(path)
    assert {row["stratum"] for row in rows} == {"V8", "V9"}
    v9 = next(row for row in rows if row["rule"] == "V9")
    assert v9["kept_spelling"] == "서울시립미술관"
    assert v9["joined_spelling"] == "Seoul Museum of Art"
    assert "열린 수장고" in v9["kept_examples"]
    assert "Open Storage" in v9["joined_examples"]
    assert v9["kept_examples"].count(" || ") < 3
    v8 = next(row for row in rows if row["rule"] == "V8")
    assert "전시실" in v8["joined_spelling"]
    assert v8["joined_examples"]


def test_score_wilson_on_a_labeled_demo_sheet(demo_config: Path, tmp_path: Path):
    assert wilson(8, 10) == pytest.approx(WILSON_8_OF_10)
    assert wilson(8, 11) == pytest.approx(WILSON_8_OF_11)
    assert wilson(6, 8) == pytest.approx(WILSON_6_OF_8)
    assert wilson(6, 9) == pytest.approx(WILSON_6_OF_9)
    assert wilson(1, 1) == pytest.approx(WILSON_1_OF_1)
    assert wilson(0, 5)[1] == 0.0

    config = load(demo_config)
    path = tmp_path / "cv.csv"
    sample_sheet(config, "cv", 100, 0, path)
    fields, rows = read_sheet(path)
    assert rows
    # Sheet order, not a stratum size. Other stages may change how many rows
    # each activity type has; the score is checked against the labels written here.
    cycle = ("correct", "incorrect", "cannot tell", "")
    for index, row in enumerate(rows):
        row["label"] = cycle[index % len(cycle)]
    write_sheet(path, fields, rows)

    report = score_sheet(path, "cv")
    _assert_wilson(report["overall"], rows)
    for name, block in report["strata"].items():
        _assert_wilson(block, [row for row in rows if row["stratum"] == name])


def _assert_wilson(block: dict, rows: list[dict[str, str]]) -> None:
    """``block`` is the Wilson summary of the labels on ``rows``."""
    correct = sum(row["label"] == "correct" for row in rows)
    incorrect = sum(row["label"] == "incorrect" for row in rows)
    cannot = sum(row["label"] == "cannot tell" for row in rows)
    unlabeled = sum(row["label"] == "" for row in rows)
    other = sum(row["label"] not in {"correct", "incorrect", "cannot tell", ""} for row in rows)
    assert block["correct"] == correct
    assert block["incorrect"] == incorrect
    assert block["cannot_tell"] == cannot
    assert block["unlabeled"] == unlabeled
    assert block["other"] == other
    assert block["n"] == correct + incorrect
    assert block["conservative_n"] == correct + incorrect + cannot
    primary = wilson(correct, correct + incorrect)
    conservative = wilson(correct, correct + incorrect + cannot)
    if primary is None:
        assert block["precision"] is None
        assert block["wilson_low"] is None
        assert block["wilson_high"] is None
    else:
        assert (block["precision"], block["wilson_low"], block["wilson_high"]) == pytest.approx(primary)
    if conservative is None:
        assert block["conservative_precision"] is None
        assert block["conservative_low"] is None
        assert block["conservative_high"] is None
    else:
        assert (
            block["conservative_precision"],
            block["conservative_low"],
            block["conservative_high"],
        ) == pytest.approx(conservative)


def test_serve_post_writes_the_sheet(demo_config: Path, tmp_path: Path):
    config = load(demo_config)
    path = tmp_path / "sheet.csv"
    sample_sheet(config, "people", 3, 1, path)
    target = _rows(path)[0]
    page = render_page(_rows(path))
    assert "1 correct" in page and "2 incorrect" in page and "3 cannot tell" in page
    assert 'event.key === "1"' in page
    server = serve_sheet(path, 0)
    try:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(base + "/") as response:
            html = response.read().decode("utf-8")
        assert target["item_id"] in html
        payload = json.dumps({"item_id": target["item_id"], "label": "cannot tell", "note": "thin evidence"}).encode()
        request = urllib.request.Request(
            base + "/label", data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["ok"] is True
        saved = next(row for row in _rows(path) if row["item_id"] == target["item_id"])
        assert saved["label"] == "cannot tell"
        assert saved["note"] == "thin evidence"
        assert saved["evidence"] == target["evidence"]
        bad = urllib.request.Request(
            base + "/label",
            data=json.dumps({"item_id": target["item_id"], "label": "maybe"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(bad)
        assert raised.value.code == 400
        saved = next(row for row in _rows(path) if row["item_id"] == target["item_id"])
        assert saved["label"] == "cannot tell"
    finally:
        server.shutdown()
        server.server_close()


def test_module_command_writes_a_sheet(demo_config: Path, tmp_path: Path):
    out = tmp_path / "venues.csv"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "giye.audit",
            "sample",
            "venues",
            "--config",
            str(demo_config),
            "--n",
            "5",
            "--seed",
            "1",
            "--out",
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    assert "V9" in out.read_text(encoding="utf-8")
