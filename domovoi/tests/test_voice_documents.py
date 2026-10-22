"""Documents by voice (documents plan D7): "read me …" and "take a letter".

Pure parts first (speakable text, parts, the letter phrases), then the
handler through ``route()`` against the lane database with a Documents
folder of our own (``settings.documents_dir`` → tmp), the model faked at
:func:`domovoi.voice_docs.letters.generate`:

* "read me the house rules" reads a short document whole; a long one says
  its length first, then reads a page or so at a time ("Shall I go on?"),
  and stop / pause / start again / "continue reading" answer it;
* two documents that fit → which one; nothing that fits → said plainly;
* "take a letter to the landlord" → dictation → "end of letter" → a chat
  with the dictation, a D4 draft (version 1, saved) and a .docx in
  Documents → From Chat, never sent; the author from voice ID, or asked;
* the voice_notes collision: "write a letter to …" is a letter, "write a
  note to the letter carrier" is still a note;
* AI features off: the letter refuses before any dictation, reading works;
* a quiet room ends the letter by itself and the room hears where it is.

The satellite gate (both phrases over a satellite's WebSocket) is in
``test_voice_documents_satellite.py``.
"""

from __future__ import annotations

import asyncio
import types

import pytest
from sqlalchemy import text

from domovoi import ai_features
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.drafts import docx_file, pdf_file, templates
from domovoi.handlers import documents as documents_handler
from domovoi.models import Context, Intent
from domovoi.router import route
from domovoi.tests.conftest import requires_db
from domovoi.voice_docs import letters, reading

# ─── material ────────────────────────────────────────────────────────────

RULES = "# House rules\n\nShoes off at the door. Bins go out on Tuesday night.\n\nQuiet after ten.\n"


def _long_text(sections: int = 6) -> str:
    out = ["# Lease agreement", ""]
    for i in range(1, sections + 1):
        out.append(f"## Clause {i}")
        out.append("")
        for j in range(1, 9):
            out.append(f"Clause {i} line {j} says the tenant keeps the flat clean and pays the rent on the first day "
                       f"of the month, and the landlord fixes the heating within three days.")
        out.append("")
    return "\n".join(out)


LETTER_BODY = (
    "Sam Lee\n\nOctober 21, 2026\n\nDear Mr. Okafor,\n\n"
    "The heating in flat 3B has been broken since Monday. Please send someone to fix it by Friday.\n\n"
    "Best wishes,\n\nSam Lee\n"
)


