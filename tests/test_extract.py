# SPDX-License-Identifier: AGPL-3.0-only
"""CV extraction: schema checks, offline replay, content hash, owner and supersession.

People and URLs are fictitious. No test opens a socket: CV pages are read through
the offline fetcher, which still consults robots.txt.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import shutil
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests
from pydantic import ValidationError

from giye.cli import main
from giye.config import load
from giye.extract.apply import apply_extractions, same_activity
from giye.extract.prompt import prompt_sha256
from giye.extract.provider import CacheMiss, OpenAICompatibleProvider, ProviderError, write_cache
from giye.extract.schema import Extraction, parse_extraction, without_unknown_sources
from giye.extract.service import ExtractResult, _extract_pending, _extraction_current, _render_document, extract
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
    assert "requests" not in rendered


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
            _person("LED-en", "Haneul Kim", name_en="Haneul Kim", gy="GY-000011"),
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
    # The repeat lives on two people at extract time, so it is not collapsed.
    assert {row["origin"] for row in by_title["예시 미디어전"]} == {
        "cv:CV-DEMO-HANEUL-ko",
        "cv:CV-DEMO-HANEUL-en",
    }
    assert by_title["신호"][0]["venue"] == "서울시립미술관 외"
    assert by_title["Signal"][0]["venue"] == "Seoul Museum of Art"
    assert by_title["Signal"][0]["ledger_id"] == "LED-en"
    assert by_title["Example Residency"][0]["ledger_id"] == "LED-en"
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


@contextmanager
def _chat_server(mode: str, content: str):
    """POST ``/v1/chat/completions`` on 127.0.0.1. No other host is contacted."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))
            self.server.bodies.append(body)  # type: ignore[attr-defined]
            self.server.authorizations.append(self.headers.get("Authorization"))  # type: ignore[attr-defined]
            if self.path != "/v1/chat/completions":
                self._send(404, {"error": "not found"})
                return
            fmt = body.get("response_format") if isinstance(body.get("response_format"), dict) else {}
            if mode == "reject_schema" and fmt.get("type") == "json_schema":
                self._send(400, {"error": "json_schema is not supported"})
                return
            if mode == "reject_format" and "response_format" in body:
                self._send(400, {"error": "response_format is not supported"})
                return
            if mode == "length":
                self._send(200, {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]})
                return
            if mode == "empty_body":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if mode == "empty_content":
                self._send(200, {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]})
                return
            reply = {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}
            self._send(200, reply)

        def _send(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.bodies = []  # type: ignore[attr-defined]
    httpd.authorizations = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    try:
        yield f"http://{host}:{port}/v1", httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def _offline_cv(tmp_path: Path) -> tuple[Path, Path]:
    site = tmp_path / "site"
    site.mkdir()
    shutil.copy(FIXTURE / "robots.txt", site / "robots.txt")
    shutil.copy(FIXTURE / "artist.html", site / "artist.html")
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
    return config, cache


def test_default_provider_is_anthropic(tmp_path: Path):
    demo = load(DEMO / "giye.toml")
    assert demo.extract_provider == "anthropic"
    assert demo.extract_base_url == "http://localhost:11434/v1"
    assert demo.extract_model == "claude-opus-5"
    assert demo.extract_api_key_env == "GIYE_LLM_API_KEY"
    path = tmp_path / "giye.toml"
    path.write_text(
        """
[archive]
name = "Synthetic provider test"
[extract]
provider = "openai_compatible"
base_url = "http://127.0.0.1:9/v1/"
model = "example-local"
api_key_env = "GIYE_TEST_LLM_KEY"
""",
        encoding="utf-8",
    )
    cfg = load(path)
    assert cfg.extract_provider == "openai_compatible"
    assert cfg.extract_base_url == "http://127.0.0.1:9/v1"
    assert cfg.extract_model == "example-local"
    assert cfg.extract_api_key_env == "GIYE_TEST_LLM_KEY"
    path.write_text('[archive]\nname = "Synthetic"\n[extract]\nprovider = "local"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="openai_compatible"):
        load(path)


def test_local_extraction_round_trip_is_cached_under_the_model_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_network(monkeypatch)
    monkeypatch.delenv("GIYE_LLM_API_KEY", raising=False)
    config, cache = _offline_cv(tmp_path)
    content = json.dumps({"activities": [_entry("CV-TEST-en", "Local Signal", 2019)]})
    with _chat_server("ok", content) as (base, server):
        assert (
            main(
                [
                    "extract",
                    "--config",
                    str(config),
                    "--provider",
                    "openai_compatible",
                    "--base-url",
                    base,
                    "--model",
                    "qwen2.5:14b",
                ]
            )
            == 0
        )
        assert len(server.bodies) == 1
        sent = server.bodies[0]
        assert sent["model"] == "qwen2.5:14b"
        assert sent["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": "extraction", "schema": Extraction.model_json_schema(), "strict": True},
        }
        assert sent["temperature"] == 0
        assert server.authorizations == [None]
        assert sent["messages"][0]["role"] == "system"
        assert sent["messages"][1]["role"] == "user"
        assert "CV-TEST-en" in sent["messages"][1]["content"]
    # Drop the extraction file so the next run must read the cache. The base URL
    # is closed: a cache miss would be a provider error, not a replay hit.
    (tmp_path / "data" / "work" / "cv_extract" / "LED-haneul.json").unlink()
    cached = replace(
        load(config),
        extract_provider="openai_compatible",
        extract_base_url="http://127.0.0.1:9/v1",
        extract_model="qwen2.5:14b",
    )
    second = extract(cached, replay_only=False, today=TODAY)
    assert second.extracted == ["LED-haneul"]
    assert second.invalid == []
    assert second.replay_misses == []
    rows = Ledger.open(load(config)).read("activities")
    assert [row["title"] for row in rows] == ["Local Signal"]
    stored = json.loads((tmp_path / "data" / "work" / "cv_extract" / "LED-haneul.json").read_text(encoding="utf-8"))
    assert stored["extracted_by"] == "qwen2.5:14b"
    files = list(cache.glob("*.json"))
    assert len(files) == 1
    assert "qwen2.5_14b" in files[0].name
    assert "claude-opus-5" not in files[0].name
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["model"] == "qwen2.5:14b"
    assert payload["response_mode"] == "json_schema"
    assert json.loads(payload["response"])["activities"][0]["title"] == "Local Signal"


def test_local_provider_sends_the_extraction_schema():
    content = json.dumps({"activities": []})
    with _chat_server("ok", content) as (base, server):
        provider = OpenAICompatibleProvider("example-local", base, timeout=5, api_key="")
        text = provider.complete("prompt", "document")
    assert text == content
    assert provider.response_mode == "json_schema"
    assert len(server.bodies) == 1
    assert server.bodies[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "extraction", "schema": Extraction.model_json_schema(), "strict": True},
    }
    assert server.bodies[0]["messages"][0] == {"role": "system", "content": "prompt"}
    assert server.authorizations == [None]


