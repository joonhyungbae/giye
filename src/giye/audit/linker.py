# SPDX-License-Identifier: AGPL-3.0-only
"""Compare the evidence rules with a probabilistic linker (Splink).

``python -m giye.audit splink --config giye.toml --out splink.csv`` scores
every pair of person records in the ledger with a Splink model and writes the
pairs at or above the threshold as a judging sheet (kind ``splink``). The
sheet then goes through ``page``, ``serve`` and ``score`` like the other
frames.

The model is fixed here so the comparison in ``docs/EVALUATION.md`` can be
rebuilt from any ledger:

- three comparisons: name similarity (exact Korean name, exact Latin name,
  then Jaro–Winkler at 0.9, Splink's default upper threshold), agreement on
  a programme edition, and agreement on a personal website;
- ``u`` from random pairs (seeded), ``m`` by expectation–maximisation in two
  sessions (blocked on the Korean name, then on the website), and the prior
  from exact-name blocking rules at an assumed recall of 0.7;
- no term-frequency adjustment (Splink's remedy for common names), which is
  a stated limit of the comparison;
- a pair is a match at a match probability of 0.9 or more.

The ledger records already merged by evidence are one record each, so every
pair this writes is one the rules did not merge. ``stratum`` is ``name_only``
when the pair shares no edition and no website, otherwise ``shared``.

Splink is an optional extra (``pip install -e ".[splink]"``). The record
builder, :func:`linker_records`, does not import it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from giye.audit.sheet import columns_for, write_sheet
from giye.config import Config
from giye.ledger.io import read_csv
from giye.ledger.schemas import TABLES

THRESHOLD = 0.9
# Recall assumed for the exact-name rules that estimate the prior. The rules
# miss cross-script and respelled pairs, so they are not taken as complete.
PRIOR_RECALL = 0.7
U_MAX_PAIRS = 10_000_000


def _table(config: Config, table: str) -> list[dict[str, str]]:
    filename, _columns = TABLES[table]
    return read_csv(config.ledger / filename)


def _site_key(url: str) -> str:
    """Host and path, lower case, without scheme, ``www.`` or a trailing slash."""
    text = (url or "").strip().lower()
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)
    text = re.sub(r"^www\.", "", text)
    return text.split("#", 1)[0].split("?", 1)[0].rstrip("/")


def _name(value: str) -> str | None:
    text = " ".join((value or "").split()).lower()
    return text or None


def linker_records(config: Config) -> list[dict]:
    """One record per person: names, editions and websites, in ledger order.

    Editions are the person's roster memberships plus the origin of every
    roster activity (an edition code; ``cv:`` origins are CV rows, not
    editions). Websites are links whose type is ``website``.
    """
    editions: dict[str, set[str]] = defaultdict(set)
    for row in _table(config, "frame_membership"):
        code = (row.get("frame_code") or "").strip()
        if code:
            editions[row.get("ledger_id") or ""].add(code)
    for row in _table(config, "activities"):
        origin = (row.get("origin") or "").strip()
        if origin and not origin.startswith("cv:"):
            editions[row.get("ledger_id") or ""].add(origin)
    sites: dict[str, set[str]] = defaultdict(set)
    for row in _table(config, "links"):
        if (row.get("link_type") or "").strip() != "website":
            continue
        key = _site_key(row.get("url") or "")
        if key:
            sites[row.get("ledger_id") or ""].add(key)
    records = []
    for person in _table(config, "artists"):
        ledger_id = person.get("ledger_id") or ""
        if not ledger_id:
            continue
        own_sites = sorted(sites.get(ledger_id, ()))
        records.append(
            {
                "unique_id": ledger_id,
                "gy_id": person.get("gy_id") or "",
                "name_ko": _name(person.get("name_ko") or ""),
                "name_en": _name(person.get("name_en") or ""),
                "editions": sorted(editions.get(ledger_id, ())) or None,
                "websites": own_sites or None,
                "first_website": own_sites[0] if own_sites else None,
            }
        )
    return records


def _settings():
    import splink.comparison_level_library as cll
    import splink.comparison_library as cl
    from splink import SettingsCreator, block_on

    name = cl.CustomComparison(
        output_column_name="name",
        comparison_levels=[
            cll.And(cll.NullLevel("name_ko"), cll.NullLevel("name_en")),
            cll.ExactMatchLevel("name_ko"),
            cll.ExactMatchLevel("name_en"),
            cll.JaroWinklerLevel("name_en", 0.9),
            cll.JaroWinklerLevel("name_ko", 0.9),
            cll.ElseLevel(),
        ],
    )
    return SettingsCreator(
        link_type="dedupe_only",
        comparisons=[
            name,
            cl.ArrayIntersectAtSizes("editions", [1]),
            cl.ArrayIntersectAtSizes("websites", [1]),
        ],
        blocking_rules_to_generate_predictions=[
            block_on("name_ko"),
            block_on("name_en"),
            block_on("first_website"),
        ],
        retain_intermediate_calculation_columns=False,
    )


def predict_pairs(records: list[dict], threshold: float = THRESHOLD, seed: int = 20261006) -> list[dict]:
    """Train the model on ``records`` and return the pairs at or above ``threshold``."""
    try:
        from splink import Linker, block_on
        from splink.backends.duckdb import DuckDBAPI
        from splink.internals.exceptions import SplinkException
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise RuntimeError('Splink is not installed; pip install -e ".[splink]"') from exc

    import pyarrow as pa

    db_api = DuckDBAPI()
    # An explicit schema: a column that is empty in every record (no Korean
    # names, say) would otherwise be typed as integers and break the string
    # comparisons.
    schema = pa.schema(
        [
            ("unique_id", pa.string()),
            ("gy_id", pa.string()),
            ("name_ko", pa.string()),
            ("name_en", pa.string()),
            ("editions", pa.list_(pa.string())),
            ("websites", pa.list_(pa.string())),
            ("first_website", pa.string()),
        ]
    )
    frame = db_api.register(pa.Table.from_pylist(records, schema=schema), dataset_display_name="ledger_people")
    linker = Linker(frame, _settings(), log_level=None)
    same_name = "l.name_ko = r.name_ko or l.name_en = r.name_en"
    try:
        linker.training.estimate_probability_two_random_records_match([same_name], recall=PRIOR_RECALL)
        linker.training.estimate_u_using_random_sampling(max_pairs=U_MAX_PAIRS, seed=seed)
        # One session blocked on the name trains editions and websites; one
        # blocked on the website trains the name.
        linker.training.estimate_parameters_using_expectation_maximisation(same_name)
        linker.training.estimate_parameters_using_expectation_maximisation(block_on("first_website"))
    except (ZeroDivisionError, SplinkException) as exc:
        raise RuntimeError(
            "the ledger has too few same-name or same-website pairs to train the Splink model "
            f"({type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''})"
        ) from exc
    predictions = linker.inference.predict(threshold_match_probability=threshold)
    return predictions.as_record_list()


def compare_sheet(config: Config, out: Path, threshold: float = THRESHOLD, seed: int = 20261006) -> list[dict[str, str]]:
    """Write the Splink matches as a ``splink`` judging sheet and return its rows."""
    records = linker_records(config)
    by_id = {record["unique_id"]: record for record in records}
    rosters: dict[str, set[str]] = defaultdict(set)
    for row in _table(config, "frame_membership"):
        rosters[row.get("ledger_id") or ""].add(row.get("frame_code") or "")
    rows = []
    for pair in predict_pairs(records, threshold, seed):
        left, right = sorted((str(pair["unique_id_l"]), str(pair["unique_id_r"])))
        a, b = by_id[left], by_id[right]
        editions = sorted(set(a["editions"] or ()) & set(b["editions"] or ()))
        websites = sorted(set(a["websites"] or ()) & set(b["websites"] or ()))
        rows.append(
            {
                "item_id": f"{left}/{right}",
                "kind": "splink",
                "stratum": "shared" if editions or websites else "name_only",
                "seed": str(seed),
                "label": "",
                "note": "",
                "match_probability": f"{float(pair['match_probability']):.6f}",
                "left_ledger_id": left,
                "left_gy_id": a["gy_id"],
                "left_name_ko": a["name_ko"] or "",
                "left_name_en": a["name_en"] or "",
                "left_rosters": " | ".join(sorted(rosters.get(left, ()))),
                "right_ledger_id": right,
                "right_gy_id": b["gy_id"],
                "right_name_ko": b["name_ko"] or "",
                "right_name_en": b["name_en"] or "",
                "right_rosters": " | ".join(sorted(rosters.get(right, ()))),
                "shared_editions": " | ".join(editions),
                "shared_websites": " | ".join(websites),
            }
        )
    rows.sort(key=lambda row: (row["stratum"], row["item_id"]))
    write_sheet(out, columns_for("splink"), rows)
    return rows
