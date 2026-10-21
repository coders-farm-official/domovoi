from __future__ import annotations

import asyncio
import os
import socket
import sys

# The home first: HOME/USERPROFILE at a temp folder before anything computes
# a path from them (the repo-root conftest.py has already done this, and
# guards the real ~/.domovoi; this covers a run that starts below the root,
# a plugin repo importing this conftest for instance).
from domovoi.tests import home_isolation as _home_isolation  # imports nothing of domovoi

_home_isolation.isolate()

# Force stub clients for the entire test run — BEFORE any domovoi import.
# Real clients would try to load Whisper models, connect to Ollama, etc., which
# the test suite shouldn't require.
os.environ.setdefault("USE_STUBS", "true")

# The suite never reaches a live service on the machine running it. A
# Domovoi installed on the same PC answers at the defaults (its core on
# :6370, Ollama on :11434, SearXNG on :6888), and tests that made the web
# forward to the core without pointing it anywhere reached that live core.
# Every service URL a test could fall through to is pinned to a dead port
# here, before anything imports domovoi (the settings read it once) and
# whatever the shell exported; a test that wants a service sets it itself
# (monkeypatch.setenv, or monkeypatch.setattr on settings). A new
# localhost default in the code must join this list:
# domovoi/tests/test_service_isolation.py fails until it does. The test
# database is configured separately, below.
DEAD_SERVICE_URL = "http://127.0.0.1:9"
LIVE_SERVICE_ENV = ("DOMOVOI_URL", "OLLAMA_URL", "SEARXNG_URL", "LETTA_BASE_URL")
for _key in [k for k in os.environ if k.upper() in LIVE_SERVICE_ENV]:
    os.environ.pop(_key, None)
for _key in LIVE_SERVICE_ENV:
    os.environ[_key] = DEAD_SERVICE_URL


