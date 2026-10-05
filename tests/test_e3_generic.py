# SPDX-License-Identifier: AGPL-3.0-only
"""E3 refuses a generic work title: one that many records use, or a very short one.

A title base (normalised, trailing number dropped) credited to or listed by at
least ``[resolve] generic_title_records`` distinct records does not identify
one work. The resolver and the manual-merge check read the same set. People
are fictitious; URLs are example.org.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from giye.config import GiyeError, load
from giye.ledger.ledger import Ledger
from giye.resolve.decide import verify_merge_evidence
from giye.resolve.evidence import (
    GENERIC_TITLE_RECORDS,
    generic_titles,
    is_work_title,
    norm_title,
    roster_works,
    title_base,
    title_records,
)
from giye.resolve.service import resolve_ledger
from tests.test_resolve import _act, _artist, _config, _cv, _mem, _seed


def _ledger(tmp_path: Path, threshold: int | None = None) -> Ledger:
    config = load(_config(tmp_path))
    if threshold is not None:
        config = replace(config, generic_title_records=threshold)
    return Ledger.open(config)


def _untitled_pair(ledger: Ledger, *, others: int) -> None:
    """Two 한별 records crediting 〈Untitled #3〉, and ``others`` unrelated records whose CVs list Untitled."""
    _seed(
        ledger,
        [
            _artist("LED-a", "GY-000001", "한별"),
            _artist("LED-b", "GY-000002", "한별", "Han Byeol"),
            _artist("LED-c", "GY-000003", "박서연", "Seoyeon Park"),
            _artist("LED-d", "GY-000004", "정다운", "Daun Jeong"),
        ],
        [
            _act("LED-a", "EXAMPLE-RESIDENCY", 2019, role="〈Untitled #3〉"),
            _act("LED-b", "EXAMPLE-RESIDENCY", 2019, role="〈Untitled #3〉"),
            _act("LED-c", "EXAMPLE-WORKSHOP", 2015),
            _act("LED-d", "EXAMPLE-WORKSHOP", 2016),
        ],
        [
            _mem("LED-a", "EXAMPLE-RESIDENCY"),
            _mem("LED-b", "EXAMPLE-RESIDENCY"),
            _mem("LED-c", "EXAMPLE-WORKSHOP"),
            _mem("LED-d", "EXAMPLE-WORKSHOP"),
        ],
    )
    # A plain CV line counts its whole title: "Untitled (2012)" has the base "untitled".
    for lid in ("LED-c", "LED-d")[:others]:
        _cv(ledger, lid, [{"title": "Untitled (2012)", "venue": "", "year": 2012}])


def test_title_base_and_short_titles():
    assert title_base(norm_title("Untitled #3")) == "untitled"
    assert title_base(norm_title("Untitled (2019)")) == "untitled"
    assert title_base(norm_title("무제 3")) == "무제"
    assert is_work_title(norm_title("푸른 신호"))
    # The base is shorter than 2 characters, though the title has 3 or more.
    assert not is_work_title(norm_title("2019"))
    assert not is_work_title(norm_title("A 12"))
    assert not is_work_title(norm_title("Untitled #3"), {"untitled"})


def test_title_records_counts_distinct_records_over_rosters_and_cvs():
    rows_of = {
        "LED-a": [{"title": "FRAME", "role": "〈Untitled #1〉"}, {"title": "FRAME", "role": "〈Untitled #2〉"}],
        "LED-b": [{"title": "〈Untitled〉", "role": ""}],
    }
    cvs = {"LED-c": [{"title": "Untitled, 2010"}], "LED-a": [{"title": "Untitled"}]}
    counts = title_records(rows_of, cvs)
    assert counts["untitled"] == 3
    assert generic_titles(rows_of, cvs, 3) == frozenset({"untitled"})
    assert generic_titles(rows_of, cvs, 4) == frozenset()
    # 0 turns the rule off.
    assert generic_titles(rows_of, cvs, 0) == frozenset()
    rows = [{"origin": "FRAME", "year": "2019", "title": "FRAME", "role": "〈Untitled #3〉"}]
    assert roster_works(rows, generic_titles(rows_of, cvs, 3)) == set()
    assert roster_works(rows) == {("untitled3", 2019)}


def test_resolver_skips_a_title_four_records_use(tmp_path: Path):
    assert GENERIC_TITLE_RECORDS == 4
    # The pair and one other record use the title: it is still E3 evidence.
    ledger = _ledger(tmp_path / "three")
    _untitled_pair(ledger, others=1)
    assert any(item.rule == "E3" for item in resolve_ledger(ledger).merges)

    # Two other records list it too: no merge.
    ledger = _ledger(tmp_path / "four")
    _untitled_pair(ledger, others=2)
    result = resolve_ledger(ledger)
    assert not any(item.rule == "E3" for item in result.merges)
    assert len(ledger.read("artists")) == 4

    # With the rule off, the same ledger merges on E3.
    ledger = _ledger(tmp_path / "off", threshold=0)
    _untitled_pair(ledger, others=2)
    assert any(item.rule == "E3" for item in resolve_ledger(ledger).merges)


def test_manual_merge_check_refuses_a_generic_title(tmp_path: Path):
    ledger = _ledger(tmp_path / "pair")
    _untitled_pair(ledger, others=0)
    assert verify_merge_evidence(ledger, "LED-a", "LED-b", "E3 〈Untitled #3〉 https://example.org/roster") == "E3"

    ledger = _ledger(tmp_path / "four")
    _untitled_pair(ledger, others=2)
    with pytest.raises(GiyeError, match="E3 does not hold"):
        verify_merge_evidence(ledger, "LED-a", "LED-b", "E3 〈Untitled #3〉 https://example.org/roster")


def test_config_key(tmp_path: Path):
    path = _config(tmp_path)
    assert load(path).generic_title_records == GENERIC_TITLE_RECORDS
    text = path.read_text(encoding="utf-8").replace("[resolve]\n", "[resolve]\ngeneric_title_records = 5\n", 1)
    path.write_text(text, encoding="utf-8")
    assert load(path).generic_title_records == 5
    path.write_text(text.replace("= 5", "= -1"), encoding="utf-8")
    with pytest.raises(TypeError, match="generic_title_records"):
        load(path)
