"""How much of the PC Domovoi may use: the household's hardware ceiling on a
Windows install (owner, 2026-11-10; docs/WINDOWS_HOST.md, "How much of
this PC Domovoi may use").

On the Windows installer's install Domovoi runs in a private WSL2 VM, and
the VM's size is the hidden account's ``.wslconfig``: ``memory=`` (GB) and
``processors=`` (logical processors), read when that VM starts. The core
can't write a Windows file, so the choice goes through DomovoiHost
(windows/common/HardwareLimits.cs), which polls every 30 s:

* **The report**, ``/var/lib/domovoi-windows/limits.json``, written by
  DomovoiHost as root: this PC's RAM (``ram_gb``) and logical processors,
  each limit's bounds, default, the household's choice and what
  ``.wslconfig`` says now, and what became of the last request. Believed
  only while root owns it (and its folder) and nobody else can write
  either, as ``side.json`` is (domovoi/windows_side.py). No report: not a
  Windows install, or a DomovoiHost from before this feature, and the
  dashboard shows nothing.
* **The request**, ``~/.domovoi/hardware_limits/request.json``, written
  here for an admin (:func:`save`): ``{"v": 1, "memory_gb": N|null,
  "processors": N|null, "restart_id": "<32 hex>"|null}`` (null:
  automatic, the installer's value). Checked here against the report's
  bounds (refused outside them) and again by DomovoiHost, which clamps
  and refuses on its own; it is only data on the Windows side.
* **When it applies.** WSL reads ``.wslconfig`` when the VM starts: after
  Windows restarts, or right away with "Restart now", which writes a new
  ``restart_id`` (DomovoiHost restarts Domovoi's Linux system once for it,
  a couple of minutes offline). :func:`status` says whether the VM runs on
  what ``.wslconfig`` says (``pending``), from the VM's own MemTotal and
  processor count.

On a Linux box there is no such ceiling here (:func:`status` answers
``available: false``): the programs share the machine with nothing else
Domovoi manages.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPORT_FILE = "/var/lib/domovoi-windows/limits.json"
_REPORT_MAX_BYTES = 8192
REQUEST_NAME = "request.json"

#: Below this much memory the dashboard warns (the AI models, Whisper and
#: the rooms' players together); DomovoiHost never goes under 8.
WARN_BELOW_GB = 16
#: What wake-word training needs to see (domovoi/wake_trainer_setup.py).
TRAINING_GB = 24

GIB = 1024 ** 3

#: What DomovoiHost says of the last restart for new limits. "busy": it
#: waits because an update or Restart of Domovoi runs.
RESTART_STATES = ("none", "waiting", "busy", "done", "failed")
_AT_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")

UPDATE_RUNNING = ("an update or restart of Domovoi is running; restart it for the new limits once "
                  "that has finished")


def report_file() -> Path:
    return Path(os.environ.get("DOMOVOI_HOST_LIMITS_REPORT") or REPORT_FILE)


def request_dir() -> Path:
    return Path(os.environ.get("DOMOVOI_HOST_LIMITS_DIR") or (Path.home() / ".domovoi" / "hardware_limits"))


def _root_only(st: os.stat_result) -> bool:
    """Owned by root and writable by nobody else; nothing passes where
    there are no POSIX owners (the tests replace this)."""
    if not hasattr(os, "getuid"):
        return False
    return st.st_uid == 0 and not (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _int(v: Any, lo: int = 0, hi: int = 4096) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def _limit(doc: Any) -> dict[str, int | None] | None:
    if not isinstance(doc, dict):
        return None
    out = {k: _int(doc.get(k)) for k in ("min", "max", "default", "chosen", "in_file")}
    if out["min"] is None or out["max"] is None or out["min"] > out["max"]:
        return None
    return out


def read_report() -> dict[str, Any] | None:
    """The report as DomovoiHost wrote it, its fields checked; None when
    there is none or it can't be believed. Never raises."""
    path = report_file()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("hardware limits: can't open %s: %s", path, e)
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not _root_only(st) or not _root_only(os.stat(path.parent)):
            log.warning("hardware limits: %s isn't root's alone; not believing it", path)
            return None
        if st.st_size > _REPORT_MAX_BYTES:
            return None
        data = os.read(fd, _REPORT_MAX_BYTES + 1)
    except OSError as e:
        log.warning("hardware limits: can't read %s: %s", path, e)
        return None
    finally:
        os.close(fd)
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return None
    mem, cpu = _limit(doc.get("memory_gb")), _limit(doc.get("processors"))
    ram, cores = _int(doc.get("ram_gb"), 1), _int(doc.get("logical_processors"), 1)
    if mem is None or cpu is None or ram is None or cores is None:
        return None
    request = doc.get("request") if doc.get("request") in ("none", "ok", "clamped", "invalid") else "none"
    problem = doc.get("request_problem")
    restart = doc.get("restart") if doc.get("restart") in RESTART_STATES else "none"
    written = doc.get("written_at")
    seen = doc.get("request_at")
    return {
        # The `at` of the request DomovoiHost last read (taken, clamped or
        # refused): how the page knows it has been seen.
        "request_at": seen if isinstance(seen, str) and _AT_RE.fullmatch(seen) else None,
        "ram_gb": ram,
        "logical_processors": cores,
        "memory_gb": mem,
        "processors": cpu,
        "request": request,
        "request_problem": problem if isinstance(problem, str) and len(problem) <= 200 else None,
        "restart": restart,
        "reported_at": written if isinstance(written, str) and len(written) <= 40 else None,
    }


