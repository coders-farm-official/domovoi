"""What kind of machine the Domovoi server is running on.

Almost nothing in Domovoi cares: the Linux stack is the same on a dedicated
box and inside the private WSL2 distro the Windows installer sets up. The
exception is hardware the server has to see directly. WSL automounts no
removable drives - Windows keeps USB sticks and SD cards to itself - so on
that host USB satellite adoption and writing a satellite's card from the
dashboard have nothing to work with. The dashboard reads this to say so,
instead of showing lists that are always empty.

``host_kind()`` answers ``"wsl"``, ``"linux"``, ``"windows"`` or
``"darwin"`` (any other platform answers its ``sys.platform``). WSL is
recognised by its kernel: ``/proc/sys/kernel/osrelease`` names Microsoft on
WSL 1 (``4.4.0-19041-Microsoft``) and WSL 2
(``6.6.87.2-microsoft-standard-WSL2``); ``/proc/version`` is read only when
that file can't be. Deliberately NOT by
``/proc/sys/fs/binfmt_misc/WSLInterop``: the Domovoi distro turns interop
off, and that file goes with it. Nor by ``WSL_DISTRO_NAME``, which wsl.exe
sets for the shells it starts - a systemd service is not one of them.

``"mac"`` is the Linux VM the macOS installer runs (Lima, mac-installer
PLAN 3). Its kernel is a plain Ubuntu one, so it is told apart by the file
the Mac's root helper writes into it at install,
``/var/lib/domovoi-mac/side.json`` with ``"kind": "mac"`` (the same file
``domovoi/windows_side.py`` reads for the Version card, under the same
rules: owned by root, writable by nobody else, small). Like WSL, that VM
sees no SD cards or USB gadgets: Lima's VM gets no host USB devices. The
file can appear after the core has started (the helper writes it after the
first health check), so it is looked at again every
:data:`_MAC_RECHECK_SEC`, never cached for the life of the process.

``DOMOVOI_HOST_KIND`` in ``.env`` overrides the detection; ``auto`` (the
default) detects. Stdlib and config only, so the web process can import it.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
import time
from functools import lru_cache
from pathlib import Path

from domovoi.config import settings

log = logging.getLogger(__name__)

KINDS = ("linux", "wsl", "windows", "darwin", "mac")

#: Kinds whose Linux system can't see the machine's removable drives: the
#: Windows installer's WSL distro and the Mac installer's VM.
NO_DRIVE_KINDS = ("wsl", "mac")

#: The Mac helper's record inside its VM (domovoi/windows_side.py
#: HOST_SIDE_FILE; DOMOVOI_HOST_SIDE_FILE moves both, for tests).
HOST_SIDE_FILE = "/var/lib/domovoi-mac/side.json"
_HOST_SIDE_MAX_BYTES = 4096
_MAC_RECHECK_SEC = 10.0
_mac_cache: tuple[float, bool, bool] | None = None

_OSRELEASE = Path("/proc/sys/kernel/osrelease")
_PROC_VERSION = Path("/proc/version")

# Override values already warned about, so a typo in .env is logged once
# rather than on every dashboard poll.
_WARNED: set[str] = set()


def detect_host_kind(
    platform: str | None = None,
    osrelease: Path = _OSRELEASE,
    proc_version: Path = _PROC_VERSION,
) -> str:
    """Detect the host kind, ignoring any override. The arguments exist
    for tests; production calls it bare."""
    platform = sys.platform if platform is None else platform
    if platform.startswith("linux"):
        for path in (osrelease, proc_version):
            try:
                kernel = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # The first file that answers decides. /proc/version also
            # carries the builder's name and compiler, which is more text
            # to match by accident, so it is only the fallback.
            return "wsl" if "microsoft" in kernel.lower() else "linux"
        return "linux"
    if platform in ("win32", "cygwin"):
        return "windows"
    if platform == "darwin":
        return "darwin"
    return platform


@lru_cache(maxsize=1)
def _detected() -> str:
    # The kernel does not change under a running process.
    return detect_host_kind()


def _override() -> str | None:
    value = (getattr(settings, "domovoi_host_kind", "") or "").strip().lower()
    if value in ("", "auto"):
        return None
    if value not in KINDS:
        if value not in _WARNED:
            _WARNED.add(value)
            log.warning(
                "DOMOVOI_HOST_KIND=%r is not one of auto, %s - detecting instead",
                value, ", ".join(KINDS),
            )
        return None
    return value


def _root_only(st: os.stat_result) -> bool:
    """Owned by root and writable by nobody else (windows_side's rule)."""
    if not hasattr(os, "getuid"):
        return False
    return st.st_uid == 0 and not (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _read_mac_side(path: Path | None = None) -> dict | None:
    """The Mac helper's root-written record, when it is one this module
    trusts and it says ``"kind": "mac"``; None otherwise. Never raises;
    anything odd (missing, a symlink, not root's, too big, not JSON,
    another kind) is None."""
    p = path or Path(os.environ.get("DOMOVOI_HOST_SIDE_FILE") or HOST_SIDE_FILE)
    try:
        st = os.lstat(p)
        if not stat.S_ISREG(st.st_mode) or not _root_only(st) or st.st_size > _HOST_SIDE_MAX_BYTES:
            return None
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("kind") == "mac" else None


def read_mac_marker(path: Path | None = None) -> bool:
    """Whether the Mac helper's root-written record says this Linux system
    is the Mac installer's VM."""
    return _read_mac_side(path) is not None


def _card_helper_flag(doc: dict | None) -> bool:
    """``"card_helper": 1``: that Mac side has the menu-bar card helper
    (Domovoi's menu-bar icon > Prepare an SD card...). A Mac side from
    before it doesn't say so, and gets the Finder steps instead."""
    if doc is None:
        return False
    v = doc.get("card_helper")
    return type(v) is int and v == 1


def _mac_side() -> tuple[bool, bool]:
    """(the Mac VM, its side has the card helper), re-read at most every
    :data:`_MAC_RECHECK_SEC`."""
    global _mac_cache
    now = time.monotonic()
    if _mac_cache is not None and now - _mac_cache[0] < _MAC_RECHECK_SEC:
        return _mac_cache[1], _mac_cache[2]
    doc = _read_mac_side()
    _mac_cache = (now, doc is not None, _card_helper_flag(doc))
    return _mac_cache[1], _mac_cache[2]


def _mac_vm() -> bool:
    return _mac_side()[0]


def reset_for_tests() -> None:
    global _mac_cache
    _mac_cache = None


def host_kind() -> str:
    """The host kind: the ``DOMOVOI_HOST_KIND`` override when it names
    one, otherwise what :func:`detect_host_kind` found, with a plain Linux
    answer turned into ``"mac"`` inside the Mac installer's VM."""
    over = _override()
    if over:
        return over
    kind = _detected()
    if kind == "linux" and _mac_vm():
        return "mac"
    return kind


def is_wsl() -> bool:
    return host_kind() == "wsl"


def is_mac_vm() -> bool:
    """The macOS installer's Linux VM (see the module docstring)."""
    return host_kind() == "mac"


def mac_card_helper() -> bool:
    """Inside the Mac installer's VM, whether the Mac side has the
    menu-bar card helper (``"card_helper": 1`` in its side.json): the
    dashboard and the zip's README then point at Domovoi's menu-bar icon
    > Prepare an SD card... instead of the Finder steps. False on every
    other host, and on a Mac whose side doesn't say so (an older Mac app
    under a newer core, or the kind forced by DOMOVOI_HOST_KIND)."""
    return host_kind() == "mac" and _mac_side()[1]


def sees_removable_drives() -> bool:
    """False inside WSL and the Mac VM: their host keeps SD cards and USB
    drives to itself."""
    return host_kind() not in NO_DRIVE_KINDS
