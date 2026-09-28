"""Spoken Q&A answers are whole, honest and punctuated.

Pinned to the office satellite's conversation_log on the live server
(2026-09-23 .. 2026-09-28), where the QA fallthrough said:

  #188/#152  'Tell me a joke.'  -> 'What do you call a fake noodle?'   (no punchline)
  #162/#165  a remark           -> 'I don'                             (cut mid-word)
  #135       background speech  -> 'I don Want me to check that online?'
  #156       a question         -> 'no Want me to check that online?'
  #158       'German. Wow.'     -> 'none found Want me to check that online?'
  #157       'Yes, please.'     -> "... melting point of cocaine. NONE"   (sentinel)
  #139       'sir.'             -> "I don't have enough context to answer that Want me to check that online?"
  #136       long speech        -> ''                                   (empty)
  #187       'Back so soon.'    -> "My language model isn't answering right now ..." (the model was up)

All but #157 came from one contract: the spoken answer was the "answer"
field of a JSON object (``format='json'``), and llama3.2:3b closes that
string early — after the joke's "?", at a contraction's apostrophe, with a
placeholder, or empty. The router then glued the offer on with a bare
space, fired it on a self-doubt flag that was noise, and announced an empty
answer as a dead model. #157 is the answer-from-sources parser speaking the
NONE sentinel the model wrote on the ANSWER line.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text

from domovoi.clients.ollama import (
    VOICE_QA_SYSTEM_PROMPT,
    QAWithUncertainty,
    RealOllamaClient,
    _clean_spoken_answer,
)
from domovoi.config import settings
from domovoi.handlers.double_check import (
    _NO_CLEAR_ANSWER_TEXT,
    _parse_answer_from_sources,
)
from domovoi.models import Context, Intent
from domovoi.router import (
    _LLM_UNREACHABLE_TEXT,
    _NO_ANSWER_TEXT,
    _end_sentence,
    route,
)
from domovoi.speech_sanitize import sanitize_for_speech
from domovoi.streaming import _split_sentences
from domovoi.tests.conftest import requires_db
from domovoi.uncertainty import (
    answer_admits_staleness,
    answer_offers_lookup,
    looks_like_question,
)

JOKE = "What do you call a fake noodle? An impasta!"
OFFER = "Want me to check that online?"


class _Chat:
    """Stands in for ``ollama.AsyncClient.chat``. QA calls take the next
    scripted reply (the last one repeats); a routing call (``tools=``) gets
    a reply with no tool call, so the turn falls through to QA without
    using up a script entry. An exception in the script is raised."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        if kwargs.get("tools"):
            return {"message": {"content": ""}}
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return {"message": {"content": reply}}


def _client(chat: _Chat) -> RealOllamaClient:
    c = RealOllamaClient.__new__(RealOllamaClient)   # skip __init__'s ollama import
    c._client = type("C", (), {"chat": staticmethod(chat)})()
    c._qa_model = "llama3.2:3b"
    c._tool_model = "qwen3:8b"
    c._tool_think = False
    c._send_think = False
    c._keep_alive = "24h"
    c._send_keep_alive = False
    return c


# ─── The client: free text, retried when unusable ─────────────────────────


@pytest.mark.asyncio
async def test_the_spoken_answer_is_free_text_not_a_json_field() -> None:
    chat = _Chat(JOKE)
    qa = await _client(chat).qa_with_uncertainty("Tell me a joke.")
    assert qa.answer == JOKE                     # the punchline is spoken (#188, #152)
    assert qa.unreachable is False
    assert len(chat.calls) == 1
    call = chat.calls[0]
    assert "format" not in call                  # no JSON contract to close early
    assert call["messages"][0] == {
        "role": "system",
        "content": VOICE_QA_SYSTEM_PROMPT.format(bot=settings.bot_name),
    }
    assert call["messages"][-1] == {"role": "user", "content": "Tell me a joke."}


