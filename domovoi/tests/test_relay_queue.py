"""L20 (review 2026-11-23): no raw internet URL sits in a room's music queue.

domovoi/relay_queue.py: the queue check at start (rewrite to a fresh relay
URL, or remove), the hand-off to another room (a fresh room-bound token; a
raw URL refused), and the backstop under every path that adds to a
player's queue. The relay's renewal of a queued entry is in
test_player_relay.py. DB-free: the play history lookup is replaced.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from domovoi import player_relay, relay_queue
from domovoi.clients import mpd as mpd_mod
from domovoi.player_route import PlayerRoute
from domovoi.tests.test_mpd_protocol import FakeMPD

STATION = "https://radio.example/live.aac"
SONG = "https://files.example/song.mp3"
ROUTE = PlayerRoute(host="172.17.0.1", peer="172.17.0.5")
OTHER_ROUTE = PlayerRoute(host="172.17.0.1", peer="172.17.0.6")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    player_relay.reset_for_tests()
    routes = {"kitchen": ROUTE, "office": OTHER_ROUTE}

    async def route(room_id):
        return routes.get(room_id)

    monkeypatch.setattr(player_relay, "_route_for", route)

    async def vet(url, **kw):
        return None

    from domovoi import net_safety

    monkeypatch.setattr(net_safety, "avet_outbound_url", vet)
    history: dict[str, str] = {}

    async def kind(room_id, url):
        return history.get(url)

    monkeypatch.setattr(relay_queue, "_history_kind", kind)
    yield history
    player_relay.reset_for_tests()


class _Room:
    """A room's own player: a queue with songids, the current song, a state."""

    def __init__(self, files: list[str], *, current: int | None = None, state: str = "stop") -> None:
        self.queue = [{"file": f, "id": i + 1, "title": f"t{i}"} for i, f in enumerate(files)]
        self.next_id = len(files) + 1
        self.current = current
        self.st = state
        self.played: list[tuple[int, bool]] = []
        self.stopped = 0

    async def queue_list(self) -> list[dict[str, Any]]:
        return [dict(e, pos=i) for i, e in enumerate(self.queue)]

    async def current_song(self):
        return next((dict(e) for e in self.queue if e["id"] == self.current), None)

    async def state(self) -> str:
        return self.st

    async def queue_add_files(self, uris, *, position=None):
        out = []
        for uri in uris:
            if relay_queue.refuses_queue_uri(uri):
                out.append(None)
                continue
            e = {"file": uri, "id": self.next_id}
            self.next_id += 1
            self.queue.insert(len(self.queue) if position is None else position, e)
            out.append(dict(e))
        return out

    async def queue_tag(self, song_id, *, title=None, artist=None):
        for e in self.queue:
            if e["id"] == song_id and title:
                e["title"] = title

    async def play_id(self, song_id, *, paused=False):
        self.current = song_id
        self.st = "pause" if paused else "play"
        self.played.append((song_id, paused))
        return True

    async def queue_remove(self, ids):
        gone = [e["id"] for e in self.queue if e["id"] in ids]
        self.queue = [e for e in self.queue if e["id"] not in ids]
        return gone

    async def stop(self):
        self.stopped += 1
        self.st = "stop"

    def files(self) -> list[str]:
        return [e["file"] for e in self.queue]


# ─── what counts as an internet entry ────────────────────────────────────


def test_what_is_an_internet_entry() -> None:
    relayed = player_relay.mint("kitchen", STATION, ROUTE, kind="radio")
    assert relay_queue.is_internet_uri(STATION)
    assert relay_queue.is_internet_uri("rtsp://radio.example/x")
    assert not relay_queue.is_internet_uri("Artist/Album/01 Song.mp3")
    assert not relay_queue.is_internet_uri("http://192.168.1.20:8070/fm.mp3")   # the tuner, at home
    assert not relay_queue.is_internet_uri(relayed)
    # A relay-looking path on a server out there is not one the core minted.
    assert relay_queue.is_internet_uri("https://evil.example/v1/player-relay/" + "A" * 43)


# ─── the queue check at start ────────────────────────────────────────────


