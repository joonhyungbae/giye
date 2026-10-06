# SPDX-License-Identifier: AGPL-3.0-only
"""Queue, merge, hide, and evidence commands on a temp copy of the demo ledger."""

from __future__ import annotations

import csv
import json
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from giye.cli import main
from giye.config import load
from giye.demo import run_demo
from giye.ledger.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"
CLOCK = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _copy(tmp_path: Path) -> Path:
    dest = tmp_path / "demo"
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("data", "__pycache__", "*.pyc"))
    run_demo(dest / "giye.toml", dest / "data", now=CLOCK)
    return dest


def _artists(dest: Path) -> list[dict[str, str]]:
    return Ledger.open(load(dest / "giye.toml")).read("artists")


def _named(rows: list[dict[str, str]], name: str) -> list[dict[str, str]]:
    return [row for row in rows if row.get("name_ko") == name or row.get("name_en") == name]


def _other_live(item: dict[str, str], live: set[str]) -> bool:
    from giye.resolve.candidates import review_id_set

    own = item.get("ledger_id") or ""
    others = review_id_set(item) - {own}
    return len(others) == 1 and own in live and others <= live


def _queue(dest: Path) -> list[dict[str, str]]:
    path = dest / "data" / "ledger" / "review_queue.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_queue_merge_hide_and_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def boom(self, url, *args, **kwargs):
        raise requests.ConnectionError(f"network blocked: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    dest = _copy(tmp_path)
    config = str(dest / "giye.toml")
    artists = _artists(dest)
    minsoo = _named(artists, "최민수")
    assert len(minsoo) == 2
    bae = _named(artists, "배수아")
    assert len(bae) == 2
    assert any("members=" in (row.get("reviewer_note") or "") for row in bae)
    assert any("members=" not in (row.get("reviewer_note") or "") for row in bae)

    assert main(["queue", "list", "--config", config, "--status", "open"]) == 0
    listed = [line for line in capsys.readouterr().out.splitlines() if "\topen\t" in line]
    assert len(listed) == 4
    assert any("latin name only" in line for line in listed)

    live = {row["ledger_id"] for row in artists}
    open_items = [row for row in _queue(dest) if row["status"] == "open" and row["reason"] == "possible_same_person"]
    by_detail = {row["queue_id"]: row.get("detail") or "" for row in open_items}

    def item_for(token: str) -> dict[str, str]:
        found = [row for row in open_items if token in by_detail[row["queue_id"]]]
        assert len(found) == 1, by_detail
        return found[0]

    distinct = item_for("서지우")
    dismiss = item_for("배수아")
    merging = item_for("최민수")
    assert _other_live(merging, live)

    assert main(["queue", "decide", distinct["queue_id"], "--config", config, "--decision", "distinct"]) == 0
    capsys.readouterr()
    assert main(
        ["queue", "decide", dismiss["queue_id"], "--config", config, "--decision", "dismiss", "--note", "not this edition"]
    ) == 0
    capsys.readouterr()
    assert main(["queue", "decide", merging["queue_id"], "--config", config, "--decision", "merge"]) == 2
    assert "evidence" in capsys.readouterr().err
    assert (
        main(
            [
                "queue",
                "decide",
                merging["queue_id"],
                "--config",
                config,
                "--decision",
                "merge",
                "--evidence",
                "H the queued pair is one person, checked by the author 2026-01-15",
            ]
        )
        == 0
    )
    capsys.readouterr()
    decided = next(row for row in _queue(dest) if row["queue_id"] == merging["queue_id"])
    assert decided["status"] == "done" and "decided=same" in decided["detail"]

    assert main(["merge", minsoo[0]["gy_id"], minsoo[1]["gy_id"], "--config", config, "--evidence", "   "]) == 2
    err = capsys.readouterr().err
    assert err.startswith("giye: error:") and "Traceback" not in err
    # The queue decision already merged this pair. A second merge of the same gy_id
    # is the dropped id, which no longer has a row. The CLI path is exercised on
    # two other people below, after the team guard.
    assert main(["merge", bae[0]["gy_id"], bae[1]["gy_id"], "--config", config, "--evidence", "E1 https://example.org/team a team is not a person"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("giye: error:") and "T1" in err and "Traceback" not in err
    assert len(_named(_artists(dest), "배수아")) == 2

    others = [
        row
        for row in _artists(dest)
        if row["gy_id"] not in {item["gy_id"] for item in minsoo + bae} and "members=" not in (row.get("reviewer_note") or "")
    ]
    keep, drop = others[0], others[1]
    assert (
        main(
            ["merge", keep["gy_id"], drop["gy_id"], "--config", config, "--evidence", "H the two rows are one person, checked by the author 2026-01-15"]
        )
        == 0
    )
    merged_out = capsys.readouterr().out
    assert f"retired {drop['gy_id']}" in merged_out
    retired = Ledger.open(load(dest / "giye.toml")).read("gy_retired")
    redirect = next(row for row in retired if row["gy_id"] == drop["gy_id"])
    assert redirect["merged_into_ledger_id"] == keep["ledger_id"]
    retired_gy = {row["gy_id"] for row in retired}
    gone = [row for row in minsoo if row["gy_id"] in retired_gy]
    kept_minsoo = [row for row in minsoo if row["gy_id"] not in retired_gy]
    assert len(gone) == 1 and len(kept_minsoo) == 1
    redirect_minsoo = next(row for row in retired if row["gy_id"] == gone[0]["gy_id"])
    assert redirect_minsoo["merged_into_ledger_id"] == kept_minsoo[0]["ledger_id"]
    assert any((dest / "data" / "work" / "backups").glob("artists-*-before-merge.csv.gz"))
    assert main(["resolve", "--config", config]) == 0
    capsys.readouterr()
    assert main(["resolve", "--config", config]) == 0
    capsys.readouterr()
    after_rows = _queue(dest)
    after = {row["queue_id"]: row for row in after_rows}
    assert after[distinct["queue_id"]]["status"] == "done"
    assert "decided=different" in after[distinct["queue_id"]]["detail"]
    assert after[dismiss["queue_id"]]["status"] == "dismissed"
    assert "decided=dismissed" in after[dismiss["queue_id"]]["detail"]
    # A decided X1 pair is not opened again. Two resolves add no second item.
    assert [row["queue_id"] for row in after_rows].count(distinct["queue_id"]) == 1
    assert not any(row["status"] == "open" and "서지우" in (row.get("detail") or "") for row in after_rows)

    hidden = next(row for row in _artists(dest) if row.get("status") != "MERGED" and row.get("name_ko"))
    gy = hidden["gy_id"]
    name = hidden["name_ko"]
    assert main(["hide", gy, "--config", config, "--reason", "asked to be removed"]) == 0
    capsys.readouterr()
    assert any((dest / "data" / "work" / "backups").glob("artists-*-before-hide.csv.gz"))
    assert main(["publish", "--config", config]) == 0
    capsys.readouterr()
    stubs = json.loads((dest / "data" / "site" / "artist_stubs.json").read_text(encoding="utf-8"))
    published = json.loads((dest / "data" / "site" / "artists.json").read_text(encoding="utf-8"))
    assert stubs[gy] == "HIDDEN_BY_REQUEST"
    assert name not in {row.get("name_ko") for row in published}
    # publish also rewrites the ring, so the hidden id leaves rim_order.json without `giye explore`.
    assert gy not in (dest / "data" / "site" / "rim_order.json").read_text(encoding="utf-8")
    assert main(["unhide", gy, "--config", config]) == 0
    capsys.readouterr()
    assert main(["publish", "--config", config]) == 0
    capsys.readouterr()
    published = json.loads((dest / "data" / "site" / "artists.json").read_text(encoding="utf-8"))
    assert name in {row.get("name_ko") for row in published}
    stubs = json.loads((dest / "data" / "site" / "artist_stubs.json").read_text(encoding="utf-8"))
    assert gy not in stubs

    assert main(["evidence", "--config", config]) == 0
    out = capsys.readouterr().out
    assert out.startswith("evidence ")
    assert "Traceback" not in capsys.readouterr().err


def test_manual_merge_needs_a_rule_and_a_source(tmp_path: Path):
    from giye.config import GiyeError
    from giye.resolve.decide import check_merge_evidence

    dest = _copy(tmp_path)
    ledger = Ledger.open(load(dest / "giye.toml"))
    source_id = ledger.read("cv_sources")[0]["source_id"]
    frame_code = ledger.read("frame_membership")[0]["frame_code"]
    for bad in (
        "the queued pair is one person",
        "E2",
        "E2 same teacher, same city",
        "E5 https://example.org/a",
        "X1 https://example.org/a",
        "H same person",
        "H 2026-01-15",
        "H same person 2026-02-30",
        "Hm https://example.org/a",
    ):
        with pytest.raises(GiyeError, match="merge"):
            check_merge_evidence(ledger, bad)
    assert check_merge_evidence(ledger, "E2 https://example.org/press/2020") == "E2"
    assert check_merge_evidence(ledger, f"X1+E3: {source_id} lists both names") == "X1+E3"
    assert check_merge_evidence(ledger, f"E1 roster {frame_code}") == "E1"
    assert check_merge_evidence(ledger, f"E1 roster {frame_code}-2021") == "E1"
    assert check_merge_evidence(ledger, "H same face in both catalogues, checked by the author 2026-01-15") == "H"


def test_merge_over_a_distinct_decision_needs_the_override(tmp_path: Path):
    from giye.config import GiyeError
    from giye.resolve.candidates import review_id_set
    from giye.resolve.decide import decide_queue, merge_people

    dest = _copy(tmp_path)
    ledger = Ledger.open(load(dest / "giye.toml"))
    item = next(
        row
        for row in ledger.read("review_queue")
        if row["status"] == "open" and row["reason"] == "possible_same_person" and "서지우" in row["detail"]
    )
    decide_queue(ledger, item["queue_id"], "distinct")
    decided = next(row for row in ledger.read("review_queue") if row["queue_id"] == item["queue_id"])
    assert "decided=different" in decided["detail"] and "decided_at=" in decided["detail"]
    left = item["ledger_id"]
    right = min(review_id_set(item) - {left})
    evidence = "H the press release names both spellings, read by author 2026-01-15"
    with pytest.raises(GiyeError, match="distinct"):
        merge_people(ledger, left, right, evidence=evidence)
    assert {row["ledger_id"] for row in ledger.read("artists")} >= {left, right}
    # Review round 6: a judgement dated before the decision it overrides is refused.
    with pytest.raises(GiyeError, match="dated before the distinct decision"):
        merge_people(ledger, left, right, evidence="H the press release names both spellings, read by author 2026-01-20", override_distinct=True)
    evidence = f"H the press release names both spellings, read by author {_today()}"

    keep, drop = merge_people(ledger, left, right, evidence=evidence, override_distinct=True)
    kept = next(row for row in ledger.read("artists") if row["ledger_id"] == keep)
    import re

    assert re.search(r"overrides distinct decision of \d{4}-\d{2}-\d{2}", kept["reviewer_note"])
    assert "rule=H" in kept["reviewer_note"]
    assert drop not in {row["ledger_id"] for row in ledger.read("artists")}
    after = next(row for row in ledger.read("review_queue") if row["queue_id"] == item["queue_id"])
    assert "decided=different" not in after["detail"]
    assert "decided=same" in after["detail"] and "overrides distinct decision of " in after["detail"]


def test_public_ledger_merge_honours_a_distinct_decision(tmp_path: Path):
    """Review round 5: ``Ledger.merge`` merged a pair decided distinct and left the queue saying so."""
    import re

    from giye.config import GiyeError
    from giye.resolve.candidates import review_id_set
    from giye.resolve.decide import decide_queue

    dest = _copy(tmp_path)
    ledger = Ledger.open(load(dest / "giye.toml"))
    item = next(
        row
        for row in ledger.read("review_queue")
        if row["status"] == "open" and row["reason"] == "possible_same_person" and "서지우" in row["detail"]
    )
    decide_queue(ledger, item["queue_id"], "distinct")
    left = item["ledger_id"]
    right = min(review_id_set(item) - {left})
    evidence = f"H x y z by author {_today()}"
    with pytest.raises(GiyeError, match="distinct"):
        ledger.merge(left, right, evidence=evidence, rule="H")
    assert {row["ledger_id"] for row in ledger.read("artists")} >= {left, right}

    ledger.merge(left, right, evidence=evidence, rule="H", override_distinct=True)
    live = {row["ledger_id"]: row for row in ledger.read("artists")}
    assert len({left, right} & set(live)) == 1
    kept = live[left] if left in live else live[right]
    assert re.search(r"overrides distinct decision of \d{4}-\d{2}-\d{2}", kept["reviewer_note"])
    after = next(row for row in ledger.read("review_queue") if row["queue_id"] == item["queue_id"])
    assert "decided=different" not in after["detail"]
    assert "overrides distinct decision of " in after["detail"] and "decided=same" in after["detail"]


def _today() -> str:
    """The date ``decided_at`` records (UTC), so an H judgement is not dated before it."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()
