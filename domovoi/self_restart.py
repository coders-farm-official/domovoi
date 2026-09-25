"""Bounce the Domovoi services from the dashboard.

The version panel can pull new code, but the running process keeps serving
the modules it imported at boot — so the honest end of that flow is a
restart. Doing it needs privilege the service user doesn't have by default,
so this mirrors the pattern the satellite already uses for its own
self-restart (``PROVISIONING.md`` §8.1): a single least-privilege sudoers
grant for one exact command.

Which command depends on the host (docs/LINUX_HOST.md):

* With ``domovoi-update.service`` installed, the restart starts that unit.
  It runs ``scripts/linux/apply-update.sh`` as root: back up the database,
  sync dependencies, rebuild the MPD image, migrate, start core and web,
  health-check them, and roll back if they don't come up. Grant::

    domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block start domovoi-update.service

* Without it, the restart bounces core and web and nothing else, exactly as
  before the unit existed. Grant::

    domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-core.service domovoi-web.service

Whether the unit is installed is a plain look at the systemd unit
directories: no sudo, no systemctl. Nothing here escalates on its own.
:func:`capable` reports whether the grant for the current mode exists so the
UI can offer a working button or fall back to showing the command;
:func:`restart` refuses rather than prompting when it doesn't. An installed
unit without its grant is reported as incapable, never quietly downgraded to
a plain restart that would skip the migrations.

Both units are bounced together: a pull moves the whole checkout, and core
and web import from the same tree, so restarting one would leave the other
running stale code — the exact confusion the version panel exists to end.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time

log = logging.getLogger(__name__)

UNITS = ("domovoi-core.service", "domovoi-web.service")
UPDATE_UNIT = "domovoi-update.service"

# The system manager's unit search path, highest priority first
# (systemd.unit(5)). The first directory holding the unit decides.
_UNIT_DIRS = (
    "/etc/systemd/system",
    "/run/systemd/system",
    "/usr/local/lib/systemd/system",
    "/usr/lib/systemd/system",
    "/lib/systemd/system",
)

# No systemd on Windows; the restart there is always "incapable".
_WINDOWS = sys.platform == "win32"

# Strong references to in-flight restart tasks (see restart()).
_PENDING: set[asyncio.Task] = set()

# Long enough for the HTTP response to flush before systemd kills this
# process — the client must learn the restart started, or it can't tell
# "restarting" from "the server broke".
_RESTART_DELAY_SEC = 1.0
_PROBE_TIMEOUT_SEC = 5.0


def _systemctl() -> str | None:
    return shutil.which("systemctl")


def _sudo() -> str | None:
    return shutil.which("sudo")


def update_unit_installed() -> bool:
    """Whether ``domovoi-update.service`` is installed, by looking for its
    unit file. A masked unit (a link to /dev/null, or an empty file, which
    systemd also reads as masked) counts as not installed: masking is how
    an admin switches it off."""
    if _WINDOWS:
        return False
    for unit_dir in _UNIT_DIRS:
        path = os.path.join(unit_dir, UPDATE_UNIT)
        if not os.path.lexists(path):
            continue
        try:
            return (
                os.path.realpath(path) != os.devnull
                and os.path.isfile(path)
                and os.path.getsize(path) > 0
            )
        except OSError:
            return False
    return False


def restart_mode() -> str:
    """``"update"`` when the restart starts the update unit, else ``"restart"``."""
    return "update" if update_unit_installed() else "restart"


def _action(mode: str) -> list[str]:
    """The systemctl arguments the restart runs in ``mode``."""
    if mode == "update":
        return ["--no-block", "start", UPDATE_UNIT]
    return ["--no-block", "restart", *UNITS]


def _units(mode: str) -> list[str]:
    return [UPDATE_UNIT] if mode == "update" else list(UNITS)


def capable(mode: str | None = None) -> tuple[bool, str | None]:
    """Whether this host can restart itself unattended.

    ``sudo -n -l <cmd>`` asks "may I run exactly this?" without running it and
    without prompting. Blocking and cheap; callers thread-wrap it.
    """
    mode = mode or restart_mode()
    systemctl, sudo = _systemctl(), _sudo()
    if systemctl is None:
        return False, "systemctl not found — not a systemd host"
    if sudo is None:
        return False, "sudo not found"
    try:
        proc = subprocess.run(
            [sudo, "-n", "-l", systemctl, *_action(mode)],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"sudo probe failed: {e}"
    if proc.returncode == 0:
        return True, None
    if mode == "update":
        return False, (
            f"{UPDATE_UNIT} is installed but there is no passwordless sudoers "
            "grant to start it — see the update unit in docs/LINUX_HOST.md"
        )
    return False, (
        "no passwordless sudoers grant for systemctl restart — see the "
        "self-restart entry in docs/LINUX_HOST.md"
    )


# The version panel polls, and each probe forks sudo — memoize. Capability
# changes only when someone edits sudoers or installs the update unit; the
# cache is keyed by mode so installing the unit is picked up at once, and a
# short TTL picks up a sudoers edit.
_CAP_TTL_SEC = 60.0
_cap_cache: tuple[float, str, tuple[bool, str | None]] | None = None


async def capable_async() -> tuple[bool, str | None]:
    global _cap_cache
    now = time.monotonic()
    mode = restart_mode()
    if (
        _cap_cache is not None
        and _cap_cache[1] == mode
        and now - _cap_cache[0] < _CAP_TTL_SEC
    ):
        return _cap_cache[2]
    result = await asyncio.to_thread(capable, mode)
    _cap_cache = (now, mode, result)
    return result


def _spawn_restart(mode: str | None = None) -> None:
    """Fire the restart. ``--no-block`` returns immediately instead of waiting
    on units that are about to kill this very process.

    The outcome is LOGGED rather than discarded. The endpoint has already
    answered "ok" by the time this runs — all it knew was that a restart had
    been scheduled — so if sudo refuses here, the journal is the only place
    anyone can find out. Silence looks identical to success from the UI."""
    mode = mode or restart_mode()
    systemctl, sudo = _systemctl(), _sudo()
    if systemctl is None or sudo is None:  # pragma: no cover — capable() gates
        log.error("self-restart: systemctl or sudo vanished between probe and fire")
        return
    cmd = [sudo, "-n", systemctl, *_action(mode)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_PROBE_TIMEOUT_SEC, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("self-restart failed to spawn: %s", e)
        return
    if proc.returncode != 0:
        log.error(
            "self-restart REFUSED (rc=%s): %s — check the sudoers grant in "
            "docs/LINUX_HOST.md matches this exact command: %s",
            proc.returncode, (proc.stderr or "").strip(), " ".join(cmd),
        )
    elif mode == "update":
        log.warning("self-restart: systemctl accepted the start of %s", UPDATE_UNIT)
    else:
        log.warning("self-restart: systemctl accepted the bounce")


async def restart() -> dict:
    """Schedule the restart, shortly after this response flushes: start the
    update unit when it's installed, else bounce both units.

    Returns ``{"ok", "mode", "units", "delay_sec", "error"}`` and never
    raises — an incapable host reports why instead of half-restarting.
    """
    mode = restart_mode()
    units = _units(mode)
    ok, why = await capable_async()
    if not ok:
        return {"ok": False, "mode": mode, "units": units, "delay_sec": None, "error": why}

    async def _later() -> None:
        await asyncio.sleep(_RESTART_DELAY_SEC)
        await asyncio.to_thread(_spawn_restart, mode)

    # Hold a strong reference. asyncio keeps only a weak one, so a task with
    # no other referent can be garbage-collected mid-sleep — and the restart
    # then simply never happens, while the endpoint has already reported
    # success. The discard callback keeps the set from growing.
    task = asyncio.create_task(_later())
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    if mode == "update":
        log.warning("self-restart requested — starting %s", UPDATE_UNIT)
    else:
        log.warning("self-restart requested — bouncing %s", " ".join(UNITS))
    return {
        "ok": True,
        "mode": mode,
        "units": units,
        "delay_sec": _RESTART_DELAY_SEC,
        "error": None,
    }
