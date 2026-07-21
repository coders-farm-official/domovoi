"""The PluginSDK facade (design §4.10) — one injected object per plugin.

``PluginSDK`` instances are built by :func:`build_sdk` (the plugin
loader will call it when the runtime lands; tests call it directly, or
use :func:`domovoi.sdk.testing.make_stub_sdk` for a no-DB double).
Plugin authors only ever *type* against ``PluginSDK`` — they never
construct one.

Every namespaced view here records registrations against the owning
slug, which is what makes plugin disable/uninstall a clean teardown
(:meth:`PluginSDK.teardown`).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, MutableMapping

from sqlalchemy import text as _sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import registered_values
from domovoi.acquisitions import (
    ACQUISITIONS,
    Acquisition,
    AcquisitionService,
    EnqueueResult,
)
from domovoi.capabilities import CAPABILITIES, CapabilityRegistry
from domovoi.connectivity import current_probe
from domovoi.db.session import session_scope
from domovoi.events import EVENTS, EventBus, EventCallback, Subscription
from domovoi.now_playing import NOW_PLAYING, NowPlayingStamp
from domovoi.sdk.assets import AssetAPI
from domovoi.sdk.coreconfig import CoreConfigView
from domovoi.sdk.http import HttpFactory
from domovoi.sdk.library import LibraryAPI
from domovoi.sdk.observability import get_logger
from domovoi.sdk.playback import PlaybackAPI
from domovoi.sdk.realtime import RealtimeAPI
from domovoi.sdk.sessions import SessionAPI
from domovoi.sdk.speech import SpeechAPI

log = logging.getLogger(__name__)

# Per-slug app.state-style stores (single-process; not thread-safe by
# design — dossier §7 inv. 14). Module-level so re-builds of a facade
# for the same slug (enable → disable → enable) see the same store
# until teardown clears it.
_STATE_STORES: dict[str, dict[str, Any]] = {}


class EventsView:
    """Slug-scoped view of the core bus: subscriptions carry the slug as
    teardown owner; emits are force-prefixed ``plugin.<slug>.``."""

    def __init__(self, slug: str, bus: EventBus) -> None:
        self.slug = slug
        self._bus = bus

    def subscribe(self, event: str, cb: EventCallback) -> Subscription:
        return self._bus.subscribe(event, cb, owner=self.slug)

    def unsubscribe(self, sub: Subscription) -> None:
        self._bus.unsubscribe(sub)

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> int:
        prefix = f"plugin.{self.slug}."
        if not event.startswith(prefix):
            event = prefix + event
        return self._bus.emit(event, payload)


class NowPlayingView:
    """Slug-scoped now-playing registry (§4.7): registrations are owned
    by the slug; stamp/get/clear pass through."""

    def __init__(self, slug: str) -> None:
        self.slug = slug

    def register_source(self, source_slug: str) -> None:
        NOW_PLAYING.register_source(source_slug, owner=self.slug)

    def register_matcher(self, source_slug: str, fn: Callable[..., Any]) -> None:
        NOW_PLAYING.register_matcher(source_slug, fn, owner=self.slug)

    def stamp(self, room_id: str, source: str, data: dict[str, Any]) -> NowPlayingStamp:
        return NOW_PLAYING.stamp(room_id, source, data)

    def get(self, room_id: str) -> NowPlayingStamp | None:
        return NOW_PLAYING.get(room_id)

    def clear(self, room_id: str, source: str | None = None) -> bool:
        return NOW_PLAYING.clear(room_id, source=source)


class AcquisitionView:
    """Slug-scoped acquisition API (§4.8): ``register_fulfiller`` and
    ``claim_next`` are bound to the plugin's slug; ``enqueue`` defaults
    ``requested_by`` to ``plugin:<slug>``."""

    def __init__(self, slug: str, service: AcquisitionService) -> None:
        self.slug = slug
        self._service = service

    def register_fulfiller(
        self,
        *,
        kinds: set[str],
        url_matcher: Callable[[str], bool] | None = None,
    ) -> None:
        self._service.register_fulfiller(
            self.slug, kinds=kinds, url_matcher=url_matcher
        )

    async def enqueue(self, session: AsyncSession, **kwargs: Any) -> EnqueueResult:
        kwargs.setdefault("requested_by", f"plugin:{self.slug}")
        return await self._service.enqueue(session, **kwargs)

    async def claim_next(self, session: AsyncSession) -> Acquisition | None:
        return await self._service.claim_next(session, slug=self.slug)

    async def complete(self, session: AsyncSession, acq_id: int, **kwargs: Any) -> None:
        await self._service.complete(session, acq_id, **kwargs)

    async def fail(self, session: AsyncSession, acq_id: int, **kwargs: Any) -> None:
        await self._service.fail(session, acq_id, **kwargs)

    async def completed_for_origin(
        self, session: AsyncSession, **kwargs: Any
    ) -> list[Acquisition]:
        return await self._service.completed_for_origin(session, **kwargs)

    def availability(self):
        return self._service.availability()

    def friendly_absence_message(self, kind: str) -> str:
        return self._service.friendly_absence_message(kind)


class ConnectivityView:
    """Read view of the ConnectivityProbe (§4.10). With no live probe
    (unit contexts) it reports online — matching the probe's own
    optimistic pre-first-check default."""

    @property
    def online(self) -> bool:
        probe = current_probe()
        return True if probe is None else bool(probe.online)


class PluginDB:
    """Plugin-schema-scoped DB access. Sessions come from the same
    NullPool engine core uses (dossier §7 inv. 7) with
    ``search_path = plugin_<slug>, public`` preset — plugin SQL is
    unqualified against its own tables; core tables go through the SDK
    APIs, not raw SQL (convention + review, not enforcement)."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.schema = f"plugin_{slug}"

    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        async with session_scope() as s:
            # SET LOCAL scopes the search_path to this transaction.
            await s.execute(
                _sql_text(f'SET LOCAL search_path = "{self.schema}", public')
            )
            yield s


