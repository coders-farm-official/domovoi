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

    async def announce(self, text: str) -> None:
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
