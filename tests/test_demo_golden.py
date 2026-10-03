# SPDX-License-Identifier: MIT
"""The offline demo publishes a snapshot that matches the committed golden file.

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
    got = {path.name: normalise(json.loads(path.read_text(encoding="utf-8"))) for path in sorted(site.glob("*.json"))}
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert got == expected
    assert result.people == len(got["artists.json"])
    assert result.activities == len(got["activities.json"])
    assert result.roster_rows > 0
    assert result.merges
    assert "people:" in result.summary
    assert "roster rows:" in result.summary
    assert "activities:" in result.summary
    assert "merges:" in result.summary
    assert "queue items:" in result.summary
    assert "institution merges:" in result.summary
    page = next(path for path in (site / "html").glob("GY-*.html") if "김하늘" in path.read_text(encoding="utf-8"))
    text = page.read_text(encoding="utf-8")
    assert 'href="https://' in text
    assert ">source</a>" in text
    assert (site / "html" / "index.html").is_file()
