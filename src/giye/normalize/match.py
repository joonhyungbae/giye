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
  not be a word character), but it may end inside one. Why: Korean writes a
  modifier before the head of a compound (시립미술관, 푸른바다), so a match that
  starts mid-word names a different, longer thing; a particle or a suffix
  follows the word it attaches to (미술관에서, 바다전), so a match that ends
  mid-word is usually the same name. A trailing boundary would refuse those
  readings, and a leading-only boundary keeps the false hits the review
  found, which were all prefixes or infixes.

``loose_spaces`` lets a space occur or not between any two characters of the
needle, because Korean spacing in names is not stable (V7d: 아르코미술관 and
아르코 미술관 are one spelling) and E3's work keys have their spaces removed.
"""

from __future__ import annotations

import re
from functools import lru_cache

_HANGUL = re.compile(r"[가-힣]")


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
    # Hangul may end inside a word (a particle or suffix follows it); other scripts may not.
    tail = r"(?![^\W가-힣])" if _word(chars[-1]) and not _hangul(chars[-1]) else ""
    return re.compile(head + body + tail)


def occurrences(needle: str, text: str, *, loose_spaces: bool = False) -> list[int]:
    """Start offsets in ``text`` where ``needle`` occurs on word boundaries."""
    pattern = _pattern(needle.strip(), loose_spaces)
    return [match.start() for match in pattern.finditer(text)] if pattern else []


def occurs(needle: str, text: str, *, loose_spaces: bool = False) -> bool:
    """True when ``needle`` occurs in ``text`` on word boundaries (see the module docstring)."""
    pattern = _pattern(needle.strip(), loose_spaces)
    return bool(pattern and pattern.search(text))
