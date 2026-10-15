"""Restart a core that doesn't answer, from the dashboard (incident 2026-10-14).

The Version card's Restart asks the CORE to restart itself
(``/v1/admin/version/restart``, domovoi/self_restart.py). A core that is
frozen can't answer that, so on 2026-10-14 the dashboard was up, the core
wasn't, and the only fix was SSH or a power cycle. The core now restarts
itself when it notices (domovoi/loop_watchdog.py); this is the button for
when it doesn't: the web process ends the core and lets the service
manager start it again.

How, in order of preference (:func:`plan`):

1. **Signal** (``how: "signal"``): the core runs as a systemd unit whose
   ``Restart=`` starts it again after a kill (``always``, ``on-failure``,
   ``on-abnormal``, ``on-abort``), and this process may signal its main
   process, i.e. both run as the same account, as every install the repo
   sets up does. The core gets SIGUSR1 first (faulthandler, registered by
   the core's loop watchdog, prints every thread's stack to the journal),
   then SIGKILL, and systemd starts it ``RestartSec=`` later. No privilege
   is used or needed: the web unit keeps ``NoNewPrivileges=yes`` and its
   full sandbox, and no sudoers rule is involved. The update path adds
   ``Restart=on-failure`` to a hand-made core unit that lacks it
   (scripts/linux/apply-update.sh, reconcile_core_restart).
2. **Sudo** (``how: "sudo"``): the core isn't running (no main process),
   or isn't this account's, but this process can use the restart grant the
   core's own Restart uses (domovoi/self_restart.py: the update unit, or
   the plain bounce of core and web). Only where the web unit lets sudo
   run at all; the shipped one does not (``NoNewPrivileges=yes``).
3. Neither: the reason, and the command to run by hand.

Never while the core answers (``/v1/health`` within
:data:`PROBE_TIMEOUT_SEC`: the ordinary Restart is the one to use then),
and never while it is starting: a ``Type=notify`` core that hasn't said
READY yet, or one whose process is younger than :data:`STARTING_GRACE_SEC`.
The caller is the security tier (an admin Bearer checked against the
shared ``admin_sessions`` table, 501 before setup): the route applies it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

from domovoi import loop_watchdog, self_restart

log = logging.getLogger(__name__)

CORE_UNIT = "domovoi-core.service"
# Restart= values under which systemd starts a killed main process again
# (systemd.service(5): SIGKILL is an "unclean signal").
RESTARTS_AFTER_A_KILL = frozenset({"always", "on-failure", "on-abnormal", "on-abort"})
# "Not answering" means no 200 from /v1/health within this.
PROBE_TIMEOUT_SEC = 3.0
# A core process younger than this may just be loading its models.
STARTING_GRACE_SEC = 120.0
# Between the stack dump (SIGUSR1) and the kill.
DUMP_GRACE_SEC = 1.0
# How long the kill gets to show (the process gone, or systemd on a new one).
KILL_WAIT_SEC = 5.0
_SYSTEMCTL_TIMEOUT_SEC = 5.0
_WINDOWS = sys.platform == "win32"

# One restart at a time: the main PID this process last ended, and when.
_LAST_KILL: tuple[int, float] | None = None
_LAST_KILL_HOLD_SEC = 60.0


def core_url() -> str:
    return os.environ.get("DOMOVOI_URL", "http://localhost:6370").rstrip("/")


async def core_answers(timeout: float = PROBE_TIMEOUT_SEC) -> bool:
    """Whether the core's /v1/health says 200 within ``timeout``."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{core_url()}/v1/health")
        return r.status_code == 200
    except Exception:  # noqa: BLE001 - refused, timed out, reset: not answering
        return False


@dataclass(frozen=True)
class CoreUnit:
    """What systemd says about domovoi-core.service."""

    load_state: str
    active_state: str
    sub_state: str
    main_pid: int
    restart: str
    service_type: str
    started_monotonic_sec: float | None


def _systemctl() -> str | None:
    return None if _WINDOWS else shutil.which("systemctl")


