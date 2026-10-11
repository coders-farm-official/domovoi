"""Guests on a playing room (owner decisions 2026-10-10).

"Guests should be able to edit queues that rooms are actively playing",
with the room's transport controls too, "until they get banned by someone
with admin privileges". A caller with NO household credential may, for a
room whose MPD is ``play`` or ``pause`` with a current song, read that
room's queue, add / remove / move entries, and pause / resume / skip /
go back — and nothing else: not clear, not stop, not the entry playing
now, nothing on an idle room, never a room's player started, nothing once
an admin banned it (by device id or address, every room).

Every rule is pinned at BOTH hops:

* **core** (``domovoi/guest_queue.py`` + the queue and transport routes
  in ``domovoi/main.py``): the authority. Driven DB-free through the real
  core app with the auth primitives faked (``install_fake_db``), a stub
  MPD per room, and the ban table in memory (``fake_bans``); the
  handlers' own Postgres touches (the library lookup, the intent log) are
  replaced with in-memory stand-ins.
* **web** (``web/backend/api/music_queue.py``, the previous route in
  ``api/music.py``): the switch, the blocklist, bans, the throttle,
  provenance, and that a guest reaches the core as a guest. DB-free with
  a fake core and a fake session; V023's tables and the provenance and
  block tables are walked for real in the ``requires_db`` half.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import web.backend.api.music_queue as queue_api
from domovoi import admin_auth, guest_bans, guest_queue
from domovoi import main as core_main
from domovoi.clients import mpd as mpd_mod
from domovoi.clients.mpd import MPDStubClient
from domovoi.config import settings
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    bearer,
    install_fake_db,
    web_app,
)
from domovoi.tests.conftest import requires_db

ADMIN_TOKEN = "guest-test-admin-token"
DEVICE_TOKEN = "guest-test-device-token"
BROWSER = {"X-Requested-With": "XMLHttpRequest"}

# The real table functions, kept before any test swaps them for FakeBans.
_REAL_FIND_BAN = guest_bans.find_ban
_REAL_RECORD_GUEST = guest_bans.record_guest

ACTIVE = "gq-living"     # playing
PAUSED = "gq-den"        # paused on a song
IDLE = "gq-garage"       # player up, stopped
UNKNOWN = "gq-attic"     # no player at all


# ─── Fixtures ──────────────────────────────────────────────────────────────


def _stub(*titles: str, state: str = "stop") -> MPDStubClient:
    stub = MPDStubClient()
    if titles:
        asyncio.run(stub.prepare_tracks([{"title": t, "file_path": f"{t}.mp3"} for t in titles]))
    if state == "play":
        asyncio.run(stub.resume())
    elif state == "stop":
        asyncio.run(stub.stop())
    return stub


@pytest.fixture(autouse=True)
def _fresh_throttle():
    admin_auth.GUEST_QUEUE_LIMITER.reset()
    admin_auth.GUEST_READ_LIMITER.reset()
    guest_queue.clear_activity_cache()
    yield
    admin_auth.GUEST_QUEUE_LIMITER.reset()
    admin_auth.GUEST_READ_LIMITER.reset()
    guest_queue.clear_activity_cache()


class FakeBans:
    """``guest_bans`` in memory: the bans an admin made, and every guest
    action the core recorded. Matching is the real rule — device id OR
    source address."""

    def __init__(self) -> None:
        self.bans: list[dict[str, Any]] = []
        self.seen: list[dict[str, Any]] = []
        self.fail = False

    def ban(self, device_id=None, source=None) -> dict[str, Any]:
        entry = {"id": len(self.bans) + 1, "device_id": device_id, "source": source,
                 "note": None}
        self.bans.append(entry)
        return entry

    async def find_ban(self, device_id, source):
        if self.fail:
            raise ConnectionRefusedError("database down")
        device_id = guest_bans.clean_device_id(device_id)
        for b in self.bans:
            if (b["device_id"] is not None and b["device_id"] == device_id) or (
                b["source"] is not None and b["source"] == source
            ):
                return b
        return None

    async def record_guest(self, **kw):
        self.seen.append(kw)


@pytest.fixture
def fake_bans(monkeypatch) -> FakeBans:
    bans = FakeBans()
    monkeypatch.setattr(guest_bans, "find_ban", bans.find_ban)
    monkeypatch.setattr(guest_bans, "record_guest", bans.record_guest)
    return bans


@pytest.fixture
def rooms(monkeypatch, fake_bans):
    """Three rooms with a running stub player — playing, paused, stopped —
    and one with no player at all. Returns the stubs by room."""
    stubs = {
        ACTIVE: _stub("one", "two", "three", state="play"),
        PAUSED: _stub("alpha", "beta", state="pause"),
        IDLE: _stub("old", state="stop"),
    }
    for room, stub in stubs.items():
        monkeypatch.setitem(mpd_mod._clients, room, stub)
    monkeypatch.delitem(mpd_mod._clients, UNKNOWN, raising=False)
    monkeypatch.setattr(settings, "guest_queue_editing", True)
    return stubs


@pytest.fixture
def no_player_starts(monkeypatch):
    """Record every path that could provision or start a room's player."""
    calls: list[tuple[str, Any]] = []

    async def _ensure(room_id):
        calls.append(("ensure_room_mpd", room_id))

    async def _dispatch(response, room_id, **kw):
        calls.append(("dispatch", room_id, getattr(response, "music_action", None)))

    async def _ensure_room(room_id, *a, **kw):  # pragma: no cover — must not run
        calls.append(("ensure_room", room_id))

    from domovoi import mpd_provisioner

    monkeypatch.setattr(core_main, "_ensure_room_mpd", _ensure)
    monkeypatch.setattr(core_main, "_admin_dispatch_music", _dispatch)
    monkeypatch.setattr(mpd_provisioner, "ensure_room", _ensure_room)
    return calls


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


@pytest.fixture
def core_db(monkeypatch):
    """The core queue handlers' own Postgres touches, in memory: the
    library lookup answers any id with a track, and the intent log and
    play history write nothing."""

    class _Session:
        async def execute(self, sql, params=None):
            if "FROM library_tracks" in str(sql):
                ids = list((params or {}).get("ids") or [])
                return _Rows([(i, f"track{i}.mp3", f"track {i}", "artist") for i in ids])
            return _Rows([])

    @asynccontextmanager
    async def _scope():
        yield _Session()

    async def _no_log(room_id, what):
        return None

    async def _no_history(*a, **kw):
        return None

    from domovoi.handlers.shared import play_history

    monkeypatch.setattr(core_main, "session_scope", _scope)
    monkeypatch.setattr(core_main, "_log_queue_edit", _no_log)
    monkeypatch.setattr(play_history, "record_media_play", _no_history)


@pytest.fixture
def claimed(monkeypatch):
    """A box past first-run setup: an admin exists, so a request carrying
    nothing is a guest (or, with the switch off, refused)."""
    return install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )


def _core(*, cross_site: bool = False, **headers) -> AsyncClient:
    """The core app, spoken to the way the web hop and the dashboard speak
    to it: with ``X-Requested-With`` (a guest's request needs a custom
    header). ``cross_site=True`` drops it — what a page on another site can
    make a LAN browser send."""
    base = {} if cross_site else {"X-Requested-With": "XMLHttpRequest"}
    return AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False),
        base_url="http://test",
        headers={**base, **headers},
    )


def _ids(stub: MPDStubClient) -> list[int]:
    """The stub's songids in queue order (read straight off its model so
    an async test can call it)."""
    return [int(e["id"]) for e in stub._queue]


# ─── Core: what "active" means, and that deciding it starts nothing ───────


async def test_playing_and_paused_with_a_song_are_active(rooms) -> None:
    playing = await guest_queue.room_activity(ACTIVE)
    assert playing.active is True
    assert playing.song_id == (await rooms[ACTIVE].current_song())["id"]
    assert (await guest_queue.room_activity(PAUSED)).active is True


async def test_stopped_or_absent_is_not_active(rooms) -> None:
    assert (await guest_queue.room_activity(IDLE)).active is False
    assert (await guest_queue.room_activity(UNKNOWN)).active is False
    # Peeking at a room with no player did not invent one.
    assert UNKNOWN not in mpd_mod._clients


async def test_a_state_without_a_current_song_is_not_active(monkeypatch) -> None:
    stub = MPDStubClient()
    stub._state = "play"   # a player saying play with nothing loaded
    monkeypatch.setitem(mpd_mod._clients, "gq-odd", stub)
    assert (await guest_queue.room_activity("gq-odd")).active is False


async def test_a_player_that_fails_or_hangs_is_not_active(monkeypatch) -> None:
    class _Broken(MPDStubClient):
        async def state(self):
            raise ConnectionRefusedError("container down")

    class _Hung(MPDStubClient):
        async def state(self):
            await asyncio.sleep(30)
            return "play"

    monkeypatch.setitem(mpd_mod._clients, "gq-broken", _Broken())
    monkeypatch.setitem(mpd_mod._clients, "gq-hung", _Hung())
    monkeypatch.setattr(guest_queue, "ACTIVITY_TIMEOUT_SEC", 0.05)
    assert (await guest_queue.room_activity("gq-broken")).active is False
    assert (await guest_queue.room_activity("gq-hung")).active is False


