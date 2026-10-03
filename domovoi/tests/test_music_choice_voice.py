"""The "did you mean …?" dialog as a satellite hears it.

Real StreamSessions on a recording socket (the test_music_under_capture
harness), the REAL router on the lane's database (sessions, the parked
question, intents_log), and the resolver faked (music_choice_testkit).
Whisper hears what each test scripts; TTS is silence.

The room was playing music when the person woke it (its resumable URL is
``URL``). Pinned:

* the question goes out with ``expect_followup`` and NO music_start: the
  old song's restart is held through the follow-up window
  (``hold_music_start``) — asking never touches the music;
* "yes" in the follow-up capture plays the candidate: its own music_start,
  once (the held restart is superseded);
* "no" gets the old music back at once; so does a question nobody answers,
  once the follow-up capture times out;
* "no, play X" plays X;
* a whole "yes" may end its follow-up capture early (tier B); "no, play X"
  may not.
"""

from __future__ import annotations

import pytest

from domovoi import streaming
from domovoi.db.repositories import SessionRepository
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db
from domovoi.tests.music_choice_testkit import NEW_URL, clear_choice_parked, install
from domovoi.tests.test_music_under_capture import ROOM, URL, Room, _settle, _until, _wav

pytestmark = requires_db

QUESTION = "Did you mean Glass Harbor, or the album Moonlit Static by Glass Harbor?"


class VoiceRoom(Room):
    """A room whose turns are heard as ``said`` and routed for real."""

    def __init__(self) -> None:
        super().__init__()
        self.said = ""

    async def say(self, said: str, *, trigger: str = "wake_word", utt: int = 1) -> dict:
        """One whole capture; returns its response_end frame."""
        self.said = said
        before = len(self.texts())
        await self.turn(trigger=trigger, utt=utt)
        ends = [f for f in self.texts()[before:] if f["type"] == "response_end"]
        assert len(ends) == 1, self.texts()[before:]
        return ends[0]

    async def pending(self):
        async with session_scope() as s:
            ctx = await SessionRepository(s).get_context(self.sess.session_id) or {}
        return ctx.get("pending_confirmation")


@pytest.fixture
async def voice(monkeypatch, db_session):
    """db_session only for its TRUNCATE; the turns open their own sessions
    (streaming.session_scope), as on a satellite."""
    clear_choice_parked()
    fake = install(monkeypatch)
    r = VoiceRoom()

    class _Whisper:
        async def transcribe(self, pcm):
            return r.said

        async def transcribe_wav_bytes(self, wav):
            return r.said

    class _TTS:
        async def synthesize(self, text, engine=None, voice=None):
            return _wav(r.tts_seconds)

    async def _identify(pcm, embedding=None):
        return None

    async def _any_voice(name):
        return (None, None)

    async def _stream_check(room_id, stream_url=None, *, timeout=None):
        return True

    async def _resume(room_id):
        r.mpd_resumed.append(room_id)

    async def _hooks(self, s, *, response, **kw):
        return None

    from domovoi.clients import mpd as mpd_module

    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper())
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    monkeypatch.setattr(streaming, "resolve_voice", _any_voice)
    monkeypatch.setattr(streaming, "_resume_mpd_for_room", _resume)
    monkeypatch.setattr(streaming.StreamSession, "_voice_profile_hooks", _hooks)
    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    monkeypatch.setattr(mpd_module, "ensure_stream_serving", _stream_check)
    monkeypatch.setattr(streaming, "MUSIC_HOLD_POLL_SEC", 0.01, raising=False)
    r.fake = fake  # type: ignore[attr-defined]
    yield r
    for entry in r.app.state.pending_music_start.values():
        entry["task"].cancel()
    for t in [*streaming._ANNOUNCE_MUSIC_RESTARTS, *getattr(streaming, "_MUSIC_HOLDS", ())]:
        t.cancel()
    clear_choice_parked()


