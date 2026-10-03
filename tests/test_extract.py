# SPDX-License-Identifier: MIT
"""CV extraction: schema checks, offline replay, content hash, owner and supersession.

People and URLs are fictitious. No test opens a socket: CV pages are read through
the offline fetcher, which still consults robots.txt.
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import requests
from pydantic import ValidationError

from giye.cli import main
from giye.config import load
from giye.extract.apply import apply_extractions, same_activity
from giye.extract.prompt import prompt_sha256
from giye.extract.provider import write_cache
from giye.extract.schema import parse_extraction, without_unknown_sources
from giye.extract.service import extract
from giye.extract.text import bundle_fingerprint, extract_text, fingerprint, normalize
from giye.ledger.ledger import Ledger
from giye.ledger.schemas import ACTIVITIES_FIELDS, ARTISTS_FIELDS, CV_SOURCES_FIELDS, REVIEW_FIELDS, empty_row

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "extract"
DEMO = ROOT / "examples" / "demo"
UA = "GiyeTest/0.1 (+https://example.org/contact)"
TODAY = date(2026, 10, 4)
MODEL = "claude-opus-5"


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self, url, *args, **kwargs):
        raise AssertionError(f"network used: {url}")

    monkeypatch.setattr(requests.Session, "get", boom)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def _config(tmp_path: Path, *, site: Path, cache: Path, sources: str) -> Path:
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Synthetic extract test"
id_prefix = "GY"
territory = "KR"
languages = ["ko", "en"]

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{(DEMO / "frames.yml").as_posix()}"

[collect]
user_agent = "{UA}"
min_delay_s = 0.0

[collect.offline_roots]
"https://cv.example.org" = "{site.as_posix()}"

[extract]
model = "{MODEL}"
temperature = 0
cache = "{cache.as_posix()}"

{sources}
""",
        encoding="utf-8",
    )
    return path


def _person(ledger_id: str, name_ko: str, *, name_en: str = "", note: str = "", gy: str = "GY-000010") -> dict[str, str]:
    return empty_row(
        ARTISTS_FIELDS,
        ledger_id=ledger_id,
        gy_id=gy,
        name_ko=name_ko,
        name_en=name_en,
        reviewer_note=note,
        status="STAGED",
    )


def _entry(source_id: str, title: str, year: int, **overrides: object) -> dict:
    row: dict = {
        "title": title,
        "venue": "Example Hall",
        "year": year,
        "activity_type": "group_exhibition",
        "role": "",
        "cv_section": "exhibition",
        "upcoming": False,
        "source_id": source_id,
    }
    row.update(overrides)
    return row


def _cache_response(cache: Path, html_path: Path, source_id: str, rows: list[dict]) -> str:
    text = normalize(extract_text(html_path.read_bytes(), "html"))
    digest = bundle_fingerprint([(source_id, text)])
    write_cache(
        cache,
        content_sha256=digest,
        prompt_sha256=prompt_sha256(),
        model=MODEL,
        response=json.dumps({"activities": rows}, ensure_ascii=False),
        temperature=0,
        created_at="2026-01-15T00:00:00Z",
        synthetic=True,
    )
    return digest


def _ledger(tmp_path: Path, config: Path, people: list[dict[str, str]]) -> Ledger:
    ledger = Ledger.open(load(config))
    ledger.write("artists", people, task="test")
    return ledger


def test_provider_import_is_lazy():
    from giye.extract import provider

    tree = ast.parse(inspect.getsource(provider))
    top_level = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    rendered = "\n".join(ast.dump(node) for node in top_level)
    assert "anthropic" not in rendered


def test_schema_rejects_invented_rows_and_drops_unknown_sources():
    invented_type = {"activities": [_entry("CV-REAL", "First Signal", 2019, activity_type="invented_show")]}
    with pytest.raises(ValidationError):
        parse_extraction(json.dumps(invented_type))
    extra_field = {"activities": [_entry("CV-REAL", "First Signal", 2019)], "confidence": 0.2}
    with pytest.raises(ValidationError):
        parse_extraction(json.dumps(extra_field))
    with pytest.raises(ValidationError):
        parse_extraction(json.dumps({"activities": [_entry("CV-REAL", "First Signal", "soon")]}) )  # type: ignore[dict-item]

    raw = json.dumps(
        {
            "activities": [
                _entry("CV-REAL", "First Signal", 2019),
                _entry("CV-MADE-UP", "Invented Biennale", 2035, venue="Nowhere"),
            ]
        }
    )
    kept, dropped = without_unknown_sources(parse_extraction(raw), {"CV-REAL"})
    assert [row.title for row in kept.activities] == ["First Signal"]
    assert [row.source_id for row in dropped] == ["CV-MADE-UP"]