@pytest.mark.asyncio
async def test_profile_prefix_leads_the_voice_prompt() -> None:
    chat = _Chat("Some jazz, then.")
    await _client(chat).qa_with_uncertainty(
        "play me something", profile_prefix="User context: likes jazz."
    )
    system = chat.calls[0]["messages"][0]["content"]
    assert system.startswith("User context: likes jazz.\n\n")
    assert system.endswith(VOICE_QA_SYSTEM_PROMPT.format(bot=settings.bot_name))


def test_the_voice_prompt_keeps_answers_short_in_words_not_by_cutting() -> None:
    """Length is the prompt's job; nothing in the client trims a good
    answer. (Measured on llama3.2:3b: naming jokes in this prompt primed a
    long library joke and cost punchlines, so it doesn't.)"""
    prompt = VOICE_QA_SYSTEM_PROMPT.format(bot="Domovoi")
    assert "concise" in prompt and "one to three sentences" in prompt
    assert "json" not in prompt.lower() and "joke" not in prompt.lower()
    long_answer = " ".join(["This is a long but complete sentence."] * 12)
    assert _clean_spoken_answer(long_answer) == (long_answer, None)


@pytest.mark.parametrize(
    "answer",
    [
        JOKE,
        "What do you call a fake noodle?\nAn impasta!",
        "I don’t know what you mean.",          # curly apostrophe
        "I don't know what you mean.",
    ],
)
def test_a_complete_answer_survives_the_speech_path(answer: str) -> None:
    cleaned, problem = _clean_spoken_answer(answer)
    assert problem is None and cleaned == answer
    spoken = " ".join(sanitize_for_speech(s) for s in _split_sentences(cleaned))
    assert "impasta" in spoken or "know what you mean" in spoken


@pytest.mark.asyncio
async def test_an_empty_reply_is_retried_then_reported_as_no_answer() -> None:
    """#136 / #187: an empty reply from a model that is up comes back empty
    but NOT unreachable — after one retry."""
    chat = _Chat("", "")
    qa = await _client(chat).qa_with_uncertainty("Back so soon.")
    assert qa.answer == ""
    assert qa.unreachable is False
    assert len(chat.calls) == 2


@pytest.mark.asyncio
async def test_an_empty_reply_retry_can_recover() -> None:
    chat = _Chat("", "Just checking in. What can I do for you?")
    qa = await _client(chat).qa_with_uncertainty("Back so soon.")
    assert qa.answer == "Just checking in. What can I do for you?"


@pytest.mark.parametrize("placeholder", ["None", "none found", "N/A", "null", "Not applicable."])
@pytest.mark.asyncio
async def test_a_placeholder_is_never_spoken(placeholder: str) -> None:
    """#156 / #158: 'no'/'none found' in place of an answer."""
    qa = await _client(_Chat(placeholder, placeholder)).qa_with_uncertainty("German. Wow.")
    assert qa.answer == ""
    qa = await _client(_Chat(placeholder, "I can't help with that.")).qa_with_uncertainty(
        "what's the melting point of cocaine?"
    )
    assert qa.answer == "I can't help with that."


@pytest.mark.parametrize(
    "replies,expected",
    [
        (["I don", "I don't know who Chevy is."], "I don't know who Chevy is."),
        (["I didn", "I didn"], ""),
        (["Sure. Chevy is a car brand and doesn"] * 2, "Sure."),
        (["I couldn", 'He said "no."'], 'He said "no."'),
    ],
)
@pytest.mark.asyncio
async def test_an_answer_cut_at_a_contraction_is_never_spoken_cut(replies, expected) -> None:
    """#162 / #165 / #135 'I don', #130 'I couldn', #75 'I didn'."""
    qa = await _client(_Chat(*replies)).qa_with_uncertainty("I'm Adam.")
    assert qa.answer == expected