def _resolve_test_database_url() -> str:
    """Pick the database pytest will run against.

    Priority:
      1. ``TEST_DATABASE_URL`` env var if set (explicit override).
      2. Auto-derive from ``DATABASE_URL`` by replacing the dbname with
         ``<dbname>_test``. So if prod is at
         ``...:6432/domovoi``, tests hit ``...:6432/domovoi_test``.
      3. Neither: the old local default, which ``db_guard`` refuses
         before anything connects (it is the live install's port).

    The test DB must exist + be migrated; see the README's "Test
    database setup" section.
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    prod = os.environ.get("DATABASE_URL")
    if prod:
        # Append `_test` to the dbname (the path component after the last /).
        # Robust to user/password/port present or absent in the URL.
        if "/" in prod:
            head, sep, dbname = prod.rpartition("/")
            # Strip query string if any — pydantic/sqlalchemy may have it.
            dbname_only, _, query = dbname.partition("?")
            new_db = f"{dbname_only}_test"
            return f"{head}{sep}{new_db}{('?' + query) if query else ''}"
    return "postgresql+asyncpg://domovoi:domovoi@localhost:6432/domovoi_test"


# Never a database by accident (domovoi/tests/db_guard.py): no variable set,
# or port 6432 (where an installed Domovoi keeps its live Postgres) without
# the explicit DOMOVOI_TEST_DB_ON_6432=1, stops the run here, before
# anything connects.
from domovoi.tests import db_guard as _db_guard  # noqa: E402  (stdlib only)

_candidate_db = _resolve_test_database_url()
_refused = _db_guard.refusal(os.environ, _candidate_db)
if _refused:
    raise RuntimeError(_refused)

# Override DATABASE_URL with the test variant BEFORE importing settings.
# Pydantic-settings reads env vars first, then .env, then defaults — so
# pinning os.environ here wins over whatever .env says. This is the
# single line that protects the core's prod DB from pytest.
os.environ["DATABASE_URL"] = _candidate_db

# Safety belt: refuse to run if the resolved DB doesn't look like a test
# DB. Catches the contamination class of bug where TEST_DATABASE_URL
# accidentally points at prod (typo, copy-paste, env-var leak from a
# parent shell).
_resolved_db = os.environ["DATABASE_URL"]
_dbname = _resolved_db.rsplit("/", 1)[-1].partition("?")[0]
if not _dbname.endswith("_test"):
    raise RuntimeError(
        f"Refusing to run tests against database {_dbname!r} "
        f"(URL: {_resolved_db}). The test conftest TRUNCATEs "
        f"library_tracks / voice_profiles / voice_notes / etc. — "
        f"running it against prod would wipe real data. Set "
        f"TEST_DATABASE_URL to a database whose name ends with '_test', "
        f"or unset it to auto-derive 'domovoi_test' from "
        f"DATABASE_URL."
    )


def _tcp_reachable(url: str) -> bool:
    """The same cheap probe as ``_db_reachable`` below, on a URL string
    (settings aren't built yet here)."""
    try:
        host_port = url.split("@", 1)[1].split("/", 1)[0]
        host, _, port_s = host_port.rpartition(":")
        with socket.create_connection((host, int(port_s)), timeout=1.0):
            return True
    except (OSError, ValueError, IndexError):
        return False


# Under pytest-xdist (`pytest -n N`) every worker runs on its own copy of
# the lane resolved above — `domovoi8_test` becomes `domovoi8_gw3_test` for
# worker gw3 — made from it on demand and reused (domovoi/tests/xdist_db.py).
# A serial run never gets here: it uses the lane itself, as it always has.
from domovoi.tests import xdist_db as _xdist_db  # noqa: E402  (imports nothing of domovoi)

if _xdist_db.worker_id() is not None:
    os.environ["DATABASE_URL"] = _xdist_db.worker_database_url(
        _resolved_db, create=_tcp_reachable(_resolved_db)
    )
    _resolved_db = os.environ["DATABASE_URL"]
    _dbname = _resolved_db.rsplit("/", 1)[-1].partition("?")[0]
    assert _dbname.endswith("_test"), _dbname  # worker copies keep the suffix

import pytest
import pytest_asyncio
from sqlalchemy import text

# Contract D21, at import time too: the internet answer must not shape the
# suite. ``domovoi.config`` builds ``settings`` (the followers of the answer,
# and HF_HUB_OFFLINE under never) when it is first imported, from the
# process environment and ``domovoi/.env`` — and an ft run in a worktree
# writes ``INTERNET_ACCESS=never`` into that .env. So: drop any
# INTERNET_ACCESS the shell exported, shadow the .env with an empty value
# while settings are built, and drop it again, so nothing at runtime sees an
# answer pinned in the environment (the autouse fixture below pins the
# policy itself to unset for every test).
for _key in [k for k in os.environ if k.upper() == "INTERNET_ACCESS"]:
    os.environ.pop(_key, None)
_hf_hub_offline_before = os.environ.get("HF_HUB_OFFLINE")
os.environ["INTERNET_ACCESS"] = ""
try:
    from domovoi.config import settings
finally:
    os.environ.pop("INTERNET_ACCESS", None)
if os.environ.get("HF_HUB_OFFLINE") != _hf_hub_offline_before:  # belt and braces
    if _hf_hub_offline_before is None:
        os.environ.pop("HF_HUB_OFFLINE", None)
    else:
        os.environ["HF_HUB_OFFLINE"] = _hf_hub_offline_before
# A domovoi/.env naming a folder inside the real ~/.domovoi (VOICE_MODELS_DIR=…)
# would still aim the settings there: the same folder under the temp home.
for _name in type(settings).model_fields:
    _moved = _home_isolation.relocate(getattr(settings, _name))
    if _moved is not None:
        setattr(settings, _name, _moved)
from domovoi.db.session import SessionLocal, engine

# Per-test truncation set for the fresh V001 baseline. This is the CHURNED
# data — everything a test can write. Deliberately NOT truncated:
#   * reference/seed tables (client_greetings, voices, wake_words,
#     mpd_rooms, document_sessions, model_jobs) — seeded by the baseline or
#     managed by the tests that exercise them directly;
#   * `plugins` and `registered_values` — registry data owned by core boot /
#     plugin registration, not by individual tests.
TABLES_TO_TRUNCATE = [
    "timers",
    # The owner's "say it like" changes (V027); the defaults are a data file.
    "pronunciations",
    "pronunciations_changed",
    "library_tracks",
    # Generic media-acquisition queue.
    "media_acquisitions",
    "sessions",
    "intents_log",
    "conversation_log",
    "calendar_events",
    "voice_notes",
    "connectivity_events",
    "voice_profiles",
    "voice_denylist",
    "web_search_prefs",
    "memories",
    "favorites",
    "people",
    # Playlists. Listed AFTER library_tracks so the CASCADE-via-FK from
    # library_tracks doesn't have to do the work twice (truncating
    # playlist_tracks directly is faster than depending on a cascade from
    # library_tracks since the CASCADE on the playlist_tracks FK to
    # library_tracks is the only inbound edge).
    "playlists",
    "playlist_tracks",
    # Per-room media play history (event data, like intents_log /
    # conversation_log).
    "media_plays",
    # Two-way drop-in call audit rows (event data).
    "dropin_calls",
    # Spoken audio (podcasts + audiobooks + resume positions).
    # playback_positions FKs people (ON DELETE CASCADE) and podcast_episodes
    # FKs podcast_subscriptions (ON DELETE CASCADE); RESTART IDENTITY CASCADE
    # handles the ordering, but list children first for clarity.
    "playback_positions",
    "podcast_episodes",
    "podcast_subscriptions",
    "audiobooks",
    # News. Children first (news_items / news_briefings / topic_feeds all FK
    # news_topics / news_feeds / people); RESTART IDENTITY CASCADE handles
    # ordering, but list them explicitly for clarity.
    "news_items",
    "news_briefings",
    "topic_feeds",
    "news_topics",
    "news_feeds",
    # Admin auth (single-row credential + bearer sessions) — test state for
    # the auth suite, never reference data.
    "admin_auth",
    "admin_sessions",
    # Satellite WS pairing rows (V002) — test state, never reference data.
    "satellite_pairings",
    # Satellite inventory rows (V003) — adoption/type metadata written by
    # tests, never reference data.
    "satellites",
    # Printers and scanners (V033) and their job log. print_limits is a
    # seeded single row; the printing tests reset it themselves.
    "print_jobs",
    "scan_jobs",
    "printer_room_defaults",
    "printers",
]


def _db_reachable() -> bool:
    """Cheap TCP probe of the DB host:port. Avoids spinning up asyncio or touching
    the shared SQLAlchemy engine during test collection (which could poison the
    connection pool with connections bound to a throwaway event loop).
    """
    url = settings.database_url
    try:
        tail = url.split("@", 1)[1]
        host_port = tail.split("/", 1)[0]
        host, _, port_s = host_port.rpartition(":")
        port = int(port_s)
    except Exception:
        return False
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


_DB_OK = _db_reachable()

requires_db = pytest.mark.skipif(
    not _DB_OK,
    reason="Postgres not reachable at DATABASE_URL — start with `docker compose up -d postgres flyway`.",
)


def fast_path_winner(transcript: str) -> str | None:
    """Replicate the router's normalization + band-ordered fast-path scan
    WITHOUT dispatching — the §13.2 collision-corpus dry run. Returns the
    winning handler's name, or None when nothing fast-paths (the utterance
    would fall through to LLM tool routing). DB-free by construction, so a
    routing assertion built on it can never hide behind ``requires_db``.

    Imports lazily so conftest doesn't load the whole handler registry
    for every test module that never touches routing.
    """
    from domovoi.handlers import HANDLERS
    from domovoi.router import _LEADING_FILLER_RE

    t = transcript.lower().strip().rstrip(".,!?")
    t = _LEADING_FILLER_RE.sub("", t)
    for handler in HANDLERS:
        for fp in handler.fast_paths:
            if fp.pattern.match(t):
                return handler.name
    return None


@pytest.fixture(autouse=True)
def _isolate_admin_config_dir(tmp_path, monkeypatch):
    """Point the admin-auth config dir (setup-code.txt home) at a tmp
    dir for EVERY test. The core lifespan's first-run hook writes the
    setup code whenever no admin credential exists — without this,
    any test that enters the app lifespan would drop files into the
    developer's real ~/.domovoi."""
    from domovoi import admin_auth

    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path / "domovoi-config")


