"""Bounce the Domovoi services from the dashboard.

The version panel can pull new code, but the running process keeps serving
the modules it imported at boot — so the honest end of that flow is a
restart. Doing it needs privilege the service user doesn't have by default,
so this mirrors the pattern the satellite already uses for its own
self-restart (``PROVISIONING.md`` §8.1): a single least-privilege sudoers
grant for one exact command.

    domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-core.service domovoi-web.service

Nothing here escalates on its own. :func:`capable` reports whether that grant
exists so the UI can offer a working button or fall back to showing the
command; :func:`restart` refuses rather than prompting when it doesn't.

Both units are bounced together: a pull moves the whole checkout, and core
and web import from the same tree, so restarting one would leave the other
running stale code — the exact confusion the version panel exists to end.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time

log = logging.getLogger(__name__)

UNITS = ("domovoi-core.service", "domovoi-web.service")

# Long enough for the HTTP response to flush before systemd kills this
# process — the client must learn the restart started, or it can't tell
# "restarting" from "the server broke".
_RESTART_DELAY_SEC = 1.0
_PROBE_TIMEOUT_SEC = 5.0


def _systemctl() -> str | None:
    return shutil.which("systemctl")


def _sudo() -> str | None:
    return shutil.which("sudo")


def capable() -> tuple[bool, str | None]:
    """Whether this host can restart itself unattended.

    ``sudo -n -l <cmd>`` asks "may I run exactly this?" without running it and
    without prompting. Blocking and cheap; callers thread-wrap it.
    """
    systemctl, sudo = _systemctl(), _sudo()
    if systemctl is None:
        return False, "systemctl not found — not a systemd host"
    if sudo is None:
        return False, "sudo not found"
    try:
        proc = subprocess.run(
            [sudo, "-n", "-l", systemctl, "--no-block", "restart", *UNITS],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"sudo probe failed: {e}"
    if proc.returncode == 0:
        return True, None
    return False, (
        "no passwordless sudoers grant for systemctl restart — see the "
        "self-restart entry in docs/LINUX_HOST.md"
    )


# The version panel polls, and each probe forks sudo — memoize. Capability
# changes only when someone edits sudoers, which a short TTL picks up.
_CAP_TTL_SEC = 60.0
_cap_cache: tuple[float, tuple[bool, str | None]] | None = None


async def capable_async() -> tuple[bool, str | None]:
    global _cap_cache
    now = time.monotonic()
    if _cap_cache is not None and now - _cap_cache[0] < _CAP_TTL_SEC:
        return _cap_cache[1]
    result = await asyncio.to_thread(capable)
    _cap_cache = (now, result)
    return result


def _spawn_restart() -> None:
    """Fire the restart. ``--no-block`` returns immediately instead of waiting
    on units that are about to kill this very process."""
    systemctl, sudo = _systemctl(), _sudo()
    if systemctl is None or sudo is None:  # pragma: no cover — capable() gates
        return
    try:
        subprocess.run(
            [sudo, "-n", systemctl, "--no-block", "restart", *UNITS],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:  # pragma: no cover
        log.error("self-restart failed to spawn: %s", e)


async def restart() -> dict:
    """Schedule a bounce of both units, shortly after this response flushes.

    Returns ``{"ok", "units", "delay_sec", "error"}`` and never raises — an
    incapable host reports why instead of half-restarting.
    """
    ok, why = await capable_async()
    if not ok:
        return {"ok": False, "units": list(UNITS), "delay_sec": None, "error": why}

    async def _later() -> None:
        await asyncio.sleep(_RESTART_DELAY_SEC)
        await asyncio.to_thread(_spawn_restart)

    asyncio.create_task(_later())
    log.warning("self-restart requested — bouncing %s", " ".join(UNITS))
    return {
        "ok": True,
        "units": list(UNITS),
        "delay_sec": _RESTART_DELAY_SEC,
        "error": None,
    }
