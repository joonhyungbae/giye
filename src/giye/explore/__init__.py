# SPDX-License-Identifier: AGPL-3.0-only
"""Stage 6: the entry-generation ring, and scores for a division of the field.

``build_rim_order`` writes the arc order the website draws. ``evaluate`` scores
an assignment the caller already has: coverage, bootstrap adjusted Rand, and
the lift and AUC of within-group ties. Feature embeddings and a data-chosen k
are not in this package. See ``docs/EXPLORE.md``.
"""

from giye.explore.evaluate import adjusted_rand, bootstrap_stability, coverage, evaluate, pair_scores
from giye.explore.rim import build_rim_order, write_rim_order

__all__ = [
    "adjusted_rand",
    "bootstrap_stability",
    "build_rim_order",
    "coverage",
    "evaluate",
    "pair_scores",
    "write_rim_order",
]