async def test_real_mode_never_touches_a_room_without_a_running_player(monkeypatch) -> None:
    """Outside stub mode an unknown room must not even get a client: the
    port-map lookup that would fall back to ANOTHER room, or a
    provisioning call, is exactly what the rule forbids."""
    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(mpd_mod, "_room_ports", {"gq-real": (6650, 8050)})
    asked: list[str] = []
    monkeypatch.setattr(
        mpd_mod, "get_mpd_client_for", lambda room: asked.append(room) or MPDStubClient()
    )
    assert (await guest_queue.room_activity("gq-nowhere")).active is False
    assert asked == []
    # A room that IS in the map is asked (and a fresh stub is stopped).
    assert (await guest_queue.room_activity("gq-real")).active is False
    assert asked == ["gq-real"]


# ─── Core: a guest on an ACTIVE room ──────────────────────────────────────


async def test_core_guest_reads_a_playing_rooms_queue(
    rooms, claimed, no_player_starts
) -> None:
    async with _core() as c:
        for room in (ACTIVE, PAUSED):
            r = await c.get(f"/v1/admin/music/queue/{room}")
            assert r.status_code == 200, r.text
            assert r.json()["items"], room
            assert r.json()["current_song_id"] is not None
    assert no_player_starts == []


async def test_core_guest_adds_removes_and_moves_on_a_playing_room(
    rooms, claimed, no_player_starts, core_db
) -> None:
    stub = rooms[ACTIVE]
    current = (await stub.current_song())["id"]
    async with _core() as c:
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/add", json={"track_ids": [7, 8]})
        assert r.status_code == 200, r.text
        assert r.json()["started"] is False
        assert len(_ids(stub)) == 5

        last = _ids(stub)[-1]
        r = await c.post(
            f"/v1/admin/music/queue/{ACTIVE}/move", json={"song_id": last, "to_position": 1}
        )
        assert r.status_code == 200, r.text
        assert _ids(stub)[1] == last

        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/remove", json={"song_ids": [last]})
        assert r.status_code == 200, r.text
        assert last not in _ids(stub)
    # Playback was never touched: same song, still playing, nothing sent.
    assert (await stub.current_song())["id"] == current
    assert await stub.state() == "play"
    assert no_player_starts == []


async def test_core_guest_may_move_but_not_remove_the_entry_playing_now(
    rooms, claimed, no_player_starts, core_db
) -> None:
    stub = rooms[ACTIVE]
    current = (await stub.current_song())["id"]
    before = _ids(stub)
    async with _core() as c:
        r = await c.post(
            f"/v1/admin/music/queue/{ACTIVE}/remove", json={"song_ids": [current]}
        )
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == admin_auth.GUEST_PLAYING_ENTRY_DETAIL
        assert _ids(stub) == before

        r = await c.post(
            f"/v1/admin/music/queue/{ACTIVE}/move", json={"song_id": current, "to_position": 2}
        )
        assert r.status_code == 200, r.text
    assert (await stub.current_song())["id"] == current


async def test_core_guest_add_never_starts_a_room_that_stopped_meanwhile(
    monkeypatch, claimed, no_player_starts, core_db, fake_bans
) -> None:
    """The gate saw the room playing; by the time the add ran it had
    stopped with an empty queue — the case where a household add starts
    the room. A guest's add queues the tracks and starts nothing."""

    class _StopsAfterTheGate(MPDStubClient):
        calls = 0

        async def state(self):
            type(self).calls += 1
            return "play" if type(self).calls == 1 else "stop"

        async def current_song(self):
            return {"id": 99, "title": "fading out"}

    stub = _StopsAfterTheGate()
    monkeypatch.setitem(mpd_mod._clients, "gq-fading", stub)
    monkeypatch.setattr(settings, "guest_queue_editing", True)
    async with _core() as c:
        r = await c.post("/v1/admin/music/queue/gq-fading/add", json={"track_ids": [1]})
    assert r.status_code == 200, r.text
    assert r.json()["started"] is False
    assert len(await stub.queue_list()) == 1
    assert stub._state == "stop"
    assert no_player_starts == []


# ─── Core: a guest on an IDLE room, and on clear ──────────────────────────


@pytest.mark.parametrize("room", [IDLE, UNKNOWN])
async def test_core_guest_is_refused_an_idle_room_and_nothing_starts(
    room, rooms, claimed, no_player_starts, core_db
) -> None:
    idle_before = _ids(rooms[IDLE])
    async with _core() as c:
        calls = [
            ("GET", f"/v1/admin/music/queue/{room}", None),
            ("POST", f"/v1/admin/music/queue/{room}/add", {"track_ids": [1]}),
            ("POST", f"/v1/admin/music/queue/{room}/remove", {"song_ids": [1]}),
            ("POST", f"/v1/admin/music/queue/{room}/move", {"song_id": 1, "to_position": 0}),
        ]
        for method, path, body in calls:
            r = await c.request(method, path, json=body)
            assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
            # The same sentence for idle and for no-such-room: a guest
            # learns nothing about which rooms exist.
            assert r.json()["detail"] == admin_auth.GUEST_ROOM_IDLE_DETAIL
    assert no_player_starts == []
    assert UNKNOWN not in mpd_mod._clients
    assert _ids(rooms[IDLE]) == idle_before
    assert await rooms[IDLE].state() == "stop"


async def test_core_guest_cannot_clear_even_a_playing_room(
    rooms, claimed, no_player_starts
) -> None:
    before = _ids(rooms[ACTIVE])
    async with _core() as c:
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/clear", json={})
    assert r.status_code == 401, r.text
    assert _ids(rooms[ACTIVE]) == before
    assert await rooms[ACTIVE].state() == "play"
    assert no_player_starts == []


# ─── Core: household callers keep exactly today's behaviour ───────────────


@pytest.mark.parametrize(
    "creds",
    [{HEADER: DEVICE_TOKEN}, bearer(ADMIN_TOKEN)],
    ids=["household-token", "admin-bearer"],
)
async def test_core_household_callers_still_use_an_idle_room(
    creds, rooms, claimed, no_player_starts, core_db, monkeypatch
) -> None:
    """Idle or not, a credential reads (with the self-healing ensure),
    adds and STARTS the room exactly as before the guest rule."""
    empty_idle = MPDStubClient()
    monkeypatch.setitem(mpd_mod._clients, "gq-empty", empty_idle)
    async with _core(**creds) as c:
        r = await c.get(f"/v1/admin/music/queue/{IDLE}")
        assert r.status_code == 200, r.text
        r = await c.post("/v1/admin/music/queue/gq-empty/add", json={"track_ids": [3]})
        assert r.status_code == 200, r.text
        assert r.json()["started"] is True
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/remove",
                         json={"song_ids": [(await rooms[ACTIVE].current_song())["id"]]})
        assert r.status_code == 200, r.text   # the playing entry is theirs to drop
        r = await c.post(f"/v1/admin/music/queue/{IDLE}/clear", json={})
        assert r.status_code == 200, r.text
    assert ("ensure_room_mpd", IDLE) in no_player_starts
    assert ("dispatch", "gq-empty", "start") in no_player_starts


async def test_core_a_stale_token_or_a_cookie_write_is_refused_as_before(
    rooms, claimed
) -> None:
    async with _core(**{HEADER: "stale-token"}) as c:
        assert (await c.get(f"/v1/admin/music/queue/{ACTIVE}")).status_code == 401
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/add", json={"track_ids": [1]})
        assert r.status_code == 401
    async with _core() as c:
        c.cookies.set(COOKIE, ADMIN_TOKEN)
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/move",
                         json={"song_id": 1, "to_position": 0})
        assert r.status_code == 403   # cookies never authorize a change
        assert (await c.get(f"/v1/admin/music/queue/{IDLE}")).status_code == 200


async def test_core_pre_setup_grace_is_unchanged(
    rooms, monkeypatch, no_player_starts, core_db
) -> None:
    install_fake_db(monkeypatch, admin=False)
    async with _core() as c:
        assert (await c.get(f"/v1/admin/music/queue/{IDLE}")).status_code == 200
        r = await c.post(f"/v1/admin/music/queue/{IDLE}/clear", json={})
        assert r.status_code == 200
    assert ("ensure_room_mpd", IDLE) in no_player_starts


# ─── Core: the household switch ───────────────────────────────────────────


