"""Photo and video results on a room's screen (domovoi/room_media.py, the
``room_display`` frame's ``media``, the kiosk page; media understanding
slice 6).

The frame's ``media`` part and the shared fixtures (a 1.4.0 client that
reads only its keys sees nothing new); only a room with a screen (a room
display or a video satellite's kiosk) keeps results; a guest never puts a
person's photos on a screen; results clear after their time, with newer
results, and with the next frame sent for another reason; the thumbnail
token route; the web backend's digest push and the kiosk's read; and the
kiosk page drawing them as text.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import household_time, room_media
from domovoi import room_display as rd
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.media_understanding import ask
from domovoi.media_understanding.search import Query
from domovoi.models import Context
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_media_ask import NOW, _person_with_face, _set, _utc, people_cleanup  # noqa: F401
from domovoi.tests.test_media_photos import _seed_item
from domovoi.tests.test_media_understanding import house  # noqa: F401
from domovoi.tests.test_room_display import FakeSession, _frame

REPO = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((REPO / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
FRAMES = FIXTURES["room_display_frames"]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")


@pytest.fixture(autouse=True)
def _clean():
    room_media.reset()
    yield
    room_media.reset()


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _sess(sid: str, room: str, *, screen: bool) -> SimpleNamespace:
    pokes: list[int] = []
    feed = SimpleNamespace(poke=lambda: pokes.append(1)) if screen else None
    return SimpleNamespace(satellite_id=sid, room_id=room, room_display=feed, pokes=pokes)


def _house(*sessions: SimpleNamespace, video: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(
        active_satellites={s.satellite_id: s for s in sessions}, active_sessions={},
        satellite_sat_type={sid: "video" for sid in video}))


def _card(i: int, kind: str = "image", *, at_s: float | None = None, caption: str | None = "A beach.") -> dict:
    return {"id": i, "kind": kind, "name": f"f{i}", "library_id": "core:pictures", "path": f"f{i}.jpg",
            "thumb": f"/api/images/thumb?library_id=core%3Apictures&path=f{i}.jpg&size=m",
            "taken_at": "2025-07-14T10:00:00+00:00", "date_from": None, "might_show": caption,
            "tags": [], "text": None, "at_s": at_s}


def _answer(cards: list[dict], *, person: str | None = None, state: str = "found", count: int | None = None):
    return ask.Answer(state, Query(words="beach", kind=None, when=None), person=person, cards=cards,
                      count=len(cards) if count is None else count)


# ─── the frame and the fixtures ──────────────────────────────────────────


def test_the_frame_media_part_matches_the_fixtures_contract():
    clock = Clock()
    app = _house(_sess("kitchen", "kitchen", screen=True))
    cards = [_card(1), _card(2, "video", at_s=42.0, caption=None)] + [_card(i) for i in range(3, 12)]
    shown = room_media.show(app, "kitchen", _answer(cards, count=40), "I found 40 photos.", people_ok=False,
                            clock=clock)
    assert shown is not None
    m = room_media.frame_media("kitchen", clock=clock)
    assert list(m) == FRAMES["media_keys"]
    assert m["id"] == shown.id and m["said"] == "I found 40 photos." and m["count"] == 40
    assert m["expires_in_ms"] == int(room_media.SHOW_SEC * 1000)
    assert len(m["items"]) == room_media.MAX_ITEMS == 6
    for item in m["items"]:
        assert list(item) == FRAMES["media_item_keys"]
        assert re.fullmatch(r"/v1/room-media/[A-Za-z0-9_-]{20,64}", item["thumb"])
    assert m["items"][0] == {**m["items"][0], "kind": "image", "caption": "A beach.", "date": "2025-07-14",
                             "at_s": None}
    assert m["items"][1]["kind"] == "video" and m["items"][1]["at_s"] == 42.0 and m["items"][1]["caption"] is None
    # The kiosk's copy uses the dashboard's own thumbnail route.
    assert room_media.web_media("kitchen", clock=clock)["items"][0]["thumb"] == cards[0]["thumb"]


def test_the_fixtures_are_backward_compatible():
    old_keys = FRAMES["client_keys_1_4_0"]
    before, with_media, cleared = FRAMES["before_media"], FRAMES["with_media"], FRAMES["media_cleared"]
    # A 1.4.0 client reads only its keys: the new frame is the old one to it.
    assert {k: with_media[k] for k in old_keys} == before
    assert {k: cleared[k] for k in old_keys} == before
    assert set(with_media) - set(before) == {"media"} and cleared["media"] is None
    assert list(with_media["media"]) == FRAMES["media_keys"]
    for item in with_media["media"]["items"]:
        assert list(item) == FRAMES["media_item_keys"]
        assert room_media.TOKEN_RE.match(item["thumb"].removeprefix(room_media.THUMB_PATH))
    # The frame the core builds has exactly the old keys plus media.
    assert list(_frame()) == old_keys


async def test_build_frame_carries_media(monkeypatch):
    async def nothing(*_a, **_k):
        return None

    async def no_timers(*_a, **_k):
        return []

    class _S:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    import domovoi.db.session as dbs

    monkeypatch.setattr(dbs, "session_scope", lambda: _S())
    monkeypatch.setattr(rd, "now_playing", nothing)
    monkeypatch.setattr(rd, "timers", no_timers)
    monkeypatch.setattr(rd, "ringing", nothing)
    frame = await rd.build_frame("kitchen", mono_ms=lambda: 5)
    assert frame["media"] is None and list(frame) == FRAMES["client_keys_1_4_0"] + ["media"]
    room_media.show(_house(_sess("kitchen", "kitchen", screen=True)), "kitchen", _answer([_card(1)]), "x",
                    people_ok=True)
    frame = await rd.build_frame("kitchen", mono_ms=lambda: 6)
    assert frame["media"]["items"][0]["caption"] == "A beach."
    assert (await rd.build_frame("garage", mono_ms=lambda: 7))["media"] is None


# ─── only rooms with a screen ────────────────────────────────────────────


def test_only_a_room_with_a_screen_keeps_results():
    kitchen = _sess("kitchen", "kitchen", screen=True)
    garage = _sess("garage", "garage", screen=False)            # a Pi: no screen
    den = _sess("den", "den", screen=False)                     # a video satellite's kiosk
    app = _house(kitchen, garage, den, video=("den",))
    ans = _answer([_card(1)])
    assert room_media.show(app, "garage", ans, "x", people_ok=True) is None
    assert room_media.show(app, "attic", ans, "x", people_ok=True) is None     # nobody there
    assert room_media.current("garage") is None and room_media.frame_media("garage") is None
    assert room_media.show(app, "kitchen", ans, "x", people_ok=True) is not None
    assert kitchen.pokes == [1]                                  # the screen looks now
    assert room_media.show(app, "den", ans, "x", people_ok=True) is not None
    assert set(room_media.snapshot()) == {"kitchen", "den"}


def test_nothing_found_or_a_refusal_shows_nothing():
    app = _house(_sess("kitchen", "kitchen", screen=True))
    for state in ("off", "empty", "none", "people_refused"):
        assert room_media.show(app, "kitchen", _answer([], state=state), "x", people_ok=True) is None
    assert room_media.show(app, "kitchen", _answer([]), "x", people_ok=True) is None
    assert room_media.snapshot() == {}


def test_a_guest_never_puts_a_persons_photos_on_a_screen():
    app = _house(_sess("kitchen", "kitchen", screen=True))
    assert room_media.show(app, "kitchen", _answer([_card(1)], person="Sarah"), "x", people_ok=False) is None
    assert room_media.current("kitchen") is None
    # Words only (no person filter) are fine for anyone.
    assert room_media.show(app, "kitchen", _answer([_card(1)]), "x", people_ok=False) is not None
    assert room_media.show(app, "kitchen", _answer([_card(2)], person="Sarah"), "x", people_ok=True) is not None


# ─── clearing ─────────────────────────────────────────────────────────────


def test_results_clear_after_their_time_and_newer_replace_them():
    clock = Clock()
    app = _house(_sess("kitchen", "kitchen", screen=True))
    first = room_media.show(app, "kitchen", _answer([_card(1)]), "one", people_ok=True, clock=clock)
    second = room_media.show(app, "kitchen", _answer([_card(2)]), "two", people_ok=True, clock=clock)
    assert second.id > first.id and room_media.frame_media("kitchen", clock=clock)["said"] == "two"
    clock.t += room_media.SHOW_SEC - 1
    assert room_media.frame_media("kitchen", clock=clock)["expires_in_ms"] == 1000
    clock.t += 1
    assert room_media.frame_media("kitchen", clock=clock) is None and room_media.snapshot(clock=clock) == {}


def test_the_next_frame_for_another_reason_clears_them():
    feed = rd.RoomDisplayFeed(FakeSession(supports=["room_display"]))
    media = {"id": 9, "said": "x", "count": 1, "more": False, "expires_in_ms": 90000, "items": []}
    first = feed.settle_media({**_frame(0), "media": media})
    assert first["media"] == media
    feed.last_sent = first
    # Only time passing: still shown, and not a change.
    same = feed.settle_media({**_frame(1000), "media": {**media, "expires_in_ms": 89000}})
    assert same["media"]["id"] == 9 and not rd.differs(feed.last_sent, same)
    # A timer is set: that frame goes without the results, and so does every later one.
    timer = [{"id": 1, "kind": "timer", "label": None, "remaining_ms": 5000, "total_ms": 5000}]
    nxt = feed.settle_media({**_frame(2000, timers=timer), "media": media})
    assert nxt["media"] is None and rd.differs(feed.last_sent, nxt)
    feed.last_sent = nxt
    assert feed.settle_media({**_frame(3000, timers=timer), "media": media})["media"] is None
    # Newer results show again.
    assert feed.settle_media({**_frame(4000, timers=timer), "media": {**media, "id": 10}})["media"]["id"] == 10


async def test_the_feed_sends_results_then_clears_them_with_the_next_change():
    s = FakeSession(supports=["room_display"])
    state = {"timers": [], "media": None}

    async def build(_room):
        return {**_frame(int(time.monotonic() * 1000), timers=state["timers"]), "media": state["media"]}

    feed = rd.RoomDisplayFeed(s, builder=build, poll_sec=0.01, min_interval_sec=0.0)
    feed.start()
    await asyncio.sleep(0.05)
    state["media"] = {"id": 3, "said": "x", "count": 1, "more": False, "expires_in_ms": 90000, "items": []}
    feed.poke()
    await asyncio.sleep(0.05)
    state["timers"] = [{"id": 1, "kind": "timer", "label": None, "remaining_ms": 9000, "total_ms": 9000}]
    feed.poke()
    await asyncio.sleep(0.05)
    await feed.stop()
    assert [f["media"] and f["media"]["id"] for f in s.sent] == [None, 3, None]


# ─── the voice handler ───────────────────────────────────────────────────


@requires_db
async def test_the_voice_handler_shows_results_only_on_a_screen_and_never_a_guests_person(house, people_cleanup) -> None:  # noqa: F811,E501
    a = await _seed_item("a.jpg", taken=_utc(2025, 1, 1), caption="Two people on a beach.", tags=["beach"])
    await _seed_item("b.jpg", taken=_utc(2025, 2, 1), caption="A beach.", tags=["beach"])
    pid = await _person_with_face("FT Sarah", a)
    await _set(faces=True)
    kitchen = _sess("kitchen", "kitchen", screen=True)
    app = _house(kitchen, _sess("garage", "garage", screen=False))
    h = HANDLER_BY_NAME["media_search"]

    def match(said):
        return next(rx.match(said) for rx in ask.REQUEST_RES if rx.match(said))

    from domovoi.db.session import session_scope

    with household_time.use_zone(timezone.utc, lambda: NOW):
        async with session_scope() as s:
            # A guest naming a person: refused, nothing on the screen.
            guest = await h._from_match(match("show me photos of ft sarah"), Context(room_id="kitchen", app=app), s)
            assert guest.text == ask.PEOPLE_REFUSED_TEXT and room_media.current("kitchen") is None
            # A room without a screen: said, never kept.
            await h._from_match(match("show me photos of the beach"), Context(room_id="garage", app=app), s)
            assert room_media.current("garage") is None
            # A phone's chat mic: the chat has its own cards.
            await h._from_match(match("show me photos of the beach"),
                                Context(room_id="kitchen", app=app, device_kind="phone"), s)
            assert room_media.current("kitchen") is None
            # A guest asking by words: shown.
            r = await h._from_match(match("show me photos of the beach"), Context(room_id="kitchen", app=app), s)
            shown = room_media.current("kitchen")
            assert shown is not None and shown.said == r.text and len(shown.items) == 2 and not shown.person
            assert kitchen.pokes
            # A recognized voice naming the person: shown, person-filtered.
            await h._from_match(match("show me photos of ft sarah"),
                                Context(room_id="kitchen", app=app, person_id=pid), s)
            shown = room_media.current("kitchen")
            assert shown.person and [i["path"] for i in shown.items] == ["a.jpg"]
    # The spoken reply still carries no caption.
    assert "might_show" not in json.dumps(r.data)


# ─── the thumbnail route ─────────────────────────────────────────────────


async def _get(app, path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path)


async def test_a_thumbnail_token_names_one_item_and_expires(monkeypatch):
    from fastapi.responses import Response

    from domovoi.main import app as core_app
    from web.backend.api import images, videos

    asked: list[tuple] = []

    async def thumb(library_id, path, size="m"):
        asked.append(("thumb", library_id, path, size))
        return Response(content=b"webp", media_type="image/webp", headers={"Cache-Control": "public, max-age=604800"})

    async def poster(library_id, path):
        asked.append(("poster", library_id, path))
        return Response(content=b"jpg", media_type="image/jpeg")

    monkeypatch.setattr(images, "thumb", thumb)
    monkeypatch.setattr(videos, "poster", poster)
    clock = Clock()
    app = _house(_sess("kitchen", "kitchen", screen=True))
    room_media.show(app, "kitchen", _answer([_card(1), _card(2, "video", at_s=3.0)]), "x", people_ok=True)
    items = room_media.frame_media("kitchen")["items"]
    r = await _get(core_app, items[0]["thumb"])
    assert r.status_code == 200 and r.content == b"webp" and r.headers["cache-control"] == "no-store"
    assert (await _get(core_app, items[1]["thumb"])).content == b"jpg"
    assert asked == [("thumb", "core:pictures", "f1.jpg", "m"), ("poster", "core:pictures", "f2.jpg")]
    for bad in ("/v1/room-media/nope", "/v1/room-media/" + "A" * 32, "/v1/room-media/..%2F..%2Fetc"):
        assert (await _get(core_app, bad)).status_code == 404
    # Expired: gone.
    tok = room_media.mint("kitchen", "core:pictures", "x.jpg", "image", clock=clock)
    assert room_media.resolve(tok, clock=clock) is not None
    clock.t += room_media.TOKEN_TTL_SEC
    assert room_media.resolve(tok, clock=clock) is None
    # Tied to the results on the screen: replaced, cleared or timed out, the
    # old results' tokens stop working at once.
    old_tok = items[0]["thumb"].removeprefix(room_media.THUMB_PATH)
    room_media.show(app, "kitchen", _answer([_card(3)]), "y", people_ok=True)
    assert room_media.resolve(old_tok) is None
    new_tok = room_media.frame_media("kitchen")["items"][0]["thumb"].removeprefix(room_media.THUMB_PATH)
    assert room_media.resolve(new_tok) is not None
    room_media.clear("kitchen")
    assert room_media.resolve(new_tok) is None
    room_media.show(app, "kitchen", _answer([_card(4)]), "z", people_ok=True, clock=clock)
    tok4 = room_media.frame_media("kitchen", clock=clock)["items"][0]["thumb"].removeprefix(room_media.THUMB_PATH)
    clock.t += room_media.SHOW_SEC
    assert room_media.resolve(tok4, clock=clock) is None and room_media.current("kitchen", clock=clock) is None
    # Bounded: the oldest go first.
    for _ in range(room_media.MAX_TOKENS + 5):
        room_media.mint("kitchen", "core:pictures", "x.jpg", "image")
    assert len(room_media._TOKENS) == room_media.MAX_TOKENS


# ─── the web backend: digest push and the kiosk's read ───────────────────


async def test_the_kiosk_reads_its_rooms_results_and_the_push_is_a_digest(monkeypatch):
    from domovoi.tests.auth_testkit import HEADER, core_client, install_fake_db, web_client
    from web.backend import domovoi_client, realtime
    from web.backend.api import satellites as sat_api

    app = _house(_sess("den", "den", screen=False), video=("den",))
    room_media.show(app, "den", _answer([_card(1, caption="<b>hi</b>")]), "I found 1 photo.", people_ok=True)
    snap = {"room_media": room_media.snapshot()}
    # The open snapshot says which results are up, never a word of them.
    assert snap["room_media"] == {"den": room_media.current("den").id}
    monkeypatch.setattr(domovoi_client, "_snapshot", snap)
    install_fake_db(monkeypatch, admin=True, device_token="household")    # set up: no LAN grace
    hops: list[tuple[str, dict]] = []

    async def via_core(path, timeout=10.0, headers=None):
        hops.append((path, dict(headers or {})))
        async with core_client() as cc:
            rr = await cc.get(path, headers=headers or {})
        return rr.status_code, rr.json()

    monkeypatch.setattr(sat_api, "get_admin", via_core)
    async with web_client() as c:
        r = await c.get("/api/satellites/den/media", headers={HEADER: "household"})
        none = await c.get("/api/satellites/kitchen/media", headers={HEADER: "household"})
        anon = await c.get("/api/satellites/den/media")
    assert hops and hops[0][0] == "/v1/admin/room-media/den"
    # The core's results route is device read: no credential, no results.
    async with core_client() as cc:
        assert (await cc.get("/v1/admin/room-media/den")).status_code == 401
    from domovoi import main as core_main

    open_snap = {"room_media": core_main._room_media_snapshot()}    # what the open route carries
    assert open_snap["room_media"] == {"den": room_media.current("den").id}
    assert "hi" not in json.dumps(open_snap["room_media"]) and "I found" not in json.dumps(open_snap["room_media"])
    assert r.status_code == 200 and r.json()["media"]["items"][0]["caption"] == "<b>hi</b>"
    assert r.json()["media"]["items"][0]["thumb"].startswith("/api/images/thumb?")
    assert none.json() == {"media": None}
    assert anon.status_code == 401
    # The push names rooms and ids, never a caption.
    digest = realtime.room_media_digest(snap)
    assert digest == {"den": room_media.current("den").id}
    assert "hi" not in json.dumps(digest) and "thumb" not in json.dumps(digest)
    assert realtime.room_media_digest(None) == {} and realtime.room_media_digest({"room_media": []}) == {}
    assert realtime.room_media_digest({"room_media": {"x": "said words", "y": True}}) == {}


# ─── the kiosk page ──────────────────────────────────────────────────────

KIOSK_SCRIPT = r"""
h.render(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
const all = h.tree();
const textOf = (el) => all.filter((x) => x === el || h.ancestors(x).includes(el)).map((x) => x.text)
  .filter(Boolean).join(' ');
const grid = h.find((el) => el.props && el.props.className === 'dp-media');
return { shown: !!grid,
         said: textOf(h.find((el) => el.props && el.props.className === 'dp-media-said')),
         captions: h.findAll((el) => el.props && el.props.className === 'dp-media-caption').map(textOf),
         badges: h.findAll((el) => el.props && el.props.className === 'mono dp-media-at').map(textOf),
         imgs: h.findAll((el) => el.type === 'img').map((el) => el.props.src),
         html: all.filter((el) => el.props && el.props.dangerouslySetInnerHTML).length,
         asked: h.hookCalls };
"""
EVIL = '<img src=x onerror="alert(1)">'


def test_the_kiosk_page_draws_results_as_text():
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    media = {"id": 4, "said": "I found 2 photos.", "count": 2, "more": False, "expires_in_ms": 90000,
             "items": [{"kind": "image", "thumb": "/api/images/thumb?library_id=core%3Apictures&path=a.jpg&size=m",
                        "caption": EVIL, "date": "2025-07-14", "at_s": None},
                       {"kind": "video", "thumb": "/api/videos/poster?library_id=core%3Apictures&path=v.mp4",
                        "caption": None, "date": None, "at_s": 42.0}]}
    setup = ("window.location.search = '?room=den';\n"
             "window.location.pathname = '/display.html';\n"
             "globalThis.API_BASE = '';\n"
             "globalThis.withDeviceToken = (u) => u + (u.includes('?') ? '&' : '?') + 'device_token=t';\n"
             "globalThis.ReactDOM = { createRoot: () => ({ render() {} }) };\n"
             "document.getElementById = () => null;\n")
    scen = {"files": ["web/static/display.jsx"], "component": "DisplayApp", "setup": setup,
            "api": {"GET /api/satellites/den/media": {"media": media}, "GET /api/music/now-playing": []},
            "script": KIOSK_SCRIPT}
    proc = subprocess.run([node, str(HARNESS), str(REPO), json.dumps({"k": scen})],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["k"]
    assert "__harness_error" not in out, out.get("__harness_error")
    assert out["shown"] and out["said"] == "I found 2 photos."
    assert out["captions"] == ["Might show: " + EVIL] and out["html"] == 0
    assert out["badges"] == ["0:42"]
    assert out["imgs"] == ["/api/images/thumb?library_id=core%3Apictures&path=a.jpg&size=m&device_token=t",
                           "/api/videos/poster?library_id=core%3Apictures&path=v.mp4&device_token=t"]
    src = (REPO / "web" / "static" / "display.jsx").read_text(encoding="utf-8")
    assert "useStateEvents(['satellites.media.changed']" in src


def test_a_room_media_token_never_reaches_the_log():
    from domovoi import log_privacy

    tok = "Ab3_" + "x" * 28
    line = f'192.168.1.41:50000 - "GET /v1/room-media/{tok} HTTP/1.1" 200'
    out = log_privacy.scrub(line)
    assert tok not in out and "/v1/room-media/***" in out
