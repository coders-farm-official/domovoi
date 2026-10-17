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
    await repo.create(name="Aria", engine="piper", model_ref="aria-v1")
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
async def test_every_voice_switches_offline_too(seeded):
    # Every voice is local (the Edge cloud voices were dropped): offline is
    # no reason to refuse a switch or a sample.
    r = await VoiceHandler()._switch_response("aria", _ctx(online=False), seeded)
    assert r.pi_action == "set_voice" and r.voice_override == "Aria"
    s = await VoiceHandler()._sample_response("aria", _ctx(online=False), seeded)
    assert s.voice_override == "Aria"


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



# ─── A voice Domovoi offers that isn't downloaded yet (2026-10-17) ─────────
# "let me hear Linda's voice" answered "I don't have a voice called linda"
# while Settings -> Voices offered her (manual-content finding 4.9).


@pytest.fixture
async def two_voices(db_session):
    """A server that downloaded only Kristin and Norman (the internet
    question answered: the other shipped voice isn't fetched)."""
    await db_session.execute(text("DELETE FROM voices"))
    repo = VoicesRepository(db_session)
    await repo.create(name="Kristin", engine="piper", model_ref="en_US-kristin-medium",
                      is_default=True)
    await repo.create(name="Norman", engine="piper", model_ref="en_US-norman-medium")
    await db_session.commit()
    yield db_session
    # Committed (the download registers through its own session), so empty
    # the registry again: it is reference data, not in the truncate list.
    await db_session.rollback()
    await db_session.execute(text("DELETE FROM voices"))
    await db_session.commit()


def _session_ctx(**kw):
    from uuid import uuid4

    kw.setdefault("online", True)
    return Context(room_id="kitchen", session_id=uuid4(), **kw)


async def _pending(db_session, ctx) -> dict | None:
    from domovoi.db.repositories import SessionRepository

    data = await SessionRepository(db_session).get_context(ctx.session_id) or {}
    return data.get("pending_confirmation")


async def _with_session(db_session, **kw) -> Context:
    """A context whose session row exists (a parked question lives there)."""
    from domovoi.db.repositories import SessionRepository

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    kw.setdefault("online", True)
    return Context(room_id="kitchen", session_id=sid, **kw)


@requires_db
@pytest.mark.asyncio
async def test_a_voice_not_downloaded_is_offered_never_denied(two_voices, monkeypatch) -> None:
    from domovoi import egress

    ctx = await _with_session(two_voices)
    with egress.override_policy("always"):
        r = await VoiceHandler()._sample_response("linda", ctx, two_voices)
    assert "don't have a voice called" not in r.text
    assert r.text.startswith("Linda isn't downloaded on this server yet.")
    assert "about 64 megabytes" in r.text and r.text.endswith("Want me to download her?")
    assert r.expect_followup and r.voice_override is None
    pending = await _pending(two_voices, ctx)
    assert pending["kind"] == "core.voice_download"
    assert pending["model_ref"] == "en_US-ljspeech-medium"


@requires_db
@pytest.mark.asyncio
async def test_yes_downloads_and_registers_her(two_voices, monkeypatch) -> None:
    import asyncio

    from domovoi import egress
    from domovoi.clients import tts
    from domovoi.handlers import voice as voice_mod

    fetched: list[str] = []

    async def fake_ensure(model_ref: str) -> None:
        fetched.append(model_ref)

    monkeypatch.setattr(tts, "ensure_piper_voice", fake_ensure)
    monkeypatch.setattr(voice_mod, "piper_voice_on_disk", lambda ref: False)
    ctx = _session_ctx()
    with egress.override_policy("always"):
        r = await VoiceHandler().handle_confirmation(
            "core.voice_download",
            {"model_ref": "en_US-ljspeech-medium", "name": "Linda", "then": "sample"},
            True, ctx, two_voices,
        )
        assert r.text == (
            'Okay, downloading Linda. It takes a minute or two. '
            'Then say "let me hear Linda\'s voice".'
        )
        await asyncio.gather(*voice_mod._DOWNLOADS.values())
    assert fetched == ["en_US-ljspeech-medium"]
    rows = await VoicesRepository(two_voices).all()
    assert {r["name"] for r in rows} == {"Kristin", "Norman", "Linda"}
    voice_mod._DOWNLOADS.clear()


@requires_db
@pytest.mark.asyncio
async def test_no_downloads_nothing(two_voices) -> None:
    r = await VoiceHandler().handle_confirmation(
        "core.voice_download", {"model_ref": "en_US-ljspeech-medium"}, False,
        _session_ctx(), two_voices,
    )
    assert r.text == "Okay."


@requires_db
@pytest.mark.asyncio
async def test_internet_turned_off_says_where_to_get_her(two_voices) -> None:
    from domovoi import egress

    with egress.override_policy("never"):
        r = await VoiceHandler()._switch_response("linda", _session_ctx(), two_voices)
    assert r.text.startswith("Linda isn't downloaded on this server yet.")
    assert "stay off the internet" in r.text and "Settings, Voices" in r.text
    assert r.pi_action is None and not r.expect_followup


@requires_db
@pytest.mark.asyncio
async def test_offline_says_so(two_voices) -> None:
    from domovoi import egress

    with egress.override_policy("always"):
        r = await VoiceHandler()._sample_response("linda", _session_ctx(online=False), two_voices)
    assert "can't reach the internet right now" in r.text


@requires_db
@pytest.mark.asyncio
async def test_a_registered_voice_missing_from_disk_is_not_faked(two_voices, monkeypatch) -> None:
    """Norman is registered but his files aren't here and can't be
    fetched: the sample would have been the system voice saying "Here's how
    Norman sounds"."""
    from domovoi import egress
    from domovoi.handlers import voice as voice_mod

    monkeypatch.setattr(voice_mod, "piper_voice_on_disk", lambda ref: ref != "en_US-norman-medium")
    with egress.override_policy("never"):
        r = await VoiceHandler()._sample_response("norman", _session_ctx(), two_voices)
    assert r.voice_override is None
    assert r.text.startswith("Norman isn't downloaded on this server yet.")
    # on disk: the sample plays, as before
    with egress.override_policy("never"):
        r = await VoiceHandler()._sample_response("kristin", _session_ctx(), two_voices)
    assert r.voice_override == "Kristin"


@requires_db
@pytest.mark.asyncio
async def test_the_list_names_who_isnt_downloaded(two_voices) -> None:
    r = await VoiceHandler()._list_response(_session_ctx(), two_voices)
    assert r.text == "I've got these voices: Kristin and Norman. Linda isn't downloaded yet."


def test_download_phrases_route_to_voice_without_poaching() -> None:
    from domovoi.tests.conftest import fast_path_winner

    assert fast_path_winner("download Linda's voice") == "voice"
    assert fast_path_winner("let me hear Linda's voice") == "voice"
    assert fast_path_winner("get the linda voice") == "voice"
    assert fast_path_winner("download the latest episode of the daily") != "voice"


@requires_db
@pytest.mark.asyncio
async def test_a_voice_on_disk_but_not_registered_is_registered_and_played(
    two_voices, monkeypatch
) -> None:
    from domovoi.handlers import voice as voice_mod

    monkeypatch.setattr(voice_mod, "piper_voice_on_disk", lambda ref: True)
    r = await VoiceHandler()._sample_response("linda", _session_ctx(), two_voices)
    assert r.voice_override == "Linda"
    assert r.text == "Here's how Linda sounds."
    assert await VoicesRepository(two_voices).get_by_name("Linda") is not None
