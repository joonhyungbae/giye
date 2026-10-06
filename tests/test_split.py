# SPDX-License-Identifier: AGPL-3.0-only
"""``giye split``: a wrongly attached roster membership gets its own record.

On a copy of the demo ledger, the 2022 workshop line ``Haru Lee`` was joined
to the 2021 record ``Lee Haru`` by A1. The split moves that membership and its
roster activity to a new record with a new ``gy_id``, and neither resolve nor
a second collection joins them again. People are fictitious; URLs are
example.org.
"""

from __future__ import annotations

import csv
import shutil
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from giye.cli import main
from giye.config import GiyeError, load
from giye.demo import run_demo
from giye.ledger.ledger import Ledger
from giye.resolve.decide import hide_person, merge_people
from giye.resolve.service import resolve_ledger
from giye.resolve.split import SplitRequest, split_membership, split_memberships

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"
CLOCK = datetime(2026, 1, 15, tzinfo=timezone.utc)
OLD = "LED-0000001140"
FRAME = "EXAMPLE-WORKSHOP-2022"
MID = f"{OLD}@{FRAME}"
EVIDENCE = "H the 2022 fellow is a different person per the programme office; 2026-01-20"


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def refuse(*_args, **_kwargs):
        raise AssertionError("the test tried to use the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    dest = tmp_path / "demo"
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("data", "__pycache__", "*.pyc"))
    run_demo(dest / "giye.toml", dest / "data", now=CLOCK)
    return dest


def _ledger(dest: Path) -> Ledger:
    return Ledger.open(load(dest / "giye.toml"))


def _ledger_bytes(dest: Path) -> dict[str, bytes]:
    folder = dest / "data" / "ledger"
    return {path.name: path.read_bytes() for path in sorted(folder.glob("*.csv"))}


def _person(ledger: Ledger, lid: str) -> dict[str, str]:
    return next(row for row in ledger.read("artists") if row["ledger_id"] == lid)


def test_split_moves_the_membership_and_its_activities(demo: Path):
    ledger = _ledger(demo)
    before = ledger.read("activities")
    cv_before = sorted(row["activity_id"] for row in before if row["origin"].startswith("cv:"))
    moving = sorted(row["activity_id"] for row in before if row["ledger_id"] == OLD and row["origin"] == FRAME)
    assert moving
    issued = [row["gy_id"] for row in ledger.read("artists")] + [row["gy_id"] for row in ledger.read("gy_retired")]
    highest = max(int(gy.split("-")[1]) for gy in issued if gy)

    result = split_membership(ledger, MID, evidence=EVIDENCE)

    assert result.old_gy_id == "GY-000013"
    assert result.new_gy_id == f"GY-{highest + 1:06d}"
    assert (result.name_ko, result.name_en, result.name_from) == ("Haru Lee", "Haru Lee", "roster")
    new = _person(ledger, result.new_ledger_id)
    old = _person(ledger, OLD)
    assert old["gy_id"] == "GY-000013"
    marker = f"split {MID} to {result.new_ledger_id}; split_evidence="
    assert marker in old["reviewer_note"] and marker in new["reviewer_note"]
    assert new["reviewer_note"].endswith("; rule=H")
    # The date stays in the evidence segment: a note is split on ";".
    assert "per the programme office, 2026-01-20; rule=H" in new["reviewer_note"]
    assert new["status"] == "STAGED" and new["source_url"] == "https://example.org/workshop/fellows"

    memberships = {(row["ledger_id"], row["frame_code"]): row for row in ledger.read("frame_membership")}
    assert (OLD, FRAME) not in memberships
    assert memberships[(result.new_ledger_id, FRAME)]["attach_rule"] == "split:A1"
    assert (OLD, "EXAMPLE-WORKSHOP-2021") in memberships

    after = ledger.read("activities")
    assert sorted(result.moved_activities) == moving
    assert sorted(row["activity_id"] for row in after if row["ledger_id"] == result.new_ledger_id) == moving
    assert not [row for row in after if row["ledger_id"] == OLD and row["origin"] == FRAME]
    assert len(after) == len(before)
    # CV rows stay with their source's owner.
    assert sorted(row["activity_id"] for row in after if row["origin"].startswith("cv:")) == cv_before
    owners = {row["source_id"]: row["ledger_id"] for row in ledger.read("cv_sources")}
    for row in after:
        if row["origin"].startswith("cv:"):
            assert row["ledger_id"] == owners[row["origin"][3:]]

    item = next(row for row in ledger.read("review_queue") if result.new_ledger_id in row["detail"])
    assert item["ledger_id"] == OLD and item["status"] == "done"
    assert "decided=different" in item["detail"] and "evidence_at_decision=" in item["detail"]
    backups = sorted(path.name for path in (demo / "data" / "work" / "backups").glob("*-before-split*"))
    assert {name.split("-")[0] for name in backups} == {"artists", "activities", "frame_membership", "review_queue"}


