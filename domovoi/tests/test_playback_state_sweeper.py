"""Core playback-state sweeper (design §4.7 split): stale now-playing
stamps + current_playlist pruning against MPD reality, and the
natural-end music_stop push to the Pi (with the seen-playing guard)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from domovoi.now_playing import NOW_PLAYING
from domovoi.workers import playback_state_sweeper as sweeper_mod
from domovoi.workers.playback_state_sweeper import PlaybackStateSweeper

pytestmark = pytest.mark.asyncio


class _FakeMPD:
    def __init__(self, state: str = "play", file: str | None = None) -> None:
        self._state = state
        self._file = file

    async def state(self) -> str:
        return self._state

    async def current_song(self) -> dict[str, Any] | None:
        return {"file": self._file} if self._file else {}


class _FakeSession:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def _safe_send_text(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def _app(**state: Any) -> Any:
    defaults: dict[str, Any] = {
        "resumable_music": {},
        "current_playlist": {},
        "active_sessions": {},
    }
    defaults.update(state)
    return SimpleNamespace(state=SimpleNamespace(**defaults))


@pytest.fixture(autouse=True)
def _clean_registry():
    NOW_PLAYING.register_source("providerx", owner="test_sweeper")
    yield
    NOW_PLAYING.unregister_owner("test_sweeper")


def _patch_mpd(monkeypatch: pytest.MonkeyPatch, clients: dict[str, _FakeMPD]) -> None:
    monkeypatch.setattr(
        sweeper_mod, "get_mpd_client_for", lambda room: clients[room]
    )


async def test_stale_stamp_pruned_when_mpd_stopped(monkeypatch) -> None:
    app = _app()
    NOW_PLAYING.stamp("kitchen", "providerx", {"stream_url": "http://a"})
    _patch_mpd(monkeypatch, {"kitchen": _FakeMPD(state="stop")})

    pruned = await PlaybackStateSweeper(app).tick()
    assert pruned == 1
    assert NOW_PLAYING.get("kitchen") is None


async def test_stale_stamp_pruned_when_file_drifted(monkeypatch) -> None:
    """MPD playing a DIFFERENT file (a library track took over the room)
    means the stamp no longer reflects reality."""
    app = _app()
    NOW_PLAYING.stamp("kitchen", "providerx", {"stream_url": "http://a"})
    _patch_mpd(
        monkeypatch, {"kitchen": _FakeMPD(state="play", file="Radiohead/Creep.mp3")}
    )
    pruned = await PlaybackStateSweeper(app).tick()
    assert pruned == 1
    assert NOW_PLAYING.get("kitchen") is None


async def test_fresh_stamp_survives(monkeypatch) -> None:
    app = _app()
    NOW_PLAYING.stamp("kitchen", "providerx", {"stream_url": "http://a"})
    _patch_mpd(monkeypatch, {"kitchen": _FakeMPD(state="play", file="http://a")})
    pruned = await PlaybackStateSweeper(app).tick()
    assert pruned == 0
    assert NOW_PLAYING.get("kitchen") is not None


async def test_mpd_probe_failure_leaves_stamp(monkeypatch) -> None:
    """An unreachable MPD must not evict state (probe hiccup ≠ stale)."""
    app = _app()
    NOW_PLAYING.stamp("kitchen", "providerx", {"stream_url": "http://a"})

    def _raise(room: str) -> Any:
        raise RuntimeError("mpd down")

    monkeypatch.setattr(sweeper_mod, "get_mpd_client_for", _raise)
    pruned = await PlaybackStateSweeper(app).tick()
    assert pruned == 0
    assert NOW_PLAYING.get("kitchen") is not None


async def test_playlist_dict_pruned_on_drift(monkeypatch) -> None:
    app = _app(current_playlist={
        "kitchen": {"playlist_id": 1, "last_file_path": "a.mp3"},
        "garage": {"playlist_id": 2, "last_file_path": "b.mp3"},
    })
    _patch_mpd(monkeypatch, {
        "kitchen": _FakeMPD(state="play", file="a.mp3"),   # fresh — stays
        "garage": _FakeMPD(state="play", file="other.mp3"),  # drifted — pruned
    })
    pruned = await PlaybackStateSweeper(app).tick()
    assert pruned == 1
    assert "kitchen" in app.state.current_playlist
    assert "garage" not in app.state.current_playlist


async def test_pi_music_stop_only_after_seen_playing(monkeypatch) -> None:
    """The seen-playing guard: an armed room whose MPD hasn't started
    yet must NOT be stopped; once observed playing and then dry, the
    Pi gets ONE music_stop and all state clears."""
    session = _FakeSession()
    app = _app(
        resumable_music={"kitchen": "http://stream"},
        active_sessions={"kitchen": session},
    )
    NOW_PLAYING.stamp("kitchen", "providerx", {"stream_url": "http://stream"})
    mpd = _FakeMPD(state="stop", file=None)
    _patch_mpd(monkeypatch, {"kitchen": mpd})

    sweeper = PlaybackStateSweeper(app)
    # Sweep 1: armed but never seen playing — left alone (except the
    # stamp, which the stamp sweep prunes on state=stop).
    await sweeper._sweep_pi_music()
    assert session.sent == []
    assert "kitchen" in app.state.resumable_music

    # MPD catches up and plays.
    mpd._state = "play"
    mpd._file = "http://stream"
    await sweeper._sweep_pi_music()
    assert session.sent == []

    # Natural end: MPD runs dry → one music_stop, state cleared.
    mpd._state = "stop"
    stopped = await sweeper._sweep_pi_music()
    assert stopped == 1
    assert session.sent == [{"type": "music_stop"}]
    assert "kitchen" not in app.state.resumable_music
    assert NOW_PLAYING.get("kitchen") is None

    # Idempotent: nothing left to stop.
    assert await sweeper._sweep_pi_music() == 0


async def test_pi_music_no_session_still_clears(monkeypatch) -> None:
    """Pi dropped mid-song: state clears without a send and without
    endless retries."""
    app = _app(resumable_music={"kitchen": "http://stream"})
    mpd = _FakeMPD(state="play", file="http://stream")
    _patch_mpd(monkeypatch, {"kitchen": mpd})
    sweeper = PlaybackStateSweeper(app)
    await sweeper._sweep_pi_music()          # observe playing
    mpd._state = "stop"
    stopped = await sweeper._sweep_pi_music()
    assert stopped == 0                       # nothing to notify
    assert "kitchen" not in app.state.resumable_music
