# SPDX-License-Identifier: AGPL-3.0-only
"""The reading contract on every successful tool result.

What: the fields a client must be able to quote (layer, denominator, the
finished sentence, the paraphrases that are not allowed), in English and
Korean, and the helper that puts that payload on the wire.

Why: models drop qualifiers and ignore instructions found only in tool
results. The sentence is first, the same rules are data, and the prompt is a
separate channel the user selects.

How to run: imported by ``python -m giye.mcp serve``. This module is not a
script.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

Layer = Literal["roster", "cv", "both", "none"]
Lang = Literal["en", "ko"]

# Fixed strings. The English lines are the phase 2 spec; the Korean lines say
# the same thing. A call's lang picks one set. The advice line is always
# included. The small-cell line is included only when generalising is not
# allowed.
_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "roster": "The roster layer is a census of everyone on the field's admitted programme rosters.",
        "cv": (
            "The CV layer is the minority of people whose public CV was extracted; it is biased "
            "toward people active across several programmes, and weighted values are shown beside "
            "unweighted ones."
        ),
        "advice": "These are descriptions of what careers in the archive did, not advice about what to do.",
        "small": "The cell is too small to generalise; report the denominator.",
    },
    "ko": {
        "roster": "명단 층은 이 분야의 인정된 프로그램 명단에 오른 모든 사람의 전수입니다.",
        "cv": (
            "이력 층은 공개 이력이 추출된 소수이며, 여러 프로그램에 걸쳐 활동한 사람에게 기울어 있고, "
            "가중 값은 비가중 값 옆에 보여 줍니다."
        ),
        "advice": "이것은 아카이브의 경력이 한 일에 대한 기술이며, 무엇을 해야 하는지에 대한 조언이 아닙니다.",
        "small": "칸이 너무 작아 일반화할 수 없습니다. 분모를 함께 적으십시오.",
    },
}

# Both languages, every call. The client is told the phrases in the language
# it is not using as well, so a later paraphrase in either language is listed.
FORBIDDEN: tuple[str, ...] = (
    "increases your chances",
    "to advance your career",
    "you should",
    "we recommend",
    "the successful path",
    "will lead to",
    "better artists",
    "ranked",
    "가능성이 높아진다",
    "유리하다",
    "해야 한다",
    "추천",
    "성공 경로",
    "이어진다",
    "더 나은 작가",
    "순위",
)


def reading_rules(lang: str) -> str:
    """The user-selected prompt: how to read a result, in ``lang``."""
    if lang == "ko":
        return (
            "먼저 about을 호출하십시오. career_schema로 이력은 직접 구조화하고 항목만 보내십시오. "
            "그다음 position을 호출하십시오. 각 결과의 첫 문장을 그대로 인용하고, must_say를 모두 포함하고, "
            "forbidden_paraphrase는 하나도 쓰지 마십시오. 층과 분모를 밝히고, 사람을 순위 매기거나 추천하지 마십시오."
        )
    return (
        "Call about first. Structure the CV with career_schema and send only entries. "
        "Then call position. Quote the first sentence of each result as it is, include every must_say, "
        "and use none of forbidden_paraphrase. Name the layer and the denominator. Never rank or recommend people."
    )


def instructions_short(lang: str) -> str:
    """Server instructions: the same rules, short enough to sit on the handshake."""
    if lang == "ko":
        return (
            "기술만 한다. 첫 문장이 인용할 문장이다. must_say를 유지하고 forbidden_paraphrase를 쓰지 않는다. "
            "층과 분모를 밝힌다. 조언, 순위, 추천은 하지 않는다. 서버는 항목을 저장하지 않는다."
        )
    return (
        "Descriptive only. The first sentence is the one to quote. Keep every must_say and use no "
        "forbidden paraphrase. Name the layer and the denominator. Do not advise, rank, or recommend. "
        "The server stores no entries."
    )


def allows_generalization(*, layer: str, n: int | None, pct_min: int, cell_suppressed: bool) -> bool:
    """False when the cited cell is withheld, below ``pct_min``, or there is no layer."""
    blocked = layer == "none" or cell_suppressed or n is None or (n is not None and n < pct_min)
    return not blocked


def must_say(lang: str, layer: str, generalization_allowed: bool) -> list[str]:
    """Layer sentence (when a layer is cited), the advice line, and the small-cell line."""
    text = _TEXT["ko" if lang == "ko" else "en"]
    sentences: list[str] = []
    if layer == "roster":
        sentences.append(text["roster"])
    elif layer == "cv":
        sentences.append(text["cv"])
    elif layer == "both":
        sentences.append(text["roster"])
        sentences.append(text["cv"])
    sentences.append(text["advice"])
    if not generalization_allowed:
        sentences.append(text["small"])
    return sentences


class Population(BaseModel):
    """Who is counted, and the published n (rounded cell count, or None when withheld)."""

    description: str
    n: int | None = None


class Interval(BaseModel):
    """A published interval. Bounds are the numbers to cite, already rounded in the bundle."""

    lo: float
    hi: float


class Contract(BaseModel):
    """Fields present on every successful tool payload."""

    layer: Layer
    population: Population
    report_value: Any = None
    interval: Interval | None = None
    suppressed: int
    must_say: list[str]
    forbidden_paraphrase: list[str]
    generalization_allowed: bool
    claim_template: str
    bundle_version: str
    rules: list[str]
    lang: Lang


def contract_fields(
    *,
    lang: str,
    layer: str,
    description: str,
    n: int | None,
    suppressed: int,
    claim: str,
    rules: list[str],
    bundle_version: str,
    pct_min: int,
    cell_suppressed: bool,
    report_value: Any = None,
    interval: Interval | None = None,
) -> dict[str, Any]:
    """The contract kwargs for a payload model. ``claim`` becomes both sentence and template."""
    chosen: Lang = "ko" if lang == "ko" else "en"
    layer_name: Layer = layer if layer in {"roster", "cv", "both", "none"} else "none"
    allowed = allows_generalization(layer=layer_name, n=n, pct_min=pct_min, cell_suppressed=cell_suppressed)
    return {
        "layer": layer_name,
        "population": Population(description=description, n=n),
        "report_value": report_value,
        "interval": interval,
        "suppressed": suppressed,
        "must_say": must_say(chosen, layer_name, allowed),
        "forbidden_paraphrase": list(FORBIDDEN),
        "generalization_allowed": allowed,
        "claim_template": claim,
        "bundle_version": bundle_version,
        "rules": rules,
        "lang": chosen,
    }


def respond(payload: BaseModel, sentence: str) -> CallToolResult:
    """Two text blocks, sentence first, then the JSON payload. The template is the sentence.

    The return annotation of the tool is the payload model, so the SDK publishes
    that model's schema. This helper returns a ``CallToolResult`` the SDK
    validates against the model and does not rewrite.
    """
    data = payload.model_dump(mode="json")
    data["claim_template"] = sentence
    return CallToolResult(
        content=[
            TextContent(type="text", text=sentence),
            TextContent(type="text", text=json.dumps(data, ensure_ascii=False)),
        ],
        structured_content=data,
    )


def fail(text: str) -> CallToolResult:
    """A validation or lookup error the model can read. No traceback."""
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def format_number(value: float) -> str:
    """Two decimals, trailing zeros dropped, matching the bundle's share format."""
    rounded = round(float(value), 2)
    if rounded == 0:
        return "0"
    text = f"{rounded:.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


