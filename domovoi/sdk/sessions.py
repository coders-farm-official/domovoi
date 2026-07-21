"""SessionAPI — namespaced session context + mediated confirmations
(design §4.7, SDK side).

``sessions.context`` JSONB carries a reserved ``"plugins"`` object:
plugin keys live at ``context["plugins"]["<slug>"][...]`` and are
reachable ONLY through this API. Core keys (``recent_turns``,
``pending_confirmation``, ``conversational_mode``, ...) stay top-level
and are not writable here.

``request_confirmation`` wraps the core mediated pending API
(:mod:`domovoi.confirmations`) with the plugin's namespace: the kind
must be ``"<slug>.<kind>"`` and declared in the owning handler's
``confirmation_kinds`` — enforced at SET time, so two plugins can never
park colliding payload shapes in the single per-session slot.

Rooms vs sessions: the voice stack keys context by session UUID, but a
plugin usually knows only the room. Every method accepts either a
session UUID or a room_id string and resolves the room's most-recent
session (creating one for a never-heard-from room, mirroring the
streaming layer's get_or_create).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.confirmations import request_confirmation as _core_request_confirmation
from domovoi.db.repositories import SessionRepository

log = logging.getLogger(__name__)

PLUGINS_NAMESPACE = "plugins"


async def resolve_session_id(
    session: AsyncSession, room_or_session: UUID | str | None
) -> UUID:
    """UUID passes through; a room_id string resolves to the room's
    most-recently-active session row (created if none exists)."""
    if isinstance(room_or_session, UUID):
        return await SessionRepository(session).get_or_create(room_or_session, None)
    room_id = room_or_session
    if room_id is not None:
        row = (
            await session.execute(
                text(
                    "SELECT id FROM sessions WHERE room_id = :room "
                    "ORDER BY last_activity DESC NULLS LAST LIMIT 1"
                ),
                {"room": room_id},
            )
        ).first()
        if row is not None:
            return UUID(str(row[0]))
    return await SessionRepository(session).get_or_create(None, room_id)


class SessionAPI:
    def __init__(self, slug: str) -> None:
        self.slug = slug

    # ── Namespaced context keys ────────────────────────────────────────
    async def get_key(
        self,
        session: AsyncSession,
        room_or_session: UUID | str | None,
        key: str,
        default: Any = None,
    ) -> Any:
        sid = await resolve_session_id(session, room_or_session)
        ctx = await SessionRepository(session).get_context(sid)
        return ((ctx.get(PLUGINS_NAMESPACE) or {}).get(self.slug) or {}).get(
            key, default
        )

    async def set_key(
        self,
        session: AsyncSession,
        room_or_session: UUID | str | None,
        key: str,
        value: Any,
    ) -> None:
        sid = await resolve_session_id(session, room_or_session)
        repo = SessionRepository(session)
        ctx = await repo.get_context(sid)
        plugins = dict(ctx.get(PLUGINS_NAMESPACE) or {})
        ns = dict(plugins.get(self.slug) or {})
        ns[key] = value
        plugins[self.slug] = ns
        await repo.set_context_key(sid, PLUGINS_NAMESPACE, plugins)

    async def clear_namespace(
        self, session: AsyncSession, room_or_session: UUID | str | None
    ) -> None:
        """Drop every key this plugin stored on the session — the disable
        teardown calls this per live session (design §3.4)."""
        sid = await resolve_session_id(session, room_or_session)
        repo = SessionRepository(session)
        ctx = await repo.get_context(sid)
        plugins = dict(ctx.get(PLUGINS_NAMESPACE) or {})
        if self.slug not in plugins:
            return
        del plugins[self.slug]
        await repo.set_context_key(sid, PLUGINS_NAMESPACE, plugins)

    async def clear_namespace_everywhere(self, session: AsyncSession) -> int:
        """Bulk teardown across ALL sessions (disable/uninstall). Returns
        how many rows were touched."""
        # asyncpg binds a TEXT[] path from a Python list — NOT a Postgres
        # array-literal string. Passing "{plugins,slug}" raises DataError
        # ("invalid input for query argument"), which the uninstall teardown
        # would swallow as best-effort and silently skip the cleanup.
        result = await session.execute(
            text(
                """
                UPDATE sessions
                SET context = context #- CAST(:path AS TEXT[])
                WHERE context -> :ns ? :slug
                """
            ),
            {
                "path": [PLUGINS_NAMESPACE, self.slug],
                "ns": PLUGINS_NAMESPACE,
                "slug": self.slug,
            },
        )
        return result.rowcount or 0

    # ── Mediated pending confirmation ──────────────────────────────────
    async def request_confirmation(
        self,
        session: AsyncSession,
        room_or_session: UUID | str | None,
        *,
        kind: str,
        handler: str,
        data: dict[str, Any] | None = None,
        prompt: str | None = None,
    ) -> None:
        if not kind.startswith(f"{self.slug}."):
            raise ValueError(
                f"request_confirmation: plugin {self.slug!r} must use "
                f"'{self.slug}.<kind>' namespaced kinds, got {kind!r}"
            )
        # JSON-serializable guard: the payload lands in sessions.context.
        json.dumps(data or {})
        sid = await resolve_session_id(session, room_or_session)
        await _core_request_confirmation(
            session, sid, kind=kind, handler=handler, data=data, prompt=prompt
        )