def test_resolve_and_a_second_collection_do_not_join_them_again(demo: Path, capsys: pytest.CaptureFixture[str]):
    ledger = _ledger(demo)
    result = split_membership(ledger, MID, evidence=EVIDENCE)
    outcome = resolve_ledger(ledger)
    pair = {OLD, result.new_ledger_id}
    assert not [item for item in outcome.merges if {item.kept, item.dropped} == pair]
    assert not [item for item in outcome.queued if result.new_ledger_id in item["detail"] and OLD in (item["ledger_id"] + item["detail"])]

    config = str(demo / "giye.toml")
    rows = len(ledger.read("activities"))
    assert main(["collect", "--config", config, "--from-snapshots"]) == 0
    assert main(["resolve", "--config", config]) == 0
    capsys.readouterr()
    ledger = _ledger(demo)
    lids = {row["ledger_id"] for row in ledger.read("artists")}
    assert pair <= lids
    memberships = {(row["ledger_id"], row["frame_code"]) for row in ledger.read("frame_membership")}
    assert (OLD, FRAME) not in memberships
    assert (result.new_ledger_id, FRAME) in memberships
    assert len(ledger.read("activities")) == rows
    # A merge a person makes is refused over the split's decision unless overridden.
    with pytest.raises(GiyeError, match="decided distinct"):
        merge_people(ledger, OLD, result.new_ledger_id, evidence="H the office corrected itself again; 2026-01-21")


def test_publish_treats_the_new_record_as_a_person(demo: Path, capsys: pytest.CaptureFixture[str]):
    import json

    result = split_membership(_ledger(demo), MID, evidence=EVIDENCE)
    config = str(demo / "giye.toml")
    assert main(["normalize", "--config", config]) == 0
    assert main(["publish", "--config", config]) == 0
    capsys.readouterr()
    site = demo / "data" / "site"
    text = "".join(path.read_text(encoding="utf-8") for path in site.rglob("*.json"))
    assert result.new_gy_id in text and result.old_gy_id in text
    redirects = site / "redirects.json"
    if redirects.exists():
        data = json.loads(redirects.read_text(encoding="utf-8"))
        assert result.new_gy_id not in json.dumps(data) and result.old_gy_id not in json.dumps(data)