def read_request() -> dict[str, Any] | None:
    """What this core last asked for; None when it never asked."""
    try:
        raw = (request_dir() / REQUEST_NAME).read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("v") != 1:
        return None
    rid = doc.get("restart_id")
    at = doc.get("at")
    return {"memory_gb": _int(doc.get("memory_gb"), 1), "processors": _int(doc.get("processors"), 1),
            "restart_id": rid if isinstance(rid, str) else None,
            "at": at if isinstance(at, str) and _AT_RE.fullmatch(at) else None}


def _running(proc_root: Path = Path("/proc")) -> dict[str, Any]:
    """What the VM runs on now: MemTotal in GiB and the processors this
    kernel sees."""
    mem = None
    try:
        for line in (proc_root / "meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                mem = round(int(line.split()[1]) * 1024 / GIB, 1)
                break
    except (OSError, ValueError, IndexError):
        mem = None
    return {"memory_gb": mem, "processors": os.cpu_count()}


def memory_matches(file_gb: int | None, running_gib: float | None) -> bool:
    """Whether a VM whose MemTotal reads ``running_gib`` runs on a
    ``memory=<file_gb>GB`` cap: the kernel keeps a little for itself (a
    64 GB cap reads about 62.5), so a few percent under counts as the
    same. Unknown either way counts as matching (nothing to say)."""
    if file_gb is None or running_gib is None:
        return True
    return file_gb - max(1.0, file_gb * 0.06) <= running_gib <= file_gb + 0.25


def status(*, report: dict[str, Any] | None = None, running: dict[str, Any] | None = None,
           request: dict[str, Any] | None = None) -> dict[str, Any]:
    """The Health page's "this PC" card (``GET /v1/admin/host-limits``).
    The keyword arguments are for the tests."""
    report = read_report() if report is None else report
    if report is None:
        return {"available": False,
                "why": "only the Windows install has a share of the PC to set (DomovoiHost reports it)"}
    running = _running() if running is None else running
    from_disk = request is None
    request = read_request() if request is None else request
    mem, cpu = report["memory_gb"], report["processors"]
    file_mem = mem["in_file"] if mem["in_file"] is not None else mem["default"]
    file_cpu = cpu["in_file"] if cpu["in_file"] is not None else report["logical_processors"]
    pending_mem = not memory_matches(file_mem, running.get("memory_gb"))
    run_cpu = running.get("processors")
    pending_cpu = bool(file_cpu and run_cpu and file_cpu != run_cpu)
    # The core asked for something DomovoiHost hasn't read yet (it reads
    # every 30 s while Domovoi runs): by the request's `at`, which the
    # report echoes, since a clamped request's numbers never match.
    if request and request.get("at") and report.get("request_at") is not None:
        waiting = request["at"] != report["request_at"]
    else:
        waiting = bool(request) and (request["memory_gb"] != mem["chosen"] or request["processors"] != cpu["chosen"])
    pending = pending_mem or pending_cpu
    # A restart id the VM no longer needs is taken back, so it can't fire
    # later (a DomovoiHost started after it was written, say).
    if from_disk and request and request.get("restart_id") and not pending and not waiting:
        try:
            _write_request({"v": 1, "memory_gb": request["memory_gb"], "processors": request["processors"],
                            "restart_id": None, "at": request.get("at") or _now()})
        except OSError as e:
            log.warning("hardware limits: can't take back a done restart id: %s", e)
    return {
        "available": True,
        "ram_gb": report["ram_gb"],
        "logical_processors": report["logical_processors"],
        "memory_gb": mem,
        "processors": cpu,
        "running": running,
        "pending": pending,
        "waiting_for_host": waiting,
        # What this core last asked for (None: never): the page shows it
        # until DomovoiHost reports it as ``chosen``.
        "requested": ({"memory_gb": request["memory_gb"], "processors": request["processors"]}
                      if request else None),
        "request": report["request"],
        "request_problem": report["request_problem"],
        "restart": report["restart"],
        "reported_at": report["reported_at"],
        "warn_below_gb": WARN_BELOW_GB,
        "training_gb": TRAINING_GB,
    }


class Refused(ValueError):
    """A choice the report's bounds don't allow, or no Windows host."""


class Busy(Refused):
    """A restart asked for while an update or Restart of Domovoi runs."""


def _checked(value: Any, bounds: dict[str, int | None], what: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise Refused(f"{what} must be a whole number")
    lo, hi = bounds["min"], bounds["max"]
    if not (lo <= value <= hi):    # type: ignore[operator]
        raise Refused(f"{what} must be {lo} to {hi} on this PC")
    return value


def save(memory_gb: int | None, processors: int | None, *, restart_now: bool = False,
         update_running=None) -> dict[str, Any]:
    """Write the request for DomovoiHost: the two limits (None: automatic)
    and, with ``restart_now``, a fresh restart id. Refused (``Refused``)
    without a report or outside its bounds. Returns :func:`status`."""
    report = read_report()
    if report is None:
        raise Refused("this server isn't the Windows install, so there is no share of the PC to set")
    doc = {
        "v": 1,
        "memory_gb": _checked(memory_gb, report["memory_gb"], "memory_gb"),
        "processors": _checked(processors, report["processors"], "processors"),
        "restart_id": None,
        "at": _now(),
    }
    if restart_now:
        if (update_running or _update_running)():
            raise Busy(UPDATE_RUNNING)
        doc["restart_id"] = secrets.token_hex(16)
    _write_request(doc)
    log.info("hardware limits: asked DomovoiHost for memory %s, processors %s%s",
             doc["memory_gb"] or "automatic", doc["processors"] or "automatic",
             " and a restart" if restart_now else "")
    return status(report=report)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _update_running() -> bool:
    """An update or Restart of Domovoi runs now (domovoi/self_restart.py's
    own test: the update unit activating, or a restart just accepted). A
    limits restart then would stop the services under it."""
    try:
        from domovoi import git_version, self_restart

        return self_restart.underway(git_version.read_last_update())
    except Exception:  # noqa: BLE001 - can't tell: let DomovoiHost's own check decide
        log.debug("hardware limits: can't tell whether an update runs", exc_info=True)
        return False


def _write_request(doc: dict[str, Any]) -> None:
    d = request_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    tmp = d / f".{REQUEST_NAME}.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write((json.dumps(doc) + "\n").encode("ascii"))
    os.replace(tmp, d / REQUEST_NAME)
