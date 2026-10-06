# SPDX-License-Identifier: AGPL-3.0-only
"""Whole-word occurrence of one normalised string inside another.

Used by E3 (a CV line lists a roster work, ``giye.resolve.evidence``) and by
the CV grounding check (``giye.extract.grounding``). Both compared plain
substrings before 2026-10-06, so 〈Sea〉 was found in "Research" and 〈Light〉
in "Lighthouse" (software review, round 6, MAJOR-4).

Both strings are expected in one normal form already (case-folded,
punctuation replaced by a space, whitespace collapsed); this module only adds
the boundaries:

- Latin letters and digits: the match may not start or end inside a word. The
  character before the first matched character and the one after the last
  may not be a letter or digit of a non-Hangul script.
- Hangul: the match may not start inside a word (the character before it may
  not be a word character). It may end inside one only when what follows is
  one of ``HANGUL_TRAILERS`` (a closed list of particles and the exhibition
  suffix), and then the word must end. Why: Korean writes a modifier before
  the head of a compound (시립미술관, 푸른바다), so a match that starts mid-word
  names a different, longer thing; a particle or the suffix 전 follows the
  word it attaches to (미술관에서, 바다전), so that match is the same name. Any
  other continuation is a different word: before 2026-10-06 every
  continuation was allowed, so the truncated venue 부산현대미술 was grounded by
  부산현대미술관 and 〈푸른 신호〉 was found in 푸른 신호등.

``loose_spaces`` lets a space occur or not between any two characters of the
needle, because Korean spacing in names is not stable (V7d: 아르코미술관 and
아르코 미술관 are one spelling) and E3's work keys have their spaces removed.
"""

from __future__ import annotations

import re
from functools import lru_cache

_HANGUL = re.compile(r"[가-힣]")
# What may follow a Hangul match inside one word: case particles, their
# common two-particle forms, the copula, and the exhibition suffix 전 / 展.
# Longer forms first, so the alternation takes the whole particle.
HANGUL_TRAILERS = (
    "에서의", "에서는", "에서도", "으로의", "으로는", "에게서",
    "에서", "에게", "에는", "에도", "에의", "으로", "로의", "로는", "과의", "와의", "까지", "부터",
    "이다", "이며", "이고", "처럼", "보다", "한테", "께서",
    "은", "는", "이", "가", "을", "를", "의", "에", "께", "와", "과", "로", "도", "만", "며",
    "들", "전", "展",
)
_TRAILER = "(?:" + "|".join(re.escape(item) for item in HANGUL_TRAILERS) + ")?"


def _word(char: str) -> bool:
    return char.isalnum()


def _hangul(char: str) -> bool:
    return bool(_HANGUL.fullmatch(char))


@lru_cache(maxsize=4096)
def _pattern(needle: str, loose_spaces: bool) -> re.Pattern[str] | None:
    chars = [char for char in needle if not char.isspace()] if loose_spaces else list(needle)
    if not chars:
        return None
    if loose_spaces:
        body = r"\s?".join(re.escape(char) for char in chars)
    else:
        body = "".join(r"\s+" if char.isspace() else re.escape(char) for char in chars)
    head = r"(?<!\w)" if _word(chars[0]) else ""
    # A Hangul ending may take one listed particle or suffix, then the word ends.
    # Other scripts end where a letter or digit of a non-Hangul script does not follow.
    if _hangul(chars[-1]):
        tail = _TRAILER + r"(?!\w)"
    elif _word(chars[-1]):
        tail = r"(?![^\W가-힣])"
    else:
        tail = ""
    return re.compile(head + body + tail)


def occurrences(needle: str, text: str, *, loose_spaces: bool = False) -> list[int]:
    """Start offsets in ``text`` where ``needle`` occurs on word boundaries."""
    pattern = _pattern(needle.strip(), loose_spaces)
    return [match.start() for match in pattern.finditer(text)] if pattern else []


def occurs(needle: str, text: str, *, loose_spaces: bool = False) -> bool:
    """True when ``needle`` occurs in ``text`` on word boundaries (see the module docstring)."""
    pattern = _pattern(needle.strip(), loose_spaces)
    return bool(pattern and pattern.search(text))
