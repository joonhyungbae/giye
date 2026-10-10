# SPDX-License-Identifier: AGPL-3.0-only
"""MCP tools over one career bundle.

What: ``about``, ``career_schema``, ``position``, ``programme_profile``,
``next_steps``, ``field_trend``, ``map_question``, ``list_vocab``, and the
prompt ``read_my_career``. Every success carries the reading contract.

Why: a practitioner's own client asks the questions. The server only reads
the bundle it was given and forgets the entries when the call returns.

How to run: ``build_server`` is what ``python -m giye.mcp serve`` calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from giye.career.rules import C1, C2, C3, C3F, C4, C4R, C5, C6, C7, C8, C9, C10, C11, band_of
from giye.mcp.bundle import Bundle, load_bundle
from giye.mcp.career import MAX_REFERENCE_AGE, CareerEntry, assign_band, derive, validate_entries
from giye.mcp.contract import (
    ABOUT_CLAIM,
    MAP_CLAIM,
    NEXT_CLAIM,
    NEXT_EMPTY,
    NEXT_SUPPRESSED,
    NO_PUBLIC,
    POSITION_BOUND,
    POSITION_CLAIM,
    POSITION_SUPPRESSED,
    PROFILE_CLAIM,
    PROFILE_SUPPRESSED,
    SCHEMA_CLAIM,
    TOOL_RULE,
    TREND_CLAIM,
    TREND_SUPPRESSED,
    VOCAB_CLAIM,
    Contract,
    Interval,
    Populations,
    QuantileCell,
    contract_fields,
    fail,
    format_number,
    instructions_short,
    published_float,
    published_int,
    quantile_cell,
    reading_rules,
    respond,
    schema_instructions,
)
from giye.mcp.routing import route_question

_ANNOTATIONS = ToolAnnotations(read_only_hint=True, open_world_hint=False)

_POSITION_RULES = [C1, C2, C3, C3F, C4, C4R, C5, C6, C7, C9, "D1", "D2", "D3"]
_NEXT_RULES = [C9, C10, C11, "D1", "D4"]
_PROFILE_RULES = [C8, "D1", "D2", "D4"]
_TREND_RULES = ["D1", "D3", "D4"]


class AboutResult(Contract):
    """Boundary of the evidence and the headline populations."""

    boundary: str
    populations: Populations
    current_year: int
    k: int
    pct_min: int
    not_covered: list[str]
    reading_rules: str
    tools: list[str]


class CareerSchemaResult(Contract):
    """How to structure a CV. The server does not read the document."""

    entry_schema: dict[str, Any]
    kinds: list[str]
    programmes: list[dict[str, str]]
    instructions: str


class MeasureRow(BaseModel):
    """The caller's value beside the reference cell, as a band rather than a score."""

    measure: str
    value: float
    reference: QuantileCell
    band: str