def test_same_activity_folds_a_hangul_repeat_and_keeps_a_short_latin_title():
    assert same_activity({"year": "2019", "title": "미래전설"}, {"year": "2019", "title": "미래전설 extra"})
    assert same_activity({"year": "2019", "title": "Show"}, {"year": "2019", "title": "Show"})
    assert not same_activity({"year": "2019", "title": "Show"}, {"year": "2019", "title": "Show Extra"})
    assert not same_activity({"year": "2019", "title": "미래전설"}, {"year": "2020", "title": "미래전설"})
    assert fingerprint("First  Signal") == fingerprint("First Signal")


def test_replay_is_offline_and_a_hash_change_reads_the_cv_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_network(monkeypatch)
    site = tmp_path / "site"
    site.mkdir()
    shutil.copy(FIXTURE / "robots.txt", site / "robots.txt")
    page = site / "artist.html"
    shutil.copy(FIXTURE / "artist.html", page)
    cache = tmp_path / "cache"
    source = """
[[extract.sources]]
ledger_id = "LED-haneul"
lang = "en"
url = "https://cv.example.org/artist.html"
source_id = "CV-TEST-en"
"""
    config = _config(tmp_path, site=site, cache=cache, sources=source)
    _ledger(tmp_path, config, [_person("LED-haneul", "김하늘", name_en="Haneul Kim")])
    _cache_response(cache, page, "CV-TEST-en", [_entry("CV-TEST-en", "First Signal", 2019)])

    assert main(["extract", "--config", str(config), "--replay-only"]) == 0
    ledger = Ledger.open(load(config))
    rows = ledger.read("activities")
    assert [row["title"] for row in rows] == ["First Signal"]
    assert rows[0]["origin"] == "cv:CV-TEST-en"
    assert rows[0]["source_type"] == "SELF_SUBMITTED"
    snapshots = list((tmp_path / "data" / "raw" / "cv").rglob("*.txt"))
    assert len(snapshots) == 1
    stored = json.loads((tmp_path / "data" / "work" / "cv_extract" / "LED-haneul.json").read_text(encoding="utf-8"))
    assert stored["prompt_sha256"] == prompt_sha256()
    assert stored["extracted_by"] == MODEL

    page.write_text(page.read_text(encoding="utf-8").replace("First Signal", "First   Signal"), encoding="utf-8")
    second = extract(load(config), replay_only=True, today=TODAY)
    assert second.pull.get("unchanged") == 1
    assert second.extracted == []
    assert len(list((tmp_path / "data" / "raw" / "cv").rglob("*.txt"))) == 1

    page.write_text(page.read_text(encoding="utf-8").replace("First   Signal", "Second Signal"), encoding="utf-8")
    missed = extract(load(config), replay_only=True, today=TODAY)
    assert missed.replay_misses == ["LED-haneul"]
    assert [row["title"] for row in ledger.read("activities")] == ["First Signal"]

    _cache_response(cache, page, "CV-TEST-en", [_entry("CV-TEST-en", "Second Signal", 2019)])
    third = extract(load(config), replay_only=True, today=TODAY)
    assert third.extracted == ["LED-haneul"]
    assert third.pull.get("unchanged") == 1 or third.pull.get("changed") == 1
    titles = [row["title"] for row in ledger.read("activities")]
    assert titles == ["Second Signal"]
    assert len(list((tmp_path / "data" / "raw" / "cv").rglob("*.txt"))) == 2


