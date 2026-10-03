# SPDX-License-Identifier: MIT
"""Rule X1: Hangul and Latin spellings of one name share a romanized key."""

from giye.resolve.names import hangul_name_keys, latin_name_keys


def match(ko: str, en: str) -> bool:
    return bool(hangul_name_keys(ko) & latin_name_keys(en))


def test_given_family_and_family_given_orders_match():
    assert match("배준형", "Joonhyung Bae")
    assert match("배준형", "Bae Joon-hyung")


def test_customary_spellings_match():
    assert match("김민지", "Minji Kim")
    assert match("이정현", "Jeonghyun Lee")
    assert match("박영희", "Younghee Park")


def test_different_people_do_not_match():
    assert not match("배준형", "Minji Kim")
    assert not match("김민지", "Minho Kim")


def test_invalid_inputs_give_no_keys():
    assert hangul_name_keys("배") == set()          # single syllable
    assert hangul_name_keys("Bae") == set()         # not Hangul
    assert latin_name_keys("Madonna") == set()      # one token


def test_keys_are_deterministic():
    assert hangul_name_keys("배준형") == hangul_name_keys("배준형")
