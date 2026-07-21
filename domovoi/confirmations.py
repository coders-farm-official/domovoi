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
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

PENDING_CONFIRMATION_KEY = "pending_confirmation"
CORE_KIND_PREFIX = "core."


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
