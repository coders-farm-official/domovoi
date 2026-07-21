"""RadioHandler behavior — fast paths, resolution, the numeric(5,1)
cast, SDR-before-MPD stop ordering, confirmations, offline contract."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from domovoi.models import Context
from domovoi.tests.conftest import requires_db

from domovoi_plugin_radio.handlers.radio import (
    _PLAY_FREQUENCY_RE,
    _STOP_STREAM_RE,
    _STREAM_RE,
    _TUNE_RE,
    RadioHandler,
)

pytestmark = requires_db


def _ctx(room: str = "kitchen") -> Context:
    return Context(room_id=room, online=True)


async def _insert_station(session, **kw) -> int:
    defaults = {
        "name": "KEXP",
        "source": "online",
        "stream_url": "http://kexp.example/stream.mp3",
        "frequency_mhz": None,
        "call_sign": None,
        "favorited": True,
        "market_city": None,
        "market_state": None,
    }
    defaults.update(kw)
    row = await session.execute(
        text(
            """
            INSERT INTO plugin_radio.radio_stations
                (name, source, stream_url, frequency_mhz, call_sign,
                 favorited, market_city, market_state)
            VALUES (:name, :source, :stream_url, :frequency_mhz, :call_sign,
                    :favorited, :market_city, :market_state)
            RETURNING id
            """
        ),
        defaults,
    )
    await session.commit()
    return int(row.scalar_one())


# ─── Regex anchoring ─────────────────────────────────────────────────────


def test_patterns_anchor_and_capture() -> None:
    assert _STREAM_RE.match("stream kexp").group(1) == "kexp"
    assert _STREAM_RE.match("stream the river").group(1) == "river"
    assert _TUNE_RE.match("tune to kexp").group(1) == "kexp"
    assert _TUNE_RE.match("tune in to npr").group(1) == "npr"
    m = _PLAY_FREQUENCY_RE.match("play 97.5 fm")
    assert m and m.group(1) == "97.5" and m.group(2) == "fm"
    assert _PLAY_FREQUENCY_RE.match("play 1010 am")
    assert _STOP_STREAM_RE.match("stop the radio")
    assert _STOP_STREAM_RE.match("stop streaming")
    # MusicHandler territory never matches.
    assert _PLAY_FREQUENCY_RE.match("play the beatles") is None
    assert _STREAM_RE.match("play kexp") is None


def test_offline_ok_declarations(stub_sdk) -> None:
    """Internet paths auto-fallback offline; FM/stop paths keep running
    (design §4.3 — the audit M4 fix)."""
    h = RadioHandler(stub_sdk)
    flags = {fp.pattern.pattern: fp.offline_ok for fp in h.fast_paths}
    assert flags[_STOP_STREAM_RE.pattern] is True
    assert flags[_PLAY_FREQUENCY_RE.pattern] is True
    assert flags[_STREAM_RE.pattern] is False
    assert flags[_TUNE_RE.pattern] is False
    assert h.requires_network == "degraded"
    assert h.confirmation_kinds == ("radio.station_choice",)


# ─── Name resolution ─────────────────────────────────────────────────────


async def test_stream_by_name_single_hit_plays(stub_sdk, db_session) -> None:
    await _insert_station(db_session, name="KEXP", call_sign="KEXP")
    h = RadioHandler(stub_sdk)
    m = _STREAM_RE.match("stream kexp")
    resp = await h._stream_from_match(m, _ctx(), db_session)
    assert resp.text == "Streaming KEXP."
    assert resp.music_action == "start"
    (call,) = stub_sdk.playback.calls
    assert call["stream_url"] == "http://kexp.example/stream.mp3"
    assert call["source"] == "radio"
    # Station-name-as-title stamping convention (favorites depends on it).
    assert call["title"] == "KEXP"


async def test_stream_by_name_no_hit(stub_sdk, db_session) -> None:
    h = RadioHandler(stub_sdk)
    m = _STREAM_RE.match("stream nothing")
    resp = await h._stream_from_match(m, _ctx(), db_session)
    assert "don't have a favorited station" in resp.text
    assert stub_sdk.playback.calls == []


async def test_stream_by_name_unfavorited_is_invisible(stub_sdk, db_session) -> None:
    await _insert_station(db_session, name="KEXP", favorited=False)
    h = RadioHandler(stub_sdk)
    resp = await h._stream_from_match(_STREAM_RE.match("stream kexp"), _ctx(), db_session)
    assert "don't have a favorited station" in resp.text


async def test_multi_candidate_parks_namespaced_confirmation(
    stub_sdk, db_session
) -> None:
    await _insert_station(db_session, name="KEXP Seattle")
    await _insert_station(db_session, name="KEXP Tacoma")
    h = RadioHandler(stub_sdk)
    resp = await h._stream_from_match(_STREAM_RE.match("stream kexp"), _ctx(), db_session)
    assert "2 matches" in resp.text
    assert resp.expect_followup is True
    (conf,) = stub_sdk.sessions.confirmations
    assert conf["kind"] == "radio.station_choice"
    assert conf["handler"] == "radio"
    assert len(conf["data"]["candidates"]) == 2


async def test_handle_confirmation_affirmative_streams_first(
    stub_sdk, db_session
) -> None:
    h = RadioHandler(stub_sdk)
    data = {
        "candidates": [
            {"id": 1, "name": "KEXP", "source": "online",
             "stream_url": "http://a/x.mp3", "call_sign": None},
            {"id": 2, "name": "KQED", "source": "online",
             "stream_url": "http://b/y.mp3", "call_sign": None},
        ]
    }
    resp = await h.handle_confirmation(
        "radio.station_choice", data, True, _ctx(), db_session
    )
    assert resp.text == "Streaming KEXP."
    (call,) = stub_sdk.playback.calls
    assert call["stream_url"] == "http://a/x.mp3"


async def test_handle_confirmation_negative_drops(stub_sdk, db_session) -> None:
    h = RadioHandler(stub_sdk)
    resp = await h.handle_confirmation(
        "radio.station_choice", {"candidates": [{"id": 1}]}, False,
        _ctx(), db_session,
    )
    assert resp.text == "OK, never mind."
    assert stub_sdk.playback.calls == []


# ─── Frequency resolution (the load-bearing numeric cast) ────────────────


async def test_frequency_lookup_hits_numeric_column(stub_sdk, db_session) -> None:
    """A bound Python float must compare against NUMERIC(5,1) — without
    the (:freq)::numeric(5,1) cast this lookup silently misses."""
    await _insert_station(
        db_session, name="WKAR", source="fm", stream_url=None,
        frequency_mhz=90.5, call_sign="WKAR",
        market_city="LANSING", market_state="MI",
    )
    stub_sdk.state["sdr_tuner"] = _FakeTuner()
    h = RadioHandler(stub_sdk)
    m = _PLAY_FREQUENCY_RE.match("play 90.5 fm")
    resp = await h._frequency_from_match(m, _ctx(), db_session)
    assert resp.text == "Streaming WKAR."


async def test_frequency_unknown_market(stub_sdk, db_session) -> None:
    h = RadioHandler(stub_sdk)
    m = _PLAY_FREQUENCY_RE.match("play 99.9 fm")
    resp = await h._frequency_from_match(m, _ctx(), db_session)
    assert "don't know 99.9 FM" in resp.text


async def test_state_filter_hard_city_preference_soft(
    stub_sdk, db_session, radio_settings
) -> None:
    radio_settings.market_state = "MI"
    radio_settings.market_city = "Lansing"
    # Out-of-state row on the same frequency must lose to nothing.
    await _insert_station(
        db_session, name="KCAL", source="fm", stream_url=None,
        frequency_mhz=97.5, market_city="LOS ANGELES", market_state="CA",
    )
    # In-state, wrong-city row still resolves (city is a preference).
    await _insert_station(
        db_session, name="WDET", source="fm", stream_url=None,
        frequency_mhz=97.5, market_city="DETROIT", market_state="MI",
    )
    stub_sdk.state["sdr_tuner"] = _FakeTuner()
    h = RadioHandler(stub_sdk)
    m = _PLAY_FREQUENCY_RE.match("play 97.5 fm")
    resp = await h._frequency_from_match(m, _ctx(), db_session)
    assert resp.text == "Streaming WDET."


async def test_am_refused(stub_sdk, db_session) -> None:
    h = RadioHandler(stub_sdk)
    m = _PLAY_FREQUENCY_RE.match("play 760 am")
    resp = await h._frequency_from_match(m, _ctx(), db_session)
    assert "can't tune AM" in resp.text


# ─── FM / SDR paths ─────────────────────────────────────────────────────


class _FakeTuner:
    def __init__(self) -> None:
        self.tuned: list[float] = []
        self.stopped = 0
        self.stop_order: list[str] = []

    async def tune(self, freq: float) -> str:
        self.tuned.append(freq)
        return "http://127.0.0.1:6391/fm.mp3"

    async def stop(self) -> None:
        self.stopped += 1
        self.stop_order.append("tuner")


async def test_fm_without_tuner_explains(stub_sdk, db_session) -> None:
    await _insert_station(
        db_session, name="WKAR", source="fm", stream_url=None,
        frequency_mhz=90.5,
    )
    h = RadioHandler(stub_sdk)
    resp = await h._stream_from_match(_STREAM_RE.match("stream wkar"), _ctx(), db_session)
    assert "FM tuner isn't enabled" in resp.text
    assert stub_sdk.playback.calls == []


async def test_fm_with_tuner_tunes_then_plays(stub_sdk, db_session) -> None:
    await _insert_station(
        db_session, name="WKAR", source="fm", stream_url=None,
        frequency_mhz=90.5, call_sign="WKAR",
    )
    tuner = _FakeTuner()
    stub_sdk.state["sdr_tuner"] = tuner
    h = RadioHandler(stub_sdk)
    resp = await h._stream_from_match(_STREAM_RE.match("stream wkar"), _ctx(), db_session)
    assert tuner.tuned == [90.5]
    assert resp.text == "Streaming WKAR."
    (call,) = stub_sdk.playback.calls
    assert call["stream_url"] == "http://127.0.0.1:6391/fm.mp3"


async def test_stop_stops_tuner_before_mpd(stub_sdk, db_session) -> None:
    """Invariant 9: SDR pipeline down FIRST, then MPD — reversing the
    order makes ffmpeg log a spurious unexpected-exit on every stop."""
    order: list[str] = []
    tuner = _FakeTuner()
    tuner.stop_order = order
    stub_sdk.state["sdr_tuner"] = tuner

    original_stop = stub_sdk.playback.stop

    async def _tracking_stop(room_id: str):
        order.append("mpd")
        return await original_stop(room_id)

    stub_sdk.playback.stop = _tracking_stop
    h = RadioHandler(stub_sdk)
    resp = await h._stop_from_match(
        _STOP_STREAM_RE.match("stop the radio"), _ctx(), db_session
    )
    assert resp.text == "Stopped."
    assert resp.music_action == "stop"
    assert order == ["tuner", "mpd"]


async def test_stop_without_tuner_is_idempotent(stub_sdk, db_session) -> None:
    h = RadioHandler(stub_sdk)
    resp = await h._stop_from_match(
        _STOP_STREAM_RE.match("stop streaming"), _ctx(), db_session
    )
    assert resp.music_action == "stop"


# ─── Offline fallback ────────────────────────────────────────────────────


async def test_fallback_offline_resolves_fm_by_frequency(
    stub_sdk, db_session
) -> None:
    await _insert_station(
        db_session, name="WKAR", source="fm", stream_url=None,
        frequency_mhz=90.5,
    )
    stub_sdk.state["sdr_tuner"] = _FakeTuner()
    h = RadioHandler(stub_sdk)
    from domovoi.models import Intent

    resp = await h.fallback_offline(
        Intent(transcript="play 90.5 fm"), _ctx(), db_session
    )
    assert resp.text == "Streaming WKAR."


async def test_fallback_offline_fm_only_name_resolution(
    stub_sdk, db_session
) -> None:
    # An online station must NOT resolve offline; the FM row must.
    await _insert_station(db_session, name="KEXP", source="online")
    await _insert_station(
        db_session, name="WKAR", source="fm", stream_url=None,
        frequency_mhz=90.5,
    )
    stub_sdk.state["sdr_tuner"] = _FakeTuner()
    h = RadioHandler(stub_sdk)
    from domovoi.models import Intent

    resp = await h.fallback_offline(
        Intent(transcript="stream kexp"), _ctx(), db_session
    )
    assert "don't have a favorited station" in resp.text
    resp = await h.fallback_offline(
        Intent(transcript="stream wkar"), _ctx(), db_session
    )
    assert resp.text == "Streaming WKAR."


# ─── Tool-call surface ───────────────────────────────────────────────────


async def test_execute_from_tool_stop(stub_sdk, db_session) -> None:
    h = RadioHandler(stub_sdk)
    resp = await h.execute_from_tool({"action": "stop"}, _ctx(), db_session)
    assert resp.music_action == "stop"


async def test_execute_from_tool_missing_query(stub_sdk, db_session) -> None:
    h = RadioHandler(stub_sdk)
    resp = await h.execute_from_tool({"action": "stream"}, _ctx(), db_session)
    assert resp.text == "Which station?"
