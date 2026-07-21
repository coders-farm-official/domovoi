"""Handler contract — the formalized ABC every voice handler implements.

Core and plugin handlers share this exact surface (design §4.3/§4.3.1):

* ``priority_band`` (required, no default) replaces position-in-list
  ordering. Dispatch order is ascending band; ties break core-first,
  then plugin slug, then handler name — fully deterministic and free
  of registration-order dependence. See ``registry_sort_key``.
* ``display`` carries the human-facing metadata (label / tone / icon)
  that the web dashboard filters and the Android tone map render from.
  ``Handler.name`` itself stays a STABLE IDENTIFIER — it is what lands
  in ``intents_log.matched_handler`` / ``conversation_log`` and must
  never be renamed for cosmetic reasons (design §12).
* ``fast_paths`` holds :class:`FastPath` entries. Bare
  ``(pattern, method)`` tuples are accepted as sugar and normalized at
  registration (``normalize_fast_paths``); ``FastPath`` also supports
  2-tuple unpacking so tuple-style iteration keeps working.
* ``handle_confirmation`` is ON the ABC (no more duck-typing). Kinds
  are declared in ``confirmation_kinds`` and namespaced — ``core.<kind>``
  for core handlers, ``<slug>.<kind>`` for plugins — so two features can
  never write colliding payload shapes into the single per-session
  pending-confirmation slot (design §4.7).

Offline-gate asymmetry (design §4.3, dossier §2.1 audit M4): the router
auto-invokes ``fallback_offline`` for ``requires_network == "yes"``
handlers while offline. ``"degraded"`` handlers are
NOT auto-fallen-back wholesale; instead each FastPath declares
``offline_ok`` (default True for degraded handlers) and the router
auto-falls-back only an ``offline_ok=False`` fast path.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterator, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.models import Context, Intent, Response

FastPathMethod = Callable[["Handler", re.Match[str], Context, AsyncSession], Awaitable[Response]]
RequiresNetwork = Literal["no", "degraded", "yes"]


@dataclass(frozen=True)
class HandlerDisplay:
    """Human-facing metadata for a handler (design §4.3).

    ``label`` names the handler in web filters / the Android manual;
    ``tone`` is the UI tone slug (neutral|media|device|info|comms);
    ``icon`` is an optional asset path (plugins point into their
    ``web/static/``; core handlers usually leave it None).
    """

    label: str
    tone: str = "neutral"
    icon: str | None = None


@dataclass(frozen=True)
class FastPath:
    """One anchored-regex dispatch entry.

    ``offline_ok`` is only meaningful for ``requires_network="degraded"``
    handlers: ``None`` there means the default True (path works offline);
    ``False`` marks a path the router must auto-fallback while offline.
    For "no"/"yes" handlers it MUST stay ``None`` — contract-checked in
    test_registry (and at plugin install time, design §13.2).
    """

    pattern: re.Pattern[str]
    method: FastPathMethod
    offline_ok: bool | None = None

    def __iter__(self) -> Iterator[Any]:
        # 2-tuple unpacking sugar: `for pattern, method in handler.fast_paths`.
        yield self.pattern
        yield self.method


def as_fast_path(entry: FastPath | tuple[re.Pattern[str], FastPathMethod]) -> FastPath:
    """Normalize a fast-path entry — bare (pattern, method) tuples are
    accepted as declaration sugar and become FastPath here."""
    if isinstance(entry, FastPath):
        return entry
    pattern, method = entry
    return FastPath(pattern=pattern, method=method)


def normalize_fast_paths(handler: "Handler") -> None:
    """Normalize ``handler.fast_paths`` in place at registration time."""
    handler.fast_paths = [as_fast_path(e) for e in handler.fast_paths]


def registry_sort_key(handler: "Handler") -> tuple[int, int, str, str]:
    """Deterministic dispatch order (design §4.2): ascending band;
    tie-break core handlers before plugin handlers, then plugin slug
    ascending, then handler name ascending."""
    slug = getattr(handler, "plugin_slug", None)
    return (
        handler.priority_band,
        0 if slug is None else 1,
        slug or "",
        handler.name,
    )


class Handler(ABC):
    name: str                                  # == tool_schema["name"]; stable identifier (§12)
    priority_band: int                         # REQUIRED, no default — design §4.2 band table
    tool_schema: dict[str, Any]
    fast_paths: list[FastPath]                 # (pattern, method) tuples accepted as sugar
    requires_network: RequiresNetwork = "no"
    display: HandlerDisplay                    # REQUIRED — web filters + Android tone map
    # Namespaced confirmation kinds this handler can resume: "core.<kind>"
    # for core handlers, "<slug>.<kind>" for plugin handlers. The mediated
    # pending API (domovoi.confirmations) validates at set time; the router
    # dispatches only declared kinds.
    confirmation_kinds: tuple[str, ...] = ()
    # Plugin handlers get their slug stamped by the plugin loader (C3);
    # None marks a core handler (used by the registry tie-break).
    plugin_slug: str | None = None
    # Exposed to conversational chat mode (#8) as a Letta-callable tool via the
    # proxy-tool bridge? Default off; a handler opts in only when its action fits
    # an ORGANIC conversational moment (e.g. play a song) rather than a
    # transactional command. Requires execute_from_tool + a usable tool_schema.
    # See letta_tools.build_chat_tool_sources + the /v1/admin/chat-tool endpoint.
    chat_exposed: bool = False

    @abstractmethod
    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        ...

    async def execute_from_tool(
        self, args: dict[str, Any], ctx: Context, session: AsyncSession
    ) -> Response:
        raise NotImplementedError(
            f"{self.name} does not implement execute_from_tool (LLM tool-call routing)"
        )

    async def fallback_offline(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        raise NotImplementedError(
            f"{self.name} declares requires_network={self.requires_network} "
            f"but does not implement fallback_offline()"
        )

    async def handle_confirmation(
        self,
        kind: str,
        data: dict[str, Any],
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """Resume a parked yes/no flow. The router dispatches here by the
        pending payload's handler name; ``kind`` is guaranteed to be one of
        ``self.confirmation_kinds`` (the mediated pending API enforces it
        at set time, the router re-checks at dispatch)."""
        raise NotImplementedError(
            f"{self.name} declares no confirmation flow (confirmation_kinds="
            f"{self.confirmation_kinds!r}) but handle_confirmation was called "
            f"with kind={kind!r}"
        )
