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

**The order and the class are the core's.** A source is registered wrapped
in a :class:`RegisteredSource` that fixes its class when it is registered
(:func:`register`), so a class that changes later changes nothing. The
class is the core's decision, not the plugin's claim: a core source keeps
its own; a plugin may be "local" only when the core allowlists it
(:data:`LOCAL_PLUGINS`: plugins whose search never leaves the box), and
any other plugin claiming "local" is treated as "web" (it asks first).
:func:`ordered_sources` sorts by class (local, then API, then web), then
core sources before plugins', then registration slug. Any ``priority`` /
``order`` attribute on a source is ignored, and a source that names an
unknown class is refused (:func:`validate_source`). What a source returns
is capped: :data:`MAX_HITS_PER_SOURCE` hits, :data:`SNIPPET_MAX` characters
a snippet and :data:`FETCH_MAX_CHARS` a fetched text.

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
SNIPPET_MAX = 600
_SNIPPET_MAX = SNIPPET_MAX
#: A fetched text longer than this is cut (a passage, not a book).
FETCH_MAX_CHARS = 64 * 1024
#: Plugins whose source may be "local": their search runs on this box
#: (Coders Farm's Kiwix: the offline library). Any other plugin's "local"
#: claim is treated as "web".
LOCAL_PLUGINS: frozenset[str] = frozenset({"kiwix"})
CORE_SLUG = "core"
# The types the core registered its own sources as (needs_permission).
_CORE_TYPES: tuple[type, ...] = ()


def mark_core_type(cls: type) -> None:
    """Name a class as one of the core's own sources (needs_permission)."""
    global _CORE_TYPES
    if cls not in _CORE_TYPES:
        _CORE_TYPES = (*_CORE_TYPES, cls)


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
    #: The text's licence when the source knows it ("CC BY-SA 4.0" for a
    #: Wikipedia page), shown under a reply. Optional (added in 1.8).
    licence: str | None = None


@dataclass(frozen=True)
class LookupDocument:
    """A hit's whole text, as :meth:`LookupSource.fetch` returns it."""

    id: str
    title: str
    text: str
    url: str | None = None
    section: str | None = None
    licence: str | None = None


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


def validate_source(source: Any, *, lookup_class: str | None = None) -> str:
    """Raise ``TypeError`` / ``ValueError`` unless ``source`` keeps the
    contract: the two async calls, a slug, a name and a known class. The
    class is read ONCE (or taken from ``lookup_class``) and returned: what
    the core registers is that answer, whatever the attribute says later."""
    cls = getattr(source, "lookup_class", None) if lookup_class is None else lookup_class
    for attr, value in (("slug", getattr(source, "slug", None)), ("name", getattr(source, "name", None)),
                        ("lookup_class", cls)):
        if not isinstance(value, str) or not value.strip():
            raise TypeError(f"a lookup source needs a non-empty string {attr!r}")
    if cls not in CLASS_ORDER:
        raise ValueError(f"lookup_class {cls!r} must be one of {list(CLASS_ORDER)}")
    for meth in ("search", "fetch"):
        fn = getattr(source, meth, None)
        if not callable(fn) or not asyncio.iscoroutinefunction(fn):
            raise TypeError(f"a lookup source's {meth}() must be an async method")
    return cls


class RegisteredSource:
    """A source as the core holds it: its class fixed at registration,
    whether it is the core's, and its answers capped."""

    def __init__(self, source: Any, *, slug: str, lookup_class: str, core: bool) -> None:
        self.source = source
        self.slug = str(getattr(source, "slug", slug))
        self.name = str(getattr(source, "name", slug))
        self.lookup_class = lookup_class
        self.core = core
        self.registered_as = slug

    async def search(self, query: str, *, limit: int = 3) -> list[LookupHit]:
        return await self.source.search(query, limit=limit)

    async def fetch(self, id: str) -> LookupDocument | None:
        doc = await self.source.fetch(id)
        if not isinstance(doc, LookupDocument):
            return None
        text = str(doc.text or "")
        if len(text) > FETCH_MAX_CHARS:
            text = text[:FETCH_MAX_CHARS]
        return LookupDocument(id=str(doc.id)[:200], title=str(doc.title or "")[:_TITLE_MAX], text=text,
                              url=(str(doc.url)[:500] if doc.url else None),
                              section=(str(doc.section)[:_TITLE_MAX] if doc.section else None),
                              licence=_clean_licence(getattr(doc, "licence", None)))


def decided_class(claimed: str, *, slug: str, core: bool) -> str:
    """The class the core gives a source that claims ``claimed``."""
    if core:
        return claimed
    if claimed == LOCAL and slug not in LOCAL_PLUGINS:
        return WEB
    return claimed


def register(source: Any, *, slug: str, core: bool = False, registry: Any | None = None) -> RegisteredSource:
    """Validate ``source``, fix its class and register it (the plugin
    loader's ``add_lookup_source`` and the core's own help come here)."""
    claimed = validate_source(source)
    wrapped = RegisteredSource(source, slug=slug, lookup_class=decided_class(claimed, slug=slug, core=core),
                               core=core)
    _registry(registry).register(LOOKUP_SOURCE, wrapped, slug=slug)
    return wrapped


