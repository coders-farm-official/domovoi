"""Stations by voice (1.3.1): spoken-name matching, "did you mean …?",
the search past the favorites (the table, then the internet directory when
the household allows it), "add this to my favorites", and playing a
station in a room from the dashboard (the core route, its web twin, and
the manifest's room_play_template the browser player's cast uses).

The two misheard phrases are real (2026-11-16): the owner's favorite
"Lofi 24/7" was heard as "lo-fi 24 slash 7" and "Low 5 24-7", and the
old ``LIKE %query%`` found neither."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi import egress
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db

from domovoi_plugin_radio import station_match as sm
from domovoi_plugin_radio.clients import radio_browser
from domovoi_plugin_radio.clients.radio_browser import RadioBrowserStation
from domovoi_plugin_radio.handlers.radio import (
    _FAVORITE_STATION_RE,
    _FAVORITE_THIS_RE,
    _PLAY_RADIO_X_RE,
    _PLAY_STATION_RE,
    _PLAY_X_RADIO_RE,
    _STREAM_RE,
    RadioHandler,
)

KIND = "radio.station_choice"
FAVORITES = ["Lofi 24/7", "Jazz 24/7", "Lofi Hip Hop", "Chillhop Cafe", "Radio Paradise"]


def _decide(query: str, names=FAVORITES) -> tuple[str, list[str]]:
    d = sm.decide(sm.rank(query, [{"name": n} for n in names]))
    return d.action, [s["name"] for s in d.stations]


# ─── Matching (pure) ─────────────────────────────────────────────────────


def test_the_two_misheard_phrases_reach_lofi_24_7() -> None:
    # Said clearly enough to play outright: "slash" is the "/", "lo-fi" is
    # "lofi", numbers are compared as words.
    assert _decide("lo-fi 24 slash 7") == ("play", ["Lofi 24/7"])
    # Close but unsure ("low five" only sounds like "lofi"): ask.
    assert _decide("Low 5 24-7") == ("ask", ["Lofi 24/7"])


@pytest.mark.parametrize("heard", [
    "lofi 24/7", "lo fi twenty four seven", "low fi 24 7", "LOFI 24-7",
    "the lofi 24 7 radio",
])
def test_ways_of_saying_it_play_it(heard: str) -> None:
    assert _decide(heard) == ("play", ["Lofi 24/7"])


@pytest.mark.parametrize("heard, expected", [
    # A shared "24/7" must not carry a different first word to Lofi 24/7.
    ("jazz 24 slash 7", ("play", ["Jazz 24/7"])),
    ("chill 24/7", ("none", [])),
    ("country 24 7", ("none", [])),
    ("rock 24 7", ("none", [])),
    # "lofi hip hop" is its own favorite, not "Lofi 24/7".
    ("low fi hip hop", ("play", ["Lofi Hip Hop"])),
    ("hip hop", ("play", ["Lofi Hip Hop"])),
    ("chillhop", ("play", ["Chillhop Cafe"])),
    ("paradise", ("play", ["Radio Paradise"])),
    ("90s hits", ("none", [])),
])
def test_near_misses_never_reach_another_favorite(heard, expected) -> None:
    assert _decide(heard) == expected


def test_a_word_two_favorites_share_offers_both() -> None:
    assert _decide("lofi") == ("choose", ["Lofi 24/7", "Lofi Hip Hop"])


def test_call_sign_and_short_names_match_exactly_only() -> None:
    assert sm.score("kexp", "KEXP 90.3 FM", "KEXP") == 1.0
    assert sm.score("k e x p", "Seattle", "KEXP") == 1.0
    # Three letters or fewer are never matched fuzzily.
    assert sm.score("kex", "KEXP 90.3 FM") < sm.ASK
    assert sm.score("wxyz", "WXYQ") < sm.ASK


def test_carrier_words_alone_name_nothing() -> None:
    for q in ("the radio", "a station", "some radio", "my radio station"):
        assert sm.is_carrier_only(q), q
    assert not sm.is_carrier_only("lofi radio")


def test_the_new_fast_paths_capture_the_name() -> None:
    assert _PLAY_STATION_RE.match("play the station lofi 24/7").group(1) == "lofi 24/7"
    assert _PLAY_STATION_RE.match("play radio station, kexp").group(1) == "kexp"
    assert _PLAY_X_RADIO_RE.match("play lofi radio").group(1) == "lofi"
    assert _PLAY_X_RADIO_RE.match("play the jazz station").group(1) == "jazz"
    assert _PLAY_RADIO_X_RE.match("play radio paradise").group(1) == "paradise"
    assert _FAVORITE_THIS_RE.match("add this to my favorites")
    assert _FAVORITE_THIS_RE.match("add it to favourites")
    assert _FAVORITE_STATION_RE.match("favorite this station")
    assert _FAVORITE_STATION_RE.match("add this station to my favorites")
    # Never a bare "play X".
    for t in ("play the beatles", "play radiohead", "play lofi"):
        assert not any(p.match(t) for p in (_PLAY_STATION_RE, _PLAY_X_RADIO_RE, _PLAY_RADIO_X_RE)), t


# ─── The handler (DB tier) ───────────────────────────────────────────────


def _ctx(room: str = "kitchen", **kw) -> Context:
    return Context(room_id=room, online=kw.pop("online", True), **kw)


async def _insert(session, name: str, *, favorited: bool = True, **kw) -> int:
    row = await session.execute(
        text(
            "INSERT INTO plugin_radio.radio_stations (name, source, stream_url, "
            "favorited, external_id, created_by_play) VALUES (:name, :source, :url, "
            ":fav, :eid, :cbp) RETURNING id"
        ),
        {
            "name": name, "source": kw.get("source", "online"),
            "url": kw.get("url", f"http://{name.lower().replace(' ', '').replace('/', '')}.example/s.mp3"),
            "fav": favorited, "eid": kw.get("external_id"),
            "cbp": kw.get("created_by_play", False),
        },
    )
    await session.commit()
    return int(row.scalar_one())


async def _favorites(session) -> None:
    for n in ("Lofi 24/7", "Jazz 24/7", "Lofi Hip Hop"):
        await _insert(session, n)


class _Directory:
    """The station directory, stubbed (no network in tests)."""

    def __init__(self, rows: list[tuple[str, str, str]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[dict] = []

    async def search(self, **kw):
        self.calls.append(kw)
        return [
            RadioBrowserStation(external_id=eid, name=name, stream_url=url,
                                country_code="US", tags=["chill"])
            for eid, name, url in self.rows
        ]


@pytest.fixture
def directory():
    d = _Directory([
        ("uuid-groove", "Groove Salad - ambient beats", "http://groove.example/s.mp3"),
        ("uuid-groove-2", "Groove Salad Classic", "http://groove2.example/s.mp3"),
    ])
    radio_browser._set_radio_browser_client_for_tests(d)
    return d


def _plays(stub_sdk) -> list[dict]:
    return [c for c in stub_sdk.playback.calls if c["action"] == "play_url"]


@requires_db
async def test_the_misheard_phrase_plays_the_favorite(stub_sdk, db_session) -> None:
    await _favorites(db_session)
    h = RadioHandler(stub_sdk)
    resp = await h._stream_from_match(_STREAM_RE.match("stream lo-fi 24 slash 7"), _ctx(), db_session)
    assert resp.text == "Streaming Lofi 24/7."
    assert _plays(stub_sdk)[0]["title"] == "Lofi 24/7"


@requires_db
async def test_unsure_asks_and_yes_plays(stub_sdk, db_session) -> None:
    await _favorites(db_session)
    h = RadioHandler(stub_sdk)
    m = _PLAY_X_RADIO_RE.match("play low 5 24-7 radio")
    resp = await h._play_x_radio_from_match(m, _ctx(), db_session)
    assert resp.text == "Did you mean Lofi 24/7?"
    assert resp.expect_followup is True
    assert _plays(stub_sdk) == []
    (conf,) = stub_sdk.sessions.confirmations
    assert conf["kind"] == KIND
    assert [c["name"] for c in conf["data"]["candidates"]] == ["Lofi 24/7"]

    resp = await h.handle_choice_reply(KIND, conf["data"], "yes", _ctx(), db_session)
    assert resp.text == "Streaming Lofi 24/7."
    assert _plays(stub_sdk)[0]["title"] == "Lofi 24/7"


@requires_db
async def test_no_drops_it_and_a_correction_plays_the_one_named(stub_sdk, db_session) -> None:
    await _favorites(db_session)
    h = RadioHandler(stub_sdk)
    await h._stream_by_name("low 5 24-7", _ctx(), db_session)
    data = stub_sdk.sessions.confirmations[-1]["data"]

    resp = await h.handle_choice_reply(KIND, data, "no", _ctx(), db_session)
    assert resp.text == "OK."
    assert _plays(stub_sdk) == []

    resp = await h.handle_choice_reply(KIND, data, "no, jazz 24 7", _ctx(), db_session)
    assert resp.text == "Streaming Jazz 24/7."
    # A reply about something else is not the question's: routed anew.
    assert await h.handle_choice_reply(KIND, data, "what time is it", _ctx(), db_session) is None


@requires_db
async def test_two_close_favorites_are_offered_and_an_ordinal_picks(stub_sdk, db_session) -> None:
    await _favorites(db_session)
    h = RadioHandler(stub_sdk)
    resp = await h._play_station_from_match(
        _PLAY_STATION_RE.match("play the station lofi"), _ctx(), db_session
    )
    assert resp.text == "Lofi 24/7 or Lofi Hip Hop?"
    data = stub_sdk.sessions.confirmations[-1]["data"]
    resp = await h.handle_choice_reply(KIND, data, "the second one", _ctx(), db_session)
    assert resp.text == "Streaming Lofi Hip Hop."


@requires_db
async def test_an_expired_question_is_not_answered(stub_sdk, db_session) -> None:
    await _favorites(db_session)
    h = RadioHandler(stub_sdk)
    await h._stream_by_name("low 5 24-7", _ctx(), db_session)
    data = dict(stub_sdk.sessions.confirmations[-1]["data"], expires_at=0)
    assert await h.handle_choice_reply(KIND, data, "yes", _ctx(), db_session) is None


@requires_db
async def test_nothing_is_asked_where_nobody_can_answer(stub_sdk, db_session) -> None:
    await _favorites(db_session)
    h = RadioHandler(stub_sdk)
    resp = await h._stream_by_name("low 5 24-7", _ctx(answerable=False), db_session)
    assert resp.text == "I don't have low 5 24-7. The closest is Lofi 24/7."
    assert stub_sdk.sessions.confirmations == []
    assert not resp.expect_followup


@requires_db
async def test_a_station_in_the_table_beats_the_directory(stub_sdk, db_session, directory) -> None:
    await _insert(db_session, "Groove Salad", favorited=False)
    h = RadioHandler(stub_sdk)
    resp = await h._play_x_radio_from_match(
        _PLAY_X_RADIO_RE.match("play groove salad radio"), _ctx(), db_session
    )
    assert resp.text == "Streaming Groove Salad."
    assert directory.calls == []


@requires_db
async def test_directory_search_offers_two_and_plays_the_pick(stub_sdk, db_session, directory) -> None:
    await _favorites(db_session)
    h = RadioHandler(stub_sdk)
    resp = await h._play_station_from_match(
        _PLAY_STATION_RE.match("play the station groove salad"), _ctx(), db_session
    )
    assert resp.text == (
        "It's not in your stations. Online there's Groove Salad or "
        "Groove Salad Classic. Which one?"
    )
    assert directory.calls and directory.calls[0]["name"] == "groove salad"
    assert _plays(stub_sdk) == []
    data = stub_sdk.sessions.confirmations[-1]["data"]
    assert [c["id"] for c in data["candidates"]] == [None, None]

    resp = await h.handle_choice_reply(KIND, data, "the second one", _ctx(), db_session)
    assert resp.text == "Streaming Groove Salad Classic."
    (play,) = _plays(stub_sdk)
    assert play["stream_url"] == "http://groove2.example/s.mp3"
    # Saved as a played-once row (in Recent, not a favorite), and the
    # now-playing data carries its id so "add this to favorites" works.
    row = (await db_session.execute(text(
        "SELECT id, favorited, created_by_play, last_played_at IS NOT NULL, external_id "
        "FROM plugin_radio.radio_stations WHERE name = 'Groove Salad Classic'"
    ))).one()
    assert row[1:] == (False, True, True, "uuid-groove-2")
    assert play["now_playing_data"]["station_id"] == row[0]


@requires_db
async def test_one_directory_hit_is_offered_as_a_yes_or_no(stub_sdk, db_session) -> None:
    radio_browser._set_radio_browser_client_for_tests(
        _Directory([("uuid-x", "Nightwave Plaza", "http://nightwave.example/s.mp3")])
    )
    h = RadioHandler(stub_sdk)
    resp = await h._stream_by_name("nightwave plaza", _ctx(), db_session)
    assert resp.text == "It's not in your stations. I found Nightwave Plaza online. Play it?"
    data = stub_sdk.sessions.confirmations[-1]["data"]
    resp = await h.handle_choice_reply(KIND, data, "yes please", _ctx(), db_session)
    assert resp.text == "Streaming Nightwave Plaza."


@requires_db
async def test_internet_set_to_no_never_searches_the_directory(stub_sdk, db_session, directory) -> None:
    await _favorites(db_session)
    h = RadioHandler(stub_sdk)
    with egress.override_policy("never"):
        resp = await h._stream_by_name("groove salad", _ctx(), db_session)
    assert resp.text.startswith("I don't have a station called groove salad.")
    assert "can't look online" in resp.text
    assert directory.calls == []
    assert stub_sdk.sessions.confirmations == []


@requires_db
async def test_offline_never_searches_the_directory(stub_sdk, db_session, directory) -> None:
    h = RadioHandler(stub_sdk)
    resp = await h._stream_by_name("groove salad", _ctx(online=False), db_session)
    assert "can't reach the internet" in resp.text
    assert directory.calls == []
    # The router's offline fallback reads the new phrasings too (FM only).
    resp = await h.fallback_offline(Intent(transcript="play groove salad radio"), _ctx(online=False), db_session)
    assert resp.text == "I don't have an FM station called groove salad."


@requires_db
async def test_play_radio_x_is_left_alone_unless_a_saved_station_answers(
    stub_sdk, db_session, directory
) -> None:
    h = RadioHandler(stub_sdk)
    # "Radio Ga Ga" is a song: nothing saved, so the turn isn't radio's.
    m = _PLAY_RADIO_X_RE.match("play radio ga ga")
    assert await h._play_radio_x_from_match(m, _ctx(), db_session) is None
    assert directory.calls == []
    await _insert(db_session, "Radio Paradise")
    m = _PLAY_RADIO_X_RE.match("play radio paradise")
    resp = await h._play_radio_x_from_match(m, _ctx(), db_session)
    assert resp.text == "Streaming Radio Paradise."
    # Carrier words alone ("play the radio") name no station.
    assert await h._play_x_radio_from_match(_PLAY_X_RADIO_RE.match("play the radio"), _ctx(), db_session) is None


# ─── "Add this to my favorites" ──────────────────────────────────────────


@requires_db
async def test_favorite_this_station_stars_what_plays(stub_sdk, db_session) -> None:
    sid = await _insert(db_session, "Groove Salad", favorited=False, created_by_play=True)
    stub_sdk.now_playing.stamp("kitchen", "radio", {"station_id": sid, "station_name": "Groove Salad"})
    h = RadioHandler(stub_sdk)
    resp = await h._favorite_this_from_match(_FAVORITE_THIS_RE.match("add this to my favorites"), _ctx(), db_session)
    assert resp.text == "Added Groove Salad to your favorites."
    row = (await db_session.execute(text(
        "SELECT favorited, created_by_play FROM plugin_radio.radio_stations WHERE id = :i"
    ), {"i": sid})).one()
    assert tuple(row) == (True, False)
    resp = await h._favorite_station_from_match(_FAVORITE_STATION_RE.match("favorite this station"), _ctx(), db_session)
    assert resp.text == "Groove Salad is already a favorite."


@requires_db
async def test_add_this_to_favorites_without_a_station_is_left_alone(stub_sdk, db_session) -> None:
    h = RadioHandler(stub_sdk)
    m = _FAVORITE_THIS_RE.match("add this to my favorites")
    assert await h._favorite_this_from_match(m, _ctx(), db_session) is None
    m = _FAVORITE_STATION_RE.match("favorite this station")
    resp = await h._favorite_station_from_match(m, _ctx(), db_session)
    assert resp.text == "There's no station playing here right now."


# ─── Routing: the new phrasings land here, neighbors keep theirs ─────────


@requires_db
async def test_new_phrasings_route_to_radio(loaded_plugin) -> None:
    for t in ("play lofi radio", "play the station lofi 24/7", "play radio station kexp",
              "play radio paradise", "add this to my favorites", "favorite this station"):
        loaded_plugin.assert_routes(t, "radio")
    loaded_plugin.assert_routes("play the beatles", "music")
    loaded_plugin.assert_routes("favorite this", "news")
    loaded_plugin.assert_routes("play my favorites", "playlist")
    from domovoi.handlers import HANDLER_BY_NAME

    assert HANDLER_BY_NAME["radio"].choice_kinds == (KIND,)


# ─── Play a station in a room (the core route) ───────────────────────────


@pytest.fixture
def room_app(radio_sdk, monkeypatch):
    """The plugin's core router behind the real plugin gate, with a
    recording player that knows one room (kitchen)."""
    from fastapi import FastAPI

    from domovoi import plugin_http
    from domovoi.sdk.testing import RecordingPlayback
    from domovoi.tests.auth_testkit import install_fake_db

    from domovoi_plugin_radio.core import _build_core_router

    install_fake_db(monkeypatch, admin=True, sessions={"admin-session"},
                    device_token="household-token")
    radio_sdk.playback = RecordingPlayback(known_rooms={"kitchen"})
    app = FastAPI()
    plugin_http.mount_plugin_routers(app, "radio", [_build_core_router(radio_sdk)])
    app.state.sdk = radio_sdk
    return app


async def _post(app, path: str, body: dict, *, token: str | None = "household-token"):
    from httpx import ASGITransport, AsyncClient

    from domovoi.tests.auth_testkit import HEADER

    headers = {HEADER: token} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post(f"/v1/plugins/radio{path}", json=body, headers=headers)


@requires_db
async def test_room_play_plays_and_tells_the_room(room_app, db_session) -> None:
    sid = await _insert(db_session, "Lofi 24/7")
    r = await _post(room_app, f"/stations/{sid}/play-in-room", {"room_id": "kitchen"})
    assert r.status_code == 200, r.text
    assert r.json()["station"] == {"id": sid, "name": "Lofi 24/7"}
    calls = room_app.state.sdk.playback.calls
    play = next(c for c in calls if c["action"] == "play_url")
    assert play["room_id"] == "kitchen" and play["title"] == "Lofi 24/7"
    assert {"action": "dispatch", "room_id": "kitchen", "music_action": "start"} in calls


@requires_db
async def test_room_play_needs_the_household_token(room_app, db_session) -> None:
    sid = await _insert(db_session, "Lofi 24/7")
    r = await _post(room_app, f"/stations/{sid}/play-in-room", {"room_id": "kitchen"}, token=None)
    assert r.status_code == 401
    assert room_app.state.sdk.playback.calls == []


@requires_db
async def test_room_play_unknown_room_and_unknown_station(room_app, db_session) -> None:
    sid = await _insert(db_session, "Lofi 24/7")
    r = await _post(room_app, f"/stations/{sid}/play-in-room", {"room_id": "attic"})
    assert r.status_code == 404 and "no room named 'attic'" in r.json()["detail"]
    r = await _post(room_app, "/stations/999999/play-in-room", {"room_id": "kitchen"})
    assert r.status_code == 404 and "not found" in r.json()["detail"]
    assert not any(c["action"] == "play_url" for c in room_app.state.sdk.playback.calls)


@requires_db
async def test_room_play_refuses_an_internet_station_when_internet_is_no(room_app, db_session) -> None:
    sid = await _insert(db_session, "Lofi 24/7", url="http://lofi.example.com/s.mp3")
    with egress.override_policy("never"):
        r = await _post(room_app, f"/stations/{sid}/play-in-room", {"room_id": "kitchen"})
    assert r.status_code == 409
    assert not any(c["action"] == "dispatch" for c in room_app.state.sdk.playback.calls)


# ─── …and its dashboard twin (proxied to the core) ───────────────────────


def _web_seed(client, **kw) -> int:
    body = {"name": "Lofi 24/7", "source": "online",
            "stream_url": "http://lofi.example/s.mp3", "external_id": "uuid-lofi"}
    body.update(kw)
    r = client.post("/api/plugins/radio/stations", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def clean_web(web_client):
    import asyncio

    from domovoi.db.session import SessionLocal

    async def _truncate():
        async with SessionLocal() as s:
            async with s.begin():
                await s.execute(text(
                    "TRUNCATE plugin_radio.radio_detections, plugin_radio.track_fingerprints, "
                    "plugin_radio.radio_stations RESTART IDENTITY CASCADE"))

    asyncio.run(_truncate())
    yield web_client
    asyncio.run(_truncate())


@requires_db
def test_web_room_play_proxies_and_stamps_recent(clean_web) -> None:
    sid = _web_seed(clean_web)
    path = f"/v1/plugins/radio/stations/{sid}/play-in-room"
    clean_web.ctx.core.responses[path] = {"ok": True, "room_id": "kitchen"}
    r = clean_web.post(f"/api/plugins/radio/stations/{sid}/play-in-room", json={"room_id": " kitchen "})
    assert r.status_code == 200, r.text
    (call,) = [c for c in clean_web.ctx.core.calls if c["path"] == path]
    assert call["method"] == "POST_ADMIN" and call["json"] == {"room_id": "kitchen"}
    assert call["forwarded_request"] is True
    recent = clean_web.get("/api/plugins/radio/recent").json()
    assert [s["id"] for s in recent] == [sid]


@requires_db
def test_web_room_play_unknown_station_and_internet_no(clean_web) -> None:
    r = clean_web.post("/api/plugins/radio/stations/424242/play-in-room", json={"room_id": "kitchen"})
    assert r.status_code == 404
    sid = _web_seed(clean_web, stream_url="http://lofi.example.com/s.mp3")
    with egress.override_policy("never"):
        r = clean_web.post(f"/api/plugins/radio/stations/{sid}/play-in-room", json={"room_id": "kitchen"})
    assert r.status_code == 409
    assert not clean_web.ctx.core.calls
    r = clean_web.post(f"/api/plugins/radio/stations/{sid}/play-in-room", json={"room_id": ""})
    assert r.status_code == 422


def test_both_room_routes_are_on_the_device_tier() -> None:
    from domovoi import webkit
    from domovoi.sdk.testing import make_stub_sdk

    from domovoi_plugin_radio import web as radio_web
    from domovoi_plugin_radio.core import _build_core_router

    class _Ctx:
        db_session_scope = None
        core = None

    def tier(router) -> str:
        (route,) = [r for r in router.routes if r.path.endswith("/play-in-room")]
        return webkit.endpoint_tier(route.endpoint)

    assert tier(_build_core_router(make_stub_sdk("radio"))) == "device"
    assert tier(radio_web.build_router(_Ctx())) == "device"


# ─── The cast's way in: the manifest's room_play_template ────────────────


def test_the_manifest_names_the_room_route_for_the_cast(tmp_path) -> None:
    import shutil

    from pathlib import Path

    from domovoi.plugins_runtime.manifest import ManifestError, parse_manifest_dir

    PLUGIN_DIR = Path(__file__).resolve().parents[1]

    (src,) = parse_manifest_dir(PLUGIN_DIR).player_sources
    assert src.kind == "radio"
    assert src.room_play_template == "/api/plugins/radio/stations/{id}/play-in-room"

    # Only one of the plugin's own routes, with an {id}, is accepted.
    copy = tmp_path / "radio"
    shutil.copytree(PLUGIN_DIR, copy, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    toml = copy / "domovoi-plugin.toml"
    original = toml.read_text(encoding="utf-8")
    for bad in ("/api/music/play-tracks", "/api/plugins/other/{id}", "/api/plugins/radio/x"):
        toml.write_text(original.replace(
            '"/api/plugins/radio/stations/{id}/play-in-room"', f'"{bad}"'), encoding="utf-8")
        with pytest.raises(ManifestError, match="room_play_template"):
            parse_manifest_dir(copy)
