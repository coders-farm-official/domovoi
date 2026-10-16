"""Restart a core that is frozen, from the dashboard (incident 2026-10-14).

The Version card's Restart asks the CORE to restart itself
(``/v1/admin/version/restart``, domovoi/self_restart.py). A core that is
frozen can't answer that, so on 2026-10-14 the dashboard was up, the core
wasn't, and the only fix was SSH or a power cycle. The core now restarts
itself when it notices (domovoi/loop_watchdog.py); this is the button for
when even that failed: the web process ends the core and lets the service
manager start it again.

**Only a core that is truly unresponsive is ever ended.** "Unresponsive"
is about the event loop, not about health: the core's ``/v1/alive`` is
answered by the loop itself, touching no database, and ANY HTTP answer
there (a 404 from an older core included) means the loop runs. A core
that answers ``/v1/health`` slowly, or with 503 because its database is
down, is alive: restarting it fixes nothing, so the dashboard says what is
wrong instead (``db_down``). The core must stay silent for
:func:`silence_needed_sec`, longer than its own loop watchdog waits before
it ends itself (``LOOP_WATCHDOG_STALL_SEC`` + :data:`SILENCE_MARGIN_SEC`),
so the button acts only once the core's own watchdog has failed. The
silence is counted per main PID from the first probe that got nothing
(the status read the page polls is one), so a press after the page has
watched the core stay silent acts at once; a fresh press waits it out,
probing every second, and gives up the moment the core answers.

How, in order of preference (:func:`plan`):

1. **Signal** (``how: "signal"``): the core runs as a systemd unit whose
   ``Restart=`` starts it again after a kill (``always``, ``on-failure``,
   ``on-abnormal``, ``on-abort``), and this process may signal its main
   process, i.e. both run as the same account, as every install the repo
   sets up does. The core gets SIGUSR1 first (faulthandler, registered by
   the core's loop watchdog, writes every thread's stack to its
   faulthandler log), then SIGKILL, and systemd starts it ``RestartSec=``
   later. No privilege is used or needed: the web unit keeps
   ``NoNewPrivileges=yes`` and its full sandbox, and no sudoers rule is
   involved.
2. **Sudo** (``how: "sudo"``): the core isn't running (no main process),
   or isn't this account's, but this process can use the restart grant the
   core's own Restart uses (domovoi/self_restart.py). Only where the web
   unit lets sudo run at all; the shipped one does not.
3. Neither: the reason, and the command to run by hand.

Never while the core is starting: a ``Type=notify`` core that hasn't said
READY yet, or, under any other unit, a process that hasn't left its ready
note (loop_watchdog.ready_path) and is younger than the core's own startup
limit (``LOOP_WATCHDOG_STARTUP_SEC``). Every guard runs again on a fresh
read of the unit right before the kill, against the same main PID, and
the PID is checked once more between the dump and the kill. The caller is
the security tier (an admin Bearer checked against the shared
``admin_sessions`` table, 501 before setup): the route applies it.
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
# One probe of /v1/alive gets this long to bring any HTTP answer.
PROBE_TIMEOUT_SEC = 3.0
# How often a press re-probes while it waits out the silence.
PROBE_EVERY_SEC = 1.0
# Beyond the core's own stall threshold: its watchdog's exit, systemd's
# RestartSec and the start of the next process all fit in it.
SILENCE_MARGIN_SEC = 15.0
# Between the stack dump (SIGUSR1) and the kill.
DUMP_GRACE_SEC = 1.0
# How long the kill gets to show (the process gone, or systemd on a new one).
KILL_WAIT_SEC = 5.0
_SYSTEMCTL_TIMEOUT_SEC = 5.0
_WINDOWS = sys.platform == "win32"

# One restart at a time: the main PID this process last ended, and when.
_LAST_KILL: tuple[int, float] | None = None
_LAST_KILL_HOLD_SEC = 60.0
# Main PID -> time.monotonic() of the first probe that got no answer from
# it, since the last one that did. Cleared whenever any answer comes.
_SILENT_SINCE: dict[int, float] = {}
# One press at a time: a second press (another admin, another tab) while
# one is waiting out the silence or ending the core is told so, rather
# than signalling the same process again or ending the new one.
_PRESS_UNDER_WAY = False


def core_url() -> str:
    return os.environ.get("DOMOVOI_URL", "http://localhost:6370").rstrip("/")


def _core_setting(name: str, default: float) -> float:
    """One of the core's LOOP_WATCHDOG_* settings, as this process read
    them from the same domovoi/.env (the web's copy of the core's settings,
    preloaded); the environment, then ``default``, without them."""
    try:
        from domovoi.config import settings as core_settings

        return float(getattr(core_settings, name))
    except Exception:  # noqa: BLE001 - a stripped-down process: the environment
        try:
            return float(os.environ.get(name.upper(), default))
        except ValueError:
            return default


def silence_needed_sec() -> float:
    """How long the core must give no answer at all before the dashboard
    may end it: longer than its own loop watchdog waits (30 s by default),
    whether or not that watchdog is on."""
    return max(30.0, _core_setting("loop_watchdog_stall_sec", 30.0)) + SILENCE_MARGIN_SEC


def startup_limit_sec() -> float:
    """How long a core may take to start before the dashboard counts it as
    stuck there rather than starting: the core's own limit."""
    limit = _core_setting("loop_watchdog_startup_sec", 900.0)
    return limit if limit > 0 else 900.0


