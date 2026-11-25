"""Ownership easter eggs (domovoi/owner_easter_eggs.py, owner-approved 2026-11-25).

"I'm the owner" gets a joke and grants nothing; a command after it still
runs, its reply after the joke. "Who's the owner?" gets a joke about the
People list (people rows only). Practical questions ("who's the admin?",
"who can approve this?") never get a joke.
"""

from __future__ import annotations

import random
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from domovoi import owner_easter_eggs as eggs
from domovoi import role_claims
from domovoi.models import Context, Intent
from domovoi.router import plan_route, route
from domovoi.tests.conftest import requires_db

ALL_LINES = eggs.CLAIM_LINES + eggs.WHO_LINES + eggs.ONE_PERSON_LINES + eggs.NO_NAME_LINES

# ─── The lines ────────────────────────────────────────────────────────────


def test_the_approved_counts() -> None:
    assert (len(eggs.CLAIM_LINES), len(eggs.WHO_LINES), len(eggs.ONE_PERSON_LINES),
            len(eggs.NO_NAME_LINES)) == (23, 23, 6, 5)
    for lines in (eggs.CLAIM_LINES, eggs.WHO_LINES, eggs.ONE_PERSON_LINES, eggs.NO_NAME_LINES):
        assert isinstance(lines, tuple) and len(set(lines)) == len(lines)
        assert all(isinstance(line, str) and line.strip() == line for line in lines)


def test_no_cat_lines() -> None:
    """The cat lines were dropped (owner, 2026-11-25)."""
    assert not [line for line in ALL_LINES if "cat" in line.lower()]


def test_placeholders() -> None:
    assert "Sure thing, boss. Wink." in eggs.CLAIM_LINES
    for line in eggs.CLAIM_LINES + eggs.NO_NAME_LINES:
        assert "{" not in line, line
    for line in eggs.ONE_PERSON_LINES:
        assert "{other}" not in line and "{name}" in line, line
    assert sum("{other}" in line for line in eggs.WHO_LINES) == 5
    for line in ALL_LINES:
        assert set(re.findall(r"\{(\w*)\}", line)) <= {"name", "other"}, line


# ─── Picking ──────────────────────────────────────────────────────────────


def test_no_people_no_names() -> None:
    for _ in range(30):
        assert eggs.who_line([], "k0") in eggs.NO_NAME_LINES


def test_one_person() -> None:
    allowed = {line.format(name="Alex") for line in eggs.ONE_PERSON_LINES}
    for _ in range(30):
        said = eggs.who_line(["Alex"], "k1")
        assert said in allowed and "{" not in said


def test_two_or_more_people_name_only_them_and_other_is_someone_else() -> None:
    people = ["Alex", "Sam", "Priya"]
    rng = random.Random(7)
    seen_other = False
    for _ in range(300):
        said = eggs.who_line(people, "k2", rng=rng)
        assert "{" not in said
        hits = [p for p in people if p in said]
        for template in eggs.WHO_LINES:
            if "{other}" not in template:
                continue
            pat = re.escape(template).replace(r"\{name\}", "(?P<n>\\w+)").replace(r"\{other\}", "(?P<o>\\w+)")
            m = re.fullmatch(pat, said)
            if m:
                seen_other = True
                assert m.group("n") != m.group("o")
                assert {m.group("n"), m.group("o")} <= set(people)
        assert set(hits) <= set(people)
    assert seen_other


def test_duplicate_names_count_once() -> None:
    """Two rows called Alex are one name: no "Alex, until Alex finds out"."""
    allowed = {line.format(name="Alex") for line in eggs.ONE_PERSON_LINES}
    assert eggs.who_line(["Alex", "Alex "], "k3") in allowed


@pytest.mark.parametrize("pick", [
    lambda key: eggs.claim_line(key),
    lambda key: eggs.who_line([], key),
    lambda key: eggs.who_line(["Alex"], key),
    lambda key: eggs.who_line(["Alex", "Sam"], key),
])
def test_never_the_same_line_twice_in_a_row(pick) -> None:
    last = None
    for _ in range(200):
        said = pick("same-room")
        assert said != last
        last = said


# ─── Which words ──────────────────────────────────────────────────────────


WHO = [
    "who's the owner", "Who is the owner?", "who's the boss", "who is the boss here",
    "who's in charge", "who's in charge here", "who is in charge of this house",
    "who runs this house", "who runs the place", "who owns this house", "who's the real boss",
    "who's the boss of this house", "so who's in charge",
]
PRACTICAL = [
    "who's the admin", "who is the admin", "who can approve this", "how do I sign in as admin",
    "who set this up", "who owns this device", "who is the owner of this device",
    "who's the owner of this phone", "who's in charge of dinner", "who can change the settings",
    "how do I become the admin", "who has admin",
]


