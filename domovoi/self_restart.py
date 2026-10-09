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
  health-check them, and roll back if they don't come up. With nothing new
  since the commit it last applied (the panel's "Restart Domovoi" when
  nothing is waiting) it skips all of that: it stops and starts core and
  web and health-checks them. Grant::

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

One at a time. :func:`underway` says whether a restart or update is under
way right now, and :func:`restart` refuses another while one is (and
:func:`git_version.pull` refuses to move the checkout under it). The
dashboard's own memory of a press is gone on a reload, so the answer comes
from the server: systemd's state of the update unit, which outlives this
process and covers a run started any way (the dashboard, another tab,
``sudo systemctl start`` over ssh); the restart this process accepted,
for the second before systemd has the job (and, without the unit, until
the bounce ends this process); and the unit's own ``running`` record when
systemctl can't say.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

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

# The restart this process accepted and has not seen through, as
# (monotonic time, wall-clock time, mode) of its ok answer; None when there
# is none. See underway().
_REQUEST: tuple[float, float, str] | None = None

# How long an accepted restart counts as under way on its own. It covers
# _RESTART_DELAY_SEC, the sudo fork and systemd taking the job, after which
# the update unit's state answers (or, without the unit, the bounce has
# ended this process). Short, so a run the unit refuses at once doesn't
# hold the dashboard's buttons for long.
_REQUEST_GRACE_SEC = 20.0

# systemd's ActiveState while the oneshot update unit runs is
# "activating"; the others are a stop or reload of it in flight.
_UNIT_BUSY_STATES = frozenset({"activating", "active", "deactivating", "reloading"})

# The unit's own "running" record, believed only while systemctl can't say
# and only this long after the run started: TimeoutStartSec=30min
# (install-update-unit.sh) ends a longer run, and a record a power cut left
# "running" must not hold the buttons for good.
_RUNNING_RECORD_MAX_SEC = 30 * 60

# last-result.json's timestamps (apply-update.sh now_iso).
_RESULT_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


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


def manual_command(mode: str | None = None) -> str | None:
    """The command that does this host's restart by hand, for the version
    panel to show when :func:`capable` says the host can't do it unattended.

    None on a host without systemd (Windows, a development box): the
    services there run however they were started, and no one command
    restarts them. Never run here; it is text for a person."""
    if _WINDOWS or _systemctl() is None:
        return None
    if (mode or restart_mode()) == "update":
        return f"sudo systemctl start {UPDATE_UNIT}"
    return "sudo systemctl restart domovoi-core domovoi-web"


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


# ─── a restart under way ──────────────────────────────────────────────────


def update_unit_state() -> str | None:
    """Blocking. systemd's ActiveState for the update unit: "activating"
    while it runs, "inactive" or "failed" once it is done. None when
    systemctl can't say (no systemd, the bus out of reach). A plain read
    over the system bus: no sudo, and nothing changes."""
    if _WINDOWS:
        return None
    systemctl = _systemctl()
    if systemctl is None:
        return None
    try:
        proc = subprocess.run(
            [systemctl, "show", "--property=ActiveState", "--value", UPDATE_UNIT],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    state = (proc.stdout or "").strip()
    return state if proc.returncode == 0 and state else None


def _result_started(last_update: dict | None) -> float | None:
    """When the unit's run in ``last_update`` started, as epoch seconds."""
    value = last_update.get("started_at") if isinstance(last_update, dict) else None
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, _RESULT_TIME_FORMAT).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _note_request(mode: str) -> None:
    global _REQUEST
    _REQUEST = (time.monotonic(), time.time(), mode)


def _forget_request() -> None:
    global _REQUEST
    _REQUEST = None


def _request_pending(last_update: dict | None) -> bool:
    """Whether the restart this process accepted still counts as under way
    on its own: inside the grace, and, with the update unit, no run the
    unit has finished since (one it refused at once never ends this
    process)."""
    if _REQUEST is None:
        return False
    at_mono, at_wall, mode = _REQUEST
    if time.monotonic() - at_mono > _REQUEST_GRACE_SEC:
        return False
    if mode == "update" and isinstance(last_update, dict) and last_update.get("status") != "running":
        started = _result_started(last_update)
        # The unit stamps its start to the second, a second or more after
        # the answer: a result from before the request is the run before.
        if started is not None and started >= int(at_wall):
            return False
    return True


def _running_record(last_update: dict | None) -> bool:
    if not isinstance(last_update, dict) or last_update.get("status") != "running":
        return False
    started = _result_started(last_update)
    return started is not None and 0 <= time.time() - started <= _RUNNING_RECORD_MAX_SEC


def underway(last_update: dict | None = None, mode: str | None = None) -> bool:
    """Blocking. Whether a restart or update is under way right now, so
    nothing may start another: the restart this process just accepted
    (:func:`_request_pending`), else, with the update unit, systemd's word
    on it (:func:`update_unit_state`), else the unit's own ``running``
    record while it is recent. ``last_update`` is the unit's last result
    (git_version.read_last_update)."""
    mode = mode or restart_mode()
    if _request_pending(last_update):
        return True
    if mode != "update":
        return False
    state = update_unit_state()
    if state is not None:
        return state in _UNIT_BUSY_STATES
    return _running_record(last_update)


def _already_under_way(mode: str, units: list[str]) -> dict:
    return {
        "ok": False,
        "mode": mode,
        "units": units,
        "delay_sec": None,
        "error": "a restart or update is already under way; wait for it to finish",
        "in_progress": True,
    }


def _spawn_restart(mode: str | None = None) -> None:
    """Fire the restart. ``--no-block`` returns immediately instead of waiting
    on units that are about to kill this very process.

    The outcome is LOGGED rather than discarded. The endpoint has already
    answered "ok" by the time this runs — all it knew was that a restart had
    been scheduled — so if sudo refuses here, the journal is the only place
    anyone can find out. Silence looks identical to success from the UI.

    A restart that never left this process is no longer under way: the
    record :func:`restart` kept is dropped, so the next press is not
    refused for it."""
    mode = mode or restart_mode()
    systemctl, sudo = _systemctl(), _sudo()
    if systemctl is None or sudo is None:  # pragma: no cover — capable() gates
        _forget_request()
        log.error("self-restart: systemctl or sudo vanished between probe and fire")
        return
    cmd = [sudo, "-n", systemctl, *_action(mode)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_PROBE_TIMEOUT_SEC, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        _forget_request()
        log.error("self-restart failed to spawn: %s", e)
        return
    if proc.returncode != 0:
        _forget_request()
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

    Returns ``{"ok", "mode", "units", "delay_sec", "error", "in_progress"}``
    and never raises — an incapable host reports why instead of
    half-restarting, and while a restart or update is already under way
    (:func:`underway`) the answer is ``ok: false`` with ``in_progress:
    true`` and nothing is scheduled: a second press would bounce the
    services out from under the first, or race it for the update unit.
    """
    # git_version imports this module; it owns reading the unit's result.
    from domovoi import git_version

    mode = restart_mode()
    units = _units(mode)
    last = await asyncio.to_thread(git_version.read_last_update) if mode == "update" else None
    if await asyncio.to_thread(underway, last, mode):
        return _already_under_way(mode, units)
    ok, why = await capable_async()
    if not ok:
        return {"ok": False, "mode": mode, "units": units, "delay_sec": None,
                "error": why, "in_progress": False}
    # Again, with no await between the look and the record: two presses
    # that both got past the check above must not both schedule.
    if _request_pending(last):
        return _already_under_way(mode, units)
    _note_request(mode)

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
        "in_progress": False,
    }
