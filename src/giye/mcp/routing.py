# SPDX-License-Identifier: AGPL-3.0-only
"""Route a free-text question to tools, or say it is not covered.

What: an ordered table of rules Q1–Q9. The first pattern that matches, in
either language, wins. Money, admissions and selection chances are not covered.
Anything else that matches a covered question names the tools to call.

Why: the server must not answer from general knowledge under its own name.
A question the archive cannot hold is a routing result, not a guess.

How to run: imported by ``python -m giye.mcp serve``. This module is not a
script.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionRule:
    """One routing rule. Patterns are searched, not anchored: the spec gives them that way."""

    id: str
    pattern_en: str
    pattern_ko: str
    status: str
    tools: tuple[str, ...]
    ask_instead: str | None
    note: str | None = None


# Order is the priority. Not-covered topics come first so "what are my chances
# of moving" is not read as a transition question. Q4 is before the generic
# "where do I stand" rule.
RULES: tuple[QuestionRule, ...] = (
    QuestionRule(
        "Q1",
        r"fee|salary|contract|pay",
        r"계약|수수료|보수|급여",
        "not_covered",
        (),
        "a contract or pay survey such as an artists' labour survey",
    ),
    QuestionRule(
        "Q2",
        r"admission|PhD|MFA",
        r"대학원|입학|박사|석사",
        "not_covered",
        (),
        # The spec names the topic and not the source sentence. This names the
        # kind of source that would hold an admissions decision.
        "the degree programme's own admissions notice",
    ),
    QuestionRule(
        "Q3",
        r"chance|odds|advance|succeed",
        r"가능성|확률|성공",
        "not_covered",
        (),
        "the archive holds no applications, rejections or success outcome",
    ),
    QuestionRule(
        "Q4",
        r"which programmes took people like me|people like me|programmes like mine",
        r"나 같은|나와 비슷한|비슷한 사람",
        "partially_covered",
        ("position", "programme_profile"),
        None,
        (
            "Selection chances are not covered. programme_profile lists entry profiles by programme code "
            "and is not a ranking."
        ),
    ),
    QuestionRule(
        "Q5",
        r"stand|compare|position",
        r"위치|어디쯤",
        "covered",
        ("career_schema", "position"),
        None,
    ),
    QuestionRule(
        "Q6",
        r"next|after|following",
        r"다음|이후",
        "covered",
        ("next_steps",),
        None,
    ),
    QuestionRule(
        "Q7",
        r"trend|changing|over time",
        r"변화|추세",
        "covered",
        ("field_trend",),
        None,
    ),
    QuestionRule(
        "Q8",
        r"move|transition",
        r"이동|옮기",
        "covered",
        ("programme_profile",),
        None,
    ),
)

FALLTHROUGH = QuestionRule(
    "Q9",
    "",
    "",
    "not_covered",
    (),
    "rephrase as one of: where my career stands, what followed similar careers, a programme's profile, a field trend",
)


def route_question(text: str) -> QuestionRule:
    """The first matching rule, or Q9 when nothing matches."""
    for rule in RULES:
        if rule.pattern_en and re.search(rule.pattern_en, text, re.IGNORECASE):
            return rule
        if rule.pattern_ko and re.search(rule.pattern_ko, text):
            return rule
    return FALLTHROUGH
