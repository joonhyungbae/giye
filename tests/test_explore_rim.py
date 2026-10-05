# SPDX-License-Identifier: AGPL-3.0-only
"""Entry-generation rim order. People and URLs are fictitious.

The synthetic cases lock R1–R7. The demo case runs the same function on the
ledger ``giye demo`` writes.
"""

from __future__ import annotations

import json
import socket
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from giye.config import load
from giye.demo import run_demo
from giye.explore.rim import build_rim_order, write_rim_order
from giye.field import Field, RimFamily
from giye.ledger.schemas import ACTIVITIES_FIELDS, ARTISTS_FIELDS, MEMBERSHIP_FIELDS, empty_row

NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo" / "giye.toml"
URL = "https://example.org/roster"


def _artist(ledger_id: str, gy: str, name: str, **extra: str) -> dict[str, str]:
    row = empty_row(
        ARTISTS_FIELDS,
        ledger_id=ledger_id,
        gy_id=gy,
        name_ko=name,
        status="STAGED",
        source_url=URL,
        source_type="PUBLIC_RECORD",
        collected_at="2026-01-15",
        cv_link_ok="no",
        frame_status="IN_FRAME",
        verification="UNVERIFIED",
    )
    row.update(extra)
    return row


def _member(ledger_id: str, code: str) -> dict[str, str]:
    return empty_row(
        MEMBERSHIP_FIELDS,
        ledger_id=ledger_id,
        frame_code=code,
        source_url=URL,
        collected_at="2026-01-15",
    )


def _activity(ledger_id: str, origin: str, role: str = "", year: str = "2010") -> dict[str, str]:
    return empty_row(
        ACTIVITIES_FIELDS,
        activity_id=f"act-{ledger_id}-{origin}",
        ledger_id=ledger_id,
        title=origin,
        year=year,
        activity_type="other",
        role=role,
        source_url=URL,
        source_type="PUBLIC_RECORD",
        collected_at="2026-01-15",
        publishable="yes",
        origin=origin,
    )


def _frame(code: str, name_ko: str, name_en: str, years: str = "") -> dict[str, str]:
    return {
        "code": code,
        "name_ko": name_ko,
        "name_en": name_en,
        "years_covered": years,
        "source_url": URL,
    }


def _rim(artists, membership, activities, frames, field: Field | None = None) -> dict:
    payload: dict = {
        "artists": artists,
        "frame_membership": membership,
        "activities": activities,
        "frames": frames,
    }
    if field is not None:
        payload["field"] = field
    return build_rim_order(payload, now=NOW)


NORTH = _frame("NORTH", "북쪽 레지던시 (2010)", "North Residency — cohort")
SOUTH = _frame("SOUTH", "남쪽 · 워크숍", "South Workshop")
SYNFLD = _frame("SYNFLD-CURRENT", "신폴드 (현재)", "Fold current")
SYNFLD_ARCHIVE = _frame("SYNFLD-ARCHIVE", "신폴드 아카이브", "Fold archive")
SYNFLD_FIELD = Field(
    rim_families=(RimFamily("SYNFLD", "SYNFLD", "신폴드", "Fold", "dated and archive codes are one programme"),),
    team_prefix="팀:",
)


