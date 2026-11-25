"""Voice "how do I…" (part 9): routing, and the spoken answer.

* The fast path takes a how-to question about Domovoi and DECLINES every
  other one, and no other handler's phrases reach it (the bands in
  ``domovoi/handlers/__init__.py``).
* The answer is the page's first sentences and one pointer ("The full
  answer is in the manual."). Since 1.5.0 nothing is offered, asked or
  sent: the "send the steps to your phone" follow-up is gone (owner
  decision, 2026-11-25), so a "yes" after it is not an answer to anything.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi.db.repositories import SessionRepository
from domovoi.handlers import HANDLER_BY_NAME, HANDLERS
from domovoi.handlers.base import as_fast_path, example_phrases_of
from domovoi.handlers.help_desk import PLUGIN_POINTER_TEXT, POINTER_TEXT, HelpDeskHandler, pointer
from domovoi.models import Context, Intent
from domovoi.router import normalize_transcript, route, strip_leading_filler
from domovoi.tests.conftest import fast_path_winner, requires_db


def _matches(said: str) -> bool:
    t = strip_leading_filler(normalize_transcript(said))
    return any(as_fast_path(fp).pattern.match(t) for fp in HANDLER_BY_NAME["help_desk"].fast_paths)


def test_it_sits_right_after_help_and_is_never_offered_as_a_tool() -> None:
    h = HANDLER_BY_NAME["help_desk"]
    assert isinstance(h, HelpDeskHandler) and h.priority_band == 106
    names = [x.name for x in HANDLERS]
    assert names.index("help_desk") == names.index("help") + 1
    assert not h.offers_tool("how do i add a room")
    assert h.requires_network == "no"


@pytest.mark.parametrize(
    "said",
    ["how do I add a room", "How can I change the wake word?", "how to install a plugin",
     "where do I find the room queue", "is there a way to make a room louder"],
)
def test_how_to_questions_reach_it(said) -> None:
    assert fast_path_winner(said) == "help_desk"


def test_no_other_handlers_phrase_reaches_it() -> None:
    """Every example phrase and every manual "try saying" phrase of another
    handler: none of them is a help_desk match (it would poach them)."""
    import json
    from pathlib import Path

    phrases = [p for h in HANDLERS if h.name != "help_desk" for p in example_phrases_of(h)]
    doc = json.loads((Path(__file__).resolve().parents[2] / "web/static/manual-content.json").read_text("utf-8"))
    for piece in [doc["start"], *doc["pieces"]]:
        for b in piece.get("blocks") or []:
            if b.get("type") == "say":
                phrases += [p["text"] for p in b["phrases"] if p.get("handler") != "help_desk"]
    assert phrases
    poached = [p for p in phrases if _matches(p)]
    assert not poached, poached


@pytest.mark.parametrize(
    "word",
    ["printer", "printers", "print", "printing", "scan", "scanner", "scanning", "document", "documents",
     "letter", "letters", "draft", "drafts", "health", "ai", "lookups", "look things up", "photos"],
)
def test_the_newer_pieces_words_are_domovoi_words(word) -> None:
    """Printing, scanning, documents, letters, drafts, Health and the AI
    switch are Domovoi's too: a how-to naming one gets past the first gate
    (the second is the help index, ``answer.is_about_domovoi``)."""
    from domovoi.handlers.help_desk import _DOMOVOI_WORDS

    assert _DOMOVOI_WORDS.search(f"how do i use the {word} here")
    assert not _DOMOVOI_WORDS.search("how do i make pancakes")
    assert not _DOMOVOI_WORDS.search("how do i paint a fence")  # "ai" only as a word


@pytest.mark.parametrize("said", ["how do i add a printer", "how do i add a scanner", "how do i scan a document",
                                  "how do i turn the ai off", "how do i take a letter"])
def test_a_how_to_about_printers_scanners_letters_or_ai_is_a_help_question(said) -> None:
    from domovoi.handlers.help_desk import looks_like_domovoi

    assert fast_path_winner(said) == "help_desk"
    assert looks_like_domovoi(said)


@pytest.mark.parametrize("said", ["how do i make pancakes", "how can i restart my router", "how do i learn ai",
                                  "how do i improve my health", "how do i scan a qr code",
                                  "how do i print from my iphone", "how do i fix a paper jam in my printer"])
def test_the_new_words_poach_no_other_how_to(said) -> None:
    from domovoi.handlers.help_desk import looks_like_domovoi

    assert not looks_like_domovoi(said)


@pytest.mark.parametrize(
    ("said", "handler"),
    [("print my cover letter", "printing"), ("scan this to Documents", "printing"),
     ("take a letter to the landlord", "documents")],  # "read me the lease": the corpus, with its Documents
)
def test_the_commands_keep_their_handlers(said, handler) -> None:
    assert fast_path_winner(said) == handler


def test_help_keeps_how_do_i_use_you() -> None:
    assert fast_path_winner("how do I use you") == "help"
    assert not _matches("how do i use you")


def test_the_pointer_says_where_the_rest_is_and_asks_nothing() -> None:
    assert pointer("manual") == POINTER_TEXT == "The full answer is in the manual."
    assert pointer("faq") == POINTER_TEXT
    assert pointer("plugin") == PLUGIN_POINTER_TEXT == "The full answer is in that plugin's help."
    for t in (POINTER_TEXT, PLUGIN_POINTER_TEXT):
        assert not t.endswith("?") and "phone" not in t


def test_it_parks_no_question() -> None:
    """The send offer and the name question (``core.help_send``,
    ``core.help_who``) were removed in 1.5.0: the handler declares no
    confirmation or choice kinds, so one parked before an upgrade resumes
    nothing (``router.dispatchable_confirmation``) and the turn routes as
    usual."""
    from domovoi.router import dispatchable_confirmation

    h = HANDLER_BY_NAME["help_desk"]
    assert tuple(h.confirmation_kinds) == () and tuple(h.choice_kinds) == ()
    for kind in ("core.help_send", "core.help_who"):
        assert dispatchable_confirmation({"handler": "help_desk", "kind": kind}) is None
    import domovoi.handlers.help_desk as mod

    assert not hasattr(mod, "SEND_KIND") and not hasattr(mod, "WHO_KIND")


# ── through the router, on the lane's database ───────────────────────────


@pytest_asyncio.fixture
async def voice(db_session):
    sessions: dict[str, object] = {}

    async def say(utterance: str, room: str = "kitchen", person: int | None = None):
        ctx = Context(room_id=room, session_id=sessions.get(room), person_id=person, online=True)
        resp = await route(Intent(transcript=utterance, room_id=room), ctx, db_session)
        sessions[room] = resp.session_id
        await db_session.commit()
        return resp

    async def person(name: str) -> int:
        pid = (await db_session.execute(text("INSERT INTO people (name) VALUES (:n) RETURNING id"),
                                        {"n": name})).scalar_one()
        await db_session.commit()
        return int(pid)

    async def phone(device_id: str, owner: int | None, *, shared: bool = False) -> None:
        await db_session.execute(
            text("INSERT INTO devices (device_id, name, platform, owner_person_id, shared_screen) "
                 "VALUES (:d, :d, 'android', :o, :s)"),
            {"d": device_id, "o": owner, "s": shared},
        )
        await db_session.commit()

    async def deliveries() -> list[dict]:
        rows = (await db_session.execute(text(
            "SELECT d.person_id, d.title, t.title AS thread_title, t.person_id AS thread_person, "
            "(SELECT count(*) FROM chat_messages m WHERE m.thread_id = t.id) AS msgs "
            "FROM help_deliveries d JOIN chat_threads t ON t.id = d.thread_id ORDER BY d.id"
        ))).mappings().all()
        return [dict(r) for r in rows]

    await db_session.execute(text("DELETE FROM devices"))
    await db_session.commit()
    say.person, say.phone, say.deliveries, say.sessions, say.db = person, phone, deliveries, sessions, db_session
    return say


async def _pending(say, room="kitchen"):
    data = await SessionRepository(say.db).get_context(say.sessions[room]) or {}
    return data.get("pending_confirmation")


@requires_db
@pytest.mark.asyncio
async def test_a_how_to_is_answered_from_the_manual_with_a_pointer(voice) -> None:
    r = await voice("how do I change the wake word")
    assert (r.matched_handler, r.matched_path) == ("help_desk", "fast")
    assert r.text.startswith("From the manual's ")
    assert r.text.endswith(" " + POINTER_TEXT) and not r.expect_followup
    assert "phone" not in r.text.split(":", 1)[0] and "Want me to send" not in r.text
    assert await _pending(voice) is None


@requires_db
@pytest.mark.asyncio
async def test_a_question_the_help_does_not_hold_routes_on(voice) -> None:
    for said in ("how do i make pancakes", "how do you say hello in spanish", "how do i get to the airport"):
        r = await voice(said)
        assert r.matched_handler != "help_desk", said


@requires_db
@pytest.mark.asyncio
async def test_nothing_is_sent_even_to_a_recognised_asker_with_a_phone(voice) -> None:
    """The case the send offer used to serve: a voice ID'd person with a
    phone marked as theirs. The answer is spoken, nothing waits for a yes,
    and a "yes" after it writes no chat and no delivery."""
    sarah = await voice.person("Sarah")
    await voice.phone("android-sarah", sarah)
    threads = (await voice.db.execute(text("SELECT count(*) FROM chat_threads"))).scalar_one()
    r = await voice("how do I change the wake word", person=sarah)
    assert r.matched_handler == "help_desk" and r.text.endswith(POINTER_TEXT) and not r.expect_followup
    assert await _pending(voice) is None
    r = await voice("yes", person=sarah)
    assert r.matched_handler != "help_desk" and "Sent to" not in r.text
    assert await voice.deliveries() == []
    assert (await voice.db.execute(text("SELECT count(*) FROM chat_threads"))).scalar_one() == threads


@requires_db
@pytest.mark.asyncio
async def test_a_phone_turn_gets_the_same_answer(voice) -> None:
    ctx = Context(room_id="phone-1", person_id=None, online=True, device_kind="phone")
    r = await route(Intent(transcript="how do I change the wake word"), ctx, voice.db)
    assert r.matched_handler == "help_desk" and r.text.endswith(POINTER_TEXT) and not r.expect_followup


@requires_db
@pytest.mark.asyncio
async def test_a_command_in_how_to_clothes_runs_the_command(voice) -> None:
    r = await voice("is there a way to set a timer for ten minutes")
    assert r.matched_handler == "timer", r.text
    r = await voice("how can I restart my router")
    assert r.matched_handler != "help_desk"
    r = await voice("how do I fix my phone screen")
    assert r.matched_handler != "help_desk"


@requires_db
@pytest.mark.parametrize(
    "said",
    ["how do I fix the wifi", "how do I stop the music", "how do I cancel the timer",
     "how do I drop in on the office", "how can I play my playlist"],
)
@pytest.mark.asyncio
async def test_a_how_to_about_a_command_is_answered_never_carried_out(voice, said) -> None:
    """Review 2: these ran the action (reconnected the Wi-Fi, stopped the
    music…). A how-to question is answered from the manual page that
    teaches the command."""
    r = await voice(said)
    assert (r.matched_handler, r.matched_path) == ("help_desk", "fast"), (said, r.matched_handler, r.text)
    assert r.text.startswith("From the manual's ") and r.text.endswith(POINTER_TEXT), r.text
    assert "reconnecting" not in r.text and r.text != "Stopped."
