# SPDX-License-Identifier: AGPL-3.0-only
"""The offline demo matches the committed golden file.

The file holds the site JSON (including ``rim_order.json``), the processed
venue table, artist attributes, and venue audit, and the co-presence layer
report (``giye.explore.ties.layer_report``).

Timestamps and calendar dates are normalised before the comparison. The clock
is frozen at 2026-01-15 because the synthetic CVs treat 2026 as the current
year. Ledger ids are pinned inside ``run_demo``, so the file does not depend
on uuid4.
"""

from __future__ import annotations

import json
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

from giye.demo import run_demo

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo" / "giye.toml"
GOLDEN = ROOT / "tests" / "golden" / "demo_snapshot.json"
# The demo CVs mark 2026 as this year and 2027 as still upcoming.
CLOCK = datetime(2026, 1, 15, tzinfo=timezone.utc)
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalise(value):
    """Replace build timestamps and dates. Years stored as integers stay."""
    if isinstance(value, str):
        text = _TIMESTAMP.sub("TIMESTAMP", value)
        return _DATE.sub("DATE", text)
    if isinstance(value, list):
        return [normalise(item) for item in value]
    if isinstance(value, dict):
        return {key: normalise(item) for key, item in value.items()}
    return value


def test_demo_snapshot_matches_golden(tmp_path: Path, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("giye demo tried to use the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    result = run_demo(DEMO, tmp_path / "out", now=CLOCK)
    site = result.site
    processed = result.output / "processed"
    got = {
        "site": {
            path.name: normalise(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(site.glob("*.json"))
        },
        "processed": {
            name: normalise((processed / name).read_text(encoding="utf-8"))
            for name in ("venues.csv", "artist_attributes.csv", "venue_audit.md")
        },
        "ties": result.copresence,
    }
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert got == expected
    assert "rim_order.json" in got["site"]
    assert result.people == len(got["site"]["artists.json"])
    assert result.activities == len(got["site"]["activities.json"])
    assert result.roster_rows == 23
    assert result.people == 20
    assert result.activities == 38
    assert "merges: E1 1, E2 1, E3 1, E4 1, X1+E2 1" in result.summary
    assert "blocked: T1 1" in result.summary
    assert "queue items: 3" in result.summary
    assert "institution merges: V7 2, V8 1, V9 1" in result.summary
    assert "co-presence ties, CV listing: base 1, V7 1, V7+V8 1, V7+V8+V9 2" in result.summary
    assert "co-presence ties, roster independent: base 0, V7 0, V7+V8 1, V7+V8+V9 2" in result.summary
    page = next(path for path in (site / "html").glob("GY-*.html") if "김하늘" in path.read_text(encoding="utf-8"))
    text = page.read_text(encoding="utf-8")
    assert 'href="https://' in text
    assert ">source</a>" in text
    assert "예시 레지던시" in text and "예시 워크숍" in text
    assert "서울시립미술관 외" in text and "서울시립미술관 《빛》" in text
    assert "서울시립미술관 전시실" in text and "Seoul Museum of Art" in text
    assert "EXAMPLE-RESIDENCY-2019" in text
    assert text.count("예시 미디어전") == 1
    assert "Open same-name review" not in text
    assert (site / "html" / "GY-000020.html").read_text(encoding="utf-8").count("GY-000001") >= 1
    assert "Open same-name review: GY-000018" in (site / "html" / "GY-000007.html").read_text(encoding="utf-8")
    assert (site / "html" / "index.html").is_file()
