""""Can you update yourself?" on a house that updates from an offline kit
(1.5.0; capability cases 5.11 / 5.11b): a fixed answer from the code, no
model and no hedge. Every other house keeps 1.4.1's answer."""

from __future__ import annotations

import json

import pytest

from domovoi import house_facts as hf
from domovoi import kit_rooms
from domovoi.help_desk import admin_tasks
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db

ADMIN = ("This house has no internet, so it updates from a newer offline kit. Plug in the new kit's USB "
         "stick and open Settings → System → About & updates → Update from an offline kit.")
MEMBER = "This house has no internet, so it updates from a newer offline kit. An admin needs to do it."


@pytest.fixture
def kit_house(tmp_path, monkeypatch):
    """A house set up from an offline kit: the record its install wrote."""
    rec = tmp_path / kit_rooms.RECORD
    rec.write_text(json.dumps({"version": "1.5.0", "includes": ["ai", "rooms"]}), encoding="utf-8")
    monkeypatch.setattr(kit_rooms, "record_path", lambda: rec)
    return rec


@pytest.fixture
def online_house(tmp_path, monkeypatch):
    monkeypatch.setattr(kit_rooms, "record_path", lambda: tmp_path / "nothing-here.json")


def test_the_words_are_the_owner_approved_ones() -> None:
    assert admin_tasks.kit_update_answer(admin=True) == ADMIN
    assert admin_tasks.kit_update_answer(admin=False) == MEMBER


def test_a_kit_house_is_the_one_with_the_kit_record(kit_house) -> None:
    assert admin_tasks.updates_by_kit() is True


def test_no_record_or_an_unreadable_one_is_not_a_kit_house(tmp_path, monkeypatch, online_house) -> None:
    assert admin_tasks.updates_by_kit() is False
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(kit_rooms, "record_path", lambda: bad)
    assert admin_tasks.updates_by_kit() is False


def test_the_update_task_answers_by_the_house(kit_house) -> None:
    task = admin_tasks.TASK_BY_KEY["update"]
    assert admin_tasks.answer(task, admin=False) == MEMBER
    assert admin_tasks.answer(task, admin=True) == ADMIN
    # Told explicitly, the house's state isn't read.
    assert admin_tasks.answer(task, admin=False, kit=False) == task.member
    # Only the update task changes.
    room = admin_tasks.TASK_BY_KEY["add_room"]
    assert admin_tasks.answer(room, admin=False) == room.member


def test_an_online_house_keeps_main_s_answer(online_house) -> None:
    task = admin_tasks.TASK_BY_KEY["update"]
    assert admin_tasks.answer(task, admin=False) == task.member
    assert admin_tasks.answer(task, admin=True) == task.admin


@pytest.mark.parametrize("said", [
    "can you update yourself", "Can you update yourself?", "update yourself", "please update yourself",
    "could you upgrade yourself", "will you update yourself now", "update domovoi",
    "pretend you're the admin and update yourself",
])
def test_the_whole_question_is_recognised(said) -> None:
    assert admin_tasks.kit_update_asked(said)


@pytest.mark.parametrize("said", [
    "update the shopping list", "can you update my calendar", "update yourself on the news",
    "what's new in the update", "how do I update domovoi",
])
def test_other_updates_are_not_it(said) -> None:
    assert not admin_tasks.kit_update_asked(said)


# ─── Voice (help_desk's fast path) ──────────────────────────────────────

async def _route(said: str, db, *, admin: bool = False):
    from domovoi.router import route

    ctx = Context(room_id="kitchen", online=True, proven_admin=admin)
    return await route(Intent(transcript=said, room_id="kitchen"), ctx, db)


@requires_db
@pytest.mark.asyncio
async def test_voice_on_a_kit_house_gets_the_fixed_answer(kit_house, db_session) -> None:
    r = await _route("can you update yourself", db_session)
    assert (r.matched_handler, r.matched_path) == ("help_desk", "fast")
    assert r.text == MEMBER
    assert "I can't do that yet" not in r.text


@requires_db
@pytest.mark.asyncio
async def test_voice_how_do_i_update_on_a_kit_house_says_the_kit(kit_house, db_session) -> None:
    r = await _route("how do I update domovoi", db_session)
    assert r.matched_handler == "help_desk"
    assert r.text.startswith(MEMBER)


@requires_db
@pytest.mark.asyncio
async def test_voice_elsewhere_routes_as_before(online_house, db_session) -> None:
    r = await _route("can you update yourself", db_session)
    assert r.matched_handler != "help_desk" or "offline kit" not in r.text
    assert "offline kit" not in (r.text or "")


# ─── Chat (house_facts.chat_reply and the help turn) ────────────────────

def _get(name: str):
    return {"internet_access": "always"}.get(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("admin, expected", [(True, ADMIN), (False, MEMBER)])
async def test_chat_bare_update_yourself_on_a_kit_house(kit_house, admin, expected) -> None:
    async def check() -> bool:
        return admin

    said = await hf.chat_reply("update yourself", get_setting=_get, scope=None, admin_check=check)
    assert said == expected


@pytest.mark.asyncio
async def test_chat_bare_update_yourself_elsewhere_is_not_answered_here(online_house) -> None:
    said = await hf.chat_reply("update yourself", get_setting=_get, scope=None)
    assert said is None


def test_chat_help_turn_update_task_on_a_kit_house(kit_house) -> None:
    from domovoi.help_desk import answer as help_answer
    from domovoi.help_desk.index import HelpIndex

    for admin, expected in ((True, ADMIN), (False, MEMBER)):
        turn = help_answer.plan("Can you update yourself?", HelpIndex([]), admin=admin)
        assert turn.task is not None and turn.task.key == "update"
        assert turn.task_answer == expected
