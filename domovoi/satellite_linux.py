"""The Linux satellite's own Python, fetched and kept by the core.

Spare-device satellites on Linux (2026-10-15). A Linux laptop becomes a room
by running the dashboard's one-line command; ``satellite/scripts/
install-pc.sh`` uses the computer's own ``python3`` on a current Debian or
Ubuntu, and otherwise (Ubuntu 22.04, Mint 21, Debian 11, Fedora, Arch,
openSUSE, …) a pinned CPython of ours: python-build-standalone's relocatable
build, one per machine (x86-64, 64-bit ARM), pinned by size and SHA-256 in
``satellite/locks/linux-python.txt`` (written by
scripts/dev/linux_runtime_lock.py) — the same lock the installer reads from
the satellite bundle whose digest the dashboard quoted. Plain text, because
the installer reads it with bash alone.

This module is the core's half, like the Windows runtime's
(domovoi/satellite_windows.py):

* :func:`fetch` — an admin asks (Settings → Rooms → Add a spare device
  → "get the Linux Python"), and every file the lock names is downloaded
  from the project's GitHub releases through the outbound gate
  (domovoi/net_safety.py: public addresses only, every redirect vetted) into
  ``~/.domovoi/satellite_media/cache/linux-python/``, and kept only if its
  size and SHA-256 are the lock's. Refused under ``INTERNET_ACCESS=never``
  (what is already cached still serves).
* :func:`cached_file` — what ``GET /v1/satellite-linux/files/{name}``
  serves: only a name the lock lists, only a file whose bytes still match
  its pin. Public on the LAN like the satellite code bundle: a public file
  the installer checks byte for byte against the lock anyway (a laptop
  whose core has not fetched it downloads the same pinned file from GitHub
  itself).
* :func:`status` — ready or not, what is missing, how much.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from domovoi.satellite_windows import _matches

log = logging.getLogger(__name__)

#: Under the satellite tree (main.SATELLITE_CODE_DIR).
LOCK_REL = "locks/linux-python.txt"
UPSTREAM = "https://github.com/astral-sh/python-build-standalone/releases/download/"
MACHINES = ("x86_64", "aarch64")
_FILE_RE = re.compile(
    r"^cpython-(3\.[0-9]{1,2}\.[0-9]{1,3})\+([0-9]{8})-(x86_64|aarch64)-unknown-linux-gnu-install_only_stripped\.tar\.gz$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}\Z")
MAX_FILE_BYTES = 128 * 1024 * 1024


class LockError(ValueError):
    """The lock is missing or malformed."""


def parse_lock(text: str) -> dict[str, Any]:
    """The lock's ``python`` lines, each checked for shape: a machine the
    installer knows (once each), a python-build-standalone file name for
    that machine and version, a sane size, a SHA-256, and exactly the URL
    the project's release would serve that file at."""
    pythons: list[dict[str, Any]] = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 7 or fields[0] != "python":
            raise LockError(f"line {n}: not 'python <machine> <version> <file> <size> <sha256> <url>'")
        _, machine, version, name, size, sha, url = fields
        m = _FILE_RE.fullmatch(name)
        if not m or m.group(1) != version or m.group(3) != machine:
            raise LockError(f"line {n}: bad file name {name!r} for {machine} {version}")
        if machine in (p["machine"] for p in pythons):
            raise LockError(f"line {n}: a second Python for {machine}")
        if not size.isdigit() or not 0 < int(size) <= MAX_FILE_BYTES:
            raise LockError(f"line {n}: bad size")
        if not _SHA_RE.match(sha):
            raise LockError(f"line {n}: bad sha256")
        if url != UPSTREAM + m.group(2) + "/" + urllib.parse.quote(name):
            raise LockError(f"line {n}: the URL is not that file's on python-build-standalone's releases")
        pythons.append({"machine": machine, "version": version, "file": name, "size": int(size),
                        "sha256": sha, "url": url, "release": m.group(2)})
    if not pythons:
        raise LockError("the Linux Python lock names no Python")
    return {"pythons": pythons}


