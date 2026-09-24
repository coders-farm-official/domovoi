"""Satellite tests run against fixtures, never against a real installation.

Two guarantees, for every test in this package, whether or not the test
asked for them:

* **No real config.** ``HOME`` points at a tmp dir, and every module-level
  path in the ``satellite`` package that pointed inside the developer's
  ``~/.domovoi`` (or at ``/etc/domovoi``) is re-pointed under it. A test
  therefore cannot read the developer's ``config.toml``, their recorded
  server fingerprint, their pairing token — and cannot write one either.

* **No socket to anything but loopback.** A satellite's whole job is to
  find a server and talk to it, so a fixture that leaves a real address in
  a ``Config`` will happily connect to whatever is at that address. That is
  not a hypothetical: ``test_send_queue_lifecycle`` built a ``Config``
  holding the house's real core and, once the identity check landed in
  front of every connect, reached it on every ``pytest`` run. It only ever
  surfaced because the pin happened to disagree.

Both are failures, not skips, and both name the thing they caught. The
answer to "this test wants to talk to a server" is a loopback fixture — not
a pinned fingerprint, and not an exclusion here.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

# Resolved once, at import, while the environment is still the real one.
REAL_HOME = Path.home()
REAL_CONFIG_DIR = REAL_HOME / ".domovoi"
ROOT_CONFIG_DIR = Path("/etc/domovoi")


@pytest.fixture(scope="session")
def real_install_paths():
    """The developer's own directories, as they were before any of this ran.

    A fixture rather than a module import: ``satellite/tests`` is a
    namespace package, so ``from satellite.tests import conftest`` loads a
    SECOND copy whose module-level ``Path.home()`` resolves to whatever the
    test's environment says — which is the tmp home, making the assertion
    it was written for pass no matter what."""
    return {"home": REAL_HOME, "config_dir": REAL_CONFIG_DIR}


# ─── no real config, and no real home ─────────────────────────────────────

def _relocate(value: Path, home: Path) -> Path | None:
    """``value`` moved under ``home``, or None if it was never ours to
    move.

    Only the two directories a satellite install owns are touched. Not the
    home directory at large: a checkout can perfectly well live under it
    (this repo is often checked out into a temp dir inside ``$HOME``), and
    relocating the source tree would take the bundled sounds and greetings
    with it."""
    for root, stand_in in ((REAL_CONFIG_DIR, home / ".domovoi"),
                           (ROOT_CONFIG_DIR, home / "etc-domovoi")):
        try:
            rel = value.relative_to(root)
        except ValueError:
            continue
        return stand_in / rel if rel.parts else stand_in
    return None


@pytest.fixture(autouse=True)
def satellite_paths_in_tmp(tmp_path, monkeypatch):
    """Every path a satellite install owns, moved into this test's tmp dir.

    Two mechanisms, because module constants are computed at import time:
    ``HOME`` is repointed so anything computed from now on lands in tmp,
    and every already-imported ``satellite.*`` module has its ``Path``
    attributes rebound. Together they cover a module imported before the
    fixture and one imported inside the test body."""
    home = tmp_path / "home"
    (home / ".domovoi").mkdir(parents=True, exist_ok=True)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    # Windows builds a home from these two when USERPROFILE is absent;
    # leaving them set would let expanduser find the real one anyway.
    for var in ("HOMEDRIVE", "HOMEPATH"):
        monkeypatch.delenv(var, raising=False)

    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "") or ""
        if name != "satellite" and not name.startswith("satellite."):
            continue
        for attr, value in list(vars(module).items()):
            if not isinstance(value, Path):
                continue
            moved = _relocate(value, home)
            if moved is not None:
                monkeypatch.setattr(module, attr, moved, raising=False)
    return home


# ─── no socket to anything but loopback ───────────────────────────────────

def _is_loopback(host: object) -> bool:
    if not isinstance(host, str):
        return False
    text = host.strip("[]").lower()
    return (
        text in ("localhost", "localhost.localdomain", "::1", "0:0:0:0:0:0:0:1")
        or text.startswith("127.")
    )


class NetworkGuard:
    """Records — and refuses — every connect to a non-loopback address."""

    is_loopback = staticmethod(_is_loopback)

    def __init__(self) -> None:
        self.violations: list[str] = []

    def check(self, address: object) -> None:
        if not isinstance(address, tuple) or not address:
            return      # AF_UNIX and friends: nothing on the wire here
        host = address[0]
        if _is_loopback(host):
            return
        where = ":".join(str(part) for part in address[:2])
        self.violations.append(where)
        raise AssertionError(
            f"a satellite test tried to open a socket to {where}. Satellite "
            "tests talk to loopback fixtures only — this address came from a "
            "real config file or a hard-coded LAN literal, and on a "
            "developer's machine it is somebody's actual house. Give the "
            "test a 127.0.0.1 fixture (or stub the call); do not pin a "
            "fingerprint to make it pass. See satellite/tests/conftest.py."
        )

    def clear(self) -> list[str]:
        seen, self.violations = self.violations, []
        return seen


@pytest.fixture(autouse=True)
def network_guard(monkeypatch):
    """Fails the test for any non-loopback connect, even a swallowed one.

    The raise alone is not enough: the code under test catches broadly on
    purpose (an unreachable server is an ordinary condition for a
    satellite), so a blocked connect can be turned into a quiet "could not
    reach it" and the test would pass while still having sent the packet.
    The recorded violation is what makes it fail."""
    guard = NetworkGuard()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection

    def connect(self, address):
        guard.check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        guard.check(address)
        return real_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):
        guard.check(address)
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    yield guard
    outstanding = guard.clear()
    if outstanding:
        pytest.fail(
            "this test opened a socket to a non-loopback address: "
            + ", ".join(outstanding)
            + " — see satellite/tests/conftest.py"
        )
