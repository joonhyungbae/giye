# SPDX-License-Identifier: AGPL-3.0-only
"""Korean–English language module.

Selected as ``giye.normalize.lang.ko_en:KoEn``. ``load`` is ``KoreanEnglish.load``:
packaged glossary and gazetteer, or the paths the config names.
"""

from dataclasses import replace

from giye.normalize.language import KO_EN_VENUE_WORDS, KoreanEnglish, OrgClass

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
# V12. A bracket with the class on exactly one side is a host beside its funder.
# Measured: every Hangul pair at support >= 2 except the festival nickname puts
# an organisation word on exactly one side.
_ORG_CLASSES = (
    OrgClass("foundation", ("재단",), r"foundations?"),
    OrgClass("council", ("위원회",), r"councils?|committees?"),
    OrgClass("agency", ("진흥원",), r"agenc(?:y|ies)"),
    OrgClass("group", ("그룹",), r"groups?"),
    OrgClass("center", ("센터",), r"cent(?:er|re)s?"),
)
# A trailing child noun or a city is a branch, not an alias (MMCA Seoul, 미술관).
_CHILD_NOUNS = (
    "theater",
    "theatre",
    "museum",
    "gallery",
    "residency",
    "hall",
    "미술관",
    "극장",
    "레지던시",
    "전시관",
    "별관",
    "본관",
    "신관",
)
# A same-row bracket that credits a role is not an alias. 협력 is a credit here;
# V7f does not strip it, because there it names a partner rather than a host role.
_ALIAS_CREDITS = ("주최", "주관", "후원", "지원", "협력", "협찬", "presented by", "supported by")
# Dropped before a rival-stem comparison so "of" is not a shared institution.
# Places are dropped separately, through the gazetteer.
_STEM_STOPS = (
    "of",
    "the",
    "and",
    "for",
    "de",
    "des",
    "du",
    "la",
    "le",
    "fur",
    "und",
    "at",
    "in",
    "on",
)


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
        org_classes=_ORG_CLASSES,
        child_nouns=_CHILD_NOUNS,
        alias_credits=_ALIAS_CREDITS,
        stem_stops=_STEM_STOPS,
    )
