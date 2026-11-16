"""No test reaches the Domovoi installed on the machine running it.

An installed Domovoi answers on THIS machine at the service ports — the
dashboard on 6369, the core on 6370 (``127.0.0.1`` and ``localhost``
included; a Windows host's core in a mirrored-network WSL distro too). A
test that dials one of those, on purpose or by falling through to a
default (a satellite with ``domovoi_url = "ws://127.0.0.1:6370"`` probing
its server, say), talks to somebody's actual house: it reads its answers
and, worse, could change it. So in this process every connection to
loopback, ``localhost`` or the unspecified address at 6369 or 6370 is
refused, and the test that tried fails at teardown — even when the code
under test swallowed the refusal as an ordinary "server offline".

It is watched where every path goes through: ``socket.getaddrinfo``
(``create_connection``, urllib, httpx, websockets and asyncio resolve
there), ``socket.socket.connect`` / ``connect_ex``, and the event loops'
``sock_connect`` (asyncio dials an IP literal without resolving it, and
on Windows the proactor connects without ``socket.connect``). A test that
needs a server starts its own on an ephemeral port. Child processes (the
installer and bash harnesses) are not covered; they run in containers.

Stdlib only: the repo-root conftest installs it for every package.
"""

from __future__ import annotations

import socket
from typing import Any

#: The ports an installed Domovoi serves on: the dashboard and the core.
LIVE_PORTS = frozenset({6369, 6370})

_LOCAL_NAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    "0.0.0.0", "::", "0:0:0:0:0:0:0:0", "::1", "0:0:0:0:0:0:0:1",
})


def _is_this_machine(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    text = host.strip().strip("[]").lower()
    if "%" in text:
        text = text.split("%", 1)[0]
    return text in _LOCAL_NAMES or text.startswith("127.") or text.startswith("::ffff:127.")


def _port(value: object) -> int | None:
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class LivePortGuard:
    def __init__(self) -> None:
        self.violations: list[str] = []

    def check(self, host: object, port: object) -> None:
        number = _port(port)
        if number not in LIVE_PORTS or not _is_this_machine(host):
            return
        where = f"{host}:{number}"
        self.violations.append(where)
        raise ConnectionRefusedError(
            f"a test tried to reach {where}: that is where an installed Domovoi "
            "on this machine answers (domovoi/tests/live_port_guard.py). Start "
            "a fake server on an ephemeral port, or stub the call."
        )

    def check_address(self, address: object) -> None:
        if isinstance(address, tuple) and len(address) >= 2:
            self.check(address[0], address[1])

    def clear(self) -> list[str]:
        seen, self.violations = self.violations, []
        return seen


def install(monkeypatch: Any) -> LivePortGuard:
    """Wrap every connect path with ``monkeypatch`` (undone after the test)."""
    guard = LivePortGuard()
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host, port, *args, **kwargs):
        guard.check(host, port)
        return real_getaddrinfo(host, port, *args, **kwargs)

    def connect(self, address):
        guard.check_address(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        guard.check_address(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)

    from asyncio import proactor_events, selector_events

    for cls in (selector_events.BaseSelectorEventLoop, proactor_events.BaseProactorEventLoop):
        real = cls.sock_connect

        def make(real_sock_connect):
            async def sock_connect(self, sock, address):
                guard.check_address(address)
                return await real_sock_connect(self, sock, address)
            return sock_connect

        monkeypatch.setattr(cls, "sock_connect", make(real))
    return guard