def test_local_provider_falls_back_to_json_object_when_schema_is_rejected():
    content = json.dumps({"activities": []})
    schema_text = json.dumps(Extraction.model_json_schema(), ensure_ascii=False)
    with _chat_server("reject_schema", content) as (base, server):
        provider = OpenAICompatibleProvider("example-local", base, timeout=5, api_key="")
        text = provider.complete("prompt", "document")
    assert text == content
    assert provider.response_mode == "json_object"
    assert len(server.bodies) == 2
    assert server.bodies[0]["response_format"]["type"] == "json_schema"
    assert server.bodies[1]["response_format"] == {"type": "json_object"}
    assert server.bodies[1]["messages"][0] == {"role": "system", "content": "prompt\n" + schema_text}
    assert server.bodies[1]["messages"][1] == {"role": "user", "content": "document"}


def test_local_provider_retries_when_response_format_is_rejected():
    content = json.dumps({"activities": []})
    schema_text = json.dumps(Extraction.model_json_schema(), ensure_ascii=False)
    with _chat_server("reject_format", content) as (base, server):
        provider = OpenAICompatibleProvider("example-local", base, timeout=5, api_key="")
        text = provider.complete("prompt", "document")
    assert text == content
    assert provider.response_mode == "none"
    assert len(server.bodies) == 3
    assert server.bodies[0]["response_format"]["type"] == "json_schema"
    assert server.bodies[1]["response_format"] == {"type": "json_object"}
    assert server.bodies[1]["messages"][0]["content"] == "prompt\n" + schema_text
    assert "response_format" not in server.bodies[2]
    assert "temperature" not in server.bodies[0]
    assert server.bodies[0]["messages"][0] == {"role": "system", "content": "prompt"}
    assert server.bodies[2]["messages"][0] == {"role": "system", "content": "prompt"}
    assert server.bodies[2]["messages"][1] == {"role": "user", "content": "document"}


