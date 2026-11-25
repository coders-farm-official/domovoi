"""Which word each room wakes on, by name (2026-11-15).

The owner wants to see, per room, the wake word it answers to ("wakes on
“Domovoi”"), and only its name: sensitivity is a separate setting. The
word the room device reports wins (the model its health sample says it
loaded, else its hello's); without one, the server's ``room_wake_words``
record: the household default it was given, the word an admin pushed, or
the word it is waiting for (shown as pending). A room older than the
household default on software that reports nothing is "unknown until this
room updates". ``GET /api/satellites`` carries it as ``wakes_on``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from domovoi import satellite_health, wake_default
from domovoi.db.session import session_scope
from web.backend.api import satellites as web_sats

NAMES = {"kitchen_word": "Kitchen Cat"}


# ─── the mapping: each state, the device's word first ────────────────────


@pytest.mark.parametrize("row, slug, name, source, text_", [
    ({"state": "assigned", "slug": "domovoi_v1"}, "domovoi_v1", "Domovoi", "assigned", "Domovoi"),
    ({"state": "chosen", "slug": "kitchen_word"}, "kitchen_word", "Kitchen Cat", "chosen",
     "Kitchen Cat"),
    ({"state": "chosen", "slug": "hey_jarvis"}, "hey_jarvis", "Hey Jarvis", "chosen", "Hey Jarvis"),
    ({"state": "pending", "slug": "domovoi_v1"}, "domovoi_v1", "Domovoi", "pending",
     "Domovoi (pending)"),
])
def test_without_a_report_the_server_s_record_names_the_word(row, slug, name, source, text_):
    out = wake_default.room_word(None, row, NAMES)
    assert (out["slug"], out["name"], out["source"], out["text"]) == (slug, name, source, text_)
    assert out["pending_slug"] is None


@pytest.mark.parametrize("row", [
    None,                                       # no record at all
    {"state": "kept", "slug": None},            # existed before V054, never touched
    {"state": "assigned", "slug": None},        # a record without a word says nothing
])
def test_nothing_known_is_unknown_until_the_room_updates(row):
    out = wake_default.room_word(None, row, NAMES)
    assert out == {"slug": None, "name": None, "source": "unknown", "pending_slug": None,
                   "pending_name": None, "text": "unknown until this room updates",
                   "words": [], "device_max": None, "limit": 1, "max_words": 5}


@pytest.mark.parametrize("row", [
    None,
    {"state": "kept", "slug": None},
    {"state": "assigned", "slug": "domovoi_v1"},
    {"state": "chosen", "slug": "kitchen_word"},
])
def test_the_device_s_own_word_wins_over_any_record(row):
    out = wake_default.room_word("hey_jarvis", row, NAMES)
    assert (out["slug"], out["name"], out["source"], out["text"]) == (
        "hey_jarvis", "Hey Jarvis", "device", "Hey Jarvis")
    assert out["pending_slug"] is None


def test_a_pending_room_reporting_its_boot_word_names_both():
    out = wake_default.room_word("hey_jarvis", {"state": "pending", "slug": "domovoi_v1"})
    assert (out["name"], out["source"], out["pending_slug"], out["pending_name"]) == (
        "Hey Jarvis", "device", "domovoi_v1", "Domovoi")
    assert out["text"] == "Hey Jarvis (waiting to switch to Domovoi)"


def test_a_pending_room_that_already_has_the_word_is_just_on_it():
    out = wake_default.room_word("domovoi_v1", {"state": "pending", "slug": "domovoi_v1"})
    assert (out["text"], out["pending_slug"]) == ("Domovoi", None)


@pytest.mark.parametrize("slug, name", [
    ("domovoi_v1", "Domovoi"),
    ("hey_jarvis", "Hey Jarvis"),
    ("hey_jarvis_v0.1", "Hey Jarvis"),          # a stock model file's stem
    ("alexa_v0.1", "Alexa"),
    ("hey_mycroft", "Hey Mycroft"),
    ("kitchen_word", "Kitchen Cat"),            # a trained word's registry name
    ("porch-light", "Porch Light"),
])
def test_slugs_read_as_names(slug, name):
    assert wake_default.word_name(slug, NAMES) == name


# ─── what counts as the device's report ──────────────────────────────────

T0 = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def test_a_sample_since_it_connected_beats_the_hello():
    assert web_sats._reported_wake_word(("domovoi_v1", T0), "hey_jarvis",
                                        T0 - timedelta(minutes=5)) == "domovoi_v1"


def test_the_hello_beats_a_sample_from_before_it_reconnected():
    # Pushed a new word, restarted, said hello: the old sample is stale.
    assert web_sats._reported_wake_word(("hey_jarvis", T0 - timedelta(minutes=5)), "domovoi_v1",
                                        T0) == "domovoi_v1"


def test_an_older_sample_still_counts_when_the_core_lost_the_hello():
    assert web_sats._reported_wake_word(("domovoi_v1", T0 - timedelta(hours=1)), None,
                                        T0) == "domovoi_v1"


def test_a_sample_without_a_word_leaves_the_hello():
    assert web_sats._reported_wake_word((None, T0), "hey_jarvis", T0 - timedelta(minutes=1)) == "hey_jarvis"
    assert web_sats._reported_wake_word(None, None, T0) is None


def test_a_health_sample_keeps_only_a_slug_shaped_word():
    assert satellite_health.validate_sample({"wake_word": "domovoi_v1", "mic_fps": 12.5})[
        "wake_word"] == "domovoi_v1"
    assert satellite_health.validate_sample({"wake_word": "../etc", "mic_fps": 12.5})[
        "wake_word"] is None
    assert satellite_health.validate_sample({"wake_word": 3, "mic_fps": 12.5})["wake_word"] is None


# ─── GET /api/satellites `wakes_on`, from the database ───────────────────

ROOMS = ("wo_kitchen", "wo_office", "wo_attic", "wo_porch", "wo_shed")


@pytest.fixture
async def wake_rooms(db_session):
    async def clean():
        async with session_scope() as s:
            await s.execute(text("DELETE FROM satellite_health WHERE satellite_id LIKE 'wo\\_%'"))
            await s.execute(text("DELETE FROM room_wake_words WHERE room_id LIKE 'wo\\_%'"))
            await s.execute(text("DELETE FROM wake_words WHERE slug LIKE 'test_wo_%'"))

    await clean()
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO wake_words (name, phrase, slug, status) "
            "VALUES ('Porch Cat', 'porch cat', 'test_wo_porch', 'ready')"))
        for room, state, slug in (("wo_kitchen", "assigned", "domovoi_v1"),
                                  ("wo_office", "chosen", "test_wo_porch"),
                                  ("wo_attic", "kept", None),
                                  ("wo_porch", "pending", "domovoi_v1"),
                                  ("wo_shed", "kept", None)):
            await s.execute(text(
                "INSERT INTO room_wake_words (room_id, state, slug) VALUES (:r, :st, :sl)"),
                {"r": room, "st": state, "sl": slug})
        # The shed's newest sample names the word it loaded; an older one
        # names another; the porch's newest names none (older software).
        for room, word, ago in (("wo_shed", "hey_jarvis", 30), ("wo_shed", "domovoi_v1", 1),
                                ("wo_porch", "hey_jarvis", 30), ("wo_porch", None, 1)):
            sample = {"mic_fps": 12.5, **({"wake_word": word} if word else {})}
            await s.execute(text(
                "INSERT INTO satellite_health (satellite_id, received_at, sample) "
                "VALUES (:r, now() - make_interval(mins => :ago), CAST(:s AS JSONB))"),
                {"r": room, "ago": ago, "s": json.dumps(sample)})
    yield
    await clean()


async def _row(room: str, facts, hello: dict | None = None):
    async def no_np(*a, **kw):
        return None

    web_sats._now_playing_for, saved = no_np, web_sats._now_playing_for
    try:
        snap = {"active_rooms": [room], "satellite_wake_word": hello or {}}
        row = {"room_id": room, "control_port": None, "http_port": None,
               "last_connected_at": datetime.now(timezone.utc) - timedelta(hours=2)}
        return (await web_sats._satellite_for(row, snap, {}, {}, {}, wake_facts=facts)).wakes_on
    finally:
        web_sats._now_playing_for = saved


async def test_every_room_s_line_from_the_database(wake_rooms):
    facts = await web_sats._room_wake_facts_or_empty()
    got = {r: (await _row(r, facts)) for r in ROOMS}
    assert {r: (w.source, w.text) for r, w in got.items()} == {
        "wo_kitchen": ("assigned", "Domovoi"),
        "wo_office": ("chosen", "Porch Cat"),
        "wo_attic": ("unknown", "unknown until this room updates"),
        # Its newest sample says nothing: no report, so the pending record.
        "wo_porch": ("pending", "Domovoi (pending)"),
        # The newest sample's word, not the older one's; over "kept".
        "wo_shed": ("device", "Domovoi"),
    }


async def test_one_room_s_facts_and_the_hello(wake_rooms):
    facts = await web_sats._room_wake_facts_or_empty("wo_attic")
    assert set(facts["states"]) == {"wo_attic"} and facts["samples"] == {}
    # A kept room on software that only says its word in the hello.
    w = await _row("wo_attic", facts, hello={"wo_attic": "hey_jarvis"})
    assert (w.source, w.slug, w.text) == ("device", "hey_jarvis", "Hey Jarvis")


async def test_no_facts_at_all_is_unknown():
    w = await _row("wo_nowhere", {})
    assert (w.source, w.name, w.text) == ("unknown", None, "unknown until this room updates")