async def test_core_switch_off_is_todays_behaviour(
    rooms, claimed, monkeypatch, no_player_starts, core_db
) -> None:
    monkeypatch.setattr(settings, "guest_queue_editing", False)
    before = _ids(rooms[ACTIVE])
    async with _core() as c:
        calls = [
            ("GET", f"/v1/admin/music/queue/{ACTIVE}", None),
            ("POST", f"/v1/admin/music/queue/{ACTIVE}/add", {"track_ids": [1]}),
            ("POST", f"/v1/admin/music/queue/{ACTIVE}/remove", {"song_ids": [before[-1]]}),
            ("POST", f"/v1/admin/music/queue/{ACTIVE}/move",
             {"song_id": before[-1], "to_position": 0}),
        ]
        for method, path, body in calls:
            r = await c.request(method, path, json=body)
            assert r.status_code == 401, f"{method} {path}: {r.text}"
            assert r.json()["detail"] == "X-Device-Token or admin session required"
    assert _ids(rooms[ACTIVE]) == before
    assert no_player_starts == []


def test_core_snapshot_carries_the_live_switch() -> None:
    """The web hop reads the switch from the core's snapshot (its own
    settings copy goes stale on a save). The snapshot needs a booted app,
    so this pins the line itself; the web half is tested below."""
    import inspect

    src = inspect.getsource(core_main.admin_snapshot)
    assert '"guest_queue_editing": bool(settings.guest_queue_editing)' in src


# ─── Core: the throttle ───────────────────────────────────────────────────


async def test_core_guest_edits_are_throttled_per_address(
    rooms, claimed, monkeypatch, no_player_starts, core_db
) -> None:
    monkeypatch.setattr(admin_auth.GUEST_QUEUE_LIMITER, "max_per_window", 2)
    stub = rooms[ACTIVE]
    target = _ids(stub)[-1]
    move = {"song_id": target, "to_position": 1}
    async with _core() as c:
        for _ in range(2):
            r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/move", json=move)
            assert r.status_code == 200, r.text
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/move", json=move)
        assert r.status_code == 429, r.text
        assert int(r.headers["retry-after"]) >= 1
        # An idle room's refusal costs the budget too (it is charged first).
        r = await c.post(f"/v1/admin/music/queue/{IDLE}/move", json=move)
        assert r.status_code == 429
        # Reads are not throttled; and another address has its own budget.
        assert (await c.get(f"/v1/admin/music/queue/{ACTIVE}")).status_code == 200
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/move", json=move,
                         headers={"X-Forwarded-For": "192.168.1.77"})
        assert r.status_code == 200, r.text   # loopback peer: XFF names the guest
    # A household credential is never on the guest budget.
    async with _core(**{HEADER: DEVICE_TOKEN}) as c:
        for _ in range(4):
            r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/move", json=move)
            assert r.status_code == 200, r.text


# ─── Core: transport controls for a guest ─────────────────────────────────


@pytest.fixture
def transport(monkeypatch, no_player_starts):
    """The transport route's handler, minus the router: records each
    synthetic turn it would have routed (the gate is what is under test);
    the frame to the satellite lands in ``no_player_starts``."""
    from domovoi.models import Response

    routed: list[tuple[str, str]] = []

    async def _route(transcript, room_id):
        routed.append((transcript, room_id))
        return Response(text="ok", matched_handler="music")

    monkeypatch.setattr(core_main, "_admin_route_intent", _route)
    return routed


@pytest.mark.parametrize("action", ["pause", "resume", "skip", "next", "previous"])
async def test_core_guest_transport_on_a_playing_room(
    action, rooms, claimed, transport, no_player_starts
) -> None:
    async with _core() as c:
        for room in (ACTIVE, PAUSED):
            r = await c.post(f"/v1/admin/music/{action}/{room}")
            assert r.status_code == 200, r.text
    assert [room for _t, room in transport] == [ACTIVE, PAUSED]
    assert not [c_ for c_ in no_player_starts if c_[0] != "dispatch"]


@pytest.mark.parametrize("room", [IDLE, UNKNOWN])
async def test_core_guest_transport_is_refused_on_an_idle_room(
    room, rooms, claimed, transport, no_player_starts
) -> None:
    async with _core() as c:
        for action in ("pause", "resume", "skip", "previous"):
            r = await c.post(f"/v1/admin/music/{action}/{room}")
            assert r.status_code == 403, r.text
            assert r.json()["detail"] == admin_auth.GUEST_ROOM_IDLE_DETAIL
    assert transport == []
    assert no_player_starts == []
    assert await rooms[IDLE].state() == "stop"


async def test_core_guest_cannot_stop_or_send_a_made_up_verb(
    rooms, claimed, transport, no_player_starts
) -> None:
    async with _core() as c:
        for action in ("stop", "explode"):
            r = await c.post(f"/v1/admin/music/{action}/{ACTIVE}")
            assert r.status_code == 401, r.text
            assert r.json()["detail"] == "X-Device-Token or admin session required"
    assert transport == []
    async with _core(**{HEADER: DEVICE_TOKEN}) as c:
        assert (await c.post(f"/v1/admin/music/stop/{IDLE}")).status_code == 200
    assert transport == [("stop the music", IDLE)]


async def test_core_switch_off_closes_guest_transport(
    rooms, claimed, transport, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "guest_queue_editing", False)
    async with _core() as c:
        r = await c.post(f"/v1/admin/music/pause/{ACTIVE}")
    assert r.status_code == 401
    assert transport == []


async def test_core_guest_transport_shares_the_throttle(
    rooms, claimed, transport, monkeypatch, core_db
) -> None:
    monkeypatch.setattr(admin_auth.GUEST_QUEUE_LIMITER, "max_per_window", 2)
    async with _core() as c:
        assert (await c.post(f"/v1/admin/music/pause/{ACTIVE}")).status_code == 200
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/move",
                         json={"song_id": _ids(rooms[ACTIVE])[-1], "to_position": 0})
        assert r.status_code == 200
        assert (await c.post(f"/v1/admin/music/resume/{ACTIVE}")).status_code == 429


# ─── Core: bans ───────────────────────────────────────────────────────────

GUEST_PHONE = {admin_auth.GUEST_DEVICE_HEADER: "browser-party",
               "User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/130"}


async def _every_guest_action(c: AsyncClient, room: str, stub: MPDStubClient) -> list[int]:
    ids = _ids(stub)   # [current, ..., last]: never remove the current one
    calls = [
        ("POST", f"/v1/admin/music/queue/{room}/add", {"track_ids": [1]}),
        ("POST", f"/v1/admin/music/queue/{room}/remove", {"song_ids": [ids[-1]]}),
        ("POST", f"/v1/admin/music/queue/{room}/move", {"song_id": ids[-2], "to_position": 0}),
        ("POST", f"/v1/admin/music/pause/{room}", None),
        ("POST", f"/v1/admin/music/resume/{room}", None),
        ("POST", f"/v1/admin/music/skip/{room}", None),
        ("POST", f"/v1/admin/music/previous/{room}", None),
    ]
    out = []
    for method, path, body in calls:
        r = await c.request(method, path, json=body)
        out.append(r.status_code)
    return out


@pytest.mark.parametrize("by", ["device_id", "source"])
async def test_core_a_banned_guest_is_refused_everything_on_every_room(
    by, rooms, claimed, transport, core_db, fake_bans
) -> None:
    ban = (fake_bans.ban(device_id="browser-party") if by == "device_id"
           else fake_bans.ban(source="127.0.0.1"))
    async with _core(**GUEST_PHONE) as c:
        for room in (ACTIVE, PAUSED):
            assert set(await _every_guest_action(c, room, rooms[room])) == {403}, room
        r = await c.post(f"/v1/admin/music/pause/{ACTIVE}")
        assert r.json()["detail"] == admin_auth.GUEST_BANNED_DETAIL
        # Reading is not changing the music: a banned guest still reads.
        assert (await c.get(f"/v1/admin/music/queue/{ACTIVE}")).status_code == 200
    assert transport == []

    # Unban: everything is back.
    fake_bans.bans.remove(ban)
    async with _core(**GUEST_PHONE) as c:
        assert set(await _every_guest_action(c, ACTIVE, rooms[ACTIVE])) == {200}


async def test_core_a_ban_never_touches_a_household_credential_on_that_address(
    rooms, claimed, transport, core_db, fake_bans
) -> None:
    fake_bans.ban(device_id="browser-party", source="127.0.0.1")
    for creds in ({HEADER: DEVICE_TOKEN}, bearer(ADMIN_TOKEN)):
        async with _core(**GUEST_PHONE, **creds) as c:
            assert set(await _every_guest_action(c, ACTIVE, rooms[ACTIVE])) == {200}
            assert (await c.post(f"/v1/admin/music/stop/{IDLE}")).status_code == 200


async def test_core_a_ban_check_that_fails_refuses_the_guest(
    rooms, claimed, transport, fake_bans
) -> None:
    fake_bans.fail = True
    async with _core(**GUEST_PHONE) as c:
        r = await c.post(f"/v1/admin/music/pause/{ACTIVE}")
    assert r.status_code == 503
    assert transport == []


