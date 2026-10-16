"""systemd's service notification protocol (sd_notify(3)), the few lines
the core needs and no dependency.

The shipped ``domovoi-core.service`` is ``Type=notify`` with
``WatchdogSec=`` (scripts/linux/units/): the core says ``READY=1`` once its
startup is done, and its loop watchdog (domovoi/loop_watchdog.py) sends
``WATCHDOG=1`` while the core is healthy. If those keep-alives stop, because
even the watchdog's own thread can't run, systemd ends the process
(SIGABRT, which faulthandler turns into every thread's stack in the
journal) and starts it again.

Off systemd, or under a unit that isn't ``Type=notify`` (``NOTIFY_SOCKET``
unset), every call here does nothing. :meth:`Notifier.from_env` takes the
variables out of the environment as it reads them, so children the core
starts (fingerprinting, ffmpeg, pip) can't speak for it.
"""

from __future__ import annotations

import os
import socket

_ENV_VARS = ("NOTIFY_SOCKET", "WATCHDOG_USEC", "WATCHDOG_PID")


class Notifier:
    """Sends notifications to the service manager's socket, if any."""

    def __init__(self, address: str | None, watchdog_usec: int | None = None) -> None:
        self.address = address or None
        self.watchdog_usec = watchdog_usec if watchdog_usec and watchdog_usec > 0 else None

    @classmethod
    def from_env(cls, *, unset: bool = True) -> "Notifier":
        """The notifier systemd's environment describes. The watchdog
        interval counts only when ``WATCHDOG_PID`` is unset or this
        process (sd_watchdog_enabled(3))."""
        address = os.environ.get("NOTIFY_SOCKET")
        usec: int | None = None
        try:
            usec = int(os.environ.get("WATCHDOG_USEC", "") or 0) or None
        except ValueError:
            usec = None
        pid = os.environ.get("WATCHDOG_PID")
        if usec is not None and pid not in (None, "", str(os.getpid())):
            usec = None
        if unset:
            for var in _ENV_VARS:
                os.environ.pop(var, None)
        return cls(address, usec)

    @property
    def enabled(self) -> bool:
        return self.address is not None and hasattr(socket, "AF_UNIX")

    @property
    def watchdog_sec(self) -> float | None:
        """The unit's WatchdogSec=, or None when no watchdog is armed."""
        return self.watchdog_usec / 1_000_000 if self.watchdog_usec else None

    def notify(self, message: str) -> bool:
        """Send ``message`` (``READY=1``, ``WATCHDOG=1``, ``STATUS=...``).
        True when it went out; never raises."""
        if not self.enabled:
            return False
        address = self.address or ""
        if address.startswith("@"):  # abstract namespace
            address = "\0" + address[1:]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.settimeout(1.0)
                sock.connect(address)
                sock.sendall(message.encode("utf-8", "replace"))
            return True
        except (OSError, ValueError):
            return False