@pytest.fixture
def docs(tmp_path, monkeypatch):
    d = tmp_path / "Documents"
    d.mkdir()
    monkeypatch.setattr(settings, "documents_dir", str(d))
    (d / "House rules.md").write_text(RULES, encoding="utf-8")
    (d / "Lease.docx").write_bytes(docx_file.render(_long_text(), templates.get("letter-block"), title="Lease"))
    (d / "Insurance letter.pdf").write_bytes(
        pdf_file.render("Dear Sam,\n\nYour policy renews in March.\n", templates.get("letter-block"), title="x"))
    (d / "Insurance policy.md").write_text("Cover starts on the first of March.\n", encoding="utf-8")
    (d / "Budget.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    secret = d / ".private"
    secret.mkdir()
    (secret / "Lease copy.md").write_text("hidden\n", encoding="utf-8")
    reading._CACHE.clear()
    return d


class Room:
    """One satellite room's turns through route() (no live socket: the
    letter is written up inline)."""

    def __init__(self, db_session, *, room: str = "kitchen", person_id: int | None = None, **ctx) -> None:
        self.s = db_session
        self.room = room
        self.person_id = person_id
        self.session_id = None
        self.ctx = ctx

    async def say(self, utterance: str, *, trigger: str = "followup", **extra):
        ctx = Context(room_id=self.room, session_id=self.session_id, online=True, person_id=self.person_id,
                      trigger=trigger, **{**self.ctx, **extra})
        resp = await route(Intent(transcript=utterance, room_id=self.room), ctx, self.s)
        # Each turn commits, as the satellite's turn does (its own session).
        await self.s.commit()
        self.session_id = resp.session_id
        return resp


@pytest.fixture
def model(monkeypatch):
    """The fake model: what it was asked, and the letter it writes."""
    calls: list[dict] = []

    async def _generate(state, dictation):
        calls.append({"state": dict(state), "dictation": dictation,
                      "messages": letters.letter_messages(state, dictation, today="October 21, 2026")})
        if getattr(_generate, "fail", False):
            raise letters.LetterFailed("the language model didn't answer")
        return "Here is your letter:\n\n" + LETTER_BODY, "llama3.2:3b"

    monkeypatch.setattr(letters, "generate", _generate)
    _generate.calls = calls
    return _generate


# ─── speakable text (no database) ────────────────────────────────────────

def test_markdown_becomes_sentences_without_markers_headers_or_tables_rules():
    parts = [
        {"label": "page header", "text": "ACME LETTINGS · page"},
        {"label": "Section: Rent", "text": "## Rent\n\n**Rent** is due on the 1st. Late fees apply!\n\n"
                                           "| Month | Amount |\n| --- | --- |\n| May | $900 |"},
        {"label": "page footer", "text": "Page 1 of 9"},
        {"label": "footnotes", "text": "1. A note."},
    ]
    s = reading.sentences_of(parts)
    assert s == ["Rent.", "Rent is due on the 1st.", "Late fees apply!", "Month, Amount.", "May, $900."]
    assert not any("ACME" in x or "Page 1" in x or "A note" in x for x in s)


def test_parts_cut_between_sentences_and_a_run_on_is_cut():
    s = ["One two three.", "Four five six.", "Seven eight nine."]
    assert reading.split_parts(s, 6) == ["One two three. Four five six.", "Seven eight nine."]
    assert reading.split_parts(s, 1) == s
    long = " ".join(["word"] * 130)
    assert [len(x.split()) for x in reading._cut_long(long)] == [60, 60, 10]


def test_length_line_and_short():
    sp = reading.Spoken(rel="a", title="a", sentences=("x " * 820,), words=820)
    assert reading.length_line(sp) == "It's 3 pages, about 5 minutes."
    assert not sp.is_short()
    one = reading.Spoken(rel="a", title="a", sentences=("x",), words=120, pdf_pages=1)
    assert reading.length_line(one) == "It's one page, about a minute." and one.is_short()


@pytest.mark.parametrize("said,kind,template,title,content", [
    ("take a letter", "letter", "letter-block", "Letter", ""),
    ("take a letter to the landlord", "letter", "letter-personal", "Letter to the landlord", ""),
    ("write a letter to the landlord saying the heating is broken", "letter", "letter-personal",
     "Letter to the landlord", "the heating is broken"),
    ("take a letter: the heating is broken", "letter", "letter-block", "Letter", "the heating is broken"),
    ("draft a cover letter for the acme job", "cover_letter", "letter-block", "Cover letter for the acme job", ""),
    ("i'd like to write a thank you letter to sam", "letter", "letter-block", "Thank you letter to sam", ""),
    ("write my resume", "resume", "resume-plain", "Resume", ""),
])
def test_letter_requests(said, kind, template, title, content):
    ask = letters.parse_request(said)
    assert ask is not None
    assert (ask.kind, ask.template, ask.title, ask.content) == (kind, template, title, content)


@pytest.mark.parametrize("said", [
    "write a note to the letter carrier", "write down the letter came today", "take the letter to the post office",
    "write an email to the landlord", "write a poem about a letter", "jot down write a letter",
])
def test_not_letter_requests(said):
    assert letters.LETTER_RE.match(said) is None


def test_dictation_words():
    assert letters.END_RE.match("end of letter") and letters.END_RE.match("okay, that's it")
    assert letters.END_RE.match("i'm done") and not letters.END_RE.match("the heating is done for")
    assert letters.END_TAIL_RE.match("and fix it by friday, end of letter").group("body") == "and fix it by friday"
    assert letters.CANCEL_RE.match("cancel the letter") and letters.CANCEL_RE.match("never mind")
    assert letters.UNDO_RE.match("scratch that")
    assert letters.author_from_reply("it's from sam lee") == ("Sam Lee", False)
    assert letters.author_from_reply("nobody") == (None, True)
    assert letters.clean_dictation("dear sir new paragraph the heating") == "dear sir\n\nthe heating"


def test_the_prompt_holds_the_dictation_as_data_with_who_its_from():
    state = letters.new_state(letters.parse_request("take a letter to the landlord"), author="Sam Lee",
                              author_id=None, room="kitchen")
    msgs = letters.letter_messages(state, "the heating is broken", today="October 21, 2026")
    user = msgs[1]["content"]
    assert "<<<DICTATION\nthe heating is broken\nDICTATION>>>" in user
    assert "From (sign it with this name): Sam Lee" in user
    assert "Dear [name]," in user and "Today's date: October 21, 2026" in user
    assert "Never invent" in msgs[0]["content"]


# ─── reading, through the router ─────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_a_short_document_is_read_whole(db_session, docs):
    r = await Room(db_session).say("read me the house rules", trigger="wake_word")
    assert r.matched_handler == "documents" and r.matched_path == "fast"
    assert r.text == "House rules. Shoes off at the door. Bins go out on Tuesday night. Quiet after ten."
    assert not r.expect_followup


@requires_db
@pytest.mark.asyncio
async def test_a_long_document_says_its_length_then_reads_part_by_part(db_session, docs):
    room = Room(db_session)
    r = await room.say("read me the lease", trigger="wake_word")
    assert r.text.startswith("Lease. It's ") and r.text.endswith("The whole thing, or just the start?")
    assert r.expect_followup
    r = await room.say("the whole thing")
    assert r.text.startswith("Lease agreement. Clause 1.") and r.text.endswith("more. Shall I go on?")
    assert r.expect_followup and r.matched_path == "confirmation"
    first = r.text
    words = len(first.split())
    assert 300 < words < 460
    r = await room.say("yes")
    assert r.text != first and "Clause 1 line 1 " not in r.text
    second = r.text
    r = await room.say("read that again")
    assert r.text == second
    r = await room.say("start again")
    assert r.text == first
    r = await room.say("stop")
    assert r.text == "Okay, I've stopped reading."
    r = await room.say("continue reading", trigger="wake_word")
    # Nothing is being read any more: not ours (declined, routed on).
    assert r.matched_handler != "documents"


@requires_db
@pytest.mark.asyncio
async def test_just_the_start_then_continue_reading_later(db_session, docs):
    room = Room(db_session)
    await room.say("read me the lease", trigger="wake_word")
    r = await room.say("just the start")
    assert r.text.endswith("say continue reading to hear it.") and not r.expect_followup
    assert len(r.text.split()) < 200
    r = await room.say("what time is it", trigger="wake_word")
    assert r.matched_handler == "clock"
    r = await room.say("continue reading", trigger="wake_word")
    assert r.matched_handler == "documents" and r.text.endswith("Shall I go on?")
    r = await room.say("pause")
    assert r.text == "Okay. Say continue reading when you want the rest."
    # Read to the end.
    for _ in range(12):
        r = await room.say("continue reading", trigger="wake_word")
        if r.text.endswith("That's the end."):
            break
    assert r.text.endswith("That's the end.")


@requires_db
@pytest.mark.asyncio
async def test_a_part_cut_off_by_the_wake_word_is_read_again(db_session, docs):
    room = Room(db_session)
    await room.say("read me the lease", trigger="wake_word")
    first = (await room.say("the whole thing")).text
    r = await room.say("pause", trigger="barge_in")
    assert r.text.startswith("Okay.")
    r = await room.say("continue reading", trigger="wake_word")
    assert r.text == first


@requires_db
@pytest.mark.asyncio
async def test_two_that_fit_ask_which_and_none_says_so(db_session, docs):
    room = Room(db_session)
    r = await room.say("read me the insurance documents", trigger="wake_word")
    assert r.text == "I found two: Insurance letter and Insurance policy. Which one?" and r.expect_followup
    r = await room.say("the policy")
    assert r.text == "Cover starts on the first of March."
    r = await room.say("read me the tax letter", trigger="wake_word")
    assert r.text.startswith("I couldn't find a document called the tax letter in Documents.")
    r = await room.say("read me a document", trigger="wake_word")
    assert r.text.startswith("Which document?")
    # Hidden folders, spreadsheets: never offered.
    assert [d.title for d in reading.find("lease")] == ["Lease"]
    assert reading.find("budget") == []


@requires_db
@pytest.mark.asyncio
async def test_a_misheard_name_is_asked_about_never_read_on_a_guess(db_session, docs):
    """The vt.py satellite run (2026-10-21): Whisper heard "read me the
    leaves" for "read me the lease"."""
    room = Room(db_session)
    r = await room.say("read me the leaves", trigger="wake_word")
    assert r.text == "Did you mean Lease?" and r.expect_followup
    r = await room.say("yes")
    assert r.text.startswith("Lease. It's ")
    r = await room.say("read me the hose rules", trigger="wake_word")
    assert r.text == "Did you mean House rules?"
    r = await room.say("no")
    assert r.text == "Okay."
    # Nothing near: not a document, routed on as before.
    r = await room.say("read me a limerick", trigger="wake_word")
    assert r.matched_handler != "documents"


@requires_db
@pytest.mark.asyncio
async def test_reading_works_with_ai_off(db_session, docs):
    with ai_features.override(False):
        r = await Room(db_session).say("read me the house rules", trigger="wake_word")
    assert r.text.startswith("House rules. Shoes off")


@requires_db
@pytest.mark.asyncio
async def test_near_misses_keep_their_handlers(db_session, docs):
    room = Room(db_session)
    assert (await room.say("read me my last note", trigger="wake_word")).matched_handler == "voice_notes"
    assert (await room.say("write a note to the letter carrier", trigger="wake_word")).matched_handler == "voice_notes"
    assert (await room.say("read me the headlines", trigger="wake_word")).matched_handler == "news"


# ─── taking a letter ─────────────────────────────────────────────────────

async def _person(name: str) -> int:
    async with session_scope() as s:
        pid = (await s.execute(text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name})).scalar_one()
    return int(pid)


