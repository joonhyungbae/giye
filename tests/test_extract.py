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
from giye.extract.text import bundle_fingerprint, extract_text, fingerprint, normalize, replay_key
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
    # --strict turns the miss into a failed command; without it the run reports and goes on.
    assert main(["extract", "--config", str(config), "--replay-only"]) == 0
    assert main(["extract", "--config", str(config), "--replay-only", "--strict"]) == 1

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
    assert calls[0]["content_sha256"] == replay_key([("CV-TEST-en", text)])
    assert calls[0]["legacy_sha256"] == bundle_fingerprint([("CV-TEST-en", text)])
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
    assert result.invalid == []
    assert result.provider_errors == [("LED-haneul", "stopped")]
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
    assert calls[0]["content_sha256"] == replay_key([("CV-TEST-en", text)])


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


def _shared_text_ledger(tmp_path: Path) -> tuple[Ledger, object, str]:
    """Two people who hold the same CV text under two source ids (a duo's shared page)."""
    site = tmp_path / "site"
    site.mkdir()
    config = _config(tmp_path, site=site, cache=tmp_path / "cache", sources="")
    cfg = load(config)
    ledger = Ledger.open(cfg)
    ledger.write(
        "artists",
        [_person("LED-sol", "김솔", gy="GY-000010"), _person("LED-bada", "박바다", gy="GY-000011")],
        task="test",
    )
    text = "EXHIBITIONS\n\n2019 Shared Signal, Example Hall\n"
    rows = []
    for lid, sid in (("LED-sol", "CV-SOL-en"), ("LED-bada", "CV-BADA-en")):
        folder = cfg.data / "raw" / "cv" / lid / sid
        folder.mkdir(parents=True)
        (folder / "snap.txt").write_text(text, encoding="utf-8")
        rows.append(
            empty_row(
                CV_SOURCES_FIELDS,
                source_id=sid,
                ledger_id=lid,
                lang="en",
                kind="web",
                url=f"https://duo.example.org/{lid}",
                active="true",
                content_sha256=fingerprint(text),
                snapshot_path=f"data/raw/cv/{lid}/{sid}/snap",
            )
        )
    ledger.write("cv_sources", rows, task="test")
    return ledger, cfg, text


def _write_response(cfg, key: str, rows: list[dict]) -> None:
    write_cache(
        cfg.extract_cache,
        content_sha256=key,
        prompt_sha256=prompt_sha256(),
        model=MODEL,
        response=json.dumps({"activities": rows}),
        temperature=0,
        created_at="2026-01-15T00:00:00Z",
        synthetic=True,
    )


def test_replay_key_names_the_source_so_a_shared_text_keeps_both_readings(tmp_path: Path, monkeypatch):
    _block_network(monkeypatch)
    ledger, cfg, text = _shared_text_ledger(tmp_path)
    assert replay_key([("CV-SOL-en", text)]) != replay_key([("CV-BADA-en", text)])
    assert bundle_fingerprint([("CV-SOL-en", text)]) == bundle_fingerprint([("CV-BADA-en", text)])
    _write_response(cfg, replay_key([("CV-SOL-en", text)]), [_entry("CV-SOL-en", "Shared Signal", 2019)])
    _write_response(cfg, replay_key([("CV-BADA-en", text)]), [_entry("CV-BADA-en", "Shared Signal", 2019)])
    result = extract(cfg, replay_only=True, today=TODAY)
    assert sorted(result.extracted) == ["LED-bada", "LED-sol"]
    owners = sorted((row["ledger_id"], row["origin"]) for row in ledger.read("activities"))
    assert owners == [("LED-bada", "cv:CV-BADA-en"), ("LED-sol", "cv:CV-SOL-en")]


