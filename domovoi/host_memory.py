"""Where the Domovoi server's memory goes: Settings → Health's "memory" row.

An admin READ (``GET /v1/admin/memory``, the dashboard's
``GET /api/models/memory``), like the disk row beside it and the container
guard's status: what runs on the box is an operator's business.

On a Linux host, the Windows installer's WSL distro included, it reads:

* ``/proc/meminfo``: the total, what programs use (``MemTotal`` minus
  ``MemAvailable``), the file cache (``Cached + Buffers - Shmem``: files
  the kernel keeps in memory and hands back the moment a program needs
  it), shared memory (``Shmem``) and swap in use;
* each Domovoi systemd unit's memory (``systemctl show -p MemoryCurrent``):
  the core, the dashboard, the updater, and Ollama when it runs as a unit
  on this host;
* each running Docker container's memory (``docker stats --no-stream``,
  the CLI the core already drives), under a plain label: the music player
  for a room, the database, the search helper, a plugin's container, the
  wake-word trainer. The container guard's last check names what it
  watches, so its labels are reused;
* the few other processes using the most memory, by NAME only (the
  kernel's ``comm``, at most 15 characters). Never the command line: an
  argument can hold a password or a token. Processes inside a container or
  one of the units above are left out, so nothing is counted twice.

Every source has its own time limit, and one that fails or times out is
left out with a note (``missing``); the read itself never fails. The
answer is cached for :data:`CACHE_SEC`, so several open dashboards cost
one look.

On the Windows installer's distro, ``MemTotal`` is the VM's size: the
``memory=`` limit DomovoiHost writes into its ``.wslconfig``
(windows/common/WslConfig.cs), less what the VM's kernel keeps for itself,
so it reads a little under that figure (``host: "wsl"`` tells the page to
say so). The exact figure lives on the Windows side, which the core can't
read; no host channel passes it in today. A Windows or Mac dev core
answers ``available: false``.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CACHE_SEC = 15.0
SYSTEMCTL_TIMEOUT_SEC = 5.0
DOCKER_TIMEOUT_SEC = 10.0
PROCESS_SCAN_BUDGET_SEC = 2.0
PROCESS_SCAN_MAX_PIDS = 20000
TOP_PROCESSES = 5
# Past a source's own limit, how long its worker thread is waited for.
GRACE_SEC = 1.0
# A process group smaller than this isn't worth a line.
PROCESS_MIN_BYTES = 20 * 1024 * 1024

# The units this box may run, with what a person calls them.
UNITS: dict[str, str] = {
    "domovoi-core.service": "Domovoi server",
    "domovoi-web.service": "dashboard",
    "domovoi-update.service": "updater",
    "ollama.service": "AI models (Ollama)",
}

WAKE_TRAINER_CONTAINERS = ("domovoi-wake-train", "domovoi-wake-warm")

_KB = 1024
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_UNSET = 2**64 - 1          # systemd's "no value" for MemoryCurrent
_COMM_SAFE = re.compile(r"[^A-Za-z0-9._+@:/ -]")


# ─── /proc/meminfo ────────────────────────────────────────────────────────


def parse_meminfo(text: str) -> dict[str, int]:
    """``{key: bytes}`` from /proc/meminfo's ``Key:   123 kB`` lines. Lines
    it can't read are skipped; a value with no unit is taken as is (the
    HugePages counts)."""
    out: dict[str, int] = {}
    for line in (text or "").splitlines():
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        parts = rest.split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        unit = parts[1].lower() if len(parts) > 1 else ""
        out[key.strip()] = value * _KB if unit == "kb" else value
    return out


def summarize(mi: dict[str, int]) -> dict[str, int] | None:
    """The row's numbers, in bytes; None without ``MemTotal``.

    ``programs`` is ``MemTotal - MemAvailable``: what programs hold and
    the kernel can't simply drop. A kernel too old for ``MemAvailable``
    (before 3.14) gets ``MemFree + Cached + Buffers`` instead. ``cache`` is
    ``Cached + Buffers - Shmem``: shared memory sits in ``Cached`` but
    can't be dropped, so it is shown on its own."""
    total = mi.get("MemTotal")
    if not total:
        return None
    free = mi.get("MemFree", 0)
    cached = mi.get("Cached", 0)
    buffers = mi.get("Buffers", 0)
    shmem = mi.get("Shmem", 0)
    available = mi.get("MemAvailable")
    if available is None:
        available = free + cached + buffers
    available = max(0, min(total, available))
    swap_total = mi.get("SwapTotal", 0)
    swap_free = mi.get("SwapFree", swap_total)
    return {
        "total_bytes": total,
        "programs_bytes": max(0, total - available),
        "cache_bytes": max(0, cached + buffers - shmem),
        "shared_bytes": max(0, shmem),
        "free_bytes": max(0, free),
        "available_bytes": available,
        "swap_total_bytes": max(0, swap_total),
        "swap_used_bytes": max(0, swap_total - swap_free),
    }


