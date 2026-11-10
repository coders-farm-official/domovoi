"""Domovoi's file-cache trim: give memory back that only holds old file reads.

Run as root by domovoi-cache-trim.service, which domovoi-cache-trim.timer
starts every 2 minutes (docs/LINUX_HOST.md, "The file-cache trim"). It is
installed root-owned as /usr/local/lib/domovoi-cache-trim/cache-trim.py by
scripts/linux/provision.sh (units step) and scripts/linux/apply-update.sh
(reconcile_cache_trim, so an existing install gets it with its next core
update). Started as ``python3 -I -S``: no site-packages, no environment,
no current folder on the import path; the standard library only.

Why: the Windows installer runs Domovoi in a private WSL2 VM. Linux keeps
every file it read in memory (the page cache) until something needs the
room, and the VM holds that memory on Windows' side. WSL's own
``autoMemoryReclaim`` only hands it back once the VM is idle, which a house
assistant rarely is; on 2026-11-10 a house showed 51 GB of cache in a 63 GB
VM while programs used 5.3 GB, and Windows paged and compressed around it.
Freed page cache goes back to Windows within minutes.

What one run does:

1. Reads /proc/meminfo. The file cache is ``Cached + Buffers - Shmem``
   (the Health page's figure, domovoi/host_memory.py). What a trim can
   free is the file LRU (``Active(file) + Inactive(file)``, which holds no
   shared memory) less the file pages programs have mapped, at least
   ``Mapped - Shmem`` (``Mapped`` counts mapped shared memory too, so
   taking all of it off would count that twice). Pages a program has
   mapped (an AI model Ollama keeps loaded, say) are in use and stay;
   counting them would trim every 2 minutes for nothing. The trim is due
   when the part it can free is over the limit by at least 256 MiB.
2. On or off: ON inside WSL (the kernel names Microsoft, the rule
   domovoi/host_kind.py uses), OFF on a dedicated Linux box, where the
   cache is harmless (the kernel hands it to programs the moment they
   need it, and nobody else wants the memory). The operator file below
   can switch it either way.
3. The limit: max(4 GiB, 10% of MemTotal) unless the operator file sets
   one. The cache UP TO the limit stays: a few GB keep what Domovoi reads
   again and again fast (the database's working set, the Python libraries,
   Whisper's model, the music being played); old reads beyond it are what
   fills the VM.
4. Skips while an update or restart of Domovoi runs (domovoi-update.service
   activating: the marker domovoi/self_restart.py reads too), and when the
   last trim was under 100 s ago (at most one per timer interval).
5. Trims only the excess, where the kernel can: ``<excess bytes>
   swappiness=0`` into the root cgroup's /sys/fs/cgroup/memory.reclaim
   (cgroup v2; WSL's 6.x kernels have the file). swappiness=0 asks the
   kernel to reclaim file pages, not to push programs' memory to swap; a
   kernel that doesn't take that argument (before 6.12) refuses the write,
   and then, as on a kernel without the file, the fallback runs: ``sync``,
   then ``1`` into /proc/sys/vm/drop_caches, which drops all the clean page
   cache (never dentries/inodes, never what a program holds) and never
   swaps. swappiness=0 is a preference, not a promise, so swap use is
   compared before and after: if it grew by more than 64 MiB, the status
   records it and every later run uses the fallback instead. Logs one line
   with the cache before and after and the method.
6. Writes what it found and did to /var/lib/domovoi-cache-trim/status.json
   (root's, 0644: the core reads it for the Health page's memory row).

The operator file: /etc/domovoi/cache-trim.conf, for an administrator, with
no dashboard control. Read only while root owns it (and its folder) and
nobody else can write either; at most 1 KiB; ``#`` comments; two keys:

    enabled=auto|on|off        # auto (the default): on inside WSL only
    limit_gb=8                 # 1 .. 1024, a whole number of GiB

Anything else in it (an unknown key, a value out of range, a second copy
of a key) makes the whole file count as absent, with the reason in the
status file and the journal: defaults, never a half-read file.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import stat
import subprocess
import sys
import time

GIB = 1024 ** 3
DEFAULT_MIN_LIMIT = 4 * GIB
DEFAULT_SHARE = 0.10
LIMIT_GB_MIN = 1
LIMIT_GB_MAX = 1024
# How far over the limit before a trim is worth one.
MIN_EXCESS = 256 * 1024 * 1024
# Swap that grew this much across a memory.reclaim counts as swapping.
SWAP_GROWTH_MAX = 64 * 1024 * 1024
RECLAIM_FILE = "/sys/fs/cgroup/memory.reclaim"
# The timer starts a run every 120 s; a run started by hand in between
# (or a timer catching up) doesn't trim twice.
MIN_INTERVAL_SEC = 100
OVERRIDE_MAX_BYTES = 1024
STATUS_SCHEMA = 1

OVERRIDE_FILE = "/etc/domovoi/cache-trim.conf"
STATUS_FILE = "/var/lib/domovoi-cache-trim/status.json"
UPDATE_UNIT = "domovoi-update.service"
SYSTEMCTL = "/usr/bin/systemctl"


def log(msg: str) -> None:
    print(f"cache-trim: {msg}", flush=True)


def fmt_gb(n: int) -> str:
    gb = n / GIB
    return f"{gb:.1f} GB" if gb < 10 else f"{gb:.0f} GB"


# ─── /proc ────────────────────────────────────────────────────────────────


def parse_meminfo(text: str) -> dict[str, int]:
    """``{key: bytes}`` from /proc/meminfo; lines it can't read are skipped."""
    out: dict[str, int] = {}
    for line in (text or "").splitlines():
        key, sep, rest = line.partition(":")
        parts = rest.split()
        if not sep or not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        out[key.strip()] = value * 1024 if len(parts) > 1 and parts[1].lower() == "kb" else value
    return out


