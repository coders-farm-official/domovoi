"""Who's in this house: the People list as facts (owner ruling, case 8.10).

"Who's in this house?", "who lives here?", "who do you know?" get who
Domovoi KNOWS (the ``people`` rows), never who is home; "who's home?" /
"is anyone home?" says it can't tell, then who it knows. A guest hears
that the house doesn't share who lives here. Fixed answers from
``domovoi/house_facts.py``, by voice (the house_facts fast path) and in
typed chat (``house_facts.chat_reply``), never the model.

Who is a guest: typed chat, ``admin_auth.request_is_guest`` (the
room-queue guest rule: after setup, no household credential at all);
voice, never (``house_facts.voice_turn_is_guest``). No database or model
is needed here.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from domovoi import house_facts as hf
from domovoi import owner_easter_eggs as eggs

TAIL = "That's who has a profile with me; I can't tell who's home right now."
# Never a presence claim or a room.
PRESENCE_RE = re.compile(r"\b(?:is|are) (?:home|here|in the)\b|\bkitchen\b|\bliving room\b|\bbedroom\b")


class _People:
    """A session (or scope) answering the People list's one query."""

    def __init__(self, names, *, fail: bool = False) -> None:
        self.names = names
        self.fail = fail
        self.reads = 0

    async def execute(self, stmt, params=None):
        self.reads += 1
        if self.fail:
            raise RuntimeError("db down")
        assert " ".join(str(stmt).split()) == "SELECT name FROM people ORDER BY id"
        return SimpleNamespace(all=lambda: [(n,) for n in self.names])


def _unreadable(name: str):
    raise KeyError(name)


async def _chat(said: str, people: _People, guest_check=None) -> str | None:
    return await hf.chat_reply(said, get_setting=_unreadable, scope=people, guest_check=guest_check)


PEOPLE_Q = [
    "Who's in this house?", "who is in this house", "who lives here", "Who lives here?",
    "who lives in this house", "who else lives here", "who lives with me", "who do you know",
    "Who do you know?", "who else do you know", "who's in the family", "who is in the household",
    "who are the people in this house", "who has a profile", "list the people in this house",
    "tell me who lives here", "do you know who lives here", "who is in the house",
]
HOME_Q = [
    "who's home", "Who's home?", "who is home", "whos home", "who is home right now", "is anyone home",
    "Is anyone home?", "is anybody home", "is someone home", "who's here", "is anybody here",
    "who's in the house right now", "do you know who's home",
]


@pytest.mark.parametrize("said", PEOPLE_Q)
def test_people_questions(said: str) -> None:
    assert hf.household_question(hf.normalize(said)) == "people"


@pytest.mark.parametrize("said", HOME_Q)
def test_home_questions(said: str) -> None:
    assert hf.household_question(hf.normalize(said)) == "home"


@pytest.mark.parametrize("said", [
    "who is in the kitchen", "who's the owner", "who is this", "where is everyone",
    "who is at the door", "who's in charge here", "who sings this song", "is anyone listening",
    "who's the admin", "who am i", "who is talking", "who lives in paris", "who do you know in paris",
])
def test_other_questions_are_left_alone(said: str) -> None:
    assert hf.household_question(hf.normalize(said)) is None


@pytest.mark.parametrize("said", PEOPLE_Q + HOME_Q)
def test_never_a_joke_and_lands_on_house_facts_by_voice(said: str) -> None:
    from domovoi.router import plan_route

    assert not eggs.is_who_question(hf.normalize(said))
    plan = plan_route(said)
    assert plan is not None and plan.handler.name == "house_facts", said
    assert plan.fast_path.method.__name__ == "_household"


# ─── The exact words ──────────────────────────────────────────────────────


def test_spoken_names() -> None:
    assert hf.spoken_names(["Alex"]) == "Alex"
    assert hf.spoken_names(["Alex", "Sam"]) == "Alex and Sam"
    assert hf.spoken_names(["Alex", "Sam", "Priya"]) == "Alex, Sam and Priya"


@pytest.mark.parametrize(("names", "expected"), [
    ([], "No one has a profile with me yet."),
    (["Alex"], f"I know Alex. {TAIL}"),
    (["Alex", "Sam"], f"I know Alex and Sam. {TAIL}"),
    (["Alex", "Sam", "Priya"], f"I know Alex, Sam and Priya. {TAIL}"),
    (["Alex", " Alex ", ""], f"I know Alex. {TAIL}"),
])
def test_people_answer(names, expected: str) -> None:
    assert hf.household_answer("people", names, guest=False) == expected


@pytest.mark.parametrize(("names", "expected"), [
    ([], "I can't tell who's home."),
    (["Alex"], "I can't tell who's home. I know Alex."),
    (["Alex", "Sam", "Priya"], "I can't tell who's home. I know Alex, Sam and Priya."),
])
def test_home_answer(names, expected: str) -> None:
    assert hf.household_answer("home", names, guest=False) == expected


@pytest.mark.parametrize("kind", ["people", "home"])
@pytest.mark.parametrize("names", [[], ["Alex"], ["Alex", "Sam", "Priya"]])
def test_never_a_presence_claim(kind: str, names) -> None:
    assert not PRESENCE_RE.search(hf.household_answer(kind, names, guest=False))


@pytest.mark.parametrize("kind", ["people", "home"])
def test_a_guest_hears_no_names(kind: str) -> None:
    assert hf.household_answer(kind, ["Alex", "Sam"], guest=True) == "I don't share who lives here."


