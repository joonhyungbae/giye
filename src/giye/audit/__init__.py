# SPDX-License-Identifier: AGPL-3.0-only
"""Accuracy-audit sheets for any Giye ledger.

The paper's extraction, same-person, and institution checks are a sample, one
coder, and a Wilson interval. ``python -m giye.audit`` draws the sheet, scores
labels, and serves the judging page. The protocol is ``docs/EVALUATION.md``.
"""

from giye.audit.page import render_page
from giye.audit.sample import sample_sheet
from giye.audit.score import score_sheet
from giye.audit.serve import serve_sheet

__all__ = ["render_page", "sample_sheet", "score_sheet", "serve_sheet"]