def test_an_older_text_only_key_written_for_another_source_is_a_miss(tmp_path: Path, monkeypatch):
    """The text-only key is shared by both people; it answers the one it was written for only."""
    _block_network(monkeypatch)
    ledger, cfg, text = _shared_text_ledger(tmp_path)
    _write_response(cfg, bundle_fingerprint([("CV-SOL-en", text)]), [_entry("CV-SOL-en", "Shared Signal", 2019)])
    standing = empty_row(
        ACTIVITIES_FIELDS,
        activity_id="act-bada-1",
        ledger_id="LED-bada",
        title="Earlier Reading",
        year="2019",
        activity_type="group_exhibition",
        source_type="SELF_SUBMITTED",
        publishable="yes",
        origin="cv:CV-BADA-en",
    )
    ledger.write("activities", [standing], task="test")
    result = extract(cfg, replay_only=True, today=TODAY)
    assert result.extracted == ["LED-sol"]
    assert result.replay_misses == ["LED-bada"]
    assert not (cfg.work / "cv_extract" / "LED-bada.json").exists()
    rows = {(row["ledger_id"], row["title"]) for row in ledger.read("activities")}
    assert ("LED-bada", "Earlier Reading") in rows
    assert ("LED-sol", "Shared Signal") in rows


def test_a_reading_that_would_remove_every_cv_row_is_a_miss(tmp_path: Path, monkeypatch):
    text = "EXHIBITIONS\n\n1990 Example Show, Example Hall\n"
    ledger, cfg = _staged_cv(tmp_path, text, chunk_chars=0)
    standing = empty_row(
        ACTIVITIES_FIELDS,
        activity_id="act-haneul-1",
        ledger_id="LED-haneul",
        title="Example Show",
        year="1990",
        activity_type="group_exhibition",
        origin="cv:CV-TEST-en",
    )
    ledger.write("activities", [standing], task="test")
    for rows, kind in (([], "empty_readings"), ([_entry("CV-OTHER-en", "Example Show", 1990)], "unknown_sources")):
        monkeypatch.setattr("giye.extract.service._complete", lambda _cache, rows=rows, **_kw: json.dumps({"activities": rows}))
        result = ExtractResult()
        _extract_pending(ledger, cfg, result, replay_only=True)
        # Neither is a replay miss (the cache answered); each has its own counter.
        assert result.replay_misses == []
        assert getattr(result, kind) == ["LED-haneul"]
        assert result.extracted == []
        assert not (cfg.work / "cv_extract" / "LED-haneul.json").exists()


def test_schema_accepts_note_and_a_missing_role_and_still_rejects_other_fields():
    row = _entry("CV-REAL", "First Signal", 2019, note="original wording kept")
    del row["role"]
    parsed = parse_extraction(json.dumps({"activities": [row]}))
    assert parsed.activities[0].role == ""
    assert parsed.activities[0].note == "original wording kept"
    with pytest.raises(ValidationError):
        parse_extraction(json.dumps({"activities": [_entry("CV-REAL", "First Signal", 2019, reviewer_note="x")]}))
    # The schema sent to a model is unchanged: eight required fields, no note.
    entry = Extraction.model_json_schema()["$defs"]["Entry"]
    assert "note" not in entry["properties"]
    assert entry["required"] == list(entry["properties"])
    assert "default" not in entry["properties"]["role"]


def test_replay_only_does_not_pull_a_source_outside_the_offline_roots(tmp_path: Path, monkeypatch):
    _block_network(monkeypatch)
    site = tmp_path / "site"
    site.mkdir()
    config = _config(tmp_path, site=site, cache=tmp_path / "cache", sources="")
    cfg = load(config)
    ledger = Ledger.open(cfg)
    ledger.write("artists", [_person("LED-haneul", "김하늘")], task="test")
    source = empty_row(
        CV_SOURCES_FIELDS,
        source_id="CV-FAR-en",
        ledger_id="LED-haneul",
        lang="en",
        kind="web",
        url="https://far.example.org/cv",
        active="true",
    )
    ledger.write("cv_sources", [source], task="test")
    before = ledger.read("cv_sources")
    result = extract(cfg, replay_only=True, today=TODAY)
    assert result.pull == {}
    assert ledger.read("cv_sources") == before
    queue = ledger.read("review_queue") if ledger.path("review_queue").exists() else []
    assert not any(item["reason"] == "cv_pull_failed" for item in queue)