def test_an_unreadable_list_is_never_no_one() -> None:
    assert hf.household_answer("people", None, guest=False) == hf.CANT_CHECK
    assert hf.household_answer("home", None, guest=False) == "I can't tell who's home."


@pytest.mark.asyncio
async def test_reply_reads_the_people_rows() -> None:
    db = _People(["Alex", "Sam"])
    assert await hf.household_reply("people", scope=db, guest=False) == f"I know Alex and Sam. {TAIL}"
    assert db.reads == 1


@pytest.mark.asyncio
async def test_a_guest_reply_reads_nothing() -> None:
    db = _People(["Alex"])
    assert await hf.household_reply("people", scope=db, guest=True) == hf.GUEST_PEOPLE
    assert db.reads == 0


@pytest.mark.asyncio
async def test_a_failed_read_is_cant_check() -> None:
    assert await hf.household_reply("people", scope=_People([], fail=True), guest=False) == hf.CANT_CHECK


# ─── Voice ────────────────────────────────────────────────────────────────


def test_voice_is_never_a_guest() -> None:
    from domovoi.models import Context

    assert hf.voice_turn_is_guest(Context(room_id="kitchen")) is False
    assert hf.voice_turn_is_guest(Context(room_id=None, person_id=None)) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("said", "names", "expected"), [
    ("who's in this house", ["Alex", "Sam"], f"I know Alex and Sam. {TAIL}"),
    ("who lives here", [], "No one has a profile with me yet."),
    ("is anyone home", ["Alex"], "I can't tell who's home. I know Alex."),
])
async def test_voice_answer(said: str, names, expected: str) -> None:
    from domovoi.handlers.house_facts import HouseFactsHandler

    m = hf.HOUSEHOLD_RE.match(said)
    ctx = SimpleNamespace(session_id=None, room_id="kitchen", proven_admin=False)
    r = await HouseFactsHandler()._household(m, ctx, _People(names))
    assert r.matched_handler == "house_facts" and r.text == expected


# ─── Typed chat ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_member() -> None:
    async def member() -> bool:
        return False

    db = _People(["Alex", "Sam", "Priya"])
    assert await _chat("Who's in this house?", db, member) == f"I know Alex, Sam and Priya. {TAIL}"
    assert await _chat("Who's home?", db, member) == "I can't tell who's home. I know Alex, Sam and Priya."
    # No check given: the household's.
    assert await _chat("who do you know", db) == f"I know Alex, Sam and Priya. {TAIL}"


@pytest.mark.asyncio
async def test_chat_guest() -> None:
    async def guest() -> bool:
        return True

    db = _People(["Alex"])
    for said in ("Who's in this house?", "who lives here", "Who's home?", "is anyone home"):
        reply = await _chat(said, db, guest)
        assert reply == "I don't share who lives here." and "Alex" not in reply
    assert db.reads == 0


@pytest.mark.asyncio
async def test_chat_guest_check_that_fails_is_a_guest() -> None:
    async def broken() -> bool:
        raise RuntimeError("db down")

    assert await _chat("who lives here", _People(["Alex"]), broken) == hf.GUEST_PEOPLE


@pytest.mark.asyncio
async def test_chat_other_questions_never_ask_the_guest_check() -> None:
    async def check() -> bool:
        raise AssertionError("only a who-lives-here question asks")

    def no_db():
        raise AssertionError("no database read is needed")

    assert await hf.chat_reply("Who's the admin?", get_setting=_unreadable, scope=no_db,
                               guest_check=check) == hf.ADMIN_WHO_ANSWERS["admin"]
    assert await hf.chat_reply("hello", get_setting=_unreadable, scope=no_db, guest_check=check) is None


def test_the_chat_passes_the_guest_check() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "web" / "backend" / "api" / "chat.py").read_text(
        encoding="utf-8")
    assert src.count("guest_check=lambda: request_is_guest(request)") == 2


# ─── Who is a guest on the web: admin_auth.request_is_guest ──────────────


class _Req:
    def __init__(self, headers=None, cookies=None) -> None:
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.cookies = cookies or {}
        self.query_params = {}


@pytest.mark.asyncio
@pytest.mark.parametrize(("result", "guest"), [
    ("no-auth", True), ("pre-setup", False), ("ok", False), ("cookie-only", False), ("invalid", False),
])
async def test_request_is_guest_follows_the_queue_guest_rule(monkeypatch, result: str, guest: bool) -> None:
    from domovoi import admin_auth

    async def check(request, *a, **k):
        return result

    monkeypatch.setattr(admin_auth, "check_admin_request", check)
    assert await admin_auth.request_is_guest(_Req()) is guest


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    {"X-Device-Token": "tok"}, {"Authorization": "Bearer abc"},
])
async def test_a_household_credential_is_never_a_guest(monkeypatch, headers) -> None:
    from domovoi import admin_auth

    async def check(request, *a, **k):
        raise AssertionError("a credential decides it")

    monkeypatch.setattr(admin_auth, "check_admin_request", check)
    assert await admin_auth.request_is_guest(_Req(headers=headers)) is False
    assert await admin_auth.request_is_guest(_Req(cookies={admin_auth.COOKIE_NAME: "c"})) is False


@pytest.mark.asyncio
async def test_request_is_guest_fails_closed(monkeypatch) -> None:
    from domovoi import admin_auth

    async def check(request, *a, **k):
        raise admin_auth.AuthCheckUnavailable()

    monkeypatch.setattr(admin_auth, "check_admin_request", check)
    assert await admin_auth.request_is_guest(_Req()) is True
    assert await admin_auth.request_is_guest(None) is False
