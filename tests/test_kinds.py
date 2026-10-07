# SPDX-License-Identifier: AGPL-3.0-only
"""K1 activity_kind. People, titles, and URLs are fictitious."""

from __future__ import annotations

from pathlib import Path

from giye.ledger.io import read_csv, write_csv
from giye.ledger.schemas import ACTIVITIES_FIELDS, ARTISTS_FIELDS, empty_row
from giye.normalize.kinds import classify, kind_of, words_of
from giye.normalize.service import normalize


def _row(**extra: str) -> dict[str, str]:
    base = {
        "activity_id": extra.get("activity_id", "act"),
        "activity_type": "other",
        "title": "Example",
        "venue": "",
        "role": "",
        "origin": "cv:CV-DEMO",
        "reviewer_note": "",
    }
    base.update(extra)
    return base


def _cv(n: int, role: str, section: str, *, title: str = "Line", activity_id: str = "") -> list[dict[str, str]]:
    return [
        _row(
            activity_id=activity_id or f"cv-{role}-{i}",
            role=role,
            title=title if title != "Line" else f"Line {i}",
            reviewer_note=f"cv_section={section}",
        )
        for i in range(n)
    ]


def test_private_section_beats_a_specific_type() -> None:
    kind, reason = kind_of("solo_exhibition", "education", "Example College", "")
    assert (kind, reason) == ("education", "private_section")
    rows = classify([_row(activity_type="solo_exhibition", reviewer_note="cv_section=education", title="Example College")])
    assert rows[0].kind == "education"
    assert rows[0].channel == "background"
    assert rows[0].reason == "private_section"


def test_award_grant_is_funding_and_award_project_is_other() -> None:
    grant = classify([_row(activity_type="award", reviewer_note="cv_section=grant", title="Example grant")])
    assert (grant[0].kind, grant[0].reason, grant[0].channel) == ("funding", "award_grant", "activity")
    project = classify([_row(activity_type="award", reviewer_note="cv_section=project", title="Example project")])
    assert (project[0].kind, project[0].reason, project[0].channel) == ("other", "award_project", "activity")


def test_other_project_stays_other() -> None:
    got = classify([_row(activity_type="other", reviewer_note="cv_section=project", title="Example project")])
    assert (got[0].kind, got[0].reason, got[0].channel) == ("other", "other_project", "activity")


def test_collection_lexicon_and_locative_venue() -> None:
    held = classify(
        [_row(reviewer_note="cv_section=collection", title="A piece", venue="Example Collection, Seoul")]
    )
    assert (held[0].kind, held[0].reason) == ("collection", "collection_lexicon")
    title_hit = classify([_row(reviewer_note="cv_section=collection", title="Work acquired in 2019", venue="Example Hall")])
    assert title_hit[0].kind == "collection"
    locative = classify(
        [
            _row(
                reviewer_note="cv_section=collection",
                title="A piece",
                venue="Art collection in the example garden",
            )
        ]
    )
    assert (locative[0].kind, locative[0].reason, locative[0].channel) == ("other", "collection_residual", "activity")


def test_bare_prize_on_scholarship_award_is_hidden_award() -> None:
    bare = classify(
        [_row(activity_type="award", reviewer_note="cv_section=scholarship", title="Department Award")]
    )
    assert (bare[0].kind, bare[0].reason, bare[0].channel) == ("award", "scholarship_bare_prize", "hidden")
    funded = classify(
        [_row(activity_type="award", reviewer_note="cv_section=scholarship", title="Travel Grant Award")]
    )
    assert (funded[0].kind, funded[0].reason, funded[0].channel) == ("funding", "private_section", "hidden")


def test_whole_words_do_not_fire_inside_a_compound() -> None:
    assert "교수" not in words_of("조교수")
    assert "연구원" not in words_of("전임연구원")
    assert words_of("Lecturer") == {"lecturer"}


def test_roster_words_are_discovered_from_the_rows() -> None:
    rows = [
        *_cv(10, "Lecturer", "teaching"),
        *_cv(2, "컨설턴트", "project", title="Creative line"),
        _row(
            activity_id="roster-lecturer",
            origin="EXAMPLE-PROGRAMME",
            role="Lecturer",
            title="Example Programme 2024",
            reviewer_note="",
        ),
        _row(
            activity_id="roster-consultant",
            origin="EXAMPLE-PROGRAMME",
            role="컨설턴트 (창작)",
            title="Example support",
            reviewer_note="",
        ),
        _row(
            activity_id="roster-session",
            origin="EXAMPLE-PROGRAMME",
            role="Lecturer",
            title="Example Programme 3회차",
            reviewer_note="",
        ),
        _row(
            activity_id="roster-syllable",
            origin="EXAMPLE-PROGRAMME",
            role="강",
            title="Short",
            reviewer_note="",
        ),
    ]
    by_id = {row["activity_id"]: got for row, got in zip(rows, classify(rows), strict=True)}
    lecturer = by_id["roster-lecturer"]
    assert (lecturer.kind, lecturer.reason, lecturer.channel) == ("teaching", "roster_token_lecturer", "background")
    consultant = by_id["roster-consultant"]
    assert (consultant.kind, consultant.channel) == ("other", "activity")
    session = by_id["roster-session"]
    assert (session.kind, session.reason, session.channel) == ("talk_workshop", "roster_session", "activity")
    assert by_id["roster-syllable"].kind == "other"


