# SPDX-License-Identifier: AGPL-3.0-only
"""A batch of judged merges folds the CV extractions once, not once per merge.

Why: ``fold_merged_cvs`` re-applies every extraction on the ledger, so a fold
after the last merge gives the same ledger as a fold after each one, and a fold
per merge made about a hundred judged merges take hours. People are fictitious.
"""

from __future__ import annotations

import giye.resolve.cv as cv_module
import giye.resolve.decide as decide_module
from giye.resolve.decide import merge_people
from tests.test_resolve import _act, _artist, _ledger, _mem, _seed

REASON = "H same person in both catalogues, checked by the author 2026-01-15"


def _three_records(tmp_path):
    ledger = _ledger(tmp_path)
    _seed(
        ledger,
        [
            _artist("LED-a", "GY-000001", "김하늘", "Kim Haneul"),
            _artist("LED-b", "GY-000002", "김하늘"),
            _artist("LED-c", "GY-000003", "김하늘"),
        ],
        [_act("LED-a", "EXAMPLE-RESIDENCY", 2019), _act("LED-b", "EXAMPLE-WORKSHOP", 2021), _act("LED-c", "EXAMPLE-LAB", 2023)],
        [_mem("LED-a", "EXAMPLE-RESIDENCY"), _mem("LED-b", "EXAMPLE-WORKSHOP"), _mem("LED-c", "EXAMPLE-LAB")],
        [],
    )
    return ledger


def _count_folds(monkeypatch) -> list[int]:
    calls: list[int] = []

    def fake_fold(ledger) -> None:
        calls.append(1)

    monkeypatch.setattr(decide_module, "fold_merged_cvs", fake_fold)
    monkeypatch.setattr(cv_module, "fold_merged_cvs", fake_fold)
    return calls


def test_merge_people_folds_once_by_default(tmp_path, monkeypatch):
    calls = _count_folds(monkeypatch)
    ledger = _three_records(tmp_path)
    merge_people(ledger, "LED-a", "LED-b", evidence=REASON)
    assert len(calls) == 1


def test_fold_false_leaves_the_fold_to_the_caller(tmp_path, monkeypatch):
    calls = _count_folds(monkeypatch)
    ledger = _three_records(tmp_path)
    merge_people(ledger, "LED-a", "LED-b", evidence=REASON, fold=False)
    merge_people(ledger, "LED-a", "LED-c", evidence=REASON, fold=False)
    assert calls == []
    assert {row["ledger_id"] for row in ledger.read("artists")} == {"LED-a"}


def test_ledger_merge_of_several_drops_folds_once(tmp_path, monkeypatch):
    calls = _count_folds(monkeypatch)
    ledger = _three_records(tmp_path)
    ledger.merge("LED-a", ["LED-b", "LED-c"], evidence=REASON, rule="H")
    assert len(calls) == 1
    assert {row["ledger_id"] for row in ledger.read("artists")} == {"LED-a"}


def test_one_fold_at_the_end_equals_a_fold_per_merge(tmp_path):
    per_merge = _three_records(tmp_path / "per")
    merge_people(per_merge, "LED-a", "LED-b", evidence=REASON)
    merge_people(per_merge, "LED-a", "LED-c", evidence=REASON)
    batched = _three_records(tmp_path / "batch")
    merge_people(batched, "LED-a", "LED-b", evidence=REASON, fold=False)
    merge_people(batched, "LED-a", "LED-c", evidence=REASON, fold=False)
    decide_module.fold_merged_cvs(batched)

    def table(ledger, name):
        return sorted(tuple(sorted(row.items())) for row in ledger.read(name))

    for name in ("artists", "activities", "frame_membership", "gy_retired"):
        left, right = table(per_merge, name), table(batched, name)
        # Merge timestamps differ between the two runs; compare everything else.
        strip = lambda rows: [tuple(kv for kv in row if kv[0] not in {"retired_at", "updated_at"}) for row in rows]
        assert strip(left) == strip(right), name
