"""The Wi-Fi watcher heals the network only when the network is what broke.

Found on hardware 2026-09-15: both satellites went to the no-server ring
with the core up the whole time. `iw dev wlan0 link` reports the rate of
the LAST frame, and on an idle link that is the 1 Mbit/s beacon rate, so
the old rule "reassociate below 5 Mbit/s" tore down healthy links every
cooldown - and a reassociate the AP is slow to re-admit is exactly a
satellite that cannot reach the core.

Three rules pinned here:
  * a link carrying a live session is never reassociated, whatever the
    rate reads;
  * with the session down, the watcher asks whether the core answers a
    TCP connect before blaming the Wi-Fi - a core that answers is a core
    problem, not a link problem;
  * and (2026-10-12) a core whose machine REFUSES the connect, or a
    gateway that answers, is not a link problem either: the old rule
    counted a refusal as "unreachable", so every core restart made every
    satellite drop and rejoin the house Wi-Fi. One rule, shared with the
    health self-checks (`health.path_verdict` / `health.link_suspect`).
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


def make_sat(*, ws_up: bool, rx: float | None, core: str | None = "silent",
             gateway_ok: bool | None = False, ticks: int = 2, mode: str = "managed",
             core_answers: bool | None = None):
    """``core`` is the core probe's outcome ("accepted" / "refused" /
    "silent", None for no address yet); ``gateway_ok`` what the gateway
    probe says. ``core_answers`` is the old boolean form: True = accepted."""
    if core_answers is not None:
        core = "accepted" if core_answers else "silent"
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
    link = {"rx_mbits": rx, "tx_mbits": 57.7, "ssid": "Kamber Wifi 2.0",
            "signal_dbm": -55, "mode": mode}
    if mode == "ap":
        link = {"rx_mbits": None, "tx_mbits": None, "ssid": None, "signal_dbm": None, "mode": "ap"}
    sat._check_wifi_link = lambda: dict(link)
    sat._emit_wifi_status = lambda link: sat.calls.append(("emit", link["rx_mbits"]))
    sat._core_probe = lambda timeout=3.0: sat.calls.append(("probe",)) or core
    sat._gateway_ok = lambda: sat.calls.append(("gateway",)) or gateway_ok
    sat._reassociate_wifi = lambda: sat.calls.append(("reassociate",)) or True
    sat._check_net_settings = lambda: None
    return sat


def reassociated(sat) -> bool:
    return ("reassociate",) in sat.calls


def test_a_live_session_is_never_reassociated_even_at_the_beacon_rate():
    """The 2026-09-15 case: rx reads 1.0 Mbit/s on an idle, working link."""
    sat = make_sat(ws_up=True, rx=1.0, core="accepted")
    sat._wifi_watcher_thread_run()
    assert not reassociated(sat)
    assert ("probe",) not in sat.calls, "a live session needs no probe"
    assert ("emit", 1.0) in sat.calls, "the rate is still reported to the dashboard"


def test_a_live_session_clears_degraded_whatever_the_rate():
    sat = make_sat(ws_up=True, rx=1.0, core="accepted")
    sat._network_degraded.set()
    sat._wifi_watcher_thread_run()
    assert not sat._network_degraded.is_set()


def test_ws_down_but_core_answering_is_not_a_wifi_problem():
    """A core that accepts is a core problem (the session), not a link one."""
    sat = make_sat(ws_up=False, rx=1.0, core="accepted")
    sat._wifi_watcher_thread_run()
    assert ("probe",) in sat.calls
    assert not reassociated(sat)
    # Long-down WS still arms degraded, so the next wake gets the canned
    # message - that part is about the session, not the link.
    assert sat._network_degraded.is_set()


def test_a_core_restart_never_makes_the_satellite_rejoin_the_wifi():
    """2026-10-12: while the core restarts its machine REFUSES the connect.
    The old rule read that as "unreachable" and dropped and rejoined the
    house Wi-Fi (a fresh DHCP exchange: a new arrival on the router) on
    every satellite, every core restart, and again every cooldown while
    the core stayed down. A refusal proves the path."""
    sat = make_sat(ws_up=False, rx=1.0, core="refused", gateway_ok=False, ticks=6)
    sat._wifi_watcher_thread_run()
    assert not reassociated(sat)
    assert ("gateway",) not in sat.calls, "a refusal settles it; no second probe"


def test_a_silent_core_behind_an_answering_gateway_is_not_a_wifi_problem():
    """The core's machine is off; the router answers, so the radio works and
    rejoining it cannot bring a switched-off server back."""
    sat = make_sat(ws_up=False, rx=1.0, core="silent", gateway_ok=True)
    sat._wifi_watcher_thread_run()
    assert ("gateway",) in sat.calls
    assert not reassociated(sat)


def test_ws_down_and_nothing_answering_is_when_recovery_fires():
    sat = make_sat(ws_up=False, rx=1.0, core="silent", gateway_ok=False)
    sat._wifi_watcher_thread_run()
    assert reassociated(sat)
    # Asked before and after the recovery.
    assert sat.calls.count(("probe",)) == 2
    assert sat._network_degraded.is_set(), "still silent after recovery -> degraded"


def test_recovery_still_fires_when_the_rate_looks_fine():
    """The decision is the path, not the rate: a healthy-looking rate with
    a dead path (router lost its route, DHCP lease gone) still gets the
    remedy."""
    sat = make_sat(ws_up=False, rx=52.0, core="silent", gateway_ok=False)
    sat._wifi_watcher_thread_run()
    assert reassociated(sat)


def test_no_core_address_yet_is_never_a_reason_to_touch_the_wifi():
    """`auto` before discovery: nothing to probe, so nothing is torn down."""
    sat = make_sat(ws_up=False, rx=1.0, core=None, gateway_ok=False)
    sat._wifi_watcher_thread_run()
    assert not reassociated(sat)


def test_the_watcher_honours_a_recovery_the_health_check_just_ran():
    """Both loops run `_reassociate_wifi`, which stamps one shared clock;
    the watcher's cooldown starts from whichever ran last, so the two never
    tear the link down seconds apart."""
    import time

    sat = make_sat(ws_up=False, rx=52.0, core="silent", gateway_ok=False)
    sat._last_reassociate_at = time.monotonic() - 60.0
    sat._wifi_watcher_thread_run()
    assert not reassociated(sat)
    assert sat._network_degraded.is_set()


def test_an_unassociated_link_is_left_to_the_supplicant():
    sat = make_sat(ws_up=False, rx=None, core="silent", gateway_ok=False)
    sat._wifi_watcher_thread_run()
    assert not reassociated(sat)
    assert ("probe",) not in sat.calls


def test_a_radio_hosting_the_setup_hotspot_is_escalated_and_recovered(caplog):
    """The basement unit, 2026-10-12: 18 days broadcasting its setup
    network. `iw dev wlan0 link` says "Not connected" there, which the old
    watcher left alone as "unassociated"."""
    import logging

    caplog.set_level(logging.ERROR)
    sat = make_sat(ws_up=False, rx=None, mode="ap")
    sat._wifi_watcher_thread_run()
    assert reassociated(sat)
    assert any("HOSTING an access point" in r.getMessage() for r in caplog.records)


def test_hotspot_recovery_honours_the_shared_cooldown():
    import time

    sat = make_sat(ws_up=False, rx=None, mode="ap")
    sat._last_reassociate_at = time.monotonic() - 60.0
    sat._wifi_watcher_thread_run()
    assert not reassociated(sat)
    assert sat._network_degraded.is_set()


@pytest.mark.parametrize(
    "url, expected",
    [
        ("ws://192.168.0.117:6370", ("192.168.0.117", 6370)),
        ("wss://domovoi.local", ("domovoi.local", 443)),
        ("ws://domovoi.local", ("domovoi.local", 80)),
    ],
)
def test_the_core_probe_asks_the_configured_host_and_port(monkeypatch, url, expected):
    seen = []

    def probe_service(host, port, timeout=2.0, connect=None):
        seen.append((host, port))
        return "accepted"

    monkeypatch.setattr(client.health, "probe_service", probe_service)
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(domovoi_url=url)
    assert sat._core_probe() == "accepted"
    assert seen == [expected]


@pytest.mark.parametrize(
    "exc, outcome",
    [(ConnectionRefusedError("refused"), "refused"), (TimeoutError("timed out"), "silent"),
     (OSError(113, "No route to host"), "silent")],
)
def test_the_core_probe_tells_a_refusal_from_silence(monkeypatch, exc, outcome):
    """The rule the watcher and the self-checks share (health.probe_service):
    a refusal is the core's machine answering; a timeout or no route is
    silence."""
    def connect(addr, timeout=None):
        raise exc

    monkeypatch.setattr(client.health.socket, "create_connection", connect)
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(domovoi_url="ws://192.168.0.117:6370")
    real = client.health.probe_service
    monkeypatch.setattr(
        client.health, "probe_service",
        lambda host, port, timeout=2.0: real(host, port, timeout, connect=connect),
    )
    assert sat._core_probe() == outcome


def test_the_core_probe_has_nothing_to_ask_for_an_unresolved_url(monkeypatch):
    """`auto` before discovery: nothing to probe."""
    monkeypatch.setattr(
        client.health, "probe_service",
        lambda *a, **k: pytest.fail("must not probe without a host"),
    )
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(domovoi_url="auto")
    assert sat._core_probe() is None


@pytest.mark.parametrize(
    "core, gateway, verdict",
    [
        ("accepted", None, "core_up"),
        ("refused", False, "core_down"),
        ("silent", True, "lan_up"),
        ("silent", False, "path_down"),
        ("silent", None, "path_down"),
    ],
)
def test_one_reachability_rule(core, gateway, verdict):
    from satellite import health

    assert health.path_verdict(core, gateway) == verdict
    # The self-check table reads the same facts the same way.
    core_host_ok = None if core is None else core != "silent"
    assert health.link_suspect(core_host_ok, gateway) is (verdict == "path_down")


# ─── the reconnect backoff ────────────────────────────────────────────────
#
# SAT-CRASH D1 (2026-10-08): a core that accepts the socket and closes it
# before `ready` (pairing refused, parked for approval, the fail-closed
# path) returned from `_run_session` NORMALLY, which reset the backoff to
# one second. The client then redialled at about 1 Hz for as long as the
# refusal lasted, doing the whole connect on every pass - on a Pi Zero 2 W
# that is a unit pinned at 100 % CPU with nothing to show for it.

import logging  # noqa: E402

from satellite.tests.test_mic_approval_gate import (  # noqa: E402
    accepted, make_sat as make_gate_sat, parked, run_sessions,
)


def _session(attempt):
    """Mirror the real `_run_session`, which forgets the previous
    session's `ready` before it dials."""
    async def run(sat):
        sat._session_ready_seen = False
        outcome = attempt(sat)
        if outcome is not None:
            await outcome
    return run


def _waits(caplog) -> list[float]:
    return [
        float(r.getMessage().split("reconnecting in ")[1].rstrip("s"))
        for r in caplog.records
        if r.getMessage().startswith("reconnecting in ")
    ]


def test_a_session_refused_before_ready_backs_off_like_a_failed_connect(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    rec = make_gate_sat(monkeypatch)
    run_sessions(rec, [_session(parked()) for _ in range(5)], monkeypatch)
    assert _waits(caplog) == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert rec.s.fatal_error is None, "a refusal is not a crash"


def test_an_accepted_session_resets_the_backoff(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    rec = make_gate_sat(monkeypatch)
    run_sessions(
        rec,
        [_session(parked()), _session(parked()), _session(accepted()), _session(parked())],
        monkeypatch,
    )
    assert _waits(caplog) == [1.0, 2.0, 1.0, 2.0]


def test_the_backoff_is_capped_at_a_minute(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    rec = make_gate_sat(monkeypatch)
    run_sessions(rec, [_session(parked()) for _ in range(9)], monkeypatch)
    assert _waits(caplog)[-3:] == [60.0, 60.0, 60.0]
    assert client._BACKOFF_MAX_SEC == 60.0
