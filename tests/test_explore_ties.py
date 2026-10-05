# SPDX-License-Identifier: AGPL-3.0-only
"""Co-presence ties on synthetic rows. People are fictitious ids; venues are public places."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from giye.cli import main
from giye.explore import ties as T

PATTERNS = {"EXAMPLE-RESIDENCY": "example residency|예시 레지던시"}


def row(index, person, year, venue="", *, origin="cv:S", publishable="yes", title="exhibition"):
    return {
        "activity_id": f"a{index}",
        "ledger_id": person,
        "year": str(year),
        "venue": venue,
        "title": title,
        "origin": origin,
        "publishable": publishable,
    }


def institution(*ids, venue="VEN-1", kind="institution"):
    return {activity: {"venue_id": venue, "venue_kind": kind, "funder_id": ""} for activity in ids}


def test_pairs_are_unordered_and_counted_once():
    groups = {("V", 2020): {"b", "a"}, ("V", 2021): {"a", "b", "c"}, ("W", 2020): {"z"}}
    assert T.pairs_of(groups) == {("a", "b"), ("a", "c"), ("b", "c")}


def test_cv_listing_reads_only_usable_cv_rows_at_institutions():
    activities = [
        row(1, "p1", 2020),
        row(2, "p2", 2020),
        row(3, "p3", 2020, publishable="no"),  # not publishable
        row(4, "p4", 2020, origin="EXAMPLE-RESIDENCY-2020"),  # a roster row, not a CV row
        row(5, "p5", 1945, title="Art after 1945"),  # P1 Y2: the year is the title's period
        row(6, "p6", 1945),
        row(7, "p7", 2020),  # a funder
        row(8, "p8", ""),  # no year
    ]
    annotations = {
        **institution("a1", "a2", "a3", "a4", "a8"),
        **institution("a5", "a6", venue="VEN-2"),
        **institution("a7", venue="FUN-1", kind="funder"),
    }
    assert T.cv_listing_ties(activities, annotations) == {("p1", "p2")}
    assert T.cv_population(activities) == {"p1", "p2", "p6", "p7"}


def test_roster_independent_drops_a_cv_row_that_restates_the_own_edition():
    memberships = [
        {"ledger_id": "p1", "frame_code": "EXAMPLE-RESIDENCY-2020"},
        {"ledger_id": "p3", "frame_code": "EXAMPLE-RESIDENCY"},  # undated: drops nothing
        {"ledger_id": "p4", "frame_code": "OTHER-PROGRAMME-2020"},  # no event pattern
    ]
    activities = [
        row(1, "p1", 2020, "Example Hall", title="Example Residency open studio"),  # restates: dropped
        row(2, "p1", 2021, "Example Hall", title="Example Residency open studio"),  # next year: kept
        row(3, "p2", 2020, "Example Hall"),
        row(4, "p2", 2021, "Example Hall", publishable="no"),  # publishable is not read here
        row(5, "p3", 2020, "Example Hall", title="Example Residency"),
        row(6, "p4", 2020, "Example Hall", title="Example Residency"),
    ]
    annotations = institution(*(f"a{index}" for index in range(1, 7)))
    found = T.roster_independent_ties(activities, annotations, memberships, PATTERNS)
    assert found == {("p1", "p2"), ("p2", "p3"), ("p2", "p4"), ("p3", "p4")}
    assert T.roster_independent_ties(activities, annotations, memberships, PATTERNS, {"p2", "p3"}) == {
        ("p2", "p3")
    }
    kept, dropped = T.roster_independent_rows(activities, memberships, PATTERNS)
    assert dropped == 1 and len(kept) == 5


def test_processed_rows_carry_their_own_venue():
    """A processed row (venue_id, venue_kind, title_norm, venue_norm) needs no annotations."""
    memberships = [{"ledger_id": "p1", "frame_code": "EXAMPLE-RESIDENCY-2020"}]
    ledger_rows = [
        row(1, "p1", 2020, "Example Hall", title="Example Residency"),
        row(2, "p1", 2020, "Example Hall", title="talk"),
        row(3, "p2", 2020, "Example Hall"),
    ]
    annotations = institution("a1", "a2", "a3")
    processed = [
        {
            **{key: value for key, value in item.items() if key not in {"title", "venue"}},
            "title_norm": item["title"],
            "venue_norm": item["venue"],
            **annotations[item["activity_id"]],
        }
        for item in ledger_rows
    ]
    from_ledger = T.roster_independent_ties(ledger_rows, annotations, memberships, PATTERNS)
    assert T.roster_independent_ties(processed, None, memberships, PATTERNS) == from_ledger == {("p1", "p2")}


def _museum_rows():
    # p1 writes the museum four ways. Each other person writes the bare name in one of p1's years,
    # so each layer joins exactly one more pair.
    return [
        row(1, "p1", 2020, "서울시립미술관"),
        row(2, "p2", 2020, "서울시립미술관"),  # base
        row(3, "p1", 2021, "서울시립미술관 《빛》"),
        row(4, "p3", 2021, "서울시립미술관"),  # V7a: the exhibition title is trimmed
        row(5, "p1", 2023, "서울시립미술관 전시실"),
        row(6, "p4", 2023, "서울시립미술관"),  # V8: a hall is part of the building
        row(7, "p1", 2019, "Seoul Museum of Art"),
        row(8, "p5", 2019, "서울시립미술관"),  # V9: the Hangul reading matches the Latin bag
    ]


def test_layers_add_one_tie_each_and_the_replay_matches():
    activities = _museum_rows()
    report = T.layer_report(activities, T.resolve_layers(activities))
    assert report["n_people"] == 5
    assert [report["ties"][name]["cv_listing"] for name, _rules in T.LAYERS] == [1, 2, 3, 4]
    attribution = report["attribution"]
    assert attribution["V7a-d"] == 1
    assert attribution["V7e"] == {"merges": 0, "merges_that_added_ties": 0, "ties_added": 0}
    assert attribution["V8"] == {"merges": 1, "merges_that_added_ties": 1, "ties_added": 1}
    assert attribution["V9"] == {"merges": 1, "merges_that_added_ties": 1, "ties_added": 1}
    assert report["entities"]["base"]["entities"] > report["entities"]["V7+V8+V9"]["entities"] == 1


def test_attribution_counts_a_pair_once_and_skips_prior_ties():
    obs = [("a", 2020, "k1"), ("b", 2020, "k2"), ("a", 2020, "k2"), ("c", 2020, "k3")]
    roots = {"k1": "k1", "k2": "k2", "k3": "k3"}
    merges = [("V8", "k1", "k2"), ("V9", "k1", "k3")]
    report = T.attribute_merges(obs, roots, merges)
    # a sits on k1 and k2, so (a, b) is already a tie before V8 joins them.
    assert report["ties_after_spelling_before_merges"] == 1
    assert report["per_rule"]["V8"] == {"merges": 1, "merges_that_added_ties": 0, "ties_added": 0}
    assert report["per_rule"]["V9"]["ties_added"] == 2
    with_prior = T.attribute_merges(obs, roots, merges, prior={("a", "c")})
    assert with_prior["per_rule"]["V9"]["ties_added"] == 1


def test_layer_report_raises_when_a_layer_is_missing():
    activities = _museum_rows()
    builds = T.resolve_layers(activities, layers=T.LAYERS[:2])
    with pytest.raises(ValueError):
        T.layer_report(activities, builds)


def _write_archive(root: Path) -> Path:
    ledger = root / "data" / "ledger"
    ledger.mkdir(parents=True)
    fields = ["activity_id", "ledger_id", "year", "venue", "title", "origin", "publishable"]
    lines = [",".join(fields)]
    for item in _museum_rows():
        lines.append(",".join(item[name] for name in fields))
    (ledger / "activities.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ledger / "frame_membership.csv").write_text("ledger_id,frame_code\n", encoding="utf-8")
    (root / "frames.yml").write_text("frames: []\n", encoding="utf-8")
    config = root / "giye.toml"
    config.write_text('[archive]\nname = "Synthetic"\n', encoding="utf-8")
    return config


def test_cli_writes_ties_and_layers(tmp_path: Path, capsys):
    config = _write_archive(tmp_path)
    out = tmp_path / "ties.json"
    layers = tmp_path / "layers.json"
    assert main(["explore", "ties", "--config", str(config), "--out", str(out), "--layers", str(layers)]) == 0
    printed = capsys.readouterr().out
    assert "ties\troster-independent\tties=4" in printed
    assert "V7+V8+V9\t1\t1\t4\t4" in printed
    assert "added\tV9\t1\tmerges=1\tmerges_that_added_ties=1" in printed
    assert json.loads(out.read_text(encoding="utf-8")) == [["p1", "p2"], ["p1", "p3"], ["p1", "p4"], ["p1", "p5"]]
    assert json.loads(layers.read_text(encoding="utf-8"))["ties"]["base"] == {"cv_listing": 1, "roster_independent": 1}