@dataclass(frozen=True)
class Probe:
    """One look at the core: ``alive`` when ``/v1/alive`` brought any HTTP
    answer; ``health`` the status ``/v1/health`` gave (None: no answer, or
    not asked); ``db_down`` when that was the database's 503."""

    alive: bool
    health: int | None = None
    db_down: bool = False


async def _get(client: httpx.AsyncClient, path: str) -> httpx.Response | None:
    try:
        return await client.get(f"{core_url()}{path}")
    except Exception:  # noqa: BLE001 - refused, timed out, reset: no answer
        return None


async def probe_core(timeout: float = PROBE_TIMEOUT_SEC, *, with_health: bool = True) -> Probe:
    """Is the core's loop running (any answer on /v1/alive), and if so,
    what does its health say?"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        alive = await _get(client, "/v1/alive")
        if alive is None:
            return Probe(alive=False)
        if not with_health:
            return Probe(alive=True)
        health = await _get(client, "/v1/health")
    if health is None:
        return Probe(alive=True)
    db_down = health.status_code == 503 and "db unreachable" in health.text
    return Probe(alive=True, health=health.status_code, db_down=db_down)


def _note_probe(pid: int, alive: bool) -> float:
    """Record a probe against ``pid``; how long it has been silent (0 when
    it answered)."""
    now = time.monotonic()
    if alive:
        _SILENT_SINCE.clear()
        return 0.0
    for other in [p for p in _SILENT_SINCE if p != pid]:
        del _SILENT_SINCE[other]
    since = _SILENT_SINCE.setdefault(pid, now)
    return now - since


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
    checks without sending anything. Never on Windows, where os.kill with
    any number but the console events terminates the process."""
    if pid <= 0 or _WINDOWS:
        return False
    try:
        os.kill(pid, 0)
        return True
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


def process_uptime_sec(pid: int) -> float | None:
    """How long ``pid`` has run (Linux ``/proc``), or None."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            fields = f.read().rsplit(b")", 1)[1].split()
        start_ticks = int(fields[19])   # starttime, field 22 of stat(5)
        with open("/proc/uptime", "rb") as f:
            boot_sec = float(f.read().split()[0])
        return max(0.0, boot_sec - start_ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, IndexError, ValueError, AttributeError):
        return None


def _said_ready(pid: int) -> bool:
    """The core process ``pid`` finished starting (its ready note)."""
    note = loop_watchdog.read_ready()
    return bool(note and note.get("pid") == pid)


def manual_command() -> str | None:
    return None if _systemctl() is None else "sudo systemctl restart domovoi-core"


def plan() -> dict[str, Any]:
    """Blocking. What this process could do to a core that doesn't answer:
    ``{"capable", "how", "why", "starting", "manual_command", "pid"}``.
    ``how`` is "signal", "sudo" or None; ``why`` says why not (or why a
    restart now would be wrong: ``starting``). Every guard reads the system
    afresh: call it again right before acting."""
    out: dict[str, Any] = {"capable": False, "how": None, "why": None, "starting": False,
                           "manual_command": manual_command(), "pid": 0}
    unit = core_unit()
    if unit is None:
        out["why"] = ("the core doesn't run as the systemd unit domovoi-core.service on this "
                      "server, so the dashboard can't restart it")
        return out
    pid = unit.main_pid
    out["pid"] = pid
    if unit.active_state in ("activating", "reloading") or unit.sub_state in ("start", "start-pre", "auto-restart"):
        out["starting"] = True
        out["why"] = "the core is starting; give it a minute"
        return out
    if pid > 0 and unit.service_type != "notify" and not _said_ready(pid):
        uptime = process_uptime_sec(pid)
        if uptime is not None and uptime < startup_limit_sec():
            out["starting"] = True
            out["why"] = (f"the core started {uptime:.0f} s ago and hasn't finished starting; "
                          f"it ends itself if it hasn't after {startup_limit_sec():.0f} s")
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


def _still_the_core(pid: int) -> bool:
    unit = core_unit()
    return (unit is not None and unit.main_pid == pid and _may_signal(pid)
            and _looks_like_the_core(pid))


def end_core(pid: int) -> bool:
    """Blocking. Stack dump, then SIGKILL; True once ``pid`` is gone. The
    PID is checked to still be the core's before each signal."""
    global _LAST_KILL
    if not _still_the_core(pid):
        log.warning("core restart: pid %d is no longer the core's main process; nothing sent", pid)
        return False
    _LAST_KILL = (pid, time.monotonic())
    log.warning("core restart: the core (pid %d) has not answered for %.0f s; ending it so systemd "
                "starts it again", pid, silence_needed_sec())
    try:
        os.kill(pid, signal.SIGUSR1)
        time.sleep(DUMP_GRACE_SEC)
    except OSError:
        pass
    if not _still_the_core(pid):
        return _gone(pid)
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
    # SIGUSR1 wrote are moved into its retained log at its next start.
    loop_watchdog.note_last_stall(
        "the dashboard restarted the core: it gave no answer at all for "
        f"{silence_needed_sec():.0f} s", pid=pid)
    return True


