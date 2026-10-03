"""The rooms' music players stay off the internet under ``never``.

A room's MPD container fetches a queued stream URL by itself, out of sight
of every in-process gate, and resumes its queue (state file + ``restart:
unless-stopped``) after any container restart. Two closures, both checked
here DB-free:

* nothing new goes in: ``sdk.playback.play_url`` refuses an internet URL
  with a spoken reply before MPD is asked, and the MPD client's
  ``play_url`` / ``prepare_url`` refuse it too (the backstop);
* nothing old comes back: ``domovoi.mpd_internet`` stops a room that plays
  one and deletes internet entries from every queue — the
  ``internet_access`` reapply hook, and a background step at core start.

Unanswered (and always / sometimes) is today's behaviour: nothing refused,
nothing removed.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from domovoi import egress, mpd_internet, reapply
from domovoi.clients import mpd as mpd_mod
from domovoi.now_playing import NOW_PLAYING
from domovoi.sdk import build_sdk

INTERNET = "https://kexp.streamguys1.com/kexp160.aac"
LAN = "http://192.168.1.50:8000/fm.mp3"


@pytest.fixture(autouse=True)
def _clean():
    mpd_mod._clients.clear()
    NOW_PLAYING.clear("kitchen")
    yield
    mpd_mod._clients.clear()
    NOW_PLAYING.clear("kitchen")


@pytest.fixture
def sdk():
    s = build_sdk("testplug", version="0.1.0")
    s.now_playing.register_source("testplug")
    yield s
    s.teardown()


# ─── sdk.playback.play_url ────────────────────────────────────────────────


class _NoMPD:
    """A room client that must not be asked."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def prepare_url(self, url, **kw):  # pragma: no cover - asserted not called
        self.calls.append(url)
        return True


@pytest.mark.asyncio
async def test_play_url_refuses_an_internet_stream_under_never(sdk) -> None:
    room = _NoMPD()
    mpd_mod._clients["kitchen"] = room   # type: ignore[assignment]
    with egress.override_policy("never"):
        r = await sdk.playback.play_url(
            "kitchen", INTERNET, title="KEXP", source="testplug", record_play=False,
        )
    assert r.music_action is None
    assert r.data["status"] == "internet_off" and r.data["ok"] is False
    assert r.text == "I'm set to stay off the internet, so I can't play KEXP."
    assert room.calls == []                       # MPD never saw the URL
    assert NOW_PLAYING.get("kitchen") is None     # nothing stamped


@pytest.mark.asyncio
async def test_play_url_still_plays_a_house_stream_under_never(sdk) -> None:
    with egress.override_policy("never"):
        r = await sdk.playback.play_url(
            "kitchen", LAN, title="FM box", source="testplug", record_play=False,
        )
    assert r.music_action == "start"
    assert mpd_mod._clients["kitchen"]._song["file"] == LAN


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["", "always", "sometimes"])
async def test_play_url_is_unchanged_on_other_answers(sdk, answer) -> None:
    with egress.override_policy(answer):
        r = await sdk.playback.play_url(
            "kitchen", INTERNET, title="KEXP", source="testplug", record_play=False,
        )
    assert r.music_action == "start"
    assert r.data.get("status") is None


# ─── the MPD client backstop ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_stub_client_refuses_an_internet_url_under_never() -> None:
    stub = mpd_mod.MPDStubClient()
    with egress.override_policy("never"):
        assert await stub.prepare_url(INTERNET, title="x") is False
        assert await stub.play_url(INTERNET, title="x") is False
        assert stub._song is None
        assert await stub.prepare_url(LAN, title="x") is True


@pytest.mark.asyncio
async def test_the_real_client_refuses_before_touching_the_queue() -> None:
    class _Conn:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def clear(self):
            self.calls.append("clear")

        async def addid(self, url):
            self.calls.append(f"addid {url}")
            return 7

        async def addtagid(self, *a):
            self.calls.append("addtagid")

        async def play(self):
            self.calls.append("play")

        async def pause(self, *_a):
            self.calls.append("pause")

    client = mpd_mod.RealMPDClient("127.0.0.1", 6650)
    conn = _Conn()
    with egress.override_policy("never"):
        ok = await client._queue_url(conn, INTERNET, title="KEXP", artist=None, start=False)
    assert ok is False and conn.calls == []       # the queue is left as it was
    with egress.override_policy(""):
        ok = await client._queue_url(conn, INTERNET, title="KEXP", artist=None, start=False)
    assert ok is True and conn.calls[:2] == ["clear", f"addid {INTERNET}"]


# ─── purging the queues ───────────────────────────────────────────────────


class _Room:
    """A room's MPD as the purge sees it."""

    def __init__(self, entries: list[str], current: str | None, *, unreachable: int = 0) -> None:
        self.entries = [{"file": f, "id": i + 1, "pos": i} for i, f in enumerate(entries)]
        self.current = current
        self.stopped = 0
        self.removed: list[int] = []
        self.unreachable = unreachable

    async def queue_list(self) -> list[dict[str, Any]]:
        if self.unreachable:
            self.unreachable -= 1
            raise ConnectionRefusedError("container still starting")
        return [dict(e) for e in self.entries]

    async def current_song(self):
        return {"file": self.current} if self.current else None

    async def stop(self) -> None:
        self.stopped += 1

    async def queue_remove(self, ids: list[int]) -> list[int]:
        self.removed.extend(ids)
        self.entries = [e for e in self.entries if e["id"] not in ids]
        return list(ids)