def cache_figures(mi: dict[str, int]) -> dict[str, int]:
    """``{total, cache, droppable, swap_used}`` in bytes: the file cache as
    Health shows it, and the part of it a trim can free (module docstring,
    step 1)."""
    shmem = mi.get("Shmem", 0)
    cache = max(0, mi.get("Cached", 0) + mi.get("Buffers", 0) - shmem)
    # File pages programs map: Mapped counts mapped shared memory too, which
    # is not in the file LRU (nor in `cache`), so at least Mapped - Shmem.
    file_mapped = max(0, mi.get("Mapped", 0) - shmem)
    if "Active(file)" in mi and "Inactive(file)" in mi:
        lru = mi["Active(file)"] + mi["Inactive(file)"]
    else:
        lru = cache
    droppable = max(0, min(cache, lru) - file_mapped)
    swap_used = max(0, mi.get("SwapTotal", 0) - mi.get("SwapFree", mi.get("SwapTotal", 0)))
    return {"total": mi.get("MemTotal", 0), "cache": cache, "droppable": droppable, "swap_used": swap_used}


def is_wsl(proc_root: str = "/proc") -> bool:
    """The kernel names Microsoft inside WSL 1 and 2: osrelease first,
    /proc/version only when that can't be read (domovoi/host_kind.py)."""
    for rel in ("sys/kernel/osrelease", "version"):
        try:
            with open(os.path.join(proc_root, rel), encoding="utf-8", errors="replace") as f:
                return "microsoft" in f.read().lower()
        except OSError:
            continue
    return False


def default_limit(total: int) -> int:
    """max(4 GiB, 10% of MemTotal)."""
    return max(DEFAULT_MIN_LIMIT, int(total * DEFAULT_SHARE))


# ─── the operator file ────────────────────────────────────────────────────


