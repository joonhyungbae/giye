# SPDX-License-Identifier: AGPL-3.0-only
"""Dataset release on fictitious rows. No real people and no network."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import warnings
from pathlib import Path
from urllib.parse import quote

from giye.config import ConfigWarning, load
from giye.export.release import (
    Cell,
    ReleaseError,
    apply_disclosure,
    build_release,
    name_tokens,
    open_name_leaks,
    quantile_allowed,
    restricted_leaks,
    url_embeds_name,
)
from giye.ledger.io import write_csv
from giye.ledger.schemas import (
    ACTIVITIES_FIELDS,
    ARTISTS_FIELDS,
    CV_SOURCES_FIELDS,
    LINKS_FIELDS,
    MEMBERSHIP_FIELDS,
    SCOPE_FIELDS,
    empty_row,
)

ROOT = Path(__file__).resolve().parents[1]
TITLE = "Untitled Example Work"
CV_URL = "https://cv.example.org/secret-page"
HIDDEN_EN = "Hidden Example"
HIDDEN_KO = "숨은예시사람"
OUTSIDE = "Outside Example"
SKIPPED = "Skipped Example"

FRAMES = """
version: 1
frames:
  - code: EXAMPLE-ALPHA
    name_en: Example Alpha
    name_ko: 예시 알파
    source_url: https://example.org/alpha
    years_covered: "2020-2021"
    operators:
      - name_ko: ""
        name_en: Example Institute
        role: organiser
        source_url: https://example.org/alpha
        snapshot_path: data/raw/example/page.html
        quote: Organised by Example Institute
    eligibility:
      decision: included
      f1_purpose: The page states the field.
      f2_cohort: Residents are selected by an open call and a jury.
      f3_territory: Held in the configured territory.
      f4_roster: The alumni page lists participants.
      f5_period: Editions in 2020 and 2021.
  - code: EXAMPLE-BETA
    name_en: Example Beta
    name_ko: 예시 베타
    source_url: https://example.org/beta
    years_covered: "2022"
    eligibility:
      decision: adjacent
      f1_purpose: The page does not state the field.
      f2_cohort: Fellows are selected by a jury.
      f3_territory: Held in the configured territory.
      f4_roster: The fellows page is public.
      f5_period: An edition in 2022.
      note: Adjacent because F1 is not met.
  - code: EXAMPLE-SKIP
    name_en: Example Skip
    name_ko: 예시 제외
    source_url: https://example.org/skip
    years_covered: "2019"
    eligibility:
      decision: excluded
      f1_purpose: The notice states the field.
      f2_cohort: No cohort is selected.
      f3_territory: Held in the configured territory.
      f4_roster: No participant list is published.
      f5_period: A single notice.
      note: Excluded because F2 and F4 fail.
"""


def _config(tmp_path: Path, release: str = "") -> Path:
    if not release:
        release = """
open_names = true
pseudonym = "per_release"
licence = "CC-BY-4.0"
commercial_use = false
k = 10
"""
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Synthetic media-art field"
id_prefix = "GY"
territory = "KR"
languages = ["en"]

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "frames.yml"

[release]
{release}
""",
        encoding="utf-8",
    )
    (tmp_path / "frames.yml").write_text(FRAMES, encoding="utf-8")
    return path


def _person(number: int, **extra: str) -> dict[str, str]:
    row = empty_row(
        ARTISTS_FIELDS,
        ledger_id=f"LED-{number:04d}",
        gy_id=f"GY-{number:06d}",
        name_ko=f"예시인물{number:02d}",
        name_en=f"Example Person {number:02d}",
        source_url="https://example.org/alpha",
        collected_at="2020-06-01",
    )
    row.update(extra)
    return row


