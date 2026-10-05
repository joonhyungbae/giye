# SPDX-License-Identifier: AGPL-3.0-only
"""Place names → (country, Korean region).

Lookup order, first hit wins. See docs/RULES.md.

G1  An ISO alpha-2 or alpha-3 country code, read only when the fragment is the
    whole token (``KR``, ``KOR``). Used by fragment resolution (enhanced).
G3  An uppercase two- or three-letter token after a place inherits that place's
    country (and, for Korea, its region). ``Los Angeles, CA`` stays in the US;
    the token is not re-read as Canada.
G6  A two-letter US postal abbreviation that is not itself a country code
    (``NY``), when no previous place gave G3 a country.

G2, G4, and G5 are not separate steps. The audit header says G1–G6, and the
remaining lookups, in order, are a Korean first-level region written in Hangul,
a city (the most populous row wins when a name is listed twice), an admin1 name,
then a country name. A Korean administrative suffix is stripped before the
second try (서울시 → 서울, 경기도 → 경기).

V1 calls this with ``words=False``: the whole fragment must be a place. A town
inside an institution name (``Nam June Paik Art Center``) is not a place; the
city is the fragment that is only the city (``…, Yongin``).

The packaged table is a compact gazetteer. ``from_geonames`` reads a GeoNames
tree (cities15000 + admin1, CC BY 4.0, not shipped) so a configured archive can
resolve places the compact table omits.
A country extract in the same directory (``KR.txt``, or ``allCountries.txt``)
supplies the places cities15000 drops: administrative divisions and
neighbourhoods. See ``OMITTED_PLACE_CODES``.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import NamedTuple

# First-level Korean regions the site publishes. Incheon folds into 경기 so the
# published set stays those region tags; every other province is 기타.
KR_REGION = {
    "11": "서울",
    "13": "경기",
    "12": "경기",
    "10": "부산",
    "15": "대구",
    "18": "광주",
    "19": "대전",
}
# Short forms the country lists leave out. "Korea" alone is the Republic of
# Korea: no CV in this archive uses that bare name for the DPRK.
EXTRA_COUNTRY = {
    "korea": "KR",
    "한국": "KR",
    "uk": "GB",
    "england": "GB",
    "scotland": "GB",
    "holland": "NL",
    "the netherlands": "NL",
    "the us": "US",
    "the usa": "US",
    "the united states": "US",
}
# Korean names of first-level regions. GeoNames admin1 is ASCII-only.
KR_ADMIN1_KO = {
    "서울": "11",
    "경기": "13",
    "인천": "12",
    "부산": "10",
    "대구": "15",
    "광주": "18",
    "대전": "19",
    "울산": "21",
    "세종": "22",
    "강원": "06",
    "충북": "05",
    "충청북": "05",
    "충남": "17",
    "충청남": "17",
    "전북": "03",
    "전라북": "03",
    "전남": "16",
    "전라남": "16",
    "경북": "14",
    "경상북": "14",
    "경남": "20",
    "경상남": "20",
    "제주": "01",
}
HANGUL = re.compile(r"[가-힣]")
COUNTRY_ALPHA2_CODE = re.compile(r"^([A-Z]{2})\.?$")
COUNTRY_ALPHA3_CODE = re.compile(r"^([A-Z]{3})\.?$")
REGIONAL_ABBREVIATION = re.compile(r"^[A-Z]{2,3}\.?$")
CITY_SUFFIX = re.compile(r"-(?:si|gun|gu|do|eup)$", re.IGNORECASE)
# cities15000 is populated places with population over 15,000 (and capitals).
# A city, county, or district is often an administrative division (ADM2) whose
# Hangul name is not on the populated-place row. A neighbourhood is an ADM3
# (동·읍·면) or a PPLX, and GeoNames often records its population as 0, so the
# 15,000 cutoff drops it (명동). Those codes are read from a country extract.
# They fill names the city file does not already resolve; they do not outrank it.
OMITTED_PLACE_CODES = frozenset({"ADM2", "ADM3", "PPLX"})
# (population, country, admin1, canonical English name)
City = tuple[int, str, str, str]


class Index(NamedTuple):
    """Tables one gazetteer lookup reads: countries, cities, admin1 names, US postal codes."""

    countries: dict[str, str]
    country_codes: set[str]
    alpha3_codes: dict[str, str]
    cities: dict[str, City]
    legacy_cities: dict[str, City]
    admin1_names: dict[str, tuple[str, str]]
    us_admin_codes: set[str]


def place_key(text: str) -> str:
    """Comparison key: stripped, lower-cased, surrounding punctuation removed."""
    return re.sub(r"\s+", " ", text.strip().lower().strip(".,;:()[]\"'"))


def _latin_name(text: str) -> bool:
    """True when every letter in a GeoNames alternate name is Latin.

    Airport and city codes stored in the alternate-name column are not aliases.
    """
    if re.fullmatch(r"(?=.{2,8}$)(?=.*[A-Z])[A-Z0-9.&]+", text):
        return False
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and all("LATIN" in unicodedata.name(char, "") for char in letters)


def _name_variants(name: str) -> set[str]:
    """Surface forms of one gazetteer name.

    A Latin administrative suffix (-si, -gun, -gu, -do, -eup) is optional.
    A hyphen is optional too: GeoNames writes Myeong-dong and a venue writes
    Myeongdong. Both are the same name. This is not a list of extra places.
    """
    forms = {name, CITY_SUFFIX.sub("", name)}
    extra: set[str] = set()
    for item in forms:
        if "-" not in item:
            continue
        flat = item.replace("-", "")
        extra.add(flat)
        extra.add(CITY_SUFFIX.sub("", flat))
    return {item for item in forms | extra if item}


def _add_city(cities: dict[str, City], name: str, record: City, *, protect: set[str] | None = None) -> None:
    """Index ``name`` and its surface forms. A protected key is left as it is.

    ``protect`` is the set of keys a populated-place file already resolved.
    An administrative or neighbourhood row must not replace that resolution
    when the two share a name; the more populous populated place stays.
    """
    pop = record[0]
    for variant in _name_variants(name):
        key = place_key(variant)
        if len(key) < 2 or (protect is not None and key in protect):
            continue
        if key not in cities or cities[key][0] < pop:
            cities[key] = record


def load_country_tables(countries_dir: Path) -> tuple[dict[str, str], set[str], dict[str, str]]:
    """Country name → alpha-2, the alpha-2 set, and alpha-3 → alpha-2.

    ``EXTRA_COUNTRY`` is applied first and is not overwritten by the JSON names.
    """
    countries: dict[str, str] = dict(EXTRA_COUNTRY)
    code_rows = json.loads((countries_dir / "codes.json").read_text(encoding="utf-8"))
    country_codes = {row[0] for row in code_rows}
    alpha3_codes = {row[1]: row[0] for row in code_rows}
    for lang in ("en", "ko"):
        payload = json.loads((countries_dir / f"{lang}.json").read_text(encoding="utf-8"))
        for code, names in payload["countries"].items():
            for name in [names] if isinstance(names, str) else names:
                countries.setdefault(place_key(name), code)
    return countries, country_codes, alpha3_codes


def _country_extracts(geonames_dir: Path, country_codes: set[str]) -> tuple[Path, ...]:
    """GeoNames country files that carry rows cities15000 leaves out.

    ``allCountries.txt``, when present, is the whole extract and the per-country
    files are not also read. Otherwise every ``XX.txt`` whose stem is an ISO
    alpha-2 code in the country table is read (``KR.txt``).
    """
    all_countries = geonames_dir / "allCountries.txt"
    if all_countries.is_file():
        return (all_countries,)
    found: list[Path] = []
    for path in sorted(geonames_dir.glob("*.txt")):
        stem = path.stem
        if len(stem) == 2 and stem.isalpha() and stem.isupper() and stem in country_codes:
            found.append(path)
    return tuple(found)


def _index_omitted_places(
    path: Path,
    enhanced: dict[str, City],
    legacy: dict[str, City],
    held_enhanced: set[str],
    held_legacy: set[str],
) -> None:
    """Index ADM2, ADM3, and PPLX rows from a GeoNames country extract.

    The population on these rows is often zero, because GeoNames stored it on
    a different feature. Latin alternate names are therefore not held back by
    the million-person gate used for cities15000. A key the city file already
    resolved is left alone.
    """
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 15 or columns[7] not in OMITTED_PLACE_CODES:
                continue
            pop, country, admin_code = int(columns[14] or 0), columns[8], columns[10]
            alternates = columns[3].split(",") if columns[3] else []
            names = {columns[1], columns[2]}
            names.update(item for item in alternates if HANGUL.search(item) or _latin_name(item))
            record = (pop, country, admin_code, columns[1])
            for name in names:
                _add_city(legacy, name, record, protect=held_legacy)
                _add_city(enhanced, name, record, protect=held_enhanced)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    """Tab-separated rows. Blank lines and ``#`` comments are not data."""
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            rows.append(line)
    if not rows:
        return []
    return list(csv.DictReader(rows, delimiter="\t"))


