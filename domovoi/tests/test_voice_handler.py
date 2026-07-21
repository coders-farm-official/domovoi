"""VoiceHandler — fast-path regexes, name cleaning, and the list/sample/
switch responses (the last backed by the voices registry).

Pure regex/cleaning tests run without Postgres; the response tests seed
the ``voices`` table (cleared first — it's reference data, not in the
per-test truncate list).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.db.repositories import VoicesRepository
from domovoi.handlers.voice import (
    VoiceHandler,
    _LIST_RE,
    _CURRENT_RE,
    _SAMPLE_RE,
    _SWITCH_RE,
    _clean_name,
)
from domovoi.models import Context
from domovoi.tests.conftest import requires_db


# ─── Regexes ────────────────────────────────────────────────────────────────

def test_list_regex():
    for s in ("what voices are there", "what voices are available",
              "which voices do you have", "list voices", "list the voices",
              "what voices can you use"):
        assert _LIST_RE.match(s), s


def test_current_regex():
    for s in ("what voice are you using", "which voice are you speaking in",
              "what's your voice", "what is your current voice"):
        assert _CURRENT_RE.match(s), s


def test_sample_regex_captures_name():
    cases = {
        "let me hear ryan's voice": "ryan",
        "hear aria's voice": "aria",
        "what does ryan sound like": "ryan",
        "how does aria sound": "aria",
        "sample lessac": "lessac",
        "let me hear what ryan sounds like": "ryan",
    }
    for phrase, expected in cases.items():
        m = _SAMPLE_RE.match(phrase)
        assert m, phrase
        assert _clean_name(next(g for g in m.groups() if g)) == expected


def test_switch_regex_captures_name():
    cases = {
        "switch to ryan": "ryan",
        "switch your voice to aria": "aria",
        "change voice to lessac": "lessac",
        "set your voice to ryan": "ryan",
        "use the aria voice": "aria",
        "talk like ryan": "ryan",
        "sound like aria": "aria",
    }
    for phrase, expected in cases.items():
        m = _SWITCH_RE.match(phrase)
        assert m, phrase
        assert _clean_name(next(g for g in m.groups() if g)) == expected


def test_sample_regex_does_not_poach_plain_hear():
    # "let me hear the news" has no "voice"/"sounds like" → not a sample.
    assert _SAMPLE_RE.match("let me hear the news") is None


def test_clean_name_strips_filler_and_voice_suffix():
    assert _clean_name("the aria voice") == "aria"
    assert _clean_name("ryan's voice") == "ryan"
    assert _clean_name("  Lessac.  ") == "Lessac"


# ─── Response tests (registry-backed) ───────────────────────────────────────

@pytest.fixture
async def seeded(db_session):
    await db_session.execute(text("DELETE FROM voices"))
    repo = VoicesRepository(db_session)
    await repo.create(name="Lessac", engine="piper", model_ref="en_US-lessac-medium", is_default=True)
    await repo.create(name="Aria", engine="edge", model_ref="en-US-AriaNeural")
    await repo.create(name="Ryan", engine="piper", model_ref="ryan-v1")
    return db_session


def _ctx(**kw):
    kw.setdefault("online", True)
    return Context(room_id="kitchen", **kw)


@requires_db
@pytest.mark.asyncio
async def test_list_response_speaks_names(seeded):
    r = await VoiceHandler().execute(None, _ctx(), seeded)
    assert "Lessac" in r.text and "Aria" in r.text and "Ryan" in r.text


@requires_db
@pytest.mark.asyncio
async def test_current_uses_ctx_voice_then_default(seeded):
    h = VoiceHandler()
    # ctx.voice set → that voice.
    r = await h._current_response(_ctx(voice="Ryan"), seeded)
    assert "Ryan" in r.text
    # No ctx.voice → registry default (Lessac).
    r2 = await h._current_response(_ctx(), seeded)
    assert "Lessac" in r2.text


@requires_db
@pytest.mark.asyncio
async def test_sample_sets_voice_override(seeded):
    r = await VoiceHandler()._sample_response("ryan", _ctx(), seeded)
    assert r.voice_override == "Ryan"
    assert r.pi_action is None  # sampling doesn't switch


@requires_db
@pytest.mark.asyncio
async def test_switch_sets_override_and_pi_action(seeded):
    r = await VoiceHandler()._switch_response("aria", _ctx(), seeded)
    assert r.voice_override == "Aria"
    assert r.pi_action == "set_voice"
    assert r.pi_action_arg == "Aria"


@requires_db
@pytest.mark.asyncio
async def test_switch_edge_voice_offline_degrades(seeded):
    # Aria is an Edge (cloud) voice; offline → no switch, graceful message.
    r = await VoiceHandler()._switch_response("aria", _ctx(online=False), seeded)
    assert r.pi_action is None
    assert r.voice_override is None
    assert "offline" in r.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_fuzzy_matches_stt_homophone(seeded):
    # Whisper hears "Aria" as "area" — the fuzzy fallback should still
    # resolve it to the Aria voice rather than reporting it unknown.
    r = await VoiceHandler()._sample_response("area", _ctx(), seeded)
    assert r.voice_override == "Aria"


@requires_db
@pytest.mark.asyncio
async def test_unknown_voice_lists_alternatives(seeded):
    r = await VoiceHandler()._switch_response("bogus", _ctx(), seeded)
    assert "don't have a voice called bogus" in r.text
    assert "Lessac" in r.text  # suggests what it does have
    assert r.pi_action is None