def test_generation_bins_staff_and_the_open_grid_before_2000():
    artists = [
        _artist("L-early", "early", "림가"),
        _artist("L-mid", "mid", "림나"),
        _artist("L-late", "late", "림다"),
        _artist("L-none", "none", "림라"),
        _artist("L-staff", "staff", "림마"),
        _artist("L-both", "both", "림바"),
    ]
    membership = [
        _member("L-early", "NORTH-1990"),
        _member("L-mid", "NORTH-1997"),
        _member("L-late", "NORTH-2001"),
        _member("L-none", "YEARLESS"),
        _member("L-staff", "NORTH-2001"),
        _member("L-both", "NORTH-2001"),
        _member("L-both", "SOUTH-2012"),
    ]
    activities = [
        _activity("L-early", "NORTH-1990", year="1990"),
        _activity("L-mid", "NORTH-1997", year="1997"),
        _activity("L-late", "NORTH-2001", year="2001"),
        # A year that lives only on the activity is not an entry year (R1).
        _activity("L-none", "YEARLESS", year="1998"),
        _activity("L-staff", "NORTH-2001", role="mentor", year="2001"),
        _activity("L-both", "NORTH-2001", role="심사", year="2001"),
        _activity("L-both", "SOUTH-2012", year="2012"),
    ]
    doc = _rim(artists, membership, activities, [NORTH, SOUTH])
    assert [row["code"] for row in doc["families"]] == ["GEN-1990", "GEN-1995", "GEN-2000", "GEN-2010", "GEN-UNDATED"]
    # 1995–1999 and 2005–2009 have nobody, so those arcs are absent.
    assert "GEN-2005" not in {row["code"] for row in doc["families"]}
    by_id = {row["id"]: row for row in doc["artists"]}
    assert by_id["early"]["family"] == "GEN-1990" and by_id["early"]["year"] == 1990
    assert by_id["early"]["edition"] == "1990"
    assert by_id["mid"]["family"] == "GEN-1995" and by_id["mid"]["year"] == 1997
    assert by_id["late"]["family"] == "GEN-2000"
    assert by_id["none"]["family"] == "GEN-UNDATED" and by_id["none"]["year"] is None
    assert by_id["none"]["edition"] == "" and by_id["none"]["also"] == ["YEARLESS"]
    assert by_id["staff"]["family"] == "GEN-UNDATED" and by_id["staff"]["also"] == []
    # Staff on NORTH does not count; the SOUTH participation does.
    assert by_id["both"]["family"] == "GEN-2010" and by_id["both"]["also"] == ["SOUTH"]
    undated = next(row for row in doc["families"] if row["code"] == "GEN-UNDATED")
    assert undated["linked"] is False and undated["first_year"] is None
    assert undated["label_ko"] == "진입 연도 미상" and undated["label_en"] == "Entry year unknown"
    dated = next(row for row in doc["families"] if row["code"] == "GEN-2000")
    assert dated["linked"] is True and dated["participants"] == dated["n"]
    assert dated["label_en"] == "Entered 2000–2004"
    assert doc["boundaries"][-1]["seam"] is True
    assert doc["boundaries"][-1]["a"] == "GEN-UNDATED" and doc["boundaries"][-1]["b"] == "GEN-1990"
    assert all(row["shared"] == row["cos"] == row["agree"] == 0 for row in doc["boundaries"])
    assert sum(row["seam"] for row in doc["boundaries"]) == 1
    # Parenthetical and the subtitle after 「 — 」 / 「 · 」 come off the registry name.
    north = next(row for row in doc["programmes"] if row["code"] == "NORTH")
    assert north["label_ko"] == "북쪽 레지던시" and north["label_en"] == "North Residency"
    south = next(row for row in doc["programmes"] if row["code"] == "SOUTH")
    assert south["label_ko"] == "남쪽" and south["label_en"] == "South Workshop"


def test_team_block_family_fold_and_deterministic_bytes(tmp_path: Path):
    artists = [
        _artist("L-ga", "ga", "림가"),
        _artist("L-na", "na", "림나"),
        _artist("L-team", "team", "림다"),
        _artist("L-ape", "ape", "림사"),
    ]
    membership = [
        _member("L-ga", "NORTH-2010"),
        _member("L-na", "NORTH-2010"),
        _member("L-team", "NORTH-2010"),
        _member("L-ape", "SYNFLD-2019"),
        _member("L-ape", "SYNFLD-ARCHIVE-2020"),
    ]
    activities = [
        _activity("L-ga", "NORTH-2010", role="팀: 림다", year="2010"),
        _activity("L-na", "NORTH-2010", year="2010"),
        _activity("L-team", "NORTH-2010", role="팀: 림다", year="2010"),
        _activity("L-ape", "SYNFLD-2019", year="2019"),
        _activity("L-ape", "SYNFLD-ARCHIVE-2020", year="2020"),
    ]
    frames = [NORTH, SYNFLD_ARCHIVE, SYNFLD]
    first = _rim(artists, membership, activities, frames, SYNFLD_FIELD)
    second = _rim(artists, membership, activities, frames, SYNFLD_FIELD)
    assert first == second
    # Inside 2010: 림나 (no team), then the team row 림다, then member 림가.
    order_2010 = [row["id"] for row in first["artists"] if row["year"] == 2010]
    assert order_2010 == ["na", "team", "ga"]
    ape = next(row for row in first["artists"] if row["id"] == "ape")
    assert ape["family"] == "GEN-2015" and ape["year"] == 2019 and ape["also"] == ["SYNFLD"]
    programme = next(row for row in first["programmes"] if row["code"] == "SYNFLD")
    assert programme["label_ko"] == "신폴드" and programme["label_en"] == "Fold"
    assert [row["code"] for row in first["programmes"]] == sorted(row["code"] for row in first["programmes"])
    assert first["version"] == "2026-01-15" and first["computed_at"] == "2026-01-15T00:00:00Z"
    assert first["method"]["home"] == "entry generation"
    assert "staff roles excluded" in first["method"]["grouping"]
    path = write_rim_order(first, tmp_path)
    assert path.name == "rim_order.json"
    text = path.read_text(encoding="utf-8")
    assert not text.endswith("\n") and " " not in text.split('"method"')[0]
    assert json.loads(text) == first