async def _rows(title: str):
    async with session_scope() as s:
        t = (await s.execute(text("SELECT id, title, person_id FROM chat_threads WHERE title = :t ORDER BY id DESC"),
                             {"t": title})).first()
        if t is None:
            return None, [], None
        msgs = (await s.execute(text("SELECT role, content, source, draft FROM chat_messages WHERE thread_id = :t "
                                     "ORDER BY id"), {"t": t[0]})).all()
        v = (await s.execute(text(
            "SELECT d.kind, d.template, v.version, v.body, v.model, v.saved, v.person_id FROM chat_drafts d "
            "JOIN chat_draft_versions v ON v.draft_id = d.id WHERE d.thread_id = :t"), {"t": t[0]})).first()
    return t, msgs, v


@requires_db
@pytest.mark.asyncio
async def test_take_a_letter_by_voice_is_a_saved_draft_never_sent(db_session, docs, model):
    pid = await _person("Sam Lee")
    room = Room(db_session, person_id=pid)
    r = await room.say("take a letter to the landlord", trigger="wake_word")
    assert r.text == "Go ahead. Say end of letter when you're done." and r.expect_followup
    r = await room.say("the heating in flat 3B has been broken since monday")
    assert r.text == "Go on." and r.expect_followup and r.matched_path == "confirmation"
    r = await room.say("please send someone to fix it by friday")
    assert r.text == "Go on."
    r = await room.say("end of letter")
    assert r.text == "Saved as Letter to the landlord, in Documents, From Chat. Say read it back to hear it."
    assert not r.expect_followup

    saved = docs / "From Chat" / "Letter to the landlord v1.docx"
    assert saved.is_file()
    (call,) = model.calls
    assert call["dictation"] == ("the heating in flat 3b has been broken since monday please send someone to "
                                 "fix it by friday")
    assert call["state"]["author"] == "Sam Lee" and call["state"]["template"] == "letter-personal"
    t, msgs, v = await _rows("Letter to the landlord")
    assert t[2] == pid
    assert [m[0] for m in msgs] == ["user", "assistant"]
    assert msgs[0][2] == "voice" and "taken by voice in the kitchen" in msgs[0][1]
    assert "the heating in flat 3b" in msgs[0][1]
    assert msgs[1][3]["id"] and msgs[1][3]["version"] == 1 and msgs[1][3]["action"] == "create"
    assert "Nothing was sent anywhere." in msgs[1][1]
    kind, template, version, body, model_name, saved_json, vperson = v
    assert (kind, template, version, model_name, vperson) == ("letter", "letter-personal", 1, "llama3.2:3b", pid)
    assert body.startswith("Sam Lee") and "Here is your letter" not in body  # the model's remark dropped
    assert saved_json[0]["format"] == "docx" and saved_json[0]["rel_path"] == "From Chat/Letter to the landlord v1.docx"
    assert saved_json[0]["check"]["ok"] is True

    r = await room.say("read it back", trigger="wake_word")
    assert r.text.startswith("Sam Lee. October 21, 2026. Dear Mr. Okafor,") and "broken since Monday" in r.text


