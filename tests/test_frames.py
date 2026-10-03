# SPDX-License-Identifier: MIT
"""Frame registry: F1–F5 judgements are recorded in full, and coverage is members / roster size."""

from __future__ import annotations

from pathlib import Path

import pytest

from giye.collect.frames import coverage, load_frames

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
        "EXAMPLE-GRANT": "excluded",
    }
    residency = registry.by_code("EXAMPLE-RESIDENCY")
    assert residency is not None
    assert residency.coverage(4) == 1.0
    grant = registry.by_code("EXAMPLE-GRANT")
    assert grant is not None
    assert grant.coverage(0) is None
    assert grant.eligibility.note.startswith("Excluded")


def test_missing_criterion_names_the_rule(tmp_path: Path):
    text = (FIXTURES / "frames_valid.yml").read_text(encoding="utf-8").replace("      f1_purpose: States the field.\n", "")
    path = tmp_path / "frames.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"f1_purpose is required \(F1\)"):
        load_frames(path)


def test_unknown_decision_is_rejected(tmp_path: Path):
    text = (FIXTURES / "frames_valid.yml").read_text(encoding="utf-8").replace("decision: included", "decision: maybe", 1)
    path = tmp_path / "frames.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="decision"):
        load_frames(path)
