from __future__ import annotations

import asyncio
import os
import socket

# Force stub clients for the entire test run — BEFORE any domovoi import.
# Real clients would try to load Whisper models, connect to Ollama, etc., which
# the test suite shouldn't require.
os.environ.setdefault("USE_STUBS", "true")

def _resolve_test_database_url() -> str:
    """Pick the database pytest will run against.

    Priority:
      1. ``TEST_DATABASE_URL`` env var if set (explicit override).
      2. Auto-derive from ``DATABASE_URL`` by replacing the dbname with
         ``<dbname>_test``. So if prod is at
         ``...:6432/domovoi``, tests hit ``...:6432/domovoi_test``.
      3. Fall back to a hard-coded local default.

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


# Override DATABASE_URL with the test variant BEFORE importing settings.
# Pydantic-settings reads env vars first, then .env, then defaults — so
# pinning os.environ here wins over whatever .env says. This is the
# single line that protects the core's prod DB from pytest.
os.environ["DATABASE_URL"] = _resolve_test_database_url()

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

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi.config import settings
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


@pytest.fixture(autouse=True)
def _isolate_admin_config_dir(tmp_path, monkeypatch):
    """Point the admin-auth config dir (setup-code.txt home) at a tmp
    dir for EVERY test. The core lifespan's first-run hook writes the
    setup code whenever no admin credential exists — without this,
    any test that enters the app lifespan would drop files into the
    developer's real ~/.domovoi."""
    from domovoi import admin_auth

    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path / "domovoi-config")


@pytest_asyncio.fixture
async def db_session():
    """Fresh session per test, with all tables truncated first."""
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE"))
    async with SessionLocal() as s:
        yield s
        await s.rollback()