def test_dry_run_writes_nothing(demo: Path, capsys: pytest.CaptureFixture[str]):
    before = _ledger_bytes(demo)
    config = str(demo / "giye.toml")
    code = main(["split", "--config", config, "--membership", MID, "--evidence", EVIDENCE, "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"dry-run split {MID} → LED-") and "activities=1" in out
    assert _ledger_bytes(demo) == before
    assert not list((demo / "data" / "work" / "backups").glob("*-before-split*"))


@pytest.mark.parametrize(
    "evidence",
    [
        "",
        "the 2022 fellow is someone else",
        "E1 https://example.org/studio both list this",
        "H different; 2026-01-20",
        "H the 2022 fellow is someone else",
        f"H the 2022 fellow is someone else; {(datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()}",
        "H the 2022 fellow is someone else; 2026-02-30",
        "H the 2022 fellow is someone else; 20/01/2026",
    ],
)
def test_split_refuses_evidence_that_is_not_a_dated_h(demo: Path, evidence: str):
    before = _ledger_bytes(demo)
    with pytest.raises(GiyeError, match="split refused"):
        split_membership(_ledger(demo), MID, evidence=evidence)
    assert _ledger_bytes(demo) == before


def test_split_refuses_a_hidden_record_and_an_only_membership(demo: Path):
    ledger = _ledger(demo)
    only = next(
        row
        for row in ledger.read("frame_membership")
        if sum(1 for other in ledger.read("frame_membership") if other["ledger_id"] == row["ledger_id"]) == 1
    )
    with pytest.raises(GiyeError, match="only membership"):
        split_membership(ledger, (only["ledger_id"], only["frame_code"]), evidence=EVIDENCE)
    with pytest.raises(GiyeError, match="no membership"):
        split_membership(ledger, f"{OLD}@EXAMPLE-FORUM-2023", evidence=EVIDENCE)
    hide_person(ledger, "GY-000013", reason="person asked to be hidden")
    before = _ledger_bytes(demo)
    with pytest.raises(GiyeError, match="hidden by request"):
        split_membership(ledger, MID, evidence=EVIDENCE)
    assert _ledger_bytes(demo) == before


def test_ids_are_never_reused(demo: Path):
    ledger = _ledger(demo)
    # A retired id above every live id: the next record must go past it.
    retired = ledger.read("gy_retired")
    retired.append({"gy_id": "GY-000090", "merged_into_ledger_id": OLD, "retired_at": "2026-01-15"})
    ledger.write("gy_retired", retired, task="test")
    first = split_membership(ledger, MID, evidence=EVIDENCE)
    assert first.new_gy_id == "GY-000091"
    # A later split on another record takes the next number.
    other = next(
        row["ledger_id"]
        for row in ledger.read("frame_membership")
        if row["ledger_id"] != OLD
        and sum(1 for item in ledger.read("frame_membership") if item["ledger_id"] == row["ledger_id"]) > 1
    )
    frame = next(row["frame_code"] for row in ledger.read("frame_membership") if row["ledger_id"] == other)
    later = split_membership(ledger, f"{other}@{frame}", evidence=EVIDENCE, name_en="Mira Example")
    assert later.new_gy_id == "GY-000092"
    assert (later.name_ko, later.name_en, later.name_from) == ("Mira Example", "Mira Example", "stated")
    gys = [row["gy_id"] for row in ledger.read("artists")] + [row["gy_id"] for row in ledger.read("gy_retired")]
    assert len(gys) == len(set(gys))


def test_batch_applies_every_row_under_one_backup_or_none(demo: Path, capsys: pytest.CaptureFixture[str]):
    ledger = _ledger(demo)
    memberships = ledger.read("frame_membership")
    other = next(
        row
        for row in memberships
        if row["ledger_id"] != OLD and sum(1 for item in memberships if item["ledger_id"] == row["ledger_id"]) > 1
    )
    batch = demo / "split.csv"
    rows = [
        {"membership_id": MID, "evidence": EVIDENCE, "name_ko": "", "name_en": ""},
        {"membership_id": f"{other['ledger_id']}@{other['frame_code']}", "evidence": EVIDENCE, "name_ko": "가상인", "name_en": "Ga Sangin"},
        {"membership_id": MID, "evidence": EVIDENCE, "name_ko": "", "name_en": ""},
    ]

    def write(items):
        with batch.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["membership_id", "evidence", "name_ko", "name_en"])
            writer.writeheader()
            writer.writerows(items)

    config = str(demo / "giye.toml")
    before = _ledger_bytes(demo)
    # Row 3 names a membership row 1 already moved: the whole batch is refused.
    write(rows)
    assert main(["split", "--config", config, "--from", str(batch)]) == 2
    assert "row 3: no membership" in capsys.readouterr().err
    assert _ledger_bytes(demo) == before
    write(rows[:2])
    assert main(["split", "--config", config, "--from", str(batch)]) == 0
    out = [line for line in capsys.readouterr().out.splitlines() if line.startswith("split ")]
    assert len(out) == 2 and "name from stated" in out[1]
    backups = list((demo / "data" / "work" / "backups").glob("artists-*-before-split*"))
    assert len(backups) == 1


def test_split_of_a_small_ledger_without_roster_reports(tmp_path: Path):
    from tests.test_resolve import _act, _artist, _mem, _seed
    from tests.test_resolve import _ledger as small_ledger

    ledger = small_ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", "김하늘", "Haneul Kim")],
        [_act("LED-a", "EXAMPLE-RESIDENCY-2019", 2019), _act("LED-a", "EXAMPLE-WORKSHOP-2021", 2021)],
        [_mem("LED-a", "EXAMPLE-RESIDENCY-2019"), _mem("LED-a", "EXAMPLE-WORKSHOP-2021")],
    )
    [result] = split_memberships(ledger, [SplitRequest(("GY-000001", "EXAMPLE-WORKSHOP-2021"), EVIDENCE)])
    # No collection report: the name is the record's the line was attached under.
    assert (result.name_ko, result.name_en, result.name_from) == ("김하늘", "Haneul Kim", "record")
    assert result.new_gy_id == "GY-000002"
    assert resolve_ledger(ledger).merges == []