def test_local_provider_sends_a_bearer_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GIYE_LLM_API_KEY", raising=False)
    monkeypatch.setenv("GIYE_TEST_LLM_KEY", "synthetic-token")
    content = json.dumps({"activities": []})
    with _chat_server("ok", content) as (base, server):
        OpenAICompatibleProvider("example-local", base, timeout=5, api_key="explicit-token").complete("prompt", "document")
    assert server.authorizations == ["Bearer explicit-token"]
    with _chat_server("ok", content) as (base, server):
        OpenAICompatibleProvider("example-local", base, timeout=5, api_key_env="GIYE_TEST_LLM_KEY").complete(
            "prompt", "document"
        )
    assert server.authorizations == ["Bearer synthetic-token"]


def test_local_extract_sends_bearer_from_the_configured_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_network(monkeypatch)
    monkeypatch.delenv("GIYE_LLM_API_KEY", raising=False)
    monkeypatch.setenv("GIYE_TEST_LLM_KEY", "synthetic-token")
    config, _cache = _offline_cv(tmp_path)
    text = (tmp_path / "giye.toml").read_text(encoding="utf-8")
    text = text.replace("temperature = 0\n", 'temperature = 0\napi_key_env = "GIYE_TEST_LLM_KEY"\n')
    (tmp_path / "giye.toml").write_text(text, encoding="utf-8")
    content = json.dumps({"activities": [_entry("CV-TEST-en", "Local Signal", 2019)]})
    with _chat_server("ok", content) as (base, server):
        assert (
            main(
                [
                    "extract",
                    "--config",
                    str(config),
                    "--provider",
                    "openai_compatible",
                    "--base-url",
                    base,
                    "--model",
                    "example-local",
                ]
            )
            == 0
        )
    assert server.authorizations == ["Bearer synthetic-token"]


def test_local_provider_rejects_length_and_an_empty_body():
    with _chat_server("length", "") as (base, _server), pytest.raises(ProviderError, match="length"):
        OpenAICompatibleProvider("example-local", base, timeout=5).complete("prompt", "document")
    with _chat_server("empty_body", "") as (base, _server), pytest.raises(ProviderError, match="empty body"):
        OpenAICompatibleProvider("example-local", base, timeout=5).complete("prompt", "document")
    with _chat_server("empty_content", "") as (base, _server), pytest.raises(ProviderError, match="empty content"):
        OpenAICompatibleProvider("example-local", base, timeout=5).complete("prompt", "document")
    with pytest.raises(ProviderError, match="connection error"):
        OpenAICompatibleProvider("example-local", "http://127.0.0.1:9/v1", timeout=2).complete("prompt", "document")


def test_default_config_does_not_call_a_local_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _block_network(monkeypatch)
    config, _cache = _offline_cv(tmp_path)
    with _chat_server("ok", json.dumps({"activities": []})) as (base, server):
        # The file selects Anthropic. Pointing base_url at the server must not be enough to call it.
        text = (tmp_path / "giye.toml").read_text(encoding="utf-8")
        text = text.replace('model = "claude-opus-5"', f'model = "claude-opus-5"\nbase_url = "{base}"')
        (tmp_path / "giye.toml").write_text(text, encoding="utf-8")
        result = extract(load(config), replay_only=False, today=TODAY)
    assert result.replay_misses == ["LED-haneul"]
    assert server.bodies == []
    assert load(config).extract_provider == "anthropic"


def test_chunk_chars_defaults_follow_the_provider_and_reject_bad_values(tmp_path: Path):
    demo = load(DEMO / "giye.toml")
    assert demo.extract_provider == "anthropic"
    assert demo.extract_chunk_chars == 0

    def write(body: str) -> Path:
        path = tmp_path / "giye.toml"
        path.write_text(body, encoding="utf-8")
        return path

    hosted = write('[archive]\nname = "Synthetic"\n')
    assert load(hosted).extract_chunk_chars == 0
    local = write('[archive]\nname = "Synthetic"\n[extract]\nprovider = "openai_compatible"\n')
    assert load(local).extract_chunk_chars == 8000
    explicit = write(
        '[archive]\nname = "Synthetic"\n[extract]\nprovider = "openai_compatible"\nchunk_chars = 0\n'
    )
    assert load(explicit).extract_chunk_chars == 0
    override = write('[archive]\nname = "Synthetic"\n[extract]\nprovider = "anthropic"\nchunk_chars = 4000\n')
    assert load(override).extract_chunk_chars == 4000
    write('[archive]\nname = "Synthetic"\n[extract]\nchunk_chars = -1\n')
    with pytest.raises(ValueError, match=r"chunk_chars must be >= 0"):
        load(tmp_path / "giye.toml")
    for bad in ('chunk_chars = "8000"', "chunk_chars = 1.5", "chunk_chars = true"):
        write(f'[archive]\nname = "Synthetic"\n[extract]\n{bad}\n')
        with pytest.raises(TypeError, match="chunk_chars must be an integer"):
            load(tmp_path / "giye.toml")


