# SPDX-License-Identifier: AGPL-3.0-only
"""Korean–English language module.

Selected as ``giye.normalize.lang.ko_en:KoEn``. ``load`` is ``KoreanEnglish.load``:
packaged glossary and gazetteer, or the paths the config names.
"""

from dataclasses import replace

from giye.normalize.language import KO_EN_VENUE_WORDS, KoreanEnglish

# V4n. A generic name is national when the spaceless form starts with 국립, or
# its first Latin token is "national". Another field replaces these tuples.
_NATIONAL_PREFIXES = ("국립",)
_NATIONAL_TOKENS = ("national",)
# A national generic name is one institution only in Korea, where it is a
# government house name (국립현대미술관 in Seoul, Gwacheon, Cheongju, Goyang).
# Measured 2026-10-08: without this, the national museums of two Polish cities
# were joined; they are two institutions.
_NATIONAL_COUNTRIES = ("KR",)
# V7f. Role of a host or a funder, stripped only at a token boundary. Longer
# phrases are tried first when the pattern is built. 협력 is not a role: it
# names a partner, as does "in collaboration with".
_HOST_ROLES = (
    "공동주최",
    "공동주관",
    "주최",
    "주관",
    "후원",
    "협찬",
    "지원",
    "commissioned by",
    "supported by",
    "organised by",
    "organized by",
    "presented by",
    "funded by",
    "support",
)
_COLLABORATION_PHRASES = ("in collaboration with",)


class KoEn(KoreanEnglish):
    """Entry-point class. The protocol name stays ``ko-en``.

    National markers and host-role words live here so a venue rule never
    hard-codes Korean or English. ``KoreanEnglish`` without this override has
    empty tuples, and those rules then join nothing.
    """

    venue_words = replace(
        KO_EN_VENUE_WORDS,
        national_prefixes=_NATIONAL_PREFIXES,
        national_tokens=_NATIONAL_TOKENS,
        national_countries=_NATIONAL_COUNTRIES,
        host_roles=_HOST_ROLES,
        collaboration_phrases=_COLLABORATION_PHRASES,
    )