def _root_only(st: os.stat_result, root_uids: tuple[int, ...]) -> bool:
    return st.st_uid in root_uids and not (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def parse_override(text: str) -> tuple[dict[str, object], str | None]:
    """``({enabled?, limit_gb?}, None)``, or ``({}, why)`` when anything in
    it is wrong: the file then counts as absent."""
    seen: dict[str, object] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep:
            return {}, f"line {n} is not key=value"
        if key in seen:
            return {}, f"{key} is set twice"
        if key == "enabled":
            if value not in ("auto", "on", "off"):
                return {}, "enabled must be auto, on or off"
            seen[key] = value
        elif key == "limit_gb":
            if not value.isascii() or not value.isdigit() or len(value) > 4:
                return {}, "limit_gb must be a whole number of GiB"
            gb = int(value)
            if not LIMIT_GB_MIN <= gb <= LIMIT_GB_MAX:
                return {}, f"limit_gb must be {LIMIT_GB_MIN} to {LIMIT_GB_MAX}"
            seen[key] = gb
        else:
            return {}, f"unknown key on line {n}"
    return seen, None


def read_override(path: str, root_uids: tuple[int, ...] = (0,)) -> tuple[dict[str, object], str | None]:
    """The operator file's settings, or ``({}, None)`` when there is none,
    or ``({}, why)`` when it can't be believed. Never follows a link, never
    blocks on a FIFO, never reads more than 1 KiB."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_NOCTTY", 0))
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        return {}, f"can't be opened ({e.strerror or e})"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return {}, "is not a plain file"
        parent = os.stat(os.path.dirname(path) or ".")
        if not _root_only(st, root_uids) or not _root_only(parent, root_uids):
            return {}, "is not root's alone (it, or its folder, can be written by others)"
        if st.st_size > OVERRIDE_MAX_BYTES:
            return {}, f"is larger than {OVERRIDE_MAX_BYTES} bytes"
        data = os.read(fd, OVERRIDE_MAX_BYTES + 1)
    except OSError as e:
        return {}, f"can't be read ({e.strerror or e})"
    finally:
        os.close(fd)
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return {}, "is not plain ASCII"
    return parse_override(text)


# ─── the decision ─────────────────────────────────────────────────────────


def decide(*, figures: dict[str, int], wsl: bool, override: dict[str, object],
           update_running: bool, last_trim_at: float | None, now: float) -> dict[str, object]:
    """What this run should do: ``{enabled, why_enabled, limit, limit_source,
    action, why}``; ``action`` is ``off``, ``skip``, ``under`` or ``trim``.
    Pure: every input is an argument."""
    mode = override.get("enabled", "auto")
    if mode == "on":
        enabled, why_enabled = True, "operator"
    elif mode == "off":
        enabled, why_enabled = False, "operator"
    else:
        enabled, why_enabled = wsl, "wsl" if wsl else "not-wsl"
    if "limit_gb" in override:
        limit, source = int(override["limit_gb"]) * GIB, "operator"   # type: ignore[arg-type]
    else:
        limit, source = default_limit(figures["total"]), "default"
    out: dict[str, object] = {"enabled": enabled, "why_enabled": why_enabled,
                              "limit": limit, "limit_source": source}
    if not enabled:
        return {**out, "action": "off", "why": "off"}
    if update_running:
        return {**out, "action": "skip", "why": "an update or restart of Domovoi is running"}
    if figures["droppable"] <= limit:
        return {**out, "action": "under", "why": "under the limit"}
    if figures["droppable"] - limit < MIN_EXCESS:
        return {**out, "action": "under", "why": "too little over the limit"}
    if last_trim_at is not None and 0 <= now - last_trim_at < MIN_INTERVAL_SEC:
        return {**out, "action": "skip", "why": "trimmed less than 2 minutes ago"}
    return {**out, "action": "trim", "why": "over the limit"}


# ─── side effects ─────────────────────────────────────────────────────────


def update_running(systemctl: str = SYSTEMCTL) -> bool:
    """domovoi-update.service is running (a oneshot is ``activating`` while
    it runs): an update or the dashboard's Restart."""
    try:
        r = subprocess.run([systemctl, "show", "--property=ActiveState", "--value", UPDATE_UNIT],
                           capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.stdout.strip() in ("activating", "deactivating", "reloading")


def read_status(path: str) -> dict[str, object]:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(path: str, doc: dict[str, object]) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o755, exist_ok=True)
    tmp = os.path.join(d, ".status.json.new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def drop_page_cache(proc_root: str) -> None:
    os.sync()
    with open(os.path.join(proc_root, "sys/vm/drop_caches"), "w", encoding="ascii") as f:
        f.write("1\n")


def reclaim(reclaim_file: str, nbytes: int) -> str:
    """``<nbytes> swappiness=0`` into memory.reclaim: ``done``, ``partial``
    (the kernel reclaimed less than asked: EAGAIN), ``unsupported`` (no
    file, or a kernel that refuses the swappiness argument: EINVAL).
    Other errors raise."""
    if not os.path.exists(reclaim_file):
        return "unsupported"
    try:
        fd = os.open(reclaim_file, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return "unsupported"
    try:
        os.write(fd, f"{int(nbytes)} swappiness=0".encode("ascii"))
    except OSError as e:
        if e.errno == errno.EINVAL:
            return "unsupported"
        if e.errno == errno.EAGAIN:
            return "partial"
        raise
    finally:
        os.close(fd)
    return "done"


def trim(*, proc_root: str, reclaim_file: str, excess: int, before: dict[str, int],
         reclaim_allowed: bool, read_after) -> dict[str, object]:
    """Free ``excess`` bytes of clean file cache (memory.reclaim), or all of
    it (drop_caches) where that can't be done without risking swap. Returns
    ``{method, after, swapped, note}``; ``after`` is the figures afterwards."""
    note = None
    if reclaim_allowed:
        state = reclaim(reclaim_file, excess)
        if state in ("done", "partial"):
            after = read_after()
            swapped = after["swap_used"] - before["swap_used"] > SWAP_GROWTH_MAX
            return {"method": "reclaim", "after": after, "swapped": swapped,
                    "note": "the kernel freed less than asked" if state == "partial" else None}
        note = "memory.reclaim can't be told to leave swap alone on this kernel"
    drop_page_cache(proc_root)
    return {"method": "drop_caches", "after": read_after(), "swapped": False, "note": note}


def _figures(proc_root: str) -> dict[str, int]:
    with open(os.path.join(proc_root, "meminfo"), encoding="utf-8", errors="replace") as f:
        return cache_figures(parse_meminfo(f.read()))


def run(*, proc_root: str, status_path: str, override_path: str, root_uids: tuple[int, ...],
        systemctl: str, dry_run: bool, clock=time.time, reclaim_file: str = RECLAIM_FILE) -> int:
    now = clock()
    try:
        figures = _figures(proc_root)
    except OSError as e:
        log(f"can't read {proc_root}/meminfo: {e}")
        return 1
    if not figures["total"]:
        log("meminfo has no MemTotal; nothing done")
        return 1
    override, problem = read_override(override_path, root_uids)
    if problem:
        log(f"{override_path} {problem}: ignored, the defaults apply")
    previous = read_status(status_path)
    last = previous.get("last_trim") if isinstance(previous.get("last_trim"), dict) else None
    last_at = last.get("at_epoch") if last else None
    d = decide(figures=figures, wsl=is_wsl(proc_root), override=override,
               update_running=update_running(systemctl), last_trim_at=last_at
               if isinstance(last_at, (int, float)) else None, now=now)
    doc: dict[str, object] = {
        "schema": STATUS_SCHEMA,
        "checked_at": iso(now),
        "enabled": d["enabled"],
        "why_enabled": d["why_enabled"],
        "limit_bytes": d["limit"],
        "limit_source": d["limit_source"],
        "total_bytes": figures["total"],
        "cache_bytes": figures["cache"],
        "droppable_bytes": figures["droppable"],
        "action": d["action"],
        "why": d["why"],
        "override_problem": problem,
        "last_trim": last,
        # A memory.reclaim that once let swap grow is not used again here.
        "reclaim_swapped": previous.get("reclaim_swapped") is True,
    }
    excess = max(0, figures["droppable"] - int(d["limit"]))   # type: ignore[arg-type]
    if d["action"] == "trim" and not dry_run:
        def read_after() -> dict[str, int]:
            try:
                return _figures(proc_root)
            except OSError:
                return dict(figures)
        try:
            out = trim(proc_root=proc_root, reclaim_file=reclaim_file, excess=excess, before=figures,
                       reclaim_allowed=not doc["reclaim_swapped"], read_after=read_after)
        except OSError as e:
            log(f"could not trim the page cache: {e}")
            doc["action"], doc["why"] = "failed", f"could not trim the page cache: {e.strerror or e}"
        else:
            after = out["after"]
            freed = max(0, figures["cache"] - after["cache"])   # type: ignore[index]
            doc["last_trim"] = {"at": iso(now), "at_epoch": int(now), "before_bytes": figures["cache"],
                                "after_bytes": after["cache"], "freed_bytes": freed,   # type: ignore[index]
                                "method": out["method"]}
            doc["cache_bytes"], doc["droppable_bytes"] = after["cache"], after["droppable"]   # type: ignore[index]
            if out["swapped"]:
                doc["reclaim_swapped"] = True
            log(f"file cache {fmt_gb(figures['cache'])} ({fmt_gb(figures['droppable'])} not in use) was "
                f"{fmt_gb(excess)} over the {fmt_gb(int(d['limit']))} limit: trimmed to "
                f"{fmt_gb(after['cache'])}, freed {fmt_gb(freed)} ({out['method']}"   # type: ignore[index]
                + (f"; {out['note']}" if out["note"] else "")
                + ("; swap grew, so later trims use drop_caches" if out["swapped"] else "") + ")")
    elif d["action"] == "trim":
        log(f"dry run: would trim {fmt_gb(excess)} of file cache "
            f"(limit {fmt_gb(int(d['limit']))})")
    if dry_run:
        print(json.dumps(doc, indent=1, sort_keys=True))
        return 0
    try:
        write_status(status_path, doc)
    except OSError as e:
        log(f"can't write {status_path}: {e}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Trim the clean page cache above a limit (Domovoi).")
    p.add_argument("--dry-run", action="store_true", help="say what would happen; change nothing")
    # For the tests only: a fake /proc and root, without running as root.
    p.add_argument("--proc", default="/proc", help=argparse.SUPPRESS)
    p.add_argument("--status", default=STATUS_FILE, help=argparse.SUPPRESS)
    p.add_argument("--override", default=OVERRIDE_FILE, help=argparse.SUPPRESS)
    p.add_argument("--systemctl", default=SYSTEMCTL, help=argparse.SUPPRESS)
    p.add_argument("--reclaim", default=RECLAIM_FILE, help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    test_paths = ((a.proc, a.status, a.override, a.systemctl, a.reclaim)
                  != ("/proc", STATUS_FILE, OVERRIDE_FILE, SYSTEMCTL, RECLAIM_FILE))
    euid = getattr(os, "geteuid", lambda: 0)()
    if not test_paths and not a.dry_run and euid != 0:
        log("run as root (domovoi-cache-trim.service), or with --dry-run")
        return 2
    # Under the test paths the caller's own uid stands in for root.
    uids = (0, euid) if test_paths else (0,)
    return run(proc_root=a.proc, status_path=a.status, override_path=a.override, root_uids=uids,
               systemctl=a.systemctl, dry_run=a.dry_run, reclaim_file=a.reclaim)


if __name__ == "__main__":
    sys.exit(main())