def test_unreachable_model_server_is_a_provider_error_and_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _block_network(monkeypatch)
    monkeypatch.delenv("GIYE_LLM_API_KEY", raising=False)
    config, _cache = _offline_cv(tmp_path)
    args = ["extract", "--config", str(config), "--provider", "openai_compatible"]
    # Port 9 (discard) is closed: the connection is refused at once.
    assert main([*args, "--base-url", "http://127.0.0.1:9/v1", "--model", "example-local"]) == 1
    captured = capsys.readouterr()
    assert "invalid=0 provider_errors=1" in captured.out
    assert "invalid extraction" not in captured.out
    assert "provider error LED-haneul: connection error" in captured.err
    assert "every model call failed" in captured.err


def _grounding_ledger(tmp_path: Path, *, grounding: bool) -> Ledger:
    """One person, one CV text on disk, and an extraction file that cites it. Fictitious data."""
    (tmp_path / "empty").mkdir(exist_ok=True)
    config = _config(tmp_path, site=tmp_path / "empty", cache=tmp_path / "cache", sources="")
    if not grounding:
        config.write_text(config.read_text(encoding="utf-8").replace("temperature = 0\n", "temperature = 0\ngrounding = false\n"), encoding="utf-8")
    ledger = _ledger(tmp_path, config, [_person("LED-owner", "김하늘")])
    stored = "data/raw/cv/LED-owner/CV-KO/20260115-aaaa"
    text_path = tmp_path / f"{stored}.txt"
    text_path.parent.mkdir(parents=True)
    text_path.write_text(
        "전시\n2021  신호 — Example   Hall\n2019 Open Studio, Example Art Space (Seoul)\n2018 밤의 주파수\n",
        encoding="utf-8",
    )
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
                content_sha256="hash-1",
                snapshot_path=stored,
            )
        ],
        task="test",
    )
    _write_extract(
        tmp_path,
        "LED-owner",
        ["CV-KO"],
        ["hash-1"],
        [
            _entry("CV-KO", "신호", 2021, venue="example hall"),
            _entry("CV-KO", "Open Studio", 2019, venue="Example Art Space, Seoul"),
            _entry("CV-KO", "밤의 주파수", 2018, venue=""),
            _entry("CV-KO", "Invented Show", 2021, venue="Example Grand Museum"),
            _entry("CV-KO", "신호 재연", 2020, venue="Example Hall"),
        ],
    )
    return ledger


def test_grounding_hides_a_row_whose_year_or_venue_is_not_in_the_cv(tmp_path: Path):
    ledger = _grounding_ledger(tmp_path, grounding=True)
    self_report = empty_row(
        ACTIVITIES_FIELDS,
        activity_id="self-1",
        ledger_id="LED-owner",
        title="Invented Show",
        year="2021",
        venue="",
        source_type="SELF_SUBMITTED",
        source_url="https://example.org/self",
        publishable="yes",
        origin="survey",
    )
    roster = empty_row(
        ACTIVITIES_FIELDS,
        activity_id="roster-1",
        ledger_id="LED-owner",
        title="Example Workshop",
        year="1999",
        venue="Nowhere In The CV",
        source_url="https://example.org/roster",
        publishable="yes",
        origin="EXAMPLE-WORKSHOP",
    )
    ledger.write("activities", [self_report, roster], task="test")
    stats = apply_extractions(ledger, today=TODAY)
    every = ledger.read("activities")
    rows = {row["title"]: row for row in every if row["activity_id"] != "self-1"}
    for grounded in ("신호", "Open Studio", "밤의 주파수"):
        assert rows[grounded]["publishable"] == "yes", grounded
        assert "ungrounded" not in rows[grounded]["reviewer_note"]
    assert rows["Invented Show"]["publishable"] == "no"
    assert rows["Invented Show"]["reviewer_note"].endswith("ungrounded=venue")
    assert rows["신호 재연"]["publishable"] == "no"
    assert rows["신호 재연"]["reviewer_note"].endswith("ungrounded=year")
    # Never deleted, and an ungrounded row does not hide the self-report it repeats.
    assert len(every) == 7
    assert rows["Invented Show"]["origin"] == "cv:CV-KO"
    self_row = next(row for row in every if row["activity_id"] == "self-1")
    assert self_row["publishable"] == "yes"
    # Roster rows are not model output and are not checked.
    assert rows["Example Workshop"]["publishable"] == "yes"
    assert (stats.grounding.year, stats.grounding.venue, stats.grounding.both, stats.grounding.unchecked) == (1, 1, 0, 0)


