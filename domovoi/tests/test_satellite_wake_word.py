"""The wake word each room answers to, from its hello to the dashboard.

The user manual tells people what to say to a room (owner, 2026-10-17: a
v1.0.0 room answers to "Hey Jarvis", and a household whose rooms answer
to another word sees that word). So the word has to come from the rooms
themselves: every satellite names its effective wake word in its hello
(``wake_word``: the openWakeWord slug, a pushed word's slug after a
push); the core keeps each room's last one, also after a disconnect, and
``GET /api/satellites`` carries it per room, with a trained word's phrase
from the registry.

No database: the hello's inventory writes are recorded, not run, and the
web row is built from a snapshot dict.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from domovoi import streaming
from domovoi.streaming import StreamSession, _clean_wake_slug

REPO = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((REPO / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
DEVICES: dict[str, dict[str, Any]] = FIXTURES["devices"]


def _state() -> SimpleNamespace:
    # No satellite_wake_word here on purpose: a state built without it
    # (older tests' doubles) still takes the word.
    return SimpleNamespace(
        active_sessions={}, active_dropins={},
        satellite_full_duplex={}, satellite_synced_sha={}, satellite_sat_type={},
        satellite_mic_enabled={}, wifi_status={}, satellite_volume={}, satellite_voice={},
        satellite_config={}, satellite_display={},
    )


class FakeWS:
    def __init__(self, state: Any = None) -> None:
        self.app = SimpleNamespace(state=state or _state())
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        return None


@pytest.fixture
def paired_inventory(monkeypatch):
    async def _accept(self, ctrl):
        self.token_authenticated = True
        return True

    class Repo:
        def __init__(self, _s):
            pass

        async def upsert_type(self, room, sat_type):
            return None

        async def upsert_board(self, room, board):
            return None

    @asynccontextmanager
    async def scope():
        yield None

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)
    monkeypatch.setattr(streaming, "session_scope", scope)
    monkeypatch.setattr(streaming, "SatellitesRepository", Repo)


async def _hello(hello: dict, state: Any) -> StreamSession:
    session = StreamSession(FakeWS(state), hello["room_id"])  # type: ignore[arg-type]
    await session._on_control(dict(hello))
    return session


@pytest.mark.parametrize("raw, kept", [
    ("hey_jarvis", "hey_jarvis"), ("hey_domovoi", "hey_domovoi"), (" alexa ", "alexa"),
    ("Hello-Domo.v2", "Hello-Domo.v2"), ("", None), (None, None), (7, None), (["hey_jarvis"], None),
    ("hey jarvis", None), ("../etc", None), ("x" * 65, None),
])
def test_only_a_slug_is_kept(raw, kept):
    assert _clean_wake_slug(raw) == kept


@pytest.mark.parametrize("device", sorted(DEVICES))
async def test_every_kind_of_satellite_reports_its_word(device, paired_inventory):
    hello = DEVICES[device]["hello"]
    state = _state()
    await _hello(hello, state)
    assert state.satellite_wake_word == {hello["room_id"]: hello["wake_word"]}


async def test_a_pushed_word_replaces_the_old_and_survives_a_disconnect(paired_inventory):
    state = _state()
    hello = dict(DEVICES["pi_xvf3800"]["hello"])
    await _hello(hello, state)
    await _hello(dict(hello, wake_word="hey_domovoi"), state)  # back after a push and a restart
    assert state.satellite_wake_word[hello["room_id"]] == "hey_domovoi"
    # A hello with nothing usable keeps the last known word.
    await _hello(dict(hello, wake_word=None), state)
    assert state.satellite_wake_word[hello["room_id"]] == "hey_domovoi"


async def test_the_snapshot_carries_the_words(monkeypatch):
    from domovoi import main as core_main

    async def sha():
        return "abc1234"

    monkeypatch.setattr(core_main.git_version, "cached_current_sha", sha)
    state = core_main.app.state
    had = getattr(state, "satellite_wake_word", None)
    try:
        state.satellite_wake_word = {"kitchen": "hey_jarvis", "office": "hey_domovoi"}
        for name in ("active_sessions", "resumable_music", "wifi_status", "current_playlist",
                     "active_dropins", "satellite_full_duplex", "satellite_display", "satellite_voice",
                     "satellite_volume", "satellite_synced_sha", "satellite_sat_type",
                     "satellite_mic_enabled", "satellite_upgrades", "satellite_health",
                     "satellite_last_outage"):
            if not hasattr(state, name):
                setattr(state, name, {})
        snap = await core_main.admin_snapshot()
    finally:
        if had is None:
            del state.satellite_wake_word
        else:
            state.satellite_wake_word = had
    assert snap["satellite_wake_word"] == {"kitchen": "hey_jarvis", "office": "hey_domovoi"}


async def test_the_dashboard_row_names_the_word_and_a_trained_word_s_phrase(monkeypatch):
    from web.backend.api import satellites as web_sats

    async def no_np(*a, **kw):
        return None

    monkeypatch.setattr(web_sats, "_now_playing_for", no_np)
    snap = {"active_rooms": ["kitchen"],
            "satellite_wake_word": {"kitchen": "hey_domovoi", "garage": "hey_jarvis"}}
    phrases = {"hey_domovoi": "hey domovoi"}
    rows = {r: {"room_id": r, "control_port": None, "http_port": None, "last_connected_at": None}
            for r in ("kitchen", "garage", "attic")}
    kitchen = await web_sats._satellite_for(rows["kitchen"], snap, {}, {}, {}, wake_phrases=phrases)
    assert (kitchen.wake_word, kitchen.wake_phrase) == ("hey_domovoi", "hey domovoi")
    garage = await web_sats._satellite_for(rows["garage"], snap, {}, {}, {}, wake_phrases=phrases)
    assert (garage.status, garage.wake_word, garage.wake_phrase) == ("waiting", "hey_jarvis", None)
    attic = await web_sats._satellite_for(rows["attic"], snap, {}, {}, {})
    assert (attic.wake_word, attic.wake_phrase) == (None, None)
