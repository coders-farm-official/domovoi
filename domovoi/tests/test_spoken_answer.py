"""A voice Q&A answer is spoken sentence by sentence as the model writes it.

The QA fallthrough used to wait for llama3.2:3b's whole reply before the
first word was synthesized (0.7-6 s on a CPU host). Now the streaming layer
speaks each sentence as soon as it is complete. Pinned here, DB-free:

  * a sentence is handed over the moment it ends — before the rest of the
    reply has even been written — and a joke keeps its punchline;
  * the whole-reply rules still hold where they can: nothing usable yet
    (empty, a bare placeholder, cut on a contraction stem) is asked for
    once more; a cut tail is never spoken; ``unreachable`` only when the
    calls failed and nothing was said; a failure after the first sentence
    keeps what was said and asks for nothing twice;
  * closing the answer (a barge-in) closes the model's stream;
  * the router hands a voice turn a stream and records it once said
    (``Response.qa_stream``), the offer said after the answer, while
    /v1/intent gets the whole reply as before (DB tests, lane-safe);
  * the streaming layer opens the response at the first sentence, keeps the
    self-echo text whole, records the turn, speaks the offer, and on a
    barge-in stops the model and records what was said.
"""

from __future__ import annotations

import asyncio
import io
import json
import types
import wave
from contextlib import asynccontextmanager

import pytest

from domovoi import streaming
from domovoi.clients.ollama import OllamaStubClient, RealOllamaClient, voice_qa_system_prompt
from domovoi.config import settings
from domovoi.models import Context, Intent, Response
from domovoi.spoken_answer import SpokenAnswer, StreamedQA
from domovoi.tests.conftest import requires_db

JOKE = ["Why did", " the scarecrow", " win an award?", " Because he was", " outstanding", " in his field."]


class _Stream:
    """One streamed Q&A call: yields deltas, can fail at a given point,
    records how far it was read and whether it was closed."""

    def __init__(self, deltas, *, fail_at: int | None = None, error: Exception | None = None) -> None:
        self.deltas = list(deltas)
        self.fail_at = fail_at
        self.error = error or RuntimeError("connection reset")
        self.read = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.fail_at is not None and self.read == self.fail_at:
            raise self.error
        if self.read >= len(self.deltas):
            raise StopAsyncIteration
        d = self.deltas[self.read]
        self.read += 1
        await asyncio.sleep(0)
        return d

    async def aclose(self) -> None:
        self.closed = True


def _answer(*streams: _Stream) -> tuple[SpokenAnswer, list[_Stream]]:
    queue = list(streams)
    opened: list[_Stream] = []

    def open_stream():
        s = queue.pop(0)
        opened.append(s)
        return s

    return SpokenAnswer(open_stream, transcript="test"), opened


async def _all(answer: SpokenAnswer) -> list[str]:
    return [s async for s in answer.sentences()]


# ─── sentence by sentence ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_first_sentence_is_handed_over_before_the_rest_is_written() -> None:
    stream = _Stream(JOKE)
    answer, _ = _answer(stream)
    agen = answer.sentences()
    first = await agen.__anext__()
    assert first == "Why did the scarecrow win an award?"
    # Handed over as soon as the next words showed the sentence had ended.
    assert stream.read == 4 < len(JOKE)
    assert answer.first_sentence_at is not None
    rest = [s async for s in agen]
    assert rest == ["Because he was outstanding in his field."]
    assert answer.answer == "Why did the scarecrow win an award? Because he was outstanding in his field."
    assert stream.closed


@pytest.mark.asyncio
async def test_a_joke_keeps_its_punchline() -> None:
    answer, _ = _answer(_Stream(["What do you call a fake noodle?", " An impasta!"]))
    assert await _all(answer) == ["What do you call a fake noodle?", "An impasta!"]


@pytest.mark.asyncio
async def test_a_reply_without_an_end_is_said_whole_at_the_end() -> None:
    answer, _ = _answer(_Stream(["Paris is the capital", " of France"]))
    assert await _all(answer) == ["Paris is the capital of France"]


