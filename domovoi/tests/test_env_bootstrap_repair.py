"""``python -m domovoi.env_bootstrap --repair``: an upgraded install gets its
own helper-container secrets (2026-10 review REV-16).

``docker-compose.yml`` no longer falls back to the ``LETTA_TOKEN`` /
``SEARXNG_SECRET`` literals every install shared; it refuses to run without
them. A fresh ``.env`` has always been written with random ones, so the
repair is for the ``.env`` files written before that: it APPENDS a value
for each one the file lacks (or leaves empty, or sets to an old shared
literal), and changes nothing else. Idempotent. ``dev.sh`` / ``dev.ps1``
and the update unit run it before compose (see test_dev_scripts_searxng.py
and scripts/linux/tests/test-apply-update.sh).

Pure file work on ``tmp_path``: no Docker, no DB, never skips except the
POSIX file-mode checks on Windows.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

import pytest

from domovoi import env_bootstrap as eb

TOKEN = re.compile(r"[A-Za-z0-9_-]{32}")

OLD_ENV = (
    "# my household's settings\n"
    "DATABASE_URL=postgresql+asyncpg://domovoi:s3cret@localhost:6432/domovoi\n"
    "POSTGRES_PASSWORD=s3cret\n"
    "SATELLITE_PAIRING_STRICT=true\n"
    "# LETTA_TOKEN=domovoi-local\n"
)


def _write(path: Path, text: str | bytes) -> Path:
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


def _counter():
    n = iter(range(1, 100))
    return lambda: f"generated-{next(n):02d}-" + "x" * 19


def test_an_old_env_gets_both_secrets_appended_and_nothing_else_changes(tmp_path) -> None:
    env = _write(tmp_path / ".env", OLD_ENV)
    before = env.read_bytes()

    result = eb.repair_env_file(env)

    after = env.read_bytes()
    assert result.added == ("LETTA_TOKEN", "SEARXNG_SECRET") and not result.created
    assert after.startswith(before), "a byte of the existing file changed"
    values = eb.env_values(after.decode("utf-8"))
    assert TOKEN.fullmatch(values["LETTA_TOKEN"])
    assert TOKEN.fullmatch(values["SEARXNG_SECRET"])
    assert values["LETTA_TOKEN"] != values["SEARXNG_SECRET"]
    # Everything the household set reads back the same.
    assert values["POSTGRES_PASSWORD"] == "s3cret"
    assert values["SATELLITE_PAIRING_STRICT"] == "true"
    assert eb.missing_helper_secrets(after.decode("utf-8")) == []


def test_a_second_repair_writes_nothing(tmp_path) -> None:
    env = _write(tmp_path / ".env", OLD_ENV)
    eb.repair_env_file(env)
    once = env.read_bytes()

    again = eb.repair_env_file(env)

    assert again.added == ()
    assert env.read_bytes() == once


def test_only_the_missing_one_is_added(tmp_path) -> None:
    env = _write(tmp_path / ".env", OLD_ENV + "LETTA_TOKEN=mine-already\n")
    result = eb.repair_env_file(env, generate=_counter())
    assert result.added == ("SEARXNG_SECRET",)
    values = eb.env_values(env.read_text(encoding="utf-8"))
    assert values["LETTA_TOKEN"] == "mine-already"
    assert values["SEARXNG_SECRET"].startswith("generated-01-")
    live = [l for l in env.read_text(encoding="utf-8").splitlines() if l.startswith("LETTA_TOKEN=")]
    assert live == ["LETTA_TOKEN=mine-already"]


@pytest.mark.parametrize("line", [
    "LETTA_TOKEN=",                       # set empty: compose's :? refuses it
    "LETTA_TOKEN=   ",
    "LETTA_TOKEN= # filled in later",     # an unquoted value ends at " #"
    "LETTA_TOKEN=domovoi-local",          # the value every install shared
    "LETTA_TOKEN=\"domovoi-local\"",
    "LETTA_TOKEN=good-value\nLETTA_TOKEN=",  # the last assignment wins
])
def test_empty_or_shared_counts_as_missing(tmp_path, line) -> None:
    env = _write(tmp_path / ".env", OLD_ENV + line + "\nSEARXNG_SECRET=fine-one\n")
    result = eb.repair_env_file(env, generate=_counter())
    assert result.added == ("LETTA_TOKEN",)
    values = eb.env_values(env.read_text(encoding="utf-8"))
    assert values["LETTA_TOKEN"].startswith("generated-")


@pytest.mark.parametrize("line", [
    "LETTA_TOKEN=abc",
    "export LETTA_TOKEN=abc",
    "  LETTA_TOKEN = abc  ",
    "LETTA_TOKEN='abc'",
])
def test_any_spelling_compose_reads_counts_as_set(tmp_path, line) -> None:
    env = _write(tmp_path / ".env", line + "\nSEARXNG_SECRET=fine\n")
    assert eb.repair_env_file(env).added == ()


def test_the_appended_value_is_what_the_core_reads_too(tmp_path) -> None:
    """The core reads .env through python-dotenv (pydantic-settings); its
    last assignment must be the repaired one, as compose's is."""
    dotenv = pytest.importorskip("dotenv")
    env = _write(tmp_path / ".env", OLD_ENV + "LETTA_TOKEN=domovoi-local\n")
    eb.repair_env_file(env)
    ours = eb.env_values(env.read_text(encoding="utf-8"))
    theirs = dotenv.dotenv_values(env)
    assert theirs["LETTA_TOKEN"] == ours["LETTA_TOKEN"] != "domovoi-local"
    assert theirs["SEARXNG_SECRET"] == ours["SEARXNG_SECRET"]


