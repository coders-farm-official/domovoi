"""Capability N5: a failure is said out loud — never silence, never a fake
success.

* A handler or a plugin that raises: route() answers with
  ``TURN_FAILED_TEXT`` and records the turn (``matched_path="error"``),
  with what the turn wrote before it failed rolled back. A cancellation
  (a barge-in) stays a cancellation.
* ``POST /v1/intent``: the same line in the ordinary reply, not a 500.
* A list change the database refuses: "I couldn't add milk to your
  shopping list. Something went wrong." — never "Added".

The satellite side (the line spoken, error + response_end when it can't
be) is in test_streaming.py.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi import lists as L
from domovoi.handlers import register_handler, unregister_handler
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response
from domovoi.router import TURN_FAILED_TEXT, route
from domovoi.tests.conftest import requires_db

_SAID = "set off the test handler"


class _Boom(Handler):
    """A handler whose fast path raises, after writing to the database."""

    name = "test_boom"
    priority_band = 100
    display = HandlerDisplay(label="Test boom", tone="system")
    requires_network = "no"
    tool_schema = {
        "name": "test_boom",
        "description": "test-only",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }

    def __init__(self, exc: BaseException, *, abort_transaction: bool = False) -> None:
        self.exc = exc
        self.abort_transaction = abort_transaction
        self.fast_paths = [FastPath(re.compile(r"^set off the test handler$"), _Boom._go)]

    async def _go(self, m, ctx, session) -> Response:
        if session is not None:
            await session.execute(text(
                "INSERT INTO lists (name, name_key, position) VALUES ('Half made', 'halfmade', 9)"))
            if self.abort_transaction:
                # A database error: the transaction can't be used until it
                # is rolled back.
                await session.execute(text("SELECT 1/0"))
        raise self.exc

    async def execute(self, intent, ctx, session) -> Response:
        return Response(text="unused")


class _PluginBoom(_Boom):
    name = "test_plugin_boom"
    plugin_slug = "test_plugin_boom"


@pytest.fixture
def boom():
    made: list[Handler] = []

    def make(exc: BaseException, cls=_Boom, **kw) -> Handler:
        h = cls(exc, **kw)
        register_handler(h)
        made.append(h)
        return h

    yield make
    for h in made:
        unregister_handler(h)


# ─── route(), DB-free ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_line_comes_back_even_when_nothing_can_be_recorded(boom) -> None:
    """No usable database (the session is None, so the turn fails before
    any handler and its record fails too): the line still comes back."""
    boom(RuntimeError("handler bug: secret internals"))
    r = await route(Intent(transcript=_SAID, room_id="kitchen"), Context(room_id="kitchen", online=True), None)
    assert r.text == TURN_FAILED_TEXT
    assert r.matched_path == "error"
    assert r.matched_handler is None
    assert r.expect_followup is False


# ─── route(), against the database ───────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_cancelled_turn_is_not_turned_into_a_spoken_failure(boom, db_session) -> None:
    """A barge-in cancels the turn: CancelledError goes on up, untouched."""
    boom(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await route(Intent(transcript=_SAID, room_id="kitchen"),
                    Context(room_id="kitchen", online=True), db_session)


async def _last_rows(db) -> tuple[tuple, tuple]:
    intent = (await db.execute(text(
        "SELECT transcript, matched_handler, matched_path FROM intents_log ORDER BY id DESC LIMIT 1"
    ))).one()
    convo = (await db.execute(text(
        "SELECT user_text, assistant_text, matched_path FROM conversation_log ORDER BY id DESC LIMIT 1"
    ))).one()
    return tuple(intent), tuple(convo)


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [_Boom, _PluginBoom], ids=["core", "plugin"])
@pytest.mark.parametrize("abort", [False, True], ids=["bug", "db_error"])
async def test_a_raising_handler_is_said_and_recorded(boom, db_session, cls, abort) -> None:
    boom(RuntimeError("kaput"), cls=cls, abort_transaction=abort)
    r = await route(Intent(transcript=_SAID, room_id="kitchen"), Context(room_id="kitchen", online=True), db_session)
    await db_session.commit()
    assert (r.text, r.matched_path) == (TURN_FAILED_TEXT, "error")
    assert "kaput" not in r.text and "boom" not in r.text
    assert r.session_id is not None
    # One intents_log and one conversation_log row, as for every turn.
    intent, convo = await _last_rows(db_session)
    assert intent == (_SAID, None, "error")
    assert convo == (_SAID, TURN_FAILED_TEXT, "error")
    # What the failed turn wrote is not kept.
    assert await L.find_list(db_session, "half made") is None


# ─── POST /v1/intent ──────────────────────────────────────────────────────


@requires_db
def test_v1_intent_answers_a_raising_handler(boom) -> None:
    from domovoi import main as core_main

    boom(RuntimeError("kaput"))
    with TestClient(core_main.app, headers={"X-Requested-With": "domovoi-tests"}) as c:
        r = c.post("/v1/intent", json={"transcript": _SAID, "room_id": "kitchen"})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == TURN_FAILED_TEXT
    assert r.json()["matched_path"] == "error"


@requires_db
def test_v1_intent_answers_a_failure_outside_the_router(monkeypatch, caplog) -> None:
    """What route() can't answer itself (the commit, the database gone) is
    the same line, in the ordinary reply shape, and logged — not a 500."""
    from domovoi import main as core_main

    async def _route(intent, ctx, s):
        raise RuntimeError("database went away")

    monkeypatch.setattr(core_main, "route", _route)
    with TestClient(core_main.app, headers={"X-Requested-With": "domovoi-tests"}) as c:
        r = c.post("/v1/intent", json={"transcript": "what time is it", "room_id": "kitchen"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == TURN_FAILED_TEXT
    assert body["matched_path"] == "error"
    assert "database" not in r.text
    assert any("database went away" in (rec.exc_text or "") or rec.exc_info for rec in caplog.records)


# ─── A list change the database refuses ───────────────────────────────────


async def _seed(db) -> None:
    await db.execute(text(
        "INSERT INTO lists (name, name_key, position) VALUES ('To-do', 'todo', 1), ('Shopping', 'shopping', 2)"
    ))
    await db.commit()


def _refuse(monkeypatch, name: str) -> None:
    """``L.<name>`` fails the way a database does: the statement errors and
    the transaction is aborted."""
    async def refused(session, *a, **kw):
        await session.execute(text("SELECT 1/0"))

    monkeypatch.setattr(L, name, refused)


@pytest.fixture
async def voice(db_session):
    await _seed(db_session)
    sessions: dict[str, object] = {}

    async def say(utterance: str):
        ctx = Context(room_id="kitchen", session_id=sessions.get("kitchen"), online=True)
        resp = await route(Intent(transcript=utterance, room_id="kitchen"), ctx, db_session)
        sessions["kitchen"] = resp.session_id
        return resp

    say.db = db_session
    return say


async def _assert_recorded(db, said: str, reply: str) -> None:
    intent, convo = await _last_rows(db)
    assert intent == (said, "lists", "fast")
    assert convo == (said, reply, "fast")


@requires_db
@pytest.mark.asyncio
async def test_a_failed_add_says_so_and_adds_nothing(voice, monkeypatch) -> None:
    _refuse(monkeypatch, "add_item")
    r = await voice("add milk to the shopping list")
    assert r.text == "I couldn't add milk to your shopping list. Something went wrong."
    assert "Added" not in r.text
    await _assert_recorded(voice.db, "add milk to the shopping list", r.text)
    lst = await L.find_list(voice.db, "shopping")
    assert await L.items_of(voice.db, lst.id) == []


@requires_db
@pytest.mark.asyncio
async def test_a_failed_add_of_several_keeps_none_of_them(voice, monkeypatch) -> None:
    real = L.add_item
    calls = {"n": 0}

    async def second_fails(session, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            await session.execute(text("SELECT 1/0"))
        return await real(session, *a, **kw)

    monkeypatch.setattr(L, "add_item", second_fails)
    r = await voice("add milk, eggs and bread to the shopping list")
    assert r.text == "I couldn't add milk, eggs, and bread to your shopping list. Something went wrong."
    lst = await L.find_list(voice.db, "shopping")
    assert await L.items_of(voice.db, lst.id) == []


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(("refused", "said", "reply"), [
    ("delete_item", "remove milk from the shopping list",
     "I couldn't remove milk from your shopping list. Something went wrong."),
    ("update_item", "cross milk off the shopping list",
     "I couldn't cross milk off your shopping list. Something went wrong."),
])
async def test_a_failed_item_change_says_so(voice, monkeypatch, refused, said, reply) -> None:
    assert (await voice("add milk to the shopping list")).text == "Added milk to your shopping list."
    _refuse(monkeypatch, refused)
    r = await voice(said)
    assert r.text == reply
    await _assert_recorded(voice.db, said, reply)
    lst = await L.find_list(voice.db, "shopping")
    assert [(i.text, i.done) for i in await L.items_of(voice.db, lst.id)] == [("Milk", False)]


@requires_db
@pytest.mark.asyncio
async def test_a_failed_clear_of_crossed_off_things_says_so(voice, monkeypatch) -> None:
    await voice("add milk to the shopping list")
    await voice("cross milk off the shopping list")
    _refuse(monkeypatch, "clear_items")
    r = await voice("clear the crossed off items from my shopping list")
    assert r.text == ("I couldn't clear the crossed-off things off your shopping list. "
                      "Something went wrong.")


@requires_db
@pytest.mark.asyncio
async def test_a_failed_clear_after_yes_says_so(voice, monkeypatch) -> None:
    await voice("add milk to the shopping list")
    assert (await voice("clear the shopping list")).expect_followup
    _refuse(monkeypatch, "clear_items")
    r = await voice("yes")
    assert r.text == "I couldn't clear your shopping list. Something went wrong."
    lst = await L.find_list(voice.db, "shopping")
    assert len(await L.items_of(voice.db, lst.id)) == 1


@requires_db
@pytest.mark.asyncio
async def test_failed_list_making_and_deleting_say_so(voice, monkeypatch) -> None:
    _refuse(monkeypatch, "create_list")
    r = await voice("make a list called camping")
    assert r.text == "I couldn't make your camping list. Something went wrong."
    assert await L.find_list(voice.db, "camping") is None
    monkeypatch.undo()

    assert (await voice("delete the shopping list")).expect_followup
    _refuse(monkeypatch, "delete_list")
    r = await voice("yes")
    assert r.text == "I couldn't delete your shopping list. Something went wrong."
    assert await L.find_list(voice.db, "shopping") is not None