@pytest.mark.parametrize("said", WHO)
def test_who_questions(said: str) -> None:
    t = said.lower().rstrip("?")
    assert eggs.is_who_question(t)
    plan = plan_route(said)
    assert plan is not None and plan.fast_path.method.__name__ == "_who_owner_from_match", said


@pytest.mark.parametrize("said", PRACTICAL)
def test_practical_questions_never_get_a_joke(said: str) -> None:
    assert not eggs.is_who_question(said.lower())
    plan = plan_route(said)
    assert plan is None or plan.fast_path.method.__name__ not in (
        "_who_owner_from_match", "_role_claim_from_match"), said


@pytest.mark.parametrize("said", ["i'm the owner", "i'm in charge", "i am the boss", "i'm admin",
                                  "i'm in charge here", "i'm the owner of this house"])
def test_claims_are_authority(said: str) -> None:
    claim = role_claims.split_claim(said)
    assert claim is not None and role_claims.is_authority(claim[0]) and claim[1] == ""


def test_in_charge_of_something_else_is_no_claim() -> None:
    assert role_claims.split_claim("i'm in charge of dinner") is None


# ─── Voice ────────────────────────────────────────────────────────────────


class _People:
    def __init__(self, names):
        self.names_ = names

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if sql == "SELECT name FROM people ORDER BY id":
            return SimpleNamespace(all=lambda: [(n,) for n in self.names_])
        assert sql == "SELECT id, name FROM people ORDER BY id", sql
        return SimpleNamespace(all=lambda: [(i, n) for i, n in enumerate(self.names_, 1)])


@pytest.mark.asyncio
@pytest.mark.parametrize(("names", "lines"), [
    ([], eggs.NO_NAME_LINES),
    (["Alex"], tuple(line.format(name="Alex") for line in eggs.ONE_PERSON_LINES)),
])
async def test_voice_who_reads_the_people_list(names, lines) -> None:
    from domovoi.handlers.voice_profile import VoiceProfileHandler

    m = eggs.WHO_RE.match("who's the owner")
    ctx = Context(room_id="kitchen", session_id=None)
    r = await VoiceProfileHandler()._who_owner_from_match(m, ctx, _People(names))
    assert r.text in lines


@pytest.mark.asyncio
async def test_voice_who_with_people_names_only_them() -> None:
    from domovoi.handlers.voice_profile import VoiceProfileHandler

    m = eggs.WHO_RE.match("who's in charge here")
    ctx = Context(room_id="kitchen", session_id=None)
    for _ in range(50):
        r = await VoiceProfileHandler()._who_owner_from_match(m, ctx, _People(["Alex", "Sam"]))
        assert "{" not in r.text
        assert re.sub(r"\b(?:Alex|Sam)\b", "{}", r.text) in {
            re.sub(r"\{(?:name|other)\}", "{}", line) for line in eggs.WHO_LINES}


class _Talk:
    def __init__(self, db) -> None:
        self.db = db
        self.session_id = None
        self.ctx = None

    async def __call__(self, said: str):
        self.ctx = Context(room_id="kitchen", session_id=self.session_id, online=True)
        resp = await route(Intent(transcript=said, room_id="kitchen"), self.ctx, self.db)
        await self.db.commit()
        self.session_id = resp.session_id
        return resp


@requires_db
@pytest.mark.asyncio
async def test_a_command_after_a_claim_runs_and_its_reply_follows(db_session) -> None:
    await db_session.execute(text("DELETE FROM timers"))
    await db_session.commit()
    talk = _Talk(db_session)
    r = await talk("I'm the boss, set a timer for 10 minutes")
    assert r.matched_handler == "timer"
    line, _, rest = r.text.partition(" Timer set")
    assert line in eggs.CLAIM_LINES and rest == " for 10 minutes."
    n = (await db_session.execute(text("SELECT count(*) FROM timers"))).scalar()
    assert n == 1
    assert talk.ctx.proven_admin is False


@requires_db
@pytest.mark.asyncio
async def test_the_claim_grants_nothing(db_session) -> None:
    """The admin job still gets the member answer, after the joke."""
    from domovoi.help_desk import admin_tasks

    talk = _Talk(db_session)
    r = await talk("I'm the owner, how do I add a room")
    member = next(t.member for t in admin_tasks.TASKS if t.key == "add_room")
    assert any(r.text.startswith(f"{line} {member}") for line in eggs.CLAIM_LINES), r.text
    assert talk.ctx.proven_admin is False
    bare = await talk("I'm the admin")
    assert bare.text in eggs.CLAIM_LINES and talk.ctx.proven_admin is False


