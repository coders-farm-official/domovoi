"""Pending-satellite detection + provision-file writing (USB adoption).

An unprovisioned satellite plugged into the Domovoi server's USB port
presents a small FAT gadget volume (label ``DOMOVOI-SET``) carrying a
``device-info.json``. This module finds those volumes (riding the same
removable-drive probes the Files pages use), validates them against the
shared wire format (``satellite/provisioning_protocol.py``), and writes the
adopt flow's ``provision.json`` back with the durability tricks a FAT
volume behind Windows write caching needs.

Identity is the device's per-session **nonce**, never a drive letter —
letters live only in the module-level ``_MOUNT_BY_NONCE`` cache and are
never serialized to clients (the same rule as ``MediaLibrary.root_path``).

Dev/test harness: ``SATELLITE_ADOPTION_SCAN_DIRS`` (os.pathsep-separated)
makes plain directories scan as if they were gadget volumes — the whole
flow is exercisable with a folder and a hand-written device-info.json (no
hardware, no admin rights). Real volumes are additionally label-filtered;
SCAN_DIRS entries skip the label check (a folder has no label).
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from domovoi.config import settings as core_settings
from satellite import provisioning_protocol as proto

from web.backend.api.files_security import detect_removable, drive_token

log = logging.getLogger(__name__)

PENDING_CHANNEL = "satellites.pending"

# nonce → mount path. Server-side only; rebuilt on every scan.
_MOUNT_BY_NONCE: dict[str, Path] = {}

# TTL cache so the ~1.5 s realtime poll tick doesn't re-enumerate drives
# every single pass (drive probes touch Win32 APIs and, worst case, spin
# up sleepy card readers).
_CACHE_TTL_SEC = 3.0
_cache_at: float = 0.0
_cache_value: list[dict[str, Any]] | None = None


def _scan_dirs_override() -> list[Path] | None:
    raw = os.environ.get("SATELLITE_ADOPTION_SCAN_DIRS")
    if not raw:
        return None
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def _volume_label(mount: str) -> str | None:
    """The FAT volume label for a Windows mount root, or None when it can't
    be read (non-Windows, vanished drive, no-media reader)."""
    if sys.platform != "win32":
        return None
    buf = ctypes.create_unicode_buffer(261)
    root = mount if mount.endswith("\\") else mount + "\\"
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(root), buf, len(buf), None, None, None, None, 0
        )
    except Exception:
        return None
    return buf.value if ok else None


def _candidate_mounts() -> list[tuple[Path, bool]]:
    """(mount, label_checked) pairs to probe. SCAN_DIRS entries are the
    dev harness and skip the label pre-filter."""
    override = _scan_dirs_override()
    if override is not None:
        return [(p, False) for p in override]
    out: list[tuple[Path, bool]] = []
    for mount in detect_removable():
        # A:/B: are floppy ghosts on some boards; probing them can beep or
        # block. Nothing real enumerates there.
        if mount.upper().rstrip("\\").rstrip(":") in ("A", "B"):
            continue
        out.append((Path(mount), True))
    return out


def scan_pending() -> list[dict[str, Any]]:
    """Blocking scan (callers thread-wrap). Returns pending-satellite dicts
    ready for the API layer; updates the nonce→mount cache as a side
    effect. Corrupt setup volumes surface as degraded entries (the card
    tells the user WHY nothing is adoptable) rather than vanishing."""
    found: list[dict[str, Any]] = []
    mounts: dict[str, Path] = {}
    for mount, label_checked in _candidate_mounts():
        try:
            if label_checked:
                label = _volume_label(str(mount))
                if label is not None and label != proto.VOLUME_LABEL:
                    continue
            info_path = mount / proto.DEVICE_INFO_NAME
            if not info_path.is_file():
                continue
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            info = proto.validate_device_info(raw)
        except proto.ProvisionInvalid as e:
            found.append({
                "pending_id": f"drive:{drive_token(str(mount))}",
                "parse_error": str(e),
                "detected_at": time.time(),
            })
            continue
        except (OSError, ValueError) as e:
            # Vanished drive / unreadable filesystem / non-JSON garbage.
            log.debug("adoption: skipping %s: %s", mount, e)
            continue
        nonce = info["nonce"]
        mounts[nonce] = mount
        found.append({
            "pending_id": nonce,
            "mac": info.get("mac"),
            "board": info.get("board"),
            "model": info.get("model"),
            "sat_type": info.get("sat_type", "voice"),
            "status": info.get("status", "awaiting_provision"),
            "step": info.get("step"),
            "error": info.get("error"),
            "client_version": info.get("client_version"),
            "profiles_supported": info.get("profiles_supported") or [],
            "detected_at": time.time(),
        })
    _MOUNT_BY_NONCE.clear()
    _MOUNT_BY_NONCE.update(mounts)
    return sorted(found, key=lambda p: p["pending_id"])


async def snapshot_pending() -> list[dict[str, Any]]:
    """Async, TTL-cached wrapper — what the realtime channel and the API
    route call. Returns [] instantly when adoption is disabled. Sorted and
    time-stripped-stable so the realtime differ only fires on real
    changes."""
    global _cache_at, _cache_value
    if not core_settings.satellite_adoption_enabled:
        return []
    now = time.monotonic()
    if _cache_value is not None and (now - _cache_at) < _CACHE_TTL_SEC:
        return _cache_value
    value = await asyncio.to_thread(scan_pending)
    # detected_at would make every diff unequal — strip it from the cached/
    # broadcast form (the API layer doesn't need it either).
    for p in value:
        p.pop("detected_at", None)
    _cache_at = time.monotonic()
    _cache_value = value
    return value


def invalidate_cache() -> None:
    """Force the next snapshot to rescan (used after an adopt writes a
    provision file, and by tests)."""
    global _cache_at, _cache_value
    _cache_at = 0.0
    _cache_value = None


def mount_for(pending_id: str) -> Path | None:
    """The mount currently backing a pending id, or None when the device
    vanished (unplugged / re-nonced)."""
    return _MOUNT_BY_NONCE.get(pending_id)


def write_provision(mount: Path, doc: dict[str, Any]) -> None:
    """Write ``provision.json`` durably onto a FAT volume:

    1. write to ``provision.json.part`` + flush + fsync,
    2. ``os.replace`` to the final name,
    3. fsync the containing directory so the rename itself is durable
       (POSIX; a no-op where the platform won't open a directory fd),
    4. best-effort ``FlushFileBuffers`` on the raw volume handle so FAT
       metadata leaves the Windows cache promptly.

    Durability here only affects LATENCY — correctness lives device-side
    (nonce echo + payload checksum + two stable reads). Windows' default
    "quick removal" policy already write-through-caches removable media.
    Raises OSError when the volume is gone (the API maps it to 410)."""
    part = mount / (proto.PROVISION_NAME + ".part")
    final = mount / proto.PROVISION_NAME
    data = json.dumps(doc, indent=2).encode("utf-8")
    with open(part, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, final)
    _fsync_dir(mount)
    _flush_volume(mount)


def _fsync_dir(mount: Path) -> None:
    """fsync the directory so the rename is durable, not just the file's
    contents. Matters on Linux, where the card is yanked seconds after the
    adopt click and nothing else forces the metadata out. Windows can't
    open a directory as a file descriptor — there ``_flush_volume`` is the
    equivalent. Best-effort: never raises."""
    try:
        fd = os.open(str(mount), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as e:
        log.debug("directory fsync on %s failed: %s", mount, e)
    finally:
        os.close(fd)


def _flush_volume(mount: Path) -> None:
    """Best-effort raw-volume flush (Windows). Failure is logged, never
    raised — see write_provision's durability note."""
    if sys.platform != "win32":
        return
    drive = str(mount).rstrip("\\").rstrip("/")
    if len(drive) != 2 or drive[1] != ":":
        return
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_RW = 0x1 | 0x2
    OPEN_EXISTING = 3
    try:
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = k32.CreateFileW(
            ctypes.c_wchar_p(rf"\\.\{drive}"),
            GENERIC_WRITE, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None,
        )
        if handle in (0, -1):
            return
        try:
            k32.FlushFileBuffers(handle)
        finally:
            k32.CloseHandle(handle)
    except Exception as e:
        log.debug("adoption: volume flush for %s failed: %s", drive, e)


def advertised_domovoi_url() -> str:
    """The core WS URL adopted devices should dial. The setting wins when
    set; otherwise derive the LAN IP via the UDP-connect trick (no packet
    is sent). Never hands out a loopback address — a satellite dialing
    localhost would dial itself."""
    override = (core_settings.satellite_adoption_advertise_url or "").strip()
    if override:
        parts = urlsplit(override if "//" in override else f"ws://{override}")
        scheme = parts.scheme if parts.scheme in ("ws", "wss") else "ws"
        host = parts.hostname or override
        port = parts.port or 6370
        return f"{scheme}://{host}:{port}"
    ip = _lan_ip()
    return f"ws://{ip}:6370"


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET; nothing is sent
        ip = s.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        s.close()
    if not ip or ip.startswith("127."):
        # Last resort: resolve the hostname. Still never loopback — a
        # multi-NIC box that lands here should set the advertise-url
        # override instead.
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = ""
    return ip or "127.0.0.1"