@pytest.mark.asyncio
async def test_never_stops_an_internet_station_and_keeps_the_library() -> None:
    room = _Room(["Artist/Album/01 Song.mp3", INTERNET, LAN], current=INTERNET)
    NOW_PLAYING.register_source("mpd-internet-test", owner="mpd-internet-test")
    try:
        NOW_PLAYING.stamp("kitchen", "mpd-internet-test", {"stream_url": INTERNET, "title": "KEXP"})
        with egress.override_policy("never"):
            removed = await mpd_internet.purge_room("kitchen", room)
    finally:
        NOW_PLAYING.unregister_owner("mpd-internet-test")
    assert removed == 1
    assert room.stopped == 1
    assert [e["file"] for e in room.entries] == ["Artist/Album/01 Song.mp3", LAN]
    assert NOW_PLAYING.get("kitchen") is None


@pytest.mark.asyncio
async def test_a_queued_station_is_removed_without_stopping_the_song_that_plays() -> None:
    room = _Room(["Artist/Album/01 Song.mp3", INTERNET], current="Artist/Album/01 Song.mp3")
    with egress.override_policy("never"):
        assert await mpd_internet.purge_room("kitchen", room) == 1
    assert room.stopped == 0
    assert [e["file"] for e in room.entries] == ["Artist/Album/01 Song.mp3"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["", "always", "sometimes"])
async def test_other_answers_remove_nothing(answer) -> None:
    room = _Room([INTERNET], current=INTERNET)
    with egress.override_policy(answer):
        assert await mpd_internet.purge_room("kitchen", room) == 0
        assert await mpd_internet.purge_internet_streams() == {}
    assert room.stopped == 0 and room.removed == []


@pytest.mark.asyncio
async def test_every_room_is_purged_and_a_starting_one_is_asked_again(monkeypatch) -> None:
    ready = _Room([INTERNET], current=None)
    starting = _Room([INTERNET, "a.mp3"], current=INTERNET, unreachable=2)
    monkeypatch.setattr(mpd_internet, "RETRY_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(mpd_mod, "iter_mpd_clients", lambda: [("kitchen", ready), ("office", starting)])
    with egress.override_policy("never"):
        out = await mpd_internet.purge_internet_streams(retry_sec=5)
    assert out == {"kitchen": 1, "office": 1}
    assert starting.stopped == 1 and [e["file"] for e in starting.entries] == ["a.mp3"]


@pytest.mark.asyncio
async def test_an_unreachable_room_is_logged_not_raised(monkeypatch, caplog) -> None:
    dead = _Room([INTERNET], current=None, unreachable=99)
    monkeypatch.setattr(mpd_mod, "iter_mpd_clients", lambda: [("kitchen", dead)])
    with egress.override_policy("never"):
        out = await mpd_internet.purge_internet_streams(retry_sec=0)
    assert out == {}
    assert any("couldn't check room=kitchen" in r.getMessage() for r in caplog.records)


def test_entries_are_judged_by_their_host_without_dns() -> None:
    with egress.override_policy("never"):
        assert mpd_internet.is_internet_entry(INTERNET)
        assert mpd_internet.is_internet_entry("http://192.0.2.10/x.mp3")   # TEST-NET counts as public
        assert not mpd_internet.is_internet_entry(LAN)
        assert not mpd_internet.is_internet_entry("http://nas.local/radio.mp3")
        assert not mpd_internet.is_internet_entry("Artist/Album/01 Song.mp3")
        assert not mpd_internet.is_internet_entry(None)
    with egress.override_policy("always"):
        assert not mpd_internet.is_internet_entry(INTERNET)


# ─── when it runs ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_saving_never_runs_the_purge_in_the_background(monkeypatch) -> None:
    from domovoi import main

    seen: list[float] = []

    async def fake_purge(*, retry_sec: float = 0.0):
        seen.append(retry_sec)
        return {}

    monkeypatch.setattr(mpd_internet, "purge_internet_streams", fake_purge)
    # The search helper's hook runs on the same save: keep docker out of it.
    monkeypatch.setenv("DOMOVOI_MANAGE_SEARXNG", "0")
    main._register_core_reapply_hooks()
    assert reapply._HOOKS["internet_access"]["mpd_internet"] is mpd_internet.schedule_purge
    with egress.override_policy("always"):
        reapply.run_for(["internet_access"])
        await asyncio.sleep(0)
    assert seen == []                              # only never purges
    with egress.override_policy("never"):
        ran = reapply.run_for(["internet_access"])
        for _ in range(3):
            await asyncio.sleep(0)
    assert "internet_access:mpd_internet" in ran
    assert seen == [0.0]
    assert not mpd_internet._PENDING


@pytest.mark.asyncio
async def test_the_boot_step_retries_and_never_runs_on_other_answers(monkeypatch) -> None:
    seen: list[float] = []

    async def fake_purge(*, retry_sec: float = 0.0):
        seen.append(retry_sec)
        return {}

    monkeypatch.setattr(mpd_internet, "purge_internet_streams", fake_purge)
    with egress.override_policy(""):
        assert mpd_internet.schedule_boot_purge() is None
    with egress.override_policy("never"):
        task = mpd_internet.schedule_boot_purge()
        await task
    assert seen == [mpd_internet.BOOT_RETRY_SEC]


def test_the_core_start_purges_after_warming_the_rooms() -> None:
    """The boot step sits right after warm_known_rooms (which fills the
    room → port map the purge walks) and is scheduled, never awaited."""
    from domovoi import main

    src = inspect.getsource(main.lifespan)
    warm = src.index("await warm_known_rooms()")
    boot = src.index("mpd_internet.schedule_boot_purge()")
    assert warm < boot
    assert "await mpd_internet" not in src
