# SPDX-License-Identifier: AGPL-3.0-only
"""Frame registry: F1–F5 judgements are recorded in full, and coverage is members / roster size."""

from __future__ import annotations

from pathlib import Path

import pytest

from giye.collect.frames import coverage, is_admitted, load_frames, validate_transcribed_membership

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo" / "frames.yml"


def test_valid_frame_coverage_and_optional_korean_name():
    registry = load_frames(FIXTURES / "frames_valid.yml")
    frame = registry.by_code("EXAMPLE-RESIDENCY")
    assert frame is not None
    assert frame.name_ko == ""
    assert frame.eligibility.decision == "included"
    assert frame.eligibility.f1_purpose
    assert frame.eligibility.f3_territory == "Held in the configured territory."
    assert frame.coverage() == 0.5
    assert coverage(2, 4) == 0.5
    assert coverage(1, 0) is None


def test_f3_korea_is_accepted_as_the_territory_alias():
    frame = load_frames(FIXTURES / "frames_f3_korea.yml").frames[0]
    assert frame.eligibility.f3_territory == "Held in Korea."
    assert frame.name_ko == "예시 레지던시"


def test_demo_frames_cover_included_excluded_and_adjacent():
    registry = load_frames(DEMO)
    decisions = {frame.code: frame.eligibility.decision for frame in registry.frames}
    assert decisions == {
        "EXAMPLE-RESIDENCY": "included",
        "EXAMPLE-WORKSHOP": "adjacent",
        "EXAMPLE-FORUM": "adjacent",
        "EXAMPLE-GRANT": "excluded",
    }
    residency = registry.by_code("EXAMPLE-RESIDENCY")
    assert residency is not None
    assert residency.roster_size_declared == 12
    assert residency.coverage(12) == 1.0
    assert residency.coverage(6) == 0.5
    workshop = registry.by_code("EXAMPLE-WORKSHOP")
    assert workshop is not None
    # roster_count is set, but no size is declared independently: unknown, not 100%.
    assert workshop.roster_count == 10
    assert workshop.coverage(10) is None
    grant = registry.by_code("EXAMPLE-GRANT")
    assert grant is not None
    assert grant.coverage(0) is None
    assert grant.eligibility.note.startswith("Excluded")


def test_declared_roster_size_needs_a_source(tmp_path: Path):
    text = (FIXTURES / "frames_valid.yml").read_text(encoding="utf-8")
    text = text.replace("    roster_size_source: https://example.org/residency/alumni\n", "")
    path = tmp_path / "frames.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="roster_size_source"):
        load_frames(path)


EDITION_SIZES = """    roster_size_declared_by_edition:
      2024:
        size: 5
        source: https://example.org/residency/2024
"""


def _with_edition_sizes(tmp_path: Path, block: str = EDITION_SIZES) -> Path:
    text = (FIXTURES / "frames_valid.yml").read_text(encoding="utf-8")
    text = text.replace("    included_count: 2\n", "    included_count: 2\n" + block, 1)
    path = tmp_path / "frames.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_edition_declares_its_own_size(tmp_path: Path):
    frame = load_frames(_with_edition_sizes(tmp_path)).by_code("EXAMPLE-RESIDENCY")
    assert frame is not None
    declared = frame.declared_size("2024")
    assert declared is not None
    assert (declared.size, declared.source) == (5, "https://example.org/residency/2024")
    assert frame.edition_coverage("2024", 4) == 0.8
    # More rows than the stated size read 100%, not more.
    assert frame.edition_coverage("2024", 6) == 1.0
    # An edition that states no size has unknown coverage.
    assert frame.declared_size("2023") is None
    assert frame.edition_coverage("2023", 4) is None


def test_edition_size_needs_a_source(tmp_path: Path):
    block = EDITION_SIZES.replace("        source: https://example.org/residency/2024\n", "")
    with pytest.raises(ValueError, match="2024: roster_size_declared_by_edition needs an http"):
        load_frames(_with_edition_sizes(tmp_path, block))


def test_edition_size_must_be_positive(tmp_path: Path):
    block = EDITION_SIZES.replace("size: 5", "size: 0")
    with pytest.raises(ValueError, match="positive integer"):
        load_frames(_with_edition_sizes(tmp_path, block))


def test_missing_criterion_names_the_rule(tmp_path: Path):
    text = (FIXTURES / "frames_valid.yml").read_text(encoding="utf-8").replace("      f1_purpose: States the field.\n", "")
    path = tmp_path / "frames.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"f1_purpose is required \(F1\)"):
        load_frames(path)


def test_transcribed_frame_may_omit_the_frame_url_when_every_row_has_one(tmp_path: Path):
    """A hand-copied roster has no single page. Each membership row is the source."""
    registry = load_frames(FIXTURES / "frames_transcribed.yml")
    frame = registry.by_code("HAND-COHORT")
    assert frame is not None
    assert frame.collector == "transcribed"
    assert frame.source_url == ""
    rows = [
        {"frame_code": "HAND-COHORT-2019", "source_url": "https://example.org/press/2019"},
        {"frame_code": "HAND-COHORT", "source_url": "https://example.org/press/2020"},
    ]
    validate_transcribed_membership(registry, rows)
    rows.append({"frame_code": "HAND-COHORT-2021", "source_url": ""})
    with pytest.raises(ValueError, match="source_url"):
        validate_transcribed_membership(registry, rows)


def test_empty_frame_url_is_rejected_unless_the_collector_is_transcribed(tmp_path: Path):
    text = (FIXTURES / "frames_transcribed.yml").read_text(encoding="utf-8").replace(
        "collector: transcribed", "collector: roster", 1
    )
    path = tmp_path / "frames.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="source_url must be an http"):
        load_frames(path)


def test_only_included_and_adjacent_are_admitted():
    """Any other recorded word, including one the loader would reject, is not admitted."""
    assert is_admitted("included")
    assert is_admitted("adjacent")
    for decision in ("excluded", "planned", "no_public_roster", "maybe", ""):
        assert not is_admitted(decision)


def test_unknown_decision_is_rejected(tmp_path: Path):
    text = (FIXTURES / "frames_valid.yml").read_text(encoding="utf-8").replace("decision: included", "decision: maybe", 1)
    path = tmp_path / "frames.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="decision"):
        load_frames(path)
