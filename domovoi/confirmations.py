"""Mediated pending-confirmation API (design §4.7 — session-context side).

One outstanding yes/no question per session is deliberate voice UX; what
changes here is that raw JSONB hand-management is replaced by
this single entry point, which validates the namespaced ``kind`` against
the owning handler's declared ``confirmation_kinds`` at SET time. Two
features can no longer write colliding payload shapes into the slot.

Load-bearing semantics:

* setting while occupied REPLACES (chained-pending);
* the router's yes/no pre-empt does a dict-equality one-shot clear and
  THEN dispatches ``handler.handle_confirmation(kind, data, affirmative,
  ctx, session)`` through the registry.

The plugin SDK facade (stage C3) wraps this with the plugin's slug
namespace; core call sites use it directly with ``core.<kind>`` kinds.

Choices (``Handler.choice_kinds``, 2026-10-03): a parked question whose
answer may be free text ("did you mean Glass Harbor?" → "no, play the
Velvet Kites") rather than a bare yes/no. The router has to look at the
session's slot for EVERY reply to one of those, not only for a yes/no —
but an ordinary command turn must not pay a session read for it. So
:data:`CHOICE_PARKED` remembers, in this process, which sessions have a
choice waiting: :func:`request_confirmation` adds the session when the
kind is one of the handler's ``choice_kinds`` and drops it when any
other kind replaces the slot; the router takes it out
(:func:`take_parked_choice`) on the session's next turn.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

PENDING_CONFIRMATION_KEY = "pending_confirmation"
CORE_KIND_PREFIX = "core."

#: Sessions (``str(session_id)``) with a parked CHOICE waiting for its reply.
#: Process-local on purpose: the router checks it on every turn, and a set
#: lookup costs nothing, where reading the session's context would cost a
#: database round-trip on every ordinary command. Lost on a core restart —
#: then a yes/no reply still reaches the choice through the router's yes/no
#: pre-empt, and anything else is routed as a new request.
CHOICE_PARKED: set[str] = set()
# When each entry was added, so entries for sessions that never speak again
# (a one-off /v1/intent session) don't pile up for the life of the process.
_CHOICE_PARKED_AT: dict[str, float] = {}
_CHOICE_PARKED_MAX_AGE_SEC = 3600.0
_CHOICE_PARKED_PRUNE_AT = 64


def _note_choice(session_id: Any, parked: bool) -> None:
    sid = str(session_id)
    if not parked:
        CHOICE_PARKED.discard(sid)
        _CHOICE_PARKED_AT.pop(sid, None)
        return
    now = time.monotonic()
    if len(CHOICE_PARKED) >= _CHOICE_PARKED_PRUNE_AT:
        for old, at in list(_CHOICE_PARKED_AT.items()):
            if now - at > _CHOICE_PARKED_MAX_AGE_SEC:
                CHOICE_PARKED.discard(old)
                _CHOICE_PARKED_AT.pop(old, None)
    CHOICE_PARKED.add(sid)
    _CHOICE_PARKED_AT[sid] = now


def take_parked_choice(session_id: Any) -> bool:
    """Whether ``session_id`` had a choice parked — and forget it either
    way. The router calls it once per turn; a choice the reply answers or
    abandons is gone, and one the handler parks again is added back by
    :func:`request_confirmation`."""
    sid = str(session_id)
    if sid not in CHOICE_PARKED:
        return False
    CHOICE_PARKED.discard(sid)
    _CHOICE_PARKED_AT.pop(sid, None)
    return True


async def request_confirmation(
    session: AsyncSession,
    session_id: UUID,
    *,
    kind: str,
    handler: str,
    data: dict[str, Any] | None = None,
    prompt: str | None = None,
) -> None:
    """Park a pending confirmation for ``handler`` in the session context.

    ``kind`` MUST be namespaced ("core.<kind>" / "<slug>.<kind>") and
    declared in the handler's ``confirmation_kinds`` — enforced here so a
    payload the router could never dispatch is impossible to park.
    Replaces any existing pending payload (single-slot semantics).

    ``prompt`` optionally records the spoken question for observability;
    handlers speak their own question via Response.text either way.
    """
    # Late import: handlers register through domovoi.handlers, which several
    # handler modules import this module from.
    from domovoi.db.repositories import SessionRepository
    from domovoi.handlers import HANDLER_BY_NAME

    target = HANDLER_BY_NAME.get(handler)
    if target is None:
        raise ValueError(f"request_confirmation: unknown handler {handler!r}")
    if kind not in target.confirmation_kinds:
        raise ValueError(
            f"request_confirmation: kind {kind!r} is not declared in "
            f"{handler!r}.confirmation_kinds={target.confirmation_kinds!r}"
        )

    payload: dict[str, Any] = {"handler": handler, "kind": kind}
    if prompt is not None:
        payload["prompt"] = prompt
    for key, value in (data or {}).items():
        if key in ("handler", "kind"):
            continue
        payload[key] = value

    await SessionRepository(session).set_context_key(
        session_id, PENDING_CONFIRMATION_KEY, payload
    )
    # Single slot: a choice parked here is now the session's question, and
    # any other kind has just replaced whatever choice was waiting.
    _note_choice(session_id, kind in getattr(target, "choice_kinds", ()))
