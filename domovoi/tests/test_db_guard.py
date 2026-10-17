"""The core suite never connects to a Postgres by accident (2026-10-17).

A run with neither TEST_DATABASE_URL nor DATABASE_URL set fell back to
localhost:6432, where the installed Domovoi on the same PC keeps its live
database, and tried to log in to it. domovoi/tests/db_guard.py stops that
before anything connects. Pinned here: the rule itself, that the conftest
applies it before the first connection, and that a fresh interpreter
importing the conftest with no database variables stops with the message
(the dev compose workflow still works with its one opt-in line).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi.tests import db_guard

REPO = Path(__file__).resolve().parents[2]
LANE = "postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6433/domovoi3_test"
LIVE = "postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi_test"


@pytest.mark.parametrize("env, resolved, refused", [
    ({}, LIVE, "neither TEST_DATABASE_URL nor DATABASE_URL"),
    ({"TEST_DATABASE_URL": LANE}, LANE, None),
    ({"TEST_DATABASE_URL": LIVE}, LIVE, "port 6432"),
    ({"DATABASE_URL": LIVE.replace("_test", "")}, LIVE, "port 6432"),
    ({"TEST_DATABASE_URL": "postgresql+asyncpg://domovoi:domovoi@localhost:6432/x_test"},
     "postgresql+asyncpg://domovoi:domovoi@localhost:6432/x_test", "port 6432"),
    ({"TEST_DATABASE_URL": LIVE, "DOMOVOI_TEST_DB_ON_6432": "1"}, LIVE, None),
    ({"TEST_DATABASE_URL": LIVE, "DOMOVOI_TEST_DB_ON_6432": "yes"}, LIVE, "port 6432"),
    ({"TEST_DATABASE_URL": "postgresql://u:p@db.example/x_test"},
     "postgresql://u:p@db.example/x_test", None),          # no port: 5432
])
def test_the_rule(env, resolved, refused):
    got = db_guard.refusal(env, resolved)
    if refused is None:
        assert got is None
    else:
        assert got and refused in got and "TEST_DATABASE_URL" in got


def test_the_port_is_read_from_the_url():
    assert db_guard.port_of(LIVE) == 6432
    assert db_guard.port_of("postgresql+asyncpg://u@h/db") == 5432
    assert db_guard.port_of("not a url") is None


def test_the_conftest_refuses_before_anything_connects():
    src = (REPO / "domovoi" / "tests" / "conftest.py").read_text(encoding="utf-8")
    guard = src.index("_db_guard.refusal(os.environ, _candidate_db)")
    assert guard < src.index('os.environ["DATABASE_URL"] = _candidate_db')
    assert guard < src.index("from domovoi.config import settings")
    assert guard < src.index("_tcp_reachable(_resolved_db)")


def _import_conftest(env_overrides: dict[str, str | None]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key in ("TEST_DATABASE_URL", "DATABASE_URL", db_guard.ALLOW_ENV):
        env.pop(key, None)
    for key, value in env_overrides.items():
        if value is not None:
            env[key] = value
    # Only as far as the guard: a stand-in for `import conftest` that runs
    # its module code up to the refusal, in a fresh interpreter.
    code = (
        "import runpy, sys\n"
        "try:\n"
        "    runpy.run_path(sys.argv[1], run_name='conftest_probe')\n"
        "except RuntimeError as e:\n"
        "    print('REFUSED:', e); sys.exit(3)\n"
        "except BaseException as e:\n"
        "    print('PASSED THE GUARD:', type(e).__name__); sys.exit(0)\n"
        "print('PASSED THE GUARD'); sys.exit(0)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(REPO / "domovoi" / "tests" / "conftest.py")],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120,
    )


def test_a_run_with_no_database_variable_stops_with_the_message():
    r = _import_conftest({})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "neither TEST_DATABASE_URL nor DATABASE_URL is set" in r.stdout


def test_a_run_aimed_at_6432_stops_unless_it_says_so():
    r = _import_conftest({"TEST_DATABASE_URL": "postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi_test"})
    assert r.returncode == 3 and "port 6432" in r.stdout, r.stdout + r.stderr


def test_the_xdist_drop_command_refuses_the_same(monkeypatch, capsys):
    from domovoi.tests import xdist_db

    monkeypatch.delenv("DOMOVOI_TEST_DB_ON_6432", raising=False)
    assert xdist_db.main(["--list", "--url", LIVE]) == 2
    assert "port 6432" in capsys.readouterr().err