def _staged_cv(tmp_path: Path, text: str, *, chunk_chars: int):
    site = tmp_path / "site"
    site.mkdir()
    cache = tmp_path / "cache"
    config = _config(tmp_path, site=site, cache=cache, sources="")
    cfg = replace(load(config), extract_chunk_chars=chunk_chars)
    ledger = Ledger.open(cfg)
    ledger.write("artists", [_person("LED-haneul", "김하늘", name_en="Haneul Kim")], task="test")
    snap = cfg.data / "raw" / "cv"
    snap.mkdir(parents=True)
    (snap / "body.txt").write_text(text, encoding="utf-8")
    ledger.write(
        "cv_sources",
        [
            empty_row(
                CV_SOURCES_FIELDS,
                source_id="CV-TEST-en",
                ledger_id="LED-haneul",
                lang="en",
                kind="web",
                url="https://cv.example.org/artist.html",
                active="true",
                content_sha256="staged",
                snapshot_path="data/raw/cv/body",
            )
        ],
        task="test",
    )
    return ledger, cfg


def _long_cv() -> tuple[str, int]:
    first = "EXHIBITIONS\n\n1990 Alpha Signal, Example Hall"
    second = "1991 Beta Signal, Example Hall"
    return first + "\n\n" + second, len(first)


def test_short_cv_is_one_call_and_the_document_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    text = "EXHIBITIONS\n\n1990 Example Show, Example Hall\n"
    ledger, cfg = _staged_cv(tmp_path, text, chunk_chars=8000)
    calls: list[dict] = []

    def fake(_cache_dir, **kwargs):
        calls.append(kwargs)
        return json.dumps({"activities": [_entry("CV-TEST-en", "Example Show", 1990)]})

    monkeypatch.setattr("giye.extract.service._complete", fake)
    result = ExtractResult()
    _extract_pending(ledger, cfg, result, replay_only=False)
    assert result.extracted == ["LED-haneul"]
    assert result.invalid == []
    assert len(calls) == 1
    assert calls[0]["document"] == _render_document("김하늘", [("CV-TEST-en", text)])
    assert "Part " not in calls[0]["document"]
    assert calls[0]["content_sha256"] == bundle_fingerprint([("CV-TEST-en", text)])
    stored = json.loads((cfg.work / "cv_extract" / "LED-haneul.json").read_text(encoding="utf-8"))
    assert stored["chunk_chars"] == 8000
    assert stored["chunks"] == 1
    assert stored["content_sha256"] == bundle_fingerprint([("CV-TEST-en", text)])
    assert [row["title"] for row in stored["activities"]] == ["Example Show"]