def test_out_of_scope_and_a_row_without_a_source_are_not_on_the_ring():
    artists = [
        _artist("L-in", "in", "림가"),
        _artist("L-out", "out", "림나"),
        _artist("L-nosrc", "nosrc", "림다", source_url=""),
        _artist("L-noid", "", "림라"),
    ]
    membership = [
        _member("L-in", "NORTH-2010"),
        _member("L-out", "NORTH-2010"),
        _member("L-noid", "NORTH-2010"),
    ]
    doc = build_rim_order(
        {
            "artists": artists,
            "frame_membership": membership,
            "activities": [
                _activity("L-in", "NORTH-2010"),
                _activity("L-out", "NORTH-2010"),
                _activity("L-noid", "NORTH-2010"),
            ],
            "frames": [NORTH],
            "scope": [{"ledger_id": "L-out", "scope": "out"}],
        },
        now=NOW,
    )
    # Out of scope, no http source, and a publishable row with no gy_id stay off the ring.
    assert [row["id"] for row in doc["artists"]] == ["in"]


def test_demo_ledger_rim_matches_the_published_people(tmp_path: Path, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("rim demo tried to use the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    result = run_demo(DEMO, tmp_path / "out", now=NOW)
    doc = build_rim_order(replace(load(DEMO), data=result.output), now=NOW)
    again = build_rim_order(replace(load(DEMO), data=result.output), now=NOW)
    assert doc == again
    site_artists = json.loads((result.site / "artists.json").read_text(encoding="utf-8"))
    assert {row["id"] for row in doc["artists"]} == {row["id"] for row in site_artists}
    assert sum(row["n"] for row in doc["families"]) == len(doc["artists"])
    # Demo memberships are <FRAME>-<YYYY>, so R1 dates everyone. 2019 is GEN-2015;
    # 2020–2023 is GEN-2020. Nobody is left without a year on the code.
    # A1 removes one person from GEN-2020 (same-programme spellings). The forum's
    # Kim Haneul stays its own record (Latin name only), entering in 2023.
    assert [(row["code"], row["n"]) for row in doc["families"]] == [("GEN-2015", 12), ("GEN-2020", 9)]
    assert all(row["linked"] is True for row in doc["families"])
    # A2 attaches the 2023 forum row, so no published person has 2023 as an entry year.
    assert {row["year"] for row in doc["artists"]} == {2019, 2020, 2021, 2022, 2023}
    assert doc["boundaries"][-1]["seam"] is True
    assert doc["boundaries"][-1]["a"] == "GEN-2020" and doc["boundaries"][-1]["b"] == "GEN-2015"
    assert {row["code"] for row in doc["programmes"]} >= {"EXAMPLE-FORUM", "EXAMPLE-RESIDENCY", "EXAMPLE-WORKSHOP"}
    residency = next(row for row in doc["programmes"] if row["code"] == "EXAMPLE-RESIDENCY")
    assert residency["label_ko"] == "예시 레지던시" and residency["label_en"] == "Example Residency"
    activities = json.loads((result.site / "activities.json").read_text(encoding="utf-8"))
    team_of: dict[str, str] = {}
    for row in activities:
        role = row.get("role") or ""
        if role.startswith("팀:"):
            team_of.setdefault(row["artist_id"], role.split(":", 1)[1].strip())
    assert team_of, "the demo fixtures credit at least one team"
    order = [row["id"] for row in doc["artists"]]
    for team in set(team_of.values()):
        indexes = [order.index(person) for person, name in team_of.items() if name == team and person in order]
        # Activity order follows year, title, and id, so this scan is not rim order.
        assert sorted(indexes) == list(range(min(indexes), max(indexes) + 1))
    written = json.loads(write_rim_order(doc, tmp_path / "site").read_text(encoding="utf-8"))
    assert written["artists"] == doc["artists"]
