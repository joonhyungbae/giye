# SPDX-License-Identifier: AGPL-3.0-only
"""Korean legacy encodings decode the same way for roster pages and CV text."""

from __future__ import annotations

import pytest

from giye.collect.charset import decode_body, normalise_label
from giye.collect.fetch import Page
from giye.extract.text import extract_text

KO = "작가 명단 김하늘"


def test_meta_only_euc_kr_page_decodes():
    body = ("<html><head><meta charset='euc-kr'></head><body>" + KO + "</body></html>").encode("euc-kr")
    page = Page(url="http://x.example/", status=200, content=body, content_type="text/html")
    assert KO in page.text


def test_cp949_syllables_under_an_euc_kr_label():
    body = "똠방각하".encode("cp949")
    page = Page(url="http://x.example/", status=200, content=body, content_type="text/html; charset=euc-kr")
    assert page.text == "똠방각하"


@pytest.mark.parametrize("label", ["ks_c_5601-1987", "x-windows-949", "utf8mb4", "bogus"])
def test_unknown_or_legacy_labels_never_raise(label):
    page = Page(url="http://x.example/", status=200, content=b"abc", content_type=f"text/html; charset={label}")
    assert page.text == "abc"


def test_labels_map_safely():
    assert normalise_label("KS_C_5601-1987") == "cp949"
    assert normalise_label("utf8mb4") == "utf-8"
    assert normalise_label("nonsense-label") is None


def test_undeclared_cp949_is_detected():
    assert decode_body(KO.encode("cp949")) == KO


def test_http_charset_wins_over_meta():
    body = ("<meta charset='euc-kr'>" + KO).encode("utf-8")
    assert KO in decode_body(body, "text/html; charset=utf-8")


def test_cv_text_honours_the_meta_charset():
    line = "2019 개인전, 예시미술관, 서울"
    body = (
        "<html><head><meta http-equiv='Content-Type' content='text/html; charset=euc-kr'></head><body><p>"
        + line
        + "</p></body></html>"
    ).encode("euc-kr")
    assert "예시미술관" in extract_text(body, "html")
    assert extract_text(line.encode("cp949"), "txt") == line