# ─── nothing usable yet: asked once more ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [[], ["  "], ["None."], ["N/A"], ["I don"]])
async def test_nothing_usable_is_asked_for_once_more(first) -> None:
    answer, opened = _answer(_Stream(first), _Stream(["Jane Austen wrote it."]))
    assert await _all(answer) == ["Jane Austen wrote it."]
    assert len(opened) == 2 and all(s.closed for s in opened)
    assert answer.unreachable is False


@pytest.mark.asyncio
async def test_empty_twice_says_nothing_and_is_not_an_outage() -> None:
    answer, opened = _answer(_Stream([]), _Stream(["None."]))
    assert await _all(answer) == []
    assert answer.answer == "" and answer.unreachable is False
    assert len(opened) == 2


@pytest.mark.asyncio
async def test_a_placeholder_first_sentence_is_held_until_more_follows() -> None:
    answer, opened = _answer(_Stream(["None.", " But here is", " a guess."]))
    assert await _all(answer) == ["None.", "But here is a guess."]
    assert len(opened) == 1


@pytest.mark.asyncio
async def test_a_cut_tail_is_never_spoken() -> None:
    answer, opened = _answer(_Stream(["It is sunny today.", " I don"]))
    assert await _all(answer) == ["It is sunny today."]
    assert answer.answer == "It is sunny today."
    assert len(opened) == 1                     # said something: no retry


@pytest.mark.asyncio
async def test_cut_twice_keeps_the_complete_sentences() -> None:
    answer, opened = _answer(_Stream(["I couldn"]), _Stream(["Well, hmm. I didn"]))
    # "Well, hmm." ends a sentence but nothing after it does: the second
    # reply is judged whole (nothing said yet) and keeps what is complete.
    assert await _all(answer) == ["Well, hmm."]
    assert len(opened) == 2


# ─── failures ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_call_is_retried_then_reported_unreachable() -> None:
    answer, opened = _answer(_Stream([], fail_at=0), _Stream([], fail_at=0))
    assert await _all(answer) == []
    assert answer.unreachable is True and len(opened) == 2


@pytest.mark.asyncio
async def test_a_timeout_is_not_waited_out_twice() -> None:
    answer, opened = _answer(_Stream([], fail_at=0, error=TimeoutError("read timeout")))
    assert await _all(answer) == []
    assert answer.unreachable is True and len(opened) == 1


@pytest.mark.asyncio
async def test_a_failure_after_the_first_sentence_keeps_what_was_said() -> None:
    answer, opened = _answer(_Stream(JOKE, fail_at=4))
    assert await _all(answer) == ["Why did the scarecrow win an award?"]
    assert answer.unreachable is False and len(opened) == 1 and opened[0].closed


@pytest.mark.asyncio
async def test_closing_the_answer_closes_the_model_stream() -> None:
    stream = _Stream(JOKE)
    answer, _ = _answer(stream)
    agen = answer.sentences()
    await agen.__anext__()
    await agen.aclose()                          # the barge-in
    assert stream.closed
    assert answer.answer == "Why did the scarecrow win an award?"


# ─── the clients ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_real_client_streams_with_the_voice_prompt() -> None:
    calls: list[dict] = []

    async def chat(**kwargs):
        calls.append(kwargs)

        async def gen():
            yield {"message": {"content": "Hi."}}

        return gen()

    c = RealOllamaClient.__new__(RealOllamaClient)
    c._client = type("C", (), {"chat": staticmethod(chat)})()
    c._qa_model, c._tool_model = "llama3.2:3b", "qwen3:8b"
    c._keep_alive, c._send_keep_alive = "24h", True
    out = [d async for d in c.stream_voice_answer("hi", history=[{"role": "user", "text": "yo"}],
                                                  profile_prefix="User context: likes jazz.")]
    assert out == ["Hi."]
    call = calls[0]
    assert call["stream"] is True and call["model"] == "llama3.2:3b"
    assert call["messages"][0] == {"role": "system", "content": voice_qa_system_prompt("User context: likes jazz.")}
    assert call["messages"][1] == {"role": "user", "content": "yo"}
    assert call["messages"][-1] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_the_stub_streams_what_it_would_have_answered() -> None:
    stub = OllamaStubClient()
    whole = await stub.qa_with_uncertainty("hi", profile_prefix="likes jazz")
    streamed = "".join([d async for d in stub.stream_voice_answer("hi", profile_prefix="likes jazz")])
    assert streamed == whole.answer


