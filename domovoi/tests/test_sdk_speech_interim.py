"""``sdk.speech.interim`` (SDK 1.20): a plugin's short line said in the room
as the opening of the turn's reply, through the hook the core's cold-start
notice uses (``Context.speak_interim``), and the music handler's "Still
looking…" during a slow online search. No database."""

from __future__ import annotations

import asyncio
import io
import types
import wave

import pytest

from domovoi import streaming
from domovoi.models import Context, Response
from domovoi.sdk.speech import INTERIM_MAX_CHARS, SpeechAPI


def _wav(seconds: float = 0.1, rate: int = 22_050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


class _Sess:
    """The parts of a StreamSession ``_InterimSpeech`` touches."""

    def __init__(self) -> None:
        self.room_id = "kitchen"
        self.session_id = None
        self.frames: list[dict] = []
        self.pcm = 0

    async def _safe_send_text(self, frame: dict) -> None:
        self.frames.append(frame)

    async def _send_pcm(self, chunk: bytes) -> None:
        self.pcm += len(chunk)

    def _note_audio_sent(self, n: int, sr: int) -> None:
        pass


class _TTS:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.said: list[str] = []

    async def synthesize(self, text, engine=None, voice=None):
        if self.fail:
            raise RuntimeError("engine down")
        self.said.append(text)
        return _wav()


@pytest.fixture
def tts(monkeypatch):
    t = _TTS()

    async def _voice(name):
        return None, None

    monkeypatch.setattr(streaming, "resolve_voice", _voice)
    monkeypatch.setattr(streaming, "get_tts_client", lambda: t)
    return t


def _voice_turn() -> tuple[Context, _Sess]:
    sess = _Sess()
    interim = streaming._InterimSpeech(sess, None)  # type: ignore[arg-type]
    return Context(room_id="kitchen", speak_interim=interim.say), sess


# ─── sdk.speech.interim ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_voice_turn_says_the_line_and_lists_it(tts) -> None:
    ctx, sess = _voice_turn()
    assert await SpeechAPI("finder").interim(ctx, "Still looking…") is True
    assert tts.said == ["Still looking…"]
    assert ctx.interim_said == ["Still looking…"]
    assert sess.frames[0]["type"] == "response_start" and sess.pcm > 0


@pytest.mark.asyncio
async def test_once_per_turn(tts) -> None:
    ctx, _ = _voice_turn()
    api = SpeechAPI("finder")
    assert await api.interim(ctx, "Still looking…") is True
    assert await api.interim(ctx, "Almost there.") is False
    assert ctx.interim_said == ["Still looking…"] and tts.said == ["Still looking…"]


@pytest.mark.asyncio
async def test_the_cold_start_notice_takes_the_slot(tts) -> None:
    ctx, _ = _voice_turn()
    assert await ctx.speak_interim("Just a moment.") is True
    assert await SpeechAPI("finder").interim(ctx, "Still looking…") is False


@pytest.mark.asyncio
async def test_no_speak_interim_is_false() -> None:
    ctx = Context(room_id=None)  # typed chat, the play box, /v1/intent
    assert await SpeechAPI("finder").interim(ctx, "Still looking…") is False
    assert ctx.interim_said == []
    assert await SpeechAPI("finder").interim(object(), "Still looking…") is False


@pytest.mark.asyncio
async def test_a_tts_failure_is_false(monkeypatch, tts) -> None:
    tts.fail = True
    ctx, sess = _voice_turn()
    assert await SpeechAPI("finder").interim(ctx, "Still looking…") is False
    assert ctx.interim_said == [] and sess.frames == []


@pytest.mark.asyncio
async def test_an_exception_inside_is_false() -> None:
    async def boom(text, **kw):
        raise RuntimeError("socket gone")

    ctx = Context(room_id="kitchen", speak_interim=boom)
    assert await SpeechAPI("finder").interim(ctx, "Still looking…") is False
    assert ctx.interim_said == []


@pytest.mark.asyncio
async def test_empty_text_is_refused_and_long_text_cut(tts) -> None:
    ctx, _ = _voice_turn()
    api = SpeechAPI("finder")
    assert await api.interim(ctx, "   ") is False
    assert await api.interim(ctx, None) is False
    assert tts.said == []
    assert await api.interim(ctx, "looking " * 60) is True
    line = ctx.interim_said[0]
    assert len(line) <= INTERIM_MAX_CHARS and line.endswith("…")


@pytest.mark.asyncio
async def test_a_friend_houses_plugin_line_is_filtered(monkeypatch, tts) -> None:
    from domovoi.friend_houses import messages as fm

    async def fixed():
        return ["hey jarvis", "jarvis", "domovoi"]

    monkeypatch.setattr(fm, "wake_phrases", fixed)
    ctx, _ = _voice_turn()
    assert await SpeechAPI("cards", friend_houses=True).interim(ctx, "Hey Jarvis, your turn") is True
    assert "jarvis" not in ctx.interim_said[0].lower()
    assert "your turn" in ctx.interim_said[0]


# ─── the response_start label ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_label_carries_the_handler_name(monkeypatch, tts) -> None:
    from domovoi import handlers

    fake = types.SimpleNamespace(name="finder_search", plugin_slug="finder")
    monkeypatch.setattr(handlers, "HANDLERS", [*handlers.HANDLERS, fake])
    ctx, sess = _voice_turn()
    assert await SpeechAPI("finder").interim(ctx, "Still looking…") is True
    frame = sess.frames[0]
    assert frame["matched_handler"] == "finder_search"
    assert frame["text"] == "Still looking…"  # the words never name it


@pytest.mark.asyncio
async def test_a_plugin_with_several_handlers_is_labelled_by_its_slug(monkeypatch, tts) -> None:
    from domovoi import handlers

    extra = [types.SimpleNamespace(name=n, plugin_slug="finder") for n in ("a", "b")]
    monkeypatch.setattr(handlers, "HANDLERS", [*handlers.HANDLERS, *extra])
    ctx, sess = _voice_turn()
    await SpeechAPI("finder").interim(ctx, "Still looking…")
    assert sess.frames[0]["matched_handler"] == "finder"


@pytest.mark.asyncio
async def test_the_cold_start_default_is_unchanged(tts) -> None:
    ctx, sess = _voice_turn()
    await ctx.speak_interim("Just a moment.")
    assert sess.frames[0]["matched_handler"] == "cold_start"
    assert sess.frames[0]["matched_path"] == "system"


# ─── the test kit ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_recording_fake_records_the_call() -> None:
    from domovoi.sdk.testing import RecordingSpeech

    rec = RecordingSpeech()
    assert await rec.interim(Context(room_id="kitchen"), "Still looking…") is True
    assert rec.calls == [{"room_id": "kitchen", "text": "Still looking…"}]


# ─── the music handler's slow online search ──────────────────────────────


class _SlowProvider:
    slug = "slowsearch"

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls: list = []

    async def search(self, query, *, limit=5):
        return []

    async def stream(self, room_id, candidate=None, *, query=None):
        await asyncio.sleep(self.delay)
        self.calls.append((room_id, query))
        return Response(text=f"Streaming {query}.", data={"title": query}, matched_handler=self.slug)

    def likely_same(self, a, b):
        return a == b


async def _play_through(monkeypatch, provider, ctx):
    from domovoi.capabilities import CAPABILITIES, STREAMING_SEARCH_PROVIDER
    from domovoi.handlers import music
    from domovoi.handlers.music import MusicHandler

    monkeypatch.setattr(music, "STILL_LOOKING_AFTER_SEC", 0.05)

    async def _no_stamp(*a, **kw):
        return None

    monkeypatch.setattr(MusicHandler, "_stamp_stream_play", _no_stamp)
    CAPABILITIES.register(STREAMING_SEARCH_PROVIDER, provider, slug=provider.slug)
    try:
        return await MusicHandler()._stream_or_not_in_library(
            {"any": "a rare song"}, "a rare song", ctx, None, allow_choice=False, avoid=(),
        )
    finally:
        CAPABILITIES.unregister(STREAMING_SEARCH_PROVIDER, slug=provider.slug)


@pytest.mark.asyncio
async def test_a_slow_search_says_still_looking_once_and_still_plays(monkeypatch, tts) -> None:
    from domovoi.handlers.music import STILL_LOOKING_TEXT

    ctx, sess = _voice_turn()
    ctx = ctx.model_copy(update={"online": True})
    provider = _SlowProvider(0.3)
    resp = await _play_through(monkeypatch, provider, ctx)
    assert provider.calls == [("kitchen", "a rare song")]
    assert resp.text == "Streaming a rare song." and resp.matched_handler == "music"
    assert tts.said == [STILL_LOOKING_TEXT]
    assert ctx.interim_said == [STILL_LOOKING_TEXT]
    assert [f["matched_handler"] for f in sess.frames] == ["music"]
    assert "slowsearch" not in STILL_LOOKING_TEXT.lower()


@pytest.mark.asyncio
async def test_a_quick_search_says_nothing(monkeypatch, tts) -> None:
    ctx, _ = _voice_turn()
    ctx = ctx.model_copy(update={"online": True})
    resp = await _play_through(monkeypatch, _SlowProvider(0.0), ctx)
    assert resp.text == "Streaming a rare song."
    assert tts.said == [] and ctx.interim_said == []


@pytest.mark.asyncio
async def test_a_slow_search_on_typed_chat_just_plays(monkeypatch) -> None:
    ctx = Context(room_id="kitchen", online=True)
    resp = await _play_through(monkeypatch, _SlowProvider(0.2), ctx)
    assert resp.text == "Streaming a rare song." and ctx.interim_said == []