async def test_the_start_scan_rewrites_a_known_station_and_removes_the_unknown(_fresh, caplog) -> None:
    _fresh[STATION] = "radio"                       # the play history knows it
    room = _Room(["Artist/a.mp3", STATION, "https://unknown.example/x.mp3",
                  "http://192.168.1.20:8070/fm.mp3"], current=2, state="pause")
    with caplog.at_level(logging.INFO):
        out = await relay_queue.scan_room("kitchen", room)
    assert out == {"rewritten": 1, "removed": 1}
    files = room.files()
    assert files[0] == "Artist/a.mp3" and files[2] == "http://192.168.1.20:8070/fm.mp3"
    assert player_relay.target_for(files[1]) == STATION
    rec = player_relay.record_for(files[1])
    assert rec.room_id == "kitchen" and rec.kind == "radio"
    assert files[1].startswith(ROUTE.url("/v1/player-relay/"))
    assert room.queue[1]["title"] == "t1"           # the station's name kept
    assert room.current == room.queue[1]["id"] and room.st == "pause"   # still where it was
    assert not any(relay_queue.is_internet_uri(f) for f in files)
    text = caplog.text
    assert "radio.example" not in text and "unknown.example" not in text   # no URL in a log line
    assert "url#" in text


async def test_the_start_scan_removes_a_dead_relay_and_keeps_a_good_one() -> None:
    good = player_relay.mint("kitchen", STATION, ROUTE, kind="radio")
    player_relay.revoke_room("kitchen")             # spent; the record keeps it playable
    dead = ROUTE.url("/v1/player-relay/" + "B" * 43)
    room = _Room([good, dead, "x.mp3"])
    out = await relay_queue.scan_room("kitchen", room)
    assert out == {"rewritten": 0, "removed": 1}
    assert room.files() == [good, "x.mp3"]


async def test_another_rooms_relay_is_reminted_for_this_room_and_stale_records_go() -> None:
    theirs = player_relay.mint("office", STATION, OTHER_ROUTE, kind="radio")
    gone = player_relay.mint("kitchen", SONG, ROUTE, kind="file")   # no longer queued
    room = _Room([theirs])
    out = await relay_queue.scan_room("kitchen", room)
    assert out["rewritten"] == 1
    fresh = room.files()[0]
    assert fresh != theirs and player_relay.record_for(fresh).room_id == "kitchen"
    assert player_relay.record_for(gone) is None


async def test_a_raw_entry_with_no_route_is_removed_and_a_playing_one_stops(monkeypatch, _fresh) -> None:
    async def none(room_id):
        return None

    monkeypatch.setattr(player_relay, "_route_for", none)
    _fresh[STATION] = "radio"
    room = _Room([STATION], current=1, state="play")
    out = await relay_queue.scan_room("kitchen", room)
    assert out == {"rewritten": 0, "removed": 1}
    assert room.files() == [] and room.stopped == 1


async def test_scan_all_checks_every_room_and_retries_one_still_starting(monkeypatch, _fresh) -> None:
    _fresh[STATION] = "file"

    class _Starting(_Room):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.fails = 1

        async def queue_list(self):
            if self.fails:
                self.fails -= 1
                raise ConnectionRefusedError("starting")
            return await super().queue_list()

    kitchen = _Starting([STATION])
    monkeypatch.setattr(relay_queue, "RETRY_INTERVAL_SEC", 0.0)
    monkeypatch.setattr(mpd_mod, "_clients", {"kitchen": kitchen})
    await relay_queue.scan_all(retry_sec=1.0)
    assert player_relay.target_for(kitchen.files()[0]) == STATION


# ─── the hand-off ────────────────────────────────────────────────────────


async def test_a_hand_off_gets_a_fresh_relay_url_bound_to_the_new_room() -> None:
    old = player_relay.mint("kitchen", STATION, ROUTE, kind="radio")
    uris, start_ok = await relay_queue.carry_over("kitchen", "office", ["a.mp3", old], 1)
    assert start_ok and uris[0] == "a.mp3"
    fresh = uris[1]
    assert fresh != old and player_relay.token_of(fresh) != player_relay.token_of(old)
    rec = player_relay.record_for(fresh)
    assert rec.room_id == "office" and rec.url == STATION and rec.kind == "radio"
    grant = player_relay._GRANTS[player_relay.token_of(fresh)]
    assert grant.room_id == "office" and grant.route is OTHER_ROUTE


