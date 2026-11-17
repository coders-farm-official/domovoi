"""Several wake words per room (1.4.0, V063; domovoi/wake_default.py).

A room may wake on up to five words, main word first. What this pins:

* the migration keeps every existing room's word as its first (and only)
  entry, and the database refuses a list that doesn't start with the main
  word or is longer than five;
* the server never sends a device more words than its hello said it can
  run, and a device that didn't say (older software) is sent its main word
  only, in the frame it has always understood;
* which word woke a turn reaches the turn's history row and the room's
  history on the dashboard;
* the dashboard's row lists the words, which ones the device runs, and how
  many it can.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text

from domovoi import satellite_abilities, streaming, turn_history, wake_default
from domovoi.db.session import session_scope
from domovoi.streaming import StreamSession

MIGRATION = (Path(__file__).resolve().parents[1] / "db" / "migrations"
             / "V063__room_wake_word_lists.sql")
ROOMS = ("mw_kitchen", "mw_attic", "mw_porch", "mw_office")


# ─── the hello's fields ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, got", [
    (1, 1), (3, 3), (5, 5), (9, 5), (3.0, 3),
    (0, None), (-2, None), (2.5, None), (True, None), ("3", None), (None, None),
    (float("nan"), None),
])
def test_a_device_s_limit_is_a_whole_number_up_to_five(raw, got):
    assert wake_default.device_max(raw) == got


def test_a_device_that_does_not_say_runs_one_word():
    assert wake_default.device_limit(None) == 1
    assert wake_default.device_limit(3) == 3


def test_the_reported_words_are_cleaned():
    assert wake_default.reported_words(["domovoi_v1", "hey_jarvis", "domovoi_v1", "../x", 7]) == [
        "domovoi_v1", "hey_jarvis"]
    assert wake_default.reported_words([f"w{i}" for i in range(9)]) == [f"w{i}" for i in range(5)]
    assert wake_default.reported_words("hey_jarvis") is None
    assert wake_default.reported_words([]) is None


@pytest.mark.parametrize("raw, why", [
    ([], "at least one"),
    ("hey_jarvis", "at least one"),
    (["hey_jarvis", "hey_jarvis"], "twice"),
    (["hey jarvis"], "isn't a wake word"),
    ([f"w{i}" for i in range(6)], "at most 5"),
])
def test_an_admin_list_is_checked(raw, why):
    with pytest.raises(ValueError, match=why):
        wake_default.check_word_list(raw)


def test_a_word_pushed_the_old_way_becomes_the_main_word_and_keeps_the_extras():
    assert wake_default.with_main_word(None, "porch_cat") == ["porch_cat"]
    assert wake_default.with_main_word(["domovoi_v1", "hey_jarvis"], "porch_cat") == [
        "porch_cat", "hey_jarvis"]
    # Already an extra: it moves to the front, once.
    assert wake_default.with_main_word(["domovoi_v1", "porch_cat"], "porch_cat") == ["porch_cat"]


# ─── the server caps what it sends ─────────────────────────────────────────

class FakeWS:
    def __init__(self) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace())
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))


WORDS = [{"slug": "domovoi_v1", "threshold": None},
         {"slug": "porch_cat", "threshold": 0.62},
         {"slug": "hey_jarvis", "threshold": None},
         {"slug": "kitty", "threshold": 0.55},
         {"slug": "house_cat", "threshold": None}]


def _session(wake_max: int | None) -> tuple[StreamSession, FakeWS]:
    ws = FakeWS()
    session = StreamSession(ws, "mw_kitchen")  # type: ignore[arg-type]
    session.wake_max = wake_max
    return session, ws


async def test_an_older_device_is_sent_its_main_word_only():
    session, ws = _session(None)
    await session.request_set_wake_word(slug="domovoi_v1", threshold=None, words=WORDS)
    assert ws.sent == [{"type": "set_wake_word", "slug": "domovoi_v1"}]


@pytest.mark.parametrize("limit", [1, 3, 5])
async def test_a_device_is_never_sent_more_words_than_it_can_run(limit):
    session, ws = _session(limit)
    await session.request_set_wake_word(slug="domovoi_v1", threshold=None, words=WORDS)
    frame = ws.sent[0]
    assert frame["slug"] == "domovoi_v1"
    assert [w["slug"] for w in frame["words"]] == [w["slug"] for w in WORDS[:limit]]
    # Each word keeps its own threshold; a word without one says nothing.
    assert frame["words"][0] == {"slug": "domovoi_v1"}
    if limit > 1:
        assert frame["words"][1] == {"slug": "porch_cat", "threshold": 0.62}


async def test_a_single_word_push_is_the_frame_it_always_was():
    session, ws = _session(5)
    await session.request_set_wake_word(slug="porch_cat", threshold=0.62)
    assert ws.sent == [{"type": "set_wake_word", "slug": "porch_cat", "threshold": 0.62}]


async def test_the_hello_keeps_the_device_s_words_and_limit():
    state = SimpleNamespace()
    ws = FakeWS()
    ws.app.state = state
    session = StreamSession(ws, "mw_kitchen")  # type: ignore[arg-type]
    session.wake_words_reported = wake_default.reported_words(["domovoi_v1", "hey_jarvis"])
    session.wake_max = wake_default.device_max(3)
    assert (session.wake_words_reported, session.wake_max) == (["domovoi_v1", "hey_jarvis"], 3)
    # The hello code itself: both fields are read and kept in the app state
    # the dashboard's snapshot carries.
    import inspect
    src = inspect.getsource(StreamSession._on_control)
    assert 'wake_default.reported_words(ctrl.get("wake_words"))' in src
    assert 'wake_default.device_max(ctrl.get("wake_words_max"))' in src
    assert "satellite_wake_max" in src and "satellite_wake_words" in src


def test_the_snapshot_carries_the_words_and_limits():
    import inspect

    from domovoi import main as core_main
    src = inspect.getsource(core_main.admin_snapshot)
    assert '"satellite_wake_words"' in src and '"satellite_wake_max"' in src


# ─── the dashboard's row ───────────────────────────────────────────────────

def test_one_word_is_one_word():
    out = wake_default.room_word("domovoi_v1", {"state": "assigned", "slug": "domovoi_v1"},
                                 reported_list=["domovoi_v1"], declared_max=3)
    assert out["words"] == [{"slug": "domovoi_v1", "name": "Domovoi", "running": True}]
    assert (out["text"], out["device_max"], out["limit"], out["max_words"]) == (
        "Domovoi", 3, 3, 5)


def test_the_words_the_device_runs_and_the_ones_it_does_not():
    row = {"state": "chosen", "slug": "domovoi_v1",
           "slugs": ["domovoi_v1", "hey_jarvis", "porch_cat"], "device_max": 2}
    out = wake_default.room_word("domovoi_v1", row, {"porch_cat": "Porch Cat"},
                                 reported_list=["domovoi_v1", "hey_jarvis"], declared_max=2)
    assert [(w["name"], w["running"]) for w in out["words"]] == [
        ("Domovoi", True), ("Hey Jarvis", True), ("Porch Cat", False)]
    assert out["text"] == "Domovoi (also Hey Jarvis, Porch Cat)"


def test_an_older_device_offline_runs_its_main_word():
    row = {"state": "chosen", "slug": "domovoi_v1", "slugs": ["domovoi_v1", "hey_jarvis"],
           "device_max": None}
    out = wake_default.room_word(None, row)
    assert [(w["slug"], w["running"]) for w in out["words"]] == [
        ("domovoi_v1", True), ("hey_jarvis", False)]
    assert (out["device_max"], out["limit"]) == (None, 1)


def test_the_kept_limit_is_used_while_the_room_is_offline():
    row = {"state": "chosen", "slug": "domovoi_v1", "slugs": ["domovoi_v1", "hey_jarvis"],
           "device_max": 3}
    out = wake_default.room_word(None, row)
    assert all(w["running"] for w in out["words"]) and out["limit"] == 3


# ─── the database ─────────────────────────────────────────────────────────

async def _clean() -> None:
    async with session_scope() as s:
        await s.execute(text("DELETE FROM room_wake_words WHERE room_id LIKE 'mw\\_%'"))
        await s.execute(text("DELETE FROM wake_words WHERE slug LIKE 'test_mw_%'"))
        await s.execute(text("DELETE FROM conversation_log WHERE room_id LIKE 'mw\\_%'"))


@pytest_asyncio.fixture
async def rooms():
    await _clean()
    yield
    await _clean()


def _statements(sql: str) -> list[str]:
    sql = re.sub(r"--[^\n]*", "", sql)
    return [st.strip() for st in sql.split(";") if st.strip()]


async def test_the_migration_keeps_every_room_s_word_as_its_first(rooms):
    async with session_scope() as s:
        # Rooms as V054 left them: a word, or none ('kept').
        for room, state, slug in (("mw_kitchen", "assigned", "domovoi_v1"),
                                  ("mw_office", "chosen", "test_mw_porch"),
                                  ("mw_porch", "pending", "domovoi_v1"),
                                  ("mw_attic", "kept", None)):
            await s.execute(text(
                "INSERT INTO room_wake_words (room_id, state, slug, slugs) "
                "VALUES (:r, :st, :sl, NULL)"), {"r": room, "st": state, "sl": slug})
    async with session_scope() as s:
        for st in _statements(MIGRATION.read_text(encoding="utf-8")):
            await s.execute(text(st))
    async with session_scope() as s:
        got = {r: (row["state"], row["slug"], row["slugs"], row["device_max"])
               for r, row in (await wake_default.room_states(s)).items() if r in ROOMS}
    assert got == {
        "mw_kitchen": ("assigned", "domovoi_v1", ["domovoi_v1"], None),
        "mw_office": ("chosen", "test_mw_porch", ["test_mw_porch"], None),
        "mw_porch": ("pending", "domovoi_v1", ["domovoi_v1"], None),
        # Its device keeps whatever it had: nothing to copy.
        "mw_attic": ("kept", None, None, None),
    }


@pytest.mark.parametrize("slug, slugs", [
    ("domovoi_v1", ["hey_jarvis", "domovoi_v1"]),        # the main word isn't first
    ("domovoi_v1", ["domovoi_v1"] + [f"w{i}" for i in range(5)]),   # six
    ("domovoi_v1", []),
])
async def test_the_database_refuses_a_list_that_does_not_fit(rooms, slug, slugs):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with session_scope() as s:
            await s.execute(text(
                "INSERT INTO room_wake_words (room_id, state, slug, slugs) "
                "VALUES ('mw_kitchen', 'chosen', :sl, :sls)"), {"sl": slug, "sls": slugs})


async def test_a_chosen_list_and_the_device_s_limit_are_kept(rooms):
    async with session_scope() as s:
        await wake_default.mark_chosen(s, "mw_kitchen", "domovoi_v1",
                                       ["domovoi_v1", "hey_jarvis"])
        await wake_default.record_device_max(s, "mw_kitchen", 3)
    async with session_scope() as s:
        row = await wake_default.room_state(s, "mw_kitchen")
    assert (row["state"], row["slugs"], row["device_max"]) == (
        "chosen", ["domovoi_v1", "hey_jarvis"], 3)
    # A single-word write (the household default) is a one-word list.
    async with session_scope() as s:
        await wake_default._put(s, "mw_kitchen", "assigned", "hey_jarvis")
        row = await wake_default.room_state(s, "mw_kitchen")
    assert (row["slugs"], row["device_max"]) == (["hey_jarvis"], 3)


async def test_a_hello_keeps_the_room_s_limit(rooms):
    async def push(slug, threshold):
        raise AssertionError("nothing to push: the room already has its word")

    async with session_scope() as s:
        await wake_default.mark_chosen(s, "mw_attic", "hey_jarvis")
    assert await wake_default.on_hello("mw_attic", "hey_jarvis", True, push,
                                       wake_max=5, record_max=True) == "chosen"
    async with session_scope() as s:
        assert (await wake_default.room_state(s, "mw_attic"))["device_max"] == 5
    # An older device's hello (no limit) says it runs one.
    await wake_default.on_hello("mw_attic", "hey_jarvis", True, push, wake_max=None,
                                record_max=True)
    async with session_scope() as s:
        assert (await wake_default.room_state(s, "mw_attic"))["device_max"] is None


# ─── setting a room's words (PUT /v1/admin/wake/room) ──────────────────────

class Target:
    """A connected room: what its hello said, and what it was sent."""

    def __init__(self, wake_max: int | None) -> None:
        self.room_id = "mw_kitchen"
        self.board = None
        self.wake_max = wake_max
        self.abilities = satellite_abilities.LEGACY
        self.sent: list[dict[str, Any]] = []

    async def request_set_wake_word(self, *, slug, threshold=None, words=None):
        self.sent.append({"slug": slug, "threshold": threshold, "words": words})


@pytest_asyncio.fixture
async def connected(rooms, monkeypatch):
    from domovoi import main as core_main

    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO wake_words (name, phrase, slug, status, model_ref, threshold) VALUES "
            "('Porch Cat', 'porch cat', 'test_mw_porch', 'ready', 'test_mw_porch.onnx', 0.62), "
            "('Half Cat', 'half cat', 'test_mw_half', 'training', NULL, 0.5)"))
    sessions: dict[str, Any] = {}
    monkeypatch.setattr(core_main.app.state, "active_sessions", sessions, raising=False)
    return core_main, sessions


async def test_an_admin_sets_a_room_s_words(connected):
    core_main, sessions = connected
    sessions["mw_kitchen"] = target = Target(3)
    out = await core_main.admin_wake_room(core_main._AdminWakeRoomBody(
        room_id="mw_kitchen", slugs=["test_mw_porch", "hey_jarvis"]))
    assert out == {"pushed": True, "room_id": "mw_kitchen",
                   "slugs": ["test_mw_porch", "hey_jarvis"], "limit": 3}
    assert target.sent == [{"slug": "test_mw_porch", "threshold": 0.62, "words": [
        {"slug": "test_mw_porch", "threshold": 0.62}, {"slug": "hey_jarvis", "threshold": None}]}]
    async with session_scope() as s:
        row = await wake_default.room_state(s, "mw_kitchen")
    assert (row["state"], row["slugs"]) == ("chosen", ["test_mw_porch", "hey_jarvis"])


@pytest.mark.parametrize("wake_max, slugs, says", [
    (None, ["hey_jarvis", "test_mw_porch"], "can run 1 wake word: its software is older"),
    (3, ["hey_jarvis", "test_mw_porch", "w3", "w4"], "can run 3 wake words"),
])
async def test_more_words_than_the_device_runs_are_refused(connected, wake_max, slugs, says):
    core_main, sessions = connected
    sessions["mw_kitchen"] = target = Target(wake_max)
    with pytest.raises(HTTPException) as e:
        await core_main.admin_wake_room(core_main._AdminWakeRoomBody(
            room_id="mw_kitchen", slugs=slugs))
    assert e.value.status_code == 409 and says in e.value.detail
    assert target.sent == []


async def test_a_word_that_is_not_ready_is_refused(connected):
    core_main, sessions = connected
    sessions["mw_kitchen"] = target = Target(5)
    with pytest.raises(HTTPException) as e:
        await core_main.admin_wake_room(core_main._AdminWakeRoomBody(
            room_id="mw_kitchen", slugs=["hey_jarvis", "test_mw_half"]))
    assert e.value.status_code == 409 and "isn't trained yet" in e.value.detail
    assert target.sent == []


async def test_an_offline_room_is_not_found(connected):
    core_main, _sessions = connected
    with pytest.raises(HTTPException) as e:
        await core_main.admin_wake_room(core_main._AdminWakeRoomBody(
            room_id="mw_kitchen", slugs=["hey_jarvis"]))
    assert e.value.status_code == 404


async def test_pushing_one_word_keeps_the_room_s_extras(connected):
    """Wake words → Push (one trained word) replaces the main word and keeps
    the extras an admin added; the device is sent the whole list."""
    core_main, sessions = connected
    sessions["mw_kitchen"] = target = Target(3)
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO wake_words (name, phrase, slug, status, model_ref, threshold) VALUES "
            "('Kit', 'kit', 'test_mw_kit', 'ready', 'test_mw_kit.onnx', 0.7)"))
        kit = (await s.execute(text("SELECT id FROM wake_words WHERE slug = 'test_mw_kit'"))).scalar()
        await wake_default.mark_chosen(s, "mw_kitchen", "hey_jarvis", ["hey_jarvis", "test_mw_porch"])
    await core_main.admin_wake_push(core_main._AdminWakePushBody(
        room_id="mw_kitchen", wake_word_id=kit))
    assert target.sent == [{"slug": "test_mw_kit", "threshold": pytest.approx(0.7), "words": [
        {"slug": "test_mw_kit", "threshold": 0.7}, {"slug": "test_mw_porch", "threshold": 0.62}]}]
    async with session_scope() as s:
        assert (await wake_default.room_state(s, "mw_kitchen"))["slugs"] == [
            "test_mw_kit", "test_mw_porch"]


# ─── which word woke the turn ─────────────────────────────────────────────

async def test_the_word_that_woke_a_turn_is_in_its_history(rooms):
    from web.backend.api import satellites as sat_api

    turn_history._forget_probe()
    sat_api._HAS_WAKE_WORD = None
    for word, said in (("hey_jarvis", "what time is it"), ("domovoi_v1", "play some jazz")):
        entry = await turn_history.open_entry(
            room_id="mw_kitchen", session_id=None, user_text=said, trigger="wake_word",
            audio_ms=1500, at=datetime.now(timezone.utc), wake_score=0.91, wake_word=word)
        assert entry is not None
        await turn_history.close_entry(entry, turn_history.OVERHEARD, room_id="mw_kitchen")
    await turn_history.record_unheard(
        room_id="mw_kitchen", session_id=None, trigger="wake_word", audio_ms=900,
        reason=turn_history.TIMED_OUT, at=datetime.now(timezone.utc), wake_word="hey_jarvis")
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT user_text, wake_word FROM conversation_log WHERE room_id = 'mw_kitchen' "
            "ORDER BY id"))).all()
    assert [tuple(r) for r in rows] == [("what time is it", "hey_jarvis"),
                                        ("play some jazz", "domovoi_v1"), ("", "hey_jarvis")]
    turns = await sat_api.list_conversations("mw_kitchen", limit=10)
    assert sorted((t.wake_word, t.wake_word_name) for t in turns) == [
        ("domovoi_v1", "Domovoi"), ("hey_jarvis", "Hey Jarvis"), ("hey_jarvis", "Hey Jarvis")]


async def test_the_utterance_start_s_word_rides_with_the_turn():
    """`utterance_start.wake_word` is latched with the capture and passed to
    every history write the turn makes, next to the wake score."""
    import inspect

    src = inspect.getsource(streaming)
    assert src.count("wake_score=self._wake_score, wake_word=self._wake_fired") == 2
    assert src.count("wake_score=wake_score, wake_word=wake_word") >= 6
    on_control = inspect.getsource(StreamSession._on_control)
    assert 'wake_default.clean_slug(ctrl.get("wake_word"))' in on_control