# ─── the router: a stream for a voice turn, the whole reply otherwise ─────


class _StreamingOllama:
    """A client that can stream a voice answer, and answers whole too."""

    def __init__(self, deltas, *, whole: str = "Whole answer.") -> None:
        self.deltas = deltas
        self.whole = whole
        self.streamed = 0
        self.whole_calls = 0

    async def route(self, transcript, tool_schemas):
        return None

    async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
        from domovoi.clients.ollama import QAWithUncertainty

        self.whole_calls += 1
        return QAWithUncertainty(answer=self.whole, needs_verification=False)

    async def stream_voice_answer(self, transcript, history=None, profile_prefix=None):
        self.streamed += 1
        for d in self.deltas:
            yield d

    async def extract_search_subject(self, transcript, history=None):
        from domovoi.clients.ollama import SearchSubject

        return SearchSubject(subject="", refined_query=transcript)


async def _route_voice(db_session, client, transcript: str, *, stream_qa: bool = True):
    from unittest.mock import patch

    from domovoi.router import route

    with patch("domovoi.router.get_ollama_client", lambda: client):
        return await route(
            Intent(transcript=transcript, room_id="kitchen"),
            Context(room_id="kitchen", online=True, stream_qa=stream_qa),
            db_session,
        )


@requires_db
@pytest.mark.asyncio
async def test_a_voice_turn_gets_the_answer_as_a_stream_and_records_it_once_said(db_session) -> None:
    from sqlalchemy import text

    client = _StreamingOllama(JOKE)
    routed = await _route_voice(db_session, client, "Tell me a joke.")
    await db_session.commit()
    assert isinstance(routed.qa_stream, StreamedQA)
    assert routed.text == "" and routed.matched_path == "qa"
    assert "qa_stream" not in routed.model_dump()           # never serialized
    said = [s async for s in routed.qa_stream.answer.sentences()]
    response, rest = await routed.qa_stream.finish(db_session)
    await db_session.commit()
    assert said == ["Why did the scarecrow win an award?", "Because he was outstanding in his field."]
    assert response.text == " ".join(said) and rest == ""
    assert client.whole_calls == 0 and client.streamed == 1
    row = (await db_session.execute(text(
        "SELECT assistant_text, matched_path FROM conversation_log ORDER BY id DESC LIMIT 1"
    ))).one()
    assert tuple(row) == (response.text, "qa")              # the full text, punchline included


@requires_db
@pytest.mark.asyncio
async def test_a_streamed_answer_that_admits_it_is_stale_gets_the_offer_after_it(db_session) -> None:
    client = _StreamingOllama(["My information might be out of date.", " It was Homer, last I knew."])
    routed = await _route_voice(db_session, client, "Who wrote the Odyssey?")
    [s async for s in routed.qa_stream.answer.sentences()]
    response, rest = await routed.qa_stream.finish(db_session)
    await db_session.commit()
    assert rest == "Want me to check that online?"
    assert response.text.endswith(" Want me to check that online?")
    assert response.expect_followup is True


@requires_db
@pytest.mark.asyncio
async def test_a_streamed_answer_with_nothing_in_it_says_the_no_answer_line(db_session) -> None:
    from domovoi.router import _NO_ANSWER_TEXT

    client = _StreamingOllama([])
    routed = await _route_voice(db_session, client, "Back so soon.")
    assert [s async for s in routed.qa_stream.answer.sentences()] == []
    response, rest = await routed.qa_stream.finish(db_session)
    await db_session.commit()
    assert response.text == rest == _NO_ANSWER_TEXT
    assert response.matched_path == "qa"


