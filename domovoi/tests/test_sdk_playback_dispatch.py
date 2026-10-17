"""sdk.playback.dispatch: a plugin's play or stop that did not come from a
voice turn reaches the room's satellite (plugin board F-P017).

The sleep plugin's "play brown noise in the bedroom" button and the media
provider's "stream this result to a room" route call ``play_url`` from an
HTTP route. ``play_url`` queues the room's MPD paused and leaves the
``music_start`` → ``music_ready`` handshake to whoever delivers the
Response; a voice turn's socket does that, an HTTP route had nothing, so
the room stayed silent while the page counted down. ``dispatch`` hands the
Response to the same delivery the dashboard's own room controls use
(``domovoi.room_music.dispatch_music``).

No database, no core boot: a plain app-state stand-in and a fake session.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from domovoi.models import Response
from domovoi.now_playing import NOW_PLAYING
from domovoi.plugins_runtime.loader import LOADER
from domovoi.sdk.playback import PlaybackAPI
from domovoi.sdk.testing import RecordingPlayback


class _Satellite:
    """A connected room: records what the room is sent."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.frames: list[dict[str, Any]] = []

    async def start_music(self, url: str) -> bool:
        self.started.append(url)
        return True

    async def _safe_send_text(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


@pytest.fixture
def core_app(monkeypatch):
    """The core app as the lifespan leaves it, bound to the plugin loader."""
    app = SimpleNamespace(state=SimpleNamespace(
        active_sessions={},
        resumable_music={},
        current_playlist={},
        pending_music_start={},
        music_paused_by_person=set(),
    ))
    monkeypatch.setattr(LOADER, "_app", app)
    return app


def _start(room: str = "bedroom") -> Response:
    return Response(text="Playing Brown noise.", music_action="start",
                    music_stream_url=f"http://mpd/{room}.mp3")


async def test_a_play_from_a_route_sends_the_room_its_music_start(core_app) -> None:
    sat = _Satellite()
    core_app.state.active_sessions["bedroom"] = sat

    sent = await PlaybackAPI("sleep").dispatch(_start(), "bedroom")

    assert sent is True
    assert sat.started == ["http://mpd/bedroom.mp3"]
    # A later reconnect auto-resumes from here, as for any room control.
    assert core_app.state.resumable_music["bedroom"] == "http://mpd/bedroom.mp3"


async def test_a_play_with_no_satellite_connected_resumes_the_player(core_app, monkeypatch) -> None:
    import domovoi.streaming as streaming

    resumed: list[str] = []

    async def _resume(room_id: str) -> None:
        resumed.append(room_id)

    monkeypatch.setattr(streaming, "_resume_mpd_for_room", _resume)

    assert await PlaybackAPI("sleep").dispatch(_start("den"), "den") is True
    assert resumed == ["den"], "MPD was queued paused; with nobody to say music_ready it must not sit paused"


async def test_a_stop_from_a_worker_sends_music_stop_and_clears_the_room(core_app, monkeypatch) -> None:
    sat = _Satellite()
    core_app.state.active_sessions["bedroom"] = sat
    core_app.state.resumable_music["bedroom"] = "http://mpd/bedroom.mp3"
    monkeypatch.setitem(NOW_PLAYING._sources, "dispatch-test", "sleep")
    NOW_PLAYING.stamp("bedroom", "dispatch-test", {"title": "Brown noise"})

    sent = await PlaybackAPI("sleep").dispatch(Response(text="Stopped.", music_action="stop"), "bedroom")

    assert sent is True
    assert sat.frames == [{"type": "music_stop"}]
    assert "bedroom" not in core_app.state.resumable_music
    assert NOW_PLAYING.get("bedroom") is None


@pytest.mark.parametrize("response", [
    Response(text="I'm set to stay off the internet, so I can't play that.",
             data={"status": "internet_off"}),
    Response(text="I found it, but the music player wouldn't accept the stream."),
])
async def test_a_refused_play_sends_nothing(core_app, response) -> None:
    sat = _Satellite()
    core_app.state.active_sessions["bedroom"] = sat

    assert await PlaybackAPI("sleep").dispatch(response, "bedroom") is False
    assert sat.started == [] and sat.frames == []


async def test_without_a_core_app_it_sends_nothing(monkeypatch) -> None:
    monkeypatch.setattr(LOADER, "_app", None)
    assert await PlaybackAPI("sleep").dispatch(_start(), "bedroom") is False


def test_every_plugin_facade_has_it() -> None:
    from domovoi.sdk.facade import PluginSDK

    assert callable(getattr(PluginSDK(slug="sleep").playback, "dispatch", None))


async def test_the_recording_double_records_it() -> None:
    rec = RecordingPlayback()
    resp = await rec.play_url("bedroom", "http://x/s.mp3", title="Rain")

    assert await rec.dispatch(resp, "bedroom") is True
    assert await rec.dispatch(Response(text="no"), "bedroom") is False
    assert rec.calls[-2:] == [
        {"action": "dispatch", "room_id": "bedroom", "music_action": "start"},
        {"action": "dispatch", "room_id": "bedroom", "music_action": None},
    ]


def test_the_sdk_version_says_so() -> None:
    from domovoi import sdk

    assert tuple(int(p) for p in sdk.API_VERSION.split(".")) >= (1, 6, 0)