@pytest.mark.parametrize("answer", ["Yes, I can", "The Lakers won", "Ask Don"])
def test_words_that_merely_look_like_stems_are_not_cuts(answer: str) -> None:
    assert _clean_spoken_answer(answer) == (answer, None)


@pytest.mark.asyncio
async def test_ollama_failing_twice_is_unreachable() -> None:
    chat = _Chat(ConnectionError("connection refused"))
    qa = await _client(chat).qa_with_uncertainty("who wrote pride and prejudice")
    assert qa.unreachable is True and qa.answer == ""
    assert len(chat.calls) == 2


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried() -> None:
    chat = _Chat(ConnectionError("reset"), "Jane Austen.")
    qa = await _client(chat).qa_with_uncertainty("who wrote pride and prejudice")
    assert qa.answer == "Jane Austen." and qa.unreachable is False


@pytest.mark.asyncio
async def test_a_timeout_is_not_waited_out_twice() -> None:
    chat = _Chat(httpx.ReadTimeout("timed out"))
    qa = await _client(chat).qa_with_uncertainty("who wrote pride and prejudice")
    assert qa.unreachable is True
    assert len(chat.calls) == 1


@pytest.mark.asyncio
async def test_the_real_client_never_self_flags() -> None:
    """The JSON self-doubt flag it replaced was noise; the router judges
    the offer from the question and the answer's own words."""
    qa = await _client(_Chat("I don't have real-time info, but it was the iPhone 14.")).qa_with_uncertainty(
        "what's the newest iphone?"
    )
    assert qa.needs_verification is False


# ─── The offer's trigger ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "answer",
    [
        "I don't have real-time info, but the latest I know of is the iPhone 14.",
        "As of my last update, it was Canberra.",
        "That may be out of date by now.",
        "I don’t have current information on that.",
        "My knowledge cutoff is 2023, so I can't say.",
        # llama3.2:3b's own hedges on questions the categorizer lets through
        # (local probe, 2026-09-28).
        "I'm a bit out of date, to be honest. My knowledge stopped in 2023.",
        "I think it's 2023. I was last updated in December 2023, so I might "
        "not have the very latest information.",
        "That would be Mike McCarthy, but I'm not up to date on all the latest sports news.",
        "My information might not be current, but it was Parag Agrawal.",
    ],
)
def test_an_answer_that_admits_it_may_be_stale(answer: str) -> None:
    assert answer_admits_staleness(answer)


@pytest.mark.parametrize(
    "answer",
    [
        "I don't have enough context to answer that",   # #139
        "I'm not sure who Chevy is.",
        "What do you call a fake noodle? An impasta!",
        "I can't provide information on the melting point of cocaine.",
        "Elon Musk is the CEO of Twitter.",
        "I might not have heard you right.",
        "I'm up to date on my chores, thanks.",
        "The page was last updated in 2020.",
        "",
    ],
)
def test_ordinary_answers_do_not_admit_staleness(answer: str) -> None:
    assert not answer_admits_staleness(answer)


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("Mike McCarthy. I'm not up to date on sports, can I look that up for you?", True),
        ("I'm not sure. Want me to check that online?", True),
        ("I'm not sure. Should I search the web for it?", True),
        ('Should I look it up?"', True),
        ("I looked it up once. It's Canberra.", False),
        ("Can I look that up for you? It's probably Canberra.", False),
        ("Canberra.", False),
        ("", False),
    ],
)
def test_answer_offers_lookup(answer: str, expected: bool) -> None:
    assert answer_offers_lookup(answer) is expected