# ─── systemd units ────────────────────────────────────────────────────────


def parse_systemctl_show(out: str) -> list[dict[str, Any]]:
    """``systemctl show -p Id -p LoadState -p ActiveState -p MemoryCurrent
    a b c``: one block per unit, blank-line separated. Returns the loaded,
    active units with a memory value."""
    rows: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", (out or "").strip()):
        props: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key.strip()] = value.strip()
        unit = props.get("Id", "")
        if not unit or props.get("LoadState", "loaded") != "loaded":
            continue
        if props.get("ActiveState") not in ("active", "activating", "reloading", "deactivating"):
            continue
        try:
            mem = int(props.get("MemoryCurrent", ""))
        except ValueError:
            continue
        if mem <= 0 or mem >= _UNSET:
            continue
        rows.append({"unit": unit, "bytes": mem})
    return rows


# ─── Docker containers ────────────────────────────────────────────────────

_SIZE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGTP]?i?B)\s*$", re.IGNORECASE)
_FACTORS = {
    "b": 1,
    "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4, "pb": 1000**5,
    "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4, "pib": 1024**5,
}


def parse_size(text: str) -> int | None:
    """``"512.3MiB"`` → bytes; None when it isn't a size."""
    m = _SIZE.match(text or "")
    if not m:
        return None
    factor = _FACTORS.get(m.group(2).lower())
    if factor is None:
        return None
    return int(float(m.group(1)) * factor)


def parse_docker_stats(out: str) -> list[tuple[str, int]]:
    """``docker stats --no-stream --format '{{.Name}}\\t{{.MemUsage}}'``:
    ``[(name, bytes)]``. MemUsage reads ``used / limit``; the limit is
    dropped. Lines it can't read are skipped."""
    rows: list[tuple[str, int]] = []
    for line in (out or "").splitlines():
        name, sep, usage = line.partition("\t")
        name = name.strip().lstrip("/")
        if not sep or not name:
            continue
        used = parse_size(usage.split("/")[0])
        if used is None:
            continue
        rows.append((name, used))
    return rows


def _plain(label: str) -> str:
    label = (label or "").strip()
    return label[4:] if label.lower().startswith("the ") else label


def container_label(name: str, *, mpd_prefix: str, guard_labels: dict[str, str]) -> str:
    """What a person calls a container. The container guard's label when
    it watches it ("the music player for kitchen" → "music player for
    kitchen"), else by its name: the music player prefix, the compose
    project's postgres / searxng services, the wake-word trainer. Anything
    else is "other container"."""
    if name in guard_labels and guard_labels[name]:
        return _plain(guard_labels[name])
    if mpd_prefix and name.startswith(mpd_prefix) and len(name) > len(mpd_prefix):
        return f"music player for {name[len(mpd_prefix):]}"
    if name in WAKE_TRAINER_CONTAINERS:
        return "wake-word trainer"
    low = name.lower()
    if re.search(r"(^|[-_])postgres([-_]\d+)?$", low):
        return "database"
    if re.search(r"(^|[-_])searxng([-_]\d+)?$", low):
        return "web search helper"
    if re.search(r"(^|[-_])flyway", low):
        return "database migrations"
    return "other container"


# ─── other processes ──────────────────────────────────────────────────────

_CONTAINER_CGROUP = ("docker-", "/docker/", "containerd", "libpod-", "/kubepods")


def _comm(raw: str) -> str:
    """A process name fit to show: the kernel's ``comm`` (never the
    command line), printable characters only."""
    name = _COMM_SAFE.sub("", (raw or "").strip())[:15].strip()
    return name or "unnamed"