async def test_a_raw_internet_url_in_a_hand_off_is_refused() -> None:
    uris, start_ok = await relay_queue.carry_over("kitchen", "office", ["a.mp3", STATION], 1)
    assert uris == ["a.mp3", ""] and start_ok is False
    uris, start_ok = await relay_queue.carry_over("kitchen", "office", [STATION, "a.mp3"], 1)
    assert uris == ["", "a.mp3"] and start_ok is True


async def test_the_group_hand_off_moves_a_station_with_a_new_token(monkeypatch) -> None:
    from domovoi import music_groups

    old_url = player_relay.mint("kitchen", STATION, ROUTE, kind="radio")
    src = mpd_mod.MPDStubClient()
    src._queue = src._assign_ids([{"file": old_url, "title": "KEXP"}])
    src._song, src._state = src._queue[0], "play"
    dst = mpd_mod.MPDStubClient()
    monkeypatch.setitem(mpd_mod._clients, "kitchen", src)
    monkeypatch.setitem(mpd_mod._clients, "office", dst)
    monkeypatch.setattr(music_groups, "_paused_rooms", lambda app: None)
    handed = await music_groups._hand_off(None, "kitchen", "office")
    assert handed.ok
    [entry] = dst._queue
    assert entry["file"] != old_url
    assert player_relay.record_for(entry["file"]).room_id == "office"
    assert player_relay.target_for(entry["file"]) == STATION


async def test_a_group_hand_off_of_only_a_raw_url_is_refused(monkeypatch) -> None:
    from domovoi import music_groups

    src = mpd_mod.MPDStubClient()
    src._queue = src._assign_ids([{"file": STATION}])
    src._song, src._state = src._queue[0], "play"
    dst = mpd_mod.MPDStubClient()
    monkeypatch.setitem(mpd_mod._clients, "kitchen", src)
    monkeypatch.setitem(mpd_mod._clients, "office", dst)
    monkeypatch.setattr(music_groups, "_paused_rooms", lambda app: None)
    handed = await music_groups._hand_off(None, "kitchen", "office")
    assert handed.ok is False and dst._queue == [] and src._state == "play"


# ─── every adder: nothing raw reaches a player ───────────────────────────


def _adds(fake: FakeMPD) -> list[str]:
    return [line for line in fake.lines if line.split(" ", 1)[0] in ("add", "addid")]


async def test_no_adder_of_the_real_client_sends_a_raw_internet_url() -> None:
    relayed = player_relay.mint("kitchen", STATION, ROUTE, kind="radio")
    replies = {"addid": "Id: 9\n", "playlistinfo": "file: a.mp3\nPos: 0\nId: 1\n",
               "search": f"file: {SONG}\nTitle: C\n"}
    async with FakeMPD(replies) as fake:
        c = mpd_mod.RealMPDClient("127.0.0.1", fake.port)
        assert await c.play_url(STATION, title="x") is False
        assert await c.prepare_url(STATION, title="x") is False
        assert await c.prepare_files([STATION, "a.mp3"]) != []
        assert (await c.queue_add_files([STATION, "a.mp3"]))[0] is None
        assert await c.queue_add_tracks([{"title": "C"}]) == []          # a search can't smuggle one in
        await c.prepare_tracks([{"title": "C"}])
        assert await c.prepare_url(relayed, title="KEXP") is True        # the relay URL goes in
        assert await c.play_url("http://192.168.1.20:8070/fm.mp3") is True   # the tuner, at home
    sent = _adds(fake)
    assert not any("example" in line and "player-relay" not in line for line in sent)
    assert any("player-relay" in line for line in sent)


async def test_the_protocol_refuses_a_raw_add_whoever_calls_it() -> None:
    from domovoi.clients.mpd_protocol import CommandError, MPDConnection

    async with FakeMPD({"addid": "Id: 3\n"}) as fake:
        c = MPDConnection()
        await c.connect("127.0.0.1", fake.port)
        for cmd in (c.add, c.addid):
            with pytest.raises(CommandError):
                await cmd(STATION)
        assert await c.addid("a.mp3") == "3"
        c.disconnect()
    assert fake.lines == ['addid "a.mp3"']


