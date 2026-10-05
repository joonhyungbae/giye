# SPDX-License-Identifier: AGPL-3.0-only
"""Score a division of people against ties the division was not built from.

This module only measures an assignment the caller already has. It does not
build the clusters. See docs/EXPLORE.md.

Given ``{person_id: group}`` and unordered pairs of person ids (for example
two people at the same institution in the same year, already filtered so a
pair does not restate a roster appearance):

- Coverage: how many people are placed, how many groups, the largest share,
  and how many groups hold one person.
- Bootstrap stability: adjusted Rand index of the assignment against itself
  on people drawn with replacement. A fixed assignment scores 1, because the
  labels do not move. A ``refit`` callable can label each draw again; the
  index is then against the full assignment, on people who appear at least
  once. Adjusted Rand is implemented here so this module does not depend on
  scikit-learn.
- Lift: among the ties, the share that sit inside a group, divided by the
  share of all pairs that sit inside a group. 1 is chance.
- AUC: the area under the ROC of the binary score "same group", which for a
  binary score is ``(TPR + TNR) / 2``.
- A person-level bootstrap 95% interval for lift and AUC: 2.5 and 97.5
  percentiles, linear, on the draws where the ratio is defined. Two copies of
  one person are not a pair. Distinct people ``i`` and ``j`` are counted
  ``count[i] × count[j]`` times.

Seeds are ``numpy`` ``Generator(seed)`` objects. Stability and the metric
interval each build their own generator from the same seed, so neither stream
depends on how far the other has been read. The defaults are 100 stability
draws and 500 metric draws.

A group value of ``None`` is unplaced. That person is counted in coverage and
left out of the pair universe. The outcome ties are the caller's. This module
does not decide which CV row restates a roster.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from typing import Any

import numpy as np

Person = Hashable
Group = Hashable
# ids in index order, and the draw (indices into that order, with replacement).
Refit = Callable[[Sequence[Person], np.ndarray], Mapping[Person, Group]]

# Flock evaluation sizes. A test may pass a smaller pair.
DEFAULT_STABILITY_DRAWS = 100
DEFAULT_METRIC_DRAWS = 500


def evaluate(
    assignment: Mapping[Person, Group | None],
    ties: Iterable[tuple[Person, Person]],
    *,
    seed: int = 0,
    n_boot: int = DEFAULT_STABILITY_DRAWS,
    n_metric_boot: int = DEFAULT_METRIC_DRAWS,
    refit: Refit | None = None,
) -> dict[str, Any]:
    """Coverage, bootstrap stability, lift, AUC, and the lift/AUC 95% intervals.

    ``seed`` is passed to two fresh ``Generator`` objects. The same ``seed``
    and the same inputs return the same dict.
    """
    if n_boot < 1 or n_metric_boot < 1:
        raise ValueError("bootstrap draws must be at least 1")
    placed = _placed(assignment)
    described = coverage(assignment)
    scores = pair_scores(placed, ties)
    # Separate streams: the person draws for stability do not consume the metric stream.
    stability = bootstrap_stability(placed, n_boot=n_boot, seed=seed, refit=refit)
    intervals = _metric_intervals(placed, ties, n_boot=n_metric_boot, seed=seed)
    return {
        "n_people": described["n_people"],
        "n_placed": described["n_placed"],
        "coverage": described,
        "stability": stability,
        "lift": None if scores is None else scores["lift"],
        "auc": None if scores is None else scores["auc"],
        "lift_ci95": intervals["lift_ci95"],
        "auc_ci95": intervals["auc_ci95"],
        "n_pairs": None if scores is None else scores["n_pairs"],
        "n_ties": None if scores is None else scores["n_ties"],
        "n_ties_same_group": None if scores is None else scores["n_ties_same_group"],
        "p_same": None if scores is None else scores["p_same"],
        "p_same_given_tie": None if scores is None else scores["p_same_given_tie"],
        "ci_n_draws": intervals["n_draws"],
        "ci_n_defined": intervals["n_defined"],
        "seed": seed,
    }


def coverage(assignment: Mapping[Person, Group | None]) -> dict[str, Any]:
    """Placement and size of ``assignment``. A ``None`` group is unplaced.

    ``placed_share`` is placed / everyone. ``largest_share`` and the singleton
    count use placed people only; an empty assignment has no largest share.
    """
    n = len(assignment)
    labels = [group for group in assignment.values() if group is not None]
    placed = len(labels)
    counts = Counter(labels)
    sizes = sorted(counts.values(), reverse=True)
    return {
        "n_people": n,
        "n_placed": placed,
        "placed_share": None if n == 0 else placed / n,
        "n_groups": len(counts),
        "largest_share": None if placed == 0 else sizes[0] / placed,
        "n_singletons": sum(1 for size in sizes if size == 1),
        "sizes_desc": sizes,
    }


def pair_scores(
    assignment: Mapping[Person, Group | None],
    ties: Iterable[tuple[Person, Person]],
) -> dict[str, Any] | None:
    """Lift and AUC of "same group" as a predictor of a tie.

    The universe is every unordered pair of placed people. A ``None`` group is
    unplaced and dropped. Returns ``None`` when the ratio is undefined: fewer
    than two people, no ties, no non-ties, or nobody shares a group.
    """
    ids, labels = _index(_placed(assignment))
    if labels.shape[0] < 2:
        return None
    tie_i, tie_j = _tie_index(ids, ties)
    counts = np.ones(labels.shape[0], dtype=np.int64)
    same = labels[tie_i] == labels[tie_j] if tie_i.size else np.zeros(0, dtype=bool)
    return _pair_metrics_counts(labels, counts, tie_i, tie_j, same)


def adjusted_rand(left: Mapping[Person, Group], right: Mapping[Person, Group]) -> float:
    """Adjusted Rand on the people present in both mappings.

    Label names do not matter: a renaming of the groups scores 1. One person,
    or none, scores 1. This is Hubert and Arabie's adjustment, the value
    ``sklearn.metrics.adjusted_rand_score`` returns, without importing it.
    """
    keys = sorted(set(left) & set(right), key=_sort_key)
    if len(keys) < 2:
        return 1.0
    return _adjusted_rand_arrays(
        np.array([left[key] for key in keys], dtype=object),
        np.array([right[key] for key in keys], dtype=object),
    )


def bootstrap_stability(
    assignment: Mapping[Person, Group | None],
    *,
    n_boot: int = DEFAULT_STABILITY_DRAWS,
    seed: int = 0,
    refit: Refit | None = None,
) -> dict[str, Any]:
    """Mean adjusted Rand of ``n_boot`` draws of people, against the full assignment.

    Without ``refit``, each draw keeps the labels, so the mean is 1 whenever
    at least one draw can be scored. ``refit(ids, draw)`` returns a new
    assignment for that draw. People the draw does not contain are omitted,
    which is why ``mean_share_omitted`` is reported beside the index.
    The 5% and 95% percentiles use the linear method.
    """
    if n_boot < 1:
        raise ValueError("bootstrap draws must be at least 1")
    ids, labels = _index(_placed(assignment))
    n = int(labels.shape[0])
    if n == 0:
        return _stability_block([], [], n_boot)
    rng = np.random.default_rng(seed)
    draws = rng.choice(n, size=(n_boot, n), replace=True)
    aris: list[float] = []
    omitted: list[float] = []
    full = {ids[index]: labels[index] for index in range(n)}
    for draw in draws:
        present = np.zeros(n, dtype=bool)
        present[draw] = True
        omitted.append(float(1.0 - present.mean()))
        if refit is None:
            rebuilt = full
        else:
            rebuilt = dict(refit(ids, draw))
            missing = [ids[index] for index in np.flatnonzero(present) if ids[index] not in rebuilt]
            if missing:
                raise RuntimeError("refit left a drawn person without a group")
        mask_ids = [ids[index] for index in np.flatnonzero(present)]
        left = {person: full[person] for person in mask_ids}
        right = {person: rebuilt[person] for person in mask_ids}
        aris.append(adjusted_rand(left, right))
    return _stability_block(aris, omitted, n_boot)


def _placed(assignment: Mapping[Person, Group | None]) -> dict[Person, Group]:
    """Drop unplaced people. A ``None`` group is not in the pair universe."""
    return {person: group for person, group in assignment.items() if group is not None}


def _sort_key(person: Person) -> tuple[str, str]:
    """Stable order. Strings sort as themselves; anything else sorts by ``repr`` after them."""
    if isinstance(person, str):
        return ("", person)
    return (type(person).__name__, repr(person))


def _index(assignment: Mapping[Person, Group]) -> tuple[list[Person], np.ndarray]:
    """People in a stable order, and their group labels in that same order."""
    ids = sorted(assignment, key=_sort_key)
    labels = np.array([assignment[person] for person in ids], dtype=object)
    return ids, labels


def _tie_index(
    ids: Sequence[Person],
    ties: Iterable[tuple[Person, Person]],
) -> tuple[np.ndarray, np.ndarray]:
    """Tie endpoints as indices into ``ids``. Pairs outside the assignment are dropped."""
    locate = {person: index for index, person in enumerate(ids)}
    left: list[int] = []
    right: list[int] = []
    seen: set[tuple[int, int]] = set()
    for pair in ties:
        if len(pair) != 2:
            raise ValueError("a tie is a pair of person ids")
        a = locate.get(pair[0])
        b = locate.get(pair[1])
        if a is None or b is None or a == b:
            continue
        if a > b:
            a, b = b, a
        if (a, b) in seen:
            continue
        seen.add((a, b))
        left.append(a)
        right.append(b)
    return np.asarray(left, dtype=np.int64), np.asarray(right, dtype=np.int64)


def _pair_metrics_counts(
    labels: np.ndarray,
    counts: np.ndarray,
    tie_i: np.ndarray,
    tie_j: np.ndarray,
    tie_same: np.ndarray,
) -> dict[str, Any] | None:
    """Lift and AUC when each person is present ``counts`` times.

    A bootstrap draw is a multiset. Two draws of the same person are not a
    pair. A pair of distinct people is counted ``counts[i] × counts[j]`` times.
    When every count is 1 this is the ordinary pair universe.
    """
    n_boot = int(counts.sum())
    if n_boot < 2:
        return None
    total_slots = n_boot * (n_boot - 1) // 2
    self_pairs = int(np.sum(counts * (counts - 1) // 2))
    total = total_slots - self_pairs
    if total <= 0:
        return None
    encoded = _encode(labels)
    n_g = np.rint(np.bincount(encoded, weights=counts.astype(np.float64))).astype(np.int64)
    same = int(np.sum(n_g * (n_g - 1) // 2)) - self_pairs
    if tie_i.size:
        weights = counts[tie_i].astype(np.int64) * counts[tie_j].astype(np.int64)
        n_ties = int(weights.sum())
        n_same_and_tie = int(weights[tie_same].sum()) if np.any(tie_same) else 0
    else:
        n_ties = 0
        n_same_and_tie = 0
    if n_ties == 0 or n_ties == total or same <= 0:
        return None
    p_same = same / total
    p_same_given_tie = n_same_and_tie / n_ties
    tpr = p_same_given_tie
    fpr = (same - n_same_and_tie) / (total - n_ties)
    return {
        "lift": p_same_given_tie / p_same,
        "auc": (tpr + (1.0 - fpr)) / 2.0,
        "n_people": int(np.count_nonzero(counts)),
        "n_pairs": total,
        "n_ties": n_ties,
        "n_ties_same_group": n_same_and_tie,
        "p_same": p_same,
        "p_same_given_tie": p_same_given_tie,
    }


def _encode(labels: np.ndarray) -> np.ndarray:
    """Integer codes for labels, stable within one array."""
    _values, inverse = np.unique(labels, return_inverse=True)
    return inverse.astype(np.int64)


def _metric_intervals(
    assignment: Mapping[Person, Group],
    ties: Iterable[tuple[Person, Person]],
    *,
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    """Person-level bootstrap 95% intervals for lift and AUC. Undefined draws are dropped."""
    ids, labels = _index(assignment)
    n = int(labels.shape[0])
    tie_i, tie_j = _tie_index(ids, ties)
    tie_same = labels[tie_i] == labels[tie_j] if tie_i.size else np.zeros(0, dtype=bool)
    if n < 2:
        return {"lift_ci95": None, "auc_ci95": None, "n_draws": n_boot, "n_defined": 0}
    draws = np.random.default_rng(seed).choice(n, size=(n_boot, n), replace=True)
    lifts: list[float] = []
    aucs: list[float] = []
    for draw in draws:
        counts = np.bincount(draw, minlength=n).astype(np.int64)
        stats = _pair_metrics_counts(labels, counts, tie_i, tie_j, tie_same)
        if stats is None:
            continue
        lifts.append(float(stats["lift"]))
        aucs.append(float(stats["auc"]))
    return {
        "lift_ci95": _percentile_ci(lifts),
        "auc_ci95": _percentile_ci(aucs),
        "n_draws": n_boot,
        "n_defined": len(lifts),
    }


def _percentile_ci(values: list[float]) -> list[float] | None:
    """95% percentile interval: 2.5 and 97.5, linear, on defined draws only."""
    if len(values) < 2:
        return None
    low, high = np.percentile(np.asarray(values, dtype=float), [2.5, 97.5], method="linear")
    return [float(low), float(high)]


def _stability_block(aris: list[float], omitted: list[float], n_boot: int) -> dict[str, Any]:
    """Mean adjusted Rand, the 5% and 95% percentiles, and the mean share omitted."""
    if not aris:
        return {
            "n_bootstrap": n_boot,
            "resample_unit": "people",
            "ari_mean": None,
            "ari_p05": None,
            "ari_p95": None,
            "mean_share_omitted": None,
        }
    array = np.asarray(aris, dtype=float)
    low, high = np.percentile(array, [5, 95], method="linear")
    return {
        "n_bootstrap": n_boot,
        "resample_unit": "people",
        "ari_mean": float(array.mean()),
        "ari_p05": float(low),
        "ari_p95": float(high),
        "mean_share_omitted": float(np.mean(np.asarray(omitted, dtype=float))),
    }


def _comb2(values: np.ndarray) -> np.ndarray:
    """Pairs among n items: n*(n-1)/2, for each count."""
    values = values.astype(np.int64)
    return values * (values - 1) // 2


def _adjusted_rand_arrays(left: np.ndarray, right: np.ndarray) -> float:
    """Hubert–Arabie adjusted Rand. 1 when the partitions agree, including one cluster."""
    n = int(left.shape[0])
    if n < 2:
        return 1.0
    left_i = _encode(left)
    right_i = _encode(right)
    n_left = int(left_i.max()) + 1
    n_right = int(right_i.max()) + 1
    table = np.zeros((n_left, n_right), dtype=np.int64)
    np.add.at(table, (left_i, right_i), 1)
    sum_ij = int(_comb2(table).sum())
    sum_a = int(_comb2(table.sum(axis=1)).sum())
    sum_b = int(_comb2(table.sum(axis=0)).sum())
    total = n * (n - 1) // 2
    # Agreement on every pair: the index is 1 even when the chance correction is 0/0.
    if sum_ij == sum_a == sum_b:
        return 1.0
    # Integer form of (index − expected) / (max − expected), times 2/2, so a
    # dyadic result such as −1/2 is exact instead of a binary rounding.
    numerator = 2 * (sum_ij * total - sum_a * sum_b)
    denominator = total * (sum_a + sum_b) - 2 * sum_a * sum_b
    if denominator == 0:
        return 1.0
    return numerator / denominator