def _rss_bytes(status_text: str) -> int | None:
    for line in status_text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            try:
                return int(parts[1]) * _KB
            except (IndexError, ValueError):
                return None
    return None


def scan_processes(
    proc_root: Path,
    *,
    skip_units: tuple[str, ...] = tuple(UNITS),
    budget_sec: float = PROCESS_SCAN_BUDGET_SEC,
    max_pids: int = PROCESS_SCAN_MAX_PIDS,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[dict[str, Any]], bool]:
    """``([{name, bytes, count}], complete)``: memory (resident set) by
    process name, largest first, of the processes that are NOT in a
    container or one of ``skip_units`` (those are counted above).

    Reads ``comm``, ``status`` and ``cgroup`` per process and nothing
    else: never ``cmdline`` or ``environ``. Bounded by ``budget_sec`` and
    ``max_pids``; ``complete`` is False when it stopped early. RSS counts
    pages a process shares with others once per process, so a group is an
    upper bound."""
    started = clock()
    groups: dict[str, list[int]] = {}
    complete = True
    seen = 0
    try:
        entries = os.scandir(proc_root)
    except OSError:
        return [], False
    with entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            seen += 1
            if seen > max_pids or clock() - started > budget_sec:
                complete = False
                break
            base = Path(entry.path)
            try:
                rss = _rss_bytes((base / "status").read_text(encoding="utf-8", errors="replace"))
                if not rss:
                    continue    # a kernel thread, or gone
                cgroup = (base / "cgroup").read_text(encoding="utf-8", errors="replace")
                if any(m in cgroup for m in _CONTAINER_CGROUP):
                    continue
                if any(f"/{u}" in cgroup for u in skip_units):
                    continue
                name = _comm((base / "comm").read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue    # exited, or not ours to read
            g = groups.setdefault(name, [0, 0])
            g[0] += rss
            g[1] += 1
    rows = [{"name": n, "bytes": b, "count": c} for n, (b, c) in groups.items()]
    rows.sort(key=lambda r: r["bytes"], reverse=True)
    return rows, complete


# ─── running the sources ──────────────────────────────────────────────────


@dataclass
class Sources:
    """Where a snapshot looks. Tests swap any of these."""

    proc_root: Path = Path("/proc")
    systemctl: Callable[[list[str], float], tuple[int, str, str]] | None = None
    docker: Callable[[list[str], float], tuple[int, str, str]] | None = None
    guard_labels: Callable[[], dict[str, str]] | None = None
    mpd_prefix: str | None = None
    host_kind: str | None = None


def _run(exe: str) -> Callable[[list[str], float], tuple[int, str, str]] | None:
    path = shutil.which(exe)
    if path is None:
        return None

    def run(args: list[str], timeout: float) -> tuple[int, str, str]:
        proc = subprocess.run(
            [path, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, creationflags=_NO_WINDOW,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    return run


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


async def _bounded(fn: Callable[[], Any], timeout: float) -> Any:
    """``fn`` in a worker thread, given up on after ``timeout`` (the
    subprocess has its own limit too; this one covers a stuck read)."""
    return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)


async def _units(src: Sources) -> list[dict[str, Any]]:
    run = src.systemctl
    if run is None:
        raise RuntimeError("systemd isn't running this server")
    args = ["show", "-p", "Id", "-p", "LoadState", "-p", "ActiveState", "-p", "MemoryCurrent", *UNITS]
    rc, out, err = await _bounded(lambda: run(args, SYSTEMCTL_TIMEOUT_SEC), SYSTEMCTL_TIMEOUT_SEC + GRACE_SEC)
    if rc != 0 and not out.strip():
        raise RuntimeError(_first_line(err) or f"systemctl exited {rc}")
    return [
        {"kind": "service", "name": r["unit"], "label": UNITS.get(r["unit"], r["unit"]), "bytes": r["bytes"]}
        for r in parse_systemctl_show(out)
    ]


async def _containers(src: Sources) -> list[dict[str, Any]]:
    run = src.docker
    if run is None:
        raise RuntimeError("Docker isn't installed here")
    args = ["stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"]
    rc, out, err = await _bounded(lambda: run(args, DOCKER_TIMEOUT_SEC), DOCKER_TIMEOUT_SEC + GRACE_SEC)
    if rc != 0:
        raise RuntimeError(_first_line(err) or f"docker stats exited {rc}")
    try:
        labels = src.guard_labels() if src.guard_labels else {}
    except Exception:  # noqa: BLE001 - labels are a nicety; names still work
        labels = {}
    prefix = src.mpd_prefix or ""
    return [
        {"kind": "container", "name": name,
         "label": container_label(name, mpd_prefix=prefix, guard_labels=labels), "bytes": used}
        for name, used in parse_docker_stats(out)
    ]


async def _processes(src: Sources) -> tuple[list[dict[str, Any]], bool]:
    rows, complete = await _bounded(
        lambda: scan_processes(src.proc_root), PROCESS_SCAN_BUDGET_SEC + GRACE_SEC
    )
    top = [r for r in rows if r["bytes"] >= PROCESS_MIN_BYTES][:TOP_PROCESSES]
    return [{"kind": "process", "name": r["name"], "label": r["name"], "bytes": r["bytes"],
             "count": r["count"]} for r in top], complete


def _why(e: BaseException) -> str:
    if isinstance(e, (asyncio.TimeoutError, subprocess.TimeoutExpired)):
        return "it didn't answer in time"
    return str(e) or type(e).__name__


async def snapshot(src: Sources | None = None) -> dict[str, Any]:
    """The whole answer (module docstring). Never raises."""
    src = src or default_sources()
    kind = src.host_kind or "linux"
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind not in ("linux", "wsl"):
        return {"available": False, "host": kind, "checked_at": checked_at,
                "why": "not available on this system"}
    try:
        text = await _bounded(
            lambda: (src.proc_root / "meminfo").read_text(encoding="utf-8", errors="replace"), 3.0
        )
        summary = summarize(parse_meminfo(text))
    except (OSError, asyncio.TimeoutError):
        summary = None
    if summary is None:
        return {"available": False, "host": kind, "checked_at": checked_at,
                "why": "this system's memory figures couldn't be read"}

    missing: list[dict[str, str]] = []
    results = await asyncio.gather(_units(src), _containers(src), _processes(src),
                                   return_exceptions=True)
    breakdown: list[dict[str, Any]] = []
    for source, result in zip(("services", "containers", "processes"), results):
        if isinstance(result, BaseException):
            missing.append({"source": source, "why": _why(result)})
            continue
        if source == "processes":
            rows, complete = result
            if not complete:
                missing.append({"source": source, "why": "only some processes were looked at in time"})
            breakdown.extend(rows)
        else:
            breakdown.extend(result)
    breakdown.sort(key=lambda r: r["bytes"], reverse=True)
    doc: dict[str, Any] = {
        "available": True,
        "host": kind,
        "checked_at": checked_at,
        **summary,
        "breakdown": breakdown,
        "missing": missing,
    }
    return doc


def _guard_labels() -> dict[str, str]:
    from domovoi.container_guard import engine

    status = engine.get_guard().status or {}
    return {str(w.get("name")): str(w.get("label") or "")
            for w in status.get("watched") or [] if isinstance(w, dict) and w.get("name")}


def default_sources() -> Sources:
    from domovoi.config import settings
    from domovoi.host_kind import host_kind

    linux = sys.platform.startswith("linux")
    return Sources(
        proc_root=Path("/proc"),
        systemctl=_run("systemctl") if linux else None,
        docker=_run("docker") if linux else None,
        guard_labels=_guard_labels,
        mpd_prefix=getattr(settings, "mpd_container_prefix", "") or "",
        host_kind=host_kind(),
    )


_CACHE: tuple[float, dict[str, Any]] | None = None
_LOCK: asyncio.Lock | None = None


async def cached_snapshot() -> dict[str, Any]:
    """:func:`snapshot`, at most once per :data:`CACHE_SEC`; callers that
    arrive while one runs share it."""
    global _CACHE, _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    async with _LOCK:
        now = time.monotonic()
        if _CACHE is not None and now - _CACHE[0] < CACHE_SEC:
            return _CACHE[1]
        doc = await snapshot()
        _CACHE = (time.monotonic(), doc)
        return doc
