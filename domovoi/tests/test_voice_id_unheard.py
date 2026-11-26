"""A turn whose voice was never scored is not an unknown voice.

Found on real hardware (1.3.0, ERes2Net): "who am I?" in a room answered
"I don't recognize your voice yet … upgraded …" with outcome
``no_embedding``, a minute after the same person enrolled there. The
recording was over ``voice_profile_min_utterance_sec`` (the satellite keeps
the second of silence it waited through), but the embedder measures the
speech alone (domovoi/voice_prep.py), and "who am I?" is about half a
second of it: the embedder rightly made nothing, identification judged the
recording's length and called it ``no_embedding``, and the reply claimed
not to know someone it had never scored.

Now the outcome is ``too_short`` (with the speech's own length), and every
reply that would say "I don't know you" says instead that it couldn't tell
who was talking (``voice_identifier.unheard_text``).
"""

from __future__ import annotations

import numpy as np
import pytest

from domovoi import voice_identifier
from domovoi.clients.voice_embedder import EMBEDDING_DIM, RealVoiceEmbedder
from domovoi.config import settings
from domovoi.handlers.memory import MemoryHandler
from domovoi.handlers.voice_profile import VoiceProfileHandler
from domovoi.models import Context

RATE = 16_000


def _recording(speech_sec: float, before: float = 0.3, after: float = 1.0) -> bytes:
    """A satellite-shaped recording: a little room, ``speech_sec`` of a
    voice-loud signal, then the trailing second of quiet."""
    rng = np.random.default_rng(7)
    n = int((before + speech_sec + after) * RATE)
    wav = rng.normal(0, 30, n)                                  # the room, about -60 dBFS
    s0, s1 = int(before * RATE), int((before + speech_sec) * RATE)
    t = np.arange(s1 - s0) / RATE
    wav[s0:s1] += 6000 * np.sin(2 * np.pi * 180 * t) * (1 + 0.3 * np.sin(2 * np.pi * 4 * t))
    return np.clip(wav, -32768, 32767).astype(np.int16).tobytes()


class _FakeEncoder:
    def embed_utterance(self, wav: np.ndarray) -> np.ndarray:
        v = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        v[0] = 1.0
        return v


@pytest.fixture
def real_embedder(monkeypatch) -> RealVoiceEmbedder:
    """The real embedder's path (speech measuring, the minimum) with a
    stand-in for the ONNX network, which isn't on a test box."""
    emb = RealVoiceEmbedder()
    emb._encoder = _FakeEncoder()
    monkeypatch.setattr(voice_identifier, "get_voice_embedder", lambda: emb)
    return emb


# ─── (b) why there was no embedding ───────────────────────────────────────


async def test_a_short_command_in_a_long_recording_makes_no_print(real_embedder) -> None:
    """The live case: over a second of recording, well under a second of
    speech. The embedder declines (by design: too little speech to name
    anyone from), and that is what the recording is: too short."""
    pcm = _recording(0.55)
    assert len(pcm) / (RATE * 2) >= settings.voice_profile_min_utterance_sec
    assert await real_embedder.embed_print(pcm) is None
    # Enough speech in the same shape of recording: a print.
    voice = await real_embedder.embed_print(_recording(1.6))
    assert voice is not None and voice.speech_sec >= settings.voice_profile_min_utterance_sec


async def test_identify_calls_it_too_short_with_the_speechs_length(real_embedder) -> None:
    ident = await voice_identifier.identify(_recording(0.55), room_id="garage")
    assert ident.person_id is None and ident.embedding is None
    assert ident.outcome == voice_identifier.OUTCOME_TOO_SHORT
    assert ident.speech_seconds is not None and 0.4 < ident.speech_seconds < 1.0
    # What the log and the history row say: the speech, not the recording's 1.85 s.
    match = voice_identifier.VoiceMatch.of(ident, speech_seconds=1.85)
    assert match.outcome == "too_short" and match.speech_seconds == ident.speech_seconds
    assert match.describe().startswith("too short to tell (0.")