def core_unit() -> CoreUnit | None:
    """Blocking. The core unit's state, read over the system bus without
    privilege (``systemctl show``), or None without systemd or the unit."""
    systemctl = _systemctl()
    if systemctl is None:
        return None
    props = ("LoadState", "ActiveState", "SubState", "MainPID", "Restart", "Type",
             "ExecMainStartTimestampMonotonic")
    try:
        proc = subprocess.run(
            [systemctl, "show", CORE_UNIT, *(f"--property={p}" for p in props)],
            capture_output=True, text=True, timeout=_SYSTEMCTL_TIMEOUT_SEC, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    got: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            got[key.strip()] = value.strip()
    if got.get("LoadState") in (None, "", "not-found", "masked"):
        return None
    try:
        pid = int(got.get("MainPID") or 0)
    except ValueError:
        pid = 0
    try:
        started = int(got.get("ExecMainStartTimestampMonotonic") or 0) / 1_000_000 or None
    except ValueError:
        started = None
    return CoreUnit(
        load_state=got.get("LoadState", ""),
        active_state=got.get("ActiveState", ""),
        sub_state=got.get("SubState", ""),
        main_pid=pid,
        restart=got.get("Restart", ""),
        service_type=got.get("Type", ""),
        started_monotonic_sec=started,
    )


def _may_signal(pid: int) -> bool:
    """Whether this process may signal ``pid`` (same account): signal 0
    checks without sending anything."""
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return False
    except OSError:
        return False


def _looks_like_the_core(pid: int) -> bool:
    """The process is a Python running ``domovoi.main``: never signal a
    PID that systemd's answer and a reuse could have put out of step."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().split(b"\0")
    except OSError:
        return False
    return any(b"domovoi.main" in a or a.endswith(b"domovoi/main.py") for a in argv)


def manual_command() -> str | None:
    return None if _systemctl() is None else "sudo systemctl restart domovoi-core"


def plan() -> dict[str, Any]:
    """Blocking. What this process can do about a core that doesn't
    answer: ``{"capable", "how", "why", "starting", "manual_command"}``.
    ``how`` is "signal", "sudo" or None; ``why`` says why not (or why a
    restart now would be wrong: ``starting``)."""
    out: dict[str, Any] = {"capable": False, "how": None, "why": None, "starting": False,
                           "manual_command": manual_command()}
    unit = core_unit()
    if unit is None:
        out["why"] = ("the core doesn't run as the systemd unit domovoi-core.service on this "
                      "server, so the dashboard can't restart it")
        return out
    if unit.active_state in ("activating", "reloading") or unit.sub_state in ("start", "start-pre", "auto-restart"):
        out["starting"] = True
        out["why"] = "the core is starting; give it a minute"
        return out
    pid = unit.main_pid
    if pid > 0 and unit.started_monotonic_sec is not None:
        age = time.monotonic() - unit.started_monotonic_sec
        if 0 <= age < STARTING_GRACE_SEC and unit.service_type != "notify":
            out["starting"] = True
            out["why"] = f"the core started {age:.0f} s ago and may still be loading; give it a minute"
            return out
    if _LAST_KILL is not None and _LAST_KILL[0] == pid and time.monotonic() - _LAST_KILL[1] < _LAST_KILL_HOLD_SEC:
        out["starting"] = True
        out["why"] = "a restart is already under way"
        return out
    if pid > 0 and unit.restart in RESTARTS_AFTER_A_KILL and _may_signal(pid) and _looks_like_the_core(pid):
        out.update(capable=True, how="signal")
        return out
    ok, _hint = self_restart.capable()
    if ok:
        out.update(capable=True, how="sudo")
        return out
    if pid <= 0:
        out["why"] = f"the core isn't running (domovoi-core.service is {unit.active_state})"
    elif unit.restart not in RESTARTS_AFTER_A_KILL:
        out["why"] = (f"domovoi-core.service has Restart={unit.restart or 'no'}, so systemd would not "
                      "start it again after the dashboard ended it (docs/LINUX_HOST.md, The loop watchdog)")
    else:
        out["why"] = ("the core runs under another account than the dashboard, so the dashboard "
                      "can't end it")
    return out


def _gone(pid: int) -> bool:
    """The process ``pid`` has ended (or is a zombie waiting for systemd)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            state = f.read().rsplit(b")", 1)[-1].split()[0]
        return state in (b"Z", b"X")
    except (OSError, IndexError):
        return True


def end_core(pid: int) -> bool:
    """Blocking. Stack dump, then SIGKILL; True once ``pid`` is gone."""
    global _LAST_KILL
    _LAST_KILL = (pid, time.monotonic())
    log.warning("core restart: the core (pid %d) isn't answering; ending it so systemd starts it again", pid)
    try:
        os.kill(pid, signal.SIGUSR1)
        time.sleep(DUMP_GRACE_SEC)
    except OSError:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError as e:
        log.error("core restart: could not end pid %d: %s", pid, e)
        return False
    deadline = time.monotonic() + KILL_WAIT_SEC
    while time.monotonic() < deadline:
        if _gone(pid):
            break
        time.sleep(0.1)
    if not _gone(pid):
        return False
    # The Version card's "last restarted for freezing" line, the core's own
    # note (it writes the same file when its watchdog ends it); the stacks
    # SIGUSR1 printed are in its faulthandler log, moved into the retained
    # log at its next start.
    loop_watchdog.note_last_stall(
        "the dashboard restarted the core: it did not answer /v1/health", pid=pid)
    return True


async def restart_unanswering_core() -> dict[str, Any]:
    """Restart the core if it doesn't answer. Never raises. Returns
    ``{"ok", "how", "error", "answering", "starting", "manual_command"}``."""
    if await core_answers():
        return {"ok": False, "how": None, "answering": True, "starting": False,
                "manual_command": None,
                "error": "the core is answering; use Restart Domovoi instead"}
    p = await asyncio.to_thread(plan)
    base = {"answering": False, "starting": p["starting"], "manual_command": p["manual_command"]}
    if not p["capable"]:
        return {**base, "ok": False, "how": None, "error": p["why"]}
    if p["how"] == "signal":
        unit = await asyncio.to_thread(core_unit)
        pid = unit.main_pid if unit is not None else 0
        if pid <= 0:
            return {**base, "ok": False, "how": None, "error": "the core stopped meanwhile"}
        ended = await asyncio.to_thread(end_core, pid)
        if not ended:
            return {**base, "ok": False, "how": "signal",
                    "error": f"the core (pid {pid}) did not end; restart it by hand"}
        return {**base, "ok": True, "how": "signal", "error": None}
    res = await self_restart.restart()
    return {**base, "ok": bool(res.get("ok")), "how": "sudo", "error": res.get("error"),
            "starting": bool(res.get("in_progress"))}


async def status() -> dict[str, Any]:
    """For the dashboard: whether the core answers, and if not, what the
    button would do. ``{"answering", "capable", "how", "why", "starting",
    "manual_command"}``."""
    if await core_answers():
        return {"answering": True, "capable": False, "how": None, "why": None,
                "starting": False, "manual_command": None}
    return {"answering": False, **(await asyncio.to_thread(plan))}