def test_an_ungrounded_row_opens_one_review_item_and_is_kept(tmp_path: Path):
    """Software review 5, MAJOR-3: a failing row is hidden and queued for a person, never dropped."""
    from giye.extract.grounding import REVIEW_REASON

    ledger = _grounding_ledger(tmp_path, grounding=True)
    stats = apply_extractions(ledger, today=TODAY)
    items = [item for item in ledger.read("review_queue") if item["reason"] == REVIEW_REASON]
    assert stats.ungrounded_opened == 2 and len(items) == 2
    assert all(item["status"] == "open" and item["ledger_id"] == "LED-owner" for item in items)
    details = sorted(item["detail"] for item in items)
    assert "missing=venue" in details[0] + details[1] and "missing=year" in details[0] + details[1]
    assert any("Invented Show — Example Grand Museum" in detail for detail in details)
    # A second apply opens nothing new.
    stats = apply_extractions(ledger, today=TODAY)
    assert stats.ungrounded_opened == 0
    assert len([item for item in ledger.read("review_queue") if item["reason"] == REVIEW_REASON]) == 2
    # A reading without the invented row closes its item.
    _write_extract(tmp_path, "LED-owner", ["CV-KO"], ["hash-1"], [_entry("CV-KO", "신호", 2021, venue="example hall")])
    stats = apply_extractions(ledger, today=TODAY)
    assert stats.ungrounded_closed == 2
    assert {item["status"] for item in ledger.read("review_queue") if item["reason"] == REVIEW_REASON} == {"done"}


def test_grounding_also_checks_rows_of_a_stale_extraction(tmp_path: Path):
    """A row written before grounding existed, whose file is skipped as stale, is checked too."""
    ledger = _grounding_ledger(tmp_path, grounding=True)
    _write_extract(tmp_path, "LED-owner", ["CV-KO"], ["hash-old"], [_entry("CV-KO", "Invented Show", 2021, venue="x")])
    old = [
        empty_row(
            ACTIVITIES_FIELDS,
            activity_id=f"old-{n}",
            ledger_id="LED-owner",
            title=title,
            year=year,
            venue=venue,
            source_url="https://cv.example.org/ko",
            collected_at="2026-01-15",
            publishable="yes",
            origin="cv:CV-KO",
        )
        for n, (title, year, venue) in enumerate((("Invented Show", "2021", "Example Grand Museum"), ("신호", "2021", "Example Hall")))
    ]
    ledger.write("activities", old, task="test")
    stats = apply_extractions(ledger, today=TODAY)
    assert stats.skipped_stale == ["LED-owner"]
    rows = {row["activity_id"]: row for row in ledger.read("activities")}
    assert rows["old-0"]["publishable"] == "no" and rows["old-0"]["reviewer_note"].endswith("ungrounded=venue")
    assert rows["old-1"]["publishable"] == "yes"
    assert stats.grounding.marked_outside_apply == 1


def test_grounding_can_be_turned_off(tmp_path: Path):
    ledger = _grounding_ledger(tmp_path, grounding=False)
    stats = apply_extractions(ledger, today=TODAY)
    assert all(row["publishable"] == "yes" for row in ledger.read("activities"))
    assert stats.grounding.marked == 0


def test_grounding_text_rules():
    from giye.extract.grounding import CvText, failures

    text = CvText.of("2019\tShow,  Example Art-Space (Seoul)\n120200 numbers")
    assert failures({"year": "2019", "venue": "example art-space"}, text) == []
    assert failures({"year": "2019", "venue": "Example Art Space, Seoul"}, text) == []
    assert failures({"year": "2020", "venue": ""}, text) == ["year"]
    assert failures({"year": "2018", "venue": "Example Museum"}, text) == ["year", "venue"]


