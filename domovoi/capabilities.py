"""Capability registry — the seam between core and media-provider plugins.

Core code never imports provider code. Instead, providers register
implementations of the Protocol interfaces below under well-known
capability slugs, and core call sites ``resolve()`` at use time and
degrade gracefully when nothing is registered (design §4.10, §10.2).

The two seams core consumes today:

* ``streaming-search-provider`` — MusicHandler's local-miss cascade and
  smart-skip. Absent ⇒ "no streaming provider is installed" voice copy.
* ``media-acquisition-fulfiller`` — informational presence check for the
  acquisition queue's voice copy (the queue itself always accepts rows;
  a fulfiller drains them — design §4.8 graceful absence).

The plugin runtime (loader / PluginContext.add_capability, stage C3)
records registrations against the owning plugin slug so disable/uninstall
is a clean teardown; core-owned registrations use slug "core".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from domovoi.models import Response

log = logging.getLogger(__name__)

# ─── Well-known capability slugs (design §12 — open namespace) ────────────
STREAMING_SEARCH_PROVIDER = "streaming-search-provider"
MEDIA_ACQUISITION_FULFILLER = "media-acquisition-fulfiller"
NOW_PLAYING_MATCHER = "now-playing-matcher"

# Canonical graceful-absence voice/UI copy (design §4.8, locked 6).
ACQUISITION_ABSENCE_MESSAGE = (
    "I've noted that down, but no media provider is installed to fetch it."
)
STREAMING_ABSENCE_SUFFIX = "and no streaming provider is installed."


@dataclass(frozen=True)
class MediaCandidate:
    """One external search result a streaming provider can play
    (design §10.2). ``id`` is provider-namespaced and opaque to core."""

    id: str
    title: str
    artist_hint: str | None = None
    duration_sec: int | None = None
    source_url: str | None = None


@runtime_checkable
class StreamingSearchProvider(Protocol):
    """Search external media and start playback in a room without
    (necessarily) downloading. Consumed by core MusicHandler's
    local-miss cascade and smart-skip (design §10.2)."""

    slug: str

    async def search(self, query: str, *, limit: int = 5) -> list[MediaCandidate]:
        ...

    async def stream(
        self,
        room_id: str,
        candidate: MediaCandidate | None = None,
        *,
        query: str | None = None,
    ) -> Response:
        """Resolve a fresh stream URL and start playback itself
        (via sdk.playback.play_url once the SDK lands — C3)."""
        ...

    def likely_same(self, title_a: str, title_b: str) -> bool:
        """Smart-skip similarity — True when two result titles are
        probably the same recording (was the provider's private
        title-comparison heuristic)."""
        ...


@runtime_checkable
class AcquisitionFulfiller(Protocol):
    """A registered provider that claims and completes media
    acquisitions (design §4.8). Core only needs presence + slug for
    voice copy; the claim/complete cycle runs through the acquisition
    service (C3)."""

    slug: str


class CapabilityRegistry:
    """Name → ordered providers. ``resolve()`` returns the single
    preferred implementation or None (absence is a supported state,
    never an error). Determinism (design §4.10): an explicit
    ``prefer()`` choice wins, otherwise ascending registration slug.
    """

    def __init__(self) -> None:
        self._providers: dict[str, dict[str, object]] = {}
        self._preferences: dict[str, str] = {}

    def register(self, name: str, impl: object, *, slug: str = "core") -> None:
        by_slug = self._providers.setdefault(name, {})
        if slug in by_slug:
            log.warning("capability %r re-registered by %r (replacing)", name, slug)
        by_slug[slug] = impl

    def unregister(self, name: str, *, slug: str) -> None:
        by_slug = self._providers.get(name)
        if by_slug is not None:
            by_slug.pop(slug, None)

    def unregister_all_for(self, slug: str) -> None:
        """Teardown hook for plugin disable/uninstall (C3)."""
        for by_slug in self._providers.values():
            by_slug.pop(slug, None)

    def prefer(self, name: str, slug: str) -> None:
        """Pin which provider ``resolve`` returns when several are
        registered (the CAPABILITY_PREFERENCES knob, design §4.10)."""
        self._preferences[name] = slug

    def resolve(self, name: str) -> Any | None:
        by_slug = self._providers.get(name) or {}
        if not by_slug:
            return None
        preferred = self._preferences.get(name)
        if preferred is not None and preferred in by_slug:
            return by_slug[preferred]
        return by_slug[min(by_slug)]  # ascending slug — deterministic

    def get_all(self, name: str) -> list[Any]:
        by_slug = self._providers.get(name) or {}
        return [by_slug[s] for s in sorted(by_slug)]

    def absent(self, name: str) -> bool:
        return not (self._providers.get(name) or {})

    # ── Introspection (GET /v1/capabilities, design §12) ───────────────
    def names(self) -> list[str]:
        """Capability names with at least one live provider, sorted."""
        return sorted(n for n, by_slug in self._providers.items() if by_slug)

    def providers_for(self, name: str) -> list[str]:
        """Provider slugs registered under ``name``, sorted."""
        return sorted((self._providers.get(name) or {}).keys())


# The process-wide singleton (single-process invariant — dossier §7 inv. 14).
CAPABILITIES = CapabilityRegistry()
