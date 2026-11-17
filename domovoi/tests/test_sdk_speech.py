"""sdk.speech (SDK 1.1) — the out-of-turn announcement plane.

Unit tier only: the API is a thin wrapper over the bound active-session
map; delivery mechanics (mid-response skip, music restore) belong to
StreamSession.announce and are covered by the streaming tests.
"""

from __future__ import annotations

import pytest

from domovoi.sdk import speech as speech_mod
from domovoi.sdk.speech import SpeechAPI, bind_active_sessions

pytestmark = pytest.mark.asyncio


class _FakeSession:
    def __init__(self, room_id: str, *, explode: bool = False) -> None:
        self.room_id = room_id
        self.explode = explode
        self.announced: list[str] = []

    async def announce(self, text: str, **kw) -> None:
        if self.explode:
            raise OSError("satellite dropped wifi")
        self.announced.append(text)


@pytest.fixture(autouse=True)
def _reset_binding():
    original = speech_mod._active_sessions_provider
    yield
    speech_mod._active_sessions_provider = original


async def test_unbound_reaches_nobody():
    speech_mod._active_sessions_provider = None
    assert await SpeechAPI("kiwix").announce("kitchen", "hello") == []


async def test_blank_text_is_a_noop():
    sessions = {"kitchen": _FakeSession("kitchen")}
    bind_active_sessions(lambda: sessions)
    assert await SpeechAPI("kiwix").announce("kitchen", "   ") == []
    assert sessions["kitchen"].announced == []


async def test_targeted_announce():
    sessions = {
        "kitchen": _FakeSession("kitchen"),
        "office": _FakeSession("office"),
    }
    bind_active_sessions(lambda: sessions)
    reached = await SpeechAPI("kiwix").announce("kitchen", "summary ready")
    assert reached == ["kitchen"]
    assert sessions["kitchen"].announced == ["summary ready"]
    assert sessions["office"].announced == []


async def test_broadcast_announce():
    sessions = {
        "kitchen": _FakeSession("kitchen"),
        "office": _FakeSession("office"),
    }
    bind_active_sessions(lambda: sessions)
    reached = await SpeechAPI("kiwix").announce(None, "dinner's ready")
    assert sorted(reached) == ["kitchen", "office"]


async def test_disconnected_room_reaches_nobody():
    bind_active_sessions(lambda: {"kitchen": _FakeSession("kitchen")})
    assert await SpeechAPI("kiwix").announce("attic", "hello") == []


async def test_failing_session_is_isolated():
    sessions = {
        "kitchen": _FakeSession("kitchen", explode=True),
        "office": _FakeSession("office"),
    }
    bind_active_sessions(lambda: sessions)
    reached = await SpeechAPI("kiwix").announce(None, "hello")
    assert reached == ["office"]          # failure logged, not raised


# ── render (SDK 1.9): text to a WAV in the room's voice ──────────────────


def _wav(n_frames: int, rate: int) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x01\x00" * n_frames)
    return buf.getvalue()


class _FakeTTS:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def synthesize(self, text, *, engine=None, voice=None):
        self.calls.append((text, engine, voice))
        return _wav(100, 22050)


@pytest.fixture
def _render_env(monkeypatch):
    import domovoi.clients.tts as tts_mod
    import domovoi.streaming as streaming

    tts = _FakeTTS()
    resolved: list[str | None] = []

    async def fake_resolve(name):
        resolved.append(name)
        return ("piper", f"model-of-{name or 'default'}")

    async def no_kept_voice(room_id):
        return None

    monkeypatch.setattr(tts_mod, "get_tts_client", lambda: tts)
    monkeypatch.setattr(streaming, "resolve_voice", fake_resolve)
    original = speech_mod._room_voices_provider
    yield tts, resolved
    speech_mod._room_voices_provider = original


async def test_render_speaks_in_the_rooms_voice(_render_env):
    import io
    import wave

    tts, resolved = _render_env
    speech_mod.bind_room_voices(lambda: {"bedroom": "amy"})
    wav = await SpeechAPI("sleep").render(
        "Once upon a time. The fox slept.", room_id="bedroom"
    )
    assert resolved == ["amy"]
    assert [c[0] for c in tts.calls] == ["Once upon a time.", "The fox slept."]
    assert all(c[2] == "model-of-amy" for c in tts.calls)
    with wave.open(io.BytesIO(wav), "rb") as wf:
        assert wf.getframerate() == 22050
        assert wf.getnframes() == 200  # both sentences, joined


async def test_render_without_a_room_uses_the_server_voice(_render_env):
    tts, resolved = _render_env
    speech_mod.bind_room_voices(lambda: {"bedroom": "amy"})
    await SpeechAPI("sleep").render("Hello there.")
    assert resolved == [None]


async def test_render_falls_back_to_the_kept_voice(_render_env, monkeypatch):
    tts, resolved = _render_env
    speech_mod.bind_room_voices(lambda: {})

    async def kept(room_id):
        return "lessac" if room_id == "kids" else None

    from domovoi.db import repositories

    class _Repo:
        def __init__(self, s):
            pass

        async def room_voice(self, room_id):
            return await kept(room_id)

    monkeypatch.setattr(repositories, "SatellitesRepository", _Repo)
    import contextlib

    import domovoi.db.session as session_mod

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield None

    monkeypatch.setattr(session_mod, "session_scope", fake_scope)
    await SpeechAPI("sleep").render("Hi.", room_id="kids")
    assert resolved == ["lessac"]


async def test_render_refuses_empty_text(_render_env):
    with pytest.raises(ValueError):
        await SpeechAPI("sleep").render("   ")


async def test_ollama_settings_are_plugin_visible():
    from domovoi.sdk.coreconfig import CoreConfigView

    view = CoreConfigView()
    assert view.ollama_url and view.ollama_model
