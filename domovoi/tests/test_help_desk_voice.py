"""Voice "how do I…" (part 9): routing, and who the steps go to.

* The fast path takes a how-to question about Domovoi and DECLINES every
  other one, and no other handler's phrases reach it (the bands in
  ``domovoi/handlers/__init__.py``).
* Delivery is to the asker only: the speaker voice ID recognised, on the
  phones marked "This phone belongs to <them>"; unknown → "Who should I
  send it to?"; no answer → nothing sent; no phone → said plainly; never
  every phone.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi.db.repositories import SessionRepository
from domovoi.handlers import HANDLER_BY_NAME, HANDLERS
from domovoi.handlers.base import as_fast_path, example_phrases_of
from domovoi.handlers.help_desk import HelpDeskHandler, parse_who
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


@pytest.mark.parametrize(
    ("said", "want"),
    [("no", ("decline", "")), ("never mind", ("decline", "")), ("", ("decline", "")),
     ("send it to sarah", ("name", "sarah")), ("to Sarah please", ("name", "sarah")),
     ("it's for alex", ("name", "alex")), ("me", ("self", "")),
     ("what time is it in tokyo right now", ("pass", ""))],
)
def test_who_should_i_send_it_to(said, want) -> None:
    assert parse_who(said) == want


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
async def test_a_how_to_is_answered_from_the_manual_then_offered(voice) -> None:
    r = await voice("how do I change the wake word")
    assert (r.matched_handler, r.matched_path) == ("help_desk", "fast")
    assert r.text.startswith("From the manual's ")
    assert r.text.endswith("Want me to send the steps to your phone?") and r.expect_followup
    assert (await _pending(voice))["kind"] == "core.help_send"


@requires_db
@pytest.mark.asyncio
async def test_a_question_the_help_does_not_hold_routes_on(voice) -> None:
    for said in ("how do i make pancakes", "how do you say hello in spanish", "how do i get to the airport"):
        r = await voice(said)
        assert r.matched_handler != "help_desk", said


@requires_db
@pytest.mark.asyncio
async def test_yes_sends_to_the_recognised_askers_phones_only(voice) -> None:
    sarah = await voice.person("Sarah")
    alex = await voice.person("Alex")
    await voice.phone("android-sarah", sarah)
    await voice.phone("android-alex", alex)
    await voice.phone("android-kitchen", sarah, shared=True)   # a shared screen is nobody's phone
    await voice("how do I change the wake word", person=sarah)
    r = await voice("yes", person=sarah)
    assert r.text.startswith("Sent to your phone. It's in Chat as Help: How do I change the wake word")
    got = await voice.deliveries()
    assert len(got) == 1 and got[0]["person_id"] == sarah and got[0]["thread_person"] == sarah
    assert got[0]["title"] == got[0]["thread_title"] and got[0]["title"].startswith("Help: ")
    assert got[0]["msgs"] == 2  # the question, then the steps


@requires_db
@pytest.mark.asyncio
async def test_no_sends_nothing(voice) -> None:
    sarah = await voice.person("Sarah")
    await voice.phone("android-sarah", sarah)
    await voice("how do I change the wake word", person=sarah)
    r = await voice("no", person=sarah)
    assert r.text == "Okay, I won't send it."
    assert await voice.deliveries() == []


@requires_db
@pytest.mark.asyncio
async def test_no_phone_linked_is_said_plainly(voice) -> None:
    sarah = await voice.person("Sarah")
    await voice("how do I change the wake word", person=sarah)
    r = await voice("yes", person=sarah)
    assert "no phone marked as yours" in r.text and "This phone belongs to" in r.text
    # The app's section since the Settings regroup (SettingsScreen.kt).
    assert "Phones and browsers" in r.text and "Connection" not in r.text
    assert await voice.deliveries() == []


@requires_db
@pytest.mark.asyncio
async def test_an_unknown_speaker_is_asked_who_and_a_name_sends_it(voice) -> None:
    sarah = await voice.person("Sarah")
    await voice.phone("android-sarah", sarah)
    await voice("how do I change the wake word")
    r = await voice("yes")
    assert r.text == "Who should I send it to?" and r.expect_followup
    r = await voice("send it to sarah")
    assert r.text.startswith("Sent to Sarah's phone.")
    assert [d["person_id"] for d in await voice.deliveries()] == [sarah]


@requires_db
@pytest.mark.asyncio
async def test_an_unknown_speaker_with_no_answer_sends_nothing(voice) -> None:
    sarah = await voice.person("Sarah")
    await voice.phone("android-sarah", sarah)
    await voice("how do I change the wake word")
    await voice("yes")
    r = await voice("never mind")
    assert "send" in r.text.lower() and await voice.deliveries() == []
    # A name nobody has, or one two people share: nothing is guessed.
    await voice("how do I change the wake word")
    await voice("yes")
    r = await voice("to bob")
    assert r.matched_handler != "help_desk"          # not a name it knows: routed on
    await voice.person("Sarah")
    await voice("how do I change the wake word")
    await voice("yes")
    r = await voice("sarah")
    assert "won't guess" in r.text
    assert await voice.deliveries() == []


@requires_db
@pytest.mark.asyncio
async def test_a_phone_turn_gets_the_answer_without_the_offer(voice) -> None:
    ctx = Context(room_id="phone-1", person_id=None, online=True, device_kind="phone")
    r = await route(Intent(transcript="how do I change the wake word"), ctx, voice.db)
    assert r.matched_handler == "help_desk" and "Want me to send" not in r.text and not r.expect_followup


# ── round 1 review: limits, the name question, commands in how-to clothes ──


@requires_db
@pytest.mark.asyncio
async def test_the_same_steps_go_once_and_a_person_gets_a_few_an_hour(voice) -> None:
    kamron = await voice.person("Kamron")
    await voice.phone("android-kamron", kamron)
    said = []
    for _ in range(4):                                   # the reviewer's spam loop
        await voice("how do I change the wake word")
        await voice("yes")
        said.append((await voice("kamron")).text)
    assert said[0].startswith("Sent to Kamron's phone.")
    assert all("already sent those steps" in s for s in said[1:])
    assert len(await voice.deliveries()) == 1
    # Different steps: at most PER_PERSON_HOUR an hour, from any session.
    from domovoi.help_desk import delivery

    for q, room in (("how do I add a room", "office"), ("how do I install a plugin", "garage"),
                    ("how do I pair the app", "den")):
        await voice(q, room=room)
        await voice("yes", room=room)
        await voice("kamron", room=room)
    assert len(await voice.deliveries()) == delivery.PER_PERSON_HOUR


@requires_db
@pytest.mark.asyncio
async def test_one_session_sends_a_few_an_hour(voice) -> None:
    from domovoi.help_desk import delivery

    people = [await voice.person(n) for n in ("Ann", "Bea", "Cid", "Dee")]
    for i, pid in enumerate(people):
        await voice.phone(f"android-{i}", pid)
    texts = []
    for name in ("ann", "bea", "cid", "dee"):
        await voice("how do I change the wake word")
        await voice("yes")
        texts.append((await voice(name)).text)
    assert len(await voice.deliveries()) == delivery.PER_SESSION_HOUR
    assert "enough help steps from here" in texts[-1]


@requires_db
@pytest.mark.asyncio
async def test_the_name_question_eats_no_command(voice) -> None:
    await voice("how do I change the wake word")
    assert (await voice("yes")).text == "Who should I send it to?"
    r = await voice("next song")
    assert r.matched_handler == "music" and await voice.deliveries() == []


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
@pytest.mark.asyncio
async def test_a_help_chat_keeps_its_title_and_its_steps_are_the_helps(voice) -> None:
    """Chat history (C2) retitles threads in the background and offers a
    retry on model replies: a "Help: …" chat is neither's."""
    from domovoi.help_desk import delivery

    sarah = await voice.person("Sarah")
    await voice.phone("android-sarah", sarah)
    await voice("how do I change the wake word", person=sarah)
    await voice("yes", person=sarah)
    row = (await voice.db.execute(text(
        "SELECT m.stats->>'model_role' FROM chat_messages m JOIN help_deliveries d ON d.thread_id = m.thread_id "
        "WHERE m.role = 'assistant'"))).scalar_one()
    assert row == "help"
    if await delivery.has_title_generated(voice.db):       # V034 (wf/chat-history) present
        kept = (await voice.db.execute(text(
            "SELECT t.title_generated FROM chat_threads t JOIN help_deliveries d ON d.thread_id = t.id"))).scalar_one()
        assert kept is True


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
    assert r.text.startswith("From the manual's "), r.text
    assert "reconnecting" not in r.text and r.text != "Stopped."