def load_lock(path: Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise LockError(f"the Linux Python lock is unreadable: {e}") from e
    return parse_lock(text)


def entries(lock: dict[str, Any]) -> list[dict[str, Any]]:
    return list(lock.get("pythons") or [])


def bucket() -> Path:
    from domovoi.satellite_media import cache

    return cache.bucket("linux-python")


def cached_file(lock: dict[str, Any], name: str) -> Path | None:
    """The cached file for ``name`` if the lock lists it and the bytes on
    disk are the pinned ones; else None."""
    if not isinstance(name, str) or not _FILE_RE.fullmatch(name):
        return None
    entry = next((e for e in entries(lock) if e["file"] == name), None)
    if entry is None:
        return None
    path = bucket() / name
    return path if _matches(path, entry["sha256"], entry["size"]) else None


_job: dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "ok": None, "message": None}
_job_lock = threading.Lock()


def status(lock: dict[str, Any]) -> dict[str, Any]:
    missing = [e["file"] for e in entries(lock) if cached_file(lock, e["file"]) is None]
    return {
        "ready": not missing,
        "pythons": [{"machine": e["machine"], "version": e["version"], "size": e["size"]} for e in entries(lock)],
        "files": len(entries(lock)),
        "missing": missing,
        "bytes_total": sum(e["size"] for e in entries(lock)),
        "bytes_cached": sum(e["size"] for e in entries(lock) if e["file"] not in missing),
        "fetch": dict(_job),
    }


def fetch(lock: dict[str, Any]) -> tuple[bool, str]:
    """Download every missing file the lock names, each held to its pin.
    Blocking (a minute or two): run it off the event loop. ``(ok, message)``."""
    from domovoi.satellite_media import fetchers

    refused = fetchers._turned_off("the Linux satellite's Python")
    if refused:
        return refused
    dest = bucket()
    failed: list[str] = []
    fetched = 0
    for entry in entries(lock):
        target = dest / entry["file"]
        if _matches(target, entry["sha256"], entry["size"]):
            continue
        try:
            fetchers._fetch_pinned(entry["url"], target, sha256=entry["sha256"], size=entry["size"])
        except Exception as e:  # noqa: BLE001 — a network error is one file's failure
            log.warning("Linux satellite Python: %s refused: %s", entry["file"], e)
            failed.append(f"{entry['file']}: {e}")
            continue
        fetched += 1
    # Anything in the bucket the lock doesn't vouch for goes (an older pin).
    wanted = {e["file"] for e in entries(lock)}
    for p in dest.iterdir():
        if p.name not in wanted and p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
    if failed:
        return False, f"{len(failed)} of {len(entries(lock))} files failed: " + "; ".join(failed)[:900]
    return True, f"{len(entries(lock))} files verified ({fetched} downloaded)"


def start_fetch(lock: dict[str, Any]) -> bool:
    """Run :func:`fetch` on a thread unless one is running. True if started."""
    with _job_lock:
        if _job["running"]:
            return False
        _job.update(running=True, started_at=time.time(), finished_at=None, ok=None, message=None)

    def run() -> None:
        try:
            ok, message = fetch(lock)
        except Exception as e:  # noqa: BLE001
            ok, message = False, f"{type(e).__name__}: {e}"
        with _job_lock:
            _job.update(running=False, finished_at=time.time(), ok=ok, message=message)
        log.info("Linux satellite Python: %s", message)

    threading.Thread(target=run, name="satellite-linux-fetch", daemon=True).start()
    return True


# ─── A Linux room's Python packages, from this server (offline kit, P6) ──
#
# install-pc.sh installs the satellite's packages from
# ``satellite/locks/pc-<cpXY>-<machine>.txt`` with ``pip --require-hashes
# --only-binary=:all: --no-deps``, from PyPI. A server installed from an
# offline kit (domovoi/kit_rooms.py) holds those wheels in
# ``cache/linux-wheels/``, and serves them, so a laptop in a house with no
# internet installs with ``--no-index --find-links`` pointed here. Only a
# wheel whose SHA-256 the lock lists for its package is served, and the
# index page is offered only when every package of the lock is here (pip
# then never needs PyPI); pip checks every hash again on the laptop.