async def _asked(voice: VoiceRoom) -> None:
    end = await voice.say("Play glass harber.")
    assert end["expect_followup"] is True
    await _settle(0.05)
    # Asking never touches the music: no start, no stop, the old song held.
    assert voice.starts() == []
    assert "music_stop" not in voice.kinds()
    assert voice.sess.music_block() == "followup"
    assert voice.app.state.resumable_music[ROOM] == URL
    pending = await voice.pending()
    assert pending["kind"] == "core.music_choice"


async def test_yes_in_the_followup_plays_the_candidate_once(voice) -> None:
    await _asked(voice)
    await voice.start_capture("followup", 2)
    voice.said = "Yes."
    await voice.frames(20)
    await voice.end_capture(2)
    await voice.sess._response_task
    await _settle(0.05)
    assert voice.fake.played_labels == ["Glass Harbor"]
    assert voice.starts() == [{"type": "music_start", "stream_url": NEW_URL}]
    assert voice.app.state.resumable_music[ROOM] == NEW_URL
    assert voice.sess._music_hold_task is None
    assert await voice.pending() is None


async def test_no_gets_the_old_music_back(voice) -> None:
    await _asked(voice)
    end = await voice.say("No.", trigger="followup", utt=2)
    assert end["expect_followup"] is False
    await _settle(0.05)
    assert voice.fake.played == []
    assert voice.starts() == [{"type": "music_start", "stream_url": URL}]
    assert len(voice.starts()) == 1


async def test_no_play_something_else_plays_that(voice) -> None:
    await _asked(voice)
    await voice.say("No, play the Velvet Kites.", trigger="followup", utt=2)
    await _settle(0.05)
    assert voice.fake.played_labels == ["The Velvet Kites"]
    assert voice.starts() == [{"type": "music_start", "stream_url": NEW_URL}]


async def test_an_unanswered_question_gets_the_old_music_back(voice) -> None:
    await _asked(voice)
    await voice.start_capture("followup", 2)
    await voice.frames(20)
    await _settle(0.05)
    assert voice.starts() == []
    voice.capture_goes_quiet()                      # nobody answered
    await _until(lambda: voice.starts() != [])
    assert voice.starts() == [{"type": "music_start", "stream_url": URL}]
    assert voice.fake.played == []
    # The question is still parked for its minute; a later "yes" answers it.
    assert (await voice.pending())["kind"] == "core.music_choice"


async def test_a_new_command_in_the_followup_drops_the_question(voice) -> None:
    await _asked(voice)
    await voice.say("What time is it?", trigger="followup", utt=2)
    assert await voice.pending() is None
    await voice.say("Yes.", trigger="wake_word", utt=3)
    assert voice.fake.played == []


async def test_a_whole_yes_may_end_its_followup_early_a_correction_may_not(voice) -> None:
    """The early-commit dry run reads the parked question from the session
    (StreamSession._read_commit_context) and plans a whole "yes" as its
    confirmation, tier B; "no, play X" has an open tail."""
    from domovoi.early_commit import TIER_B, early_commit_for

    await _asked(voice)
    pending, chat = await voice.sess._read_commit_context()
    assert chat is False and pending["kind"] == "core.music_choice"
    ec = early_commit_for("Yes.", pending=pending)
    assert ec is not None and ec.tier == TIER_B and ec.plan.handler.name == "music"
    # A bare "no" is the reply most often followed by the correction.
    assert early_commit_for("No.", pending=pending) is None
    assert early_commit_for("No, play the Velvet Kites.", pending=pending) is None
    assert early_commit_for("Velvet Kites.", pending=pending) is None


async def test_an_early_committed_yes_is_answered_by_the_choice(voice) -> None:
    await _asked(voice)
    voice.said = "Yes."
    committed = streaming._Committed(serial=1, utt=2, frames=20, tier="B")
    await voice.sess._process_utterance(
        b"\x00\x00" * 480 * 20, trigger="followup", early_commit=committed,
    )
    await _settle(0.05)
    kinds = voice.kinds()
    assert "end_capture" in kinds
    assert voice.fake.played_labels == ["Glass Harbor"]
    assert voice.starts()[-1] == {"type": "music_start", "stream_url": NEW_URL}