def test_long_cv_is_one_call_per_chunk_and_rows_stay_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    text, limit = _long_cv()
    ledger, cfg = _staged_cv(tmp_path, text, chunk_chars=limit)
    calls: list[dict] = []

    def fake(_cache_dir, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            rows = [_entry("CV-TEST-en", "Alpha Signal", 1990)]
        else:
            # Same title and venue as the first piece: the file keeps both.
            rows = [_entry("CV-TEST-en", "Alpha Signal", 1990), _entry("CV-TEST-en", "Beta Signal", 1991)]
        return json.dumps({"activities": rows})

    monkeypatch.setattr("giye.extract.service._complete", fake)
    result = ExtractResult()
    _extract_pending(ledger, cfg, result, replay_only=False)
    assert result.invalid == []
    assert len(calls) == 2
    assert calls[0]["document"].index("Part 1 of 2 of this CV.") < calls[0]["document"].index(
        "Artist: 김하늘. Extract the activity rows."
    )
    assert "Part 2 of 2 of this CV." in calls[1]["document"]
    assert "[continued; last heading: EXHIBITIONS]" in calls[1]["document"]
    assert "1991 Beta Signal, Example Hall" in calls[1]["document"]
    whole = bundle_fingerprint([("CV-TEST-en", text)])
    for call in calls:
        rendered = call["document"]
        assert call["content_sha256"] == hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        assert call["content_sha256"] != whole
    stored = json.loads((cfg.work / "cv_extract" / "LED-haneul.json").read_text(encoding="utf-8"))
    assert stored["chunk_chars"] == limit
    assert stored["chunks"] == 2
    assert stored["content_sha256"] == whole
    assert [row["title"] for row in stored["activities"]] == ["Alpha Signal", "Alpha Signal", "Beta Signal"]


def test_a_failing_chunk_invalidates_the_artist_and_writes_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    text, limit = _long_cv()
    ledger, cfg = _staged_cv(tmp_path, text, chunk_chars=limit)
    path = cfg.work / "cv_extract" / "LED-haneul.json"
    calls = {"n": 0}

    def fail(_cache_dir, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"activities": [_entry("CV-TEST-en", "Alpha Signal", 1990)]})
        raise ProviderError("stopped")

    monkeypatch.setattr("giye.extract.service._complete", fail)
    result = ExtractResult()
    _extract_pending(ledger, cfg, result, replay_only=False)
    assert result.invalid == ["LED-haneul"]
    assert result.extracted == []
    assert result.replay_misses == []
    assert not path.is_file()

    calls["n"] = 0

    def bad_json(_cache_dir, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"activities": [_entry("CV-TEST-en", "Alpha Signal", 1990)]})
        return "not-json"

    monkeypatch.setattr("giye.extract.service._complete", bad_json)
    result = ExtractResult()
    _extract_pending(ledger, cfg, result, replay_only=False)
    assert result.invalid == ["LED-haneul"]
    assert not path.is_file()

    calls["n"] = 0

    def miss(_cache_dir, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"activities": [_entry("CV-TEST-en", "Alpha Signal", 1990)]})
        return CacheMiss("abc", "def", "example-local")

    monkeypatch.setattr("giye.extract.service._complete", miss)
    result = ExtractResult()
    _extract_pending(ledger, cfg, result, replay_only=False)
    assert result.replay_misses == ["LED-haneul"]
    assert result.invalid == []
    assert result.extracted == []
    assert not path.is_file()


def test_chunk_chars_zero_keeps_a_long_cv_in_one_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    text, _limit = _long_cv()
    ledger, cfg = _staged_cv(tmp_path, text, chunk_chars=0)
    calls: list[dict] = []

    def fake(_cache_dir, **kwargs):
        calls.append(kwargs)
        return json.dumps({"activities": []})

    monkeypatch.setattr("giye.extract.service._complete", fake)
    result = ExtractResult()
    _extract_pending(ledger, cfg, result, replay_only=False)
    assert len(calls) == 1
    assert calls[0]["document"] == _render_document("김하늘", [("CV-TEST-en", text)])
    assert calls[0]["content_sha256"] == bundle_fingerprint([("CV-TEST-en", text)])


def test_changing_chunk_chars_makes_extraction_current_false(tmp_path: Path):
    path = tmp_path / "LED-haneul.json"
    sources = [{"source_id": "CV-TEST-en", "content_sha256": "staged"}]
    path.write_text(
        json.dumps(
            {
                "prompt_sha256": "p",
                "extracted_by": "example-local",
                "sources": sources,
            }
        ),
        encoding="utf-8",
    )
    assert _extraction_current(path, sources, prompt_sha="p", model="example-local", chunk_chars=0)
    assert not _extraction_current(path, sources, prompt_sha="p", model="example-local", chunk_chars=8000)
    path.write_text(
        json.dumps(
            {
                "prompt_sha256": "p",
                "extracted_by": "example-local",
                "chunk_chars": 8000,
                "sources": sources,
            }
        ),
        encoding="utf-8",
    )
    assert _extraction_current(path, sources, prompt_sha="p", model="example-local", chunk_chars=8000)
    assert not _extraction_current(path, sources, prompt_sha="p", model="example-local", chunk_chars=4000)


def test_cli_provider_override_recomputes_an_omitted_chunk_chars(tmp_path: Path, monkeypatch):
    from giye.extract import service
    from giye.extract.service import ExtractResult

    seen = []
    monkeypatch.setattr(service, "extract", lambda config, **_: seen.append(config) or ExtractResult())
    omitted = tmp_path / "omitted.toml"
    omitted.write_text('[archive]\nname = "Synthetic"\n', encoding="utf-8")
    explicit = tmp_path / "explicit.toml"
    explicit.write_text('[archive]\nname = "Synthetic"\n[extract]\nchunk_chars = 0\n', encoding="utf-8")
    for path in (omitted, explicit):
        assert main(["extract", "--config", str(path), "--provider", "openai_compatible"]) == 0
    assert [c.extract_chunk_chars for c in seen] == [8000, 0]