class PositionResult(Contract):
    """Where the entries sit in the reference at the same age and generation."""

    career_age: int | None = None
    generation: str | None = None
    mix_bucket: str | None = None
    measures: list[MeasureRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProgrammeProfileResult(Contract):
    """One programme's profile, entry quantiles, and transitions, as published."""

    profile: dict[str, str]
    entry: list[dict[str, str]]
    transitions: list[dict[str, str]]


class OutcomeRow(BaseModel):
    """One next-window outcome, copied from the bundle."""

    age_band: str
    generation: str
    mix_bucket: str
    outcome: str
    n_people: str
    n_censored: str
    share: str
    share_lo: str
    share_hi: str
    w_share: str
    bound: str
    bound_value: str


class NextStepsResult(Contract):
    """Base rates of what followed at this band, generation and mix."""

    age_band: str
    generation: str
    mix_bucket: str | None = None
    outcomes: list[OutcomeRow] = Field(default_factory=list)
    n_censored: str = ""


class FieldTrendResult(Contract):
    """One measure across generations."""

    measure: str
    rows: list[dict[str, str]]


class MapQuestionResult(Contract):
    """Which tools answer a question, or the kind of source that would."""

    status: str
    tools: list[str]
    rule: str
    ask_instead: str | None = None
    note: str | None = None


class VocabResult(Contract):
    """The bundle vocabulary, unchanged."""

    vocab: dict[str, Any]


def _lang(requested: str | None, default: str) -> str:
    if requested in {"en", "ko"}:
        return requested
    return "ko" if default == "ko" else "en"


def _withheld(value: str) -> bool:
    return value in {"", "suppressed"}


def _position_sentence(
    lang: str,
    *,
    age: int,
    generation: str,
    n: int | None,
    k: int,
    cited: MeasureRow | None,
) -> str:
    key = "ko" if lang == "ko" else "en"
    if n is None:
        return POSITION_SUPPRESSED[key].format(age=age, generation=generation, k=k)
    assert cited is not None
    if cited.band == "no_reference" and cited.reference.bound == "yes":
        return POSITION_BOUND[key].format(
            age=age,
            generation=generation,
            n=n,
            measure=cited.measure,
            value=format_number(cited.value),
            bound_value=cited.reference.bound_value or ">=90",
        )
    return POSITION_CLAIM[key].format(
        age=age,
        generation=generation,
        n=n,
        measure=cited.measure,
        value=format_number(cited.value),
        band=cited.band,
    )


def _outcome(row: dict[str, str]) -> OutcomeRow:
    return OutcomeRow(
        age_band=row.get("age_band", ""),
        generation=row.get("generation", ""),
        mix_bucket=row.get("mix_bucket", ""),
        outcome=row.get("outcome", ""),
        n_people=row.get("n_people", ""),
        n_censored=row.get("n_censored", ""),
        share=row.get("share", ""),
        share_lo=row.get("share_lo", ""),
        share_hi=row.get("share_hi", ""),
        w_share=row.get("w_share", ""),
        bound=row.get("bound", ""),
        bound_value=row.get("bound_value", ""),
    )


def build_server(bundle_dir: str | Path, lang: str = "en") -> MCPServer:
    """Tools and the prompt, closed over ``bundle`` and nothing that outlives a call."""
    bundle: Bundle = load_bundle(bundle_dir)
    default_lang = "ko" if lang == "ko" else "en"
    tool_names = [
        "about",
        "career_schema",
        "position",
        "programme_profile",
        "next_steps",
        "field_trend",
        "map_question",
        "list_vocab",
    ]
    server = MCPServer(name="giye-career", instructions=instructions_short(default_lang))

    def _fields(chosen: str, **kwargs: Any) -> dict[str, Any]:
        return contract_fields(lang=chosen, bundle_version=bundle.version, pct_min=bundle.pct_min, **kwargs)

    @server.tool(
        annotations=_ANNOTATIONS,
        description="Boundary of the evidence, populations, and reading rules. Call first. " + TOOL_RULE,
    )
    def about(lang: str | None = None) -> AboutResult:
        chosen = _lang(lang, default_lang)
        counts = bundle.manifest["counts"]
        published = int(counts["published"])
        cv_layer = int(counts["cv_layer"])
        suppressed = bundle.suppressed_total()
        # The CV layer is the one the comparisons cite, and it is the smaller
        # population. Headline roster counts stay in ``populations``.
        sentence = ABOUT_CLAIM[chosen].format(
            version=bundle.version,
            published=published,
            cv=cv_layer,
            suppressed=suppressed,
            k=bundle.k,
        )
        payload = AboutResult(
            **_fields(
                chosen,
                layer="both",
                description="Roster layer and CV layer, as counted when the bundle was built.",
                n=cv_layer,
                suppressed=suppressed,
                claim=sentence,
                rules=["C0", "D1"],
                cell_suppressed=False,
                report_value={"published": published, "cv_layer": cv_layer},
            ),
            boundary=sentence,
            populations=Populations(published=published, cv_layer=cv_layer),
            current_year=bundle.current_year,
            k=bundle.k,
            pct_min=bundle.pct_min,
            not_covered=list(bundle.vocab.get("not_covered") or []),
            reading_rules=reading_rules(chosen),
            tools=list(tool_names),
        )
        return respond(payload, sentence)

    @server.tool(
        annotations=_ANNOTATIONS,
        description=(
            "JSON schema of CareerEntry. The client structures the CV; the server stores nothing. " + TOOL_RULE
        ),
    )
    def career_schema(lang: str | None = None) -> CareerSchemaResult:
        chosen = _lang(lang, default_lang)
        sentence = SCHEMA_CLAIM[chosen]
        payload = CareerSchemaResult(
            **_fields(
                chosen,
                layer="none",
                description="No population is cited. This is the shape of an entry, not a cell.",
                n=None,
                suppressed=bundle.suppressed_total(),
                claim=sentence,
                rules=[],
                cell_suppressed=False,
            ),
            entry_schema=CareerEntry.model_json_schema(),
            kinds=list(bundle.vocab["kinds"]),
            programmes=list(bundle.vocab["programmes"]),
            instructions=schema_instructions(),
        )
        return respond(payload, sentence)

    @server.tool(
        annotations=_ANNOTATIONS,
        description=(
            "Career age, generation, and each measure beside the reference quantiles at that age. "
            "Bands, not scores. " + TOOL_RULE
        ),
    )
    def position(entries: list[CareerEntry], lang: str | None = None) -> PositionResult:
        chosen = _lang(lang, default_lang)
        message = validate_entries(entries, kinds=list(bundle.vocab["kinds"]), programmes=bundle.programme_codes())
        if message:
            return fail(message)
        names = bundle.measure_names("reference_position")
        derived = derive(
            entries,
            territory=bundle.territory,
            programmes=list(bundle.vocab["programmes"]),
            measure_names=names,
        )
        if derived.first_year is None or derived.career_age is None or derived.generation is None:
            sentence = NO_PUBLIC[chosen]
            payload = PositionResult(
                **_fields(
                    chosen,
                    layer="none",
                    description="No dated public activity, so no reference cell is selected.",
                    n=None,
                    suppressed=bundle.suppressed_total(),
                    claim=sentence,
                    rules=[C1],
                    cell_suppressed=False,
                ),
                mix_bucket="none",
                notes=list(derived.notes),
            )
            return respond(payload, sentence)
        measures: list[MeasureRow] = []
        suppressed = 0
        cited: MeasureRow | None = None
        for name in names:
            row = bundle.reference_row(derived.career_age, derived.generation, name)
            cell = quantile_cell(row)
            if row is None or _withheld(cell.n_people):
                suppressed += 1
            # The bundle's quantiles are two decimals. Publish the same
            # precision; keep the band on the unrounded share so a cut that
            # sits between the two does not move.
            raw = derived.values.get(name, 0.0)
            item = MeasureRow(
                measure=name,
                value=round(raw, 2),
                reference=cell,
                band=assign_band(raw, cell.model_dump()),
            )
            measures.append(item)
            if cited is None and item.band != "no_reference":
                cited = item
        if cited is None and measures:
            cited = measures[0]
        n = published_int(measures[0].reference.n_people) if measures else None
        sentence = _position_sentence(
            chosen,
            age=derived.career_age,
            generation=derived.generation,
            n=n,
            k=bundle.k,
            cited=cited,
        )
        payload = PositionResult(
            **_fields(
                chosen,
                layer="cv",
                description=f"CV-layer careers at career age {derived.career_age}, generation {derived.generation}.",
                n=n,
                suppressed=suppressed,
                claim=sentence,
                rules=_POSITION_RULES,
                cell_suppressed=n is None,
                report_value=None if cited is None else {cited.measure: cited.value},
            ),
            career_age=derived.career_age,
            generation=derived.generation,
            mix_bucket=derived.mix,
            measures=measures,
            notes=list(derived.notes),
        )
        return respond(payload, sentence)

    @server.tool(
        annotations=_ANNOTATIONS,
        description=(
            "One programme's profile, entry quantiles, and transitions, listed by programme code. " + TOOL_RULE
        ),
    )
    def programme_profile(programme: str, lang: str | None = None) -> ProgrammeProfileResult:
        chosen = _lang(lang, default_lang)
        codes = bundle.programme_codes()
        if programme not in codes:
            return fail("programme is not in the vocabulary. Accepted programmes: " + ", ".join(codes))
        profile = bundle.profile_row(programme) or {"programme": programme, "n_people": "suppressed"}
        entry = bundle.entry_rows(programme)
        transitions = bundle.transition_rows(programme)
        suppressed = 0
        if _withheld(profile.get("n_people", "")):
            suppressed += 1
        suppressed += sum(1 for row in entry if _withheld(row.get("n_people", "")))
        suppressed += sum(
            1
            for row in transitions
            if _withheld(row.get("n_at_risk", "")) or _withheld(row.get("n_movers", ""))
        )
        n = published_int(profile.get("n_people", ""))
        if n is None:
            sentence = PROFILE_SUPPRESSED[chosen].format(code=programme, k=bundle.k)
        else:
            sentence = PROFILE_CLAIM[chosen].format(code=programme, n=n)
        payload = ProgrammeProfileResult(
            **_fields(
                chosen,
                layer="both",
                description=f"Roster-layer people on {programme}, plus the CV-layer entry profile.",
                n=n,
                suppressed=suppressed,
                claim=sentence,
                rules=_PROFILE_RULES,
                cell_suppressed=n is None,
                report_value=n,
            ),
            profile=profile,
            entry=entry,
            transitions=transitions,
        )
        return respond(payload, sentence)

    @server.tool(
        annotations=_ANNOTATIONS,
        description=(
            "Base rates of what followed in reference careers at the same age band and mix. " + TOOL_RULE
        ),
    )
    def next_steps(
        entries: list[CareerEntry] | None = None,
        career_age: int | None = None,
        generation: str | None = None,
        mix_bucket: str | None = None,
        lang: str | None = None,
    ) -> NextStepsResult:
        chosen = _lang(lang, default_lang)
        age = career_age
        gen = generation
        mix = mix_bucket or None
        if entries:
            message = validate_entries(entries, kinds=list(bundle.vocab["kinds"]), programmes=bundle.programme_codes())
            if message:
                return fail(message)
            derived = derive(
                entries,
                territory=bundle.territory,
                programmes=list(bundle.vocab["programmes"]),
                measure_names=bundle.measure_names("reference_position"),
            )
            if derived.first_year is None or derived.career_age is None or derived.generation is None:
                sentence = NO_PUBLIC[chosen]
                payload = NextStepsResult(
                    **_fields(
                        chosen,
                        layer="none",
                        description="No dated public activity, so no next-window cell is selected.",
                        n=None,
                        suppressed=bundle.suppressed_total(),
                        claim=sentence,
                        rules=[C1],
                        cell_suppressed=False,
                    ),
                    age_band="",
                    generation="",
                    mix_bucket="none",
                )
                return respond(payload, sentence)
            # Entries win when both forms are sent: the mix has to come from the career.
            age = derived.career_age
            gen = derived.generation
            mix = derived.mix
        if age is None or not gen:
            return fail("next_steps needs entries, or career_age and generation.")
        # The reference stops at the last published age. An explicit age past
        # that still selects the last band; position is what records the note.
        age = min(age, MAX_REFERENCE_AGE)
        if age < 0:
            return fail(f"career_age must be between 0 and {MAX_REFERENCE_AGE}.")
        buckets = list(bundle.vocab.get("mix_buckets") or [])
        if mix and mix not in buckets:
            return fail("mix_bucket is not in the vocabulary. Accepted mix buckets: " + ", ".join(buckets))
        band = band_of(age)
        if band is None:
            return fail(f"career_age must be between 0 and {MAX_REFERENCE_AGE}.")
        rows = bundle.next_rows(band, gen, mix)
        outcomes = [_outcome(row) for row in rows]
        suppressed = sum(1 for row in outcomes if _withheld(row.n_people))
        cited = next((row for row in outcomes if not _withheld(row.n_people)), None)
        n = published_int(cited.n_people) if cited else None
        if not outcomes:
            sentence = NEXT_EMPTY[chosen].format(band=band, generation=gen)
        elif cited is None:
            sentence = NEXT_SUPPRESSED[chosen].format(band=band, generation=gen, k=bundle.k)
        else:
            sentence = NEXT_CLAIM[chosen].format(
                band=band,
                generation=gen,
                mix=cited.mix_bucket,
                n=cited.n_people,
                outcome=cited.outcome,
                share=cited.share or "suppressed",
            )
        censored = outcomes[0].n_censored if len({row.n_censored for row in outcomes}) == 1 and outcomes else ""
        interval = None
        if cited is not None:
            lo = published_float(cited.share_lo)
            hi = published_float(cited.share_hi)
            if lo is not None and hi is not None:
                interval = Interval(lo=lo, hi=hi)
        payload = NextStepsResult(
            **_fields(
                chosen,
                layer="cv",
                description=f"CV-layer careers in age band {band}, generation {gen}.",
                n=n,
                suppressed=suppressed,
                claim=sentence,
                rules=_NEXT_RULES,
                cell_suppressed=bool(outcomes) and cited is None,
                report_value=None if cited is None else {cited.outcome: cited.share},
                interval=interval,
            ),
            age_band=band,
            generation=gen,
            mix_bucket=mix,
            outcomes=outcomes,
            n_censored=censored,
        )
        return respond(payload, sentence)

    @server.tool(
        annotations=_ANNOTATIONS,
        description="Generation-level trend for one measure, with the published interval. " + TOOL_RULE,
    )
    def field_trend(measure: str, lang: str | None = None) -> FieldTrendResult:
        chosen = _lang(lang, default_lang)
        accepted = bundle.measure_names("field_trend")
        if measure not in accepted:
            return fail("measure is not in the vocabulary. Accepted measures: " + ", ".join(accepted))
        found = bundle.trend_rows(measure) or []
        suppressed = sum(1 for row in found if _withheld(row.get("n_people", "")))
        numbers = [published_int(row.get("n_people", "")) for row in found]
        published = [number for number in numbers if number is not None]
        n = min(published) if published else None
        if n is None:
            sentence = TREND_SUPPRESSED[chosen].format(measure=measure, k=bundle.k)
        else:
            sentence = TREND_CLAIM[chosen].format(measure=measure, n=n)
        interval = None
        for row in found:
            lo = published_float(row.get("mean_lo", ""))
            hi = published_float(row.get("mean_hi", ""))
            if lo is not None and hi is not None:
                interval = Interval(lo=lo, hi=hi)
                break
        payload = FieldTrendResult(
            **_fields(
                chosen,
                layer="cv",
                description=f"CV-layer people in each generation, for {measure}.",
                n=n,
                suppressed=suppressed,
                claim=sentence,
                rules=_TREND_RULES,
                cell_suppressed=n is None,
                report_value=n,
                interval=interval,
            ),
            measure=measure,
            rows=found,
        )
        return respond(payload, sentence)

    @server.tool(
        annotations=_ANNOTATIONS,
        description=(
            "Route a question to tools, or say it is not covered and what kind of source would answer. " + TOOL_RULE
        ),
    )
    def map_question(text: str, lang: str | None = None) -> MapQuestionResult:
        chosen = _lang(lang, default_lang)
        rule = route_question(text)
        tools = list(rule.tools)
        note = rule.note
        ask = rule.ask_instead
        template = MAP_CLAIM[chosen][rule.status]
        sentence = template.format(tools=", ".join(tools), note=note or "", ask_instead=ask or "")
        payload = MapQuestionResult(
            **_fields(
                chosen,
                layer="none",
                description="No population is cited. This routes the question.",
                n=None,
                suppressed=bundle.suppressed_total(),
                claim=sentence,
                rules=[rule.id],
                cell_suppressed=False,
            ),
            status=rule.status,
            tools=tools,
            rule=rule.id,
            ask_instead=ask,
            note=note,
        )
        return respond(payload, sentence)

    @server.tool(
        annotations=_ANNOTATIONS,
        description="Accepted kinds, programmes, measures, and regions. " + TOOL_RULE,
    )
    def list_vocab(lang: str | None = None) -> VocabResult:
        chosen = _lang(lang, default_lang)
        sentence = VOCAB_CLAIM[chosen]
        payload = VocabResult(
            **_fields(
                chosen,
                layer="none",
                description="No population is cited. This is the vocabulary of the bundle.",
                n=None,
                suppressed=bundle.suppressed_total(),
                claim=sentence,
                rules=list(bundle.manifest.get("rule_ids") or []),
                cell_suppressed=False,
            ),
            vocab=bundle.vocab,
        )
        return respond(payload, sentence)

    @server.prompt(title="Read my career")
    def read_my_career(lang: str = "en") -> str:
        """How to read a result. The user selects this; it is not hidden in a tool result."""
        return reading_rules(_lang(lang, "en"))

    return server
