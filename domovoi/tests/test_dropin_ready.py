"""A drop-in's audio waits for a satellite still playing its announcement.

Owner report, 2026-11-12: "Dropping in on the kitchen." was cut off before
the room name. The core sends `dropin_start` after the reply's
`response_end`, while the satellite still has most of the reply queued, and
the call's audio (start chime, then the peer's mic) made the satellite drop
the queued reply (satellite/tests/test_dropin_after_reply.py has that side).

A satellite whose hello says `dropin_ready` now gets a `dropin_start`
marked `await_ready`; until its `dropin_ready` (or DROPIN_READY_WAIT_SEC)
the core sends it nothing of the call: no start chime, no relay from the
peer (a room or a phone). A satellite without it — every one from before —
gets today's frames in today's order.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from domovoi import satellite_abilities as sa
from domovoi import streaming
from domovoi.config import settings
from domovoi.dropin_chimes import END_CHIME_PCM, START_CHIME_PCM
from domovoi.phone_dropin import PhoneDropinSession
from domovoi.streaming import StreamSession
from domovoi.tests.test_dropin import FakeWS, make_app

LOUD = b"\x00\x40" * 160  # a mic frame well above any relay gate


@pytest.fixture(autouse=True)
def _no_silence_watchdog(monkeypatch):
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0.0, raising=False)


def _rooms(*, office_ready: bool, kitchen_ready: bool):
    app = make_app(fd={"office": True, "kitchen": True})
    a = StreamSession(FakeWS(app), "office")
    b = StreamSession(FakeWS(app), "kitchen")
    for sess, ready in ((a, office_ready), (b, kitchen_ready)):
        hello = {"type": "hello", "supports_full_duplex": True}
        if ready:
            hello["dropin_ready"] = True
        sess.abilities = sa.from_hello(hello, room_id=sess.room_id)
        app.state.active_sessions[sess.room_id] = sess
    return app, a, b


def _starts(sess) -> list[dict]:
    return [json.loads(t) for t in sess.ws.sent_text if '"dropin_start"' in t]


def test_the_hello_key_is_negotiated_never_assumed():
    assert sa.from_hello({"type": "hello", "dropin_ready": True}).allows("dropin_ready")
    # Every Pi in the field: a hello without `supports` and without the key.
    assert not sa.from_hello({"type": "hello"}).allows("dropin_ready")
    assert not sa.LEGACY.allows("dropin_ready")
    # A device listing it in `supports` counts too.
    assert sa.from_hello({"type": "hello", "supports": ["dropin_ready"]}).allows("dropin_ready")


async def test_older_satellites_get_todays_frames_in_todays_order():
    app, a, b = _rooms(office_ready=False, kitchen_ready=False)
    await a._begin_dropin(b)
    for sess in (a, b):
        (start,) = _starts(sess)
        assert "await_ready" not in start
        assert sess.ws.sent_bytes == [START_CHIME_PCM]
        assert sess._dropin_awaiting_ready is False
        assert sess._dropin_ready_task is None
    await a._on_audio(LOUD)
    assert b.ws.sent_bytes[-1] == LOUD
    await a._end_dropin(ended_by="office")


async def test_the_announcing_room_hears_nothing_of_the_call_until_dropin_ready():
    # office asked ("Dropping in on the kitchen." still playing there);
    # kitchen is idle, an older satellite.
    app, a, b = _rooms(office_ready=True, kitchen_ready=False)
    await a._begin_dropin(b)

    (start_a,) = _starts(a)
    assert start_a["await_ready"] is True
    assert "await_ready" not in _starts(b)[0]
    assert a.ws.sent_bytes == [], "no chime over the announcement"
    assert b.ws.sent_bytes == [START_CHIME_PCM]

    # The kitchen's open mic is not relayed into the announcement...
    await b._on_audio(LOUD)
    assert a.ws.sent_bytes == []
    # ...while the office's own mic (if anything) still reaches the kitchen.
    await a._on_audio(LOUD)
    assert b.ws.sent_bytes[-1] == LOUD

    # The office finished its reply and is in the call.
    await a._on_control({"type": "dropin_ready"})
    assert a.ws.sent_bytes == [START_CHIME_PCM]
    assert a._dropin_awaiting_ready is False and a._dropin_ready_task is None
    await b._on_audio(LOUD)
    assert a.ws.sent_bytes[-1] == LOUD

    # A second (stray) dropin_ready changes nothing.
    await a._on_control({"type": "dropin_ready"})
    assert a.ws.sent_bytes.count(START_CHIME_PCM) == 1
    await a._end_dropin(ended_by="office")
    assert END_CHIME_PCM in a.ws.sent_bytes and END_CHIME_PCM in b.ws.sent_bytes


async def test_confirm_mode_the_accepting_target_waits_too():
    # The kitchen said "yeah" and hears "Okay, connecting you with the
    # office." — the accept turn pairs initiator (office) with it.
    app, a, b = _rooms(office_ready=True, kitchen_ready=True)
    await a._begin_dropin(b)
    assert a.ws.sent_bytes == [] and b.ws.sent_bytes == []
    await a._on_control({"type": "dropin_ready"})  # office was idle: at once
    assert a.ws.sent_bytes == [START_CHIME_PCM]
    await a._on_audio(LOUD)
    assert b.ws.sent_bytes == [], "the kitchen is still saying 'connecting you'"
    await b._on_control({"type": "dropin_ready"})
    assert b.ws.sent_bytes == [START_CHIME_PCM]
    await a._on_audio(LOUD)
    assert b.ws.sent_bytes[-1] == LOUD
    await a._end_dropin(ended_by="office")


async def test_a_dropin_ready_that_never_comes_opens_the_call_at_the_backstop(monkeypatch):
    monkeypatch.setattr(streaming, "DROPIN_READY_WAIT_SEC", 0.05)
    app, a, b = _rooms(office_ready=True, kitchen_ready=False)
    await a._begin_dropin(b)
    assert a.ws.sent_bytes == []
    await asyncio.sleep(0.2)
    assert a.ws.sent_bytes == [START_CHIME_PCM]
    assert a._dropin_awaiting_ready is False
    await b._on_audio(LOUD)
    assert a.ws.sent_bytes[-1] == LOUD
    await a._end_dropin(ended_by="office")


async def test_a_call_ended_before_dropin_ready_never_chimes_there():
    app, a, b = _rooms(office_ready=True, kitchen_ready=False)
    await a._begin_dropin(b)
    backstop = a._dropin_ready_task
    assert backstop is not None and not backstop.done()
    await b._end_dropin(ended_by="kitchen")
    await asyncio.sleep(0)
    assert backstop.cancelled() or backstop.done()
    assert a.ws.sent_bytes == [], "neither a start nor an end chime"
    assert any('"dropin_end"' in t for t in a.ws.sent_text)
    assert END_CHIME_PCM in b.ws.sent_bytes
    assert a._dropin_awaiting_ready is False and a._dropin_ready_task is None
    # A late dropin_ready from the satellite is harmless.
    await a._on_control({"type": "dropin_ready"})
    assert a.ws.sent_bytes == []


async def test_a_phone_s_audio_waits_for_the_room_too():
    app, _a, b = _rooms(office_ready=False, kitchen_ready=True)
    phone = PhoneDropinSession(FakeWS(app), phone_id="phone-a1", target_room="kitchen")
    await phone._begin_dropin(b)
    assert _starts(b)[0]["await_ready"] is True
    assert b.ws.sent_bytes == []
    await phone._relay(LOUD)
    assert b.ws.sent_bytes == []
    await b._on_control({"type": "dropin_ready"})
    await phone._relay(LOUD)
    assert b.ws.sent_bytes == [START_CHIME_PCM, LOUD]
    await b._end_dropin(ended_by="kitchen")