@pytest.fixture(autouse=True)
def _isolate_update_state(tmp_path, monkeypatch):
    """Same idea for the update unit's files: a test that pulls must not
    record a prev_sha in the developer's real ~/.domovoi/update, and the
    version panel must not report a real host's last update. Nor may a
    real domovoi-update.service on the machine running pytest (a Linux
    server that has it installed) flip every restart test into update
    mode: the unit search path points at an empty dir unless a test sets
    its own. A restart one test accepted is not under way in the next
    (self_restart._REQUEST), or every later restart and pull is refused."""
    from domovoi import self_restart

    monkeypatch.setattr(settings, "update_state_dir", str(tmp_path / "update-state"))
    monkeypatch.setattr(
        settings, "update_result_file", str(tmp_path / "update-unit" / "last-result.json")
    )
    monkeypatch.setattr(self_restart, "_UNIT_DIRS", (str(tmp_path / "no-systemd-units"),))
    monkeypatch.setattr(self_restart, "_REQUEST", None)


@pytest.fixture(autouse=True)
def _isolate_command_captures(tmp_path, monkeypatch):
    """Opt-in command recordings (domovoi/command_captures.py) land under a
    tmp dir for EVERY test: a test that routes a voice turn in a room some
    other test opted in must never write household-speech-shaped files
    into the developer's real ~/.domovoi/captures."""
    monkeypatch.setattr(settings, "command_captures_dir", str(tmp_path / "command-captures"))