@requires_db
@pytest.mark.asyncio
async def test_unknown_voice_is_asked_who_its_from(db_session, docs, model):
    room = Room(db_session)
    r = await room.say("write a letter to grandma", trigger="wake_word")
    assert r.text == "Who's it from?" and r.expect_followup
    r = await room.say("it's from sam")
    assert r.text == "Thanks, Sam. Go ahead. Say end of letter when you're done."
    await room.say("we loved the visit")
    await room.say("scratch that")
    await room.say("thank you for the jumper")
    r = await room.say("that's it")
    assert r.text.startswith("Saved as Letter to grandma")
    assert model.calls[0]["dictation"] == "thank you for the jumper"
    assert model.calls[0]["state"]["author"] == "Sam"


@requires_db
@pytest.mark.asyncio
async def test_inline_content_cancel_and_the_failures_said(db_session, docs, model):
    pid = await _person("Sam Lee")
    room = Room(db_session, person_id=pid)
    r = await room.say("write a letter to the council saying the bins weren't collected", trigger="wake_word")
    assert r.text == "Got that. Go on, or say end of letter when you're done."
    r = await room.say("cancel the letter")
    assert r.text == "Okay, I've thrown that letter away. Nothing was saved."
    assert not (docs / "From Chat").exists() and model.calls == []

    model.fail = True
    await room.say("take a letter to the council", trigger="wake_word")
    await room.say("the bins weren't collected")
    r = await room.say("end of letter")
    assert r.text == ("I couldn't write it up: the language model didn't answer. Your words are in Chat, in "
                      "Letter to the council.")
    t, msgs, v = await _rows("Letter to the council")
    assert [m[0] for m in msgs] == ["user", "assistant"] and v is None
    assert "the bins weren't collected" in msgs[0][1]