async def test_core_records_what_each_guest_did(
    rooms, claimed, transport, core_db, fake_bans
) -> None:
    async with _core(**GUEST_PHONE, **{"X-Forwarded-For": "192.168.1.77"}) as c:
        assert (await c.post(f"/v1/admin/music/skip/{ACTIVE}")).status_code == 200
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/add", json={"track_ids": [2]})
        assert r.status_code == 200
        # Refused and read-only calls are not "seen".
        assert (await c.post(f"/v1/admin/music/pause/{IDLE}")).status_code == 403
        assert (await c.get(f"/v1/admin/music/queue/{ACTIVE}")).status_code == 200
    assert fake_bans.seen == [
        {"device_id": "browser-party", "source": "192.168.1.77",
         "user_agent": GUEST_PHONE["User-Agent"], "room_id": ACTIVE, "action": "skip"},
        {"device_id": "browser-party", "source": "192.168.1.77",
         "user_agent": GUEST_PHONE["User-Agent"], "room_id": ACTIVE, "action": "queue add"},
    ]
    # A household caller is never recorded.
    async with _core(**{HEADER: DEVICE_TOKEN}) as c:
        assert (await c.post(f"/v1/admin/music/skip/{ACTIVE}")).status_code == 200
    assert len(fake_bans.seen) == 2


def test_the_user_agent_is_believed_from_the_web_hop_only() -> None:
    from domovoi.tests.auth_testkit import make_request

    hop = make_request({"User-Agent": "python-httpx/0.28",
                        admin_auth.FORWARDED_UA_HEADER: "Phone Chrome"}, client_host="127.0.0.1")
    assert admin_auth.request_user_agent(hop) == "Phone Chrome"
    lan = make_request({"User-Agent": "curl/8", admin_auth.FORWARDED_UA_HEADER: "spoofed"})
    assert admin_auth.request_user_agent(lan) == "curl/8"


# ─── Core: route tiers ────────────────────────────────────────────────────


def _core_gates(method: str, path: str) -> list[Any]:
    from domovoi.tests.test_core_route_tiers import _gates_for

    return _gates_for(method, path)


@pytest.mark.parametrize("suffix", ["add", "remove", "move"])
def test_core_edit_routes_wear_the_guest_gate(suffix) -> None:
    gates = _core_gates("POST", f"/v1/admin/music/queue/{{room_id}}/{suffix}")
    assert guest_queue.require_device_or_active_room_guest in gates
    assert admin_auth.require_device not in gates   # it runs inside the gate


def test_core_read_route_wears_the_guest_read_gate() -> None:
    gates = _core_gates("GET", "/v1/admin/music/queue/{room_id}")
    assert guest_queue.require_device_read_or_active_room_guest in gates
    assert admin_auth.require_device_read not in gates


def test_core_clear_stays_on_the_device_tier() -> None:
    gates = _core_gates("POST", "/v1/admin/music/queue/{room_id}/clear")
    assert admin_auth.require_device in gates
    assert guest_queue.require_device_or_active_room_guest not in gates


# ─── Web: the fake core and the fake session ──────────────────────────────


