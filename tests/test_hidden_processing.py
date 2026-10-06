# SPDX-License-Identifier: AGPL-3.0-only
"""A record hidden by request leaves the package's derived outputs.

Hiding is a request to stop processing, not only to leave the website. So
``giye normalize`` writes nothing of that person into ``data/processed/``, and
``giye explore`` ties and the audit sample do not read their rows. The demo
holds fictitious people only.
"""

from __future__ import annotations

from giye.audit.sample import _ledger_table
from giye.config import load
from giye.explore.ties import ties_for_config
from giye.ledger.ledger import Ledger
from giye.normalize.service import normalize
from giye.resolve.decide import hide_person
from tests.test_decisions import _copy


def test_hidden_person_leaves_processed_outputs_and_ties(tmp_path):
    dest = _copy(tmp_path)
    config = load(dest / "giye.toml")
    ledger = Ledger.open(config)
    artists = ledger.read("artists")
    acts = ledger.read("activities")
    ties_before = ties_for_config(config, "cv-listing") | ties_for_config(config)
    # The person in the most ties, so the test also shows the ties change.
    counts: dict[str, int] = {}
    for pair in ties_before:
        for lid in pair:
            counts[lid] = counts.get(lid, 0) + 1
    target = max(sorted(counts), key=lambda lid: counts[lid])
    row = next(item for item in artists if item["ledger_id"] == target)
    activity_ids = {item["activity_id"] for item in acts if item["ledger_id"] == target}
    assert activity_ids

    hide_person(ledger, row["gy_id"], reason="person asked to be hidden")
    normalize(config)

    for path in sorted(config.processed.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert target not in text, path.name
        leaked = [item for item in activity_ids if item in text]
        assert not leaked, path.name

    ties_after = ties_for_config(config, "cv-listing") | ties_for_config(config)
    assert all(target not in pair for pair in ties_after)
    assert all(item.get("ledger_id") != target for item in _ledger_table(config, "activities"))
