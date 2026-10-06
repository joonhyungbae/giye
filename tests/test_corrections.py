# SPDX-License-Identifier: AGPL-3.0-only
"""Rule C1: the subject's own correction of a CV row survives every re-apply and the fold after a merge.

People and URLs are fictitious.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from giye.extract.apply import apply_extractions
from giye.extract.corrections import FIELDS, FILE_NAME, Correction, check
from giye.ledger.io import read_csv, write_csv
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import CV_SOURCES_FIELDS, empty_row
from tests.test_extract import TODAY, _config, _entry, _ledger, _person, _write_extract

MARKER = "self_report=upcoming 2026-10-04 (the subject's own statement)"


def _source(source_id: str, ledger_id: str, digest: str = "hash-1") -> dict[str, str]:
    return empty_row(
        CV_SOURCES_FIELDS,
        source_id=source_id,
        ledger_id=ledger_id,
        lang="ko",
        kind="web",
        url=f"https://cv.example.org/{source_id.lower()}",
        active="true",
        content_sha256=digest,
    )


def _setup(tmp_path: Path, people: list[dict[str, str]], owner: str, entries: list[dict]) -> Ledger:
    config = _config(tmp_path, site=tmp_path / "empty", cache=tmp_path / "cache", sources="")
    (tmp_path / "empty").mkdir()
    ledger = _ledger(tmp_path, config, people)
    ledger.write("cv_sources", [_source("CV-B", owner)], task="test")
    _write_extract(tmp_path, owner, ["CV-B"], ["hash-1"], entries)
    return ledger


def _record(tmp_path: Path, row: dict[str, str], field: str, value: str, **extra: str) -> None:
    """What scripts write: the CV line as read, its id, and the stated value."""
    item = {
        "source_id": row["origin"][3:],
        "title": row["title"],
        "year": row["year"],
        "activity_type": row["activity_type"],
        "venue": row["venue"],
        "activity_id": row["activity_id"],
        "field": field,
        "value": value,
        "stated_at": "2026-10-04",
        **extra,
    }
    path = tmp_path / "data" / "work" / FILE_NAME
    rows = read_csv(path) if path.exists() else []
    write_csv(path=path, fields=FIELDS, rows=[*rows, item])


def _only(ledger: Ledger, title: str) -> dict[str, str]:
    rows = [row for row in ledger.read("activities") if row["title"] == title]
    assert len(rows) == 1
    return rows[0]


def test_not_upcoming_survives_a_re_apply(tmp_path: Path):
    ledger = _setup(
        tmp_path,
        [_person("LED-b", "김하늘")],
        "LED-b",
        [_entry("CV-B", "열린 전시", TODAY.year, role="참여작가", upcoming=True)],
    )
    apply_extractions(ledger, today=TODAY)
    before = _only(ledger, "열린 전시")
    assert before["role"] == "참여작가 (예정)"
    assert "upcoming" in before["reviewer_note"].split("; ")

    _record(tmp_path, before, "upcoming", "no", confirmed_by="CV re-pull 2026-10-04")
    for _ in range(2):
        stats = apply_extractions(ledger, today=TODAY)
        row = _only(ledger, "열린 전시")
        assert stats.corrected == 1 and stats.corrections_unmatched == []
        assert row["role"] == "참여작가"
        assert "upcoming" not in row["reviewer_note"].split("; ")
        assert row["reviewer_note"].count("self_report=upcoming") == 1
        assert f"{MARKER}, confirmed by CV re-pull 2026-10-04" in row["reviewer_note"]
        assert row["publishable"] == "yes"
        # A correction is not a new activity.
        assert row["activity_id"] == before["activity_id"]


def test_correction_survives_the_fold_after_a_merge(tmp_path: Path):
    ledger = _setup(
        tmp_path,
        [_person("LED-a", "김하늘", gy="GY-000001"), _person("LED-b", "김하늘", gy="GY-000002")],
        "LED-b",
        [_entry("CV-B", "열린 전시", TODAY.year, upcoming=True)],
    )
    apply_extractions(ledger, today=TODAY)
    _record(tmp_path, _only(ledger, "열린 전시"), "upcoming", "no")
    apply_extractions(ledger, today=TODAY)

    # Ledger.merge folds the extractions once after the merge; the row moves to
    # LED-a and gets a new activity id, and the correction still applies.
    ledger.merge("LED-a", ["LED-b"], evidence="H same person, checked by the author 2026-01-15", rule="H")
    row = _only(ledger, "열린 전시")
    assert row["ledger_id"] == "LED-a"
    assert row["role"] == ""
    assert "upcoming" not in row["reviewer_note"].split("; ")
    assert MARKER in row["reviewer_note"]


def test_future_year_and_simple_fields(tmp_path: Path):
    ledger = _setup(
        tmp_path,
        [_person("LED-b", "김하늘")],
        "LED-b",
        [
            _entry("CV-B", "다음 해 전시", TODAY.year + 1, upcoming=True),
            _entry("CV-B", "틀린 해 전시", 2019, venue="Example Hal"),
            _entry("CV-B", "Example School", 2015, cv_section="education", activity_type="other"),
        ],
    )
    apply_extractions(ledger, today=TODAY)
    assert _only(ledger, "다음 해 전시")["publishable"] == "no"
    _record(tmp_path, _only(ledger, "다음 해 전시"), "upcoming", "no")
    wrong = _only(ledger, "틀린 해 전시")
    _record(tmp_path, wrong, "year", "2020")
    _record(tmp_path, wrong, "venue", "Example Hall")
    _record(tmp_path, wrong, "role", "작가")
    _record(tmp_path, _only(ledger, "Example School"), "year", "2016")
    for _ in range(2):
        apply_extractions(ledger, today=TODAY)
        assert _only(ledger, "다음 해 전시")["publishable"] == "yes"
        fixed = _only(ledger, "틀린 해 전시")
        assert (fixed["year"], fixed["venue"], fixed["role"]) == ("2020", "Example Hall", "작가")
        assert fixed["activity_id"] == wrong["activity_id"]
        for name in ("year", "venue", "role"):
            assert f"self_report={name} 2026-10-04 (the subject's own statement)" in fixed["reviewer_note"]
        # A correction does not publish a private section.
        school = _only(ledger, "Example School")
        assert (school["year"], school["publishable"]) == ("2016", "no")


def test_rows_of_a_stale_extraction_are_corrected(tmp_path: Path):
    ledger = _setup(
        tmp_path,
        [_person("LED-b", "김하늘")],
        "LED-b",
        [_entry("CV-B", "열린 전시", TODAY.year, upcoming=True)],
    )
    apply_extractions(ledger, today=TODAY)
    _record(tmp_path, _only(ledger, "열린 전시"), "upcoming", "no")
    # The CV changed after it was read: the file is skipped until it is read again.
    ledger.write("cv_sources", [_source("CV-B", "LED-b", digest="hash-2")], task="test")
    stats = apply_extractions(ledger, today=TODAY)
    assert stats.skipped_stale == ["LED-b"]
    row = _only(ledger, "열린 전시")
    assert row["role"] == "" and MARKER in row["reviewer_note"]


def test_unmatched_correction_is_reported(tmp_path: Path):
    ledger = _setup(tmp_path, [_person("LED-b", "김하늘")], "LED-b", [_entry("CV-B", "열린 전시", TODAY.year)])
    apply_extractions(ledger, today=TODAY)
    row = dict(_only(ledger, "열린 전시"), title="없는 전시", activity_id="")
    _record(tmp_path, row, "upcoming", "no")
    stats = apply_extractions(ledger, today=TODAY)
    assert stats.corrected == 0 and len(stats.corrections_unmatched) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("gy_id", "GY-000009"), ("upcoming", "maybe"), ("year", "20x0"), ("title", " ")],
)
def test_bad_corrections_are_refused(field: str, value: str):
    item = Correction("CV-B", "열린 전시", "2026", "group_exhibition", "", "", field, value, "2026-10-04")
    with pytest.raises(ValueError):
        check(item)
