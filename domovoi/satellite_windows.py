"""The Windows satellite's runtime, fetched and kept by the core.

Spare-device satellites on Windows (2026-10-13). A Windows laptop becomes a
room by running the dashboard's one-line PowerShell command; the installer
needs an embeddable CPython and about 25 win_amd64 wheels (~95 MB). They are
pinned, file by file, in ``satellite/windows-runtime.json`` (the lock:
URL, SHA-256, size, licence; written by scripts/dev/windows_runtime_lock.py)
— the same lock the installer reads from the satellite bundle whose digest
the dashboard quoted.

This module is the core's half, like the Pi wheel cache
(domovoi/satellite_media/fetchers.py):

* :func:`fetch` — an admin asks (Settings → Rooms → Add a spare device
  → Windows → "get the Windows files"), and every file the lock names is
  downloaded from python.org / PyPI through the outbound gate
  (domovoi/net_safety.py: public addresses only, redirects vetted) into
  ``~/.domovoi/satellite_media/cache/windows/<python version>/``, and kept
  only if its size and SHA-256 are the lock's. Refused under
  ``INTERNET_ACCESS=never`` (what is already cached still serves).
* :func:`cached_file` — what ``GET /v1/satellite-windows/files/{name}``
  serves: only a name the lock lists, only a file whose bytes still match
  its pin. Public on the LAN like the satellite code bundle: these are
  public files, and the installer checks every byte against the lock
  anyway (a laptop whose core has not fetched them downloads the same
  pinned files from upstream itself).
* :func:`status` — ready or not, what is missing, how much.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

LOCK_NAME = "windows-runtime.json"
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}\.(whl|zip)$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
#: Where the lock's files may come from.
UPSTREAM_HOSTS = ("https://www.python.org/ftp/python/", "https://files.pythonhosted.org/packages/")
MAX_FILE_BYTES = 128 * 1024 * 1024


class LockError(ValueError):
    """The lock is missing or malformed."""


def load_lock(path: Path) -> dict[str, Any]:
    """The lock, every entry checked for shape: a safe file name, an https
    URL on python.org or PyPI's file host, a SHA-256, a sane size."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LockError(f"the Windows runtime lock is unreadable: {e}") from e
    if not isinstance(doc, dict) or doc.get("lock_version") != 1:
        raise LockError("the Windows runtime lock is not version 1")
    for entry in entries(doc):
        name, url, sha, size = entry.get("file"), entry.get("url"), entry.get("sha256"), entry.get("size")
        if not isinstance(name, str) or not _FILE_RE.match(name):
            raise LockError(f"bad file name in the lock: {name!r}")
        if not isinstance(url, str) or not url.startswith(UPSTREAM_HOSTS) or not url.endswith("/" + name):
            raise LockError(f"{name}: the URL is not python.org's or PyPI's file host")
        if not isinstance(sha, str) or not _SHA_RE.match(sha):
            raise LockError(f"{name}: bad sha256")
        if not isinstance(size, int) or not 0 < size <= MAX_FILE_BYTES:
            raise LockError(f"{name}: bad size")
    return doc


def entries(lock: dict[str, Any]) -> list[dict[str, Any]]:
    """Every file the lock names: the Python zip, pip, the wheels, and the
    wheel Microsoft's C++ runtime DLLs are taken from (``vc_runtime``: the
    satellite places them beside its Python; the core only caches and
    serves the wheel, checked like the others)."""
    out = [lock.get("python") or {}, lock.get("pip") or {}]
    out += list(lock.get("wheels") or [])
    if lock.get("vc_runtime"):
        out.append(lock["vc_runtime"])
    return out


def bucket(lock: dict[str, Any]) -> Path:
    from domovoi.satellite_media import cache

    version = str((lock.get("python") or {}).get("version") or "unknown")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise LockError(f"bad Python version {version!r}")
    return cache.bucket("windows", version)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# (path, size, mtime_ns, inode, ctime_ns) -> verified, so a settled file is