@requires_db
@pytest.mark.asyncio
async def test_who_names_people_rows_only(db_session) -> None:
    """The People list is the people rows: a guest or an unknown voice is
    never one, so never named."""
    await db_session.execute(text("DELETE FROM people"))
    await db_session.execute(text("INSERT INTO people (name) VALUES ('Alex'), ('Sam')"))
    await db_session.commit()
    talk = _Talk(db_session)
    for _ in range(20):
        r = await talk("Who's the owner?")
        assert r.matched_handler == "voice_profile"
        names = set(re.findall(r"\b[A-Z][a-z]+\b", r.text))
        assert not ({"Guest", "Unknown"} & names)
        assert re.sub(r"\b(?:Alex|Sam)\b", "{}", r.text) in {
            re.sub(r"\{(?:name|other)\}", "{}", line) for line in eggs.WHO_LINES}


# ─── Typed chat ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_reply() -> None:
    db = _People(["Alex"])
    assert await eggs.chat_reply("I'm the owner.", scope=db, key="t1") in eggs.CLAIM_LINES
    assert await eggs.chat_reply("Who's in charge here?", scope=db, key="t1") in {
        line.format(name="Alex") for line in eggs.ONE_PERSON_LINES}
    for said in PRACTICAL + ["I'm the mom", "I'm the owner, update the house", "hello"]:
        assert await eggs.chat_reply(said, scope=db, key="t1") is None, said


def test_the_chat_calls_it_before_the_model() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "web" / "backend" / "api" / "chat.py").read_text(
        encoding="utf-8")
    assert "owner_easter_eggs.chat_reply(" in src
    assert src.index("_owner_egg_reply(thread_id, body.content, request)") < src.index(
        "return await _answer(\n        thread_id, thread.person_id")
    # A guest browser names no one (admin_auth.request_is_guest).
    assert "guest_check=lambda: request_is_guest(request)" in src


# ─── A guest never hears the household's names ────────────────────────────


def test_a_guest_hears_only_no_name_lines() -> None:
    for _ in range(50):
        assert eggs.who_line(["Alex", "Sam"], "g1", guest=True) in eggs.NO_NAME_LINES
        assert eggs.who_line(["Alex"], "g2", guest=True) in eggs.NO_NAME_LINES


@pytest.mark.asyncio
async def test_chat_guest_hears_no_names_and_the_list_is_not_read() -> None:
    class _NoRead:
        async def execute(self, stmt, params=None):
            raise AssertionError("a guest's answer reads no People list")

    async def guest() -> bool:
        return True

    for _ in range(20):
        said = await eggs.chat_reply("Who's the owner?", scope=_NoRead(), key="g3", guest_check=guest)
        assert said in eggs.NO_NAME_LINES


@pytest.mark.asyncio
async def test_chat_guest_check_that_fails_names_no_one() -> None:
    async def broken() -> bool:
        raise RuntimeError("db down")

    said = await eggs.chat_reply("who's in charge here", scope=_People(["Alex"]), key="g4",
                                 guest_check=broken)
    assert said in eggs.NO_NAME_LINES


@pytest.mark.asyncio
async def test_chat_member_still_hears_names() -> None:
    async def member() -> bool:
        return False

    said = await eggs.chat_reply("who's the boss", scope=_People(["Alex"]), key="g5", guest_check=member)
    assert said in {line.format(name="Alex") for line in eggs.ONE_PERSON_LINES}


@pytest.mark.asyncio
async def test_chat_claim_never_asks_the_guest_check() -> None:
    async def check() -> bool:
        raise AssertionError("only a who-question asks")

    assert await eggs.chat_reply("I'm the owner", scope=_People([]), key="g6",
                                 guest_check=check) in eggs.CLAIM_LINES


@pytest.mark.asyncio
async def test_voice_is_never_a_guest() -> None:
    """A voice turn is a household member's (house_facts.voice_turn_is_guest):
    "who's the owner?" by voice still names people."""
    from domovoi import house_facts
    from domovoi.handlers.voice_profile import VoiceProfileHandler

    ctx = Context(room_id="kitchen", session_id=None)
    assert house_facts.voice_turn_is_guest(ctx) is False
    r = await VoiceProfileHandler()._who_owner_from_match(eggs.WHO_RE.match("who's the owner"), ctx,
                                                          _People(["Alex"]))
    assert r.text in {line.format(name="Alex") for line in eggs.ONE_PERSON_LINES}


def test_household_questions_are_not_who_questions() -> None:
    """"Who lives here?" is house_facts' (the People list as facts), never a joke."""
    for said in ("who's in this house", "who lives here", "who's home", "is anyone home",
                 "who do you know"):
        assert not eggs.is_who_question(said)
        plan = plan_route(said)
        assert plan is not None and plan.handler.name == "house_facts", said
