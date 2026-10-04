# SPDX-License-Identifier: AGPL-3.0-only
"""Name keys for matching Korean (Hangul) and Latin-script spellings of the same person.

Part of the identity-resolution stage (rule X1 in docs/RULES.md). Ported from the Giye archive.

Also: normalise free-text Latin names from survey answers into a display name plus aliases.

Rules (applied only when the string has no Hangul):
- a parenthetical is lifted out of the name:
    "Middle Name: X"            -> X is inserted as a middle name
    'preferred name ... "X"'    -> X becomes an alias
    anything else short         -> alias
- "Given, Family" (one comma) -> "Given Family"; "Chinese full name, English name" -> "English Chinese"
The raw answer is kept by the caller in reviewer_note / the ledger history.
"""
from __future__ import annotations

import re

_HANGUL = re.compile(r"[가-힣]")
_PAREN = re.compile(r"\s*[\(（]([^\)）]*)[\)）]")
_QUOTED = re.compile(r"[\"“”']([^\"“”']{1,40})[\"“”']")
_MIDDLE = re.compile(r"^\s*middle\s*name\s*[:：]\s*(.+?)\s*$", re.IGNORECASE)


def _space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def normalize_latin_name(raw: str) -> tuple[str, list[str]]:
    """Return (display_name, aliases). Names containing Hangul are returned untouched."""
    s = _space(raw)
    if not s or _HANGUL.search(s):
        return s, []
    aliases: list[str] = []
    middle: str | None = None

    def lift(m: re.Match[str]) -> str:
        nonlocal middle
        inner = _space(m.group(1))
        mm = _MIDDLE.match(inner)
        if mm:
            middle = _space(mm.group(1))
            return ""
        q = _QUOTED.search(inner)
        if q:
            aliases.append(_space(q.group(1)))
            return ""
        if 0 < len(inner) <= 24:
            aliases.append(inner)
        return ""

    s = _space(_PAREN.sub(lift, s))
    if s.count(",") == 1:
        # the survey asked for "given, family"; the one exception is a Chinese full
        # name followed by an English given name ("Ong Wei Xuan, Justin"), which
        # customarily reads English name first
        left, right = [_space(x) for x in s.split(",", 1)]
        if left and right:
            s = f"{right} {left}" if len(right.split()) == 1 and len(left.split()) >= 3 else f"{left} {right}"
    s = _space(s.replace(",", " "))
    if middle:
        parts = s.split(" ")
        s = " ".join([parts[0], middle, *parts[1:]]) if len(parts) >= 2 else f"{s} {middle}"
    aliases = [a for a in dict.fromkeys(aliases) if a and a.lower() != s.lower()]
    return s, aliases


# Revised Romanization of Korean (2000). Final consonant clusters follow the standard spellings only (ㄱ→k, ㄵ→n, …).
# Sound changes across syllables (assimilation, liaison) are not applied; syllables are romanized and joined.
CHO = (
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h",
)
JUNG = (
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo",
    "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
)
JONG = (
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l",
    "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t",
)
_VOWEL_ALT = {
    "eo": ("eo", "u"),
    "eu": ("eu", "u"),
    "ae": ("ae", "e"),
    "oo": ("oo", "u"),
    "u": ("u", "eo", "eu", "oo"),
    "e": ("e", "ae"),
}
_VOWEL_KEYS = ("eo", "eu", "ae", "oo", "u", "e")
_ONSET_SWAP = {"g": "k", "k": "g", "d": "t", "t": "d", "b": "p", "p": "b", "j": "ch", "ch": "j"}
# Customary spellings of given-name syllables, separate from the surname set. Merged with RR even when RR is not listed.
GIVEN_ALT: dict[str, frozenset[str]] = {
    "영": frozenset({"yeong", "young", "yung", "yong"}),
    "희": frozenset({"hui", "hee", "hi", "heui"}),
    "경": frozenset({"gyeong", "kyung", "kyeong", "kyong"}),
    "정": frozenset({"jeong", "jung", "chung", "joung"}),
    "준": frozenset({"jun", "joon"}),
    "현": frozenset({"hyeon", "hyun"}),
    "성": frozenset({"seong", "sung"}),
    "수": frozenset({"su", "soo"}),
    "우": frozenset({"u", "woo"}),
    "연": frozenset({"yeon", "yun", "youn"}),
    "석": frozenset({"seok", "suk"}),
    "원": frozenset({"won", "weon"}),
    "혜": frozenset({"hye", "hae"}),
    "윤": frozenset({"yun", "yoon"}),
    "은": frozenset({"eun"}),
    "진": frozenset({"jin"}),
    "민": frozenset({"min"}),
    "지": frozenset({"ji"}),
    "주": frozenset({"ju", "joo"}),
    "호": frozenset({"ho"}),
    "훈": frozenset({"hun", "hoon"}),
    "용": frozenset({"yong"}),
    "선": frozenset({"seon", "sun"}),
    "종": frozenset({"jong"}),
    "상": frozenset({"sang"}),
    "재": frozenset({"jae"}),
    "태": frozenset({"tae"}),
    "동": frozenset({"dong"}),
    "철": frozenset({"cheol", "chul"}),
    "규": frozenset({"gyu", "kyu"}),
}
# No keys are produced when the number of candidate spellings (surname spellings × product of syllable variants) exceeds this.
CAP = 2000
_KEY_SEP = "/"

