"""The client's half of the clock sync: once the server says ``ready``, ask
the root helper to copy its clock and time zone - once per connect, off
the receiver thread, and never as a reason to fail.

Found on hardware: a satellite five hours and three months out, and no
path on the device that would ever have corrected it.
"""

from __future__ import annotations

import inspect
import sys
import types

import pytest


def _import_client():
    """The client imports the audio stack at module level. On a dev box
    without it, an empty stand-in is enough to read the code under test;
    the stand-ins are removed again so a later ``importorskip`` elsewhere
    still sees the truth."""
    stubbed = []
    for name in ("sounddevice", "webrtcvad"):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = types.ModuleType(name)
            stubbed.append(name)
    try:
        from satellite import client
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)
    return client


client = _import_client()


def test_ready_is_the_moment():
    """`ready` proves the server we reached is the one we are paired to."""
    src = inspect.getsource(client.Satellite._handle_text_frame)
    ready = src.split('elif t == "transcript"')[0]
    assert "self._sync_time_with_server()" in ready


def test_the_helper_is_called_through_sudo_with_the_server_url(monkeypatch):
    calls = []
    monkeypatch.setattr(client.sys, "platform", "linux")
    monkeypatch.setattr(client.os, "access", lambda p, m: p == client.SYNC_TIME_HELPER)

    def run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(
            returncode=0,
            stdout="tz America/New_York (unchanged); clock within 0.02s of the server (left alone)\n",
            stderr="",
        )

    monkeypatch.setattr(client.subprocess, "run", run)
    verdict = client._sync_time_with_server("ws://192.168.0.117:6370")
    assert calls == [["sudo", "-n", "/usr/local/sbin/domovoi-sync-time", "ws://192.168.0.117:6370"]]
    assert verdict.startswith("tz America/New_York")


def test_a_hand_built_unit_without_the_helper_is_left_alone(monkeypatch):
    monkeypatch.setattr(client.sys, "platform", "linux")
    monkeypatch.setattr(client.os, "access", lambda p, m: False)
    monkeypatch.setattr(client.subprocess, "run", lambda *a, **k: pytest.fail("no helper, no sudo"))
    assert client._sync_time_with_server("ws://x:6370") is None


def test_a_failure_is_logged_and_never_raised(monkeypatch, caplog):
    monkeypatch.setattr(client.sys, "platform", "linux")
    monkeypatch.setattr(client.os, "access", lambda p, m: True)

    def boom(*a, **k):
        raise OSError("sudo: command not found")

    monkeypatch.setattr(client.subprocess, "run", boom)
    assert client._sync_time_with_server("ws://x:6370") is None

    monkeypatch.setattr(
        client.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=2, stdout="", stderr="sudo: a password is required"),
    )
    with caplog.at_level("WARNING"):
        assert client._sync_time_with_server("ws://x:6370") is None
    assert "password is required" in caplog.text


def test_one_sync_per_connect_not_per_flap(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            started.append((target, args, name, daemon))

        def start(self):
            pass

    monkeypatch.setattr(client.threading, "Thread", FakeThread)
    sat = types.SimpleNamespace(
        cfg=types.SimpleNamespace(domovoi_url="ws://x:6370"),
        _time_synced_at=None,
        _TIME_SYNC_MIN_INTERVAL_SEC=client.Satellite._TIME_SYNC_MIN_INTERVAL_SEC,
    )
    client.Satellite._sync_time_with_server(sat)
    client.Satellite._sync_time_with_server(sat)
    assert len(started) == 1
    target, args, name, daemon = started[0]
    assert target is client._sync_time_with_server
    assert args == ("ws://x:6370",) and name == "time-sync" and daemon is True
    # ...and again once the interval has passed.
    sat._time_synced_at -= client.Satellite._TIME_SYNC_MIN_INTERVAL_SEC + 1
    client.Satellite._sync_time_with_server(sat)
    assert len(started) == 2
