# SPDX-License-Identifier: AGPL-3.0-only
"""CV chunking. People and venues are fictitious."""

from __future__ import annotations

from giye.extract.chunk import split_cv


def _body_lines(chunks: list[str]) -> list[str]:
    """Non-empty CV lines, skipping a continuation marker on later pieces."""
    found: list[str] = []
    for index, chunk in enumerate(chunks):
        lines = chunk.splitlines()
        if (
            index > 0
            and lines
            and lines[0].startswith("[continued; last heading:")
            and lines[0].endswith("]")
        ):
            lines = lines[1:]
        found.extend(line for line in lines if line.strip())
    return found


def _assert_covers(text: str, max_chars: int) -> None:
    chunks = split_cv(text, max_chars)
    assert _body_lines(chunks) == [line for line in text.splitlines() if line.strip()]


def test_short_text_and_a_zero_budget_are_unchanged():
    text = "EXHIBITIONS\n\n1990 Example Show, Example Hall\n"
    assert split_cv(text, 0) == [text]
    assert split_cv(text, len(text)) == [text]
    assert split_cv(text, len(text) + 50) == [text]
    assert split_cv(text, 0)[0] is text


def test_paragraphs_pack_up_to_the_budget():
    text = "1990 AAAA\n\n1991 BBBB\n\n1992 CCCC"
    pair = "1990 AAAA\n\n1991 BBBB"
    assert split_cv(text, len(pair)) == [pair, "1992 CCCC"]
    # Each paragraph fits, but not beside the previous one, so it is not cut into lines.
    assert split_cv(text, len("1990 AAAA")) == ["1990 AAAA", "1991 BBBB", "1992 CCCC"]


def test_a_long_paragraph_splits_at_lines_and_fills_the_open_piece():
    text = "1990 one\n1991 two\n1992 three"
    pair = "1990 one\n1991 two"
    assert split_cv(text, len(pair)) == [pair, "1992 three"]

    text = "1990\n\n1991 BBBB\n1992 CCCC\n1993 DD"
    assert split_cv(text, 20) == ["1990\n\n1991 BBBB", "1992 CCCC\n1993 DD"]


def test_an_overlong_line_is_kept_whole():
    over = "1999 " + ("X" * 40)
    text = f"1990 Keep\n\n{over}\n\n1991 Next"
    assert split_cv(text, 10) == ["1990 Keep", over, "1991 Next"]
    assert over in split_cv(text, 10)[1]
    assert "X" * 10 + "\n" not in split_cv(text, 10)[1]


def test_later_pieces_carry_the_last_heading():
    first = "EXHIBITIONS\n\n1990 Alpha Signal, Example Hall"
    second = "1991 Beta Signal, Example Hall"
    text = first + "\n\n" + second
    chunks = split_cv(text, len(first))
    assert chunks == [first, f"[continued; last heading: EXHIBITIONS]\n{second}"]

    # A dated line is an entry, not a section, so it is not the carried heading.
    # A line of 61 characters is too long to be a heading; 60 is not.
    heading = "H" * 60
    too_long = "S" * 61
    text = f"{heading}\n\n{too_long}\n\n1991 Tail"
    chunks = split_cv(text, 60)
    assert chunks[0] == heading
    assert chunks[1] == f"[continued; last heading: {heading}]\n{too_long}"
    assert chunks[2] == f"[continued; last heading: {heading}]\n1991 Tail"

    bare = "1990 Alpha Signal, Example Hall\n\n1991 Beta Signal, Example Hall"
    pieces = split_cv(bare, len("1990 Alpha Signal, Example Hall"))
    assert pieces[1] == "1991 Beta Signal, Example Hall"
    assert not pieces[1].startswith("[continued")


def test_every_nonempty_line_appears_once_in_order():
    samples = [
        ("EXHIBITIONS\n\n1990 Example Show, Example Hall\n", 0),
        ("EXHIBITIONS\n\n1990 Example Show, Example Hall\n", 10_000),
        ("1990 AAAA\n\n1991 BBBB\n\n1992 CCCC", len("1990 AAAA\n\n1991 BBBB")),
        ("1990 one\n1991 two\n1992 three", len("1990 one\n1991 two")),
        ("1990 Keep\n\n" + ("1999 " + "X" * 40) + "\n\n1991 Next", 10),
        ("1990\n\n1991 BBBB\n1992 CCCC\n1993 DD", 20),
        ("H" * 60 + "\n\n" + "S" * 61 + "\n\n1991 Tail", 60),
        ("  PUBLICATIONS  \n\n1990 Paper, Example Journal\n\n1991 Talk, Example Hall", 40),
        ("1990 Only\n\n\n\n1991 Next", 8),
    ]
    for text, limit in samples:
        _assert_covers(text, limit)
