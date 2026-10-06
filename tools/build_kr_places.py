# SPDX-License-Identifier: AGPL-3.0-only
"""Write the packaged Korean place table from a GeoNames country extract.

What: read GeoNames ``KR.txt`` and write ``src/giye/normalize/data/ko_en/kr_places.tsv``
with every first-level region (ADM1: the 17 metropolitan cities and provinces)
and every second-level unit (ADM2: each city, county and district, 시·군·구).
Each row is one name of one unit: the GeoNames name, its ASCII name, and its
Hangul alternate names.

Why: without a GeoNames tree the default gazetteer knew seven Korean cities, so
``Cheongju`` or ``종로구`` became an institution (V3c) and ``MMCA Seoul`` joined
``MMCA`` although ``MMCA Cheongju`` was written too (V8). The package ships this
derived table so the documented rules hold for a user who has no GeoNames dump.
GeoNames is CC BY 4.0; the attribution is in ``data/ko_en/SOURCES.txt``.

Which names: a Hangul alternate is kept when it ends in an administrative
suffix (시, 도, 군, 구, 특별시, 광역시, …) or shares its first syllable with
another Hangul name of the same unit (서울 beside 서울특별시, 경북 beside
경상북도). That drops alternate names that are not a form of the unit's name
(a Gangwon row carries an unrelated Hangul word). A short region name that
the gazetteer's own Korean region table already reads (``KR_ADMIN1_KO``: 경기,
충북, 경북…) is not repeated here. Latin alternate names are not
kept: GeoNames lists transliterations and other languages' exonyms there, and
the loader already accepts the name without its -si/-gun/-gu/-do suffix.

How to run (the package venv; no dependency beyond the standard library):

    .venv/bin/python tools/build_kr_places.py path/to/geonames/KR.txt

The output is sorted, so the same extract gives the same file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from giye.normalize.gazetteer import KR_ADMIN1_KO

OUT = ROOT / "src" / "giye" / "normalize" / "data" / "ko_en" / "kr_places.tsv"
CODES = {"ADM1", "ADM2"}
HANGUL_NAME = re.compile(r"[가-힣]{2,}")
SUFFIX = re.compile(r"(?:특별시|광역시|특별자치시|특별자치도|직할시|시|도|군|구)$")
HEADER = (
    "# Korean first- and second-level administrative units (ADM1, ADM2).\n"
    "# Derived from GeoNames KR.txt (CC BY 4.0) by tools/build_kr_places.py; see SOURCES.txt.\n"
    "name\tpopulation\tcountry\tadmin1\tcanonical\n"
)


def hangul_names(alternates: list[str]) -> set[str]:
    """Hangul alternates that are forms of the unit's name (see the module docstring)."""
    found = {item for item in alternates if HANGUL_NAME.fullmatch(item)}
    kept = set()
    for item in found - set(KR_ADMIN1_KO):
        if SUFFIX.search(item) or any(other != item and other[0] == item[0] for other in found):
            kept.add(item)
    return kept


def rows(path: Path) -> list[tuple[str, int, str, str, str]]:
    """``(name, population, country, admin1, canonical)`` for every ADM1 and ADM2 name."""
    out: set[tuple[str, int, str, str, str]] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 15 or columns[7] not in CODES or columns[8] != "KR":
                continue
            population, admin1, canonical = int(columns[14] or 0), columns[10], columns[1]
            alternates = columns[3].split(",") if columns[3] else []
            for name in {columns[1], columns[2], *hangul_names(alternates)}:
                if name.strip():
                    out.add((name.strip(), population, "KR", admin1, canonical))
    return sorted(out, key=lambda row: (row[3], row[4], row[0], -row[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write kr_places.tsv from GeoNames KR.txt.")
    parser.add_argument("kr_txt", type=Path, help="GeoNames KR.txt")
    parser.add_argument("--out", type=Path, default=OUT, help=f"Output TSV. Default: {OUT}")
    args = parser.parse_args(argv)
    found = rows(args.kr_txt)
    body = "".join(f"{name}\t{pop}\t{country}\t{admin1}\t{canonical}\n" for name, pop, country, admin1, canonical in found)
    args.out.write_text(HEADER + body, encoding="utf-8")
    print(f"{args.out}: {len(found)} names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