class Gazetteer:
    """Place index.

    Fragment lookup tries G1, then G6, then a Korean region, a city, an admin1
    name, and a country name.
    """

    def __init__(self, index: Index, sources: tuple[Path, ...] = ()) -> None:
        """``sources`` are the files whose hashes a normalisation manifest records."""
        self.index = index
        self.sources = sources

    @property
    def countries(self) -> dict[str, str]:
        """Lower-cased country name → ISO alpha-2, including the short forms in ``EXTRA_COUNTRY``."""
        return self.index.countries

    @property
    def cities(self) -> dict[str, City]:
        """Enhanced city index (V9 place readings, V8 acronym + city)."""
        return self.index.cities

    def place_en(self, segment: str) -> str:
        """English city name for a gazetteer key, lower-cased, suffix stripped.

        V9 looks the Hangul segment up as stored. Keys are already normalised,
        and a Hangul key is unchanged by that, so the segment is used as-is.
        """
        hit = self.cities.get(segment)
        if not hit:
            return ""
        return CITY_SUFFIX.sub("", hit[3]).lower()

    def is_place_token(self, token: str) -> bool:
        """V7e: a Latin token of at least 3 characters that is a city or a country."""
        return len(token) >= 3 and bool(self.cities.get(token) or self.countries.get(token))

    def is_admin1_name(self, text: str) -> bool:
        """Whether ``text`` equals a published admin1 name, ignoring case."""
        return place_key(text) in self.index.admin1_names

    def resolve(self, text: str, words: bool = True) -> list[tuple[str, str]]:
        """Every place named in ``text``, in order, as (country, Korean region or "")."""
        return [(country, region) for _, country, region in self.resolve_detail(text, words=words)]

    def resolve_detail(self, text: str, words: bool = True) -> list[tuple[str, str, str]]:
        """Places as (canonical city, country, Korean region). Country-only hits have an empty city."""
        out: list[tuple[str, str, str]] = []
        for part in re.split(r"[,/&|·;]|\band\b|\bbetween\b|그리고|및", text or ""):
            part = re.sub(r"^\s*the\s+", "", part, flags=re.IGNORECASE).strip()
            tokens = [word for word in part.split() if word[:1].isupper() or HANGUL.match(word)] if words else []
            whole = self._lookup_detail(part) if part[:1].isupper() or HANGUL.match(part[:1]) else None
            hit = whole or next((item for item in map(self._lookup_detail, tokens) if item), None)
            if not hit or hit in out:
                continue
            _city, country, region = hit
            if not _city and not region and any(code == country for _, code, _ in out):
                continue  # "Seoul, Korea": the country adds nothing to the city
            if _city or region:
                out = [item for item in out if not (not item[0] and not item[2] and item[1] == country)]
            out.append(hit)
        return out

    def resolve_fragments(self, fragments: list[str]) -> list[tuple[str, str, str] | None]:
        """Resolve whole venue fragments, with the neighbour context G3 needs."""
        out: list[tuple[str, str, str] | None] = []
        previous: tuple[str, str, str] | None = None
        for fragment in fragments:
            if previous and REGIONAL_ABBREVIATION.fullmatch(fragment.strip()):
                hit: tuple[str, str, str] | None = (
                    "",
                    previous[1],
                    previous[2] if previous[1] == "KR" else "",
                )
            else:
                hit = self._lookup_detail(fragment, enhanced=True)
            out.append(hit)
            previous = hit
        return out

    def _lookup_detail(self, part: str, *, enhanced: bool = False) -> tuple[str, str, str] | None:
        """One fragment: G1, then G6, then a Korean region, a city, an admin1 name, a country.

        None when nothing matches.
        """
        index = self.index
        cities = index.cities if enhanced else index.legacy_cities
        stripped = part.strip()
        alpha2_match = COUNTRY_ALPHA2_CODE.fullmatch(stripped)
        if enhanced and alpha2_match:
            code = alpha2_match.group(1)
            if code in index.country_codes:
                return "", code, ""  # G1
        alpha3_match = COUNTRY_ALPHA3_CODE.fullmatch(stripped)
        if enhanced and alpha3_match:
            code = index.alpha3_codes.get(alpha3_match.group(1))
            if code:
                return "", code, ""  # G1
        if (
            enhanced
            and re.fullmatch(r"[A-Z]{2}", stripped)
            and stripped not in index.country_codes
            and stripped in index.us_admin_codes
        ):
            return "", "US", ""  # G6

        key = place_key(part)
        if len(key) < 2:
            return None
        for candidate in (key, re.sub(r"(특별시|광역시|특별자치시|특별자치도|시|도|군|구)$", "", key)):
            if candidate in KR_ADMIN1_KO:
                admin1 = KR_ADMIN1_KO[candidate]
                city = cities.get(candidate)
                canonical = city[3] if city and city[1:3] == ("KR", admin1) else ""
                return canonical, "KR", KR_REGION.get(admin1, "기타")
            if candidate in cities and len(candidate) >= 2:
                _, country, admin1, name = cities[candidate]
                region = KR_REGION.get(admin1, "기타") if country == "KR" else ""
                return name, country, region
            if enhanced and candidate in index.admin1_names:
                country, region = index.admin1_names[candidate]
                return "", country, region
            if candidate in index.countries:
                return "", index.countries[candidate], ""
        return None

    @classmethod
    def from_records(
        cls,
        *,
        cities: list[tuple[str, int, str, str, str]],
        countries: dict[str, str],
        country_codes: set[str],
        alpha3: dict[str, str],
        admin1: dict[str, tuple[str, str]],
        us_postal: set[str],
        sources: tuple[Path, ...] = (),
    ) -> Gazetteer:
        """Build an index from in-memory rows. Both city indexes receive every row.

        ``from_geonames`` keeps Latin alternate names out of the legacy index
        unless the city has at least a million people. A table built here has
        no alternate-name column, so the two indexes match.
        """
        enhanced: dict[str, City] = {}
        legacy: dict[str, City] = {}
        for name, pop, country, admin, canonical in cities:
            record = (pop, country, admin, canonical)
            _add_city(enhanced, name, record)
            _add_city(legacy, name, record)
        return cls(
            Index(countries, set(country_codes), dict(alpha3), enhanced, legacy, dict(admin1), set(us_postal)),
            sources,
        )

    @classmethod
    def from_tables(
        cls,
        *,
        cities_path: Path,
        countries_dir: Path,
        admin1_path: Path,
        postal_path: Path,
    ) -> Gazetteer:
        """Compact gazetteer: city TSV, country JSON, admin1 TSV, USPS codes."""
        countries, country_codes, alpha3 = load_country_tables(countries_dir)
        city_rows: list[tuple[str, int, str, str, str]] = []
        for row in _read_tsv(cities_path):
            city_rows.append(
                (
                    row["name"],
                    int(row.get("population") or 0),
                    row["country"],
                    row.get("admin1") or "",
                    row["canonical"],
                )
            )
        admin1: dict[str, tuple[str, str]] = {}
        for row in _read_tsv(admin1_path):
            key = place_key(row["name"])
            if len(key) >= 2:
                admin1.setdefault(key, (row["country"], row.get("region") or ""))
        postal: set[str] = set()
        for line in postal_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                postal.add(line)
        return cls.from_records(
            cities=city_rows,
            countries=countries,
            country_codes=country_codes,
            alpha3=alpha3,
            admin1=admin1,
            us_postal=postal,
            sources=(cities_path, admin1_path, postal_path, *(countries_dir / name for name in ("codes.json", "en.json", "ko.json"))),
        )

    @classmethod
    def from_geonames(cls, reference: Path) -> Gazetteer:
        """GeoNames index. ``reference`` holds ``geonames/`` and ``countries/``.

        ``cities15000.txt`` is GeoNames (CC BY 4.0) and is not part of this
        package. A missing file raises ``FileNotFoundError``.

        A country extract next to it (``KR.txt``, or ``allCountries.txt`` when
        that file is the one present) adds administrative divisions and
        neighbourhoods (``OMITTED_PLACE_CODES``). cities15000 does not carry
        those rows, and its alternate-name column often has no Hangul for the
        populated-place row that shares the city. The extract is optional: without
        it, only cities15000 is indexed.
        """
        cities_file = reference / "geonames" / "cities15000.txt"
        admin_file = reference / "geonames" / "admin1CodesASCII.txt"
        countries_dir = reference / "countries"
        if not cities_file.is_file() or not admin_file.is_file():
            raise FileNotFoundError(
                f"{reference} has no GeoNames cities15000.txt and admin1CodesASCII.txt. "
                "Those files are CC BY 4.0 and are not shipped. Download them and set "
                "[normalize] reference to the directory that contains geonames/ and countries/."
            )
        if not (countries_dir / "codes.json").is_file():
            raise FileNotFoundError(f"{countries_dir} is missing country JSON (codes.json, en.json, ko.json)")
        countries, country_codes, alpha3 = load_country_tables(countries_dir)
        admin1: dict[str, tuple[str, str]] = {}
        us_admin: set[str] = set()
        with admin_file.open(encoding="utf-8") as handle:
            for line in handle:
                columns = line.rstrip("\n").split("\t")
                code, country = columns[0], columns[0].split(".", 1)[0]
                if country == "US":
                    us_admin.add(code.split(".", 1)[1])
                region = KR_REGION.get(code.split(".", 1)[1], "기타") if country == "KR" else ""
                for name in columns[1:3]:
                    key = place_key(name)
                    if len(key) >= 2:
                        # Duplicate province names have no population to rank them.
                        # GeoNames file order is stable, so the first entry wins.
                        admin1.setdefault(key, (country, region))
        enhanced: dict[str, City] = {}
        legacy: dict[str, City] = {}
        with cities_file.open(encoding="utf-8") as handle:
            for line in handle:
                columns = line.rstrip("\n").split("\t")
                pop, country, admin_code = int(columns[14] or 0), columns[8], columns[10]
                alternates = columns[3].split(",")
                base_names = {columns[1], columns[2]} | {item for item in alternates if HANGUL.search(item)}
                record = (pop, country, admin_code, columns[1])
                for name in base_names:
                    _add_city(legacy, name, record)
                names = set(base_names)
                # Latin alternates of a smaller populated place are transliterations
                # and airport-style spellings. Only a city of a million or more
                # keeps them. Hangul alternates are not gated: they are the name.
                if pop >= 1_000_000:
                    names.update(item for item in alternates if _latin_name(item))
                for name in names:
                    _add_city(enhanced, name, record)
        extracts = _country_extracts(reference / "geonames", country_codes)
        # Snapshot after cities15000. An omitted-place row fills a missing name
        # and does not replace one the city file already resolved.
        held_enhanced, held_legacy = set(enhanced), set(legacy)
        for extract in extracts:
            _index_omitted_places(extract, enhanced, legacy, held_enhanced, held_legacy)
        return cls(
            Index(countries, country_codes, alpha3, enhanced, legacy, admin1, us_admin),
            (
                cities_file,
                admin_file,
                *extracts,
                *(countries_dir / name for name in ("codes.json", "en.json", "ko.json")),
            ),
        )
