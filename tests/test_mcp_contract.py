# SPDX-License-Identifier: AGPL-3.0-only
"""Every scenario result carries the reading contract, and a small bundle does not generalise."""

from __future__ import annotations

import asyncio
import csv
import json
import re
import socket
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.client import Client

from giye.career.build import build
from giye.career.rules import band_start
from giye.config import load
from giye.demo import run_demo
from giye.mcp import build_server
from tests.career_synth import synthetic_archive

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo" / "giye.toml"
CLOCK = datetime(2026, 1, 15, tzinfo=timezone.utc)
_BAD = re.compile(r"GY-\d{6}|CAND-|cv:|https?://")
_HANGUL = re.compile(r"[\uac00-\ud7a3]")


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("mcp-contract")
    config = synthetic_archive(root / "archive")
    out = root / "bundle"
    build(load(config), out)
    return out


def _career(stage: str, place: str, form: str) -> list[dict[str, object]]:
    start = {"early": 2018, "mid": 2008, "late": 1996}[stage]
    span = {"early": 2, "mid": 10, "late": 25}[stage]
    country = "KR" if place == "domestic" else "US"
    if form == "single":
        kinds = ["group_exhibition"] * 6
    else:
        # Six kinds, four families, none at half or more, so the mix is mixed.
        kinds = ["group_exhibition", "screening", "performance", "talk_workshop", "funding", "festival"]
    rows = []
    for index, kind in enumerate(kinds):
        rows.append(
            {
                "year": start + (span * index) // 5,
                "kind": kind,
                "country": country,
                "venue": f"Hall {index}",
                "city": "City",
            }
        )
    return rows


def _scenarios() -> list[tuple[str, list[dict[str, object]]]]:
    rows = []
    for stage in ("early", "mid", "late"):
        for place in ("domestic", "overseas"):
            for form in ("single", "mixed"):
                rows.append((f"{stage}-{place}-{form}", _career(stage, place, form)))
    return rows


def _assert_contract(result: object, pct_min: int) -> None:
    assert not result.is_error
    body = result.structured_content
    assert result.content[0].text == body["claim_template"]
    assert len(body["must_say"]) >= 2
    forbidden = " ".join(body["forbidden_paraphrase"])
    assert "you should" in forbidden
    assert "추천" in forbidden
    number = body["population"]["n"]
    if number is None or number < pct_min:
        assert body["generalization_allowed"] is False
    blob = "\n".join(block.text for block in result.content)
    assert not _BAD.search(blob)


def test_scenario_contract(bundle_dir: Path) -> None:
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    pct_min = int(manifest["pct_min"])
    server = build_server(bundle_dir)
    careers = _scenarios()
    assert len(careers) == 12

    async def run() -> tuple[list[object], object, object]:
        async with Client(server) as client:
            results = []
            for _name, entries in careers:
                results.append(await client.call_tool("position", {"entries": entries}))
                results.append(await client.call_tool("next_steps", {"entries": entries}))
            korean = await client.call_tool("position", {"entries": careers[0][1], "lang": "ko"})
            english = await client.call_tool("position", {"entries": careers[0][1], "lang": "en"})
            return results, korean, english

    results, korean, english = asyncio.run(run())
    assert len(results) == 24
    for result in results:
        _assert_contract(result, pct_min)
    _assert_contract(korean, pct_min)
    assert korean.structured_content["lang"] == "ko"
    assert _HANGUL.search(korean.content[0].text)
    assert any(_HANGUL.search(sentence) for sentence in korean.structured_content["must_say"])
    assert english.structured_content["lang"] == "en"
    assert korean.content[0].text != english.content[0].text
    assert korean.structured_content["must_say"] != english.structured_content["must_say"]


def test_demo_bundle_does_not_generalise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The demo's cells are withheld or under the share minimum, so no tool generalises."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("career MCP demo tried to use the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    result = run_demo(DEMO, tmp_path / "out", now=CLOCK)
    config = replace(load(DEMO), data=result.output.resolve())
    out = tmp_path / "bundle"
    build(config, out)
    with (out / "next_window.csv").open(encoding="utf-8", newline="") as handle:
        sample = next(csv.DictReader(handle))
    vocab = json.loads((out / "vocab.json").read_text(encoding="utf-8"))
    server = build_server(out)
    entries = [{"year": 2019, "kind": "group_exhibition", "country": "KR", "venue": "Hall"}]

    async def run() -> list[object]:
        async with Client(server) as client:
            calls = [
                await client.call_tool("about", {}),
                await client.call_tool("career_schema", {}),
                await client.call_tool("position", {"entries": entries}),
                await client.call_tool("list_vocab", {}),
                await client.call_tool("map_question", {"text": "Where do I stand?"}),
                await client.call_tool(
                    "next_steps",
                    {"career_age": band_start(sample["age_band"]), "generation": sample["generation"]},
                ),
                await client.call_tool("field_trend", {"measure": vocab["measures"]["field_trend"][0]}),
            ]
            for programme in vocab["programmes"]:
                calls.append(await client.call_tool("programme_profile", {"programme": programme["code"]}))
            return calls

    for result in asyncio.run(run()):
        assert not result.is_error
        body = result.structured_content
        assert body["generalization_allowed"] is False
        assert body["suppressed"] > 0