def test_a_word_spread_across_titles_does_not_pass() -> None:
    """Ten role hits are not enough when more mentions sit outside the role column."""
    rows = [
        *_cv(10, "tutor", "teaching", title="Seminar"),
        *[
            _row(
                activity_id=f"title-only-{i}",
                role="",
                title="tutor seminar",
                reviewer_note="cv_section=teaching",
            )
            for i in range(11)
        ],
        _row(activity_id="roster-tutor", origin="EXAMPLE-PROGRAMME", role="tutor", title="Hour", reviewer_note=""),
    ]
    got = {row["activity_id"]: item for row, item in zip(rows, classify(rows), strict=True)}["roster-tutor"]
    assert got.kind == "other"
    assert got.reason == "roster_other"


def test_one_syllable_hangul_does_not_pass_even_with_ten_role_rows() -> None:
    rows = [
        *_cv(10, "강", "teaching"),
        _row(activity_id="roster", origin="EXAMPLE-PROGRAMME", role="강", title="Hour", reviewer_note=""),
    ]
    got = classify(rows)[-1]
    assert got.kind == "other"
    assert got.channel == "activity"


def test_teaching_word_outranks_a_higher_share_employment_word() -> None:
    rows = [
        *_cv(12, "vfx", "employment"),
        *_cv(10, "강사", "teaching"),
        _row(activity_id="both", origin="EXAMPLE-PROGRAMME", role="vfx", title="공개 강사", reviewer_note=""),
        _row(activity_id="job", origin="EXAMPLE-PROGRAMME", role="vfx", title="Show reel", reviewer_note=""),
        _row(activity_id="session", origin="EXAMPLE-PROGRAMME", role="vfx", title="1회차 강사", reviewer_note=""),
        _row(activity_id="acronym", origin="EXAMPLE-PROGRAMME", role="", title="작품 vfx 스케치", reviewer_note=""),
    ]
    by_id = {row["activity_id"]: got for row, got in zip(rows, classify(rows), strict=True)}
    assert (by_id["both"].kind, by_id["both"].reason, by_id["both"].channel) == (
        "teaching",
        "roster_token_강사",
        "background",
    )
    assert (by_id["job"].kind, by_id["job"].reason) == ("employment", "roster_token_vfx")
    assert (by_id["session"].kind, by_id["session"].reason, by_id["session"].channel) == (
        "talk_workshop",
        "roster_session",
        "activity",
    )
    assert by_id["acronym"].reason == "roster_other"


def test_normalize_writes_kind_and_the_reason(tmp_path: Path) -> None:
    data = tmp_path / "data"
    ledger = data / "ledger"
    write_csv(
        path=ledger / "artists.csv",
        fields=ARTISTS_FIELDS,
        rows=[empty_row(ARTISTS_FIELDS, ledger_id="p-ada", name_en="Ada Example")],
    )
    write_csv(
        path=ledger / "activities.csv",
        fields=ACTIVITIES_FIELDS,
        rows=[
            empty_row(
                ACTIVITIES_FIELDS,
                activity_id="grant-1",
                ledger_id="p-ada",
                title="Example grant",
                year="2019",
                activity_type="award",
                publishable="yes",
                origin="cv:CV-DEMO",
                reviewer_note="cv_section=grant",
                source_url="https://example.org/cv",
                collected_at="2026-01-15",
            )
        ],
    )
    config = tmp_path / "giye.toml"
    config.write_text(
        f"""
[archive]
name = "Synthetic field"
id_prefix = "GY"

[paths]
data = "{data.as_posix()}"
frames = "frames.yml"
""",
        encoding="utf-8",
    )
    from giye.config import load

    result = normalize(load(config))
    row = read_csv(result.processed / "activities.csv")[0]
    assert row["activity_kind"] == "funding"
    assert row["activity_channel"] == "activity"
    assert row["rules"].split("|")[-2:] == ["K1", "award_grant"]
    assert "K1 activity kind" in result.report