def test_robots_disallow_is_not_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_network(monkeypatch)
    site = tmp_path / "site"
    secret = site / "secret.html"
    secret.parent.mkdir()
    secret.write_text("<p>2019 Hidden Show, Example Hall</p>", encoding="utf-8")
    (site / "robots.txt").write_text("User-agent: *\nDisallow: /secret.html\n", encoding="utf-8")
    cache = tmp_path / "cache"
    source = """
[[extract.sources]]
ledger_id = "LED-haneul"
lang = "en"
url = "https://cv.example.org/secret.html"
source_id = "CV-SECRET-en"
"""
    config = _config(tmp_path, site=site, cache=cache, sources=source)
    _ledger(tmp_path, config, [_person("LED-haneul", "김하늘")])
    result = extract(load(config), replay_only=True, today=TODAY)
    assert result.pull.get("error") == 1
    assert list((tmp_path / "data" / "raw" / "cv").rglob("*")) == []
    queue = Ledger.open(load(config)).read("review_queue")
    assert queue[0]["reason"] == "cv_pull_failed"
    assert "RobotsDisallowed" in queue[0]["detail"]
    assert secret.read_text(encoding="utf-8").startswith("<p>")


def test_team_row_is_not_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_network(monkeypatch)
    site = tmp_path / "site"
    site.mkdir()
    shutil.copy(FIXTURE / "robots.txt", site / "robots.txt")
    shutil.copy(FIXTURE / "artist.html", site / "artist.html")
    source = """
[[extract.sources]]
name_ko = "노을 스튜디오"
lang = "ko"
url = "https://cv.example.org/artist.html"
source_id = "CV-TEAM-ko"
"""
    config = _config(tmp_path, site=site, cache=tmp_path / "cache", sources=source)
    note = "members=김바다|박바다"
    _ledger(tmp_path, config, [_person("LED-team", "노을 스튜디오", note=note, gy="GY-000011")])
    result = extract(load(config), replay_only=True, today=TODAY)
    assert result.registered == 0
    assert result.skipped_team
    assert Ledger.open(load(config)).read("cv_sources") == []


