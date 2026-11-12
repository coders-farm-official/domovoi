"""ListsHandler + domovoi/lists.py: the household's lists by voice (V048).

The live bug (2026-11-11): "Add tarp the trailer to my to-do list." had no
handler, reached the answer model, and the model said it had added it.
Nothing was kept. These tests hold the handler to keeping it, to the
phrasings it takes, and to the music phrasings it must leave alone.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from domovoi import lists as L
from domovoi.db.repositories import SessionRepository
from domovoi.handlers.lists import CLEAR_KIND, CREATE_KIND, ListsHandler, split_items
from domovoi.models import Context, Intent
from domovoi.tests.conftest import fast_path_winner, requires_db


async def seed_defaults(db_session) -> None:
    """The two lists V048 makes (the per-test TRUNCATE takes them away)."""
    await db_session.execute(text(
        "INSERT INTO lists (name, name_key, position) VALUES ('To-do', 'todo', 1), ('Shopping', 'shopping', 2)"
    ))
    await db_session.commit()


# ─── Names and items (DB-free) ────────────────────────────────────────────


@pytest.mark.parametrize(("spoken", "key"), [
    ("to-do", "todo"), ("to do", "todo"), ("todo", "todo"), ("To-Dos", "todo"),
    ("things to do", "todo"), ("To-do list", "todo"),
    ("shopping", "shopping"), ("grocery", "shopping"), ("Groceries", "shopping"),
    ("Camping", "camping"), ("camping list", "camping"), ("Christmas gifts", "christmasgifts"),
])
def test_name_keys(spoken: str, key: str) -> None:
    assert L.name_key(spoken) == key


def test_a_trailing_list_is_not_part_of_the_name() -> None:
    assert L.clean_name("  Camping   list ") == "Camping"
    assert L.clean_name("List") == "List"           # nothing left: kept


@pytest.mark.parametrize(("said", "items"), [
    ("milk", ["milk"]),
    ("mac and cheese", ["mac and cheese"]),                     # no comma: one item
    ("milk, eggs and bread", ["milk", "eggs", "bread"]),
    ("milk, eggs, and bread", ["milk", "eggs", "bread"]),
    ("milk, mac and cheese", ["milk", "mac", "cheese"]),        # documented limit
])
def test_split_items(said: str, items: list[str]) -> None:
    assert split_items(said) == items


def test_item_keys_drop_articles_and_case() -> None:
    assert L.item_key("The Milk") == L.item_key("milk") == "milk"
    assert L.item_key("some eggs!") == "eggs"


def test_spoken_names() -> None:
    assert L.spoken_name("To-do") == "to-do"
    assert L.spoken_name("Camping") == "camping"
    assert L.spoken_name("BBQ") == "BBQ"


# ─── Routing (DB-free) ────────────────────────────────────────────────────


@pytest.mark.parametrize("said", [
    "Add tarp the trailer to my to-do list.",
    "add milk to the shopping list", "put milk on the shopping list",
    "add milk, eggs and bread to the grocery list", "add eggs to my groceries list",
    "add go to the bank to my to-do list", "add call mom to the todo list",
    "add milk to my to do list", "add milk to my list", "write down call the vet on my to-do list",
    "add to my to-do list call the plumber", "can you add milk to the shopping list please",
    "what's on my to-do list", "What's on my to-do list?", "what is on the shopping list",
    "read me my shopping list", "read me the shopping list", "what's on the list",
    "how many things are on my to-do list", "is there anything on my to-do list",
    "is milk on the shopping list",
    "what lists do I have", "what are my lists",
    "cross milk off the shopping list", "cross off milk", "check off tarp the trailer",
    "mark milk as done", "tick milk off",
    "remove milk from the shopping list", "take milk off the list",
    "clear the shopping list", "clear the crossed off items from my to-do list",
    "make a list called camping", "create a camping list", "delete the camping list",
])
def test_list_phrasings_are_the_lists_fast_paths(said: str) -> None:
    assert fast_path_winner(said) == "lists", said


@pytest.mark.parametrize(("said", "owner"), [
    # Music keeps its own: playlists, the queue, the library.
    ("add this to my road trip playlist", "playlist"),
    ("add it to my favorites playlist", "playlist"),
    ("make a new playlist called road trip", "playlist"),
    ("play my chill list", "music"),
    ("play the shopping list by the band", "music"),
    # And everything else that sounds a little like it.
    ("add the dentist to my calendar", "calendar"),
    ("what's on my calendar today", "calendar"),
    ("read me the lease", "documents"),
    ("list my reminders", "reminder"),
    ("what can you do", "help"),
])
def test_lists_poaches_nothing(said: str, owner: str) -> None:
    assert fast_path_winner(said) == owner, said


@pytest.mark.parametrize("said", [
    "add this song to my playlist", "add this song to my workout playlist",
    "add bohemian rhapsody to the queue",
    "add it to my library", "add this to the queue", "check the weather",
    "remind me to buy milk", "put on some music",
])
def test_music_and_other_adds_never_reach_lists(said: str) -> None:
    assert fast_path_winner(said) != "lists", said


@pytest.mark.asyncio
@pytest.mark.parametrize("said", [
    "add this song to my play list",       # a playlist heard as two words
    "add this song to my music list",
    "cross it off",                        # no item to look for
])
async def test_declined_phrasings_route_on(said: str) -> None:
    from domovoi.router import plan_route

    plan = plan_route(said)
    assert plan is not None and plan.handler.name == "lists"
    ctx = Context(session_id=uuid4(), room_id="kitchen")
    assert await plan.fast_path.method(plan.handler, plan.match, ctx, None) is None


def test_the_tool_is_offered_only_when_a_list_is_named() -> None:
    h = ListsHandler()
    assert h.offers_tool("add milk to the shopping list")
    assert h.offers_tool("what's on my todo")
    assert h.offers_tool("we need groceries")
    assert not h.offers_tool("who wrote the odyssey")
    assert not h.offers_tool("add this song to my playlist")


# ─── Through the router, against the database ─────────────────────────────


@pytest.fixture
async def voice(db_session):
    await seed_defaults(db_session)
    sessions: dict[str, object] = {}

    async def say(utterance: str, room: str = "kitchen", person: int | None = None):
        from domovoi.router import route

        ctx = Context(room_id=room, session_id=sessions.get(room), person_id=person, online=True)
        resp = await route(Intent(transcript=utterance, room_id=room), ctx, db_session)
        sessions[room] = resp.session_id
        return resp

    say.db = db_session
    say.sessions = sessions
    return say


async def _items(db, name: str) -> list[tuple[str, bool]]:
    lst = await L.find_list(db, name)
    assert lst is not None, name
    return [(i.text, i.done) for i in await L.items_of(db, lst.id)]


@requires_db
@pytest.mark.asyncio
async def test_the_live_bug_is_kept_and_said(voice) -> None:
    alex = (await voice.db.execute(text("INSERT INTO people (name) VALUES ('Alex') RETURNING id"))).scalar_one()
    r = await voice("Add tarp the trailer to my to-do list.", person=alex)
    assert (r.matched_handler, r.matched_path) == ("lists", "fast")
    assert r.text == "Added tarp the trailer to your to-do list."
    lst = await L.find_list(voice.db, "to-do")
    [item] = await L.items_of(voice.db, lst.id)
    assert (item.text, item.done, item.added_via, item.added_by_room, item.added_by_name) == (
        "Tarp the trailer", False, "voice", "kitchen", "Alex")
    logged = (await voice.db.execute(text(
        "SELECT matched_handler FROM intents_log ORDER BY id DESC LIMIT 1"))).scalar_one()
    assert logged == "lists"


@requires_db
@pytest.mark.asyncio
async def test_several_items_already_and_back_again(voice) -> None:
    r = await voice("add milk, eggs and bread to the grocery list")
    assert r.text == "Added milk, eggs, and bread to your shopping list."
    r = await voice("add milk to the shopping list")
    assert r.text == "Milk is already on your shopping list."
    await voice("cross milk off the shopping list")
    r = await voice("add milk, cheese and eggs to the shopping list")
    assert r.text == (
        "Added cheese to your shopping list. Put milk back on your shopping list. Eggs is already there."
    )
    assert await _items(voice.db, "shopping") == [
        ("Eggs", False), ("Bread", False), ("Milk", False), ("Cheese", False)]


@requires_db
@pytest.mark.asyncio
async def test_reading_caps_and_says_how_many_more(voice) -> None:
    r = await voice("what's on my to-do list")
    assert r.text == "Your to-do list is empty."
    lst = await L.find_list(voice.db, "todo")
    for n in range(1, 12):
        await L.add_item(voice.db, lst.id, f"job {n}")
    r = await voice("read me my to-do list")
    assert r.text == (
        "Your to-do list has 11 things. The first 8: job 1, job 2, job 3, job 4, job 5, "
        "job 6, job 7, and job 8. And 3 more on the Lists page."
    )
    await L.clear_items(voice.db, lst.id, done_only=False)
    await L.add_item(voice.db, lst.id, "Tarp the trailer")
    r = await voice("what's on my to-do list")
    assert r.text == "Your to-do list has one thing: Tarp the trailer."
    await voice("check off tarp the trailer")
    r = await voice("what's on my to-do list")
    assert r.text == "Everything on your to-do list is crossed off."


@requires_db
@pytest.mark.asyncio
async def test_cross_off_and_remove(voice) -> None:
    await voice("add milk to the shopping list")
    await voice("add call the vet to my to-do list")
    r = await voice("cross off milk")
    assert r.text == "Crossed milk off your shopping list."
    r = await voice("cross off milk")
    assert r.text == "Milk is already crossed off your shopping list."
    r = await voice("is milk on the shopping list")
    assert r.text == "Milk is on your shopping list, crossed off."
    r = await voice("cross off the dishes")
    assert r.text == "I couldn't find the dishes on any of your lists."
    r = await voice("remove call the vet from my to-do list")
    assert r.text == "Removed call the vet from your to-do list."
    assert await _items(voice.db, "todo") == []
    # On two lists: which one?
    await voice("add batteries to the shopping list")
    await voice("add batteries to my to-do list")
    r = await voice("cross off batteries")
    assert r.text == "Batteries is on your to-do list and your shopping list. Say which list."
    # A close spelling is found for crossing off, never for adding.
    r = await voice("cross battery off the shopping list")
    assert r.text == "Crossed batteries off your shopping list."


@requires_db
@pytest.mark.asyncio
async def test_clear_asks_first_and_only_yes_clears(voice) -> None:
    await voice("add milk, eggs to the shopping list")
    r = await voice("clear the shopping list")
    assert r.text == "Clear all 2 things off your shopping list?" and r.expect_followup
    r = await voice("no")
    assert r.text == "OK, I'll leave it."
    assert len(await _items(voice.db, "shopping")) == 2
    await voice("clear the shopping list")
    r = await voice("yes")
    assert r.text == "Done. Your shopping list is empty."
    assert await _items(voice.db, "shopping") == []


@requires_db
@pytest.mark.asyncio
async def test_a_late_yes_clears_nothing(voice) -> None:
    await voice("add milk to the shopping list")
    lst = await L.find_list(voice.db, "shopping")
    h = ListsHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen")
    r = await h.handle_confirmation(
        CLEAR_KIND, {"list_id": lst.id, "expires_at": time.time() - 1}, True, ctx, voice.db)
    assert r.text.startswith("I didn't do that")
    assert len(await _items(voice.db, "shopping")) == 1


@requires_db
@pytest.mark.asyncio
async def test_clear_done_takes_only_crossed_off_items(voice) -> None:
    await voice("add milk, eggs to the shopping list")
    await voice("cross off milk")
    r = await voice("clear the crossed off items from the shopping list")
    assert r.text == "Removed one thing you'd crossed off your shopping list."
    assert await _items(voice.db, "shopping") == [("Eggs", False)]


@requires_db
@pytest.mark.asyncio
async def test_an_unknown_list_is_asked_about_never_made_silently(voice) -> None:
    r = await voice("add tents to my camping list")
    assert r.text == "You don't have a camping list. Want me to make one and add tents?"
    assert r.expect_followup
    assert await L.find_list(voice.db, "camping") is None
    pending = (await SessionRepository(voice.db).get_context(voice.sessions["kitchen"]))["pending_confirmation"]
    assert pending["kind"] == CREATE_KIND
    r = await voice("yes")
    assert r.text == "Added tents to your camping list."
    assert await _items(voice.db, "camping") == [("Tents", False)]
    r = await voice("add the zoo to my zoo trip list")
    r = await voice("no")
    assert r.text == "OK, I'll leave it."
    assert await L.find_list(voice.db, "zoo trip") is None


@requires_db
@pytest.mark.asyncio
async def test_a_bare_list_goes_to_the_only_list_else_asks(voice) -> None:
    r = await voice("add milk to my list")
    assert r.text == "Which list? You have to-do and shopping."
    todo = await L.find_list(voice.db, "todo")
    await L.delete_list(voice.db, todo.id)
    r = await voice("add milk to my list")
    assert r.text == "Added milk to your shopping list."


@requires_db
@pytest.mark.asyncio
async def test_lists_made_named_and_deleted(voice) -> None:
    r = await voice("what lists do I have")
    assert r.text == "You have 2 lists: to-do and shopping."
    r = await voice("make a list called camping")
    assert r.text == "Made your camping list."
    r = await voice("create a grocery list")
    assert r.text == "You already have a shopping list."
    r = await voice("what lists do I have")
    assert r.text == "You have 3 lists: to-do, shopping, and camping."
    r = await voice("delete the camping list")
    assert r.text == "Delete your camping list?" and r.expect_followup
    r = await voice("yes")
    assert r.text == "Deleted your camping list."
    assert await L.find_list(voice.db, "camping") is None


@requires_db
@pytest.mark.asyncio
async def test_a_playlist_heard_as_two_words_is_not_kept_as_a_list(voice) -> None:
    r = await voice("add this song to my play list")
    assert r.matched_handler != "lists"
    assert await L.find_list(voice.db, "play") is None


@requires_db
@pytest.mark.asyncio
async def test_the_tool_call_adds_and_reads(db_session) -> None:
    await seed_defaults(db_session)
    h = ListsHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen")
    r = await h.execute_from_tool({"action": "add", "list": "groceries", "item": "oat milk"}, ctx, db_session)
    assert r.text == "Added oat milk to your shopping list."
    r = await h.execute_from_tool({"action": "read", "list": "shopping"}, ctx, db_session)
    assert r.text == "Your shopping list has one thing: Oat milk."
    assert await h.execute_from_tool({"action": "add", "list": "playlist", "item": "x"}, ctx, db_session) is None