class PluginSDK:
    """See design §4.10 for the field-by-field contract."""

    def __init__(
        self,
        *,
        slug: str,
        version: str = "0.0.0",
        config: Any = None,
        data_dir: Path | None = None,
    ) -> None:
        self.slug = slug
        self.version = version
        self.log = get_logger(slug)
        self.data_dir = data_dir or (
            Path.home() / ".domovoi" / "plugins" / "data" / slug
        )
        self.config = config                     # plugin's own BaseSettings
        self.core_config = CoreConfigView()

        self.db = PluginDB(slug)
        self.events = EventsView(slug, EVENTS)
        self.acquisition = AcquisitionView(slug, ACQUISITIONS)
        self.now_playing = NowPlayingView(slug)
        self.sessions = SessionAPI(slug)
        self.connectivity = ConnectivityView()
        self.capabilities: CapabilityRegistry = CAPABILITIES
        self.playback = PlaybackAPI(slug)
        self.speech = SpeechAPI(slug)
        self.library = LibraryAPI(slug)
        self.realtime = RealtimeAPI(slug)
        self.assets = AssetAPI(slug)
        self.state: MutableMapping[str, Any] = _STATE_STORES.setdefault(slug, {})
        self.http = HttpFactory(version)

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    def teardown(self) -> None:
        """Disable/uninstall teardown for everything the slug registered
        through this facade: capabilities (incl. fulfillers), event
        subscriptions, now-playing sources + stamps, open-enum values,
        canned sounds, and the state store. Session-namespace cleanup is
        the caller's job (it needs a DB session — see
        ``SessionAPI.clear_namespace_everywhere``)."""
        CAPABILITIES.unregister_all_for(self.slug)
        EVENTS.unsubscribe_owner(self.slug)
        NOW_PLAYING.unregister_owner(self.slug)
        registered_values.unregister_owner(self.slug)
        self.assets.remove_canned_sounds()
        _STATE_STORES.pop(self.slug, None)


def build_sdk(
    slug: str,
    *,
    version: str = "0.0.0",
    config: Any = None,
    data_dir: Path | None = None,
) -> PluginSDK:
    """Construct a live facade wired to the process singletons. The
    plugin loader (later stage) is the production caller; integration
    tests use it directly."""
    return PluginSDK(slug=slug, version=version, config=config, data_dir=data_dir)
