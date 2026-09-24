"""The guards in conftest.py, asserted rather than assumed.

A guard nothing checks is a guard that quietly stops working. These are
the two properties every other test in this package relies on: it cannot
see the developer's installation, and it cannot open a socket to anything
but loopback.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from satellite import server_identity
from satellite.tests._client_import import AUDIO_MODULES, import_client

client = import_client()


# ─── the developer's own ~/.domovoi is out of reach ───────────────────────

def test_a_test_cannot_see_the_developers_config_dir(real_install_paths):
    real = real_install_paths["config_dir"]
    assert Path.home() != real_install_paths["home"]
    assert Path.home() / ".domovoi" != real
    assert Path("~/.domovoi").expanduser() != real
    assert not str(server_identity.CONFIG_DIR).startswith(str(real))


def test_the_satellite_modules_point_inside_the_tmp_home(satellite_paths_in_tmp):
    home = satellite_paths_in_tmp
    for path in (
        client.CONFIG_PATH,
        client.PAIRING_TOKEN_SIDECAR,
        server_identity.CONFIG_DIR,
        server_identity.RECORD_SIDECAR,
        server_identity.LEGACY_RECORD_SIDECAR,
        server_identity.PENDING_SERVER_SIDECAR,
        server_identity.ROOT_PIN,
    ):
        assert path.is_relative_to(home), f"{path} escaped the tmp home"


def test_the_recorded_fingerprint_is_never_the_developers_one():
    """The pin that made the whole thing visible. Whatever is in the
    developer's real ~/.domovoi, a test sees nothing pinned."""
    assert server_identity.pinned_fingerprint() == (None, "none")


def test_a_path_in_the_source_tree_is_left_alone(satellite_paths_in_tmp):
    """Bundled assets ship with the code and are not a satellite's state.

    Worth pinning because a checkout often sits inside the home directory,
    so a guard that relocated everything under ``$HOME`` would take the
    source tree with it."""
    source = Path(client.__file__).resolve().parent
    for path in (client.GREETINGS_DIR, client.CANNED_NETWORK_ISSUES_MP3):
        assert path.is_relative_to(source)
        assert not path.is_relative_to(satellite_paths_in_tmp)


# ─── nothing reaches off the loopback ─────────────────────────────────────

def test_a_connect_to_a_lan_address_fails_with_a_clear_message(network_guard):
    with pytest.raises(AssertionError) as e:
        socket.create_connection(("192.0.2.10", 6370), timeout=0.01)
    message = str(e.value)
    assert "192.0.2.10:6370" in message
    assert "loopback" in message
    network_guard.clear()


def test_a_swallowed_connect_is_still_recorded(network_guard):
    """The important half: code under test catches broadly, so the raise on
    its own would let a test pass having already sent the packet."""
    s = socket.socket()
    try:
        try:
            s.connect(("192.0.2.10", 6370))
        except Exception:      # noqa: BLE001 — exactly what the real code does
            pass
    finally:
        s.close()
    assert network_guard.clear() == ["192.0.2.10:6370"]


def test_loopback_is_allowed_through(network_guard):
    """The control: the guard blocks the LAN, not the fixtures."""
    s = socket.socket()
    s.settimeout(0.2)
    try:
        with pytest.raises(OSError):      # nothing listening on port 1
            s.connect(("127.0.0.1", 1))
    finally:
        s.close()
    assert network_guard.violations == []


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.53", "localhost", "::1"])
def test_every_loopback_spelling_is_allowed(host, network_guard):
    assert network_guard.is_loopback(host) is True


@pytest.mark.parametrize(
    "host", ["192.168.0.117", "10.0.2.2", "example.test", "0.0.0.0", "1270.0.1"]
)
def test_everything_else_is_not_loopback(host, network_guard):
    assert network_guard.is_loopback(host) is False


# ─── the audio stand-ins do not outlive collection ────────────────────────

def test_a_stand_in_never_survives_into_the_run():
    """conftest makes ``sounddevice`` and ``webrtcvad`` importable only
    while this package is being COLLECTED.

    If one survived into the run, ``test_devices.py``'s
    ``pytest.importorskip("sounddevice")`` would answer yes, and its nine
    Config-precedence tests would exercise a module that does nothing
    instead of skipping. Trading a loud collection error for a quiet wrong
    answer is not a fix."""
    for name in AUDIO_MODULES:
        module = sys.modules.get(name)
        if module is None:
            continue        # not installed on this box, and nothing faked it
        assert module.__spec__ is not None, (
            f"{name} in sys.modules is a test stand-in, not the real module"
        )