# Claim templates. ``{n}`` is the published count. Suppressed cells use the
# second sentence, which names k instead of a comparison.
POSITION_CLAIM = {
    "en": (
        "At career age {age} ({generation}), {n} CV-layer careers form the reference; "
        "your {measure} is {value}, inside the {band} of that reference."
    ),
    "ko": (
        "경력 연령 {age} ({generation})에서 참조는 이력 층 경력 {n}명입니다. "
        "당신의 {measure}는 {value}이며, 그 참조의 {band} 안에 있습니다."
    ),
}
POSITION_SUPPRESSED = {
    "en": "At career age {age} ({generation}) the reference has fewer than {k} people, so no comparison is published.",
    "ko": "경력 연령 {age} ({generation})의 참조는 {k}명 미만이어서 비교를 공개하지 않습니다.",
}
POSITION_BOUND = {
    "en": (
        "At career age {age} ({generation}), {n} CV-layer careers form the reference; "
        "your {measure} is {value}, and the reference reports a bound of {bound_value} instead of quantiles."
    ),
    "ko": (
        "경력 연령 {age} ({generation})에서 참조는 이력 층 경력 {n}명입니다. "
        "당신의 {measure}는 {value}이며, 참조는 분위수 대신 {bound_value} 경계를 보고합니다."
    ),
}
NO_PUBLIC = {
    "en": "A dated public activity is needed before a career age can be read.",
    "ko": "경력 연령을 읽으려면 날짜가 있는 공개 활동이 필요합니다.",
}
NEXT_CLAIM = {
    "en": (
        "In the three years after age band {band} ({generation}, mix {mix}), {n} CV-layer careers "
        "are the reference; the share for {outcome} is {share}."
    ),
    "ko": (
        "연령 구간 {band} ({generation}, 구성 {mix}) 이후 3년의 참조는 이력 층 {n}명입니다. "
        "{outcome}의 비율은 {share}입니다."
    ),
}
NEXT_SUPPRESSED = {
    "en": "After age band {band} ({generation}) the reference has fewer than {k} people, so no base rate is published.",
    "ko": "연령 구간 {band} ({generation})의 참조는 {k}명 미만이어서 그 다음 비율을 공개하지 않습니다.",
}
NEXT_EMPTY = {
    "en": "No next-window cell is published for age band {band} ({generation}).",
    "ko": "연령 구간 {band} ({generation})에 공개된 다음 구간 칸이 없습니다.",
}
PROFILE_CLAIM = {
    "en": "Programme {code} counts {n} people on the roster layer. Entry and transitions carry their own denominators.",
    "ko": "프로그램 {code}의 명단 층 인원은 {n}명입니다. 진입과 이동은 각각의 분모를 가집니다.",
}
PROFILE_SUPPRESSED = {
    "en": "Programme {code} has fewer than {k} people in the published profile, so those counts are withheld.",
    "ko": "프로그램 {code}의 공개 프로필은 {k}명 미만이어서 그 인원을 밝히지 않습니다.",
}
TREND_CLAIM = {
    "en": "The CV-layer trend for {measure} cites {n} people in the smallest published generation cell.",
    "ko": "{measure}의 이력 층 추세는 공개된 세대 칸 중 가장 작은 칸의 {n}명을 인용합니다.",
}
TREND_SUPPRESSED = {
    "en": "The field trend for {measure} is withheld: every generation cell has fewer than {k} people.",
    "ko": "{measure}의 분야 추세는 공개하지 않습니다. 모든 세대 칸이 {k}명 미만입니다.",
}
ABOUT_CLAIM = {
    "en": (
        "This bundle ({version}) describes aggregate careers. The roster layer counts {published} people "
        "and the CV layer counts {cv}. It withholds {suppressed} cells under k={k}."
    ),
    "ko": (
        "이 번들({version})은 집계된 경력을 기술합니다. 명단 층은 {published}명, 이력 층은 {cv}명입니다. "
        "k={k} 미만인 칸 {suppressed}개를 공개하지 않습니다."
    ),
}
SCHEMA_CLAIM = {
    "en": "Structure the CV yourself and send only entries. The server stores nothing.",
    "ko": "이력은 직접 구조화하고 항목만 보내십시오. 서버는 아무것도 저장하지 않습니다.",
}
VOCAB_CLAIM = {
    "en": "The vocabulary lists accepted kinds, programmes, measures, and the topics this archive does not cover.",
    "ko": "어휘는 받는 종류, 프로그램, 측정값, 그리고 이 아카이브가 다루지 않는 주제를 나열합니다.",
}
MAP_CLAIM = {
    "en": {
        "covered": "This question is covered. Call {tools}.",
        "partially_covered": "This question is only partly covered. Call {tools}. {note}",
        "not_covered": "This question is not covered. Ask instead: {ask_instead}",
    },
    "ko": {
        "covered": "이 질문은 다룹니다. {tools}를 호출하십시오.",
        "partially_covered": "이 질문은 일부만 다룹니다. {tools}를 호출하십시오. {note}",
        "not_covered": "이 질문은 다루지 않습니다. 대신 이렇게 물어보십시오: {ask_instead}",
    },
}