def needs_permission(source: Any) -> bool:
    """Whether asking ``source`` leaves the box (and so asks first). Local
    sources never do: no permission card, and they work under "never".
    Decided by the core: a registered source by the class it was given; an
    unregistered object only as a core source's type, or an allowlisted
    plugin's slug, may be local, whatever its attribute says."""
    if isinstance(source, RegisteredSource):
        return source.lookup_class != LOCAL
    if isinstance(source, _CORE_TYPES) and getattr(source, "lookup_class", None) == LOCAL:
        return False
    return not (getattr(source, "lookup_class", None) == LOCAL
                and getattr(source, "slug", None) in LOCAL_PLUGINS)


def _registry(registry: Any | None) -> Any:
    if registry is not None:
        return registry
    from domovoi.capabilities import CAPABILITIES

    return CAPABILITIES


def ordered_sources(registry: Any | None = None, *, classes: tuple[str, ...] | None = None,
                    household_order: Any = None) -> list[Any]:
    """Every registered source, in the core's order: class (local, API,
    web), then core sources before plugins', then registration slug.
    Nothing on the source object changes it. ``classes`` keeps only those
    (``(LOCAL,)`` under internet "never").

    ``household_order`` (Settings → Chat & AI → Search, the household's
    own choice, never a plugin's): source slugs or registration slugs,
    first first. A source it names goes before every source it doesn't, in
    its order; the rest keep the core's order. It changes which passages
    are read first, never whether a source asks before it is used: that
    is the class's (:func:`needs_permission`)."""
    reg = _registry(registry)
    pairs: list[tuple[int, int, str, Any]] = []
    # Both sorted by slug, so they pair up.
    for slug, source in zip(reg.providers_for(LOOKUP_SOURCE), reg.get_all(LOOKUP_SOURCE)):
        if not isinstance(source, RegisteredSource):
            # Registered around the core's register(): treated as an
            # unvetted plugin's, so it can't be "local".
            claimed = getattr(source, "lookup_class", None)
            if claimed not in CLASS_ORDER:
                continue
            source = RegisteredSource(source, slug=slug, core=False,
                                      lookup_class=decided_class(claimed, slug=slug, core=False))
        cls = source.lookup_class
        if classes is not None and cls not in classes:
            continue
        pairs.append((CLASS_ORDER.index(cls), 0 if source.core else 1, slug, source))
    pairs.sort(key=lambda p: (p[0], p[1], p[2]))
    out = [p[3] for p in pairs]
    wanted = [str(x).strip() for x in (household_order or []) if str(x).strip()]
    if wanted:
        def rank(src: Any) -> int:
            for i, name in enumerate(wanted):
                if name in (getattr(src, "slug", None), getattr(src, "registered_as", None)):
                    return i
            return len(wanted)

        out = sorted(out, key=rank)  # stable: the core's order within a rank
    return out


def parse_household_order(raw: Any) -> list[str]:
    """``lookup_source_order`` ("kiwix, core.wikipedia") as a list of slugs."""
    if isinstance(raw, (list, tuple)):
        items = raw
    else:
        items = str(raw or "").replace(";", ",").split(",")
    out: list[str] = []
    for x in items:
        x = str(x).strip()[:64]
        if x and x not in out:
            out.append(x)
    return out[:32]


def describe_sources(registry: Any | None = None, *, household_order: Any = None) -> list[dict[str, Any]]:
    """The sources a person can see and reorder (Settings → Search): slug,
    name, class, whether asking it needs the internet question, whether it
    is the core's. In the order they are asked."""
    return [
        {
            "slug": getattr(s, "slug", ""),
            "registered_as": getattr(s, "registered_as", getattr(s, "slug", "")),
            "name": getattr(s, "name", ""),
            "lookup_class": getattr(s, "lookup_class", WEB),
            "needs_permission": needs_permission(s),
            "core": bool(getattr(s, "core", False)),
        }
        for s in ordered_sources(registry, household_order=household_order)
    ]


_LICENCE_MAX = 60


def _clean_licence(value: Any) -> str | None:
    text = " ".join(str(value or "").split())[:_LICENCE_MAX]
    return text or None


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
        licence=_clean_licence(getattr(hit, "licence", None)),
    )


async def search_sources(
    query: str,
    *,
    limit: int = 3,
    classes: tuple[str, ...] | None = None,
    registry: Any | None = None,
    timeout: float = SEARCH_TIMEOUT_SEC,
    household_order: Any = None,
    enough: int | None = None,
    sources: list[Any] | None = None,
) -> list[tuple[Any, list[LookupHit]]]:
    """Ask each source in order; ``[(source, hits)]`` for those that found
    something. A source that raises, times out or returns junk is skipped
    with a log line: one broken plugin never costs the others' answers.

    ``enough``: stop asking once that many hits are in hand, so a question
    the offline library answers never reaches the internet sources after
    it. ``sources``: ask these (already ordered) instead of the registry's."""
    out: list[tuple[Any, list[LookupHit]]] = []
    found = 0
    if sources is None:
        sources = ordered_sources(registry, classes=classes, household_order=household_order)
    for source in sources:
        if enough is not None and found >= enough:
            break
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
            found += len(hits[:limit])
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
    "describe_sources",
    "needs_permission",
    "ordered_sources",
    "parse_household_order",
    "register",
    "RegisteredSource",
    "search_sources",
    "validate_source",
]
