"""House facts (1.4.1, N2 with N7's capability answers): privacy questions
and "can you …?" answered from this house's real state, never from a model
and never a guess (domovoi/house_facts.py, handlers/house_facts.py, and the
web chat's hook in web/backend/api/chat.py).

The expected answers are CAPABILITY-QUESTIONS §2, §3, §5, §9 and §15, as
exact text over the house fixtures: recordings on/off per room, voice
history kept 30/90/365 days or forever, the internet answer Yes /
Sometimes / No / unanswered, Friend Houses on/off, online music
suggestions on/off, a satellite room against a phone in room mode.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from domovoi import house_facts as hf
from domovoi.clients.ollama import VOICE_QA_SYSTEM_PROMPT
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db

R = dataclasses.replace

BASE = hf.HouseFacts(
    device=hf.SATELLITE, room_id="kitchen", recordings=False, recording_days=14, voice_keep="90",
    internet="always", ai_on=True, lookups_ask_first=True, friend_houses=True, friends=1,
    music_suggestions=False, calendar_links=0, background=("the news headlines",),
    vision_model=True, news_on=True,
)

ADMIN_VH = "An admin can change how long in Settings → Household → People, then Voice history."
NO_REC = "No recordings of your voice are kept."
INET_CHANGE = "An admin can change that in Settings → Privacy → Internet."


# ─── The exact answers, over the house fixtures ───────────────────────────

MATRIX = [
    # §9.1 / §15.1: a satellite room
    ("listening", BASE,
     "I only listen for my wake word, and that's checked right here on this device. Nothing is sent "
     "until I hear it; then what you say goes to this house's own server."),
    # §9.2: a phone in room mode adds the notification and the mic dot
    ("listening", R(BASE, device=hf.ROOM_PHONE),
     "I only listen for my wake word, and that's checked right here on this device. Nothing is sent "
     "until I hear it; then what you say goes to this house's own server. While this phone listens, "
     "it shows a notification and the microphone dot."),
    ("listening", R(BASE, device=hf.WEB_CHAT),
     "Not here: this is a typed chat, with no microphone. The rooms listen only for the wake word, on "
     "each device, and send nothing until they hear it."),
    # §9.3 / §15.2: recordings off (the default)
    ("recording", BASE,
     "No. This room keeps no recordings of your voice. I do keep the text of what's said for 90 days."),
    # §9.4 / §15.3: recordings on for this room
    ("recording", R(BASE, recordings=True),
     "Yes. This room keeps recordings of the commands said here for 14 days, to tune when I stop "
     "listening. An admin can turn that off in Settings → Household → Rooms."),
    ("recording", R(BASE, recordings=None), "I can't check that right now."),
    ("recording", R(BASE, device=hf.ROOM_PHONE, recordings=True),
     "Yes. This room keeps recordings of the commands said here for 14 days, to tune when I stop "
     "listening. An admin can turn that off in Settings → Household → Rooms."),
    # §9.5 / §15.4: internet No — nothing but the server
    ("voice_goes", R(BASE, internet="never"),
     "Only to this house's own server. Your voice never leaves the house."),
    # §9.6: internet Yes, lookups on
    ("voice_goes", BASE,
     "Only to this house's own server. Your voice never leaves the house. When you ask me to look "
     "something up, only the search words go out."),
    # §15.5
    ("leaves_house", R(BASE, internet="never", background=()),
     "Nothing. This house is set to never use the internet, so everything stays home."),
    # §15.6, with this house's own background features named
    ("leaves_house", BASE,
     "Your voice never leaves the house. When you ask for something that needs the internet, like a "
     "web lookup or the weather, only what that needs goes out. On its own, this house also goes "
     "online for the news headlines."),
    ("leaves_house", R(BASE, internet="sometimes",
                       background=("the news headlines", "lyrics for songs that have none")),
     "Your voice never leaves the house. When you ask for something that needs the internet, like a "
     "web lookup or the weather, only what that needs goes out. On its own, this house also goes "
     "online for the news headlines and lyrics for songs that have none, when the connection is up."),
    ("leaves_house", R(BASE, background=()),
     "Your voice never leaves the house. When you ask for something that needs the internet, like a "
     "web lookup or the weather, only what that needs goes out. Nothing else goes online on its own."),
    ("leaves_house", R(BASE, internet=None), "I can't check that right now."),
    # §15.7 / 15.8 / 15.9 / 15.10: every retention value
    ("keep", BASE,
     f"Yes. I keep the text of what you say, and my answers, for 90 days, then delete it. {NO_REC} {ADMIN_VH}"),
    ("keep", R(BASE, voice_keep="30"),
     f"Yes. I keep the text of what you say, and my answers, for 30 days, then delete it. {NO_REC} {ADMIN_VH}"),
    ("keep", R(BASE, voice_keep="365"),
     f"Yes. I keep the text of what you say, and my answers, for a year, then delete it. {NO_REC} {ADMIN_VH}"),
    ("keep", R(BASE, voice_keep="forever"),
     f"Yes. I keep the text of what you say, and my answers, until someone deletes it. {NO_REC} {ADMIN_VH}"),
    ("keep", R(BASE, recordings=True),
     "Yes. I keep the text of what you say, and my answers, for 90 days, then delete it. This room also "
     f"keeps recordings of the commands said here for 14 days. {ADMIN_VH}"),
    ("keep", R(BASE, voice_keep=None), "I can't check that right now."),
    ("keep", R(BASE, device=hf.WEB_CHAT),
     "Yes. This chat is kept until someone deletes it, and everyone in the house can read it."),
    # §5.10 / §9.8: the internet answer, all four
    ("internet", BASE,
     f"Yes. This house is set to use the internet when something needs it. {INET_CHANGE}"),
    ("internet", R(BASE, internet="sometimes"),
     "Sometimes. This house uses the internet for small things when the connection is up, and skips "
     f"the big downloads. {INET_CHANGE}"),
    ("internet", R(BASE, internet="never"),
     f"No. This house is set to never use the internet, so everything stays home. {INET_CHANGE}"),
    ("internet", R(BASE, internet=""),
     "Yes, when it can. Nobody has answered the internet question yet, so I use the internet when it's "
     "there. An admin can answer it in Settings → Privacy → Internet."),
    # §9.7 / §15.14 / §9.11 / §15.16
    ("chats", BASE,
     "Everyone in this house: chats on this house's server can be read on every paired phone and "
     "browser. A message you send to a friend's house is seen there too, and only that message."),
    ("chats", R(BASE, friend_houses=False, friends=0),
     "Everyone in this house: chats on this house's server can be read on every paired phone and browser."),
    # §15.15
    ("friend_houses", BASE,
     "No. A friend's house only sees that this house is running and reachable, never who's home, and "
     "only the messages and lists you send or share with it."),
    ("friend_houses", R(BASE, friend_houses=False, friends=0),
     "No. Friend Houses is off, so no other house can reach this one."),
    # §15.17
    ("private", BASE,
     f"Not entirely: anyone in the room can hear us. I keep the text of what's said for 90 days. {NO_REC}"),
    ("private", R(BASE, device=hf.WEB_CHAT),
     "This chat is shared with everyone in the house: every paired phone and browser can read it."),
    # §9.12: online music suggestions on / off
    ("listening_history", R(BASE, music_suggestions=True),
     "No. It stays in this house; only people here can see it, on the dashboard. For music suggestions "
     "I ask an online music catalogue about the artists and songs in your library, by catalogue number "
     "only, never what anyone played. An admin can turn that off in Settings → Household → Music mix."),
    ("listening_history", BASE,
     "No. It stays in this house; only people here can see it, on the dashboard."),
    # D3 by voice: never a guess
    ("internet_for_that", BASE, "I can't tell from a voice turn yet. Ask in chat and I can say exactly."),
]


@pytest.mark.parametrize(("qid", "facts", "expected"), MATRIX,
                         ids=[f"{q}-{i}" for i, (q, _f, _e) in enumerate(MATRIX)])
def test_the_exact_answer(qid: str, facts: hf.HouseFacts, expected: str) -> None:
    assert hf.answer(qid, facts) == expected


@pytest.mark.parametrize(("qid", "on", "off"), [
    ("recording", R(BASE, recordings=True), BASE),
    ("keep", R(BASE, voice_keep="30"), R(BASE, voice_keep="forever")),
    ("internet", BASE, R(BASE, internet="never")),
    ("friend_houses", BASE, R(BASE, friend_houses=False, friends=0)),
    ("listening_history", R(BASE, music_suggestions=True), BASE),
    ("listening", R(BASE, device=hf.ROOM_PHONE), BASE),
])
def test_toggle_pairs_differ(qid, on, off) -> None:
    assert hf.answer(qid, on) != hf.answer(qid, off)


def test_no_answer_names_a_service_or_plugin() -> None:
    banned = ("listenbrainz", "lrclib", "musicbrainz", "cover art archive", "wikipedia", "brave",
              "searxng", "kiwix", "romm", "imagegen", "comfyui", "tor ")
    said = [hf.answer(q, f) for q, f, _e in MATRIX] + [hf.facts_block(BASE)]
    said += [c.text or "" for c in hf.CAPABILITIES]
    for line in said:
        assert not any(b in line.lower() for b in banned), line


def test_the_facts_block_carries_the_one_instruction() -> None:
    block = hf.facts_block(BASE)
    assert block.endswith(
        "Answer only from these facts. If they don't cover the question, say: "
        "\"I'm not sure — Settings → Privacy shows exactly what's kept.\""
    )
    assert "- Command recordings in this room: none kept." in block
    assert "- Internet: yes." in block


def test_the_qa_hedge_is_unchanged() -> None:
    # Owner, measured (N7): the spoken QA prompt keeps its hedge, word for word.
    assert "\"I can't do that yet, I don't think. Maybe try again.\"" in VOICE_QA_SYSTEM_PROMPT


# ─── Which questions are in the closed set ────────────────────────────────

@pytest.mark.parametrize(("said", "qid"), [
    ("are you always listening", "listening"),
    ("are you listening right now", "listening"),
    ("are you recording me", "recording"),
    ("do you record what i say", "recording"),
    ("where does my voice go", "voice_goes"),
    ("do you send my voice anywhere", "voice_goes"),
    ("does anything leave the house", "leaves_house"),
    ("do you keep what i say", "keep"),
    ("how long do you keep it", "keep"),
    ("do you use the internet", "internet"),
    ("who can see my messages", "chats"),
    ("can other people hear my chats", "chats"),
    ("who can see my chat history", "chats"),
    ("can my friend's house hear me", "friend_houses"),
    ("can my friend's house see my stuff", "friend_houses"),
    ("is this conversation private", "private"),
    ("does anyone else see my listening history", "listening_history"),
    ("did you use the internet for that", "internet_for_that"),
    ("did that come from the internet", "internet_for_that"),
])
def test_closed_set_questions(said: str, qid: str) -> None:
    assert hf.question_id(hf.normalize(said)) == qid


# ─── Router neighbours: plan_route, the router's own decision ─────────────

def _plan(said: str) -> str | None:
    from domovoi.router import plan_route

    p = plan_route(said)
    return p.handler.name if p else None


@pytest.mark.parametrize("said", [
    "Are you recording me?", "Are you always listening?", "Do you keep what I say?",
    "Does anything leave the house?", "Did you use the internet for that?",
    "Can you order pizza?", "Can you set timers?", "Can you turn on the lights?",
    "Please, can you order pizza?", "Are you able to order pizza?", "Can you download videos?",
    "Is this conversation private?", "Who can see my messages?",
])
def test_these_land_on_house_facts(said: str) -> None:
    assert _plan(said) == "house_facts"


@pytest.mark.parametrize(("said", "handler"), [
    # Commands are carried out, never answered as questions.
    ("record a voice memo", None),
    ("jot down buy milk", "voice_notes"),
    ("set a timer for 10 minutes", "timer"),
    ("can you set a timer for ten minutes", "timer"),
    ("can you play some jazz in the kitchen", "music"),
    ("play some jazz", "music"),
    ("can you tell me the time", "clock"),
    ("can you add bananas", "lists"),
    ("what do you remember about me", "memory"),
    ("what can you do", "help"),
    ("how do i add a room", "help_desk"),
    ("are you sure", "double_check"),
    ("any messages", "friend_houses"),
    ("tell everyone dinner's ready", "intercom"),
    ("drop in on the kitchen", "dropin"),
    # An X the table doesn't hold: no answer here, routed as before.
    ("can you juggle", None),
    ("can you tell me a joke", None),
    ("can you translate this", None),
])
def test_neighbours_are_not_poached(said: str, handler: str | None) -> None:
    assert _plan(said) == handler


def test_every_example_phrase_of_another_handler_stays_its_own() -> None:
    from domovoi.handlers import HANDLERS
    from domovoi.handlers.base import example_phrases_of

    for h in HANDLERS:
        if h.name == "house_facts" or h.plugin_slug is not None:
            continue
        for p in example_phrases_of(h):
            assert _plan(p) != "house_facts", (h.name, p)
            assert _plan(f"can you {p}") != "house_facts", (h.name, p)


def test_kept_question_only_for_a_table_x() -> None:
    from domovoi.router import strip_leading_filler

    assert strip_leading_filler("can you order pizza") == "can you order pizza"
    assert strip_leading_filler("please can you order pizza") == "can you order pizza"
    assert strip_leading_filler("can you juggle") == "juggle"
    assert strip_leading_filler("can you set a timer for 10 minutes") == "set a timer for 10 minutes"
    assert strip_leading_filler("could you order pizza") == "order pizza"
    # A generic object is the question after any lead (G1).
    assert strip_leading_filler("could you remember things about me") == "can you remember things about me"
    assert strip_leading_filler("would you please remember stuff") == "can you remember stuff"


# ─── G1: "can you <verb> <generic object>?" is a question, never a command ─

_GENERIC_ASKS = [
    # memory (case 6.9)
    ("Can you remember things about me?", "memory"),
    ("can you remember stuff", "memory"), ("can you remember facts", "memory"),
    ("can you remember anything", "memory"), ("can you remember everything", "memory"),
    ("can you remember what I tell you", "memory"), ("can you remember what I say", "memory"),
    ("can you remember things about us", "memory"), ("can you remember stuff like that", "memory"),
    ("can you remember things like my birthday", "memory"), ("can you remember things for me", "memory"),
    ("could you remember things about me", "memory"), ("would you please remember stuff", "memory"),
    ("are you able to remember things", "memory"), ("do you remember things about me", "memory"),
    ("can you please remember things about me", "memory"),
    ("can you forget things", "forget"), ("can you forget stuff about me", "forget"),
    ("could you forget everything", "forget"), ("can you stop remembering things", "forget"),
    ("can you forget that stuff", "forget"),
    # lists
    ("can you add things to lists?", "list_add"), ("can you add stuff to my shopping list?", "list_add"),
    ("can you put things on my list", "list_add"), ("could you add stuff to the shopping", "list_add"),
    ("can you remove things from lists", "list_remove"),
    ("can you take things off my list", "list_remove"), ("can you cross things off my list", "list_remove"),
    # notes, announcements
    ("can you jot down things", "note_things"), ("could you jot things down", "note_things"),
    ("can you note things down", "note_things"), ("can you save stuff", "note_things"),
    ("can you tell everyone things", "announce_things"), ("can you announce stuff", "announce_things"),
]


@pytest.mark.parametrize(("said", "row"), _GENERIC_ASKS)
def test_a_generic_object_is_answered_by_voice(said: str, row: str) -> None:
    from domovoi.router import plan_route

    p = plan_route(said)
    assert p is not None and p.handler.name == "house_facts", (said, p and p.handler.name)
    found = hf.capability_of(p.transcript)
    assert found is not None and found[0].key == row


@pytest.mark.parametrize(("said", "row"), _GENERIC_ASKS)
def test_a_generic_object_is_answered_in_chat(said: str, row: str) -> None:
    found = hf.chat_capability(hf.normalize(said))
    assert found is not None and found[0].key == row, said


@pytest.mark.asyncio
async def test_the_memory_capability_answers() -> None:
    assert await _cap("Can you remember things about me?") == (
        'Yes. Say "remember that …" and I\'ll keep it for you; "what do you remember about me?" lists it.')
    assert await _cap("can you forget things") == hf.FORGET_YES
    assert await _cap("can you add things to lists") == 'Yes. Say "add milk to the shopping list".'
    assert await _cap("can you remove things from lists") == 'Yes. Say "cross milk off the shopping list".'


@pytest.mark.parametrize(("said", "handler"), [
    # The commands with a real object still act.
    ("can you remember that my locker is 12", "memory"),
    ("remember that things like keys go in the bowl", "memory"),
    ("remember the thing that my code is 4", "memory"),
    ("forget that I like jazz", "memory"),
    ("can you add milk to the shopping list", "lists"),
    ("add things to do to my to-do list", "lists"),
    ("cross off milk", "lists"),
    ("jot down buy milk", "voice_notes"),
    ("tell everyone dinner's ready", "intercom"),
    ("announce stuff is ready", "intercom"),
    ("could you order pizza", None),
])
def test_a_real_object_is_still_the_command(said: str, handler: str | None) -> None:
    assert _plan(said) == handler


@pytest.mark.parametrize("body", [
    "things about me", "stuff", "facts", "anything", "everything", "what i tell you", "what i say",
    "things about us", "stuff like that", "that stuff", "things for me", "things like my birthday",
])
def test_generic_bodies(body: str) -> None:
    assert hf.is_generic_object(body)


@pytest.mark.parametrize("body", [
    "my locker is 12", "things like keys go in the bowl", "everything is in the garage",
    "the stuff for the party", "i like jazz",
])
def test_real_bodies(body: str) -> None:
    assert not hf.is_generic_object(body)


# ─── The capability table ─────────────────────────────────────────────────

async def _cap(said: str, facts: hf.HouseFacts = BASE) -> str | None:
    found = hf.capability_of(hf.normalize(said))
    assert found is not None, said
    return await hf.capability_answer(found[0], facts)


@pytest.mark.asyncio
@pytest.mark.parametrize(("said", "expected"), [
    # §2: a yes, with the handler's own phrase
    ("Can you set timers?", 'Yes. Say "set a timer for 10 minutes".'),
    ("Can you set reminders?", 'Yes. Say "remind me to call Mom in 20 minutes".'),
    ("Can you play music in other rooms?", 'Yes. Say "play some music in the garage".'),
    ("Can you make an announcement to the whole house?", "Yes. Say \"tell everyone dinner's ready\"."),
    ("Can you write documents?", 'Yes. Say "take a letter to the landlord".'),
    ("Can you read me an audiobook?", 'Yes. Say "resume my audiobook".'),
    ("Can you do maths?", "Yes. Say \"what's 15 percent of 80\"."),
    ("Can you take voice notes?", 'Yes. Say "jot down buy milk and eggs".'),
    ("Can you look at a picture?", "Yes, in chat: attach a picture and ask about it."),
    ("Can you watch a video?", "Yes, in chat: attach a video up to 3 minutes long and ask about it."),
    # §2.15 / §3: a plain no
    ("Can you add things to my calendar?",
     "No, calendars here are read-only. To hear them, say \"what's on my calendar today\"."),
    ("Can you order pizza?", "No, I can't order food."),
    ("Can you send an email?", "No, I can't send or read email."),
    ("Can you call my mom?", "No, I can't make phone calls."),
    ("Can you buy something on Amazon?", "No, I can't buy things."),
    ("Can you text someone?", "No, I can't send text messages."),
    ("Can you control my TV?", "No, I can't control the TV."),
    ("Can you book a doctor's appointment?", "No, I can't book appointments."),
    ("Can you see through cameras?", "No, I can't see through cameras."),
    ("Can you turn on the lights?", "No, I can't control the lights."),
    ("Can you lock the front door?", "No, I can't lock or unlock doors."),
])
async def test_capability_answers(said: str, expected: str) -> None:
    assert await _cap(said) == expected


@pytest.mark.asyncio
async def test_web_search_follows_the_internet_answer() -> None:
    # §5.1-5.3
    assert await _cap("can you search the web") == (
        'Yes. Ask me a question, then say "are you sure?" to have me check it online.')
    assert await _cap("can you search the web", R(BASE, internet="sometimes")) == (
        'Yes. Ask me a question, then say "are you sure?" to have me check it online.')
    assert await _cap("can you search the web", R(BASE, internet="sometimes", device=hf.WEB_CHAT)) == (
        'Yes. Type "look up" and what you want to find. I\'ll ask before each lookup leaves the house.')
    assert await _cap("can you search the web", R(BASE, internet="never")) == (
        f"No. This house is set to never use the internet. {INET_CHANGE}")
    assert await _cap("can you search the web", R(BASE, device=hf.WEB_CHAT)) == (
        'Yes. Type "look up" and what you want to find. I\'ll ask before each lookup leaves the house.')
    # §5.4-5.6
    assert await _cap("can you check the news") == (
        'Yes. Say "what\'s the news". I ask before reading a whole article online.')
    assert await _cap("can you check the news", R(BASE, internet="never")) == (
        f"No. This house is set to never use the internet. {INET_CHANGE}")
    assert await _cap("can you check the news", R(BASE, internet="sometimes")) == (
        'Yes. Say "what\'s the news": I read the headlines from the last time the connection was '
        "up. I ask before reading a whole article online.")
    assert await _cap("can you check the news", R(BASE, lookups_ask_first=False)) == (
        'Yes. Say "what\'s the news".')
    assert await _cap("can you check the news", R(BASE, news_on=False)) == (
        "No, the news briefing is off on this house. An admin can turn on the news briefing in "
        "Settings → System → Advanced.")


@pytest.mark.asyncio
async def test_ai_off_answers() -> None:
    assert await _cap("can you look at a picture", R(BASE, ai_on=False)) == (
        "Not right now: reading pictures needs AI features, and they're off on this house. An admin "
        "can turn on AI features in Settings → Chat & AI → Models.")


def test_an_unknown_x_never_gets_a_no() -> None:
    for said in ("can you juggle", "can you tell me a joke", "can you translate this",
                 "can you help me move", "do you like jazz"):
        assert hf.capability_of(hf.normalize(said)) is None
        assert hf.question_id(hf.normalize(said)) is None


# ─── Facts read from real state ───────────────────────────────────────────

class _BrokenScope:
    def __call__(self):
        return self

    async def __aenter__(self):
        raise RuntimeError("the database is away")

    async def __aexit__(self, *a):
        return False


def _raising(name: str):
    raise RuntimeError("unreadable")


@pytest.mark.asyncio
async def test_a_failed_read_is_unknown_never_a_guess() -> None:
    f = await hf.read(room_id="kitchen", get_setting=_raising, scope=_BrokenScope())
    assert f.recordings is None
    assert f.voice_keep is None and f.friend_houses is None and f.music_suggestions is None
    assert f.calendar_links is None and f.news_on is None
    assert hf.answer("recording", f) == "I can't check that right now."
    assert hf.answer("keep", f) == "I can't check that right now."
    assert hf.answer("friend_houses", f) == "I can't check that right now."
    assert hf.answer("listening_history", f) in ("I can't check that right now.",
                                                 hf.answer("listening_history", R(f, music_suggestions=False)))
    if f.internet != "never":
        assert f.background is None


def test_snapshot_settings_round_trip(monkeypatch) -> None:
    snap = {"house_facts": hf.settings_snapshot()}
    assert set(snap["house_facts"]) == set(hf.SETTING_NAMES)
    get = hf.snapshot_setting(snap)
    assert get("voice_history_keep") == snap["house_facts"]["voice_history_keep"]
    with pytest.raises(KeyError):
        hf.snapshot_setting({})("voice_history_keep")


@requires_db
@pytest.mark.asyncio
async def test_recordings_read_like_the_capture_code(db_session) -> None:
    from domovoi.config import settings

    await db_session.execute(text("DELETE FROM command_capture_rooms"))
    await db_session.commit()
    f = await hf.read(room_id="kitchen")
    assert f.device == hf.SATELLITE and f.recordings is False
    # Opted in, but no admin: nothing is kept (--reset-admin pauses it).
    await db_session.execute(text("INSERT INTO command_capture_rooms (room_id) VALUES ('kitchen')"))
    await db_session.commit()
    assert (await hf.read(room_id="kitchen")).recordings is False
    await db_session.execute(text("INSERT INTO admin_auth (id, password_hash) VALUES (1, 'x')"))
    await db_session.commit()
    f = await hf.read(room_id="kitchen")
    assert f.recordings is True
    assert f.recording_days == min(14, settings.command_capture_retention_days)
    assert (await hf.read(room_id="garage")).recordings is False
    await db_session.execute(text("DELETE FROM command_capture_rooms"))
    await db_session.commit()


@requires_db
@pytest.mark.asyncio
async def test_device_kinds(db_session) -> None:
    await db_session.execute(text(
        "INSERT INTO satellites (room_id, board) VALUES ('den', 'android_room'), ('hall', 'raspberry_pi_4')"))
    await db_session.commit()
    assert (await hf.read(room_id="den")).device == hf.ROOM_PHONE
    assert (await hf.read(room_id="hall")).device == hf.SATELLITE
    assert (await hf.read(room_id="phone-abc", device_kind="phone")).device == hf.PHONE_CHAT
    assert (await hf.read(chat=True)).device == hf.WEB_CHAT
    assert (await hf.read()).device == hf.OTHER


@requires_db
@pytest.mark.asyncio
async def test_settings_and_rows_are_read_live(db_session, monkeypatch) -> None:
    from domovoi import egress
    from domovoi.config import settings

    monkeypatch.setattr(settings, "voice_history_keep", "30")
    monkeypatch.setattr(settings, "friend_houses_enabled", False)
    monkeypatch.setattr(settings, "music_similar_artists_enabled", True)
    monkeypatch.setattr(settings, "news_enabled", True)
    for name in ("podcast_feed_poller_enabled", "music_alias_fetch_enabled", "library_enricher_enabled",
                 "lyrics_lrclib_enabled", "music_popularity_enabled", "library_cover_art_enabled"):
        monkeypatch.setattr(settings, name, False)
    with egress.override_policy("always"):
        f = await hf.read(room_id="kitchen")
    assert f.voice_keep == "30" and f.friend_houses is False and f.friends == 0
    assert f.music_suggestions is True
    assert f.background == ("the news headlines", "music suggestions for Up next")
    await db_session.execute(text(
        "INSERT INTO calendar_sources (kind, name, url, url_host) VALUES ('link', 'School', "
        "'https://calendar.example.org/a.ics', 'calendar.example.org')"))
    await db_session.commit()
    with egress.override_policy("always"):
        f = await hf.read(room_id="kitchen")
    assert f.calendar_links == 1
    assert f.background[-1] == "the calendars added by link"
    with egress.override_policy("never"):
        f = await hf.read(room_id="kitchen")
    assert f.background == () and f.music_suggestions is False


async def _plugin_row(db_session, slug: str, *, enabled: bool, status: str = "ok",
                      provides: tuple[str, ...] = ()) -> None:
    await db_session.execute(text(
        "INSERT INTO plugins (slug, name, version, domovoi_api, install_source, install_dir, manifest, "
        "enabled, status) VALUES (:s, :s, '1.0.0', '>=1.0', 'zip', '/tmp/x', CAST(:m AS JSONB), :e, :st)"
    ), {"s": slug, "m": json.dumps({"capabilities": {"provides": list(provides)}}), "e": enabled,
        "st": status})
    await db_session.commit()


@requires_db
@pytest.mark.asyncio
async def test_plugin_features_by_state(db_session) -> None:
    await db_session.execute(text("DELETE FROM plugins"))
    await db_session.commit()
    try:
        # absent
        assert await _cap("can you make a picture of a cat") == (
            "No, not on this house. An admin can add picture making from the Plugins page.")
        await _plugin_row(db_session, "imagegen", enabled=False)
        assert await _cap("can you make a picture of a cat") == (
            "No, picture making is switched off on this house. An admin can turn on picture making "
            "from the Plugins page.")
        await db_session.execute(text("UPDATE plugins SET enabled = true, status = 'error'"))
        await db_session.commit()
        assert await _cap("can you make a picture of a cat") == (
            "Not right now: picture making isn't working. An admin can check it on the Plugins page.")
        await db_session.execute(text("UPDATE plugins SET status = 'ok'"))
        await db_session.commit()
        assert await _cap("can you make a picture of a cat") == "Yes, picture making is on in this house."
        # Q6/N4: with AI off, picture making is off too.
        assert await _cap("can you make a picture of a cat", R(BASE, ai_on=False)) == (
            "Not right now: picture making needs AI features, and they're off on this house. An admin "
            "can turn on AI features in Settings → Chat & AI → Models.")
        # A feature found by what the plugin provides, never by its name.
        await _plugin_row(db_session, "media-x", enabled=True, provides=("media-acquisition-fulfiller",))
        assert await _cap("can you download videos") == "Yes, video downloads are on in this house."
        # §4.25: never "yes" while the house stays off the internet.
        assert await _cap("can you download videos", R(BASE, internet="never")) == (
            f"Not right now: this house is set to never use the internet. {INET_CHANGE}")
    finally:
        await db_session.execute(text("DELETE FROM plugins"))
        await db_session.commit()


@requires_db
@pytest.mark.asyncio
async def test_offline_library_offers_the_web_in_every_no(db_session) -> None:
    """§4.21-4.24: missing or switched off, the web is offered instead the
    same way (while the internet answer and AI allow it)."""
    web = " I can look things up on the web instead."
    await db_session.execute(text("DELETE FROM plugins"))
    await db_session.commit()
    try:
        assert await _cap("can you look things up offline") == (
            "No, not on this house. An admin can add an offline library from the Plugins page." + web)
        await _plugin_row(db_session, "kiwix", enabled=False)
        assert await _cap("can you look things up offline") == (
            "No, an offline library is switched off on this house. An admin can turn on an offline "
            "library from the Plugins page." + web)
        assert await _cap("can you look things up offline", R(BASE, internet="never")) == (
            "No, an offline library is switched off on this house. An admin can turn on an offline "
            "library from the Plugins page.")
        assert (await _cap("can you look things up offline", R(BASE, ai_on=False))).endswith(
            "from the Plugins page.")
        # On, but nothing to answer from: most often no library downloaded yet.
        await db_session.execute(text("UPDATE plugins SET enabled = true, status = 'load_error'"))
        await db_session.commit()
        assert await _cap("can you look things up offline") == (
            "Not right now: no offline library is set up yet, or it isn't working. An admin can "
            "download one on the Library page, or check it on the Plugins page." + web)
    finally:
        await db_session.execute(text("DELETE FROM plugins"))
        await db_session.commit()


@requires_db
@pytest.mark.asyncio
async def test_print_depends_on_a_printer(db_session) -> None:
    assert await _cap("can you print") == (
        "Not yet: no printer is set up. An admin can add one in Settings → Household → Printers & scanners.")


# ─── The router: no model, and the backstop ───────────────────────────────

class _NoModel:
    async def route(self, *a, **kw):  # pragma: no cover - must not run
        raise AssertionError("the tool model was asked")

    async def qa_with_uncertainty(self, *a, **kw):  # pragma: no cover
        raise AssertionError("the answer model was asked")


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(("said", "starts"), [
    ("Are you recording me?", "No. This room keeps no recordings"),
    ("Can you order pizza?", "No, I can't order food."),
    ("Did you use the internet for that?", "I can't tell from a voice turn yet."),
])
async def test_route_answers_with_no_model(db_session, monkeypatch, said: str, starts: str) -> None:
    from domovoi import router

    monkeypatch.setattr(router, "get_ollama_client", lambda: _NoModel())
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    r = await router.route(Intent(transcript=said, room_id="kitchen"), ctx, db_session)
    assert (r.matched_handler, r.matched_path) == ("house_facts", "fast")
    assert r.text.startswith(starts)


@requires_db
@pytest.mark.asyncio
async def test_a_privacy_worded_question_gets_the_facts_and_an_ordinary_one_does_not(
    db_session, monkeypatch,
) -> None:
    from domovoi import router
    from domovoi.clients.ollama import QAWithUncertainty

    seen: list[str | None] = []

    class _QA:
        async def route(self, *a, **kw):
            return None

        async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
            seen.append(profile_prefix)
            return QAWithUncertainty(answer="Fine.", needs_verification=False)

    monkeypatch.setattr(router, "get_ollama_client", lambda: _QA())
    for said in ("Is it creepy that you are always listening?", "Why is the sky blue?"):
        ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
        await router.route(Intent(transcript=said, room_id="kitchen"), ctx, db_session)
    assert seen[0] is not None and "Answer only from these facts." in seen[0]
    assert not seen[1]


def test_wants_facts_is_narrow() -> None:
    assert hf.wants_facts("is it creepy that you are always listening")
    assert hf.wants_facts("do you spy on us")
    assert hf.wants_facts("does your server send my data to the cloud")
    for said in ("how does the internet work", "do you like listening to jazz",
                 "what's the capital of france", "how do i record a song"):
        assert not hf.wants_facts(said), said


# ─── Web chat: the same matcher first, no model ───────────────────────────

def _client():
    from fastapi.testclient import TestClient

    from web.backend.main import app

    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


def _events(body: str) -> list[tuple[str, Any]]:
    out = []
    for frame in body.split("\n\n"):
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            out.append((ev, json.loads(data)))
    return out


@pytest.fixture
def no_chat_model(monkeypatch):
    import web.backend.api.chat as chat_api

    async def _stream(*a, **kw):  # pragma: no cover - must not run
        raise AssertionError("the chat model was asked")
        yield ""

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    from web.backend import domovoi_client

    before = domovoi_client.get_cached_snapshot()
    domovoi_client.set_cached_snapshot({"house_facts": hf.settings_snapshot()})
    yield chat_api
    domovoi_client.set_cached_snapshot(before)


def _send(c, thread_id: int, content: str) -> str:
    r = c.post(f"/api/chat/threads/{thread_id}/messages", json={"content": content})
    assert r.status_code == 200, r.text
    deltas = [d["text"] for ev, d in _events(r.text) if ev == "delta"]
    return "".join(deltas)


@requires_db
def test_web_chat_answers_from_the_house(no_chat_model) -> None:
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()["id"]
        assert _send(c, t, "Are you recording me?") == (
            "No. This is a typed chat, so there's nothing to record. The chat itself is kept until "
            "someone deletes it.")
        assert _send(c, t, "Can you order pizza?") == "No, I can't order food."
        assert _send(c, t, "Who can see my chats?").startswith("Everyone in this house:")
        msgs = c.get(f"/api/chat/threads/{t}/messages").json()["messages"]
        assert msgs[-1]["role"] == "assistant" and (msgs[-1]["stats"] or {}).get("model_role") == "house_facts"


@requires_db
def test_web_chat_did_you_use_the_internet(no_chat_model) -> None:
    import asyncio

    from web.backend.db import session_scope

    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()["id"]
        assert _send(c, t, "Did you use the internet for that?") == hf.D3_CHAT_NONE

        async def _row(stats: dict[str, Any]) -> None:
            async with session_scope() as s:
                await s.execute(text(
                    "INSERT INTO chat_messages (thread_id, role, content, stats) "
                    "VALUES (:t, 'assistant', 'answer', CAST(:st AS JSONB))"), {"t": t, "st": json.dumps(stats)})
                await s.commit()

        asyncio.run(_row({"model_role": "lookup", "lookup": {"internet_used": True}}))
        assert _send(c, t, "did you use the internet for that") == hf.D3_CHAT_YES
        asyncio.run(_row({"model_role": "chat"}))
        assert _send(c, t, "Did that come from the internet?") == hf.D3_CHAT_NO
        asyncio.run(_row({"model_role": "lookup", "lookup": {"internet_used": False}}))
        assert _send(c, t, "did you use the internet for that") == hf.D3_CHAT_NO


def test_chat_internet_answer_from_the_row() -> None:
    assert hf.chat_internet_answer(None) == hf.D3_CHAT_NONE
    assert hf.chat_internet_answer({"stats": {"lookup": {"internet_used": True}}}) == hf.D3_CHAT_YES
    assert hf.chat_internet_answer({"stats": {"model_role": "voice"}}) == hf.D3_CHAT_SPOKEN
    assert hf.chat_internet_answer({"stats": {}}) == hf.D3_CHAT_NO
