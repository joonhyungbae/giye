# SPDX-License-Identifier: AGPL-3.0-only
"""No merge path touches a record hidden by request.

A hide request leaves a nameless tombstone (docs/RULES.md). A merge would undo
it: a hidden dropped row is retired and redirects to a published page that
carries its names, and a hidden survivor could be set back to ``PUBLISHED``.
So automatic merges skip a hidden record and every manual path refuses it.
People are fictitious; URLs are example.org.
"""

from __future__ import annotations

import pytest

from giye.config import GiyeError
from giye.ledger.ledger import HiddenRecordError
from giye.resolve.decide import hide_person, merge_people
from giye.resolve.service import resolve_ledger
from tests.test_resolve import _act, _artist, _ledger, _link, _mem, _seed

REASON = "H same face in both catalogues, checked by the author 2026-01-15"


def _same_site_pair(tmp_path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [_artist("LED-a", "GY-000001", "김하늘", "Kim Haneul"), _artist("LED-b", "GY-000002", "김하늘")],
        [_act("LED-a", "EXAMPLE-RESIDENCY", 2019), _act("LED-a", "EXAMPLE-RESIDENCY", 2020), _act("LED-b", "EXAMPLE-WORKSHOP", 2021)],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP")],
        [_link("LED-a", "https://haneul.example.org"), _link("LED-b", "https://haneul.example.org/")],
    )
    return ledger


def _status(ledger) -> dict[str, str]:
    return {row["ledger_id"]: row["status"] for row in ledger.read("artists")}


def test_the_pair_merges_when_nobody_is_hidden(tmp_path):
    ledger = _same_site_pair(tmp_path)
    result = resolve_ledger(ledger)
    assert [item.rule for item in result.merges] == ["E1"]


@pytest.mark.parametrize("hidden", ["GY-000001", "GY-000002"])
def test_automatic_resolve_skips_a_hidden_record(tmp_path, hidden):
    ledger = _same_site_pair(tmp_path)
    hide_person(ledger, hidden, reason="person asked to be hidden")
    for dry_run in (True, False):
        result = resolve_ledger(ledger, dry_run=dry_run)
        assert not result.merges
    assert set(_status(ledger)) == {"LED-a", "LED-b"}
    assert not ledger.read("gy_retired")
    hidden_lid = {"GY-000001": "LED-a", "GY-000002": "LED-b"}[hidden]
    assert _status(ledger)[hidden_lid] == "HIDDEN_BY_REQUEST"


@pytest.mark.parametrize("hidden", ["GY-000001", "GY-000002"])
def test_manual_merge_refuses_a_hidden_record_either_side(tmp_path, hidden):
    ledger = _same_site_pair(tmp_path)
    hide_person(ledger, hidden, reason="person asked to be hidden")
    with pytest.raises(GiyeError, match="hidden by request"):
        merge_people(ledger, "LED-a", "LED-b", evidence=REASON)
    with pytest.raises(GiyeError, match="hidden by request"):
        ledger.merge("LED-b", "LED-a", evidence=REASON, rule="H")
    # The internal path refuses as well, so no future caller can skip the check.
    with pytest.raises(HiddenRecordError):
        ledger._merge_rows("LED-a", "LED-b", evidence=REASON, rule="H")
    assert set(_status(ledger)) == {"LED-a", "LED-b"}
    assert "HIDDEN_BY_REQUEST" in _status(ledger).values()


def test_a_merge_never_publishes_a_hidden_survivor(tmp_path):
    from giye.ledger.ledger import _merge_artist_fields

    survivor = {"ledger_id": "LED-a", "status": "HIDDEN_BY_REQUEST", "cv_link_ok": ""}
    other = {"ledger_id": "LED-b", "status": "PUBLISHED", "cv_link_ok": "yes", "name_ko": "김하늘"}
    _merge_artist_fields(survivor, [other], evidence=REASON, rule="H")
    assert survivor["status"] == "HIDDEN_BY_REQUEST"
