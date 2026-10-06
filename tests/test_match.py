# SPDX-License-Identifier: AGPL-3.0-only
"""Whole-word occurrence (``giye.normalize.match``): what may follow a Hangul match."""

from __future__ import annotations

from giye.normalize.match import occurs


def test_a_hangul_name_may_be_followed_by_a_particle_or_a_listed_suffix() -> None:
    assert occurs("부산현대미술관", "2019 부산현대미술관에서 개인전")
    assert occurs("부산현대미술관", "부산현대미술관, 부산")
    assert occurs("바다", "바다전 2019")
    assert occurs("예시 공간", "예시 공간의 기획전", loose_spaces=True)


def test_a_hangul_name_is_not_found_inside_a_longer_word() -> None:
    # A truncated venue and a compound are different names.
    assert not occurs("부산현대미술", "부산현대미술관 2019")
    assert not occurs("푸른 신호", "푸른 신호등 설치", loose_spaces=True)