def test_apply_publishable_dedupe_owner_and_supersession(tmp_path: Path):
    config = _config(tmp_path, site=tmp_path / "site", cache=tmp_path / "cache", sources="")
    (tmp_path / "site").mkdir()
    people = [_person("LED-owner", "김하늘", name_en="Haneul Kim", gy="GY-000010")]
    ledger = _ledger(tmp_path, config, people)
    sources = [
        empty_row(
            CV_SOURCES_FIELDS,
            source_id="CV-KO",
            ledger_id="LED-owner",
            lang="ko",
            kind="web",
            url="https://cv.example.org/ko",
            active="true",
            content_sha256="hash-ko",
            last_changed_at="2026-02-02T00:00:00Z",
        ),
            empty_row(
                CV_SOURCES_FIELDS,
                source_id="CV-EN",
                ledger_id="LED-owner",
                lang="en",
                kind="web",
                url="https://cv.example.org/en",
                active="true",
                content_sha256="hash-en",
                last_changed_at="2026-02-02T00:00:00Z",
            ),
            empty_row(
                CV_SOURCES_FIELDS,
                source_id="CV-OTHER",
                ledger_id="LED-owner",
                lang="ko",
                kind="web",
                url="https://cv.example.org/other",
                active="true",
                content_sha256="hash-other",
            ),
        ]
    ledger.write("cv_sources", sources, task="test")
    ledger.write(
        "activities",
        [
            empty_row(
                ACTIVITIES_FIELDS,
                activity_id="pre-self",
                ledger_id="LED-owner",
                title="〈푸른 신호〉",
                venue="예시미술관",
                year="2019",
                activity_type="solo_exhibition",
                source_url="https://example.org/survey",
                source_type="SELF_SUBMITTED",
                collected_at="2020-01-01",
                publishable="yes",
                reviewer_note="from_arko_career_text",
                origin="survey",
            ),
            empty_row(
                ACTIVITIES_FIELDS,
                activity_id="pre-public",
                ledger_id="LED-owner",
                title="기관 기록전",
                venue="예시미술관",
                year="2018",
                activity_type="group_exhibition",
                source_url="https://example.org/roster",
                source_type="PUBLIC_RECORD",
                collected_at="2018-06-01",
                publishable="yes",
                origin="EXAMPLE-RESIDENCY",
            ),
            empty_row(
                ACTIVITIES_FIELDS,
                activity_id="pre-moved",
                ledger_id="LED-retired",
                title="옮겨질 기록",
                venue="예시홀",
                year="2017",
                activity_type="other",
                source_url="https://cv.example.org/ko",
                source_type="SELF_SUBMITTED",
                collected_at="2017-01-01",
                publishable="yes",
                origin="cv:CV-OTHER",
            ),
        ],
        task="test",
    )
    ledger.write(
        "review_queue",
        [
            empty_row(
                REVIEW_FIELDS,
                queue_id="q-1",
                ledger_id="LED-owner",
                reason="cv_new",
                detail="CV-KO",
                status="open",
                created_at="2026-01-01T00:00:00Z",
            )
        ],
        task="test",
    )
    _write_extract(
        tmp_path,
        "LED-owner",
        ["CV-KO", "CV-EN"],
        ["hash-ko", "hash-en"],
        [
            _entry("CV-KO", "〈푸른 신호〉", 2019, venue="예시미술관", activity_type="solo_exhibition"),
            _entry("CV-KO", "예시 미디어전", 2024, venue="예시문화원, 부산"),
            _entry("CV-EN", "예시 미디어전", 2024, venue="예시문화원, 부산"),
            _entry("CV-KO", "기관 기록전", 2018, venue="다른 표기"),
            _entry("CV-KO", "", 2015),
            _entry("CV-KO", "서울예시대학교 미술학 학사", 2014, activity_type="other", cv_section="education"),
            _entry("CV-KO", "예시 장학금", 2021, activity_type="award", cv_section="award"),
            _entry("CV-KO", "올해의 신호", 2026, upcoming=True),
            _entry("CV-KO", "다음 신호", 2027, upcoming=True),
            _entry("CV-KO", "지난 예정", 2020, upcoming=True),
        ],
    )
    _write_extract(
        tmp_path,
        "LED-retired",
        ["CV-KO"],
        ["hash-ko"],
        [_entry("CV-KO", "Old Reading", 2011)],
    )

    stats = apply_extractions(ledger, today=TODAY)
    found = ledger.read("activities")
    rows = {row["title"]: row for row in found}
    assert sum(1 for row in found if row["title"] == "예시 미디어전") == 1
    assert rows["예시 미디어전"]["origin"] == "cv:CV-KO"
    assert "Old Reading" not in rows
    assert stats.superseded_files == 1
    assert rows["〈푸른 신호〉"]["origin"].startswith("cv:")
    assert rows["〈푸른 신호〉"]["publishable"] == "yes"
    survey = next(row for row in ledger.read("activities") if row["activity_id"] == "pre-self")
    assert survey["publishable"] == "no"
    assert "superseded_by_cv" in survey["reviewer_note"]
    assert rows["기관 기록전"]["activity_id"] == "pre-public"
    assert rows["기관 기록전"]["publishable"] == "yes"
    assert rows["기관 기록전"]["origin"] == "EXAMPLE-RESIDENCY"
    assert rows["서울예시대학교 미술학 학사"]["publishable"] == "no"
    assert "cv_section=education" in rows["서울예시대학교 미술학 학사"]["reviewer_note"]
    assert rows["예시 장학금"]["publishable"] == "no"
    assert rows["올해의 신호"]["publishable"] == "yes"
    assert rows["올해의 신호"]["role"] == "(예정)"
    assert rows["다음 신호"]["publishable"] == "no"
    assert "upcoming" in rows["다음 신호"]["reviewer_note"]
    assert rows["지난 예정"]["publishable"] == "yes"
    assert rows["지난 예정"]["role"] == ""
    assert "" not in rows
    moved = rows["옮겨질 기록"]
    assert moved["ledger_id"] == "LED-owner"
    assert stats.repointed == 1
    assert all(item["status"] == "done" for item in ledger.read("review_queue"))

    again = ledger.read("activities")
    apply_extractions(ledger, today=TODAY)
    ids = {row["title"]: row["activity_id"] for row in ledger.read("activities")}
    previous = {row["title"]: row["activity_id"] for row in again}
    assert ids["〈푸른 신호〉"] == previous["〈푸른 신호〉"]