async def test_the_stub_refuses_raw_adds_too() -> None:
    stub = mpd_mod.MPDStubClient()
    assert await stub.play_url(STATION) is False
    assert await stub.prepare_files([STATION]) == []
    assert await stub.queue_add_files([STATION]) == [None]


async def test_sdk_play_url_puts_a_raw_url_through_the_relay(monkeypatch) -> None:
    from domovoi.sdk import build_sdk

    sdk = build_sdk("testplug", version="0.1.0")
    sdk.now_playing.register_source("testplug")
    stub = mpd_mod.MPDStubClient()
    monkeypatch.setitem(mpd_mod._clients, "kitchen", stub)
    try:
        r = await sdk.playback.play_url("kitchen", SONG, title="Song", source="testplug",
                                        record_play=False)
    finally:
        sdk.teardown()
    assert r.music_action == "start"
    held = stub._song["file"]
    assert held != SONG and player_relay.target_for(held) == SONG
    assert player_relay.record_for(held).kind == "file"


# ─── L23: open reads never carry a relay token ───────────────────────────


def _token(url: str) -> str:
    return player_relay.token_of(url) or ""


def test_redaction_hides_the_token_and_keeps_entries_comparable() -> None:
    a = player_relay.mint("kitchen", STATION, ROUTE, kind="radio")
    b = player_relay.mint("office", STATION, OTHER_ROUTE, kind="radio")
    ra, rb = player_relay.redact(a), player_relay.redact(b)
    assert _token(a) not in ra and "/v1/player-relay/~" in ra
    assert ra == player_relay.redact(a) and ra != rb
    assert player_relay.redact("Artist/a.mp3") == "Artist/a.mp3"
    assert player_relay.redact(None) is None


def test_the_open_snapshot_never_carries_a_relay_token() -> None:
    from domovoi.now_playing import NOW_PLAYING

    url = player_relay.mint("kitchen", STATION, ROUTE, kind="radio")
    NOW_PLAYING.stamp("kitchen", "library", {"stream_url": url, "title": "KEXP"})
    try:
        data = NOW_PLAYING.snapshot()["kitchen"]["data"]
    finally:
        NOW_PLAYING.clear("kitchen")
    assert _token(url) not in str(data)
    assert data["stream_url"] == player_relay.redact(url) and data["title"] == "KEXP"


def test_the_core_queue_read_never_carries_a_relay_token() -> None:
    from domovoi import main as core_main

    url = player_relay.mint("kitchen", STATION, ROUTE, kind="radio")
    row = core_main._queue_entry_dict({"id": 3, "pos": 0, "file": url, "title": "KEXP"})
    assert _token(url) not in str(row) and row["file"] == player_relay.redact(url)
    assert core_main._queue_entry_dict({"id": 4, "file": "A/b.mp3"})["file"] == "A/b.mp3"


async def test_the_dashboards_open_now_playing_never_carries_a_relay_token(monkeypatch) -> None:
    from web.backend.api import music as music_api
    from web.backend.api import music_groups as groups_api

    url = player_relay.mint("kitchen", STATION, ROUTE, kind="radio")

    async def read(host, port, timeout=1.5):
        return "play", {"file": url, "Title": "KEXP", "Id": "5"}, 3.0

    async def groups():
        return {}

    async def row(song):
        return False, None

    async def no_provenance(cards):
        return None

    snapshot = {"now_playing": {"kitchen": {"source": "radio", "data": {
        "stream_url": player_relay.redact(url), "url": "https://kexp.example/"}}}}
    monkeypatch.setattr(music_api, "_read_mpd", read)
    monkeypatch.setattr(groups_api, "live_groups", groups)
    monkeypatch.setattr(music_api, "_lookup_library_row", row)
    monkeypatch.setattr(music_api, "get_cached_snapshot", lambda: snapshot)
    card = await music_api._now_playing_for(("kitchen", 6650, 8050))
    assert _token(url) not in card.model_dump_json()
    assert card.song.file == player_relay.redact(url)
    assert card.source == "radio"                   # the stamp still matches the entry