def test_grounding_normalisation_misses_found_on_the_production_copy():
    """Each case is a correct reading the rule failed on the production copy of 2026-10-06 (fictitious names)."""
    from giye.extract.grounding import CvText, failures
    from giye.normalize.language import default_language

    lang = default_language()
    text = CvText.of(
        "2022 Example Day Seoul South Korea Performance\n"
        "2016年 ワークショップ｜アートラボ例示橋（\x08愛知）\n"
        "2021 Example Show,Gallery Gallery,\"Antwerp, Belgium\"\n"
        "2007 Monuments biennale, curated by A. Curator, 2nd Example Biennial\n"
        "2017 TESTIGOS (WITNESSES)\n",
        lang,
    )
    # A trailing country code is a place, not part of an institution name.
    assert failures({"year": "2022", "venue": "Seoul KR", "title": "Example Day"}, text) == []
    # A control character inside the text, and a Japanese name read as naming a place.
    assert failures({"year": "2016", "venue": "アートラボ例示橋（愛知）"}, text) == []
    # A generic name that is a whole segment of the text.
    assert failures({"year": "2021", "venue": "Gallery Gallery, Antwerp, Belgium"}, text) == []
    # A generic first part, and a later part that names the event.
    assert failures({"year": "2007", "venue": "biennale, curated by A. Curator, 2nd Example Biennial"}, text) == []
    # G-T reads the title up to a comma.
    assert failures({"year": "2017", "venue": "", "title": "TESTIGOS, documental sobre el campo"}, text) == []
    # A year stated as "N days ago", counted back from the snapshot's date.
    from datetime import date

    relative = CvText.of("Lume (Auto), 1635 days ago", lang, taken=date(2026, 9, 22))
    assert failures({"year": "2022", "venue": "", "title": "Lume (Auto)"}, relative) == []
    assert failures({"year": "2021", "venue": "", "title": "Lume (Auto)"}, relative) == ["year"]
    # Still refused: a generic name that is the tail of a longer one, and an invented institution.
    assert failures({"year": "2021", "venue": "Gallery, Antwerp"}, text) == ["venue"]
    assert failures({"year": "2022", "venue": "Example Grand Hall, Seoul KR"}, text) == ["venue"]


def test_grounding_venue_reads_the_institution_part():
    from giye.extract.grounding import CvText, failures
    from giye.normalize.language import default_language

    lang = default_language()
    text = CvText.of(
        "2019 Example Art Space (Seoul, Korea)\n2020 Busan\n2021 예시 미디어 공간 개인전\n2022 서울시립미술관 단체전\n",
        lang,
    )
    # Parts reordered or joined, a city the text does not write, spaces dropped.
    assert failures({"year": "2019", "venue": "Example Art Space, Seoul, Korea"}, text) == []
    assert failures({"year": "2019", "venue": "Example Art Space, Daegu"}, text) == []
    assert failures({"year": "2021", "venue": "예시미디어공간, 서울"}, text) == []
    # A venue that is only places is checked on its first part.
    assert failures({"year": "2020", "venue": "Busan, Korea"}, text) == []
    assert failures({"year": "2020", "venue": "Daegu, Korea"}, text) == ["venue"]
    # The institution itself must occur.
    assert failures({"year": "2019", "venue": "Example Grand Hall, Seoul"}, text) == ["venue"]
    # Cross-script: the V9 reading of a Hangul run in the text.
    assert failures({"year": "2022", "venue": "Seoul Museum of Art, Seoul"}, text) == []
    assert failures({"year": "2022", "venue": "Busan Museum of Art"}, text) == ["venue"]