PC_LOCK_RE = re.compile(r"^pc-cp3[0-9]{1,2}-(x86_64|aarch64)\Z")
WHEEL_BUCKET = "linux-wheels"
_WHEEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}\.whl\Z")


def wheel_dir() -> Path:
    """The wheels' folder (not created: a read must not make it)."""
    from domovoi.satellite_media import cache

    return cache.CACHE_ROOT / WHEEL_BUCKET


def normalize(name: str) -> str:
    """A distribution name as PEP 503 compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def pc_lock(locks_dir: Path, name: str) -> dict[str, frozenset[str]] | None:
    """``{package: its allowed SHA-256s}`` of the spare-computer lock
    ``name`` (``pc-cp313-x86_64``), or None for a name that isn't one or a
    lock that isn't there."""
    from domovoi.kit_builder import wheels

    if not isinstance(name, str) or not PC_LOCK_RE.match(name):
        return None
    try:
        text = (Path(locks_dir) / f"{name}.txt").read_text(encoding="utf-8")
    except OSError:
        return None
    locked = wheels.parse_lock(text)
    return {normalize(lk.name): lk.hashes for lk in locked} or None


def _wheel_sha(path: Path) -> str | None:
    from domovoi.satellite_windows import _sha256

    try:
        if path.is_symlink() or not path.is_file():
            return None
        return _sha256(path)
    except OSError:
        return None


def cached_wheel(lock: dict[str, frozenset[str]], name: str) -> Path | None:
    """The wheel ``name`` if the lock allows its bytes for its package."""
    if not isinstance(name, str) or not _WHEEL_RE.match(name):
        return None
    allowed = lock.get(normalize(name.split("-", 1)[0]))
    if not allowed:
        return None
    path = wheel_dir() / name
    try:
        st = path.stat()
    except OSError:
        return None
    sha = _wheel_verdicts.get((name, st.st_size, st.st_mtime_ns))
    if sha is None:
        sha = _wheel_sha(path)
        if sha is None:
            return None
        _wheel_verdicts[(name, st.st_size, st.st_mtime_ns)] = sha
    return path if sha in allowed else None


_wheel_verdicts: dict[tuple[str, int, int], str] = {}


def wheels_for(lock: dict[str, frozenset[str]]) -> dict[str, Path] | None:
    """``{file name: path}``, one wheel the lock allows per package, or
    None when some package has none here."""
    found: dict[str, Path] = {}
    have: set[str] = set()
    d = wheel_dir()
    try:
        names = sorted(p.name for p in d.iterdir()) if d.is_dir() else []
    except OSError:
        names = []
    for n in names:
        pkg = normalize(n.split("-", 1)[0])
        if pkg in lock and pkg not in have:
            path = cached_wheel(lock, n)
            if path is not None:
                found[n] = path
                have.add(pkg)
    return found if have == set(lock) else None


def wheel_index(lock: dict[str, frozenset[str]]) -> str | None:
    """pip's ``--find-links`` page (plain links, relative) for the lock, or
    None when this server can't serve all of it."""
    import html

    found = wheels_for(lock)
    if found is None:
        return None
    links = "\n".join(f'<a href="{html.escape(n)}">{html.escape(n)}</a><br>' for n in found)
    return f"<!DOCTYPE html>\n<html><body>\n{links}\n</body></html>\n"


def wheels_status(locks_dir: Path) -> dict[str, bool]:
    """``{lock name: whether this server serves all of it}``."""
    out = {}
    try:
        names = sorted(p.stem for p in Path(locks_dir).glob("pc-*.txt"))
    except OSError:
        names = []
    for n in names:
        lock = pc_lock(locks_dir, n)
        out[n] = bool(lock) and wheels_for(lock) is not None
    return out