def _write_ledger(tmp_path: Path) -> None:
    artists = [_person(number, reviewer_note="members=a|b" if number == 1 else "") for number in range(1, 16)]
    artists.append(
        _person(99, name_ko=HIDDEN_KO, name_en=HIDDEN_EN, status="HIDDEN_BY_REQUEST", gy_id="GY-000099")
    )
    artists.append(_person(88, name_ko="", name_en=OUTSIDE, gy_id="GY-000088"))
    artists.append(_person(77, name_ko="", name_en=SKIPPED, gy_id="GY-000077"))
    membership = []
    for number in range(1, 13):
        membership.append(
            empty_row(
                MEMBERSHIP_FIELDS,
                ledger_id=f"LED-{number:04d}",
                frame_code="EXAMPLE-ALPHA-2020",
                source_url="https://example.org/alpha/2020",
                collected_at="2020-06-01",
            )
        )
    for number in range(13, 16):
        membership.append(
            empty_row(
                MEMBERSHIP_FIELDS,
                ledger_id=f"LED-{number:04d}",
                frame_code="EXAMPLE-ALPHA-2021",
                source_url="https://example.org/alpha/2021",
                collected_at="2021-06-01",
            )
        )
    membership.append(
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id="LED-0099",
            frame_code="EXAMPLE-ALPHA-2020",
            source_url="https://example.org/alpha/2020",
            collected_at="2020-06-01",
        )
    )
    membership.append(
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id="LED-0088",
            frame_code="EXAMPLE-ALPHA-2020",
            source_url="https://example.org/alpha/2020",
            collected_at="2020-06-01",
        )
    )
    membership.append(
        empty_row(
            MEMBERSHIP_FIELDS,
            ledger_id="LED-0077",
            frame_code="EXAMPLE-SKIP-2019",
            source_url="https://example.org/skip",
            collected_at="2019-06-01",
        )
    )
    activities = []
    for number in range(1, 16):
        activities.append(
            empty_row(
                ACTIVITIES_FIELDS,
                activity_id=f"act-{number}",
                ledger_id=f"LED-{number:04d}",
                title=TITLE if number == 1 else f"Example Work {number}",
                year="2020",
                activity_type="group_exhibition",
                origin="cv:CV-DEMO",
                source_url=CV_URL,
                publishable="yes",
            )
        )
    activities.append(
        empty_row(
            ACTIVITIES_FIELDS,
            activity_id="act-roster",
            ledger_id="LED-0001",
            title="Roster Only Title",
            year="2020",
            activity_type="other",
            origin="EXAMPLE-ALPHA-2020",
            role="participant",
            publishable="yes",
        )
    )
    activities.append(
        empty_row(
            ACTIVITIES_FIELDS,
            activity_id="act-edu",
            ledger_id="LED-0001",
            title="Hidden Education Title",
            year="2010",
            activity_type="other",
            origin="cv:CV-DEMO",
            publishable="no",
        )
    )
    ledger = tmp_path / "data" / "ledger"
    write_csv(path=ledger / "artists.csv", fields=ARTISTS_FIELDS, rows=artists)
    write_csv(path=ledger / "frame_membership.csv", fields=MEMBERSHIP_FIELDS, rows=membership)
    write_csv(path=ledger / "activities.csv", fields=ACTIVITIES_FIELDS, rows=activities)
    write_csv(
        path=ledger / "links.csv",
        fields=LINKS_FIELDS,
        rows=[empty_row(LINKS_FIELDS, link_id="L1", ledger_id="LED-0001", url=CV_URL, link_type="cv")],
    )
    write_csv(
        path=ledger / "cv_sources.csv",
        fields=CV_SOURCES_FIELDS,
        rows=[empty_row(CV_SOURCES_FIELDS, source_id="CV-DEMO", ledger_id="LED-0001", url=CV_URL, active="true")],
    )
    write_csv(
        path=ledger / "scope.csv",
        fields=SCOPE_FIELDS,
        rows=[empty_row(SCOPE_FIELDS, ledger_id="LED-0088", scope="out", criterion="F1")],
    )
    processed = tmp_path / "data" / "processed"
    cv_rows = []
    for number in range(1, 16):
        cv_rows.append(
            {
                "ledger_id": f"LED-{number:04d}",
                "year": "2020",
                "origin": "cv:CV-DEMO",
                "activity_kind": "group_exhibition",
                "activity_channel": "activity",
                "venue_id": "VEN-BIG",
                "funder_id": "VEN-BIG",
                "venue_country": "KR",
                "venue_region": "서울",
                "venue_kind": "institution",
                "event_link": "EXAMPLE-ALPHA-2020",
                "publishable": "yes",
            }
        )
    cv_rows.append(
        {
            "ledger_id": "LED-0001",
            "year": "2020",
            "origin": "EXAMPLE-ALPHA-2020",
            "activity_kind": "other",
            "activity_channel": "activity",
            "venue_id": "VEN-SMALL",
            "funder_id": "VEN-SMALL",
            "venue_country": "US",
            "venue_region": "",
            "venue_kind": "institution",
            "event_link": "EXAMPLE-ALPHA-2020",
            "publishable": "yes",
        }
    )
    cv_rows.append(
        {
            "ledger_id": "LED-0001",
            "year": "2010",
            "origin": "cv:CV-DEMO",
            "activity_kind": "education",
            "activity_channel": "background",
            "venue_id": "VEN-BIG",
            "funder_id": "",
            "venue_country": "KR",
            "venue_region": "",
            "venue_kind": "institution",
            "event_link": "",
            "publishable": "no",
        }
    )
    # Activity-channel row the open CV tables and the restricted file both drop.
    cv_rows.append(
        {
            "ledger_id": "LED-0001",
            "year": "1999",
            "origin": "cv:CV-DEMO",
            "activity_kind": "award",
            "activity_channel": "activity",
            "venue_id": "VEN-BIG",
            "funder_id": "",
            "venue_country": "KR",
            "venue_region": "",
            "venue_kind": "institution",
            "event_link": "",
            "publishable": "no",
        }
    )
    write_csv(
        path=processed / "activities.csv",
        fields=list(cv_rows[0]),
        rows=cv_rows,
    )
    write_csv(
        path=processed / "venues.csv",
        fields=["venue_id", "name", "aliases", "kind", "city", "country", "kr_region", "n_rows", "n_artists", "rules"],
        rows=[
            {
                "venue_id": "VEN-BIG",
                "name": "Example Museum",
                "aliases": "EM",
                "kind": "institution",
                "city": "Seoul",
                "country": "KR",
                "kr_region": "서울",
                "n_rows": "20",
                "n_artists": "12",
                "rules": "V4",
            },
            {
                "venue_id": "VEN-SMALL",
                "name": "Example Room",
                "aliases": "",
                "kind": "institution",
                "city": "Busan",
                "country": "KR",
                "kr_region": "부산",
                "n_rows": "4",
                "n_artists": "4",
                "rules": "V1",
            },
        ],
    )
    write_csv(
        path=processed / "artist_attributes.csv",
        fields=["ledger_id", "field", "value", "rule", "evidence", "evidence_url"],
        rows=[
            {
                "ledger_id": f"LED-{number:04d}",
                "field": "record_depth",
                "value": "1",
                "rule": "P6 record depth",
                "evidence": "",
                "evidence_url": "",
            }
            for number in range(1, 16)
        ],
    )


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _text(root: Path) -> str:
    parts = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_fictitious_release_suppresses_small_cells_and_keeps_identifiers_out(tmp_path: Path):
    _write_ledger(tmp_path)
    result = build_release(load(_config(tmp_path)), "v1", rng=random.Random(1))
    output = result.output
    roster = _rows(output / "open" / "roster_facts.csv")
    assert len(roster) == 15
    assert {row["programme"] for row in roster} == {"EXAMPLE-ALPHA"}
    assert sum(row["year"] == "2020" for row in roster) == 12
    assert HIDDEN_EN not in _text(output) and HIDDEN_KO not in _text(output)
    assert OUTSIDE not in _text(output) and SKIPPED not in _text(output)
    assert "GY-000099" not in _text(output) and "GY-000088" not in _text(output)
    assert CV_URL not in _text(output)
    assert TITLE not in _text(output)
    assert "cv:CV-DEMO" not in _text(output)
    assert "data/raw/example/page.html" not in (output / "open" / "programmes.csv").read_text(encoding="utf-8")

    programmes = _rows(output / "open" / "programmes.csv")
    assert {row["code"] for row in programmes} == {"EXAMPLE-ALPHA", "EXAMPLE-BETA"}
    alpha = next(row for row in programmes if row["code"] == "EXAMPLE-ALPHA")
    assert alpha["eligibility"] == "included"
    assert "open call" in alpha["access_mode"]
    assert "Example Institute" in alpha["operators"]
    assert "organiser" in alpha["operators"]

    edition = _rows(output / "open" / "edition_year.csv")
    assert edition
    assert all(row["measure"] == "first_timers" for row in edition)
    assert all(row["measure"] != "edition_size" for row in edition)
    first_2020 = next(row for row in edition if row["year"] == "2020")
    assert first_2020["n_people"] == "10" and first_2020["suppressed"] == "no"
    assert first_2020["share"] == "80"
    assert all(row["year"] != "2021" for row in edition)
    assert all(row["suppressed"] == "no" for row in edition)
    assert all(row["n_people"] != "suppressed" and row["share"] != "suppressed" for row in edition)

    institutions = _rows(output / "open" / "institutions.csv")
    assert [row["venue_id"] for row in institutions] == ["VEN-BIG"]
    assert institutions[0]["n_artists"] == "10"

    people = _rows(output / "restricted" / "people.csv")
    activities = _rows(output / "restricted" / "activities.csv")
    assert list(people[0]) == [
        "pseudonym",
        "entry_year",
        "entry_generation",
        "record_depth",
        "programmes",
        "team",
    ]
    assert "cv_weight" not in _text(output)
    assert len(people) == 15
    assert all(row["pseudonym"].startswith("ps") for row in people)
    assert {row["team"] for row in people} == {"0", "1"}
    assert all(row["activity_channel"] == "activity" for row in activities)
    assert "education" not in {row["activity_kind"] for row in activities}
    assert "VEN-SMALL" not in {row["venue_id"] for row in activities}
    assert "VEN-SMALL" not in {row["funder_id"] for row in activities}
    assert "VEN-BIG" in {row["venue_id"] for row in activities}
    assert {row["origin_type"] for row in activities} == {"cv", "roster"}
    assert all(row["year"] != "1999" for row in activities)
    assert "award" not in {row["activity_kind"] for row in activities}
    names = _rows(output / "restricted" / "roster_names.csv")
    assert list(names[0]) == ["gy_id", "name_ko", "name_en", "aliases"]
    assert len(names) == 15
    assert not (output / "open" / "roster_names.csv").exists()
    assert not (output / "docs").exists()
    assert not result.key_path.is_relative_to(output)
    key = _rows(result.key_path)
    assert {row["pseudonym"] for row in key} == {row["pseudonym"] for row in people}
    assert "GY-000001" in {row["gy_id"] for row in key}

    manifest = json.loads((output / "open" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert (output / "restricted" / "MANIFEST.json").read_bytes() == (output / "open" / "MANIFEST.json").read_bytes()
    digest = hashlib.sha256((output / "open" / "roster_facts.csv").read_bytes()).hexdigest()
    assert manifest["files"]["open/roster_facts.csv"]["sha256"] == digest
    assert manifest["files"]["restricted/roster_names.csv"]["rows"] == 15
    assert "open/MANIFEST.json" not in manifest["files"]
    assert manifest["k"] == 10
    assert "D1" in manifest["rule_ids"]
    dua = (output / "open" / "DUA.md").read_text(encoding="utf-8")
    assert "TODO" not in dua
    assert "CC BY 4.0" in dua
    assert "Commercial use is not allowed." in dua
    assert "exempt" not in dua.lower()
    license_text = (output / "open" / "LICENSE").read_text(encoding="utf-8")
    assert "https://creativecommons.org/licenses/by/4.0/legalcode" in license_text
    assert "by Archive Example." in license_text
    citation = (output / "open" / "CITATION.cff").read_text(encoding="utf-8")
    assert "family-names: Example" in citation and "given-names: Archive" in citation
    assert "orcid:" not in citation
    stats = json.loads((output / "open" / "stats.json").read_text(encoding="utf-8"))
    assert stats["people"] == 15
    assert "ipw" not in stats
    assert stats["programmes"] == 2
    open_zenodo = json.loads((output / "zenodo" / "open.zenodo.json").read_text(encoding="utf-8"))
    restricted_zenodo = json.loads((output / "zenodo" / "restricted.zenodo.json").read_text(encoding="utf-8"))
    assert open_zenodo["upload_type"] == "dataset" and open_zenodo["license"] == "cc-by-4.0"
    assert open_zenodo["access_right"] == "open"
    assert restricted_zenodo["access_right"] == "restricted"
    assert "Commercial use is not allowed" in restricted_zenodo["access_conditions"]
    assert open_zenodo["creators"][0]["orcid"] == "TODO"


def test_open_names_false_drops_the_name_columns(tmp_path: Path):
    _write_ledger(tmp_path)
    path = _config(
        tmp_path,
        """
open_names = false
pseudonym = "per_release"
licence = "CC-BY-4.0"
commercial_use = false
k = 10
""",
    )
    result = build_release(load(path), "nonames", rng=random.Random(1))
    header = (result.output / "open" / "roster_facts.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "name_ko" not in header and "name_en" not in header
    assert "예시인물" not in _text(result.output / "open")
    assert "Example Person" not in _text(result.output / "open")


def test_stable_hmac_repeats_and_per_release_follows_the_rng(tmp_path: Path):
    _write_ledger(tmp_path)
    secret = tmp_path / "pseudonym.key"
    secret.write_text("test-secret\n", encoding="utf-8")
    config = load(
        _config(
            tmp_path,
            f"""
open_names = true
pseudonym = "stable_hmac"
pseudonym_secret = "{secret.as_posix()}"
licence = "CC-BY-4.0"
commercial_use = false
k = 10
""",
        )
    )
    first = build_release(config, "h1")
    second = build_release(config, "h2")
    assert _rows(first.key_path) == _rows(second.key_path)
    per = load(_config(tmp_path))
    again = build_release(per, "p1", rng=random.Random(1))
    same = build_release(per, "p2", rng=random.Random(1))
    other = build_release(per, "p3", rng=random.Random(2))
    assert _rows(again.key_path) == _rows(same.key_path)
    assert _rows(again.key_path) != _rows(other.key_path)


def test_quantile_allowed_thresholds():
    assert quantile_allowed(100, 0.9, 10)
    assert quantile_allowed(100, 0.1, 10)
    assert quantile_allowed(20, 0.5, 10)
    assert quantile_allowed(99, 0.9, 10) is False
    assert quantile_allowed(19, 0.5, 10) is False


def test_quantile_rule_and_secondary_suppression():
    assert quantile_allowed(19, 0.5, 10) is False
    assert quantile_allowed(20, 0.5, 10) is True
    assert quantile_allowed(39, 0.25, 10) is False
    assert quantile_allowed(40, 0.25, 10) is True
    assert quantile_allowed(99, 0.1, 10) is False
    assert quantile_allowed(100, 0.1, 10) is True
    assert quantile_allowed(1000, 0.0, 10) is False
    cells = {("small",): Cell(3), ("large",): Cell(12)}
    apply_disclosure(cells, [], [], [( [("small",), ("large",)], "people")], 10)
    assert cells[("small",)].status == "suppress"
    assert cells[("large",)].status == "suppress"
    only = {("only",): Cell(3)}
    apply_disclosure(only, [], [], [([("only",)], "people")], 10)
    assert only[("only",)].status == "suppress"


def test_restricted_scan_rejects_a_copied_title():
    secrets = {
        "names": {"Example Person 01"},
        "gy_ids": {"GY-000001"},
        "ledger_ids": {"LED-0001"},
        "urls": {"https://cv.example.org/secret-page"},
        "titles": {"Untitled Example Work"},
        "cv_origins": set(),
        "cv_urls": set(),
    }
    reasons = restricted_leaks(
        {"activities.csv": (["pseudonym", "year"], [{"pseudonym": "ps" + "ab" * 16, "year": "Untitled Example Work"}])},
        secrets,
        frame_codes=set(),
        venue_ids=set(),
        regions=set(),
    )
    assert any("title" in reason for reason in reasons)


def test_production_config_reads_the_release_table():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConfigWarning)
        config = load(ROOT / "deploy" / "giye.production.toml")
    assert not caught
    assert config.release.open_names is False
    assert config.release.pseudonym == "per_release"
    assert config.release.licence == "CC-BY-4.0"
    assert config.release.commercial_use is False
    assert config.release.k == 10
    assert config.release.ipw_path == "work/record_depth/ipw.csv"


def _append(path: Path, extra: list[dict[str, str]]) -> None:
    rows = _rows(path)
    write_csv(path=path, fields=list(rows[0]), rows=[*rows, *extra])


def test_staff_adjusted_cells_and_publishable_are_symmetric(tmp_path: Path):
    """Under-k roster cells are absent. A staff-adjusted count at or above k is rounded.

    A CV kind × year cell under k stays suppressed. publishable no is absent
    from the open CV table and from restricted activities. A missing
    publishable is kept in both.
    """
    _write_ledger(tmp_path)
    config_path = _config(
        tmp_path,
        """
open_names = false
pseudonym = "per_release"
licence = "CC-BY-4.0"
commercial_use = false
k = 10
""",
    )
    frames = tmp_path / "frames.yml"
    frames.write_text(
        frames.read_text(encoding="utf-8").replace(
            "    source_url: https://example.org/beta\n",
            "    source_url: ''\n    collector: transcribed\n",
            1,
        ),
        encoding="utf-8",
    )
    _append(tmp_path / "data" / "ledger" / "artists.csv", [_person(16)])
    _append(
        tmp_path / "data" / "ledger" / "frame_membership.csv",
        [
            empty_row(
                MEMBERSHIP_FIELDS,
                ledger_id="LED-0016",
                frame_code="EXAMPLE-ALPHA-2010",
                source_url="https://example.org/alpha/2010",
                collected_at="2010-06-01",
            )
        ],
    )
    _append(
        tmp_path / "data" / "ledger" / "activities.csv",
        [
            empty_row(
                ACTIVITIES_FIELDS,
                activity_id="act-staff",
                ledger_id="LED-0012",
                title="Staff Role Title",
                year="2020",
                activity_type="other",
                origin="EXAMPLE-ALPHA-2020",
                role="curator",
                publishable="yes",
            )
        ],
    )
    _append(
        tmp_path / "data" / "processed" / "artist_attributes.csv",
        [
            {
                "ledger_id": "LED-0016",
                "field": "record_depth",
                "value": "1",
                "rule": "P6 record depth",
                "evidence": "",
                "evidence_url": "",
            }
        ],
    )
    _append(
        tmp_path / "data" / "processed" / "activities.csv",
        [
            {
                "ledger_id": "LED-0001",
                "year": "2015",
                "origin": "cv:CV-DEMO",
                "activity_kind": "performance",
                "activity_channel": "activity",
                "venue_id": "",
                "funder_id": "",
                "venue_country": "KR",
                "venue_region": "",
                "venue_kind": "",
                "event_link": "",
                "publishable": "yes",
            },
            {
                "ledger_id": "LED-0002",
                "year": "2012",
                "origin": "cv:CV-DEMO",
                "activity_kind": "residency",
                "activity_channel": "activity",
                "venue_id": "",
                "funder_id": "",
                "venue_country": "KR",
                "venue_region": "",
                "venue_kind": "",
                "event_link": "",
                "publishable": "",
            },
        ],
    )
    write_csv(
        path=tmp_path / "data" / "work" / "declared_roster_sizes.csv",
        fields=["frame", "edition", "size", "unit", "source_url", "quote", "snapshot_path", "status", "reason"],
        rows=[
            {
                "frame": "EXAMPLE-BETA",
                "edition": "2018",
                "size": "1",
                "unit": "people",
                "source_url": "https://example.org/beta-rejected",
                "quote": "Rejected quote",
                "snapshot_path": "data/raw/rejected.html",
                "status": "rejected",
                "reason": "",
            },
            {
                "frame": "EXAMPLE-BETA",
                "edition": "2022",
                "size": "9",
                "unit": "people",
                "source_url": "https://example.org/beta-late",
                "quote": "Late quote names nobody",
                "snapshot_path": "data/raw/late.html",
                "status": "accepted",
                "reason": "",
            },
            {
                "frame": "EXAMPLE-BETA",
                "edition": "2019",
                "size": "4",
                "unit": "people",
                "source_url": "https://example.org/beta-early",
                "quote": "Early quote",
                "snapshot_path": "data/raw/early.html",
                "status": "accepted",
                "reason": "",
            },
        ],
    )
    result = build_release(load(config_path), "staff1", rng=random.Random(1))
    output = result.output
    edition = _rows(output / "open" / "edition_year.csv")
    assert all(row["measure"] != "edition_size" for row in edition)
    assert all(row["year"] not in {"2010", "2021"} for row in edition)
    first_2020 = next(row for row in edition if row["year"] == "2020")
    assert first_2020["n_people"] == "10"
    assert first_2020["share"] == "73"
    assert first_2020["suppressed"] == "no"
    generations = _rows(output / "open" / "entry_generation.csv")
    assert all(row["generation"] != "GEN-2010" for row in generations)
    gen_2020 = next(row for row in generations if row["generation"] == "GEN-2020")
    assert gen_2020["n_people"] == "15" and gen_2020["suppressed"] == "no"
    assert gen_2020["share"] == ">=90"
    assert all("suppressed" not in (row["n_people"], row["share"]) for row in [*edition, *generations])
    kinds = _rows(output / "open" / "activity_kind_year.csv")
    performance = next(row for row in kinds if row["activity_kind"] == "performance")
    assert performance["year"] == "2015"
    assert performance["n_people"] == "suppressed" and performance["suppressed"] == "yes"
    residency = next(row for row in kinds if row["activity_kind"] == "residency")
    assert residency["suppressed"] == "yes"
    assert all(row["activity_kind"] != "award" for row in kinds)
    activities = _rows(output / "restricted" / "activities.csv")
    assert all(row["year"] != "1999" for row in activities)
    assert any(row["year"] == "2012" and row["activity_kind"] == "residency" for row in activities)
    names = _rows(output / "restricted" / "roster_names.csv")
    assert list(names[0]) == ["gy_id", "name_ko", "name_en", "aliases"]
    assert len(names) == 16
    assert not (output / "open" / "roster_names.csv").exists()
    header = (output / "open" / "roster_facts.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "name_ko" not in header
    assert "Example Person" not in _text(output / "open")
    beta = next(row for row in _rows(output / "open" / "programmes.csv") if row["code"] == "EXAMPLE-BETA")
    assert beta["source_url"] == "https://example.org/beta-early"
    assert "data/raw/" not in beta["source_url"]
    assert "Early quote" not in (output / "open" / "programmes.csv").read_text(encoding="utf-8")
    alpha = next(row for row in _rows(output / "open" / "programmes.csv") if row["code"] == "EXAMPLE-ALPHA")
    assert alpha["source_url"] == "https://example.org/alpha"
    stats = json.loads((output / "open" / "stats.json").read_text(encoding="utf-8"))
    assert stats["years_covered"] == {"min": 2010, "max": 2021}
    assert stats["declared_roster_sizes"]
    assert all("collected" not in row for row in stats["declared_roster_sizes"])
    assert {row["edition"] for row in stats["declared_roster_sizes"]} == {"2019", "2022"}


def test_name_tokens_keep_hangul_syllables_and_reversed_latin_parts():
    assert name_tokens("김하늘") == {"김하늘"}
    assert name_tokens("김") == set()
    assert name_tokens("Ann") == set()
    assert name_tokens("Anna") == {"anna"}
    assert name_tokens("Haneul Kim") == {"haneulkim", "kimhaneul"}
    assert name_tokens("TIME+") == {"time"}
    assert url_embeds_name("https://example.org/artists/kim-haneul", name_tokens("Haneul Kim"))
    encoded = "https://example.org/artists/" + quote("김하늘")
    assert url_embeds_name(encoded, name_tokens("김하늘"))
    assert not url_embeds_name("https://example.org/ann", name_tokens("Ann"))
    # The smashed programme slug contains the four-letter name. REL-URL withholds
    # that person's own row. The open-file check does not, because the label is
    # the whole slug, not the name.
    assert url_embeds_name("https://example.org/apedock/", name_tokens("DOCK"))
    assert open_name_leaks(
        {"roster_facts.csv": (["source_url"], [{"source_url": "https://example.org/apedock/"}])},
        {},
        name_tokens("DOCK"),
    ) == []
    leaks = open_name_leaks(
        {},
        {"LICENSE": "Giye open census tables, version v1, by Example Author.\n", "NOTE.md": "collection time"},
        name_tokens("Example Author") | name_tokens("TIME+"),
        creator_given="Example",
        creator_family="Author",
    )
    assert leaks == []
    prose = open_name_leaks({}, {"NOTE.md": "The artist is Haneul Kim."}, name_tokens("Haneul Kim"))
    assert prose == ["open/NOTE.md contains a published name"]
    dock = open_name_leaks(
        {"roster_facts.csv": (["source_url"], [{"source_url": "https://example.org/dock"}])},
        {},
        name_tokens("DOCK"),
    )
    assert dock == ["open/roster_facts.csv column source_url contains a published name"]
    programme = open_name_leaks(
        {
            "programmes.csv": (
                ["name_en", "source_url"],
                [{"name_en": "Haneul Kim", "source_url": "https://example.org/alpha"}],
            )
        },
        {},
        name_tokens("Haneul Kim"),
    )
    assert programme == []


def test_rel_url_withholds_a_personal_source_and_keeps_the_programme_page(tmp_path: Path):
    """A source that embeds the person's name leaves the open roster.

    The open cell keeps the frame page and source_withheld = yes. The original
    URL is only in the restricted file. A three-letter Latin name is not a
    token. A programme name field may equal a person's name. The word "date"
    in the datasheet is not that person's name token.
    """
    _write_ledger(tmp_path)
    artists_path = tmp_path / "data" / "ledger" / "artists.csv"
    artists = _rows(artists_path)
    by_id = {row["ledger_id"]: row for row in artists}
    by_id["LED-0003"]["name_ko"] = "김"
    by_id["LED-0003"]["name_en"] = "Ann"
    by_id["LED-0004"]["name_en"] = "Date"
    by_id["LED-0005"]["name_en"] = "Haneul Kim"
    by_id["LED-0008"]["name_ko"] = "예시 알파"
    by_id["LED-0008"]["name_en"] = "Quota Holder"
    write_csv(path=artists_path, fields=ARTISTS_FIELDS, rows=artists)
    membership_path = tmp_path / "data" / "ledger" / "frame_membership.csv"
    membership = _rows(membership_path)
    personal = {
        "LED-0001": "https://example.org/artists/" + quote("예시인물01"),
        "LED-0002": "https://example.org/roster/02-person-example",
        "LED-0003": "https://example.org/ann",
        "LED-0004": "https://example.org/people/date",
        "LED-0005": "https://haneulkim.example.org/shared",
        "LED-0006": "https://haneulkim.example.org/shared",
    }
    for row in membership:
        if row["ledger_id"] in personal and row["frame_code"] == "EXAMPLE-ALPHA-2020":
            row["source_url"] = personal[row["ledger_id"]]
    write_csv(path=membership_path, fields=MEMBERSHIP_FIELDS, rows=membership)
    result = build_release(load(_config(tmp_path, """
open_names = false
pseudonym = "per_release"
licence = "CC-BY-4.0"
commercial_use = false
k = 10
""")), "relurl", rng=random.Random(1))
    output = result.output
    roster = {row["gy_id"]: row for row in _rows(output / "open" / "roster_facts.csv")}
    assert roster["GY-000001"]["source_withheld"] == "yes"
    assert roster["GY-000001"]["source_url"] == "https://example.org/alpha"
    assert roster["GY-000002"]["source_withheld"] == "yes"
    assert roster["GY-000002"]["source_url"] == "https://example.org/alpha"
    assert roster["GY-000003"]["source_withheld"] == "no"
    assert roster["GY-000003"]["source_url"] == "https://example.org/ann"
    assert roster["GY-000004"]["source_withheld"] == "yes"
    assert roster["GY-000005"]["source_withheld"] == "yes"
    assert roster["GY-000006"]["source_withheld"] == "yes"
    assert roster["GY-000008"]["source_withheld"] == "no"
    assert all(row["source_withheld"] in {"yes", "no"} for row in roster.values())
    withheld = _rows(output / "restricted" / "roster_sources_withheld.csv")
    assert [row["gy_id"] for row in withheld] == ["GY-000001", "GY-000002", "GY-000004", "GY-000005", "GY-000006"]
    by_gy = {row["gy_id"]: row["source_url"] for row in withheld}
    assert quote("예시인물01") in by_gy["GY-000001"] or "예시인물01" in by_gy["GY-000001"]
    assert by_gy["GY-000002"] == "https://example.org/roster/02-person-example"
    assert by_gy["GY-000005"] == "https://haneulkim.example.org/shared"
    assert by_gy["GY-000006"] == "https://haneulkim.example.org/shared"
    open_text = _text(output / "open")
    assert "예시인물01" not in open_text
    assert "haneulkim" not in open_text.lower()
    assert "02-person-example" not in open_text
    readme = (output / "restricted" / "README.md").read_text(encoding="utf-8")
    codebook = (output / "open" / "CODEBOOK.md").read_text(encoding="utf-8")
    assert "roster_sources_withheld.csv" in readme
    assert "source_withheld" in codebook
    assert "roster_sources_withheld.csv" in codebook
    manifest = json.loads((output / "open" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["files"]["restricted/roster_sources_withheld.csv"]["rows"] == 5
    assert "REL-URL" in manifest["rule_ids"]


def test_open_name_check_rejects_a_name_in_prose(tmp_path: Path):
    _write_ledger(tmp_path)
    config_path = _config(tmp_path)
    frames = tmp_path / "frames.yml"
    frames.write_text(
        frames.read_text(encoding="utf-8").replace(
            "Residents are selected by an open call and a jury.",
            "Example Person 01 is selected by an open call and a jury.",
        ),
        encoding="utf-8",
    )
    try:
        build_release(load(config_path), "names-in-prose", rng=random.Random(1))
    except ReleaseError as exc:
        assert "published name" in str(exc)
    else:
        raise AssertionError("a published name in the programme sentence was accepted")


def test_ipw_weight_joins_through_the_key_and_stays_off_the_open_record(tmp_path: Path):
    """cv_weight is filled only for a person with a CV row.

    The weight file is joined on the private ledger id. That id is not
    written. A person the file names, but who has no CV row, stays empty.
    The open record gets the count and the path, not the weights.
    """
    _write_ledger(tmp_path)
    processed = tmp_path / "data" / "processed" / "activities.csv"
    activities = _rows(processed)
    for row in activities:
        if row["ledger_id"] == "LED-0015" and row["origin"].startswith("cv:"):
            row["origin"] = "EXAMPLE-ALPHA-2021"
    write_csv(path=processed, fields=list(activities[0]), rows=activities)
    ipw_rel = "work/record_depth/ipw.csv"
    write_csv(
        path=tmp_path / "data" / ipw_rel,
        fields=["ledger_id", "weight", "p_hat", "p_clipped"],
        rows=[
            {"ledger_id": "LED-0001", "weight": "1.23451", "p_hat": "0.20", "p_clipped": "0.20"},
            {"ledger_id": "LED-0002", "weight": "2.34562", "p_hat": "0.10", "p_clipped": "0.10"},
            {"ledger_id": "LED-0015", "weight": "8.88881", "p_hat": "0.30", "p_clipped": "0.30"},
            {"ledger_id": "LED-0099", "weight": "9.87653", "p_hat": "0.40", "p_clipped": "0.40"},
        ],
    )
    result = build_release(
        load(
            _config(
                tmp_path,
                f"""
open_names = false
pseudonym = "per_release"
licence = "CC-BY-4.0"
commercial_use = false
k = 10
ipw_path = "{ipw_rel}"
ipw_method = "Example model of having CV rows on programme and cohort; 3 covariates stay unbalanced."
""",
            )
        ),
        "ipw1",
        rng=random.Random(1),
    )
    output = result.output
    people = _rows(output / "restricted" / "people.csv")
    assert list(people[0]) == [
        "pseudonym",
        "entry_year",
        "entry_generation",
        "record_depth",
        "programmes",
        "team",
        "cv_weight",
    ]
    key = {row["ledger_id"]: row["pseudonym"] for row in _rows(result.key_path)}
    by_pseudo = {row["pseudonym"]: row for row in people}
    assert by_pseudo[key["LED-0001"]]["cv_weight"] == "1.23451"
    assert by_pseudo[key["LED-0002"]]["cv_weight"] == "2.34562"
    assert by_pseudo[key["LED-0015"]]["cv_weight"] == ""
    assert by_pseudo[key["LED-0003"]]["cv_weight"] == ""
    assert "LED-0099" not in key
    assert sum(bool(row["cv_weight"]) for row in people) == 2
    release_text = _text(output)
    assert "ledger_id" not in release_text
    assert "LED-" not in release_text
    assert "p_hat" not in release_text
    assert "9.87653" not in release_text
    assert "8.88881" not in release_text
    open_text = _text(output / "open")
    assert "1.23451" not in open_text
    assert "2.34562" not in open_text
    for name in (
        "roster_facts.csv",
        "programmes.csv",
        "edition_year.csv",
        "entry_generation.csv",
        "activity_kind_year.csv",
        "venue_country_period.csv",
        "record_depth.csv",
        "institutions.csv",
    ):
        header = (output / "open" / name).read_text(encoding="utf-8").splitlines()[0]
        assert "cv_weight" not in header
    activity_header = (output / "restricted" / "activities.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "cv_weight" not in activity_header
    stats = json.loads((output / "open" / "stats.json").read_text(encoding="utf-8"))
    assert stats["ipw"] == {"people_weighted": 2, "source": ipw_rel}
    assert result.stats["ipw"] == stats["ipw"]
    # The method text is the instance's own ([release] ipw_method), copied verbatim.
    phrase = "Example model of having CV rows on programme and cohort; 3 covariates stay unbalanced."
    for codebook_path in (output / "open" / "CODEBOOK.md", output / "restricted" / "CODEBOOK.md"):
        codebook = codebook_path.read_text(encoding="utf-8")
        assert "`cv_weight`" in codebook
        assert phrase in codebook
    readme = (output / "restricted" / "README.md").read_text(encoding="utf-8")
    assert "cv_weight" in readme
    assert "person-level CV weight" in (output / "open" / "README.md").read_text(encoding="utf-8")


def test_missing_ipw_file_stops_the_release(tmp_path: Path):
    _write_ledger(tmp_path)
    config = load(
        _config(
            tmp_path,
            """
open_names = false
pseudonym = "per_release"
licence = "CC-BY-4.0"
commercial_use = false
k = 10
ipw_path = "work/record_depth/missing.csv"
""",
        )
    )
    try:
        build_release(config, "noipw", rng=random.Random(1))
    except ReleaseError as exc:
        assert "ipw" in str(exc)
    else:
        raise AssertionError("a missing ipw file was accepted")