@requires_db
@pytest.mark.asyncio
async def test_without_a_voice_turn_the_reply_comes_whole(db_session) -> None:
    client = _StreamingOllama(JOKE, whole="A whole joke.")
    response = await _route_voice(db_session, client, "Tell me a joke.", stream_qa=False)
    await db_session.commit()
    assert response.qa_stream is None and response.text == "A whole joke."
    assert client.streamed == 0


# ─── the streaming layer ──────────────────────────────────────────────────


def _wav(seconds: float, rate: int = 22_050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                satellite_voice={}, wifi_status={}, satellite_volume={},
                greeting_phrases=[], greeting_clips={},
                probe=types.SimpleNamespace(online=True),
                active_sessions={}, resumable_music={}, current_playlist={},
            )
        )
        self.frames: list[tuple[str, object]] = []

    async def send_text(self, data: str) -> None:
        self.frames.append(("text", json.loads(data)))

    async def send_bytes(self, data: bytes) -> None:
        self.frames.append(("bytes", data))


@asynccontextmanager
async def _no_db():
    yield None


def _streamed_turn(monkeypatch, deltas, *, rest: str = "", gate: asyncio.Event | None = None):
    """A turn whose route() hands back a streamed answer; the router's
    finish is recorded instead of written."""
    seen: dict = {"finished": [], "synth": []}

    async def gated_stream():
        for i, d in enumerate(deltas):
            if gate is not None and i == 4:
                await gate.wait()
            await asyncio.sleep(0)
            yield d

    class _Whisper:
        async def transcribe(self, pcm):
            return "tell me a joke"

    class _TTS:
        async def synthesize(self, text, engine=None, voice=None):
            seen["synth"].append(text)
            return _wav(0.2)

    async def _route(intent, ctx, session):
        assert ctx.stream_qa is True
        spoken = SpokenAnswer(gated_stream, transcript=intent.transcript)

        async def finish(s):
            seen["finished"].append(spoken.answer)
            text = spoken.answer + (f" {rest}" if rest else "")
            return Response(text=text, matched_path="qa", online=True,
                            expect_followup=bool(rest)), rest

        return Response(text="", matched_path="qa", online=True,
                        qa_stream=StreamedQA(answer=spoken, finish=finish))

    async def _identify(pcm, embedding=None):
        return None

    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper())
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    monkeypatch.setattr(streaming, "route", _route)
    monkeypatch.setattr(streaming, "session_scope", _no_db)
    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    return seen


@pytest.mark.asyncio
async def test_the_first_sentence_is_spoken_while_the_rest_is_written(monkeypatch) -> None:
    gate = asyncio.Event()
    seen = _streamed_turn(monkeypatch, JOKE, gate=gate)
    ws = _FakeWS()
    sess = streaming.StreamSession(ws, "office")  # type: ignore[arg-type]
    turn = asyncio.create_task(sess._process_utterance(b"\x00" * 32_000, trigger="wake_word"))
    for _ in range(200):
        if any(kind == "bytes" for kind, _f in ws.frames):
            break
        await asyncio.sleep(0.005)
    # Audio of the first sentence is out while the model is held mid-answer.
    starts = [f for kind, f in ws.frames if kind == "text" and f["type"] == "response_start"]
    assert [s["text"] for s in starts] == ["Why did the scarecrow win an award?"]
    assert any(kind == "bytes" for kind, _f in ws.frames)
    assert seen["finished"] == []                      # not recorded yet
    gate.set()
    await turn
    texts = [f for kind, f in ws.frames if kind == "text"]
    assert [f["type"] for f in texts] == ["transcript", "response_start", "response_end"]
    assert seen["synth"] == ["Why did the scarecrow win an award?", "Because he was outstanding in his field."]
    assert seen["finished"] == ["Why did the scarecrow win an award? Because he was outstanding in his field."]
    # The self-echo guard hears the whole reply, not just its opening.
    assert sess._last_spoken_text == seen["finished"][0]
    assert texts[-1] == {"type": "response_end", "interrupted": False, "expect_followup": False}