def test_crlf_files_get_crlf_lines(tmp_path) -> None:
    env = _write(tmp_path / ".env", OLD_ENV.replace("\n", "\r\n"))
    eb.repair_env_file(env)
    raw = env.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_a_file_without_a_final_newline_is_not_glued_to(tmp_path) -> None:
    env = _write(tmp_path / ".env", "POSTGRES_PASSWORD=s3cret")
    eb.repair_env_file(env)
    values = eb.env_values(env.read_text(encoding="utf-8"))
    assert values["POSTGRES_PASSWORD"] == "s3cret"
    assert TOKEN.fullmatch(values["LETTA_TOKEN"])


def test_no_env_at_all_gets_one_with_only_the_secrets(tmp_path) -> None:
    """A box that ran on code defaults with no .env must keep them: the new
    file carries the two secrets and nothing from .env.example."""
    env = tmp_path / ".env"
    result = eb.repair_env_file(env)
    assert result.created and result.added == ("LETTA_TOKEN", "SEARXNG_SECRET")
    values = eb.env_values(env.read_text(encoding="utf-8"))
    assert set(values) == {"LETTA_TOKEN", "SEARXNG_SECRET"}
    assert not env.read_text(encoding="utf-8").startswith("\n")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_modes_are_kept_or_private(tmp_path) -> None:
    env = _write(tmp_path / ".env", OLD_ENV)
    os.chmod(env, 0o640)
    eb.repair_env_file(env)
    assert stat.S_IMODE(env.stat().st_mode) == 0o640
    fresh = tmp_path / "fresh" / ".env"
    eb.repair_env_file(fresh)
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600


def test_a_fresh_env_needs_no_repair(tmp_path) -> None:
    """What ensure_env_file writes already satisfies compose."""
    example = tmp_path / ".env.example"
    example.write_bytes((Path(eb.__file__).parent / ".env.example").read_bytes())
    env = tmp_path / ".env"
    eb.ensure_env_file(env, example, volume_exists=lambda: False)
    before = env.read_bytes()
    assert eb.repair_env_file(env).added == ()
    assert env.read_bytes() == before


def test_the_plain_call_still_never_touches_an_existing_env(tmp_path, monkeypatch, capsys) -> None:
    env = _write(tmp_path / ".env", OLD_ENV)
    monkeypatch.setattr(eb, "ENV_FILE", env)
    assert eb.main([]) == 0
    assert env.read_bytes() == OLD_ENV.encode("utf-8")


def test_the_cli_says_what_it_added_then_that_nothing_was_needed(tmp_path, monkeypatch, capsys) -> None:
    env = _write(tmp_path / ".env", OLD_ENV)
    monkeypatch.setattr(eb, "ENV_FILE", env)
    assert eb.main(["--repair"]) == 0
    first = capsys.readouterr().out
    # The update unit reads this line: "<path>: added <KEY>, <KEY> (...)".
    assert f"{env}: added LETTA_TOKEN, SEARXNG_SECRET (" in first
    assert eb.main(["--repair"]) == 0
    assert "already set; left untouched" in capsys.readouterr().out


def test_the_cli_fails_loudly_when_it_cannot_write(tmp_path, monkeypatch, capsys) -> None:
    env = _write(tmp_path / ".env", OLD_ENV)
    monkeypatch.setattr(eb, "ENV_FILE", env)

    def refuse(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(eb.os, "open", refuse)
    assert eb.main(["--repair"]) == 1
    assert "could not repair" in capsys.readouterr().err


def test_repair_and_internet_are_separate_calls() -> None:
    with pytest.raises(SystemExit):
        eb.main(["--repair", "--internet", "always"])