class Populations(BaseModel):
    """Headline counts from the manifest. These are build counts, not a cell."""

    published: int
    cv_layer: int


# Re-exported so tool modules share one description suffix.
TOOL_RULE = (
    "Descriptive only: quote the first sentence, keep every must_say, and do not use a forbidden paraphrase. "
    "No person is named, ranked, or recommended."
)


class QuantileCell(BaseModel):
    """One published quantile row. Strings, because suppressed cells are not numbers."""

    n_people: str = ""
    q10: str = ""
    q25: str = ""
    q50: str = ""
    q75: str = ""
    q90: str = ""
    w_q10: str = ""
    w_q25: str = ""
    w_q50: str = ""
    w_q75: str = ""
    w_q90: str = ""
    bound: str = ""
    bound_value: str = ""


_QUANTILE_FIELDS = (
    "n_people",
    "q10",
    "q25",
    "q50",
    "q75",
    "q90",
    "w_q10",
    "w_q25",
    "w_q50",
    "w_q75",
    "w_q90",
    "bound",
    "bound_value",
)


def quantile_cell(row: dict[str, str] | None) -> QuantileCell:
    """Copy a bundle row. A missing row is an empty cell, not a zero."""
    if row is None:
        return QuantileCell(n_people="suppressed")
    return QuantileCell(**{name: row.get(name, "") for name in _QUANTILE_FIELDS})


def published_int(value: str) -> int | None:
    """A published count, or None when the cell is withheld or empty."""
    if not value or value in {"suppressed", ">=90"}:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def published_float(value: str) -> float | None:
    """A published share or quantile, or None when it was not written."""
    if not value or value in {"suppressed", ">=90"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def schema_instructions() -> str:
    """Tell the client model to structure the CV. The server never sees the document."""
    return (
        "Read the CV in the conversation and turn it into CareerEntry objects yourself. "
        "Send only those entries (year, kind, and optional venue, city, country, programme, role). "
        "Do not send the CV document. The server stores nothing from the request."
    )