def _answering(probe: Probe, extra: str = "") -> dict[str, Any]:
    if probe.db_down:
        why = ("the core is running, but its database isn't answering; restarting the core "
               "won't fix that")
    elif probe.health is not None and probe.health != 200:
        why = f"the core is running (its health check says {probe.health}); nothing to restart"
    else:
        why = "the core is answering; use Restart Domovoi instead"
    return {"ok": False, "how": None, "answering": True, "db_down": probe.db_down, "starting": False,
            "manual_command": None, "error": why + extra}


async def restart_unanswering_core() -> dict[str, Any]:
    """Restart the core if, and only if, it is truly unresponsive (see the
    module docstring). Never raises. Can take :func:`silence_needed_sec`
    when the silence hasn't been watched yet. One press at a time: another
    meanwhile is refused. Returns ``{"ok", "how", "error", "answering",
    "db_down", "starting", "manual_command"}``."""
    global _PRESS_UNDER_WAY
    if _PRESS_UNDER_WAY:
        return {"ok": False, "how": None, "answering": False, "db_down": False,
                "starting": False, "manual_command": None,
                "error": "a restart is already under way (another press): wait for it"}
    _PRESS_UNDER_WAY = True
    try:
        return await _restart_unanswering_core()
    finally:
        _PRESS_UNDER_WAY = False


async def _restart_unanswering_core() -> dict[str, Any]:
    probe = await probe_core()
    if probe.alive:
        _note_probe(0, True)
        return _answering(probe)
    p = await asyncio.to_thread(plan)
    base = {"answering": False, "db_down": False, "starting": p["starting"],
            "manual_command": p["manual_command"]}
    if not p["capable"]:
        return {**base, "ok": False, "how": None, "error": p["why"]}
    pid = p["pid"]
    need = silence_needed_sec()
    if pid > 0:
        silent = _note_probe(pid, False)
        while silent < need:
            await asyncio.sleep(PROBE_EVERY_SEC)
            again = await probe_core(with_health=False)
            if again.alive:
                _note_probe(pid, True)
                return _answering(again, f" (it answered again after {silent:.0f} s: busy, not frozen)")
            unit = await asyncio.to_thread(core_unit)
            if unit is None or unit.main_pid != pid:
                return {**base, "ok": False, "how": None, "starting": True,
                        "error": "the core is restarting by itself"}
            silent = _note_probe(pid, False)
        # Every guard again, on a fresh read, against the same process.
        p2 = await asyncio.to_thread(plan)
        if not p2["capable"] or p2["pid"] != pid or p2["how"] != p["how"]:
            return {**base, "ok": False, "how": None, "starting": p2["starting"],
                    "error": p2["why"] or "the core changed meanwhile"}
    if p["how"] == "signal":
        ended = await asyncio.to_thread(end_core, pid)
        if not ended:
            return {**base, "ok": False, "how": "signal",
                    "error": f"the core (pid {pid}) did not end; restart it by hand"}
        return {**base, "ok": True, "how": "signal", "error": None}
    res = await self_restart.restart()
    return {**base, "ok": bool(res.get("ok")), "how": "sudo", "error": res.get("error"),
            "starting": bool(res.get("in_progress"))}


async def status() -> dict[str, Any]:
    """For the dashboard: whether the core's loop answers, and if not, what
    the button would do and how long the core has been silent.
    ``{"answering", "db_down", "health", "capable", "how", "why",
    "starting", "manual_command", "silent_sec", "silence_needed_sec"}``.
    Each read counts toward the silence a press needs."""
    probe = await probe_core()
    need = silence_needed_sec()
    if probe.alive:
        _note_probe(0, True)
        return {"answering": True, "db_down": probe.db_down, "health": probe.health,
                "capable": False, "how": None, "why": None, "starting": False,
                "manual_command": None, "silent_sec": 0, "silence_needed_sec": need}
    p = await asyncio.to_thread(plan)
    silent = _note_probe(p["pid"], False) if p["pid"] > 0 else 0.0
    out = {k: v for k, v in p.items() if k != "pid"}
    return {"answering": False, "db_down": False, "health": None, **out,
            "silent_sec": round(silent), "silence_needed_sec": need}
