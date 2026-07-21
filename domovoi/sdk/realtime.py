"""RealtimeAPI — mechanical commit-coupled NOTIFY (design §4.12).

Plugins never call ``pg_notify`` directly: ``notify(session, suffix,
reason)`` formats ``plugin_<slug>_<suffix>`` and executes on the
caller's OPEN session, so the NOTIFY rides the caller's transaction and
the commit-coupling invariant (dossier §7 inv. 8) is preserved
mechanically. The web-side LISTEN wiring derives the same
``plugin_<slug>_`` prefix from the manifest, so the two ends can't
drift on channel names.

Deviation from the §4.10 sketch (which wrote ``notify(channel_suffix,
reason)``): the caller's session is an explicit first argument — SQL
can't execute without one, and requiring it keeps "inside a
session_scope transaction" structurally true rather than documented.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SUFFIX_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RealtimeAPI:
    def __init__(self, slug: str) -> None:
        self.slug = slug

    def channel_for(self, channel_suffix: str) -> str:
        if not _SUFFIX_RE.match(channel_suffix):
            raise ValueError(
                f"notify: bad channel suffix {channel_suffix!r} "
                "(lowercase [a-z0-9_], must start with a letter)"
            )
        return f"plugin_{self.slug}_{channel_suffix}"

    async def notify(
        self, session: AsyncSession, channel_suffix: str, reason: str = ""
    ) -> str:
        """Fire ``pg_notify('plugin_<slug>_<suffix>', reason)`` on the
        caller's open transaction. Returns the full channel name."""
        channel = self.channel_for(channel_suffix)
        await session.execute(
            text("SELECT pg_notify(:channel, :reason)"),
            {"channel": channel, "reason": reason or ""},
        )
        return channel
