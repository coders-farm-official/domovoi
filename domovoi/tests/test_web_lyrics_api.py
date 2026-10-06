"""The web's lyrics reads — ``web/backend/api/lyrics.py`` (lyrics contract
§11) — and the ``lyrics`` realtime digest.

Three household-tier routes:

* ``GET /api/music/library/{track_id}/lyrics`` — a song's ``LyricsDoc``:
  ``synced`` / ``plain`` / ``instrumental`` / ``none``, ``checking`` while
  Domovoi is still looking, where the words came from;
* ``GET /api/music/now-playing/{room_id}/lyrics`` — a room's song, where it
  has got to (``line_index``) and its doc in one read;
* ``GET /api/music/lyrics/status`` — the Jobs card: database counts merged
  with the core snapshot's ``lyrics`` / ``lyrics_index`` keys.

Two halves:

* DB-FREE (never skips): the real web app with the auth primitives faked
  (``auth_testkit.install_fake_db``) and the lyrics module's database
  replaced by a scripted one, so every tier, every payload shape, every
  refusal and the ``Cache-Control: no-store`` on all of them are proved
  without Postgres. A refused request is also shown to have read nothing.
* DB-BACKED (``requires_db``): invented songs and lyrics written straight
  into V021's tables (``lyrics_testkit.apply_v021``), read back through the
  real routes with the real household token — the view's "what is shown"
  rule, the status counts, the realtime digest.

Every lyric line here is INVENTED (lyrics contract [C2]); no real song's
words appear anywhere in this file.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    db_device_token,
    install_fake_db,
    web_app,
    web_client,
)
from domovoi.tests.conftest import requires_db
from web.backend import realtime
from web.backend.api import lyrics as lyrics_api
from web.backend.api import music as music_api
from web.backend.domovoi_client import get_cached_snapshot, set_cached_snapshot
from web.backend.schemas import NowPlaying, NowPlayingSong

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMIN_TOKEN = "admin-session-token"
DEVICE_TOKEN = "lantern-river-kettle-harbor"

# Invented lines (lyrics contract §0.2 [C2]).
L1 = "the lantern hums beside the river door"
L2 = "and every copper kettle sings at dawn"
L3 = "oh the paper boats are sailing down the hall"
L4 = "we carried paper boats along the hall"
L5 = "the river door is open tonight"
INVENTED = (L1, L2, L3, L4, L5)

SYNCED = [[12400, L1], [16850, L2], [21100, ""], [21300, L3], [65000, L3]]
SYNCED_PLAIN = "\n".join([L1, L2, L3, L3])

PATHS = {
    "track": "/api/music/library/7/lyrics",
    "room": "/api/music/now-playing/den/lyrics",
    "status": "/api/music/lyrics/status",
}


# ─── A scripted database for the DB-free half ─────────────────────────────


class _Result:
    def __init__(self, *, scalar: Any = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self) -> Any:
        return self._scalar

    def mappings(self) -> "_Result":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        assert len(self._rows) == 1, self._rows
        return self._rows[0]


def _doc_row(track_id: int, **over: Any) -> dict[str, Any]:
    """One row of lyrics_api._DOC_SQL: a library track WITH a lyrics row
    that the local scan has read, showing nothing, unless overridden."""
    row = {
        "track_id": track_id, "has_row": True, "source": None, "sidecar_name": None,
        "synced": None, "plain": None, "has_synced": False, "instrumental": False,
        "local_checked_at": datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        "lrclib_status": None, "updated_at": datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        "file_missing": False,
    }
    row.update(over)
    return row


def _status_row(**over: Any) -> dict[str, Any]:
    keys = ("tracks scanned with_lyrics synced plain instrumental src_sidecar src_embedded "
            "src_lrclib asked found not_found lrclib_instrumental skipped errors lrc_written "
            "lrc_exists lrc_edited lrc_deleted lrc_failed index_pending indexed").split()
    row: dict[str, Any] = {k: 0 for k in keys}
    row.update({"next_retry_at": None, "lrc_last_error": None})
    row.update(over)
    return row


class FakeDB:
    """Answers the three statement shapes lyrics.py sends, and records
    every statement (a refused request must leave this empty)."""

    def __init__(self, *, v021: bool = True, docs: dict[int, dict] | None = None,
                 status: dict[str, Any] | None = None, digest: tuple | None = None) -> None:
        self.v021 = v021
        self.docs = docs or {}
        self.status = status or _status_row()
        self.digest = digest
        self.statements: list[tuple[str, Any]] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        self.statements.append((sql, params))
        if "to_regclass" in sql:
            return _Result(scalar=self.v021)
        if "FROM library_tracks t" in sql:
            row = self.docs.get(int(params["id"]))
            return _Result(rows=[row] if row is not None else [])
        if "AS index_pending" in sql:
            return _Result(rows=[self.status])
        if "max(updated_at)" in sql:
            return _Result(rows=[self.digest])
        raise AssertionError(f"unexpected statement: {sql[:120]}")

    def scope(self):
        db = self

        @asynccontextmanager
        async def scope():
            yield db

        return scope


@pytest.fixture
def fake_db(monkeypatch):
    """Install a FakeDB as both the lyrics routes' and the digest's
    database; V021 is NOT taken as known-present (each request asks)."""
    db = FakeDB()
    monkeypatch.setattr(lyrics_api, "session_scope", db.scope())
    monkeypatch.setattr(realtime, "session_scope", db.scope())
    monkeypatch.setattr(lyrics_api, "_V021_READY", False)
    return db


@pytest.fixture
def snapshot():
    """The core snapshot the web last cached, restored afterwards."""
    before = get_cached_snapshot()
    yield set_cached_snapshot
    set_cached_snapshot(before)


@pytest.fixture
def den(monkeypatch):
    """Room 'den', provisioned, playing track 7 at 22.0 s unless a test
    swaps the card (``den.card = ...``)."""

    class Den:
        card: NowPlaying | None = NowPlaying(
            room_id="den", state="play", elapsed_sec=22.0, track_id=7,
            song=NowPlayingSong(file="Example Band/Lantern Song.mp3", title="Lantern Song",
                                artist="The Example Band", duration_sec=205),
        )
        asked: list[str] = []

    async def now_playing_for_room(room_id: str):
        Den.asked.append(room_id)
        return Den.card if room_id == "den" else None

    monkeypatch.setattr(music_api, "now_playing_for_room", now_playing_for_room)
    return Den


@pytest.fixture
def claimed(monkeypatch):
    """An install with an admin password (no pre-setup grace), one admin
    session and one household token."""
    return install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )


def _client(headers: dict[str, str] | None = None, **kw) -> AsyncClient:
    """The web app with no lifespan (no poll loop, no LISTEN task)."""
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers=headers or {}, **kw)


def _with_query(path: str, token: str) -> str:
    return f"{path}{'&' if '?' in path else '?'}device_token={token}"


def _no_lyric_text(body: str) -> None:
    for line in INVENTED:
        assert line not in body


# ─── Tiers: refused, and nothing read ────────────────────────────────────


@pytest.mark.parametrize("name", sorted(PATHS))
@pytest.mark.asyncio
async def test_no_credential_is_refused_and_nothing_is_read(name, claimed, fake_db, den) -> None:
    fake_db.docs[7] = _doc_row(7, source="sidecar", sidecar_name="Lantern Song.lrc",
                               synced=SYNCED, plain=SYNCED_PLAIN, has_synced=True)
    async with _client() as c:
        r = await c.get(PATHS[name])
    assert r.status_code == 401, r.text
    assert HEADER in r.json()["detail"]
    assert r.headers["cache-control"] == "no-store"
    _no_lyric_text(r.text)
    assert fake_db.statements == []
    assert den.asked == []


@pytest.mark.parametrize("name", sorted(PATHS))
@pytest.mark.asyncio
async def test_a_stale_household_token_is_refused(name, claimed, fake_db, den) -> None:
    async with _client({HEADER: "a-token-this-house-never-minted"}) as c:
        r = await c.get(PATHS[name])
        assert r.status_code == 401
        assert r.headers["cache-control"] == "no-store"
    async with _client() as c:
        r = await c.get(_with_query(PATHS[name], "a-token-this-house-never-minted"))
        assert r.status_code == 401
    assert fake_db.statements == []


# ─── Tiers: every household credential reads them ────────────────────────


def _credentials():
    return {
        "household token": dict(headers={HEADER: DEVICE_TOKEN}),
        "admin bearer": dict(headers=bearer(ADMIN_TOKEN)),
        "dashboard cookie": dict(cookies={COOKIE: ADMIN_TOKEN}),
        "query token": dict(query=DEVICE_TOKEN),
    }


@pytest.mark.parametrize("cred", sorted(_credentials()))
@pytest.mark.parametrize("name", sorted(PATHS))
@pytest.mark.asyncio
async def test_every_household_credential_reads_them(name, cred, claimed, fake_db, den) -> None:
    fake_db.docs[7] = _doc_row(7, source="sidecar", sidecar_name="Lantern Song.lrc",
                               synced=SYNCED, plain=SYNCED_PLAIN, has_synced=True)
    spec = _credentials()[cred]
    path = PATHS[name]
    if "query" in spec:
        path = _with_query(path, spec["query"])
    async with _client(spec.get("headers"), cookies=spec.get("cookies")) as c:
        r = await c.get(path)
    assert r.status_code == 200, (cred, r.text)
    assert r.headers["cache-control"] == "no-store"
    if name == "track":
        assert r.json()["lines"][0] == {"t": 12400, "text": L1}


@pytest.mark.parametrize("name", sorted(PATHS))
@pytest.mark.asyncio
async def test_a_fresh_install_keeps_its_grace(name, monkeypatch, fake_db, den) -> None:
    install_fake_db(monkeypatch, admin=False)
    fake_db.docs[7] = _doc_row(7, source="embedded", plain=L4)
    async with _client() as c:
        r = await c.get(PATHS[name])
    assert r.status_code == 200, r.text


# ─── Refusals that are not the tier's ────────────────────────────────────


@pytest.mark.parametrize("name", sorted(PATHS))
@pytest.mark.asyncio
async def test_a_database_without_v021_says_so(name, claimed, fake_db, den) -> None:
    fake_db.v021 = False
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        r = await c.get(PATHS[name])
    assert r.status_code == 503
    assert r.json() == {"detail": "lyrics are not set up on this server yet"}
    assert r.headers["cache-control"] == "no-store"
    assert den.asked == []


@pytest.mark.asyncio
async def test_v021_is_remembered_once_seen(claimed, fake_db, den) -> None:
    fake_db.docs[7] = _doc_row(7)
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        assert (await c.get(PATHS["track"])).status_code == 200
        assert (await c.get(PATHS["track"])).status_code == 200
    asked = [sql for sql, _ in fake_db.statements if "to_regclass" in sql]
    assert len(asked) == 1


@pytest.mark.asyncio
async def test_an_unknown_track_and_an_unprovisioned_room_are_404(claimed, fake_db, den) -> None:
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        r = await c.get("/api/music/library/999/lyrics")
        assert r.status_code == 404
        assert r.json() == {"detail": "track 999 not found"}
        assert r.headers["cache-control"] == "no-store"
        r = await c.get("/api/music/now-playing/attic/lyrics")
        assert r.status_code == 404
        assert r.json() == {"detail": "room 'attic' not provisioned"}
        assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/music/library/abc/lyrics", "/api/music/library/0/lyrics",
                                  "/api/music/library/99999999999/lyrics"])
@pytest.mark.asyncio
async def test_a_malformed_track_id_is_422_and_no_store(path, claimed, fake_db) -> None:
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        r = await c.get(path)
    assert r.status_code == 422
    assert r.headers["cache-control"] == "no-store"
    assert fake_db.statements == []


@pytest.mark.asyncio
async def test_a_throttled_source_keeps_its_retry_after_and_no_store(claimed, fake_db) -> None:
    from domovoi import admin_auth

    try:
        async with _client({HEADER: "wrong-token-again-and-again"}) as c:
            r = None
            for _ in range(12):
                r = await c.get(PATHS["track"])
                if r.status_code == 429:
                    break
        assert r is not None and r.status_code == 429, r.text
        assert r.headers.get("retry-after")
        assert r.headers["cache-control"] == "no-store"
    finally:
        admin_auth.DEVICE_TOKEN_BACKOFF.reset()


# ─── The doc: every shape ────────────────────────────────────────────────


async def _doc(fake_db: FakeDB, row: dict[str, Any], snap: dict | None, snapshot) -> dict:
    fake_db.docs[int(row["track_id"])] = row
    snapshot(snap)
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        r = await c.get(f"/api/music/library/{row['track_id']}/lyrics")
    assert r.status_code == 200, r.text
    return r.json()


LRCLIB_ON = {"lyrics": {"fetch": {"enabled": True, "write_lrc": True, "state": "running"}}}


@pytest.mark.asyncio
async def test_synced_lyrics_from_the_owners_lrc(claimed, fake_db, snapshot) -> None:
    doc = await _doc(fake_db, _doc_row(
        7, source="sidecar", sidecar_name="Lantern Song.lrc", synced=SYNCED,
        plain=SYNCED_PLAIN, has_synced=True), None, snapshot)
    assert doc == {
        "track_id": 7, "status": "synced", "checking": False, "source": "sidecar",
        "source_label": "from Lantern Song.lrc",
        "lines": [{"t": t, "text": s} for t, s in SYNCED],
        "text": SYNCED_PLAIN, "updated_at": "2026-10-05T12:00:00Z",
    }


@pytest.mark.asyncio
async def test_timed_lines_keep_their_order_and_skip_malformed_pairs(claimed, fake_db, snapshot) -> None:
    messy = [[100, L1], "not a pair", [200], [True, L2], [300, 7], [-5, L2], [400.0, L4],
             [None, L5], [500, L5, "extra"], [600, ""]]
    doc = await _doc(fake_db, _doc_row(8, source="embedded", synced=messy, plain=L1,
                                       has_synced=True), None, snapshot)
    assert doc["lines"] == [{"t": 100, "text": L1}, {"t": 400, "text": L4}, {"t": 600, "text": ""}]
    assert doc["source_label"] == "from the song file"


@pytest.mark.asyncio
async def test_plain_lyrics(claimed, fake_db, snapshot) -> None:
    plain = f"{L4}\n\n{L5}"
    doc = await _doc(fake_db, _doc_row(9, source="embedded", plain=plain), None, snapshot)
    assert doc["status"] == "plain"
    assert doc["lines"] is None
    assert doc["text"] == plain
    assert doc["checking"] is False


@pytest.mark.asyncio
async def test_lrclib_timed_lyrics_are_labelled_as_lrclibs(claimed, fake_db, snapshot) -> None:
    doc = await _doc(fake_db, _doc_row(10, source="lrclib", synced=[[0, L2]], plain=L2,
                                       has_synced=True, lrclib_status="found"), LRCLIB_ON, snapshot)
    assert (doc["status"], doc["source"], doc["source_label"]) == ("synced", "lrclib", "from LRCLIB")


@pytest.mark.asyncio
async def test_an_lrc_with_no_name_on_record_still_says_where(claimed, fake_db, snapshot) -> None:
    doc = await _doc(fake_db, _doc_row(11, source="sidecar", sidecar_name=None, plain=L5),
                     None, snapshot)
    assert doc["source_label"] == "from a .lrc file"


@pytest.mark.asyncio
async def test_instrumental(claimed, fake_db, snapshot) -> None:
    doc = await _doc(fake_db, _doc_row(12, instrumental=True, lrclib_status="instrumental"),
                     LRCLIB_ON, snapshot)
    assert doc["status"] == "instrumental"
    assert (doc["lines"], doc["text"], doc["source"], doc["source_label"]) == (None, None, None, None)
    assert doc["checking"] is False


@pytest.mark.asyncio
async def test_none_and_not_looking(claimed, fake_db, snapshot) -> None:
    """Read by the local scan, nothing found, LRCLIB off: nothing to wait for."""
    doc = await _doc(fake_db, _doc_row(13), {"lyrics": {"fetch": {"enabled": False, "state": "off"}}},
                     snapshot)
    assert (doc["status"], doc["checking"]) == ("none", False)


@pytest.mark.parametrize(("label", "row", "snap", "checking"), [
    ("no lyrics row yet", {"has_row": False, "updated_at": None, "local_checked_at": None}, None, True),
    ("the scan has not read it", {"local_checked_at": None}, None, True),
    ("LRCLIB on, not asked yet", {}, LRCLIB_ON, True),
    ("LRCLIB on, asked: not found", {"lrclib_status": "not_found"}, LRCLIB_ON, False),
    ("LRCLIB on but the file is missing", {"file_missing": True}, LRCLIB_ON, False),
    ("LRCLIB switched on but the internet answer is never",
     {}, {"lyrics": {"fetch": {"enabled": True, "state": "internet_off"}}}, False),
    ("LRCLIB enabled but off", {}, {"lyrics": {"fetch": {"enabled": True, "state": "off"}}}, False),
    ("the core has not reported", {}, None, False),
    ("a snapshot of the wrong shape", {}, {"lyrics": ["nonsense"]}, False),
])
@pytest.mark.asyncio
async def test_checking(label, row, snap, checking, claimed, fake_db, snapshot) -> None:
    doc = await _doc(fake_db, _doc_row(14, **row), snap, snapshot)
    assert doc["status"] == "none"
    assert doc["checking"] is checking, label


@pytest.mark.asyncio
async def test_a_song_with_no_lyrics_row_has_no_source_and_no_time(claimed, fake_db, snapshot) -> None:
    doc = await _doc(fake_db, _doc_row(15, has_row=False, updated_at=None, local_checked_at=None,
                                       file_missing=None), None, snapshot)
    assert doc == {"track_id": 15, "status": "none", "checking": True, "source": None,
                   "source_label": None, "lines": None, "text": None, "updated_at": None}


# ─── The room ────────────────────────────────────────────────────────────


async def _room(path: str = "/api/music/now-playing/den/lyrics") -> dict:
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        r = await c.get(path)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


@pytest.mark.asyncio
async def test_the_room_route_gives_the_song_its_place_and_its_lyrics(claimed, fake_db, den, snapshot) -> None:
    snapshot(None)
    fake_db.docs[7] = _doc_row(7, source="sidecar", sidecar_name="Lantern Song.lrc", synced=SYNCED,
                               plain=SYNCED_PLAIN, has_synced=True)
    before = datetime.now(timezone.utc)
    body = await _room()
    assert body["room_id"] == "den" and body["state"] == "play"
    assert body["track_id"] == 7
    assert body["elapsed_sec"] == 22.0 and body["duration_sec"] == 205
    # 22.0 s: past the gap at 21.1 s and the line at 21.3 s.
    assert body["line_index"] == 3
    assert body["lyrics"]["status"] == "synced"
    assert body["lyrics"]["lines"][3] == {"t": 21300, "text": L3}
    read_at = datetime.fromisoformat(body["read_at"])
    assert before - timedelta(seconds=1) <= read_at <= datetime.now(timezone.utc) + timedelta(seconds=1)


@pytest.mark.parametrize(("elapsed", "index"), [
    (0.0, -1), (12.399, -1), (12.4, 0), (16.0, 0), (16.85, 1), (21.1, 2), (64.999, 3), (65.0, 4),
    (500.0, 4), (None, -1),
])
@pytest.mark.asyncio
async def test_line_index_is_the_last_line_at_the_elapsed_time(
    elapsed, index, claimed, fake_db, den, snapshot,
) -> None:
    fake_db.docs[7] = _doc_row(7, source="sidecar", synced=SYNCED, plain=SYNCED_PLAIN, has_synced=True)
    den.card = den.card.model_copy(update={"elapsed_sec": elapsed, "state": "pause"})
    assert (await _room())["line_index"] == index


@pytest.mark.asyncio
async def test_plain_lyrics_in_a_room_have_no_line_index(claimed, fake_db, den, snapshot) -> None:
    fake_db.docs[7] = _doc_row(7, source="embedded", plain=L4)
    body = await _room()
    assert body["line_index"] is None
    assert body["lyrics"]["status"] == "plain"


@pytest.mark.asyncio
async def test_a_stopped_room_or_a_stream_has_no_track_and_no_lyrics(claimed, fake_db, den, snapshot) -> None:
    fake_db.docs[7] = _doc_row(7, source="embedded", plain=L4)
    den.card = den.card.model_copy(update={"state": "stop"})
    body = await _room()
    assert (body["state"], body["track_id"], body["lyrics"], body["line_index"]) == ("stop", None, None, None)
    # A stream: playing, but no library track behind it.
    den.card = NowPlaying(room_id="den", state="play", elapsed_sec=3.0, track_id=None,
                          song=NowPlayingSong(file="http://radio.example/stream", title="a stream"))
    body = await _room()
    assert (body["track_id"], body["lyrics"]) == (None, None)
    assert not any("FROM library_tracks t" in sql for sql, _ in fake_db.statements)


# ─── The Jobs card ───────────────────────────────────────────────────────


async def _status(fake_db: FakeDB, snapshot, snap: dict | None, **counts) -> dict:
    fake_db.status = _status_row(**counts)
    snapshot(snap)
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        r = await c.get("/api/music/lyrics/status")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


@pytest.mark.asyncio
async def test_status_before_the_core_reports(claimed, fake_db, snapshot) -> None:
    body = await _status(fake_db, snapshot, None, tracks=12, scanned=10, with_lyrics=6, synced=4,
                         plain=2, src_sidecar=1, src_embedded=3, src_lrclib=2)
    assert body == {
        "tracks": 12, "scanned": 10, "with_lyrics": 6, "synced": 4, "plain": 2, "instrumental": 0,
        "by_source": {"sidecar": 1, "embedded": 3, "lrclib": 2},
        "lrclib": {"enabled": None, "state": "unknown", "asked": 0, "found": 0, "not_found": 0,
                   "instrumental": 0, "skipped": 0, "errors": 0, "due": None, "next_retry_at": None,
                   "rate_limited_until": None, "paused_until": None, "last_error": None},
        "lrc_files": {"enabled": None, "written": 0, "exists": 0, "edited": 0, "deleted": 0,
                      "failed": 0, "last_error": None},
        "scan": {"state": "unknown", "unscanned": 2, "last_pass_at": None},
        "index": {"state": "unknown", "pending": 0, "indexed": 0},
        "search_enabled": None,
    }


@pytest.mark.asyncio
async def test_under_no_the_jobs_card_hears_internet_off(claimed, fake_db, snapshot, monkeypatch) -> None:
    """2026-10-06 review: INTERNET_ACCESS=never turns the LRCLIB setting off
    (greyed "needs internet"); the core's own status must then say
    internet_off — the card's "this Domovoi stays off the internet" — not
    "off", which sends the household to a switch it cannot turn on."""
    from domovoi import egress
    from domovoi.config import settings
    from domovoi.lyrics import status as lyrics_state

    monkeypatch.setattr(settings, "lyrics_lrclib_enabled", False)
    lyrics_state.reset_for_tests()
    with egress.override_policy("never"):
        core = {"lyrics": lyrics_state.lyrics_status()}
    body = await _status(fake_db, snapshot, core, tracks=12, scanned=12)
    assert (body["lrclib"]["enabled"], body["lrclib"]["state"]) == (False, "internet_off")


@pytest.mark.asyncio
async def test_status_merges_the_core_snapshot(claimed, fake_db, snapshot) -> None:
    retry = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    snap = {
        "lyrics": {
            "scan": {"state": "done", "last_tick_at": "2026-10-05T11:00:00+00:00",
                     "last_pass_at": "2026-10-05T11:00:00+00:00", "tracks": 12, "unscanned": 0},
            "fetch": {"enabled": True, "write_lrc": True, "state": "rate_limited",
                      "due": 812, "rate_limited_until": "2026-10-05T12:01:00+00:00",
                      "paused_until": "not a time", "last_error": "rate_limited" + "x" * 500},
        },
        "lyrics_index": {"state": "running", "pending": 3, "indexed": 5, "search_enabled": False},
    }
    body = await _status(fake_db, snapshot, snap, tracks=12, scanned=12, asked=9, found=6,
                         not_found=2, lrclib_instrumental=1, skipped=3, errors=1,
                         next_retry_at=retry, lrc_written=5, lrc_exists=2, lrc_edited=1,
                         lrc_deleted=1, lrc_failed=3, lrc_last_error="permission",
                         index_pending=4, indexed=7)
    lr = body["lrclib"]
    assert (lr["enabled"], lr["state"], lr["due"]) == (True, "rate_limited", 812)
    assert (lr["asked"], lr["found"], lr["not_found"], lr["instrumental"], lr["skipped"], lr["errors"]) \
        == (9, 6, 2, 1, 3, 1)
    assert lr["next_retry_at"] == "2026-11-02T12:00:00Z"
    assert lr["rate_limited_until"] == "2026-10-05T12:01:00+00:00"
    assert lr["paused_until"] is None                    # not a timestamp: dropped
    assert len(lr["last_error"]) == 120                  # a code, cut short regardless
    assert body["lrc_files"] == {"enabled": True, "written": 5, "exists": 2, "edited": 1,
                                 "deleted": 1, "failed": 3, "last_error": "permission"}
    assert body["scan"] == {"state": "done", "unscanned": 0, "last_pass_at": "2026-10-05T11:00:00+00:00"}
    # Pending / indexed are the DATABASE's (the index's own numbers are as
    # of its last tick); the state is the core's.
    assert body["index"] == {"state": "running", "pending": 4, "indexed": 7}
    assert body["search_enabled"] is False


@pytest.mark.parametrize(("enabled", "write_lrc", "expected"), [
    (True, True, True), (True, False, False), (False, True, False), (False, None, False),
    (True, None, None), (None, None, None),
])
def test_lrc_files_enabled_needs_both_switches(enabled, write_lrc, expected) -> None:
    fetch = {k: v for k, v in (("enabled", enabled), ("write_lrc", write_lrc)) if v is not None}
    out = lyrics_api.build_status(_status_row(), {"lyrics": {"fetch": fetch}})
    assert out["lrc_files"]["enabled"] is expected


def test_status_counts_never_go_negative() -> None:
    out = lyrics_api.build_status(_status_row(tracks=3, scanned=5), {"lyrics": {"fetch": {"due": -4}}})
    assert out["scan"]["unscanned"] == 0
    assert out["lrclib"]["due"] == 0


# ─── The pure helpers ────────────────────────────────────────────────────


def test_timed_lines_reads_a_json_string_too() -> None:
    assert lyrics_api.timed_lines(json.dumps([[5, L1]])) == [{"t": 5, "text": L1}]
    assert lyrics_api.timed_lines(b'[[5, "x"]]') == [{"t": 5, "text": "x"}]
    assert lyrics_api.timed_lines("not json") == []
    assert lyrics_api.timed_lines({"t": 5}) == []
    assert lyrics_api.timed_lines([[float("nan"), L1], [float("inf"), L1]]) == []


def test_the_source_label_is_a_file_name_only() -> None:
    assert lyrics_api.source_label("sidecar", "Lantern Song.lrc") == "from Lantern Song.lrc"
    assert lyrics_api.source_label("sidecar", "C:\\Music\\x\\Glass Harbor.LRC") == "from Glass Harbor.LRC"
    assert lyrics_api.source_label("sidecar", "/srv/music/a/b.lrc") == "from b.lrc"
    assert lyrics_api.source_label("sidecar", "  ") == "from a .lrc file"
    assert lyrics_api.source_label("embedded", None) == "from the song file"
    assert lyrics_api.source_label("lrclib", None) == "from LRCLIB"
    assert lyrics_api.source_label(None, None) is None
    assert lyrics_api.source_label("something new", None) is None


def test_line_at() -> None:
    lines = [{"t": t, "text": s} for t, s in SYNCED]
    assert lyrics_api.line_at([], 1000) == -1
    assert lyrics_api.line_at(None, 1000) == -1
    assert lyrics_api.line_at(lines, 12399.9) == -1
    assert lyrics_api.line_at(lines, 12400) == 0
    assert lyrics_api.line_at(lines, 10 ** 9) == 4


def test_lrclib_on() -> None:
    assert lyrics_api.lrclib_on(LRCLIB_ON) is True
    assert lyrics_api.lrclib_on(None) is False
    assert lyrics_api.lrclib_on({"lyrics": {"fetch": {"enabled": "yes", "state": "running"}}}) is False
    assert lyrics_api.lrclib_on({"lyrics": {"fetch": {"enabled": True}}}) is True
    for state in ("off", "internet_off"):
        assert lyrics_api.lrclib_on({"lyrics": {"fetch": {"enabled": True, "state": state}}}) is False
    for state in ("offline", "rate_limited", "paused", "idle", "done", "error"):
        assert lyrics_api.lrclib_on({"lyrics": {"fetch": {"enabled": True, "state": state}}}) is True


def test_the_norm_version_defaults_to_one_where_lyric_search_is_absent(monkeypatch) -> None:
    from domovoi.handlers.shared import spoken_names

    monkeypatch.delattr(spoken_names, "LYRIC_NORM_VERSION", raising=False)
    assert lyrics_api.lyric_norm_version() == 1
    monkeypatch.setattr(spoken_names, "LYRIC_NORM_VERSION", 3, raising=False)
    assert lyrics_api.lyric_norm_version() == 3


# ─── Nothing logs a lyric, no error carries one ──────────────────────────


@pytest.mark.asyncio
async def test_no_route_logs_a_lyric(claimed, fake_db, den, snapshot, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    fake_db.docs[7] = _doc_row(7, source="sidecar", synced=SYNCED, plain=SYNCED_PLAIN, has_synced=True)
    snapshot(LRCLIB_ON)
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        for path in PATHS.values():
            assert (await c.get(path)).status_code == 200
    for record in caplog.records:
        _no_lyric_text(record.getMessage())


# ─── The realtime digest ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_digest_is_counts_and_states_only(fake_db, snapshot) -> None:
    fake_db.digest = (12, 6, 4, 9, 5, 2, datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))
    snapshot({"lyrics": {"fetch": {"state": "running"}, "scan": {"state": "done"}},
              "lyrics_index": {"state": "idle"}})
    digest = await realtime._snapshot_lyrics()
    assert digest == {
        "count": 12, "with_lyrics": 6, "synced": 4, "asked": 9, "lrc_written": 5,
        "index_pending": 2, "max_updated": "2026-10-05T12:00:00+00:00",
        "fetch_state": "running", "scan_state": "done", "index_state": "idle",
    }
    assert all(isinstance(v, (int, str)) or v is None for v in digest.values())


@pytest.mark.asyncio
async def test_the_digest_is_empty_without_v021(fake_db, snapshot) -> None:
    fake_db.v021 = False
    snapshot(None)
    assert await realtime._snapshot_lyrics() == {}


@pytest.mark.asyncio
async def test_the_digest_before_the_core_reports(fake_db, snapshot) -> None:
    fake_db.digest = (0, 0, 0, 0, 0, 0, None)
    snapshot(None)
    digest = await realtime._snapshot_lyrics()
    assert (digest["fetch_state"], digest["scan_state"], digest["index_state"], digest["max_updated"]) \
        == (None, None, None, None)


def test_the_lyrics_channel_is_a_core_channel() -> None:
    assert realtime.StatePollLoop._CHANNEL_HELPERS["lyrics"] is realtime._snapshot_lyrics
    # Core, so a plugin's [[realtime]] entry can never claim (or pop) it.
    assert "lyrics" in realtime.CORE_REALTIME_CHANNELS


# ─── The committed API contract ([A14]) ──────────────────────────────────


def test_the_openapi_file_documents_the_lyrics_routes() -> None:
    spec = json.loads((REPO_ROOT / "web" / "openapi.json").read_text(encoding="utf-8"))
    for path in ("/api/music/library/{track_id}/lyrics", "/api/music/now-playing/{room_id}/lyrics",
                 "/api/music/lyrics/status"):
        assert "get" in spec["paths"][path], path
    schemas = spec["components"]["schemas"]
    for model in ("LyricsDoc", "LyricLine", "RoomLyrics", "LyricsStatus"):
        assert model in schemas, model
    assert set(schemas["LyricsDoc"]["properties"]) == {
        "track_id", "status", "checking", "source", "source_label", "lines", "text", "updated_at"}


def test_the_committed_openapi_matches_the_app_for_the_lyrics_routes() -> None:
    spec = json.loads((REPO_ROOT / "web" / "openapi.json").read_text(encoding="utf-8"))
    live = web_app.openapi()
    for path in ("/api/music/library/{track_id}/lyrics", "/api/music/now-playing/{room_id}/lyrics",
                 "/api/music/lyrics/status"):
        assert spec["paths"][path] == live["paths"][path], f"re-run python -m web.scripts.dump_openapi ({path})"


# ─── Against a real database ─────────────────────────────────────────────


async def _seed() -> dict[str, int]:
    """Invented songs, each in one lyrics state. Returns name -> track id."""
    from domovoi.db.session import engine
    from domovoi.tests.lyrics_testkit import apply_v021

    await apply_v021()
    songs = [
        ("lrc", "Lantern Song", "The Example Band"),
        ("tags", "Glass Harbor", "The Example Band"),
        ("lrclib", "Copper Morning", "The Velvet Kites"),
        ("instrumental", "Quiet Interlude", "The Velvet Kites"),
        ("unscanned", "Unread Tune", "The Example Band"),
        ("norow", "Brand New Tune", "The Example Band"),
        ("missing", "Moved Away", "The Velvet Kites"),
        ("silent", "Nothing Yet", "The Velvet Kites"),
        ("both", "Two Sources", "The Example Band"),
        ("fail1", "Locked Folder One", "The Example Band"),
        ("fail2", "Locked Folder Two", "The Example Band"),
        ("fail3", "Full Disk", "The Example Band"),
        ("notfound", "Rare Recording", "The Velvet Kites"),
    ]
    ids: dict[str, int] = {}
    synced = json.dumps(SYNCED)
    lrclib_synced = json.dumps([[1000, L4], [5000, L5]])
    async with engine.begin() as conn:
        for key, title, artist in songs:
            ids[key] = (await conn.execute(text(
                "INSERT INTO library_tracks (file_path, title, artist, duration_sec) "
                "VALUES (:p, :t, :a, 205) RETURNING id"),
                {"p": f"/music/{artist}/{title}.mp3", "t": title, "a": artist})).scalar_one()

        async def put(key: str, cols: dict[str, Any]) -> None:
            names = ", ".join(["track_id", *cols])
            values = ", ".join([":track_id", *(
                "CAST(:" + k + " AS jsonb)" if k.endswith("_synced") else ":" + k for k in cols)])
            await conn.execute(text(f"INSERT INTO track_lyrics ({names}) VALUES ({values})"),
                               {"track_id": ids[key], **cols})

        checked = datetime.now(timezone.utc) - timedelta(minutes=5)
        await put("lrc", {"local_checked_at": checked, "local_source": "sidecar",
                          "local_detail": "Lantern Song.lrc", "local_plain": SYNCED_PLAIN,
                          "local_synced": synced, "sidecar_name": "Lantern Song.lrc"})
        await put("tags", {"local_checked_at": checked, "local_source": "embedded",
                           "local_detail": "USLT", "local_plain": f"{L4}\n\n{L5}"})
        await put("lrclib", {"local_checked_at": checked, "lrclib_status": "found",
                             "lrclib_id": 9001, "lrclib_match": "get",
                             "lrclib_plain": f"{L4}\n{L5}", "lrclib_synced": lrclib_synced,
                             "lrc_state": "written", "lrc_name": "Copper Morning.lrc",
                             "lrc_sha256": "ab" * 32})
        await put("instrumental", {"local_checked_at": checked, "lrclib_status": "instrumental",
                                   "lrclib_id": 9002, "lrclib_instrumental": True})
        await put("unscanned", {})
        await put("missing", {"local_checked_at": checked, "file_missing": True})
        await put("silent", {"local_checked_at": checked})
        # The song's tags hold plain words, LRCLIB has them timed: timed wins.
        await put("both", {"local_checked_at": checked, "local_source": "embedded",
                           "local_detail": "LYRICS", "local_plain": L1,
                           "lrclib_status": "found", "lrclib_id": 9003, "lrclib_match": "search",
                           "lrclib_plain": L2, "lrclib_synced": json.dumps([[0, L2]])})
        for key, code in (("fail1", "permission"), ("fail2", "permission"), ("fail3", "io")):
            await put(key, {"local_checked_at": checked, "lrclib_status": "found", "lrclib_id": 9100,
                            "lrclib_match": "get", "lrclib_plain": L3,
                            "lrclib_synced": json.dumps([[0, L3]]), "lrc_state": "failed",
                            "lrc_error": code})
        await put("notfound", {"local_checked_at": checked, "lrclib_status": "not_found",
                               "lrclib_attempts": 1,
                               "lrclib_next_at": datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)})
        # The search index is current for the owner's .lrc and nothing else.
        await conn.execute(text(
            "UPDATE track_lyrics SET lines_md5 = plain_md5, lines_version = 1 WHERE track_id = :id"),
            {"id": ids["lrc"]})
    return ids


@requires_db
@pytest.mark.asyncio
async def test_the_real_routes_read_what_the_view_shows(_db, db_session, snapshot, monkeypatch) -> None:
    async with web_client() as setup:
        await claim_admin(setup)
    token = await db_device_token()
    assert token
    ids = await _seed()
    snapshot(LRCLIB_ON)
    monkeypatch.setattr(lyrics_api, "_V021_READY", False)

    async with _client({HEADER: token}) as c:
        async def doc(key: str) -> dict:
            r = await c.get(f"/api/music/library/{ids[key]}/lyrics")
            assert r.status_code == 200, (key, r.text)
            assert r.headers["cache-control"] == "no-store"
            return r.json()

        d = await doc("lrc")
        assert (d["status"], d["source"], d["source_label"]) == ("synced", "sidecar", "from Lantern Song.lrc")
        assert d["lines"] == [{"t": t, "text": s} for t, s in SYNCED]
        assert d["text"] == SYNCED_PLAIN and d["checking"] is False
        d = await doc("tags")
        assert (d["status"], d["text"], d["source_label"]) == ("plain", f"{L4}\n\n{L5}", "from the song file")
        d = await doc("lrclib")
        assert (d["status"], d["source"]) == ("synced", "lrclib")
        assert d["lines"] == [{"t": 1000, "text": L4}, {"t": 5000, "text": L5}]
        d = await doc("instrumental")
        assert (d["status"], d["checking"]) == ("instrumental", False)
        assert (await doc("unscanned"))["checking"] is True
        d = await doc("norow")
        assert (d["status"], d["checking"], d["updated_at"]) == ("none", True, None)
        assert (await doc("missing"))["checking"] is False
        assert (await doc("silent"))["checking"] is True
        d = await doc("both")
        assert (d["status"], d["source"], d["text"]) == ("synced", "lrclib", L2)
        assert (await c.get("/api/music/library/987654/lyrics")).status_code == 404

    async with _client() as anon:
        r = await anon.get(f"/api/music/library/{ids['lrc']}/lyrics")
        assert r.status_code == 401
        _no_lyric_text(r.text)


@requires_db
@pytest.mark.asyncio
async def test_the_real_status_counts(_db, db_session, snapshot) -> None:
    async with web_client() as setup:
        await claim_admin(setup)
    token = await db_device_token()
    ids = await _seed()
    snapshot(None)
    async with _client({HEADER: token}) as c:
        r = await c.get("/api/music/lyrics/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tracks"] == len(ids)
    assert body["scanned"] == len(ids) - 2          # 'unscanned' and 'norow'
    # lrc, tags, lrclib, both and the three failed writes have words.
    assert body["with_lyrics"] == 7
    assert body["synced"] == 6 and body["plain"] == 1
    assert body["instrumental"] == 1
    assert body["by_source"] == {"sidecar": 1, "embedded": 1, "lrclib": 5}
    lr = body["lrclib"]
    assert (lr["asked"], lr["found"], lr["not_found"], lr["instrumental"]) == (7, 5, 1, 1)
    assert lr["next_retry_at"] == "2026-11-02T12:00:00Z"
    assert body["lrc_files"]["written"] == 1 and body["lrc_files"]["failed"] == 3
    assert body["lrc_files"]["last_error"] == "permission"
    assert body["scan"]["unscanned"] == 2
    assert body["index"]["indexed"] == 1
    assert body["index"]["pending"] == 6            # words, lines not built yet
    _no_lyric_text(r.text)


@requires_db
@pytest.mark.asyncio
async def test_the_real_digest(db_session, snapshot) -> None:
    await _seed()
    snapshot(None)
    digest = await realtime._snapshot_lyrics()
    assert (digest["count"], digest["with_lyrics"], digest["synced"], digest["asked"],
            digest["lrc_written"], digest["index_pending"]) == (12, 7, 6, 7, 1, 6)
    assert digest["max_updated"]
    _no_lyric_text(json.dumps(digest))


@requires_db
@pytest.mark.asyncio
async def test_now_playing_for_room_reads_one_provisioned_room(db_session, monkeypatch) -> None:
    from domovoi.db.session import engine
    from domovoi.handlers.shared.library_match import library_path_for_mpd_file

    song = {"file": "The Example Band/Lantern Song.mp3", "Title": "Lantern Song",
            "Artist": "The Example Band", "duration": "205.3", "Id": "41"}

    async def fake_read(host, port, timeout=1.5):
        assert port == 16991
        return "play", dict(song), 12.5

    monkeypatch.setattr(music_api, "_read_mpd", fake_read)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM mpd_rooms WHERE room_id = 'lyr_test_den'"))
        await conn.execute(text(
            "INSERT INTO mpd_rooms (room_id, control_port, http_port, container_name) "
            "VALUES ('lyr_test_den', 16991, 18991, 'test-mpd-lyr-den')"))
        tid = (await conn.execute(text(
            "INSERT INTO library_tracks (file_path, title) VALUES (:p, 'Lantern Song') RETURNING id"),
            {"p": library_path_for_mpd_file(song["file"])})).scalar_one()
    try:
        card = await music_api.now_playing_for_room("lyr_test_den")
        assert card is not None
        assert (card.room_id, card.state, card.track_id, card.elapsed_sec) == ("lyr_test_den", "play", tid, 12.5)
        assert card.song.duration_sec == 205
        assert await music_api.now_playing_for_room("lyr_test_nowhere") is None
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM mpd_rooms WHERE room_id = 'lyr_test_den'"))
