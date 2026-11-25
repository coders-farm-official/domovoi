"""Who does an admin's job: a fixed facts answer (1.4.1, case 23.12).

"Who can approve this?", "who's the admin?", "who set this up?", "who can
change settings?" are practical questions: they get facts from
``domovoi/house_facts.py`` (who does it and where), never an ownership
joke (domovoi/owner_easter_eggs.py) and never the model, by voice (the
house_facts handler's fast path, band 107, before voice_profile's jokes at
110) and in typed chat (``house_facts.chat_reply``). Domovoi doesn't record
who the admin is, so no answer names a person. No database or model here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from domovoi import house_facts as hf
from domovoi import owner_easter_eggs as eggs

JOKES = set(eggs.CLAIM_LINES + eggs.WHO_LINES + eggs.ONE_PERSON_LINES + eggs.NO_NAME_LINES)


def _plan(said: str):
    from domovoi.router import plan_route

    return plan_route(said)


def _unreadable(name: str):
    raise KeyError(name)


def _no_db():
    raise AssertionError("no database read is needed")


async def _chat(said: str, admin_check=None) -> str | None:
    return await hf.chat_reply(said, get_setting=_unreadable, scope=_no_db, admin_check=admin_check)


CASES = [
    ("Who can approve this?", "approve"),
    ("who can approve that", "approve"),
    ("who approves this", "approve"),
    ("who has to approve it", "approve"),
    ("who do I ask for approval", "approve"),
    ("who do I ask to approve this", "approve"),
    ("Who's the admin?", "admin"),
    ("who is the admin", "admin"),
    ("whos the admin here", "admin"),
    ("who is the administrator of this house", "admin"),
    ("who are the admins", "admin"),
    ("who has the admin password", "admin"),
    ("Who set this up?", "setup"),
    ("who set you up", "setup"),
    ("who installed domovoi", "setup"),
    ("who set up domovoi", "setup"),
    ("Who can change settings?", "settings"),
    ("who can change the settings", "settings"),
    ("Who's in charge of the settings?", "settings"),
    ("who is responsible for the settings", "settings"),
    ("who manages the dashboard", "settings"),
    ("who should I ask about the settings", "settings"),
]


@pytest.mark.parametrize(("said", "kind"), CASES)
def test_recognised(said: str, kind: str) -> None:
    assert hf.admin_who(hf.normalize(said)) == kind


@pytest.mark.parametrize(("said", "kind"), CASES)
def test_never_an_ownership_joke(said: str, kind: str) -> None:
    """owner_easter_eggs.WHO_RE keeps excluding every practical question."""
    assert not eggs.is_who_question(hf.normalize(said))


@pytest.mark.parametrize("said", [
    "who's the owner", "who's in charge here", "who runs this house", "who is home",
    "who can play music", "who can approve the dog", "am i admin", "can i sign in as admin",
    "how do i change the settings", "who sings this song", "who's in charge of dinner",
])
def test_other_questions_are_left_alone(said: str) -> None:
    assert hf.admin_who(hf.normalize(said)) is None


@pytest.mark.parametrize(("said", "kind"), CASES)
def test_lands_on_house_facts_by_voice(said: str, kind: str) -> None:
    plan = _plan(said)
    assert plan is not None and plan.handler.name == "house_facts", said
    assert plan.fast_path.method.__name__ == "_admin_who"


def test_the_exact_answers() -> None:
    assert hf.admin_who_answer("approve") == "An admin approves that, on the dashboard."
    assert hf.admin_who_answer("settings") == (
        "An admin does that, on the dashboard, signed in with the admin password.")
    assert hf.admin_who_answer("admin") == (
        "I don't keep track of who the admin is: the admin is whoever signs in on the dashboard "
        "with the admin password.")
    assert hf.admin_who_answer("setup") == (
        "I don't keep a name for that. Whoever set up Domovoi chose the admin password, and signs "
        "in with it on the dashboard.")


@pytest.mark.parametrize("kind", ["approve", "admin", "setup", "settings"])
def test_answers_are_facts_never_jokes_never_names(kind: str) -> None:
    for admin in (False, True, None):
        said = hf.admin_who_answer(kind, admin=admin)
        assert said not in JOKES
        assert "dashboard" in said
        # 1.5.0's phone admin sign-in isn't on main: never mentioned.
        assert "phone" not in said.lower() and "fingerprint" not in said.lower()


def test_a_proven_admin_hears_that_it_is_them() -> None:
    assert hf.admin_who_answer("approve", admin=True) == (
        "An admin approves that, on the dashboard. That's you, signed in here.")
    assert hf.admin_who_answer("admin", admin=True).endswith(" " + hf.ADMIN_WHO_YOU)
    # An admin need not be whoever set Domovoi up.
    assert hf.ADMIN_WHO_YOU not in hf.admin_who_answer("setup", admin=True)
    assert hf.ADMIN_WHO_YOU not in hf.admin_who_answer("settings", admin=False)
    assert hf.ADMIN_WHO_YOU not in hf.admin_who_answer("settings", admin=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("proven", [False, True])
async def test_voice_answer(proven: bool) -> None:
    from domovoi.handlers.house_facts import HouseFactsHandler

    m = hf.ADMIN_WHO_RE.match("who can approve this")
    ctx = SimpleNamespace(session_id=None, proven_admin=proven)
    r = await HouseFactsHandler()._admin_who(m, ctx, None)
    assert r.matched_handler == "house_facts"
    assert r.text == hf.admin_who_answer("approve", admin=proven)


@pytest.mark.asyncio
@pytest.mark.parametrize(("said", "kind"), CASES)
async def test_chat_answers_the_same(said: str, kind: str) -> None:
    assert await _chat(said) == hf.admin_who_answer(kind)


@pytest.mark.asyncio
async def test_chat_asks_the_request_for_the_admin_state() -> None:
    seen = []

    async def check() -> bool:
        seen.append(True)
        return True

    assert await _chat("Who can change settings?", admin_check=check) == (
        f"{hf.ADMIN_WHO_ANSWERS['settings']} {hf.ADMIN_WHO_YOU}")
    assert seen == [True]


@pytest.mark.asyncio
async def test_chat_admin_check_that_fails_is_a_member() -> None:
    async def check() -> bool:
        raise RuntimeError("db down")

    assert await _chat("Who's the admin?", admin_check=check) == hf.ADMIN_WHO_ANSWERS["admin"]


@pytest.mark.asyncio
async def test_the_owner_egg_never_answers_them_in_chat() -> None:
    class _People:
        async def execute(self, stmt, params=None):
            return SimpleNamespace(all=lambda: [("Alex",)])

    for said, _kind in CASES:
        assert await eggs.chat_reply(said, scope=_People(), key="t") is None, said
