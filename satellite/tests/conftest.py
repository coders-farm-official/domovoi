"""Satellite tests run against fixtures, never against a real installation.

Three guarantees, for every test in this package, whether or not the test
asked for them:

* **Collectable without the audio stack.** ``satellite/client.py`` imports
  ``sounddevice`` and ``webrtcvad`` at module scope, and a dev box has
  neither. A test module that imports the client therefore does not fail —
  it fails to be COLLECTED, and one collection error ends the whole pytest
  run, every other package with it. Stand-ins go in here, before pytest
  imports a single module from this directory, and come out again the
  moment collection is over. Which module is collected first stops
  mattering.

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

# Run this package with `python -m pytest` from the repo root.
#
# This directory has no `__init__.py`, so pytest's prepend import mode stops
# here: it puts `satellite/tests` on sys.path, not the repo root, and
# `satellite` is not in the editable install's MAPPING (that lists `domovoi`
# and `web` only, pointing at whichever checkout `pip install -e .` was run
# from — from a git worktree, the OTHER tree). The bare `pytest` console
# script used to die here with "No module named 'satellite'"; since the
# repo-root conftest.py (2026-10-15) every invocation inside the repo puts
# the repo root on sys.path first, before anything imports `domovoi`, so it
# works and imports this tree. `python -m pytest` puts the cwd at
# sys.path[0] and never depended on that.
#
# Adding an `__init__.py` here would also put the repo root on sys.path, but
# it is not a drive-by fix. It changes the import name of every module in
# this package, and it would quietly invalidate the reason `real_install_paths`
# below is a fixture at all: that reasoning is about this file being loadable
# a SECOND time as `satellite.tests.conftest`, which is a property of the
# namespace package. Change one and re-read the other. The invocation is the
# cheaper thing to get right.

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

# The home before anything below computes a path from it: a temp folder for
# the session (domovoi/tests/home_isolation.py; the repo-root conftest.py
# normally got there first). Module constants built at import point inside
# SESSION_CONFIG_DIR, and the fixture below moves them on into each test's
# own tmp dir.
from domovoi.tests import home_isolation

home_isolation.isolate()

from satellite.tests._client_import import (  # noqa: E402
    install_audio_stand_ins,
    remove_audio_stand_ins,
)

# The developer's own, as it was before the run moved it.
REAL_HOME = home_isolation.real_home()
REAL_CONFIG_DIR = REAL_HOME / ".domovoi"
SESSION_CONFIG_DIR = Path.home() / ".domovoi"
ROOT_CONFIG_DIR = Path("/etc/domovoi")
# Root's state directory (satellite/net_settings.py keeps its record there).
ROOT_STATE_DIR = Path("/var/lib/domovoi")
# Root's units (satellite/net_settings.py installs domovoi-hostname.service
# there and reads the boot step's enable link).
ROOT_UNIT_DIR = Path("/etc/systemd/system")
# Where it installs the early unit and its root-owned helper.
ROOT_LOCAL_UNIT_DIR = Path("/usr/local/lib/systemd/system")
ROOT_HELPER_DIR = Path("/usr/local/lib/domovoi")


@pytest.fixture(scope="session")
def real_install_paths():
    """The developer's own directories, as they were before any of this ran.

    A fixture rather than a module import: ``satellite/tests`` is a
    namespace package, so ``from satellite.tests import conftest`` loads a
    SECOND copy whose module-level ``Path.home()`` resolves to whatever the
    test's environment says — which is the tmp home, making the assertion
    it was written for pass no matter what."""
    return {"home": REAL_HOME, "config_dir": REAL_CONFIG_DIR}


# ─── collectable without the audio stack ──────────────────────────────────
#
# Removed again in `pytest_collection_finish`, which runs after every module
# has been imported and before the first test does anything. Run-time code
# therefore sees the real `sys.modules`: `test_devices.py` asks
# `pytest.importorskip("sounddevice")` for the truth and still gets it.

_AUDIO_STAND_INS = install_audio_stand_ins()


def pytest_collection_finish(session):
    remove_audio_stand_ins(_AUDIO_STAND_INS)


# ─── no real config, and no real home ─────────────────────────────────────

def _relocate(value: Path, home: Path) -> Path | None:
    """``value`` moved under ``home``, or None if it was never ours to
    move.

    Only the two directories a satellite install owns are touched. Not the
    home directory at large: a checkout can perfectly well live under it
    (this repo is often checked out into a temp dir inside ``$HOME``), and
    relocating the source tree would take the bundled sounds and greetings
    with it."""
    for root, stand_in in ((SESSION_CONFIG_DIR, home / ".domovoi"),
                           (REAL_CONFIG_DIR, home / ".domovoi"),
                           (ROOT_CONFIG_DIR, home / "etc-domovoi"),
                           (ROOT_STATE_DIR, home / "var-lib-domovoi"),
                           (ROOT_UNIT_DIR, home / "etc-systemd-system"),
                           (ROOT_LOCAL_UNIT_DIR, home / "usr-local-lib-systemd-system"),
                           (ROOT_HELPER_DIR, home / "usr-local-lib-domovoi")):
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
    # The card's boot partition (satellite/setup_report.py writes
    # domovoi/device-info.json there). A tuple of roots outside the home, so
    # the loop above can't move it; on a Linux runner /boot/firmware/domovoi
    # could even exist. Nothing is written unless a test makes the folder.
    from satellite import setup_report

    monkeypatch.setattr(setup_report, "BOOT_DIRS", (home / "boot",))
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
def this_pcs_cpp_runtime_is_not_the_test_s(monkeypatch):
    """A Windows engine refuses to start without Microsoft's C++ runtime
    (engine_platform.windows_runtime_problem). Whether this machine has a
    new enough one is not what the engine's other tests are about: there it
    reads as present. satellite/tests/test_vc_runtime.py holds the real
    check (it keeps its own reference to the function)."""
    from satellite import engine_platform

    monkeypatch.setattr(engine_platform, "windows_runtime_problem", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def no_server_name_lookups(monkeypatch):
    """Server discovery tries the core's NAME first (``domovoi.local``,
    satellite/discovery.py). Resolved for real, that is a multicast query
    on the developer's actual house network — and on a machine where the
    house's real core answers, the address it returns would then be dialled
    (and refused by :func:`network_guard`). So a name nobody gave a test a
    resolver for does not resolve. A test of the name order passes its own
    ``resolver=``."""
    from satellite import discovery

    def refuse(host, *args, **kwargs):
        if _is_loopback(host):
            return socket.getaddrinfo(host, *args, **kwargs)
        raise socket.gaierror(getattr(socket, "EAI_NONAME", -2), f"{host}: no lookups in satellite tests")

    monkeypatch.setattr(discovery, "_DEFAULT_RESOLVER", refuse)


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
