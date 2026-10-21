"""Lookup sources: where an answer's passages come from (SDK 1.8).

A lookup source is anything that can be searched for a few short passages
and then read in full: Domovoi's own manual (``domovoi/help_desk``), a
plugin's offline library (Kiwix), a public API (Wikipedia through
SearXNG), the open web. The plan this follows is part 5 of the assistant
plan (2026-10-20): every source registers the same two calls and says
which CLASS it is, and the core, not the source, decides the order.

* :meth:`LookupSource.search` returns at most ``limit`` :class:`LookupHit`
  (a title and a short snippet, best first);
* :meth:`LookupSource.fetch` returns one :class:`LookupDocument` (the
  passage's whole text) or None;
* ``lookup_class`` is one of :data:`LOCAL` (on this server, no internet:
  the manual, an offline library), :data:`API` (a public API with real
  terms: Wikipedia, Wikidata) or :data:`WEB` (scraped engines, a
  household's own search key).

**The order is the core's.** :func:`ordered_sources` sorts by class (local,
then API, then web), then by the slug the source was registered under. A
plugin can't move itself up: any ``priority`` / ``order`` attribute on the
object is ignored, and a source that names an unknown class is refused at
registration (:func:`validate_source`).

**Local sources skip the internet question** (:func:`needs_permission`):
they never leave the box, so they work under internet "never" and with no
permission card. The others are for the lookup flow to ask about first.

Registration goes through the capability registry under
:data:`LOOKUP_SOURCE` (``ctx.add_lookup_source(source)`` from a plugin's
``register``; core sources under the slug "core"), so a plugin's disable
tears its source down with the rest of its registrations.

Text a source returns is DATA, never instructions: whoever hands passages
to a model marks them as such (``domovoi/help_desk/answer.py`` does).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

#: The capability name every lookup source is registered under.
LOOKUP_SOURCE = "lookup-source"

LOCAL = "local"
API = "api"
WEB = "web"
#: The classes, in the order the core asks them.
CLASS_ORDER: tuple[str, ...] = (LOCAL, API, WEB)

#: How long one source's search may take before the others go on without it.
SEARCH_TIMEOUT_SEC = 8.0
#: Hits one source may return, whatever it was asked for.
MAX_HITS_PER_SOURCE = 10
_TITLE_MAX = 200
_SNIPPET_MAX = 600


@dataclass(frozen=True)
class LookupHit:
    """One search result: what to show and what to ``fetch`` for the rest.

    ``id`` is opaque to the core (the source's own key). ``url`` is where a
    person can read it (a dashboard route like ``#manual/hears``, or a web
    address). ``section`` names where it sits ("Rooms and devices →
    Wi-Fi"), for "from the manual's …" lines. ``score`` is the source's own
    relevance, only comparable within that source."""

    id: str
    title: str
    snippet: str = ""
    url: str | None = None
    section: str | None = None
    score: float = 0.0


@dataclass(frozen=True)
class LookupDocument:
    """A hit's whole text, as :meth:`LookupSource.fetch` returns it."""

    id: str
    title: str
    text: str
    url: str | None = None
    section: str | None = None


@runtime_checkable
class LookupSource(Protocol):
    """What a plugin (or the core) registers with ``add_lookup_source``."""

    #: A short stable id ("domovoi-help", "kiwix").
    slug: str
    #: What a person reads under a reply ("Domovoi help", "your offline library").
    name: str
    #: :data:`LOCAL`, :data:`API` or :data:`WEB`.
    lookup_class: str

    async def search(self, query: str, *, limit: int = 3) -> list[LookupHit]:
        ...

    async def fetch(self, id: str) -> LookupDocument | None:
        ...


def validate_source(source: Any) -> None:
    """Raise ``TypeError`` / ``ValueError`` unless ``source`` keeps the
    contract: the two async calls, a slug, a name and a known class."""
    for attr in ("slug", "name", "lookup_class"):
        value = getattr(source, attr, None)
        if not isinstance(value, str) or not value.strip():
            raise TypeError(f"a lookup source needs a non-empty string {attr!r}")
    if source.lookup_class not in CLASS_ORDER:
        raise ValueError(
            f"lookup_class {source.lookup_class!r} must be one of {list(CLASS_ORDER)}"
        )
    for meth in ("search", "fetch"):
        fn = getattr(source, meth, None)
        if not callable(fn) or not asyncio.iscoroutinefunction(fn):
            raise TypeError(f"a lookup source's {meth}() must be an async method")


def needs_permission(source: Any) -> bool:
    """Whether asking ``source`` leaves the box (and so asks first). Local
    sources never do: no permission card, and they work under "never"."""
    return getattr(source, "lookup_class", None) != LOCAL


def _registry(registry: Any | None) -> Any:
    if registry is not None:
        return registry
    from domovoi.capabilities import CAPABILITIES

    return CAPABILITIES


def ordered_sources(registry: Any | None = None, *, classes: tuple[str, ...] | None = None) -> list[Any]:
    """Every registered source, in the core's order: class (local, API,
    web), then registration slug. Nothing on the source object changes it.
    ``classes`` keeps only those (``(LOCAL,)`` under internet "never")."""
    reg = _registry(registry)
    pairs: list[tuple[int, str, Any]] = []
    # Both sorted by slug, so they pair up.
    for slug, source in zip(reg.providers_for(LOOKUP_SOURCE), reg.get_all(LOOKUP_SOURCE)):
        cls = getattr(source, "lookup_class", None)
        if source is None or cls not in CLASS_ORDER:
            continue
        if classes is not None and cls not in classes:
            continue
        pairs.append((CLASS_ORDER.index(cls), slug, source))
    pairs.sort(key=lambda p: (p[0], p[1]))
    return [p[2] for p in pairs]


def _clean_hit(hit: Any) -> LookupHit | None:
    if not isinstance(hit, LookupHit):
        return None
    title = str(hit.title or "").strip()[:_TITLE_MAX]
    if not title or not str(hit.id or "").strip():
        return None
    return LookupHit(
        id=str(hit.id)[:200],
        title=title,
        snippet=str(hit.snippet or "")[:_SNIPPET_MAX],
        url=(str(hit.url)[:500] if hit.url else None),
        section=(str(hit.section)[:_TITLE_MAX] if hit.section else None),
        score=float(hit.score or 0.0),
    )


async def search_sources(
    query: str,
    *,
    limit: int = 3,
    classes: tuple[str, ...] | None = None,
    registry: Any | None = None,
    timeout: float = SEARCH_TIMEOUT_SEC,
) -> list[tuple[Any, list[LookupHit]]]:
    """Ask each source in order; ``[(source, hits)]`` for those that found
    something. A source that raises, times out or returns junk is skipped
    with a log line: one broken plugin never costs the others' answers."""
    out: list[tuple[Any, list[LookupHit]]] = []
    for source in ordered_sources(registry, classes=classes):
        try:
            raw = await asyncio.wait_for(source.search(query, limit=limit), timeout)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a source's failure is its own
            log.warning("lookup source %s failed: %s: %s", getattr(source, "slug", "?"),
                        type(e).__name__, str(e)[:200])
            continue
        hits = [h for h in (_clean_hit(x) for x in (raw or [])[:MAX_HITS_PER_SOURCE]) if h]
        if hits:
            out.append((source, hits[:limit]))
    return out


__all__ = [
    "API",
    "CLASS_ORDER",
    "LOCAL",
    "LOOKUP_SOURCE",
    "LookupDocument",
    "LookupHit",
    "LookupSource",
    "WEB",
    "needs_permission",
    "ordered_sources",
    "search_sources",
    "validate_source",
]