@pytest.fixture(autouse=True)
def _internet_answer_unset():
    """Every test starts with the internet answer UNSET (today's
    behaviour), whatever the machine says: a stray ``domovoi/.env`` (an ft
    run in a worktree writes one) or an ``INTERNET_ACCESS`` exported in
    the developer's shell must never flip the suite (contract D21). A test
    that wants an answer nests ``egress.override_policy("never")``; the
    innermost wins. ``read_policy`` tests pass ``env_path=`` / ``environ=``
    and bypass this."""
    from domovoi import egress

    with egress.override_policy(""):
        yield


@pytest.fixture(autouse=True)
def _not_shutting_down():
    """No test starts inside a shutdown. A core lifespan teardown (every
    TestClient that enters the app leaves it again) marks the process as
    shutting down (domovoi/lifecycle.py), and from then on the timer
    delivery pops nothing and claims no room — which is right for a core
    that is stopping, and wrong for the next test in the same process."""
    from domovoi import lifecycle

    lifecycle.reset()
    yield
    lifecycle.reset()


def _armed_loop_watchdog() -> bool:
    """Stand down a real loop watchdog a test left armed; whether it did."""
    lw = sys.modules.get("domovoi.loop_watchdog")
    if lw is None or (lw._ACTIVE is None and not lw._INSTALLED):
        return False
    if lw._ACTIVE is not None:
        lw._ACTIVE.stop()
    lw._ACTIVE, lw._INSTALLED = None, False
    return True


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """No test may arm the real loop watchdog (domovoi/loop_watchdog.py
    install()): it writes the ready note and the faulthandler file under
    the real ~/.domovoi and leaves a thread watching the worker for every
    later test. A test that calls main() stubs install(); one that tests
    install() itself monkeypatches its home, _ACTIVE and _INSTALLED. Looked
    at once every fixture is torn down (monkeypatch undone): what is still
    armed is stood down, and the test errors."""
    result = yield
    if _armed_loop_watchdog():
        raise AssertionError(
            f"{item.nodeid} armed the real loop watchdog (loop_watchdog.install): stub "
            "it, or monkeypatch its home, _ACTIVE and _INSTALLED")
    return result


@pytest_asyncio.fixture
async def db_session():
    """Fresh session per test, with all tables truncated first."""
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE"))
    async with SessionLocal() as s:
        yield s
        await s.rollback()


