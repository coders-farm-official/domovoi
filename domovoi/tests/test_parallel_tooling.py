"""`pytest -n` support and the known-failures summary: the per-worker
database copies (domovoi/tests/xdist_db.py) and the labelling of known
failures (domovoi/tests/known_failures.py + known_failures.toml)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from domovoi.config import settings
from domovoi.tests import known_failures, xdist_db
from domovoi.tests.conftest import requires_db

REPO_ROOT = Path(__file__).resolve().parents[2]


# ─── worker database names and URLs ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("lane", "wid", "want"),
    [
        ("domovoi8_test", "gw3", "domovoi8_gw3_test"),
        ("domovoi_test", "gw0", "domovoi_gw0_test"),
        ("domovoi8_gw3_test", "gw3", "domovoi8_gw3_test"),  # a child pytest of worker gw3
        ("domovoi8_gw3_test", "gw4", "domovoi8_gw3_gw4_test"),
    ],
)
def test_worker_db_name(lane, wid, want):
    assert xdist_db.worker_db_name(lane, wid) == want
    assert want.endswith("_test")


def test_worker_db_name_refuses_a_database_that_is_not_a_test_one():
    with pytest.raises(ValueError):
        xdist_db.worker_db_name("domovoi", "gw1")


@pytest.mark.parametrize(("value", "want"), [("gw0", "gw0"), ("gw15", "gw15"), ("", None), ("master", None), ("gw", None)])
def test_worker_id(monkeypatch, value, want):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", value)
    assert xdist_db.worker_id() == want


def test_outside_xdist_the_lane_is_used_as_is(monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    url = "postgresql+asyncpg://domovoi:pw@127.0.0.1:6432/domovoi8_test"
    assert xdist_db.worker_database_url(url, create=True) == url


def test_a_worker_url_keeps_everything_but_the_database(monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    url = "postgresql+asyncpg://domovoi:s3cr%40t@127.0.0.1:6432/domovoi8_test?ssl=disable"
    got = make_url(xdist_db.worker_database_url(url, create=False))
    want = make_url(url)
    assert got.database == "domovoi8_gw7_test"
    assert (got.drivername, got.username, got.password, got.host, got.port, dict(got.query)) == (
        want.drivername, want.username, want.password, want.host, want.port, dict(want.query)
    )


def test_the_connection_budget_warning(monkeypatch):
    url = "postgresql+asyncpg://domovoi:pw@127.0.0.1:6432/domovoi8_test"

    async def server(_url):
        return 97, 10  # usable, in use

    monkeypatch.setattr(xdist_db, "_headroom", server)
    assert xdist_db.connection_warning(url, 4) is None  # 4 x 20 + 10 <= 97
    warning = xdist_db.connection_warning(url, 8)
    assert warning and "pytest -n 8" in warning and "up to 160" in warning
    assert "-n 4 fits now" in warning

    async def down(_url):
        raise OSError("no server")

    monkeypatch.setattr(xdist_db, "_headroom", down)
    assert xdist_db.connection_warning(url, 16) is None  # nothing to say, nothing breaks


def test_this_run_is_on_the_right_database():
    """Under -n this process is on its worker's own copy; serially, on the
    lane the conftest resolved (whatever its name, it ends in _test)."""
    db = make_url(settings.database_url).database
    assert db.endswith("_test")
    wid = xdist_db.worker_id()
    if wid is not None:
        assert db.endswith(f"_{wid}_test")


# ─── the copy itself, on a scratch lane of this test's own ───────────────────


async def _admin(url):
    import asyncpg

    return await asyncpg.connect(**xdist_db._connect_kwargs(url, "postgres"), timeout=10)


async def _scratch_lane(url, lane: str, rows: list[tuple[str, int]]) -> None:
    import asyncpg

    admin = await _admin(url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{lane}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{lane}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(**xdist_db._connect_kwargs(url, lane), timeout=10)
    try:
        await conn.execute(
            "CREATE TABLE flyway_schema_history (installed_rank int, version text, checksum int, success bool)"
        )
        for rank, (version, checksum) in enumerate(rows, 1):
            await conn.execute(
                "INSERT INTO flyway_schema_history VALUES ($1, $2, $3, true)", rank, version, checksum
            )
    finally:
        await conn.close()


async def _add_migration(url, lane: str, version: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(**xdist_db._connect_kwargs(url, lane), timeout=10)
    try:
        await conn.execute(
            "INSERT INTO flyway_schema_history VALUES ((SELECT count(*) + 1 FROM flyway_schema_history), $1, 7, true)",
            version,
        )
    finally:
        await conn.close()


async def _marker_in(url, db: str) -> bool:
    import asyncpg

    conn = await asyncpg.connect(**xdist_db._connect_kwargs(url, db), timeout=10)
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.marker') IS NOT NULL"))
    finally:
        await conn.close()


async def _drop(url, *names: str) -> None:
    admin = await _admin(url)
    try:
        for name in names:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _can_create_databases(url) -> bool:
    admin = await _admin(url)
    try:
        return bool(await admin.fetchval("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user"))
    finally:
        await admin.close()


@requires_db
def test_a_copy_is_made_reused_and_remade_when_the_lane_migrates():
    if xdist_db.worker_id() is not None:
        # Whole-database DDL in the middle of a parallel run, on a server
        # other lanes share, is more load than this check is worth there.
        pytest.skip("creates and drops databases: serial runs only")
    url = make_url(settings.database_url)
    if not xdist_db._run(_can_create_databases(url)):
        pytest.skip("the test database's role can't create databases")
    lane = f"domovoi_xdistcheck_{os.getpid()}_test"
    copy = xdist_db.worker_db_name(lane, "gw99")
    try:
        xdist_db._run(_scratch_lane(url, lane, [("001", 11), ("002", 22)]))
        assert xdist_db._run(xdist_db._ensure(url, lane, copy)) == "created"
        assert xdist_db._run(xdist_db._ensure(url, lane, copy)) == "reused"

        # Something only the copy has: a reused copy keeps it...
        async def mark():
            import asyncpg

            conn = await asyncpg.connect(**xdist_db._connect_kwargs(url, copy), timeout=10)
            try:
                await conn.execute("CREATE TABLE marker (x int)")
            finally:
                await conn.close()

        xdist_db._run(mark())
        assert xdist_db._run(xdist_db._ensure(url, lane, copy)) == "reused"
        assert xdist_db._run(_marker_in(url, copy))

        # ...and a lane that migrated since gets a fresh copy.
        xdist_db._run(_add_migration(url, lane, "003"))
        assert xdist_db._run(xdist_db._ensure(url, lane, copy)) == "created"
        assert not xdist_db._run(_marker_in(url, copy))
    finally:
        xdist_db._run(_drop(url, copy, lane))


@requires_db
def test_a_missing_lane_says_how_to_make_one():
    url = make_url(settings.database_url)
    lane = f"domovoi_xdistcheck_missing_{os.getpid()}_test"
    with pytest.raises(RuntimeError, match="does not exist"):
        xdist_db._run(xdist_db._ensure(url, lane, xdist_db.worker_db_name(lane, "gw98")))


# ─── known failures ──────────────────────────────────────────────────────────


def test_the_shipped_list_is_well_formed():
    entries = known_failures.load()
    assert entries, "an empty list is fine in principle, but then this test should say so"
    seen = set()
    for e in entries:
        assert e.test not in seen, f"listed twice: {e.test}"
        seen.add(e.test)
        assert re.fullmatch(r"[0-9a-f]{7,40}", e.since), e
        path = e.test.split("::", 1)[0]
        assert (REPO_ROOT / path).is_file(), f"{e.test}: no such file {path}"


@pytest.mark.parametrize(
    ("pattern", "nodeid", "hit"),
    [
        ("a/test_x.py::test_y", "a/test_x.py::test_y", True),
        ("a/test_x.py::test_y", "a/test_x.py::test_y2", False),
        ("a/test_x.py::test_y[p1]", "a/test_x.py::test_y[p1]", True),
        ("a/test_x.py::test_y[p1]", "a/test_x.py::test_y[1]", False),  # brackets are literal
        ("a/test_x.py::*", "a/test_x.py::test_z[3]", True),
        ("a/test_x.py::test_*[a.b]", "a/test_x.py::test_q[a.b]", True),
        ("a/test_x.py::test_*[a.b]", "a/test_x.py::test_q[aXb]", False),  # . is literal
    ],
)
def test_matching(pattern, nodeid, hit):
    assert known_failures.Known(pattern, "r", "abc1234").matches(nodeid) is hit


@pytest.mark.parametrize(
    ("nodeid", "plain"),
    [
        ("a.py::t[x]@pg-roles", "a.py::t[x]"),  # --dist loadgroup's suffix
        ("a.py::t@tmp-domovoi-req-core", "a.py::t"),
        ("a.py::t[p@ss,word,12345]", "a.py::t[p@ss,word,12345]"),  # an @ inside an id stays
        ("a.py::t[p@ss]@pg-roles", "a.py::t[p@ss]"),
        ("a.py::t", "a.py::t"),
    ],
)
def test_the_xdist_group_suffix_is_not_part_of_the_name(nodeid, plain):
    assert known_failures.plain_nodeid(nodeid) == plain
    entry = known_failures.Known(plain, "r", "abc1234")
    assert known_failures.find([entry], nodeid) is entry


@pytest.mark.parametrize(
    "body",
    [
        '[[failure]]\ntest = "a::b"\nreason = "r"\n',  # no since
        '[[failure]]\ntest = "a::b"\nreason = ""\nsince = "abc1234"\n',  # empty reason
        '[[failure]]\ntest = "a::b"\nreason = "r"\nsince = "abc1234"\nowner = "x"\n',  # unknown key
        '[[failures]]\ntest = "a::b"\n',  # wrong table name
        "this is not toml",
    ],
)
def test_a_malformed_list_is_refused(tmp_path, body):
    p = tmp_path / "known_failures.toml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        known_failures.load(p)


class _Reporter:
    """Just enough of pytest's TerminalReporter."""

    def __init__(self, stats):
        self.stats = stats
        self.lines: list[str] = []

    def write_sep(self, sep, title, **markup):
        self.lines.append(f"{sep * 3} {title}")

    def write_line(self, line, **markup):
        self.lines.append(line)