async def test_a_recording_under_the_minimum_is_too_short_as_before(real_embedder) -> None:
    pcm = _recording(0.3, before=0.1, after=0.2)
    ident = await voice_identifier.identify(pcm, room_id="garage")
    assert ident.outcome == voice_identifier.OUTCOME_TOO_SHORT


async def test_enough_speech_and_still_no_print_is_no_embedding(monkeypatch) -> None:
    """When the embedder fails on plenty of speech, the outcome stays
    ``no_embedding``: not blamed on the speaker."""

    class _Broken:
        async def embed_print(self, pcm):
            return None

    monkeypatch.setattr(voice_identifier, "get_voice_embedder", lambda: _Broken())
    ident = await voice_identifier.identify(_recording(1.6), room_id="garage")
    assert ident.outcome == voice_identifier.OUTCOME_NO_EMBEDDING


async def test_without_the_model_it_is_no_embedding(monkeypatch) -> None:
    class _Broken:
        async def embed_print(self, pcm):
            return None

    monkeypatch.setattr(voice_identifier, "get_voice_embedder", lambda: _Broken())
    monkeypatch.setattr(voice_identifier, "voice_id_unavailable", lambda: True)
    ident = await voice_identifier.identify(_recording(0.55), room_id="garage")
    assert ident.outcome == voice_identifier.OUTCOME_NO_EMBEDDING


# ─── (a) the replies ──────────────────────────────────────────────────────


def _ctx(outcome: str | None, person_id: int | None = None) -> Context:
    return Context(room_id="garage", online=True, person_id=person_id, voice_outcome=outcome)


def test_unheard_text_only_for_a_voice_never_scored() -> None:
    for outcome in voice_identifier.OUTCOMES:
        said = voice_identifier.unheard_text(outcome)
        assert (said is not None) == (outcome in voice_identifier.UNHEARD), outcome
        if said:
            assert "recognize" not in said and "know" not in said
    assert voice_identifier.unheard_text(None) is None


@pytest.mark.parametrize("outcome", ["too_short", "no_embedding"])
async def test_who_am_i_too_short_says_so_not_that_it_doesnt_know_you(outcome) -> None:
    text = (await VoiceProfileHandler()._whoami(_ctx(outcome), None)).text
    assert "recognize" not in text and "upgraded" not in text and "my name is" not in text
    assert "to tell who's talking" in text
    assert 'like "who am I, Domovoi?"' in text


async def test_who_am_i_too_short_exact_words() -> None:
    text = (await VoiceProfileHandler()._whoami(_ctx("too_short"), None)).text
    assert text == (
        "That was too short for me to tell who's talking. "
        'Try again with a few more words, like "who am I, Domovoi?"'
    )


@pytest.mark.parametrize("outcome", ["timeout", "error"])
async def test_who_am_i_when_identification_did_not_finish(outcome) -> None:
    text = (await VoiceProfileHandler()._whoami(_ctx(outcome), None)).text
    assert text == "I couldn't work out who's talking that time. Please ask me again in a moment."


@pytest.mark.parametrize("outcome", ["no_match", "no_samples", None])
async def test_who_am_i_still_says_unknown_when_the_voice_was_scored(outcome) -> None:
    # (No session: reading who has to re-introduce fails, the plain answer stands.)
    text = (await VoiceProfileHandler()._whoami(_ctx(outcome), None)).text
    assert text == "I don't recognize your voice yet."


async def test_memory_does_not_ask_an_unheard_voice_to_introduce_itself() -> None:
    handler = MemoryHandler()
    short = await handler._ensure_person(_ctx("too_short"))
    assert short is not None and "too short" in short.text and "my name is" not in short.text
    unknown = await handler._ensure_person(_ctx("no_match"))
    assert unknown is not None and "my name is" in unknown.text
    assert await handler._ensure_person(_ctx("too_short", person_id=3)) is None