@requires_db
@pytest.mark.asyncio
async def test_with_ai_off_a_letter_refuses_before_dictation(db_session, docs, model):
    with ai_features.override(False):
        r = await Room(db_session).say("take a letter to the landlord", trigger="wake_word")
    assert r.text.startswith("Writing a letter needs the AI features, and they're turned off.")
    assert not r.expect_followup and model.calls == []


@requires_db
@pytest.mark.asyncio
async def test_phone_resume_and_the_old_collision(db_session, docs, model):
    r = await Room(db_session, room="phone-x", device_kind="phone").say("take a letter", trigger="push_to_talk")
    assert r.text.startswith("On the phone, type it in a chat instead")
    r = await Room(db_session).say("write my resume", trigger="wake_word")
    assert r.matched_handler == "documents" and "start it in Chat" in r.text
    # "write a letter to …" used to be saved as a voice note "a letter to …".
    r = await Room(db_session).say("write a letter to the landlord", trigger="wake_word")
    assert r.matched_handler == "documents"
    async with session_scope() as s:
        notes = (await s.execute(text("SELECT count(*) FROM voice_notes"))).scalar_one()
    assert notes == 0


@requires_db
@pytest.mark.asyncio
async def test_a_command_after_the_room_stopped_listening_ends_the_letter(db_session, docs, model, monkeypatch):
    spoken: list[tuple[str, str]] = []

    async def _announce(self, room_id, text_):
        spoken.append((room_id, text_))
        return [room_id]

    monkeypatch.setattr("domovoi.sdk.speech.SpeechAPI.announce", _announce)
    pid = await _person("Sam Lee")
    room = Room(db_session, person_id=pid)
    await room.say("take a letter to the landlord", trigger="wake_word")
    await room.say("the heating is broken")
    r = await room.say("what time is it", trigger="wake_word")
    assert r.matched_handler == "clock"
    for _ in range(100):
        if spoken:
            break
        await asyncio.sleep(0.05)
    assert spoken == [("kitchen", "Saved as Letter to the landlord, in Documents, From Chat. Say read it back to "
                                  "hear it.")]