# Accepted spellings of the surname syllable. A set without the RR form (e.g. 이) uses only the listed spellings.
SURNAME: dict[str, frozenset[str]] = {
    "이": frozenset({"lee", "yi", "rhee", "rhie", "ri"}),
    "김": frozenset({"kim", "gim"}),
    "박": frozenset({"park", "pak", "bak"}),
    "최": frozenset({"choi", "choe", "chey"}),
    "정": frozenset({"jung", "jeong", "chung", "joung"}),
    "조": frozenset({"cho", "jo", "joe"}),
    "강": frozenset({"kang", "gang"}),
    "윤": frozenset({"yoon", "yun"}),
    "장": frozenset({"jang", "chang"}),
    "임": frozenset({"lim", "im", "yim"}),
    "한": frozenset({"han"}),
    "오": frozenset({"oh", "o"}),
    "신": frozenset({"shin", "sin"}),
    "서": frozenset({"seo", "suh"}),
    "권": frozenset({"kwon", "gwon"}),
    "황": frozenset({"hwang"}),
    "안": frozenset({"ahn", "an"}),
    "송": frozenset({"song"}),
    "류": frozenset({"ryu", "yu", "yoo", "you"}),
    "유": frozenset({"ryu", "yu", "yoo", "you"}),
    "홍": frozenset({"hong"}),
    "전": frozenset({"jeon", "jun", "chun"}),
    "고": frozenset({"ko", "go"}),
    "문": frozenset({"moon", "mun"}),
    "양": frozenset({"yang"}),
    "손": frozenset({"son", "sohn"}),
    "배": frozenset({"bae", "pae"}),
    "백": frozenset({"baek", "paik", "paek"}),
    "허": frozenset({"heo", "hur", "huh"}),
    "남": frozenset({"nam"}),
    "노": frozenset({"noh", "no", "roh"}),
    "하": frozenset({"ha"}),
    "곽": frozenset({"kwak", "gwak"}),
    "성": frozenset({"sung", "seong"}),
    "차": frozenset({"cha"}),
    "주": frozenset({"joo", "ju", "chu"}),
    "우": frozenset({"woo", "u"}),
    "구": frozenset({"koo", "gu", "ku"}),
    "민": frozenset({"min"}),
    "나": frozenset({"na", "ra"}),
    "진": frozenset({"jin", "chin"}),
    "지": frozenset({"ji", "chi"}),
    "엄": frozenset({"um", "eom"}),
    "채": frozenset({"chae", "chai"}),
    "원": frozenset({"won"}),
    "천": frozenset({"cheon", "chun"}),
    "방": frozenset({"bang"}),
    "공": frozenset({"kong", "gong"}),
    "현": frozenset({"hyun", "hyeon"}),
    "함": frozenset({"ham"}),
    "변": frozenset({"byun", "byeon"}),
    "염": frozenset({"yeom", "yum"}),
    "여": frozenset({"yeo", "yoh"}),
    "추": frozenset({"choo", "chu"}),
    "도": frozenset({"do", "doh"}),
    "소": frozenset({"so"}),
    "석": frozenset({"seok", "suk"}),
    "선": frozenset({"sun", "seon"}),
    "설": frozenset({"seol", "sul"}),
    "마": frozenset({"ma"}),
    "길": frozenset({"gil", "kil"}),
    "연": frozenset({"yeon", "yun"}),
    "위": frozenset({"wi", "wee"}),
    "표": frozenset({"pyo"}),
    "명": frozenset({"myung", "myeong"}),
    "기": frozenset({"ki", "gi"}),
    "반": frozenset({"ban", "pan"}),
    "왕": frozenset({"wang"}),
    "금": frozenset({"keum", "geum"}),
    "옥": frozenset({"ok"}),
    "육": frozenset({"yook", "yuk"}),
    "인": frozenset({"in"}),
    "맹": frozenset({"maeng"}),
    "제": frozenset({"je"}),
    "모": frozenset({"mo"}),
    "탁": frozenset({"tak"}),
    "국": frozenset({"kook", "guk"}),
}


def syllable_rr(ch: str) -> str:
    code = ord(ch) - 0xAC00
    cho, jung, jong = code // 588, (code % 588) // 28, code % 28
    return CHO[cho] + JUNG[jung] + JONG[jong]


