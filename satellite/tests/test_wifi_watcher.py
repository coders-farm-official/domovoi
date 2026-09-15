"""The Wi-Fi watcher heals the network only when the network is what broke.

Found on hardware 2026-09-15: both satellites went to the no-server ring
with the core up the whole time. `iw dev wlan0 link` reports the rate of
the LAST frame, and on an idle link that is the 1 Mbit/s beacon rate, so
the old rule "reassociate below 5 Mbit/s" tore down healthy links every
cooldown - and a reassociate the AP is slow to re-admit is exactly a
satellite that cannot reach the core.

Two rules pinned here:
  * a link carrying a live session is never reassociated, whatever the
    rate reads;
  * with the session down, the watcher asks whether the core answers a
    TCP connect before blaming the Wi-Fi - a core that answers is a core
    problem, not a link problem.
"""

from __future__ import annotations

import threading
import types

import pytest

from satellite.tests._client_import import import_client

client = import_client()


class OneTick(threading.Event):
    """Lets the watcher loop run exactly one poll, then reports shutdown.

    The first wait (the poll sleep) returns False so the tick runs; the
    settle wait after a reassociate returns False too; the next poll sleep
    returns True. `is_set` is left alone so the `while` guard passes.
    """

    def __init__(self, ticks: int = 1):
        super().__init__()
        self.waits_left = ticks

    def wait(self, timeout=None):  # noqa: D401 - Event signature
        if self.waits_left > 0:
            self.waits_left -= 1
            return False
        return True


def make_sat(*, ws_up: bool, rx: float | None, core_answers: bool, ticks: int = 2):
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(
        wifi_enabled=True,
        wifi_poll_interval_sec=60.0,
        wifi_min_healthy_mbits=5.0,
        wifi_cooldown_sec=300.0,
        wifi_degraded_after_disconnect_sec=30.0,
        domovoi_url="ws://192.168.0.117:6370",
    )
    sat.shutdown_event = OneTick(ticks)
    sat._network_degraded = threading.Event()
    # None = the WS is up; a monotonic stamp far in the past = long down.
    sat._ws_disconnected_since = None if ws_up else 1.0
    sat.calls = []
    sat._check_wifi_link = lambda: {"rx_mbits": rx, "tx_mbits": 57.7, "ssid": "Kamber Wifi 2.0"}
    sat._emit_wifi_status = lambda link: sat.calls.append(("emit", link["rx_mbits"]))
    sat._core_reachable = lambda timeout=3.0: sat.calls.append(("probe",)) or core_answers
    sat._reassociate_wifi = lambda: sat.calls.append(("reassociate",)) or True
    return sat


def reassociated(sat) -> bool:
    return ("reassociate",) in sat.calls


def test_a_live_session_is_never_reassociated_even_at_the_beacon_rate():
    """The 2026-09-15 case: rx reads 1.0 Mbit/s on an idle, working link."""
    sat = make_sat(ws_up=True, rx=1.0, core_answers=True)
    sat._wifi_watcher_thread_run()
    assert not reassociated(sat)
    assert ("probe",) not in sat.calls, "a live session needs no probe"
    assert ("emit", 1.0) in sat.calls, "the rate is still reported to the dashboard"


def test_a_live_session_clears_degraded_whatever_the_rate():
    sat = make_sat(ws_up=True, rx=1.0, core_answers=True)
    sat._network_degraded.set()
    sat._wifi_watcher_thread_run()
    assert not sat._network_degraded.is_set()


def test_ws_down_but_core_answering_is_not_a_wifi_problem():
    """A core restart must not turn into a self-inflicted link outage."""
    sat = make_sat(ws_up=False, rx=1.0, core_answers=True)
    sat._wifi_watcher_thread_run()
    assert ("probe",) in sat.calls
    assert not reassociated(sat)
    # Long-down WS still arms degraded, so the next wake gets the canned
    # message - that part is about the session, not the link.
    assert sat._network_degraded.is_set()


def test_ws_down_and_core_silent_is_when_reassociate_fires():
    sat = make_sat(ws_up=False, rx=1.0, core_answers=False)
    sat._wifi_watcher_thread_run()
    assert reassociated(sat)
    # Probed before and after the reassociate.
    assert sat.calls.count(("probe",)) == 2
    assert sat._network_degraded.is_set(), "still silent after reassociate -> degraded"


def test_reassociate_still_fires_when_the_rate_looks_fine():
    """The decision is the path to the core, not the rate: a healthy-looking
    rate with a dead path (router lost its route, DHCP lease gone) still gets
    the remedy."""
    sat = make_sat(ws_up=False, rx=52.0, core_answers=False)
    sat._wifi_watcher_thread_run()
    assert reassociated(sat)


def test_an_unassociated_link_is_left_to_the_supplicant():
    sat = make_sat(ws_up=False, rx=None, core_answers=False)
    sat._wifi_watcher_thread_run()
    assert not reassociated(sat)
    assert ("probe",) not in sat.calls


@pytest.mark.parametrize(
    "url, expected",
    [
        ("ws://192.168.0.117:6370", ("192.168.0.117", 6370)),
        ("wss://domovoi.local", ("domovoi.local", 443)),
        ("ws://domovoi.local", ("domovoi.local", 80)),
    ],
)
def test_core_reachable_probes_the_configured_host_and_port(monkeypatch, url, expected):
    seen = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def create_connection(addr, timeout=None):
        seen.append(addr)
        return Conn()

    monkeypatch.setattr(client.socket, "create_connection", create_connection)
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(domovoi_url=url)
    assert sat._core_reachable() is True
    assert seen == [expected]


def test_core_reachable_is_false_on_refused_or_timeout(monkeypatch):
    def refuse(addr, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(client.socket, "create_connection", refuse)
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(domovoi_url="ws://192.168.0.117:6370")
    assert sat._core_reachable() is False


def test_core_reachable_never_blames_the_link_for_an_unresolved_url(monkeypatch):
    """`auto` before discovery: nothing to probe, so don't tear anything down."""
    monkeypatch.setattr(
        client.socket, "create_connection",
        lambda *a, **k: pytest.fail("must not probe without a host"),
    )
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(domovoi_url="auto")
    assert sat._core_reachable() is True
