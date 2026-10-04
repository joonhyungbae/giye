# SPDX-License-Identifier: AGPL-3.0-only
"""Division scores. The planted and random cases use fictitious ids only.

The algebraic case is the one locked in research/flocks/evaluate.py before any
archive row is read: three ties inside two blocks of three, lift 2.5, AUC 0.875.
"""

from __future__ import annotations

import numpy as np

from giye.explore.evaluate import (
    _pair_metrics_counts,
    adjusted_rand,
    coverage,
    evaluate,
    pair_scores,
)


def _blocks() -> tuple[dict[str, int], set[tuple[str, str]]]:
    # Ids sort in the same order as the integer labels used in the flock self-check.
    assignment = {str(i): 0 if i < 3 else 1 for i in range(6)}
    ties = {("0", "1"), ("0", "2"), ("3", "4")}
    return assignment, ties


def test_pair_algebra_matches_the_flock_self_check():
    assignment, ties = _blocks()
    stats = pair_scores(assignment, ties)
    assert stats is not None
    assert stats["lift"] == 2.5
    assert abs(stats["auc"] - 0.875) < 1e-12
    labels = np.array([0, 0, 0, 1, 1, 1])
    doubled = _pair_metrics_counts(
        labels,
        np.array([2, 1, 1, 1, 1, 1], dtype=np.int64),
        np.array([0, 0, 3], dtype=np.int64),
        np.array([1, 2, 4], dtype=np.int64),
        np.array([True, True, True]),
    )
    assert doubled is not None and doubled["n_ties"] == 5


def test_adjusted_rand_known_partitions():
    def mapping(labels: list[int]) -> dict[str, int]:
        return {str(i): label for i, label in enumerate(labels)}

    assert adjusted_rand(mapping([0, 0, 1, 1]), mapping([0, 0, 1, 1])) == 1.0
    assert adjusted_rand(mapping([0, 0, 1, 1]), mapping([1, 1, 0, 0])) == 1.0
    assert adjusted_rand(mapping([0, 0, 1, 1]), mapping([0, 0, 0, 0])) == 0.0
    assert adjusted_rand(mapping([0, 0, 1, 1]), mapping([0, 1, 0, 1])) == -0.5
    assert adjusted_rand(mapping([0, 0, 0, 0]), mapping([1, 1, 1, 1])) == 1.0
    crossed = adjusted_rand(mapping([0, 0, 0, 1, 1, 1]), mapping([0, 0, 1, 1, 1, 1]))
    assert abs(crossed - 0.32432432432432434) < 1e-12


def test_planted_division_lifts_above_one_and_a_random_one_does_not():
    planted = {f"p{i:02d}": "A" if i < 20 else "B" for i in range(40)}
    planted_ties: set[tuple[str, str]] = set()
    for block in (range(20), range(20, 40)):
        ids = [f"p{i:02d}" for i in block]
        for left_i, left in enumerate(ids):
            for right in ids[left_i + 1 :]:
                planted_ties.add((left, right))
    found = evaluate(planted, planted_ties, seed=0, n_boot=30, n_metric_boot=200)
    assert found["lift"] > 1
    assert found["lift_ci95"][0] > 1
    assert found["coverage"]["placed_share"] == 1
    assert found["coverage"]["n_groups"] == 2
    assert found["stability"]["ari_mean"] == 1.0

    n = 80
    assignment = {f"p{i:02d}": "A" if i % 2 == 0 else "B" for i in range(n)}
    pair_index = [(i, j) for i in range(n) for j in range(i + 1, n)]
    picked = np.random.default_rng(1).choice(len(pair_index), size=400, replace=False)
    random_ties = {(f"p{pair_index[k][0]:02d}", f"p{pair_index[k][1]:02d}") for k in picked}
    chance = evaluate(assignment, random_ties, seed=0, n_boot=30, n_metric_boot=200)
    assert abs(chance["lift"] - 1) < 0.15
    low, high = chance["lift_ci95"]
    assert low <= 1 <= high
    assert chance["stability"]["ari_mean"] == 1.0
    # The same seed repeats. A second generator stream does not depend on the first call.
    again = evaluate(assignment, random_ties, seed=0, n_boot=30, n_metric_boot=200)
    assert again["lift_ci95"] == chance["lift_ci95"]
    assert again["auc_ci95"] == chance["auc_ci95"]


def test_identical_assignments_have_stability_one_and_a_refit_can_move_it():
    assignment = {f"p{i}": i % 3 for i in range(12)}
    other = dict(assignment)
    assert adjusted_rand(assignment, other) == 1.0
    result = evaluate(assignment, set(), seed=0, n_boot=20, n_metric_boot=10)
    # No ties: lift is undefined, and the fixed labels still score 1.
    assert result["lift"] is None
    assert result["stability"]["ari_mean"] == 1.0
    assert result["stability"]["ari_p05"] == 1.0 and result["stability"]["ari_p95"] == 1.0

    def flipped(ids, draw):
        del draw
        return {person: person for person in ids}

    moved = evaluate(assignment, {("p0", "p1")}, seed=0, n_boot=15, n_metric_boot=10, refit=flipped)
    assert moved["stability"]["ari_mean"] < 1


def test_coverage_counts_an_unplaced_person_outside_the_groups():
    described = coverage({"a": "x", "b": "x", "c": None, "d": "y"})
    assert described["n_people"] == 4
    assert described["n_placed"] == 3
    assert described["placed_share"] == 0.75
    assert described["n_groups"] == 2
    assert described["n_singletons"] == 1
    assert described["sizes_desc"] == [2, 1]
