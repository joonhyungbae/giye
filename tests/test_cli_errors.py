# SPDX-License-Identifier: AGPL-3.0-only
"""A bad config or frames file is one line on stderr and exit status 2."""

from __future__ import annotations

from pathlib import Path

import pytest

from giye.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _write(tmp_path: Path, frames_text: str, *, collectors: str | None = None) -> Path:
    frames = tmp_path / "frames.yml"
    frames.write_text(frames_text, encoding="utf-8")
    modules = ""
    if collectors:
        module = tmp_path / "collectors.py"
        module.write_text(collectors, encoding="utf-8")
        modules = f'collector_modules = ["{module.as_posix()}"]\n'
    path = tmp_path / "giye.toml"
    path.write_text(
        f"""
[archive]
name = "Synthetic media-art field (demo)"
territory = "KR"
languages = ["ko", "en"]

[paths]
data = "{(tmp_path / "data").as_posix()}"
frames = "{frames.as_posix()}"

[publish]
site_url = "https://example.org"

[collect]
user_agent = "GiyeTest/0.1 (+https://example.org/contact)"
min_delay_s = 0.0
{modules}
""",
        encoding="utf-8",
    )
    return path


def _assert_one_line(code: int, err: str, needle: str) -> None:
    assert code == 2
    assert "Traceback" not in err
    assert err.startswith("giye: error:")
    assert needle in err
    assert err.strip().count("\n") == 0


def test_stages_reject_a_frames_file_that_is_not_a_mapping(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = _write(tmp_path, "[]\n")
    for stage in ("collect", "publish", "explore", "render"):
        code = main([stage, "--config", str(path)])
        err = capsys.readouterr().err
        _assert_one_line(code, err, "expected a mapping with a 'frames' list")


def test_collect_rejects_a_frame_the_frames_file_does_not_declare(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    frames = (ROOT / "tests" / "fixtures" / "frames_valid.yml").read_text(encoding="utf-8")
    path = _write(
        tmp_path,
        frames,
        collectors=(
            "from giye.collect import Edition, Person, RosterCollector\n"
            "class ExampleGrant(RosterCollector):\n"
            "    frame = 'EXAMPLE-GRANT'\n"
            "    def editions(self):\n"
            "        yield Edition(year=2019, people=[Person(name='김하늘')], source_url='https://example.org/grant')\n"
        ),
    )
    code = main(["collect", "--config", str(path)])
    err = capsys.readouterr().err
    _assert_one_line(code, err, "collector frame not declared")


def test_demo_outside_a_checkout_is_one_error_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(tmp_path)
    code = main(["demo"])
    err = capsys.readouterr().err
    _assert_one_line(code, err, "pass --config, or run from the repository")


def test_unexpected_errors_still_show_a_traceback(monkeypatch: pytest.MonkeyPatch):
    def boom(_args):
        raise RuntimeError("not a configuration problem")

    monkeypatch.setattr("giye.cli._name_keys", boom)
    with pytest.raises(RuntimeError, match="not a configuration problem"):
        main(["name-keys", "김하늘"])


def test_demo_into_an_existing_ledger_is_one_line(tmp_path: Path, capsys):
    """Software review round 6, minor 8: this printed a traceback."""
    (tmp_path / "ledger").mkdir()
    (tmp_path / "ledger" / "artists.csv").write_text("ledger_id\n", encoding="utf-8")
    assert main(["demo", "--output", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "already holds a ledger" in err
    assert "Traceback" not in err
