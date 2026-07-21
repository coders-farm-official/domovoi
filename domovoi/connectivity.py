from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from domovoi.config import settings
from domovoi.db.repositories import ConnectivityEventRepository
from domovoi.db.session import session_scope
from domovoi.events import EVENTS

log = logging.getLogger(__name__)

# Process-wide "the probe" accessor. The lifespan constructs the probe and
# registers it here (as well as on app.state); the SDK's ConnectivityView
# and startup-hook gating read it without needing an app reference.
_current_probe: "ConnectivityProbe | None" = None


def set_current_probe(probe: "ConnectivityProbe | None") -> None:
    global _current_probe
    _current_probe = probe


def current_probe() -> "ConnectivityProbe | None":
    return _current_probe


def _parse_target(target: str) -> tuple[str, int]:
    host, _, port = target.rpartition(":")
    if not host:
        raise ValueError(f"CONNECTIVITY_PROBE_TARGET must be host:port, got {target!r}")
    return host, int(port)


class ConnectivityProbe:
    """Background TCP ping that exposes an `online` flag.

    Logs only state *transitions* (online→offline or offline→online) to
    `connectivity_events`, not every poll — the table stays small.
    """

    def __init__(
        self,
        target: str | None = None,
        interval_sec: float | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        self.target = target or settings.connectivity_probe_target
        self.interval_sec = interval_sec or settings.connectivity_probe_interval_sec
        self.timeout_sec = timeout_sec or settings.connectivity_probe_timeout_sec
        self._host, self._port = _parse_target(self.target)

        self.online: bool = True
        self.last_checked_at: datetime | None = None
        self.last_online_at: datetime | None = None

        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._started_once = False

    async def _probe_once(self) -> bool:
        try:
            fut = asyncio.open_connection(self._host, self._port)
            reader, writer = await asyncio.wait_for(fut, timeout=self.timeout_sec)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except (asyncio.TimeoutError, OSError):
            return False

    async def check_now(self) -> bool:
        """Single probe + state update + transition log. Returns the new online state."""
        is_up = await self._probe_once()
        now = datetime.now(tz=timezone.utc)
        self.last_checked_at = now
        if is_up:
            self.last_online_at = now

        transitioned = (not self._started_once) or (is_up != self.online)
        self._started_once = True
        self.online = is_up

        if transitioned:
            state = "online" if is_up else "offline"
            log.info("connectivity %s (target=%s)", state, self.target)
            # Fire-and-forget bus event (design §4.9) — connectivity-gated
            # startup hooks and plugin workers key off this.
            try:
                EVENTS.emit("core.connectivity_changed", {"online": is_up})
            except Exception as e:  # pragma: no cover — never break the probe
                log.debug("connectivity event emit failed: %s", e)
            try:
                async with session_scope() as s:
                    await ConnectivityEventRepository(s).log_transition(
                        connectivity_state=state, target=self.target
                    )
            except Exception as e:
                log.warning("failed to log connectivity transition: %s", e)
        return is_up

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.check_now()
            except Exception as e:
                log.warning("connectivity probe error: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_sec)
            except asyncio.TimeoutError:
                continue

    async def start(self) -> None:
        if self._task is not None:
            return
        await self.check_now()
        self._task = asyncio.create_task(self._run(), name="connectivity-probe")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