@pytest.mark.parametrize(
    "said,expected",
    [
        ("What's the capital of Australia?", True),
        ("how many tracks are in my library", True),
        ("Is it raining", True),
        ("sir.", False),                      # #139
        ("German. Wow.", False),              # #158
        ("Tell me a joke.", False),
        ("Back so soon.", False),             # #187
        ("", False),
    ],
)
def test_looks_like_question(said: str, expected: bool) -> None:
    assert looks_like_question(said) is expected


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("I don't have enough context to answer that", "I don't have enough context to answer that."),
        ("That works", "That works."),
        ("Canberra.", "Canberra."),
        ("Really?", "Really?"),
        ('He said "fine."', 'He said "fine."'),
        ("Wait for it…", "Wait for it…"),
        ("trailing space  ", "trailing space."),
    ],
)
def test_end_sentence(answer: str, expected: str) -> None:
    assert _end_sentence(answer) == expected


# ─── Answer-from-sources: the NONE sentinel is never spoken (#157) ───────


@pytest.mark.parametrize(
    "raw,answer,source",
    [
        (   # #157 verbatim: SOURCE label dropped, sentinel left on the ANSWER line
            "ANSWER: The search didn't turn up a clear answer on the melting "
            "point of cocaine. NONE",
            "The search didn't turn up a clear answer on the melting point of cocaine.",
            None,
        ),
        (
            "ANSWER: The Lakers won 110 to 102. SOURCE: https://espn.com/game/1",
            "The Lakers won 110 to 102.",
            "https://espn.com/game/1",
        ),
        (
            "ANSWER: The Lakers won 110 to 102. SOURCE: NONE",
            "The Lakers won 110 to 102.",
            None,
        ),
        ("ANSWER: NONE\nSOURCE: NONE", _NO_CLEAR_ANSWER_TEXT, None),
        (
            "ANSWER: It opened in 1937.\nIt was the longest span then.\nSOURCE: https://x.test/a",
            "It opened in 1937. It was the longest span then.",
            "https://x.test/a",
        ),
        ("ANSWER: Venus has none.\nSOURCE: NONE", "Venus has none.", None),
        ("Just a sentence with no labels. NONE", "Just a sentence with no labels.", None),
        ("NONE", _NO_CLEAR_ANSWER_TEXT, None),
    ],
)
def test_answer_from_sources_never_speaks_the_sentinel(raw, answer, source) -> None:
    assert _parse_answer_from_sources(raw) == (answer, source)


# ─── Through the router, persisted: what the office will hear now ────────


async def _route_real(db_session, said: str, chat: _Chat):
    from unittest.mock import patch

    client = _client(chat)
    with patch("domovoi.router.get_ollama_client", lambda: client):
        resp = await route(
            Intent(transcript=said, room_id="office"),
            Context(room_id="office", online=True),
            db_session,
        )
        await db_session.commit()
    row = (
        await db_session.execute(
            text(
                "SELECT assistant_text, matched_path FROM conversation_log "
                "WHERE session_id = :s ORDER BY id DESC LIMIT 1"
            ),
            {"s": str(resp.session_id)},
        )
    ).one()
    return resp, row


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "said,replies,expected",
    [
        # #188 / #152 — the whole joke, punchline included.
        ("Tell me a joke.", [JOKE], JOKE),
        # #162 / #165 / #135 — a cut reply is re-asked, never spoken cut.
        ("I'm Adam, I'm much more attractive than Chevy.",
         ["I don", "I don't know who Chevy is, but confidence suits you."],
         "I don't know who Chevy is, but confidence suits you."),
        # #156 — a placeholder is re-asked; the refusal is spoken, no offer.
        ("Hey Jarvis, what's the melting point of cocaine?",
         ["None", "I can't provide information on that."],
         "I can't provide information on that."),
        # #158 — placeholder twice: an honest line, no offer.
        ("German. Wow.", ["none found"], _NO_ANSWER_TEXT),
        # #139 — a remark gets its answer, and no offer rides on it.
        ("sir.", ["I don't have enough context to answer that"],
         "I don't have enough context to answer that"),
        # #136 / #187 — empty twice from a model that is up: not an outage.
        ("Back so soon.", [""], _NO_ANSWER_TEXT),
    ],
)
async def test_live_rows_are_now_spoken_whole(db_session, said, replies, expected) -> None:
    resp, row = await _route_real(db_session, said, _Chat(*replies))
    assert resp.text == expected
    assert row.assistant_text == expected
    assert row.matched_path == "qa"
    assert OFFER not in resp.text
    assert resp.expect_followup is False