def test_owner_follows_the_source_when_the_file_names_a_retired_id(tmp_path: Path):
    config = _config(tmp_path, site=tmp_path / "empty", cache=tmp_path / "cache", sources="")
    (tmp_path / "empty").mkdir()
    ledger = _ledger(tmp_path, config, [_person("LED-owner", "김하늘")])
    ledger.write(
        "cv_sources",
        [
            empty_row(
                CV_SOURCES_FIELDS,
                source_id="CV-KO",
                ledger_id="LED-owner",
                lang="ko",
                kind="web",
                url="https://cv.example.org/ko",
                active="true",
                content_sha256="hash-ko",
            )
        ],
        task="test",
    )
    _write_extract(tmp_path, "LED-gone", ["CV-KO"], ["hash-ko"], [_entry("CV-KO", "소유자 전시", 2022)])
    apply_extractions(ledger, today=TODAY)
    rows = ledger.read("activities")
    assert len(rows) == 1
    assert rows[0]["ledger_id"] == "LED-owner"
    assert rows[0]["title"] == "소유자 전시"
    assert rows[0]["origin"] == "cv:CV-KO"


def test_stale_extraction_is_not_applied(tmp_path: Path):
    config = _config(tmp_path, site=tmp_path / "empty", cache=tmp_path / "cache", sources="")
    (tmp_path / "empty").mkdir()
    ledger = _ledger(tmp_path, config, [_person("LED-owner", "김하늘")])
    ledger.write(
        "cv_sources",
        [
            empty_row(
                CV_SOURCES_FIELDS,
                source_id="CV-KO",
                ledger_id="LED-owner",
                lang="ko",
                kind="web",
                url="https://cv.example.org/ko",
                active="true",
                content_sha256="hash-new",
            )
        ],
        task="test",
    )
    _write_extract(tmp_path, "LED-owner", ["CV-KO"], ["hash-old"], [_entry("CV-KO", "묵은 전시", 2021)])
    stats = apply_extractions(ledger, today=TODAY)
    assert stats.skipped_stale == ["LED-owner"]
    assert ledger.read("activities") == []


def test_demo_cache_replays_without_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_network(monkeypatch)
    source = (DEMO / "giye.toml").read_text(encoding="utf-8")
    source = source.replace('data = "data"', f'data = "{(tmp_path / "data").as_posix()}"')
    source = source.replace('cache = "cache"', f'cache = "{(DEMO / "cache").as_posix()}"')
    source = source.replace('frames = "frames.yml"', f'frames = "{(DEMO / "frames.yml").as_posix()}"')
    source = source.replace('"fixtures"', f'"{(DEMO / "fixtures").as_posix()}"')
    source = source.replace('"cvs"', f'"{(DEMO / "cvs").as_posix()}"')
    path = tmp_path / "giye.toml"
    path.write_text(source, encoding="utf-8")
    _ledger(
        tmp_path,
        path,
        [
            _person("LED-haneul", "김하늘", name_en="", gy="GY-000010"),
            _person("LED-minsoo", "최민수", gy="GY-000012"),
        ],
    )
    result = extract(load(path), replay_only=True, today=TODAY)
    assert result.replay_misses == []
    assert result.invalid == []
    rows = Ledger.open(load(path)).read("activities")
    by_title = {}
    for row in rows:
        by_title.setdefault(row["title"], []).append(row)
    assert len(by_title["예시 미디어전"]) == 1
    assert by_title["예시 미디어전"][0]["origin"] == "cv:CV-DEMO-HANEUL-ko"
    assert by_title["〈푸른 신호〉"][0]["publishable"] == "yes"
    assert by_title["서울예시대학교 미술학 학사"][0]["publishable"] == "no"
    assert by_title["BFA, Seoul Yesidae University"][0]["publishable"] == "no"
    assert by_title["예시 장학금"][0]["publishable"] == "no"
    assert by_title["다음 신호"][0]["publishable"] == "no"
    assert by_title["올해의 신호"][0]["publishable"] == "yes"
    assert by_title["올해의 신호"][0]["role"] == "(예정)"
    assert "밤의 주파수 / Night Frequency" in by_title
    assert by_title["MFA, Example Graduate School"][0]["publishable"] == "no"


def _write_extract(tmp_path: Path, ledger_id: str, source_ids: list[str], hashes: list[str], activities: list[dict]) -> None:
    directory = tmp_path / "data" / "work" / "cv_extract"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "ledger_id": ledger_id,
        "extracted_by": MODEL,
        "prompt_sha256": prompt_sha256(),
        "sources": [{"source_id": source_id, "content_sha256": digest} for source_id, digest in zip(source_ids, hashes)],
        "activities": activities,
    }
    (directory / f"{ledger_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