@requires_db
@pytest.mark.asyncio
async def test_a_quiet_room_ends_the_letter(db_session, docs, model, monkeypatch):
    """No "end of letter": the follow-up window closes with nothing said,
    and the room hears where the letter is once it's written."""
    announced: list[str] = []

    class _Live:
        room_id = "kitchen"
        utterance_active = False
        _response_task = None

        async def announce(self, text_, **kw):
            announced.append(text_)

    live = _Live()
    app = types.SimpleNamespace(state=types.SimpleNamespace(active_sessions={"kitchen": live}))
    from domovoi.sdk import speech

    monkeypatch.setattr(speech, "_active_sessions_provider", lambda: app.state.active_sessions)
    monkeypatch.setattr(letters, "IDLE_SEC", 0.2)
    monkeypatch.setattr(letters, "QUIET_POLL_SEC", 0.05)
    pid = await _person("Sam Lee")
    room = Room(db_session, person_id=pid, app=app)
    await room.say("take a letter to the landlord", trigger="wake_word")
    live.utterance_active = True          # the person is speaking: the timer waits
    r = await room.say("the heating is broken")
    assert r.text == "Go on."
    await asyncio.sleep(0.5)
    assert announced == []
    live.utterance_active = False
    for _ in range(200):
        if announced:
            break
        await asyncio.sleep(0.05)
    assert announced == ["Saved as Letter to the landlord, in Documents, From Chat. Say read it back to hear it."]
    assert (docs / "From Chat" / "Letter to the landlord v1.docx").is_file()
    # The dictation is no longer parked: the next turn is an ordinary one.
    r = await room.say("what time is it", trigger="wake_word")
    assert r.matched_handler == "clock"
    assert documents_handler._IDLE == {}


@requires_db
@pytest.mark.asyncio
async def test_nothing_dictated_saves_nothing(db_session, docs, model, monkeypatch):
    announced: list[str] = []

    class _Live:
        room_id = "kitchen"

        def awaiting_reply(self):
            return False

        async def announce(self, text_, **kw):
            announced.append(text_)

    app = types.SimpleNamespace(state=types.SimpleNamespace(active_sessions={"kitchen": _Live()}))
    from domovoi.sdk import speech

    monkeypatch.setattr(speech, "_active_sessions_provider", lambda: app.state.active_sessions)
    monkeypatch.setattr(letters, "IDLE_SEC", 0.1)
    pid = await _person("Sam Lee")
    room = Room(db_session, person_id=pid)
    await room.say("take a letter to the landlord", trigger="wake_word")
    r = await room.say("end of letter")
    assert r.text == "There's nothing in the letter yet, so nothing was saved."
    # The room goes quiet right after "take a letter": nothing saved either.
    room = Room(db_session, person_id=pid, app=app)
    await room.say("take a letter to the landlord", trigger="wake_word")
    for _ in range(100):
        if announced:
            break
        await asyncio.sleep(0.05)
    assert announced == ["I didn't hear a letter, so nothing was saved."]
    assert not (docs / "From Chat").exists() and model.calls == []