@requires_db
@pytest.mark.asyncio
async def test_a_dead_model_still_gets_the_outage_line(db_session) -> None:
    resp, row = await _route_real(
        db_session, "who wrote pride and prejudice", _Chat(ConnectionError("refused"))
    )
    assert resp.text == _LLM_UNREACHABLE_TEXT
    assert row.matched_path == "error"


@requires_db
@pytest.mark.asyncio
async def test_a_stale_admitting_answer_to_a_question_gets_the_offer_as_its_own_sentence(
    db_session,
) -> None:
    from domovoi.db.repositories import SessionRepository

    answer = "I don't have real-time info, but the Pixel and the iPhone both have great cameras"
    resp, row = await _route_real(
        db_session, "Which phone has the best camera?", _Chat(answer)
    )
    assert resp.text == answer + ". " + OFFER
    assert row.assistant_text == resp.text
    assert resp.expect_followup is True
    pending = (await SessionRepository(db_session).get_context(resp.session_id))[
        "pending_confirmation"
    ]
    assert pending["kind"] == "core.self_doubt_offer"
    # An answer was spoken, so a "no" may say "sticking with my answer".
    assert pending["candidate_claim"] == answer


class _Flagging:
    """A QA client that self-flags every answer (what the old JSON flag did
    on junk turns)."""

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def route(self, transcript, tool_schemas):
        return None

    async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
        return QAWithUncertainty(answer=self.answer, needs_verification=True)


async def _route_flagging(db_session, said: str, answer: str):
    from unittest.mock import patch

    with patch("domovoi.router.get_ollama_client", lambda: _Flagging(answer)):
        resp = await route(
            Intent(transcript=said, room_id="office"),
            Context(room_id="office", online=True),
            db_session,
        )
        await db_session.commit()
    return resp


@requires_db
@pytest.mark.asyncio
async def test_a_flag_on_a_remark_never_offers(db_session) -> None:
    """#139 / #158 / #135: background speech and remarks got the offer
    because the flag fired on them. A remark is not a question."""
    resp = await _route_flagging(db_session, "sir.", "I don't have enough context to answer that")
    assert resp.text == "I don't have enough context to answer that"
    assert resp.expect_followup is False


@requires_db
@pytest.mark.asyncio
async def test_a_flag_on_a_question_offers_as_its_own_sentence(db_session) -> None:
    """#139's glue: never 'that Want me to check'."""
    resp = await _route_flagging(
        db_session, "what does that mean?", "I don't have enough context to answer that"
    )
    assert resp.text == "I don't have enough context to answer that. " + OFFER


@requires_db
@pytest.mark.asyncio
async def test_the_models_own_lookup_offer_is_parked_not_asked_twice(db_session) -> None:
    """The model sometimes ends by offering to look it up itself. A "yes"
    to that must reach the web search (so the offer is parked), and the
    room must not hear the question twice."""
    from domovoi.db.repositories import SessionRepository

    answer = (
        "That would be Mike McCarthy, but I'm not up to date on all the latest "
        "sports news, can I look that up for you?"
    )
    resp, row = await _route_real(
        db_session, "Who is the head coach of the Cowboys?", _Chat(answer)
    )
    assert resp.text == answer
    assert OFFER not in resp.text
    assert resp.expect_followup is True
    pending = (await SessionRepository(db_session).get_context(resp.session_id))[
        "pending_confirmation"
    ]
    assert pending["kind"] == "core.self_doubt_offer"
    assert pending["question"] == "Who is the head coach of the Cowboys?"
