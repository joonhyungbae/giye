# SPDX-License-Identifier: AGPL-3.0-only
"""A toy language module selected from a test config.

``[normalize] language_module = "tests.toy_language:Toy"``.
"""

from __future__ import annotations

from giye.normalize.gazetteer import Gazetteer


class Toy:
    """One made-up script pair. ``load`` matches ``KoreanEnglish.load``."""

    name = "toy-qx"

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

    def name_keys(self, name: str) -> set[str]:
        return {name.casefold()} if name else set()

    def romanise(self, token: str) -> str:
        return token.casefold().replace("q", "k")

    @classmethod
    def load(cls, *, glossary=None, cities=None, reference=None):
        return cls()
