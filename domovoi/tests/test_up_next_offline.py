"""Building or extending a queue never calls the network (owner rule
2026-11-14).

ListenBrainz (similar artists, popularity) is fetched only by the
background worker (domovoi/workers/listenbrainz_fetch.py) and stored; the
Up next picker reads what is stored. A cache miss means the signal is
absent, never a request. Two checks:

* **Imports.** Nothing in ``domovoi/up_next/`` or the room extender
  worker imports the ListenBrainz client, the MusicBrainz client or an
  HTTP library.
* **Runtime.** With every way out of the process made to fail (and
  count), a refill over the real database builds a full batch, with and
  without stored ListenBrainz data, and nothing tried to connect.
"""

from __future__ import annotations

import ast
import socket
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from domovoi.tests.up_next_fixtures import NOW, synthetic_library

ROOT = Path(__file__).resolve().parents[1]
QUEUE_PATH = sorted((ROOT / "up_next").glob("*.py")) + [ROOT / "workers" / "up_next_extender.py"]
FORBIDDEN = (
    "domovoi.clients.listenbrainz", "domovoi.clients.musicbrainz", "domovoi.workers.listenbrainz_fetch",
    "requests", "httpx", "aiohttp", "urllib.request", "http.client",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:  # relative: inside domovoi.up_next
                mod = f"domovoi.up_next.{mod}" if mod else "domovoi.up_next"
            out.add(mod)
            out.update(f"{mod}.{a.name}" for a in node.names)
    return out


@pytest.mark.parametrize("path", QUEUE_PATH, ids=lambda p: p.name)
def test_the_queue_path_imports_no_network_client(path):
    bad = sorted(i for i in _imports(path) for f in FORBIDDEN if i == f or i.startswith(f + "."))
    assert not bad, f"{path.name} imports {bad}: the queue path must read stored data only"


@pytest.fixture
def no_way_out(monkeypatch):
    """Every outbound path fails, and counts the attempt."""
    tries: list[str] = []

    def refuse(name):
        def _f(*a, **k):
            tries.append(name)
            raise OSError(f"{name}: the network is off for this test")
        return _f

    # The test database is on this machine: loopback stays open, nothing else.
    local = {"127.0.0.1", "::1", "localhost"}
    real_connect, real_connect_ex, real_gai = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def connect(self, address):
        if isinstance(address, tuple) and address[0] in local:
            return real_connect(self, address)
        return refuse("socket.connect")()

    def connect_ex(self, address):
        if isinstance(address, tuple) and address[0] in local:
            return real_connect_ex(self, address)
        return refuse("socket.connect_ex")()

    def getaddrinfo(host, *a, **k):
        if host in local or host is None:
            return real_gai(host, *a, **k)
        return refuse("getaddrinfo")()

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    import requests

    monkeypatch.setattr(requests.Session, "request", refuse("requests"))
    from domovoi.clients import listenbrainz as lbc

    monkeypatch.setattr(lbc, "get_listenbrainz_client", refuse("listenbrainz client"))
    return tries


async def _library(db, *, with_listenbrainz: bool):
    from domovoi.tests.test_up_next_data import insert_library

    lib = synthetic_library(n=300)
    await insert_library(db, lib)
    # Give every artist an id and every song a recording id.
    await db.execute(text("""
        UPDATE library_tracks SET
          musicbrainz_artist_id = lpad(to_hex(hashtext(artist_norm) & 2147483647), 8, '0')
                                  || '-0000-0000-0000-000000000000',
          musicbrainz_recording_id = lpad(to_hex(id), 8, '0') || '-1111-1111-1111-111111111111'"""))
    if with_listenbrainz:
        await db.execute(text("""
            INSERT INTO artist_similarity (artist_mbid, similar_mbid, score)
            SELECT DISTINCT a.musicbrainz_artist_id, b.musicbrainz_artist_id, 1
            FROM library_tracks a JOIN library_tracks b ON b.artist_norm <> a.artist_norm
            WHERE a.id % 7 = 0 AND b.id % 11 = 0
            ON CONFLICT DO NOTHING"""))
        await db.execute(text("""
            INSERT INTO recording_popularity (recording_mbid, listen_count, user_count)
            SELECT musicbrainz_recording_id, (id * 37) % 1000, 1 FROM library_tracks"""))


@requires_db
@pytest.mark.parametrize("with_listenbrainz", [False, True], ids=["nothing stored", "stored"])
async def test_a_refill_builds_with_zero_outbound_calls(db_session, no_way_out, with_listenbrainz):
    from domovoi.up_next import data as D
    from domovoi.up_next.engine import next_batch
    from domovoi.up_next.model import SIMILAR_ARTIST
    from domovoi.up_next.session import SeedRef, UpNextSession

    await _library(db_session, with_listenbrainz=with_listenbrainz)
    library, listens = D.SqlLibrary(db_session), D.MusicListens(db_session)
    for mode, seed in (("library", SeedRef(7)), ("list", SeedRef(7, kind="artist", ref="artist 06"))):
        s = UpNextSession.start(target="room:kitchen", mode=mode, seed=seed, rng_seed=99)
        b = await next_batch(s, library=library, listens=listens, now=NOW)
        assert b.picks, mode
        b2 = await next_batch(s, library=library, listens=listens, now=NOW + timedelta(minutes=5))
        assert b2.picks or b2.ended, mode
    s = UpNextSession.start(target="room:kitchen", mode="library", seed=SeedRef(7), rng_seed=3)
    b = await next_batch(s, library=library, listens=listens, now=NOW)
    if with_listenbrainz:
        assert any(p.reason_code == SIMILAR_ARTIST for p in b.picks)
    else:
        assert not any(p.reason_code == SIMILAR_ARTIST for p in b.picks)
    assert no_way_out == []