# hashed once per change, not on every request.
#
# The stat fields alone cannot tell a same-size rewrite made within one
# timestamp tick from the file they were read from: Windows stamps file
# times from a clock that moves every ~1-16 ms, Linux from the kernel's
# coarse clock, and an in-place rewrite keeps the inode (and, on Windows,
# the creation time st_ctime reports). A verdict cached for such a file
# answered "pinned" for different bytes (the flaky
# test_only_listed_and_pinned_bytes_are_served, 2026-10-17: two thirds of
# immediate rewrites kept their mtime here). So, like git's "racy" index
# entries, a verdict is kept only for a file last changed well before it
# was hashed, and unchanged while it was; a fresh file is re-hashed on each
# request until it settles (seconds, once, after a fetch).
_VerifyKey = tuple[str, int, int, int, int]
_verified: dict[_VerifyKey, bool] = {}
_verified_lock = threading.Lock()
_SETTLED_NS = 2_000_000_000


def _verify_key(path: Path, st: Any) -> _VerifyKey:
    return (str(path), st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns)


def _matches(path: Path, sha256: str, size: int) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    if path.is_symlink() or not path.is_file() or st.st_size != size:
        return False
    key = _verify_key(path, st)
    with _verified_lock:
        known = _verified.get(key)
    if known is not None:
        return known
    started = time.time_ns()
    known = _sha256(path) == sha256
    try:
        after = path.stat()
    except OSError:
        return False
    if _verify_key(path, after) != key:
        return False  # changed while it was read: no verdict either way
    if started - max(st.st_mtime_ns, st.st_ctime_ns) > _SETTLED_NS:
        with _verified_lock:
            _verified[key] = known
    return known


def cached_file(lock: dict[str, Any], name: str) -> Path | None:
    """The cached file for ``name`` if the lock lists it and the bytes on
    disk are the pinned ones; else None."""
    if not isinstance(name, str) or not _FILE_RE.match(name):
        return None
    entry = next((e for e in entries(lock) if e.get("file") == name), None)
    if entry is None:
        return None
    path = bucket(lock) / name
    return path if _matches(path, entry["sha256"], entry["size"]) else None


def status(lock: dict[str, Any]) -> dict[str, Any]:
    missing = [e["file"] for e in entries(lock) if cached_file(lock, e["file"]) is None]
    total = sum(int(e["size"]) for e in entries(lock))
    have = sum(int(e["size"]) for e in entries(lock) if e["file"] not in missing)
    return {
        "ready": not missing,
        "python": (lock.get("python") or {}).get("version"),
        "files": len(entries(lock)),
        "missing": missing,
        "bytes_total": total,
        "bytes_cached": have,
        "fetch": dict(_job),
    }


_job: dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "ok": None, "message": None}
_job_lock = threading.Lock()


def fetch(lock: dict[str, Any]) -> tuple[bool, str]:
    """Download every missing file the lock names, each held to its pin.
    Blocking (minutes): run it off the event loop. ``(ok, message)``."""
    from domovoi.satellite_media import fetchers

    refused = fetchers._turned_off("the Windows satellite files")
    if refused:
        return refused
    dest = bucket(lock)
    failed: list[str] = []
    fetched = 0
    for entry in entries(lock):
        name = entry["file"]
        target = dest / name
        if _matches(target, entry["sha256"], entry["size"]):
            continue
        try:
            fetchers._fetch_pinned(entry["url"], target, sha256=entry["sha256"], size=entry["size"])
        except Exception as e:  # noqa: BLE001 — a network error is one file's failure
            log.warning("Windows satellite files: %s refused: %s", name, e)
            failed.append(f"{name}: {e}")
            continue
        fetched += 1
    # Anything in the bucket the lock doesn't vouch for goes.
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
        log.info("Windows satellite files: %s", message)

    threading.Thread(target=run, name="satellite-windows-fetch", daemon=True).start()
    return True