# ─── pytest -n (pytest-xdist) and the known-failures summary ────────────────
#
# `pytest -n N` runs the suite on N worker processes, each on its own copy of
# the lane database (above, and domovoi/tests/xdist_db.py). What a database
# per worker can't separate is marked in the test modules:
#
#   bash_harness  a whole bash harness (scripts/linux/tests/test-*.sh) run
#                 under Git Bash: 15-70 minutes on one worker, which would
#                 set the wall time of the whole parallel run. Deselected
#                 under -n; scripts/dev/run-linux-harness.sh runs them in a
#                 Linux container in a minute or two (beside the pytest run).
#   xdist_group   tests that share something outside their process and so
#                 must not overlap each other run on one worker, in turn:
#                 "pg-roles" — the modules that create or drop plugin roles
#                 (roles belong to the Postgres server, not to a database);
#                 "tmp-domovoi-req-core" — the two tests of the satellite
#                 image's requirements filter, which uses a fixed /tmp file.
#                 Groups need --dist loadgroup, so that is what -n means
#                 here unless --dist is given.
#
# A serial run (no -n) runs everything, exactly as before.


# The bash_harness tests a worker left out, for the controller's summary.
_HARNESS_DESELECTED = pytest.StashKey[list]()


def pytest_collection_modifyitems(config, items):
    if _xdist_db.worker_id() is None:
        return
    drop = [item for item in items if item.get_closest_marker("bash_harness")]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = [item for item in items if not item.get_closest_marker("bash_harness")]
    config.stash[_HARNESS_DESELECTED] = [item.nodeid for item in drop]


def pytest_sessionfinish(session):
    # A worker tells the controller what it left out (xdist's workeroutput);
    # every worker leaves out the same tests.
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput["domovoi_harness_deselected"] = session.config.stash.get(_HARNESS_DESELECTED, [])


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    got = getattr(node, "workeroutput", {}).get("domovoi_harness_deselected")
    if got:
        node.config.stash[_HARNESS_DESELECTED] = got


def pytest_configure(config):
    if _xdist_db.worker_id() is not None:
        # The controller chose loadgroup below; a worker reads --dist from the
        # command line, so it is told (it tags each grouped test's id with its
        # group, which is what the controller schedules by).
        if getattr(config, "workerinput", {}).get("domovoi_loadgroup"):
            config.option.loadgroup = True
        return
    # A broken known-failures list stops the run before it starts, rather
    # than after the last test.
    from domovoi.tests import known_failures

    try:
        known_failures.load()
    except (ValueError, OSError) as exc:  # tomllib.TOMLDecodeError is a ValueError
        raise pytest.UsageError(f"domovoi/tests/known_failures.toml: {exc}") from None
    # `-n` alone gives xdist's `load`, which ignores xdist_group; `loadgroup`
    # is `load` that keeps each group on one worker. An explicit --dist wins.
    explicit = any(a == "--dist" or a.startswith("--dist=") for a in config.invocation_params.args)
    if getattr(config.option, "numprocesses", None) and getattr(config.option, "dist", None) == "load" and not explicit:
        config.option.dist = "loadgroup"


def pytest_sessionstart(session):
    # Before any worker starts: will N workers fit in the server's
    # connections? (Postgres is shared with every other run on the box.)
    workers = getattr(session.config.option, "numprocesses", None)
    if _xdist_db.worker_id() is not None or not workers or workers < 2 or not _DB_OK:
        return
    warning = _xdist_db.connection_warning(os.environ["DATABASE_URL"], workers)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if warning and reporter is not None:
        reporter.write_line(warning, yellow=True, bold=True)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    if node.config.getvalue("dist") == "loadgroup":
        node.workerinput["domovoi_loadgroup"] = True


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if _xdist_db.worker_id() is not None:
        return
    from domovoi.tests import known_failures

    known_failures.summarize(terminalreporter)
    left_out = config.stash.get(_HARNESS_DESELECTED, [])
    if left_out:
        terminalreporter.write_line(
            f"pytest -n left out {len(left_out)} bash_harness test(s) "
            f"({', '.join(n.rsplit('::', 1)[-1] for n in left_out)}): run the harnesses "
            f"with scripts/dev/run-linux-harness.sh all",
            yellow=True,
        )