def vowel_variants(rr: str) -> frozenset[str]:
    """Spellings of a syllable's RR with one vowel spelling substituted once. Results are not substituted again."""
    spans: list[tuple[int, int, str]] = []
    i = 0
    while i < len(rr):
        for key in _VOWEL_KEYS:
            if rr.startswith(key, i):
                spans.append((i, i + len(key), key))
                i += len(key)
                break
        else:
            i += 1
    if not spans:
        return frozenset({rr})
    # The onset before the first vowel (and consonants between vowels) is left unchanged.
    out = {rr[: spans[0][0]]}
    for n, (_start, end, key) in enumerate(spans):
        nxt = spans[n + 1][0] if n + 1 < len(spans) else len(rr)
        gap = rr[end:nxt]
        out = {prev + alt + gap for prev in out for alt in _VOWEL_ALT[key]}
    return frozenset(out)


def _surnames(name: str) -> frozenset[str]:
    sur_ch = name[0]
    if sur_ch in SURNAME:
        return SURNAME[sur_ch]
    return frozenset({syllable_rr(sur_ch)})


def _onset(ch: str) -> str:
    return CHO[(ord(ch) - 0xAC00) // 588]


def _flat(token: str) -> str:
    return token.replace("-", "").replace(" ", "")


def syllable_v2(ch: str) -> frozenset[str]:
    """v2 spellings of one given-name syllable. Vowel variants and onset pairs apply only to syllables not in the customary list."""
    rr = syllable_rr(ch)
    if ch in GIVEN_ALT:
        return frozenset({rr}) | GIVEN_ALT[ch]
    forms = vowel_variants(rr)
    onset = _onset(ch)
    alt = _ONSET_SWAP.get(onset)
    if alt is None:
        return forms
    n = len(onset)
    if any(not form.startswith(onset) for form in forms):
        raise RuntimeError(f"onset is not a prefix: {ch}")
    return forms | {alt + form[n:] for form in forms}


def korean_keys(name: str) -> set[tuple[str, str]]:
    """v1 (surname, given) keys. Given-name syllables get vowel substitutions only."""
    givens = {""}
    for ch in name[1:]:
        step = vowel_variants(syllable_rr(ch))
        givens = {a + b for a in givens for b in step}
    return {(sur, g) for sur in _surnames(name) for g in givens if g}


def korean_keys_v2(name: str) -> tuple[set[tuple[str, str]], int]:
    """v2 (surname, given) keys and the number of candidate spellings. Keys are empty when the count exceeds CAP."""
    steps = [syllable_v2(ch) for ch in name[1:]]
    count = len(_surnames(name))
    for step in steps:
        count *= len(step)
    if count > CAP:
        return set(), count
    givens = {""}
    for step in steps:
        givens = {_flat(a) + _flat(b) for a in givens for b in step}
    keys = {(sur, g) for sur in _surnames(name) for g in givens if g}
    return keys, count


def _form_key(surname: str, given: str) -> str:
    return f"{surname}{_KEY_SEP}{given}"


def _hangul_syllable(ch: str) -> bool:
    return 0xAC00 <= ord(ch) <= 0xD7A3


def hangul_name_keys(name_ko: str) -> set[str]:
    """Romanized v2 keys of a Korean personal name written in Hangul.

    Syllables are romanized with the Revised Romanization (2000) and joined, without sound
    changes across syllables. The surname (first syllable) uses only the spellings listed in
    SURNAME, or its single RR form when unlisted. Each given-name syllable contributes
    RR ∪ GIVEN_ALT customary spellings. Syllables outside the customary list also get vowel
    substitutions (eo↔u, eu↔u, ae↔e, oo↔u; not i↔ee) and, when the onset is g/k, d/t, b/p
    or j/ch, its pair. Listed syllables get no onset pairs. Spaces and hyphens are removed.

    A key is ``surname/given`` with the given-name tokens joined. An empty set is returned
    when the number of candidate spellings exceeds 2,000, when any character is not a
    precomposed Hangul syllable, or when the name has a single syllable.
    """
    name = (name_ko or "").strip()
    if len(name) < 2 or any(not _hangul_syllable(ch) for ch in name):
        return set()
    keys, count = korean_keys_v2(name)
    if count > CAP:
        return set()
    return {_form_key(sur, given) for sur, given in keys}


def english_keys(name: str) -> set[tuple[str, str]]:
    """(surname token, remaining tokens joined) of a Latin-script name, in both orders."""
    tokens = [tok.replace("-", "") for tok in re.findall(r"[a-z]+(?:-[a-z]+)*", (name or "").lower())]
    tokens = [tok for tok in tokens if tok]
    if len(tokens) < 2:
        return set()
    keys = set()
    for sur, given_toks in ((tokens[0], tokens[1:]), (tokens[-1], tokens[:-1])):
        given = "".join(given_toks)
        if sur and given:
            keys.add((sur, given))
    return keys


def latin_name_keys(name_en: str) -> set[str]:
    """Keys of a Latin-script name, compared with ``hangul_name_keys`` by intersection.

    The name is lower-cased and split into alphabetic tokens; hyphenated parts form one token,
    and hyphens and spaces are removed in the key. A key is ``surname/given``, with both the
    first and the last token taken as the surname. Fewer than two tokens give an empty set.
    """
    return {_form_key(sur, given) for sur, given in english_keys(name_en)}
