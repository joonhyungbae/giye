# SPDX-License-Identifier: AGPL-3.0-only
"""Quantiles, the Wilson interval, and a percentile bootstrap.

What: the numeric summaries the career tables publish. Quantiles use the
weighted empirical distribution. Intervals are a Wilson score for a share and
a percentile bootstrap for a mean.

Why: the bundle reports a distribution, not a point about one person. The
bootstrap is seeded so two builds of the same archive write the same interval.

How to run: imported by ``python -m giye.career build``. This module is not a
script.
"""

from __future__ import annotations

import math
import random

# 95% normal quantile. Wilson and the bootstrap both use this level.
Z_95 = 1.959963984540054
BOOTSTRAP_DRAWS = 500


def weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    """Smallest value whose cumulative weight reaches ``q`` times the total.

    Values are sorted ascending. Equal values stay in their original relative
    order, which does not change the returned value. A non-positive total
    weight falls back to the unweighted quantile so a file of zeros still
    yields a number. ``q`` is in (0, 1). This is the inverse of the weighted
    empirical distribution, not a interpolated Hyndman type.
    """
    pairs = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(weight for _value, weight in pairs)
    if total <= 0:
        total = float(len(pairs))
        pairs = [(value, 1.0) for value, _weight in pairs]
    threshold = q * total
    acc = 0.0
    chosen = pairs[-1][0]
    for value, weight in pairs:
        acc += weight
        if acc >= threshold:
            chosen = value
            break
    return chosen


def wilson(successes: int, n: int) -> tuple[float, float, float]:
    """Wilson score interval for ``successes`` out of ``n``. Returns ``(p, lo, hi)``.

    ``p`` is the unrounded proportion. The bounds are clamped to [0, 1]. An
    empty denominator returns zeros: the caller decides whether that cell is
    written.
    """
    if n <= 0:
        return 0.0, 0.0, 0.0
    p = successes / n
    z2 = Z_95 * Z_95
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    margin = Z_95 * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return p, max(0.0, centre - margin), min(1.0, centre + margin)


def _mean(values: list[float], weights: list[float] | None) -> float:
    if not values:
        return 0.0
    if weights is None:
        return sum(values) / len(values)
    total = sum(weights)
    if total <= 0:
        return sum(values) / len(values)
    return sum(value * weight for value, weight in zip(values, weights)) / total


def _percentile(samples: list[float], q: float) -> float:
    """Nearest-rank percentile. ``q`` is in (0, 1). Index is ``ceil(q n) - 1``."""
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def bootstrap_means(
    values: list[float],
    weights: list[float],
    rng: random.Random,
    *,
    draws: int = BOOTSTRAP_DRAWS,
) -> tuple[float, float, float, float, float, float]:
    """Percentile bootstrap of the unweighted and weighted means.

    Returns ``(mean, lo, hi, w_mean, w_lo, w_hi)``. Each draw resamples people
    with equal probability and recomputes both means, which is the IPW
    bootstrap (weights are a property of the drawn person, not the draw
    probability). The reported interval is the 2.5 and 97.5 percentiles
    expanded to include the point estimate: a skewed sample can put the
    percentile interval to one side of the mean, and a published interval that
    missed its own mean would be a false statement.
    """
    n = len(values)
    point = _mean(values, None)
    weighted = _mean(values, weights)
    means: list[float] = []
    w_means: list[float] = []
    for _draw in range(draws):
        picked = [rng.randrange(n) for _index in range(n)]
        sample = [values[index] for index in picked]
        w_sample = [weights[index] for index in picked]
        means.append(_mean(sample, None))
        w_means.append(_mean(sample, w_sample))
    lo = min(_percentile(means, 0.025), point)
    hi = max(_percentile(means, 0.975), point)
    w_lo = min(_percentile(w_means, 0.025), weighted)
    w_hi = max(_percentile(w_means, 0.975), weighted)
    return point, lo, hi, weighted, w_lo, w_hi