class FakeCore:
    """Stands in for the web→core hop: records what was sent (and with
    which headers), and answers each path the way a test sets it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any, dict[str, str]]] = []
        self.read_answer: tuple[int, Any] = (200, {"items": [], "current_song_id": None})
        self.post_answer: tuple[int, Any] = (200, {})

    async def get_admin(self, path, timeout=10.0, headers=None):
        self.calls.append(("GET", path, None, dict(headers or {})))
        return self.read_answer

    async def post_admin(self, path, body=None, timeout=30.0, headers=None):
        self.calls.append(("POST", path, body, dict(headers or {})))
        if path.endswith("/add"):
            return 200, {"queued": [{"song_id": 501, "file": "a.mp3"}],
                         "requested": 1, "started": False}
        if path.endswith("/remove"):
            return 200, {"removed": list(body.get("song_ids") or []), "skipped": []}
        return self.post_answer


class FakeWebDb:
    """The web module's session: provenance rows, a block, a device
    roster, and a record of every provenance write."""

    def __init__(self) -> None:
        self.provenance: list[tuple[int, str | None, str | None, Any]] = []
        self.block: tuple | None = None
        self.devices: dict[str, str] = {}
        self.stamped: list[dict[str, Any]] = []
        self.guest_ids: list[tuple[int, str | None, str | None]] = []

    def scope(self):
        db = self

        class _Session:
            async def execute(self, sql, params=None):
                q = str(sql)
                p = params or {}
                if "FROM room_queue_items q" in q:
                    return _Rows(db.provenance)
                if "SELECT song_id, guest_device_id, guest_source" in q:
                    return _Rows(db.guest_ids)
                if "INSERT INTO room_queue_items" in q:
                    db.stamped.append(dict(p))
                    return _Rows([])
                if "SELECT name FROM devices" in q:
                    name = db.devices.get(p.get("id"))
                    return _Rows([(name,)] if name else [])
                if "FROM queue_device_blocks" in q:
                    hit = db.block is not None and (
                        db.block[1] == p.get("device_id")
                        or (db.block[2] is not None and db.block[2] == p.get("device_name"))
                    )
                    return _Rows([db.block] if hit else [])
                return _Rows([])

        @asynccontextmanager
        async def _scope():
            yield _Session()

        return _scope


@pytest.fixture
def web_side(monkeypatch, claimed, fake_bans):
    core = FakeCore()
    db = FakeWebDb()
    monkeypatch.setattr(queue_api, "get_admin", core.get_admin)
    monkeypatch.setattr(queue_api, "post_admin", core.post_admin)
    monkeypatch.setattr(queue_api, "session_scope", db.scope())
    monkeypatch.setattr(queue_api, "get_cached_snapshot", lambda: {"guest_queue_editing": True})
    return core, db


def _web(**headers) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={**BROWSER, **headers},
    )


_EDITS = [
    ("add", {"track_ids": [1], "device_id": "browser-guest1"}),
    ("remove", {"song_ids": [7], "device_id": "browser-guest1"}),
    ("move", {"song_id": 7, "to_position": 0, "device_id": "browser-guest1"}),
]

_CREDENTIAL_HEADERS = ("authorization", "x-device-token", "cookie")


# ─── Web: a guest passes through, as a guest ──────────────────────────────


async def test_web_guest_edits_pass_through_to_the_core_as_a_guest(web_side) -> None:
    core, _db = web_side
    async with _web() as c:
        for verb, body in _EDITS:
            r = await c.post(f"/api/music/queue/{ACTIVE}/{verb}", json=body)
            assert r.status_code == 200, f"{verb}: {r.text}"
    assert [p for _m, p, _b, _h in core.calls] == [
        f"/v1/admin/music/queue/{ACTIVE}/{v}" for v, _ in _EDITS
    ]
    for _m, _p, _b, headers in core.calls:
        sent = {k.lower() for k in headers}
        assert not sent & set(_CREDENTIAL_HEADERS), headers   # never upgraded
        assert "x-forwarded-for" in sent                     # its own address
        assert "x-requested-with" in sent                    # not cross-site


async def test_web_guest_read_carries_no_credential_either(web_side) -> None:
    core, _db = web_side
    async with _web() as c:
        r = await c.get(f"/api/music/queue/{ACTIVE}?device_id=browser-guest1")
    assert r.status_code == 200, r.text
    assert r.json()["guest"] is True
    (_m, _p, _b, headers), = core.calls
    assert not {k.lower() for k in headers} & set(_CREDENTIAL_HEADERS)


async def test_web_core_refusal_of_a_guest_comes_back_verbatim(web_side) -> None:
    """The web hop can't see MPD: an idle room's 403 (or a playing-entry
    removal's) is the core's, passed back untouched for an edit."""
    core, _db = web_side

    async def _idle(path, body=None, timeout=30.0, headers=None):
        core.calls.append(("POST", path, body, dict(headers or {})))
        return 403, {"detail": admin_auth.GUEST_ROOM_IDLE_DETAIL}

    queue_api.post_admin = _idle  # restored by monkeypatch via web_side's setattr
    async with _web() as c:
        r = await c.post(f"/api/music/queue/{IDLE}/move",
                         json={"song_id": 1, "to_position": 0, "device_id": "browser-guest1"})
    assert r.status_code == 403
    assert r.json()["detail"] == admin_auth.GUEST_ROOM_IDLE_DETAIL


async def test_web_guest_cannot_clear(web_side) -> None:
    core, _db = web_side
    async with _web() as c:
        r = await c.post(f"/api/music/queue/{ACTIVE}/clear", json={"device_id": "browser-guest1"})
    assert r.status_code == 401, r.text
    assert core.calls == []


# ─── Web: the read, for a guest ───────────────────────────────────────────


async def test_web_guest_read_of_an_idle_room_is_quiet_and_not_editable(web_side) -> None:
    core, _db = web_side
    core.read_answer = (403, {"detail": admin_auth.GUEST_ROOM_IDLE_DETAIL})
    async with _web() as c:
        r = await c.get(f"/api/music/queue/{IDLE}?device_id=browser-guest1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["editable"] is False
    assert body["blocked_reason"] == queue_api.GUEST_IDLE_REASON
    assert body["guest"] is True


async def test_web_household_read_carries_its_token_and_gets_no_guest_flags(web_side) -> None:
    """A household read is forwarded with its token and answered as the
    core answered it. (The core's idle sentence is said to guests only, so
    the web maps it to the quiet answer whoever it thought was asking.)"""
    core, _db = web_side
    core.read_answer = (200, {"items": [{"song_id": 1, "pos": 0, "file": "a.mp3"}],
                              "current_song_id": 1})
    async with _web(**{HEADER: DEVICE_TOKEN}) as c:
        body = (await c.get(f"/api/music/queue/{ACTIVE}")).json()
    assert body["guest"] is False and body["editable"] is True
    assert core.calls[0][3].get("X-Device-Token") == DEVICE_TOKEN


async def test_web_guest_sees_guest_entries_but_no_household_device(web_side) -> None:
    core, db = web_side
    core.read_answer = (200, {
        "items": [
            {"song_id": 1, "pos": 0, "file": "a.mp3"},
            {"song_id": 2, "pos": 1, "file": "b.mp3"},
            {"song_id": 3, "pos": 2, "file": "c.mp3"},
        ],
        "current_song_id": 1,
    })
    db.provenance = [
        (1, "Kitchen iPad", "android-kitchen", None),
        (2, queue_api.GUEST_ADDED_BY, None, None),
    ]
    async with _web() as c:
        guest = (await c.get(f"/api/music/queue/{ACTIVE}")).json()
    assert [i["added_by"] for i in guest["items"]] == [None, "a guest", None]
    assert [i["added_by_device_id"] for i in guest["items"]] == [None, None, None]
    assert guest["editable"] is True and guest["guest"] is True

    async with _web(**{HEADER: DEVICE_TOKEN}) as c:
        household = (await c.get(f"/api/music/queue/{ACTIVE}")).json()
    assert [i["added_by"] for i in household["items"]] == ["Kitchen iPad", "a guest", None]
    assert household["items"][0]["added_by_device_id"] == "android-kitchen"
    assert household["guest"] is False


# ─── Web: provenance ──────────────────────────────────────────────────────


async def test_web_guest_add_is_stamped_a_guest_not_the_claimed_device(web_side) -> None:
    """A guest claims the id of a registered household device: the entry
    still reads "a guest", with no device id to join a name through."""
    _core_, db = web_side
    db.devices["android-kitchen"] = "Kitchen iPad"
    async with _web() as c:
        r = await c.post(f"/api/music/queue/{ACTIVE}/add",
                         json={"track_ids": [1], "device_id": "android-kitchen"})
    assert r.status_code == 200, r.text
    assert db.stamped == [{"room_id": ACTIVE, "song_id": 501,
                           "device_id": None, "device_name": queue_api.GUEST_ADDED_BY,
                           "guest_device_id": "android-kitchen", "guest_source": "127.0.0.1"}]


async def test_web_household_add_is_stamped_with_its_device_as_before(web_side) -> None:
    _core_, db = web_side
    db.devices["android-kitchen"] = "Kitchen iPad"
    async with _web(**{HEADER: DEVICE_TOKEN}) as c:
        r = await c.post(f"/api/music/queue/{ACTIVE}/add",
                         json={"track_ids": [1], "device_id": "android-kitchen"})
    assert r.status_code == 200, r.text
    assert db.stamped[0]["device_id"] == "android-kitchen"
    assert db.stamped[0]["device_name"] == "Kitchen iPad"


# ─── Web: the blocklist still applies to a guest ──────────────────────────


async def test_web_a_block_by_id_stops_a_guest(web_side) -> None:
    core, db = web_side
    db.block = (1, "browser-guest1", None, ACTIVE, "party's over")
    async with _web() as c:
        for verb, body in _EDITS:
            r = await c.post(f"/api/music/queue/{ACTIVE}/{verb}", json=body)
            assert r.status_code == 403, f"{verb}: {r.text}"
            # Generic for a guest: never the block's own words (finding 3).
            assert r.json()["detail"] == queue_api.GUEST_BLOCKED_DETAIL
        core.read_answer = (200, {"items": [], "current_song_id": None})
        read = (await c.get(f"/api/music/queue/{ACTIVE}?device_id=browser-guest1")).json()
    assert read["editable"] is False
    assert read["blocked_reason"] == queue_api.GUEST_BLOCKED_REASON
    assert [c_ for c_ in core.calls if c_[0] == "POST"] == []


# ─── Web: the switch, the throttle, household callers ─────────────────────


@pytest.mark.parametrize("source", ["snapshot", "own-settings"])
async def test_web_switch_off_refuses_a_guest_before_the_hop(
    source, web_side, monkeypatch
) -> None:
    core, _db = web_side
    if source == "snapshot":
        monkeypatch.setattr(queue_api, "get_cached_snapshot",
                            lambda: {"guest_queue_editing": False})
    else:   # the core has not answered yet: this process's own copy
        monkeypatch.setattr(queue_api, "get_cached_snapshot", lambda: None)
        monkeypatch.setattr(settings, "guest_queue_editing", False)
    async with _web() as c:
        for verb, body in _EDITS:
            r = await c.post(f"/api/music/queue/{ACTIVE}/{verb}", json=body)
            assert r.status_code == 401, f"{verb}: {r.text}"
            assert r.json()["detail"] == "X-Device-Token or admin session required"
    assert core.calls == []


async def test_web_the_snapshot_wins_over_a_stale_own_copy(web_side, monkeypatch) -> None:
    monkeypatch.setattr(settings, "guest_queue_editing", False)
    assert queue_api.guests_may_edit() is True   # web_side's snapshot says on
    monkeypatch.setattr(queue_api, "get_cached_snapshot",
                        lambda: {"guest_queue_editing": False})
    monkeypatch.setattr(settings, "guest_queue_editing", True)
    assert queue_api.guests_may_edit() is False


async def test_web_guest_edits_are_throttled(web_side, monkeypatch) -> None:
    core, _db = web_side
    monkeypatch.setattr(admin_auth.GUEST_QUEUE_LIMITER, "max_per_window", 2)
    move = {"song_id": 7, "to_position": 0, "device_id": "browser-guest1"}
    async with _web() as c:
        for _ in range(2):
            assert (await c.post(f"/api/music/queue/{ACTIVE}/move", json=move)).status_code == 200
        r = await c.post(f"/api/music/queue/{ACTIVE}/move", json=move)
        assert r.status_code == 429, r.text
        assert r.headers.get("retry-after")
    assert len(core.calls) == 2
    async with _web(**{HEADER: DEVICE_TOKEN}) as c:
        for _ in range(3):
            assert (await c.post(f"/api/music/queue/{ACTIVE}/move", json=move)).status_code == 200


async def test_web_household_callers_are_judged_as_before(web_side) -> None:
    core, _db = web_side
    move = {"song_id": 7, "to_position": 0, "device_id": "browser-abc"}
    async with _web(**{HEADER: "stale-token"}) as c:
        assert (await c.post(f"/api/music/queue/{ACTIVE}/move", json=move)).status_code == 401
    async with _web() as c:
        c.cookies.set(COOKIE, ADMIN_TOKEN)
        assert (await c.post(f"/api/music/queue/{ACTIVE}/move", json=move)).status_code == 403
    assert core.calls == []
    async with _web(**bearer(ADMIN_TOKEN)) as c:
        assert (await c.post(f"/api/music/queue/{ACTIVE}/move", json=move)).status_code == 200
        r = await c.post(f"/api/music/queue/{ACTIVE}/clear", json={"device_id": "browser-abc"})
        assert r.status_code == 200
    # Forwarded as THEIR credential, not as a guest.
    assert all(h.get("Authorization") == f"Bearer {ADMIN_TOKEN}" for _m, _p, _b, h in core.calls)


async def test_web_pre_setup_grace_is_unchanged(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=False)
    core = FakeCore()
    monkeypatch.setattr(queue_api, "post_admin", core.post_admin)
    monkeypatch.setattr(queue_api, "session_scope", FakeWebDb().scope())
    async with _web() as c:
        r = await c.post(f"/api/music/queue/{IDLE}/clear", json={"device_id": "browser-abc"})
    assert r.status_code == 200, r.text


# ─── Web: transport and bans ──────────────────────────────────────────────


async def test_web_guest_transport_reaches_the_core_as_a_guest(web_side, monkeypatch) -> None:
    """pause / resume / skip / stop stay OPEN at this hop (the kiosk row,
    FE-3) and previous takes the guest gate; either way the guest arrives
    at the core with nothing but its device id and user agent."""
    import web.backend.api.music as music_api

    core, _db = web_side
    monkeypatch.setattr(music_api, "post_admin", core.post_admin)
    async with _web(**GUEST_PHONE) as c:
        for verb in ("pause", "resume", "skip", "previous"):
            r = await c.post(f"/api/music/{verb}/{ACTIVE}")
            assert r.status_code == 200, f"{verb}: {r.text}"
    assert [p for _m, p, _b, _h in core.calls] == [
        f"/v1/admin/music/{v}/{ACTIVE}" for v in ("pause", "resume", "skip", "previous")
    ]
    for _m, _p, _b, headers in core.calls:
        assert not {k.lower() for k in headers} & set(_CREDENTIAL_HEADERS)
        assert headers["X-Domovoi-Device-Id"] == "browser-party"
        assert headers["X-Forwarded-User-Agent"] == GUEST_PHONE["User-Agent"]


async def test_web_a_banned_guest_is_refused_before_the_hop(
    web_side, monkeypatch, fake_bans
) -> None:
    import web.backend.api.music as music_api

    core, _db = web_side
    monkeypatch.setattr(music_api, "post_admin", core.post_admin)
    ban = fake_bans.ban(device_id="browser-party")
    async with _web(**GUEST_PHONE) as c:
        for verb, body in _EDITS:
            r = await c.post(f"/api/music/queue/{ACTIVE}/{verb}",
                             json={**body, "device_id": "browser-party"})
            assert r.status_code == 403, f"{verb}: {r.text}"
            assert r.json()["detail"] == admin_auth.GUEST_BANNED_DETAIL
        r = await c.post(f"/api/music/previous/{ACTIVE}")
        assert r.status_code == 403
        read = (await c.get(f"/api/music/queue/{ACTIVE}?device_id=browser-party")).json()
    assert [c_[0] for c_ in core.calls] == ["GET"]   # nothing changed hands
    assert read["editable"] is False
    assert read["blocked_reason"] == queue_api.GUEST_BANNED_REASON

    # A ban on the BODY's id is caught even when the header names another.
    fake_bans.bans.remove(ban)
    fake_bans.ban(device_id="browser-sneaky")
    async with _web(**GUEST_PHONE) as c:
        r = await c.post(f"/api/music/queue/{ACTIVE}/add",
                         json={"track_ids": [1], "device_id": "browser-sneaky"})
    assert r.status_code == 403, r.text

    # A household caller on the same address and id is never banned.
    async with _web(**GUEST_PHONE, **{HEADER: DEVICE_TOKEN}) as c:
        r = await c.post(f"/api/music/queue/{ACTIVE}/add",
                         json={"track_ids": [1], "device_id": "browser-sneaky"})
        assert r.status_code == 200, r.text
        assert (await c.post(f"/api/music/previous/{ACTIVE}")).status_code == 200


async def test_web_a_ban_check_that_fails_refuses_the_guest(web_side, fake_bans) -> None:
    core, _db = web_side
    fake_bans.fail = True
    async with _web(**GUEST_PHONE) as c:
        r = await c.post(f"/api/music/queue/{ACTIVE}/move",
                         json={"song_id": 7, "to_position": 0, "device_id": "browser-party"})
    assert r.status_code == 503
    assert core.calls == []


async def test_web_a_ban_check_that_fails_on_a_read_leaves_it_read_only(
    web_side, fake_bans
) -> None:
    """A read is never refused for a ban it can't check — the guest sees
    the queue, not editable — while an edit fails closed (above)."""
    core, _db = web_side
    core.read_answer = (200, {"items": [{"song_id": 1, "pos": 0, "file": "a.mp3"}],
                              "current_song_id": 1})
    fake_bans.fail = True
    async with _web(**GUEST_PHONE) as c:
        r = await c.get(f"/api/music/queue/{ACTIVE}")
    assert r.status_code == 200, r.text
    assert r.json()["editable"] is False
    assert len(r.json()["items"]) == 1


async def test_web_admin_read_names_the_guest_behind_an_entry(web_side) -> None:
    core, db = web_side
    core.read_answer = (200, {
        "items": [{"song_id": 1, "pos": 0, "file": "a.mp3"},
                  {"song_id": 2, "pos": 1, "file": "b.mp3"}],
        "current_song_id": 1,
    })
    db.provenance = [(1, "Kitchen iPad", "android-kitchen", None),
                     (2, queue_api.GUEST_ADDED_BY, None, None)]
    db.guest_ids = [(2, "browser-party", "192.168.1.77")]
    async with _web(**bearer(ADMIN_TOKEN)) as c:
        admin = (await c.get(f"/api/music/queue/{ACTIVE}")).json()
    assert admin["admin"] is True
    assert [(i["guest_device_id"], i["guest_source"]) for i in admin["items"]] == [
        (None, None), ("browser-party", "192.168.1.77")]
    for creds in ({HEADER: DEVICE_TOKEN}, {}):
        async with _web(**creds) as c:
            other = (await c.get(f"/api/music/queue/{ACTIVE}")).json()
        assert other["admin"] is False
        assert all(i["guest_device_id"] is None and i["guest_source"] is None
                   for i in other["items"])


async def test_web_guest_add_records_which_guest_for_the_admin(web_side) -> None:
    _core_, db = web_side
    async with _web(**GUEST_PHONE) as c:
        r = await c.post(f"/api/music/queue/{ACTIVE}/add",
                         json={"track_ids": [1], "device_id": "browser-party"})
    assert r.status_code == 200, r.text
    assert db.stamped[0]["guest_device_id"] == "browser-party"
    assert db.stamped[0]["guest_source"] == "127.0.0.1"


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/music/guests"), ("GET", "/api/music/guest-bans"),
     ("POST", "/api/music/guest-bans"), ("DELETE", "/api/music/guest-bans/1")],
)
async def test_web_guest_management_is_admin_only(method, path, web_side) -> None:
    body = {"device_id": "browser-party"} if method == "POST" else None
    for creds in ({}, {HEADER: DEVICE_TOKEN}):
        async with _web(**creds) as c:
            r = await c.request(method, path, json=body)
        assert r.status_code == 401, f"{creds}: {r.text}"


# ─── Web: route tiers ─────────────────────────────────────────────────────


def _web_gates(method: str, path: str) -> list[Any]:
    from domovoi.tests.test_web_daily_tier import _gates

    return _gates(method, path)


@pytest.mark.parametrize("suffix", ["add", "remove", "move"])
def test_web_edit_routes_wear_the_guest_gate(suffix) -> None:
    gates = _web_gates("POST", f"/api/music/queue/{{room_id}}/{suffix}")
    assert queue_api.require_device_or_guest in gates
    assert admin_auth.require_device not in gates


def test_web_clear_stays_on_the_device_tier() -> None:
    gates = _web_gates("POST", "/api/music/queue/{room_id}/clear")
    assert admin_auth.require_device in gates
    assert queue_api.require_device_or_guest not in gates


# ─── Against the real tables ──────────────────────────────────────────────
#
# V023's tables are deliberately NOT in conftest's TABLES_TO_TRUNCATE (the
# V016 / V019 / V021 precedent: a lane patched by hand may not have them).
# These tests apply V023 themselves — IF NOT EXISTS throughout — and empty
# its tables.

V023 = (
    Path(__file__).resolve().parents[2]
    / "domovoi" / "db" / "migrations" / "V023__guest_access.sql"
)


def v023_statements() -> list[str]:
    lines = V023.read_text(encoding="utf-8").splitlines()
    sql = "\n".join(line.split("--", 1)[0] for line in lines)
    return [st.strip() for st in sql.split(";") if st.strip()]


def test_v023_is_the_next_core_migration_and_splits_cleanly() -> None:
    migrations = V023.parent
    versions = sorted(int(p.name[1:4]) for p in migrations.glob("V*__*.sql"))
    assert versions == list(range(1, len(versions) + 1))
    assert versions[-1] == 23
    statements = v023_statements()
    assert len(statements) == 8
    assert all("IF NOT EXISTS" in st for st in statements)


@pytest.fixture
def clean_queue_tables():
    from sqlalchemy import text as sql

    from domovoi.db.session import engine

    async def _apply_and_truncate():
        async with engine.begin() as conn:
            for statement in v023_statements():
                await conn.exec_driver_sql(statement)
            await conn.execute(
                sql("TRUNCATE queue_device_blocks, room_queue_items, devices, "
                    "guests_seen, guest_bans RESTART IDENTITY CASCADE")
            )

    asyncio.run(_apply_and_truncate())
    yield
    asyncio.run(_apply_and_truncate())


@pytest.fixture
def real_web_side(monkeypatch, claimed, clean_queue_tables):
    """Real provenance / block / device / V023 tables; a fake core holding
    a toy queue; the auth primitives faked so 'no credential' is a guest."""
    state = {"items": [], "next": 900}

    async def _get_admin(path, timeout=10.0, headers=None):
        return 200, {"items": list(state["items"]),
                     "current_song_id": state["items"][0]["song_id"] if state["items"] else None}

    async def _post_admin(path, body=None, timeout=30.0, headers=None):
        if path.endswith("/add"):
            queued = []
            for _ in body["track_ids"]:
                sid = state["next"]
                state["next"] += 1
                state["items"].append({"song_id": sid, "pos": len(state["items"]),
                                       "file": f"{sid}.mp3"})
                queued.append({"song_id": sid, "file": f"{sid}.mp3"})
            return 200, {"queued": queued, "requested": len(queued), "started": False}
        return 200, {"removed": [], "skipped": []}

    monkeypatch.setattr(queue_api, "get_admin", _get_admin)
    monkeypatch.setattr(queue_api, "post_admin", _post_admin)
    monkeypatch.setattr(queue_api, "get_cached_snapshot", lambda: {"guest_queue_editing": True})
    return state


@requires_db
async def test_db_guest_provenance_and_names_round_trip(real_web_side) -> None:
    async with _web(**{HEADER: DEVICE_TOKEN}) as house:
        r = await house.post("/api/devices/register",
                             json={"device_id": "android-kitchen", "name": "Kitchen iPad"})
        assert r.status_code == 200, r.text
        r = await house.post(f"/api/music/queue/{ACTIVE}/add",
                             json={"track_ids": [1], "device_id": "android-kitchen"})
        assert r.status_code == 200, r.text
    async with _web(**GUEST_PHONE) as guest:
        # The guest claims the kitchen iPad's id: still "a guest".
        r = await guest.post(f"/api/music/queue/{ACTIVE}/add",
                             json={"track_ids": [2], "device_id": "android-kitchen"})
        assert r.status_code == 200, r.text
        seen = (await guest.get(f"/api/music/queue/{ACTIVE}")).json()
    assert [i["added_by"] for i in seen["items"]] == [None, "a guest"]
    assert all(i["added_by_device_id"] is None for i in seen["items"])
    assert all(i["guest_device_id"] is None for i in seen["items"])
    async with _web(**{HEADER: DEVICE_TOKEN}) as house:
        full = (await house.get(f"/api/music/queue/{ACTIVE}")).json()
    assert [i["added_by"] for i in full["items"]] == ["Kitchen iPad", "a guest"]
    assert [i["added_by_device_id"] for i in full["items"]] == ["android-kitchen", None]
    assert all(i["guest_device_id"] is None for i in full["items"])   # not an admin
    async with _web(**bearer(ADMIN_TOKEN)) as admin:
        as_admin = (await admin.get(f"/api/music/queue/{ACTIVE}")).json()
    assert [(i["guest_device_id"], i["guest_source"]) for i in as_admin["items"]] == [
        (None, None), ("android-kitchen", "127.0.0.1")]


@requires_db
async def test_db_a_block_by_id_still_applies_to_a_guest(real_web_side) -> None:
    async with _web(**bearer(ADMIN_TOKEN)) as admin:
        r = await admin.post("/api/music/queue-blocks",
                             json={"device_id": "browser-party", "room_id": ACTIVE,
                                   "note": "party's over"})
        assert r.status_code == 201, r.text
    async with _web() as guest:
        r = await guest.post(f"/api/music/queue/{ACTIVE}/add",
                             json={"track_ids": [1], "device_id": "browser-party"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == queue_api.GUEST_BLOCKED_DETAIL
        read = (await guest.get(f"/api/music/queue/{ACTIVE}?device_id=browser-party")).json()
        assert read["editable"] is False
        assert "party's over" not in (read["blocked_reason"] or "")
        # Another guest id is not blocked.
        r = await guest.post(f"/api/music/queue/{ACTIVE}/add",
                             json={"track_ids": [1], "device_id": "browser-other"})
        assert r.status_code == 200, r.text
    assert real_web_side["items"] and len(real_web_side["items"]) == 1


@requires_db
async def test_db_guest_bans_are_managed_by_an_admin_and_match_id_or_address(
    real_web_side,
) -> None:
    async with _web(**bearer(ADMIN_TOKEN)) as admin:
        assert (await admin.post("/api/music/guest-bans", json={})).status_code == 400
        r = await admin.post("/api/music/guest-bans",
                             json={"device_id": "browser-party", "source": "192.168.1.77",
                                   "note": "kept skipping"})
        assert r.status_code == 201, r.text
        ban = r.json()
        dupe = await admin.post("/api/music/guest-bans",
                                json={"device_id": "browser-party", "source": "192.168.1.77"})
        assert dupe.status_code == 409
        listed = (await admin.get("/api/music/guest-bans")).json()
        assert [b["id"] for b in listed] == [ban["id"]]

    # The real matcher: the id alone, the address alone, or neither.
    assert (await guest_bans.find_ban("browser-party", "10.0.0.9"))["id"] == ban["id"]
    assert (await guest_bans.find_ban("browser-new", "192.168.1.77"))["id"] == ban["id"]
    assert await guest_bans.find_ban("browser-new", "10.0.0.9") is None
    assert await guest_bans.find_ban(None, None) is None

    async with _web(**{**GUEST_PHONE, admin_auth.GUEST_DEVICE_HEADER: "browser-party"}) as guest:
        r = await guest.post(f"/api/music/queue/{ACTIVE}/add",
                             json={"track_ids": [1], "device_id": "browser-party"})
        assert r.status_code == 403, r.text

    async with _web(**bearer(ADMIN_TOKEN)) as admin:
        assert (await admin.delete(f"/api/music/guest-bans/{ban['id']}")).status_code == 204
        assert (await admin.delete(f"/api/music/guest-bans/{ban['id']}")).status_code == 404
    async with _web(**{**GUEST_PHONE, admin_auth.GUEST_DEVICE_HEADER: "browser-party"}) as guest:
        r = await guest.post(f"/api/music/queue/{ACTIVE}/add",
                             json={"track_ids": [1], "device_id": "browser-party"})
        assert r.status_code == 200, r.text


@requires_db
async def test_db_the_core_records_guests_and_an_admin_bans_from_the_list(
    rooms, claimed, transport, clean_queue_tables, monkeypatch
) -> None:
    """End to end on the real V023 tables: the core lets a guest act and
    records it, an admin sees it under guests seen and bans it, the core
    refuses it, an unban lets it back."""
    # The rooms fixture faked the ban module; this test wants the real one.
    monkeypatch.setattr(guest_bans, "find_ban", _REAL_FIND_BAN)
    monkeypatch.setattr(guest_bans, "record_guest", _REAL_RECORD_GUEST)
    monkeypatch.setattr(queue_api, "get_cached_snapshot", lambda: {"guest_queue_editing": True})

    phone = {**GUEST_PHONE, "X-Forwarded-For": "192.168.1.88"}
    async with _core(**phone) as c:
        assert (await c.post(f"/v1/admin/music/skip/{ACTIVE}")).status_code == 200
    async with _web(**bearer(ADMIN_TOKEN)) as admin:
        seen = (await admin.get("/api/music/guests")).json()
        assert len(seen) == 1
        row = seen[0]
        assert (row["device_id"], row["source"], row["room_id"], row["last_action"]) == (
            "browser-party", "192.168.1.88", ACTIVE, "skip")
        assert row["user_agent"] == GUEST_PHONE["User-Agent"]
        assert row["ban_id"] is None
        r = await admin.post("/api/music/guest-bans",
                             json={"device_id": row["device_id"], "source": row["source"]})
        assert r.status_code == 201, r.text
        ban_id = r.json()["id"]
        assert (await admin.get("/api/music/guests")).json()[0]["ban_id"] == ban_id

    async with _core(**phone) as c:
        for room in (ACTIVE, PAUSED):
            r = await c.post(f"/v1/admin/music/pause/{room}")
            assert r.status_code == 403, r.text
    # A cleared browser (new id) on the same lease is still the banned guest.
    async with _core(**{**phone, admin_auth.GUEST_DEVICE_HEADER: "browser-fresh"}) as c:
        assert (await c.post(f"/v1/admin/music/pause/{ACTIVE}")).status_code == 403
    # The household, on that very address, is untouched.
    async with _core(**phone, **{HEADER: DEVICE_TOKEN}) as c:
        assert (await c.post(f"/v1/admin/music/pause/{ACTIVE}")).status_code == 200

    async with _web(**bearer(ADMIN_TOKEN)) as admin:
        assert (await admin.delete(f"/api/music/guest-bans/{ban_id}")).status_code == 204
    async with _core(**phone) as c:
        assert (await c.post(f"/v1/admin/music/pause/{ACTIVE}")).status_code == 200


# ─── The independent review's findings (2026-10-11), as tests ─────────────


async def test_r2_a_cross_site_post_is_refused_for_a_guest(
    rooms, claimed, transport, no_player_starts
) -> None:
    """Finding 1. What a page on any site can make a LAN browser send to
    the core: a POST with no custom header, a foreign Origin, no body. A
    guest needs no credential, so the header is what tells it apart."""
    async with _core(cross_site=True, Origin="https://evil.example") as c:
        r = await c.post(f"/v1/admin/music/skip/{ACTIVE}")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == admin_auth.GUEST_CROSS_SITE_DETAIL
        r = await c.get(f"/v1/admin/music/queue/{ACTIVE}")
        assert r.status_code == 403, r.text
    assert transport == []
    # The dashboard's way: X-Requested-With, or its device id header.
    async with _core() as c:
        assert (await c.post(f"/v1/admin/music/skip/{ACTIVE}")).status_code == 200
    async with _core(cross_site=True, **{admin_auth.GUEST_DEVICE_HEADER: "browser-x"}) as c:
        assert (await c.post(f"/v1/admin/music/pause/{ACTIVE}")).status_code == 200
    # A household credential is a header of its own; never asked.
    async with _core(cross_site=True, **{HEADER: DEVICE_TOKEN}) as c:
        assert (await c.post(f"/v1/admin/music/resume/{ACTIVE}")).status_code == 200


async def test_r2_the_dashboard_path_through_the_web_hop_still_works(
    rooms, claimed, transport, no_player_starts, fake_bans, monkeypatch
) -> None:
    """The web hop forwards the dashboard's X-Requested-With, so a guest's
    skip through :6369 reaches the real core gate and passes."""
    import web.backend.api.music as music_api

    async def _to_core(path, body=None, timeout=30.0, headers=None):
        async with AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                               base_url="http://test", headers=headers or {}) as c:
            r = await c.post(path, json=body)
        return r.status_code, r.json()

    monkeypatch.setattr(music_api, "post_admin", _to_core)
    async with _web() as c:
        r = await c.post(f"/api/music/skip/{ACTIVE}")
    assert r.status_code == 200, r.text
    assert [room for _t, room in transport] == [ACTIVE]


async def test_r1_a_junk_query_token_on_the_web_read_gets_the_guest_view(
    monkeypatch, rooms, claimed, fake_bans
) -> None:
    """Finding 2, the reviewer's exact request: no credential but a junk
    ?device_token=. The web hop doesn't forward the query, the core
    answers a guest and says so, and the web shows the guest view: no
    household device name, id or time."""
    db = FakeWebDb()
    stub = rooms[ACTIVE]
    first = int(stub._queue[0]["id"])
    db.provenance = [(first, "Kamron's Pixel", "android-kamron-1234", "2026-10-11T10:00:00Z")]
    monkeypatch.setattr(queue_api, "session_scope", db.scope())
    monkeypatch.setattr(queue_api, "get_cached_snapshot", lambda: {"guest_queue_editing": True})
    sent: list[dict] = []

    async def _real_core_get(path, timeout=10.0, headers=None):
        sent.append(dict(headers or {}))
        async with AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                               base_url="http://test", headers=headers or {}) as c:
            r = await c.get(path)
        return r.status_code, r.json()

    monkeypatch.setattr(queue_api, "get_admin", _real_core_get)
    async with _web() as c:
        plain = await c.get(f"/api/music/queue/{ACTIVE}")
        junk = await c.get(f"/api/music/queue/{ACTIVE}?device_token=nonsense")
    for r in (plain, junk):
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["guest"] is True and body["admin"] is False
        assert body["items"][0]["added_by"] is None
        assert body["items"][0]["added_by_device_id"] is None
        assert body["items"][0]["added_at"] is None
    assert not {k.lower() for k in sent[-1]} & set(_CREDENTIAL_HEADERS)


async def test_r3_a_guest_call_is_capped(rooms, claimed, core_db, no_player_starts, monkeypatch) -> None:
    """Finding 4 (the orchestrator's defaults): at most 50 tracks per add,
    no add past 500 entries, one song per remove. A household caller is
    not capped."""
    stub = rooms[ACTIVE]
    async with _core() as c:
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/add",
                         json={"track_ids": list(range(1, 52))})
        assert r.status_code == 400, r.text
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/add",
                         json={"track_ids": list(range(1, 51))})
        assert r.status_code == 200, r.text
        assert len(_ids(stub)) == 3 + 50
        monkeypatch.setattr(guest_queue, "GUEST_QUEUE_MAX_ENTRIES", 60)
        monkeypatch.setattr(core_main, "GUEST_QUEUE_MAX_ENTRIES", 60)
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/add",
                         json={"track_ids": list(range(1, 9))})
        assert r.status_code == 409, r.text
        assert len(_ids(stub)) == 53
        current = int((await stub.current_song())["id"])
        others = [i for i in _ids(stub) if i != current]
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/remove", json={"song_ids": others[:2]})
        assert r.status_code == 400, r.text
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/remove", json={"song_ids": others[:1]})
        assert r.status_code == 200, r.text
    async with _core(**{HEADER: DEVICE_TOKEN}) as c:
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/add",
                         json={"track_ids": list(range(1, 101))})
        assert r.status_code == 200, r.text
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/remove", json={"song_ids": others[1:20]})
        assert r.status_code == 200, r.text


async def test_r6_a_guest_probing_a_device_id_learns_nothing(web_side) -> None:
    """Finding 3, the reviewer's request: a guest claiming a blocked
    household device's id, for any room name, gets the generic refusal —
    not the device's name or the admin's note."""
    core, db = web_side
    db.devices["android-kitchen"] = "Kitchen iPad"
    db.block = (1, "android-kitchen", "Kitchen iPad", None, "kids at bedtime")
    async with _web() as c:
        r = await c.post("/api/music/queue/any-room-at-all/add",
                         json={"track_ids": [1], "device_id": "android-kitchen"})
    assert r.status_code == 403
    assert r.json()["detail"] == queue_api.GUEST_BLOCKED_DETAIL
    assert "Kitchen iPad" not in r.text and "bedtime" not in r.text
    assert core.calls == []
    # A household caller with that id still gets the block's own message.
    async with _web(**{HEADER: DEVICE_TOKEN}) as c:
        r = await c.post("/api/music/queue/any-room-at-all/add",
                         json={"track_ids": [1], "device_id": "android-kitchen"})
    assert "kids at bedtime" in r.json()["detail"]


async def test_guest_reads_have_their_own_bucket(rooms, claimed, monkeypatch) -> None:
    """Finding 5: a guest's reads are throttled per address on a larger
    bucket of their own; a household read never is."""
    monkeypatch.setattr(admin_auth.GUEST_READ_LIMITER, "max_per_window", 2)
    async with _core() as c:
        for _ in range(2):
            assert (await c.get(f"/v1/admin/music/queue/{ACTIVE}")).status_code == 200
        r = await c.get(f"/v1/admin/music/queue/{ACTIVE}")
        assert r.status_code == 429 and r.headers.get("retry-after")
        # The edit budget is separate.
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/move",
                         json={"song_id": _ids(rooms[ACTIVE])[-1], "to_position": 1})
        assert r.status_code == 200, r.text
    async with _core(**{HEADER: DEVICE_TOKEN}) as c:
        for _ in range(3):
            assert (await c.get(f"/v1/admin/music/queue/{ACTIVE}")).status_code == 200


async def test_guest_reads_reuse_the_activity_for_a_second(rooms, monkeypatch) -> None:
    """Finding 5: a read reuses a room's activity for ~1 s; an action
    always looks again."""
    asked = []
    stub = rooms[ACTIVE]
    real_state = stub.state

    async def counting_state():
        asked.append(1)
        return await real_state()

    monkeypatch.setattr(stub, "state", counting_state)
    await guest_queue.room_activity_for_read(ACTIVE)
    await guest_queue.room_activity_for_read(ACTIVE)
    assert len(asked) == 1
    await guest_queue.room_activity(ACTIVE)
    assert len(asked) == 2
    monkeypatch.setattr(guest_queue, "ACTIVITY_READ_CACHE_SEC", 0.0)
    await guest_queue.room_activity_for_read(ACTIVE)
    assert len(asked) == 3


def test_the_guests_seen_pruner_runs_on_a_timer() -> None:
    """Finding 7: retention does not wait for the next guest."""
    from domovoi.workers.guests_seen_pruner import GuestsSeenPruner

    src = (Path(core_main.__file__)).read_text(encoding="utf-8")
    assert 'WORKERS.add_worker(GuestsSeenPruner(), owner="core")' in src
    assert GuestsSeenPruner.stub_suppressed is False
    assert settings.guests_seen_pruner_interval_sec <= 3600


@requires_db
async def test_db_guests_seen_is_pruned_and_capped_per_address(clean_queue_tables) -> None:
    """Finding 7, on the real table: a script rotating ids from one address
    keeps GUESTS_SEEN_PER_SOURCE rows; the pruner drops a row 31 days old;
    another address's guest is untouched."""
    from sqlalchemy import text as sql

    from domovoi.db.session import engine

    for i in range(guest_bans.GUESTS_SEEN_PER_SOURCE + 5):
        await _REAL_RECORD_GUEST(device_id=f"browser-rot{i}", source="192.168.1.66",
                                 user_agent="script", room_id=ACTIVE, action="skip")
    await _REAL_RECORD_GUEST(device_id="browser-friend", source="192.168.1.20",
                             user_agent="Phone", room_id=ACTIVE, action="queue add")
    async with engine.begin() as conn:
        per = dict((await conn.execute(sql(
            "SELECT source, count(*) FROM guests_seen GROUP BY source"))).all())
        await conn.execute(sql("UPDATE guests_seen SET last_seen = now() - interval '31 days' "
                               "WHERE device_id = 'browser-friend'"))
    assert per == {"192.168.1.66": guest_bans.GUESTS_SEEN_PER_SOURCE, "192.168.1.20": 1}
    assert await guest_bans.prune_guests_seen() == 1
    async with engine.begin() as conn:
        left = (await conn.execute(sql("SELECT count(*) FROM guests_seen"))).scalar_one()
    assert left == guest_bans.GUESTS_SEEN_PER_SOURCE