def test_grounding_venue_reads_a_bracketed_other_script_name():
    from giye.extract.grounding import CvText, failures
    from giye.normalize.language import default_language

    lang = default_language()
    text = CvText.of("2019 Example Light Hall group show\n2020 예시빛관 개인전\n2021 Example Dark Hall, Seoul\n", lang)
    # Either form of a name given in two scripts grounds the venue, in either order.
    assert failures({"year": "2019", "venue": "예시빛관 (Example Light Hall), Seoul"}, text) == []
    assert failures({"year": "2020", "venue": "Example Moon Hall (예시빛관), Seoul"}, text) == []
    # Neither form occurs.
    assert failures({"year": "2019", "venue": "예시별관 (Example Star Hall), Seoul"}, text) == ["venue"]
    # A bracketed place is not a second form of the name.
    assert failures({"year": "2021", "venue": "예시어둠관 (Seoul)"}, text) == ["venue"]
    # A same-script bracket (a branch, an acronym) is not used either.
    assert failures({"year": "2021", "venue": "Example Night Hall (Example Dark Hall)"}, text) == ["venue"]


def test_grounding_refuses_the_invented_venues_of_a_fake_model():
    """Software review round 6, MAJOR-4: readings a fake model invented against the demo CV."""
    from giye.extract.grounding import CvText, failures
    from giye.normalize.language import default_language

    lang = default_language()
    text = CvText.of(
        "Haneul Kim\nResidencies\n2019 Example Residency, Seoul\nGroup exhibitions\n"
        "2022 Signal, Seoul Museum of Art\n"
        "2024 예시 미디어전, 예시문화원, 부산. Example Media Exhibition at the Example Culture Center.\n",
        lang,
    )
    assert failures({"year": "2022", "venue": "Seoul Museum of Art"}, text) == []
    assert failures({"year": "2019", "venue": "Example Residency, Seoul"}, text) == []
    for venue, year in (
        ("Imaginary Kunsthalle, Berlin", "2022"),
        # Generic words found inside a longer name are not that name.
        ("Museum of Art, Busan", "2022"),
        ("Art, Berlin", "2022"),
        ("Residency, Seoul", "2019"),
    ):
        assert failures({"year": year, "venue": venue}, text) == ["venue"], venue
    assert failures({"year": "2011", "venue": "Seoul Museum of Art"}, text) == ["year"]
    # Inherent to the institution-part rule, documented in the module: an
    # invented same-script bracket next to an institution that occurs, and an
    # empty venue, claim nothing the rule checks.
    assert failures({"year": "2024", "venue": "Example Culture Center (Imaginary Hall)"}, text) == []
    assert failures({"year": "2019", "venue": ""}, text) == []


def test_grounding_matches_whole_words_only():
    from giye.extract.grounding import CvText, failures
    from giye.normalize.language import default_language

    lang = default_language()
    text = CvText.of("2020 Lighthouse Festival\n2021 푸른바다미술관 개인전\n2022 예시미술관에서 단체전\n", lang)
    assert failures({"year": "2020", "venue": "Light"}, text) == ["venue"]
    # A Hangul name may not start inside a word, but a particle may follow it.
    assert failures({"year": "2021", "venue": "바다미술관"}, text) == ["venue"]
    assert failures({"year": "2022", "venue": "예시미술관"}, text) == []


def test_grounding_an_empty_venue_row_needs_its_title_in_the_cv():
    from giye.extract.grounding import CvText, failures

    text = CvText.of("2020  개인전 〈푸른 신호〉, 예시 공간\n2021 Open Studio / Night Garden\n")
    # An empty venue claims nothing, so the title is the claim (rule G-T).
    assert failures({"year": "2020", "venue": "", "title": "완전히 지어낸 개인전"}, text) == ["title"]
    assert failures({"year": "2020", "venue": "", "title": "푸른 신호"}, text) == []
    assert failures({"year": "2021", "venue": "", "title": "Open Studio / Night Garden"}, text) == []
    # One part of a recomposed title is enough: "Open Studio | Example" keeps the CV's part.
    assert failures({"year": "2021", "venue": "", "title": "Open Studio | Example Webinar"}, text) == []
    assert failures({"year": "2019", "venue": "", "title": "Invented"}, text) == ["year", "title"]
    # A row with a venue is not read for its title.
    assert failures({"year": "2020", "venue": "예시 공간", "title": "Fabricated Show"}, text) == []
