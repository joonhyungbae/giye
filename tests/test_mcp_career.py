# SPDX-License-Identifier: AGPL-3.0-only
"""Career measures, routing, and the rule that a request's entries are not kept."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.client import Client

from giye.career.build import build
from giye.career.rules import FAMILY_ORDER
from giye.config import load
from giye.explore.rim import generation_of
from giye.mcp import build_server
from giye.mcp.career import CareerEntry, assign_band, derive, validate_entries
from giye.mcp.routing import route_question
from tests.career_synth import synthetic_archive

_PROGRAMMES = [{"code": "EXAMPLE-RESIDENCY", "name_en": "Example Residency", "name_ko": "예시 레지던시"}]
_MEASURES = [
    "kind_share:group_exhibition",
    "kind_share:screening",
    "kind_share:performance",
    "kind_share:funding",
    "kind_share:education",
    "family_share:exhibition",
    "family_share:screening",
    "family_share:performance",
    "family_share:practice_other",
    "family_share:discourse",
    "family_share:support",
    "family_share:private",
    "country_share:home",
    "country_share:abroad",
    "country_share:unresolved",
    "region_share:unresolved",
    "arttech_share",
    "funding_share",
    "institutions_per_year",
]


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("mcp-career")
    config = synthetic_archive(root / "archive")
    out = root / "bundle"
    build(load(config), out)
    return out


def _derive(entries: list[CareerEntry], *, territory: str = "KR", programmes: list[dict[str, str]] | None = None):
    return derive(entries, territory=territory, programmes=programmes or [], measure_names=list(_MEASURES))


def test_private_years_do_not_start_the_career() -> None:
    derived = _derive(
        [
            CareerEntry(year=1990, kind="education"),
            CareerEntry(year=1991, kind="employment"),
            CareerEntry(year=2004, kind="service"),
            CareerEntry(year=2010, kind="award", country="KR"),
        ]
    )
    assert derived.first_year == 2010
    assert derived.generation == generation_of(2010)[0]
    only_private = _derive([CareerEntry(year=1990, kind="education")])
    assert only_private.first_year is None
    assert only_private.mix == "none"


def test_age_clamps_at_the_reference_and_names_generation() -> None:
    derived = _derive(
        [
            CareerEntry(year=1990, kind="group_exhibition", country="KR"),
            CareerEntry(year=2025, kind="group_exhibition", country="KR"),
        ]
    )
    assert derived.career_age == 30
    assert derived.beyond_reference
    assert "beyond_reference" in derived.notes
    assert derived.generation == "GEN-1990"
    at_anchor = _derive([CareerEntry(year=2000, kind="group_exhibition", country="KR")])
    assert at_anchor.generation == "GEN-2000"
    assert at_anchor.career_age == 0


def test_bands_follow_the_published_quantiles() -> None:
    five = {"q10": "0.10", "q25": "0.20", "q50": "0.40", "q75": "0.60", "q90": "0.80"}
    assert assign_band(0.09, five) == "below_q10"
    assert assign_band(0.10, five) == "q10_q25"
    assert assign_band(0.20, five) == "q25_q50"
    assert assign_band(0.40, five) == "q50_q75"
    assert assign_band(0.60, five) == "q75_q90"
    assert assign_band(0.80, five) == "above_q90"
    three = {"q10": "", "q25": "0.20", "q50": "0.40", "q75": "0.60", "q90": ""}
    assert assign_band(0.19, three) == "below_q25"
    assert assign_band(0.20, three) == "q25_q50"
    assert assign_band(0.40, three) == "q50_q75"
    assert assign_band(0.60, three) == "above_q75"
    one = {"q10": "", "q25": "", "q50": "0.40", "q75": "", "q90": "suppressed"}
    assert assign_band(0.39, one) == "below_median"
    assert assign_band(0.40, one) == "above_median"
    none = {"q10": "", "q25": "", "q50": "", "q75": "", "q90": ""}
    assert assign_band(0.40, none) == "no_reference"


def test_arttech_programme_venue_and_country() -> None:
    by_programme = _derive(
        [CareerEntry(year=2010, kind="group_exhibition", programme="EXAMPLE-RESIDENCY", country="KR")],
        programmes=_PROGRAMMES,
    )
    assert by_programme.values["arttech_share"] == 1.0
    by_name = _derive(
        [CareerEntry(year=2010, kind="group_exhibition", venue="  Example Residency  ", country="KR")],
        programmes=_PROGRAMMES,
    )
    assert by_name.values["arttech_share"] == 1.0
    plain = _derive(
        [CareerEntry(year=2010, kind="group_exhibition", venue="Other Hall", country="KR")],
        programmes=_PROGRAMMES,
    )
    assert plain.values["arttech_share"] == 0.0
    unresolved = _derive([CareerEntry(year=2010, kind="group_exhibition")])
    assert unresolved.values["country_share:unresolved"] == 1.0
    assert unresolved.values["country_share:home"] == 0.0
    assert unresolved.unresolved_countries == 1
    no_home = _derive([CareerEntry(year=2010, kind="group_exhibition", country="KR")], territory="")
    assert no_home.values["country_share:home"] == 0.0
    assert no_home.values["country_share:abroad"] == 1.0


def test_mix_tie_mixed_and_none() -> None:
    tie = _derive(
        [
            CareerEntry(year=2000, kind="group_exhibition", country="KR"),
            CareerEntry(year=2000, kind="screening", country="KR"),
        ]
    )
    assert tie.mix == FAMILY_ORDER[0]
    mixed = _derive(
        [
            CareerEntry(year=2000, kind=kind, country="KR")
            for kind in ("group_exhibition", "screening", "performance", "talk_workshop")
        ]
    )
    assert mixed.mix == "mixed"
    assert _derive([]).mix == "none"


def test_routing_rules_in_both_languages() -> None:
    cases = [
        ("What is the fee in the contract?", "Q1"),
        ("계약의 수수료는 얼마인가", "Q1"),
        ("Can I get MFA admission?", "Q2"),
        ("대학원 입학이 될까", "Q2"),
        ("What are my chances to succeed?", "Q3"),
        ("선발 가능성은 얼마나 되나", "Q3"),
        ("Which programmes took people like me?", "Q4"),
        ("나 같은 사람을 받은 프로그램은", "Q4"),
        ("Where do I stand?", "Q5"),
        ("나는 어디쯤인가", "Q5"),
        ("What did people do next?", "Q6"),
        ("그 다음 사람들은 무엇을 했나", "Q6"),
        ("How is the field changing?", "Q7"),
        ("분야는 어떻게 변화하는가", "Q7"),
        ("How do people move between programmes?", "Q8"),
        ("사람들은 프로그램 사이를 어떻게 이동하나", "Q8"),
        ("What is the weather today?", "Q9"),
        ("오늘 하늘은 맑다", "Q9"),
    ]
    for text, rule_id in cases:
        found = route_question(text)
        assert found.id == rule_id, text
    assert route_question("Which programmes took people like me?").status == "partially_covered"
    assert route_question("Which programmes took people like me?").tools == ("position", "programme_profile")
    assert "rephrase as one of" in (route_question("hello").ask_instead or "")
    message = validate_entries(
        [CareerEntry(year=2010, kind="not_a_kind")],
        kinds=["group_exhibition"],
        programmes=["EXAMPLE-RESIDENCY"],
    )
    assert message is not None
    assert "group_exhibition" in message


def test_entries_are_not_retained(bundle_dir: Path) -> None:
    marker = "ZZZ-VENUE-TOKEN"
    server = build_server(bundle_dir)
    before = _snapshot(bundle_dir)

    async def run() -> None:
        async with Client(server) as client:
            result = await client.call_tool(
                "position",
                {"entries": [{"year": 1901, "kind": "group_exhibition", "country": "KR", "venue": marker}]},
            )
            assert not result.is_error

    asyncio.run(run())
    assert _snapshot(bundle_dir) == before
    assert not _holds_marker(server, marker)


def _snapshot(path: Path) -> list[tuple[str, int, int]]:
    return [(item.name, item.stat().st_mtime_ns, item.stat().st_size) for item in sorted(path.iterdir())]


def _holds_marker(obj: object, marker: str, seen: set[int] | None = None) -> bool:
    """Walk attributes and closures. Globals are skipped so the check stays on this server."""
    if seen is None:
        seen = set()
    identity = id(obj)
    if identity in seen:
        return False
    seen.add(identity)
    if isinstance(obj, str):
        return marker in obj
    if isinstance(obj, CareerEntry):
        return True
    if isinstance(obj, (int, float, bool, type(None), bytes)):
        return False
    if isinstance(obj, type) or inspect.ismodule(obj):
        return False
    if inspect.isfunction(obj) or inspect.ismethod(obj):
        for cell in obj.__closure__ or ():
            try:
                content = cell.cell_contents
            except ValueError:
                continue
            if _holds_marker(content, marker, seen):
                return True
        return False
    if isinstance(obj, dict):
        return any(_holds_marker(key, marker, seen) or _holds_marker(value, marker, seen) for key, value in obj.items())
    if isinstance(obj, (list, tuple, set, frozenset)):
        return any(_holds_marker(item, marker, seen) for item in obj)
    data = getattr(obj, "__dict__", None)
    if isinstance(data, dict):
        return _holds_marker(data, marker, seen)
    return False