def test_local_provider_sends_reasoning_effort_only_when_set(tmp_path: Path):
    content = json.dumps({"activities": []})
    with _chat_server("ok", content) as (base, server):
        OpenAICompatibleProvider("example-local", base, timeout=5, api_key="").complete("prompt", "document")
        OpenAICompatibleProvider("example-local", base, timeout=5, api_key="", reasoning_effort="none").complete(
            "prompt", "document"
        )
    assert "reasoning_effort" not in server.bodies[0]
    assert server.bodies[1]["reasoning_effort"] == "none"
    path = tmp_path / "giye.toml"
    path.write_text('[archive]\nname = "Synthetic"\n[extract]\nreasoning_effort = "none"\n', encoding="utf-8")
    assert load(path).extract_reasoning_effort == "none"
    path.write_text('[archive]\nname = "Synthetic"\n[extract]\nreasoning_effort = "off"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="reasoning_effort must be one of"):
        load(path)


def test_registration_uses_the_configured_field_team_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """T1 at CV registration reads the archive's field file, not the shipped Korean list."""
    _block_network(monkeypatch)
    site = tmp_path / "site"
    site.mkdir()
    shutil.copy(FIXTURE / "robots.txt", site / "robots.txt")
    shutil.copy(FIXTURE / "artist.html", site / "artist.html")
    source = """
[[extract.sources]]
name_ko = "물결 Orchestra"
lang = "ko"
url = "https://cv.example.org/artist.html"
source_id = "CV-ORCH-ko"
"""
    config = _config(tmp_path, site=site, cache=tmp_path / "cache", sources=source)
    field = tmp_path / "field.toml"
    field.write_text("[resolve]\nteam_words = '(orchestra)'\n", encoding="utf-8")
    text = config.read_text(encoding="utf-8").replace("[paths]\n", f'[paths]\nfield = "{field.as_posix()}"\n', 1)
    config.write_text(text, encoding="utf-8")
    _ledger(tmp_path, config, [_person("LED-orch", "물결 Orchestra", gy="GY-000012")])
    result = extract(load(config), replay_only=True, today=TODAY)
    assert result.registered == 0
    assert result.skipped_team == ["LED-orch (team_name)"]


def test_field_file_selects_the_extraction_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A field's own prompt is sent and keys the replay cache; the default prompt's record is not used."""
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
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Turn a dance artist's CV into dated rows.\n", encoding="utf-8")
    field = tmp_path / "field.toml"
    field.write_text('[extract]\nprompt = "prompt.txt"\n', encoding="utf-8")
    text = config.read_text(encoding="utf-8").replace("[paths]\n", f'[paths]\nfield = "{field.as_posix()}"\n', 1)
    config.write_text(text, encoding="utf-8")
    cfg = load(config)
    assert cfg.field_config.extract_prompt == prompt.resolve()
    assert prompt_sha256(prompt) != prompt_sha256()
    _ledger(tmp_path, config, [_person("LED-haneul", "김하늘", name_en="Haneul Kim")])
    # A record made with the default prompt is not a replay for the field's prompt.
    _cache_response(cache, page, "CV-TEST-en", [_entry("CV-TEST-en", "Default Prompt", 2019)])
    missed = extract(load(config), replay_only=True, today=TODAY)
    assert missed.replay_misses == ["LED-haneul"]
    digest = bundle_fingerprint([("CV-TEST-en", normalize(extract_text(page.read_bytes(), "html")))])
    write_cache(
        cache,
        content_sha256=digest,
        prompt_sha256=prompt_sha256(prompt),
        model=MODEL,
        response=json.dumps({"activities": [_entry("CV-TEST-en", "Field Prompt", 2019)]}),
        temperature=0,
        created_at="2026-01-15T00:00:00Z",
        synthetic=True,
    )
    hit = extract(load(config), replay_only=True, today=TODAY)
    assert hit.extracted == ["LED-haneul"]
    assert [row["title"] for row in Ledger.open(load(config)).read("activities")] == ["Field Prompt"]
    stored = json.loads((tmp_path / "data" / "work" / "cv_extract" / "LED-haneul.json").read_text(encoding="utf-8"))
    assert stored["prompt_sha256"] == prompt_sha256(prompt)
