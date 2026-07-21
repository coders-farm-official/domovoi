"""Testing doubles for plugin authors (design §13.1, first slice).

``make_stub_sdk()`` returns a behavior-shaped, deterministic
:class:`~domovoi.sdk.facade.PluginSDK` look-alike that needs **no DB
and no core app**: a fresh in-memory event bus and now-playing
registry, plus recording ``playback`` / ``acquisition`` stubs. Handler
and worker-``tick()`` unit tests run against this.

The fuller harness (``plugin_harness`` with ``plugin_db`` /
``loaded_plugin`` / ``web_harness`` fixtures) ships with the plugin
loader stage — this module is the stable import point it will extend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from domovoi.capabilities import CapabilityRegistry
from domovoi.events import EventBus
from domovoi.models import Response
from domovoi.now_playing import NowPlayingRegistry
from domovoi.sdk.coreconfig import CoreConfigView
from domovoi.sdk.facade import EventsView, NowPlayingView
from domovoi.sdk.http import HttpFactory
from domovoi.sdk.observability import get_logger


@dataclass
class RecordingPlayback:
    """Records play/stop calls; returns fully-populated Responses."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def play_url(self, room_id: str, stream_url: str, **kwargs: Any) -> Response:
        self.calls.append(
            {"action": "play_url", "room_id": room_id,
             "stream_url": stream_url, **kwargs}
        )
        return Response(
            text=f"Playing {kwargs.get('title', 'stream')}.",
            music_action="start",
            music_stream_url=f"http://stub-mpd/{room_id}",
        )

    async def stop(self, room_id: str) -> Response:
        self.calls.append({"action": "stop", "room_id": room_id})
        return Response(text="Stopped.", music_action="stop")

    def mpd_stream_url_for(self, room_id: str) -> str:
        return f"http://stub-mpd/{room_id}"

    async def update_library_all_rooms(self) -> None:
        self.calls.append({"action": "update_library_all_rooms"})


@dataclass
class RecordingAcquisition:
    """In-memory queue shape: enqueue records; claim drains FIFO."""

    slug: str = "testplugin"
    enqueued: list[dict[str, Any]] = field(default_factory=list)
    completed: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    fulfiller_kinds: set[str] | None = None
    url_matcher: Callable[[str], bool] | None = None

    def register_fulfiller(self, *, kinds: set[str], url_matcher=None) -> None:
        self.fulfiller_kinds = set(kinds)
        self.url_matcher = url_matcher

    async def enqueue(self, session: Any = None, **kwargs: Any) -> Any:
        kwargs.setdefault("requested_by", f"plugin:{self.slug}")
        entry = {"id": len(self.enqueued) + 1, "status": "pending", **kwargs}
        self.enqueued.append(entry)

        class _Result:
            outcome = "enqueued"
            user_message = "Queued — I'll fetch it shortly."
            acquisition = entry
        return _Result()

    async def claim_next(self, session: Any = None) -> dict[str, Any] | None:
        for entry in self.enqueued:
            if entry["status"] == "pending":
                entry["status"] = "claimed"
                return entry
        return None

    async def complete(self, session: Any, acq_id: int, **kwargs: Any) -> None:
        self.completed.append({"id": acq_id, **kwargs})

    async def fail(self, session: Any, acq_id: int, **kwargs: Any) -> None:
        self.failed.append({"id": acq_id, **kwargs})

    def friendly_absence_message(self, kind: str) -> str:
        return "I've noted that down, but no media provider is installed to fetch it."


@dataclass
class RecordingSessions:
    """In-memory namespaced session context + confirmation recorder."""

    slug: str = "testplugin"
    context: dict[str, dict[str, Any]] = field(default_factory=dict)
    confirmations: list[dict[str, Any]] = field(default_factory=list)

    async def get_key(self, session: Any, room: Any, key: str, default: Any = None):
        return self.context.get(str(room), {}).get(key, default)

    async def set_key(self, session: Any, room: Any, key: str, value: Any) -> None:
        self.context.setdefault(str(room), {})[key] = value

    async def clear_namespace(self, session: Any, room: Any) -> None:
        self.context.pop(str(room), None)

    async def request_confirmation(self, session: Any, room: Any, **kwargs: Any) -> None:
        kind = kwargs.get("kind", "")
        if not kind.startswith(f"{self.slug}."):
            raise ValueError(f"kind {kind!r} must be namespaced '{self.slug}.<kind>'")
        self.confirmations.append({"room": room, **kwargs})


class StubConnectivity:
    def __init__(self) -> None:
        self.online = True



@dataclass
class RecordingSpeech:
    """Records sdk.speech.announce calls; every announce "reaches" its
    target so plugin logic downstream of delivery is testable."""

    calls: list = field(default_factory=list)

    async def announce(self, room_id, text):
        self.calls.append({"room_id": room_id, "text": text})
        return [room_id] if room_id is not None else ["stub-room"]


class StubSDK:
    """Duck-typed PluginSDK double (see module docstring)."""

    def __init__(self, slug: str, data_dir: Path | None = None) -> None:
        self.slug = slug
        self.version = "0.0.0-test"
        self.log = get_logger(slug)
        self.data_dir = data_dir or Path(".") / f".stub-sdk-{slug}"
        self.config = None
        self.core_config = CoreConfigView()
        self.events = EventsView(slug, EventBus())          # private bus
        self.acquisition = RecordingAcquisition(slug=slug)
        self._now_playing_registry = NowPlayingRegistry()
        self.now_playing = NowPlayingView(slug)
        # Rebind the view onto the private registry so tests are isolated
        # from the process singleton.
        self.now_playing.register_source = (
            lambda s: self._now_playing_registry.register_source(s, owner=slug)
        )
        self.now_playing.register_matcher = (
            lambda s, fn: self._now_playing_registry.register_matcher(
                s, fn, owner=slug
            )
        )
        self.now_playing.stamp = self._now_playing_registry.stamp
        self.now_playing.get = self._now_playing_registry.get
        self.now_playing.clear = self._now_playing_registry.clear
        self.sessions = RecordingSessions(slug=slug)
        self.connectivity = StubConnectivity()
        self.capabilities = CapabilityRegistry()            # private registry
        self.playback = RecordingPlayback()
        self.library = None   # DB-backed; use the plugin_db tier for these
        self.realtime = None  # DB-backed (commit-coupled NOTIFY)
        self.state: dict[str, Any] = {}
        self.speech = RecordingSpeech()
        self.http = HttpFactory(self.version)


def make_stub_sdk(slug: str = "testplugin", **kwargs: Any) -> StubSDK:
    return StubSDK(slug, **kwargs)
