# SPDX-License-Identifier: AGPL-3.0-only
"""The career MCP server lists its tools and answers from a synthetic bundle."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.client import Client

from giye.career.build import build
from giye.career.rules import band_start
from giye.cli import main
from giye.config import load
from giye.mcp import build_server
from tests.career_synth import synthetic_archive

TOOLS = {
    "about",
    "career_schema",
    "position",
    "programme_profile",
    "next_steps",
    "field_trend",
    "map_question",
    "list_vocab",
}
CONTRACT = {
    "layer",
    "population",
    "report_value",
    "interval",
    "suppressed",
    "must_say",
    "forbidden_paraphrase",
    "generalization_allowed",
    "claim_template",
    "bundle_version",
    "rules",
    "lang",
}
ENTRY_FIELDS = {"year", "kind", "venue", "city", "country", "programme", "role"}


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("mcp-server")
    config = synthetic_archive(root / "archive")
    out = root / "bundle"
    build(load(config), out)
    return out


def _early_domestic() -> list[dict[str, object]]:
    """Six dated public activities, all in the home country."""
    kinds = [
        "group_exhibition",
        "screening",
        "talk_workshop",
        "group_exhibition",
        "performance",
        "publication_press",
    ]
    return [
        {"year": 2018 + (index // 3), "kind": kind, "country": "KR", "city": "Seoul", "venue": "Hall"}
        for index, kind in enumerate(kinds)
    ]


def test_mcp_serve_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["mcp", "serve", "--help"])
    assert caught.value.code == 0
    assert "--bundle" in capsys.readouterr().out


def test_tools_prompt_about_and_schema(bundle_dir: Path) -> None:
    server = build_server(bundle_dir)
    manifest = load_manifest(bundle_dir)

    async def run() -> tuple[object, object, object, object]:
        async with Client(server) as client:
            tools = await client.list_tools()
            prompts = await client.list_prompts()
            about = await client.call_tool("about", {})
            schema = await client.call_tool("career_schema", {})
            prompt = await client.get_prompt("read_my_career", {"lang": "en"})
            return tools, prompts, about, schema, prompt

    tools, prompts, about, schema, prompt = asyncio.run(run())
    names = {tool.name for tool in tools.tools}
    assert names == TOOLS
    for tool in tools.tools:
        assert tool.output_schema is not None
        assert CONTRACT <= set(tool.output_schema["properties"])
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is False
    assert any(item.name == "read_my_career" for item in prompts.prompts)
    assert prompt.messages[0].role == "user"
    assert "about" in prompt.messages[0].content.text

    body = about.structured_content
    assert about.content[0].text == body["claim_template"]
    assert body["populations"]["published"] == manifest["counts"]["published"]
    assert body["populations"]["cv_layer"] == manifest["counts"]["cv_layer"]
    assert body["k"] == manifest["k"]
    assert body["pct_min"] == manifest["pct_min"]
    schema_body = schema.structured_content
    assert ENTRY_FIELDS <= set(schema_body["entry_schema"]["properties"])
    assert "stores nothing" in schema_body["instructions"]


def test_position_bands_and_long_career(bundle_dir: Path) -> None:
    server = build_server(bundle_dir)

    async def run() -> tuple[object, object]:
        async with Client(server) as client:
            early = await client.call_tool("position", {"entries": _early_domestic()})
            long = await client.call_tool(
                "position",
                {
                    "entries": [
                        {"year": 1990, "kind": "group_exhibition", "country": "KR"},
                        {"year": 2025, "kind": "group_exhibition", "country": "KR"},
                    ]
                },
            )
            return early, long

    early, long = asyncio.run(run())
    body = early.structured_content
    assert early.content[0].text == body["claim_template"]
    assert body["layer"] == "cv"
    assert body["measures"]
    assert all(item["band"] for item in body["measures"])
    home = next(item for item in body["measures"] if item["measure"] == "country_share:home")
    assert home["value"] == 1.0
    late = long.structured_content
    assert late["career_age"] == 30
    assert "beyond_reference" in late["notes"]


def test_position_values_use_two_decimals(bundle_dir: Path) -> None:
    """Four of seven rows is 4/7. The published value is 0.57; the band used 4/7."""
    entries = [{"year": 2018, "kind": "solo_exhibition", "country": "KR"} for _ in range(4)]
    entries += [{"year": 2018, "kind": "group_exhibition", "country": "KR"} for _ in range(3)]
    server = build_server(bundle_dir)

    async def run() -> object:
        async with Client(server) as client:
            return await client.call_tool("position", {"entries": entries})

    result = asyncio.run(run())
    body = result.structured_content
    solo = next(item for item in body["measures"] if item["measure"] == "kind_share:solo_exhibition")
    assert solo["value"] == 0.57
    assert all(item["value"] == round(item["value"], 2) for item in body["measures"])
    reported = body["report_value"]
    assert reported == {
        key: next(item["value"] for item in body["measures"] if item["measure"] == key) for key in reported
    }


def test_next_steps_matches_the_bundle(bundle_dir: Path) -> None:
    with (bundle_dir / "next_window.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    sample = rows[0]
    age = band_start(sample["age_band"])
    generation = sample["generation"]
    expected = [row for row in rows if row["age_band"] == sample["age_band"] and row["generation"] == generation]
    server = build_server(bundle_dir)

    async def run() -> object:
        async with Client(server) as client:
            return await client.call_tool("next_steps", {"career_age": age, "generation": generation})

    result = asyncio.run(run())
    assert not result.is_error
    got = result.structured_content["outcomes"]
    assert len(got) == len(expected)
    for left, right in zip(got, expected, strict=True):
        assert left["outcome"] == right["outcome"]
        assert left["share"] == right["share"]
        assert left["n_people"] == right["n_people"]
        assert left["mix_bucket"] == right["mix_bucket"]
        assert left["n_censored"] == right["n_censored"]


def test_programme_profile_and_errors(bundle_dir: Path) -> None:
    import json

    vocab = json.loads((bundle_dir / "vocab.json").read_text(encoding="utf-8"))
    codes = [item["code"] for item in vocab["programmes"]]
    server = build_server(bundle_dir)

    async def run() -> list[object]:
        async with Client(server) as client:
            profiles = [
                await client.call_tool("programme_profile", {"programme": code}) for code in codes
            ]
            unknown = await client.call_tool("programme_profile", {"programme": "NO-SUCH"})
            bad_kind = await client.call_tool(
                "position",
                {"entries": [{"year": 2018, "kind": "not_a_kind", "country": "KR"}]},
            )
            bad_measure = await client.call_tool("field_trend", {"measure": "not_a_measure"})
            return [*profiles, unknown, bad_kind, bad_measure]

    *profiles, unknown, bad_kind, bad_measure = asyncio.run(run())
    for code, result in zip(codes, profiles, strict=True):
        assert not result.is_error
        assert result.structured_content["profile"]["programme"] == code
    assert unknown.is_error
    unknown_text = unknown.content[0].text
    assert "Traceback" not in unknown_text
    for code in codes:
        assert code in unknown_text
    assert bad_kind.is_error
    kind_text = bad_kind.content[0].text
    assert "Traceback" not in kind_text
    assert "solo_exhibition" in kind_text
    assert bad_measure.is_error
    assert "funding_share" in bad_measure.content[0].text
    assert "Traceback" not in bad_measure.content[0].text


def load_manifest(bundle_dir: Path) -> dict:
    import json

    return json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