def _rep(nodeid, when="call"):
    return SimpleNamespace(nodeid=nodeid, when=when)


ENTRIES = [
    known_failures.Known("t/test_a.py::test_known", "flaky route lookup", "abc1234"),
    known_failures.Known("t/test_b.py::*", "missing optional dependency", "abc1234"),
    known_failures.Known("t/test_c.py::test_fixed", "used to fail", "def5678"),
]


def test_summary_sorts_new_from_known_and_feeds_the_last_line():
    tr = _Reporter({
        "failed": [_rep("t/test_a.py::test_known"), _rep("t/test_z.py::test_mine")],
        "error": [_rep("t/test_b.py::test_setup", when="setup")],
        "passed": [_rep("t/test_c.py::test_fixed"), _rep("t/test_d.py::test_ok")],
    })
    known_failures.summarize(tr, ENTRIES)
    text = "\n".join(tr.lines)
    assert "known failures: 1 new, 2 known" in text
    assert "NEW    t/test_z.py::test_mine" in text
    assert "known  t/test_a.py::test_known  [since abc1234] flaky route lookup" in text
    assert "known  t/test_b.py::test_setup (error in setup)" in text
    assert "passed t/test_c.py::test_fixed" in text  # listed, matched no failure, passed
    assert "test_d" not in text
    # The failures themselves stay where pytest keeps them: still failed.
    assert len(tr.stats["failed"]) == 2 and len(tr.stats["error"]) == 1
    assert [r.nodeid for r in tr.stats["new failure"]] == ["t/test_z.py::test_mine"]
    assert len(tr.stats["known failures"]) == 2


def test_summary_is_silent_on_a_clean_run():
    tr = _Reporter({"passed": [_rep("t/test_d.py::test_ok")]})
    known_failures.summarize(tr, ENTRIES)
    assert tr.lines == []
    assert set(tr.stats) == {"passed"}


def test_summary_all_known():
    tr = _Reporter({"failed": [_rep("t/test_a.py::test_known")]})
    known_failures.summarize(tr, ENTRIES)
    assert "known failures: 0 new, 1 known" in tr.lines[0]
    assert "new failure" not in tr.stats and "new failures" not in tr.stats
    assert len(tr.stats["known failure"]) == 1