@pytest.mark.asyncio
async def test_the_offer_is_said_after_the_answer(monkeypatch) -> None:
    seen = _streamed_turn(monkeypatch, ["Paris.", " Probably."], rest="Want me to check that online?")
    ws = _FakeWS()
    sess = streaming.StreamSession(ws, "office")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 32_000, trigger="wake_word")
    assert seen["synth"] == ["Paris.", "Probably.", "Want me to check that online?"]
    texts = [f for kind, f in ws.frames if kind == "text"]
    assert texts[-1] == {"type": "response_end", "interrupted": False, "expect_followup": True}


@pytest.mark.asyncio
async def test_nothing_to_say_speaks_the_routers_line(monkeypatch) -> None:
    seen = _streamed_turn(monkeypatch, [], rest="Sorry, I don't have an answer for that.")
    ws = _FakeWS()
    sess = streaming.StreamSession(ws, "office")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 32_000, trigger="wake_word")
    starts = [f for kind, f in ws.frames if kind == "text" and f["type"] == "response_start"]
    assert [s["text"] for s in starts] == ["Sorry, I don't have an answer for that."]


@pytest.mark.asyncio
async def test_a_barge_in_stops_the_model_and_records_what_was_said(monkeypatch) -> None:
    gate = asyncio.Event()                             # never set: the model "hangs"
    seen = _streamed_turn(monkeypatch, JOKE, gate=gate)
    ws = _FakeWS()
    sess = streaming.StreamSession(ws, "office")  # type: ignore[arg-type]
    turn = asyncio.create_task(sess._process_utterance(b"\x00" * 32_000, trigger="wake_word"))
    for _ in range(200):
        if any(kind == "bytes" for kind, _f in ws.frames):
            break
        await asyncio.sleep(0.005)
    turn.cancel()                                      # the barge-in
    await turn
    assert seen["finished"] == ["Why did the scarecrow win an award?"]
    texts = [f for kind, f in ws.frames if kind == "text"]
    assert texts[-1]["type"] == "response_end" and texts[-1]["interrupted"] is True


def test_voice_turns_ask_for_a_streamed_answer() -> None:
    assert Context().stream_qa is False                 # /v1/intent and the rest: whole replies
    assert settings.bot_name                            # the prompt still names the bot


@pytest.mark.asyncio
async def test_a_tts_failure_mid_answer_still_records_the_turn(monkeypatch) -> None:
    seen = _streamed_turn(monkeypatch, JOKE)

    class _DeadTTS:
        async def synthesize(self, text, engine=None, voice=None):
            raise RuntimeError("every engine failed")

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _DeadTTS())
    ws = _FakeWS()
    sess = streaming.StreamSession(ws, "office")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 32_000, trigger="wake_word")
    assert len(seen["finished"]) == 1                  # recorded once
    texts = [f for kind, f in ws.frames if kind == "text"]
    assert [f["type"] for f in texts][-2:] == ["error", "response_end"]
    assert texts[-1]["interrupted"] is True


@pytest.mark.asyncio
async def test_a_client_that_answers_a_stream_in_one_piece_still_speaks() -> None:
    async def chat(**kwargs):
        return {"message": {"content": "What do you call a fake noodle? An impasta!"}}

    c = RealOllamaClient.__new__(RealOllamaClient)
    c._client = type("C", (), {"chat": staticmethod(chat)})()
    c._qa_model, c._tool_model = "llama3.2:3b", "qwen3:8b"
    c._keep_alive, c._send_keep_alive = "24h", False
    answer = SpokenAnswer(lambda: c.stream_voice_answer("Tell me a joke."))
    assert [s async for s in answer.sentences()] == ["What do you call a fake noodle?", "An impasta!"]
