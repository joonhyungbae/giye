# SPDX-License-Identifier: AGPL-3.0-only
"""A toy language module selected from a test config.

``[normalize] language_module = "tests.toy_language:Toy"``.
"""

from __future__ import annotations

import re

from giye.normalize.gazetteer import Gazetteer
from giye.normalize.language import VenueWords


class Toy:
    """One made-up script pair. ``load`` matches ``KoreanEnglish.load``."""

    name = "toy-qx"
    # Toy venue words: "zz" is a trailing qualifier, "vol N" an edition, "wing" a building part.
    venue_words = VenueWords(
        qualifiers=("zz",),
        edition_lead=(r"vol \d+",),
        edition_tail=(r"vol \d+",),
        latin_building_parts=("wing",),
    )

    def __init__(self) -> None:
        self._glossary = {"qx": (("kwa",),)}
        self._gazetteer = Gazetteer.from_records(
            cities=[("Qxville", 1000, "QQ", "01", "Qxville")],
            countries={"qqland": "QQ"},
            country_codes={"QQ"},
            alpha3={"QQL": "QQ"},
            admin1={},
            us_postal=set(),
        )

    @property
    def glossary(self):
        return self._glossary

    @property
    def gazetteer(self):
        return self._gazetteer

    def personal_name(self, name: str) -> bool:
        # Toy rule: one word of 2–6 letters starting with "q" is a personal name.
        return bool(re.fullmatch(r"q[a-z]{1,5}", name or ""))

    def name_keys(self, name: str) -> set[str]:
        return {name.casefold()} if name else set()

    def romanise(self, token: str) -> str:
        return token.casefold().replace("q", "k")

    @classmethod
    def load(cls, *, glossary=None, cities=None, reference=None):
        return cls()
