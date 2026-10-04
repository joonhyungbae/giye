# SPDX-License-Identifier: AGPL-3.0-only
"""Korean–English language module.

Selected as ``giye.normalize.lang.ko_en:KoEn``. ``load`` is ``KoreanEnglish.load``:
packaged glossary and gazetteer, or the paths the config names.
"""

from giye.normalize.language import KoreanEnglish


class KoEn(KoreanEnglish):
    """Entry-point class. The protocol name stays ``ko-en``."""
