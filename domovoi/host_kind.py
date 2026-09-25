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

``DOMOVOI_HOST_KIND`` in ``.env`` overrides the detection; ``auto`` (the
default) detects. Stdlib and config only, so the web process can import it.
"""

from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path

from domovoi.config import settings

log = logging.getLogger(__name__)

KINDS = ("linux", "wsl", "windows", "darwin")

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


def host_kind() -> str:
    """The host kind: the ``DOMOVOI_HOST_KIND`` override when it names
    one, otherwise what :func:`detect_host_kind` found."""
    return _override() or _detected()


def is_wsl() -> bool:
    return host_kind() == "wsl"
