"""The health monitor's wiring into the client: what the sample says about
the process, what the self-checks do to it, the previous life's record in
the hello, and the spool hand-off on `ready`. The table itself is tested
in test_health_sampler.py; this is the glue around it, off a Pi."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import types

import pytest

from satellite import health, log_spool
from satellite.tests._client_import import import_client
from satellite.tests.test_send_queue_lifecycle import FakeConnect, FakeServer, make_sat, settle

client = import_client()


@pytest.fixture(autouse=True)
def no_hard_exit(monkeypatch):
    """A self-check exit arms an `os._exit` backstop; in this process that
    would end the whole test run 45 s later. Every test here gets a
    harmless one (the backstop itself is asserted by its own test)."""
    monkeypatch.setattr(client, "_hard_exit", lambda code: None)


def health_sat(tmp_path, *, loop=None):
    """A Satellite with the state the health monitor reads and writes."""
    sat = make_sat(loop=loop)
    sat.cfg.health_enabled = True
    sat.cfg.health_rss_limit_mb = 300.0
    sat.cfg.health_fd_limit = 512
    sat.ws = None
    sat._voice_input_lock = threading.Lock()
    sat._input_stream = object()
    sat._voice_input_started = True
    sat._voice_gate_closed = False
    sat._health_budget = health.RestartBudget(tmp_path / "self-restarts.json")
    sat._mic_thread = types.SimpleNamespace(is_alive=lambda: True)
    sat._turn_open = False
    sat._last_health = health.LastHealth(tmp_path / "last-health.json")
    sat._health_sampler = health.Sampler(
        proc_root=str(tmp_path / "no-proc"), sys_root=str(tmp_path / "no-sys"),
        run=lambda argv, timeout=5.0: None,
        connect=lambda addr, timeout=None: (_ for _ in ()).throw(OSError("no route")),
    )
    sat.fatal_error = None
    sat._async_shutdown = None
    return sat


# ─── the process half of the sample ───────────────────────────────────────


def test_process_facts_report_the_mic_rate_and_the_socket_state(tmp_path):
    sat = health_sat(tmp_path)
    sat._mic_frames_total = 0
    first = sat._health_process_facts(100.0)
    assert first["mic_fps"] is None, "no rate before a second reading"
    assert first["ws"] == "down" and first["ws_down_for_s"] is None
    assert first["mic_expected"] is True and first["idle"] is True
    sat._health_last_sample_at = 100.0
    sat._mic_frames_total = 1998            # 33.3 frames/s over 60 s
    sat.ws = object()
    sat._session_ready_seen = True
    sat._wake_heartbeat = 159.6
    second = sat._health_process_facts(160.0)
    assert second["mic_fps"] == 33.3
    assert second["ws"] == "up"
    assert second["wake_loop_age_s"] == 0.4
    assert "log_ring" in second and "log_spool" in second


def test_a_sample_goes_out_only_when_the_core_lists_health(tmp_path):
    async def scenario():
        sat = health_sat(tmp_path, loop=asyncio.get_running_loop())
        sat.send_q = asyncio.Queue()
        sat.ws = object()
        sat._session_ready_seen = True
        sat._core_features = frozenset()
        sat._health_sample_now(10.0)
        await settle()
        assert sat.send_q.qsize() == 0, "an older core would answer with error"
        sat._core_features = frozenset({"health"})
        sat._health_sample_now(70.0)
        await settle()
        kind, raw = sat.send_q.get_nowait()
        frame = json.loads(raw)
        assert frame["type"] == "health"
        assert frame["ws"] == "up" and frame["core_ok"] is True
        assert frame["seq"] == 2
        assert len(raw) <= health.SAMPLE_MAX_BYTES + 64

    asyncio.run(scenario())


# ─── the self-checks act on the process ───────────────────────────────────
#
# Every check needs TWO consecutive observations before it acts (two ticks
# for the mic and the wake loop, two samples for RSS, fds and the network),
# and anything that ends the process is counted against a budget of three
# self-restarts an hour, kept on disk across lives.


def sample(seq: int, **kw):
    doc = {"seq": seq, "rss_kb": 50_000, "fds": 30}
    doc.update(kw)
    return doc


def live_tick(sat, t: float) -> None:
    """One check with the microphone delivering, as on a healthy unit."""
    sat._mic_frames_total += 1000
    sat._health_checks(t)


def test_a_dead_mic_thread_exits_with_the_reason_on_disk(tmp_path, caplog):
    sat = health_sat(tmp_path)
    sat._mic_thread = types.SimpleNamespace(is_alive=lambda: False)
    sat._health_last_sample = sample(1)
    caplog.set_level(logging.ERROR)
    sat._health_checks(500.0)
    assert sat.fatal_error is None, "one observation is never enough"
    sat._health_checks(530.0)
    assert sat.fatal_error and "mic thread is dead" in sat.fatal_error
    assert sat.shutdown_event.is_set()
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert "mic thread is dead" in doc["last_exit_reason"]
    assert doc["actions"][-1]["action"] == "exit"
    assert doc["restart_budget"]["recent"][-1]["action"] == "exit"
    assert any("self-check" in r.getMessage() for r in caplog.records)


def test_rss_over_the_limit_needs_two_idle_samples(tmp_path):
    sat = health_sat(tmp_path)
    sat._health_last_sample = sample(1, rss_kb=320 * 1024)
    sat._turn_open = True
    live_tick(sat, 500.0)
    assert sat.fatal_error is None, "mid-turn: never"
    sat._turn_open = False
    live_tick(sat, 530.0)
    live_tick(sat, 560.0)
    assert sat.fatal_error is None, "the same sample twice is one observation"
    sat._health_last_sample = sample(2, rss_kb=321 * 1024)
    live_tick(sat, 590.0)
    assert sat.fatal_error and "RSS 321 MB" in sat.fatal_error


def test_a_transient_rss_spike_is_forgiven(tmp_path):
    sat = health_sat(tmp_path)
    sat._health_last_sample = sample(1, rss_kb=320 * 1024)
    live_tick(sat, 500.0)
    sat._health_last_sample = sample(2, rss_kb=150 * 1024)
    live_tick(sat, 560.0)
    sat._health_last_sample = sample(3, rss_kb=320 * 1024)
    live_tick(sat, 620.0)
    assert sat.fatal_error is None, "over, under, over: never two in a row"


def test_silent_mic_is_reopened_once_then_exits(tmp_path, caplog):
    sat = health_sat(tmp_path)
    sat._health_last_sample = sample(1)
    opened: list[str] = []
    sat._stop_mic = lambda: opened.append("stop")
    sat._start_mic = lambda: opened.append("start")
    caplog.set_level(logging.WARNING)
    sat._mic_frames_total = 10
    sat._health_checks(100.0)            # frames moving: nothing
    sat._health_checks(130.0)            # silent 30 s: first observation
    assert opened == []
    sat._health_checks(160.0)            # silent 60 s: reopen
    assert opened == ["stop", "start"], "reopened exactly once"
    assert sat.fatal_error is None
    sat._health_checks(190.0)
    assert sat.fatal_error is None
    sat._health_checks(220.0)            # still nothing 60 s after the reopen
    assert sat.fatal_error and "still no mic frames" in sat.fatal_error
    assert opened == ["stop", "start"], "never reopened a second time"


def test_frames_flowing_again_forgive_the_reopen(tmp_path):
    sat = health_sat(tmp_path)
    sat._health_last_sample = sample(1)
    sat._stop_mic = lambda: None
    sat._start_mic = lambda: None
    sat._mic_frames_total = 10
    sat._health_checks(100.0)
    sat._health_checks(130.0)
    sat._health_checks(160.0)            # silent 60 s -> reopened
    assert sat._health_mic_reopened_at == 160.0
    sat._mic_frames_total = 500          # the reopen worked
    sat._health_checks(190.0)
    assert sat._health_mic_reopened_at is None and sat._health_mic_silent_since is None
    assert sat.fatal_error is None


def test_a_closed_voice_gate_is_not_a_silent_mic_and_is_never_reopened(tmp_path):
    """The approval refusal stops capture on purpose; the health check must
    neither count that as silence nor undo it (even in a race where the gate
    closes between the decision and the reopen)."""
    sat = health_sat(tmp_path)
    sat._health_last_sample = sample(1)
    opened: list[str] = []
    sat._stop_mic = lambda: opened.append("stop")
    sat._start_mic = lambda: opened.append("start")
    sat._voice_gate_closed = True
    sat._input_stream = None
    for t in (100.0, 130.0, 160.0, 190.0, 220.0):
        sat._health_checks(t)
    assert opened == [] and sat.fatal_error is None
    # The race: the check decided while the gate was open, the gate closed.
    sat._health_reopen_mic("decided a moment ago", 250.0)
    assert opened == [], "a reopen never reopens a closed gate"


def test_a_failed_reopen_is_counted_and_retried_when_the_exit_is_held_back(tmp_path):
    sat = health_sat(tmp_path)
    sat._health_last_sample = sample(1)
    for i in range(3):                       # the hour's budget is already spent
        sat._health_budget.record("exit", f"earlier {i}")
    tries: list[float] = []

    def broken():
        tries.append(1)
        raise RuntimeError("Error querying device -1")

    sat._stop_mic = lambda: None
    sat._start_mic = broken
    sat._mic_frames_total = 10
    for t in (100.0, 130.0, 160.0):
        sat._health_checks(t)
    assert len(tries) == 1 and sat.fatal_error is None, "the exit is held back"
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert doc["restart_budget"]["spent"] is True and doc["restart_budget"]["suppressed"] >= 1
    # Ticks in between only log (held back); five minutes on, it reopens again.
    t = 190.0
    while t < 160.0 + 300.0:
        sat._health_checks(t)
        t += 30.0
    assert len(tries) == 1
    sat._health_checks(t)
    sat._health_checks(t + 30.0)
    assert len(tries) == 2


def test_the_wake_loop_right_after_a_long_chat_is_not_stuck(tmp_path):
    """The heartbeat is not stamped during a chat, a call or a turn. The
    chat loop is still playing the goodbye when `chat_end` clears the flag,
    so for a few seconds the client is idle with a heartbeat as old as the
    whole session. That is not a stuck loop."""
    sat = health_sat(tmp_path)
    sat._health_last_sample = sample(1)
    sat._mic_frames_total = 0
    sat._wake_heartbeat = 0.0
    sat.chat_active.set()
    for i, t in enumerate((100.0, 130.0, 160.0, 190.0)):
        sat._mic_frames_total += 1000
        sat._health_checks(t)
    sat.chat_active.clear()                  # chat_end; the goodbye still plays
    sat._mic_frames_total += 1000
    sat._health_checks(220.0)                # idle, heartbeat 220 s old
    sat._wake_heartbeat = 221.0              # the wake loop is back
    sat._mic_frames_total += 1000
    sat._health_checks(250.0)
    sat._mic_frames_total += 1000
    sat._health_checks(280.0)
    assert sat.fatal_error is None


def test_a_wake_loop_that_never_comes_back_after_a_turn_exits(tmp_path):
    sat = health_sat(tmp_path)
    sat._health_last_sample = sample(1)
    sat._wake_heartbeat = 0.0
    sat._turn_open = True
    sat._health_checks(100.0)
    sat._turn_open = False                   # and the loop never ticks again
    t = 130.0
    while sat.fatal_error is None and t < 400.0:
        sat._mic_frames_total += 1000
        sat._health_checks(t)
        t += 30.0
    assert sat.fatal_error and "wake loop has not ticked" in sat.fatal_error
    assert t - 30.0 >= 130.0 + 60.0, "measured from when it went idle, not from boot"


def test_the_wait_for_wake_stamps_the_heartbeat_before_the_turn_closes():
    import inspect

    src = inspect.getsource(client.Satellite._wait_for_wake)
    assert src.index("self._wake_heartbeat = time.monotonic()") < src.index("self._turn_open = False")


def test_the_restart_budget_holds_a_looping_check_to_three_an_hour(tmp_path, caplog):
    caplog.set_level(logging.ERROR)
    path = tmp_path / "self-restarts.json"
    exits = 0
    clock = [1_000_000.0]
    for life in range(5):
        sat = health_sat(tmp_path)
        sat._health_budget = health.RestartBudget(path, clock=lambda: clock[0])
        sat._mic_thread = types.SimpleNamespace(is_alive=lambda: False)
        sat._health_last_sample = sample(1)
        sat._health_checks(100.0)
        sat._health_checks(130.0)
        if sat.fatal_error:
            exits += 1
        clock[0] += 120.0
    assert exits == 3, "a wrong trigger costs three restarts an hour, no more"
    assert any("HELD BACK" in r.getMessage() for r in caplog.records)
    clock[0] += 3600.0
    sat = health_sat(tmp_path)
    sat._health_budget = health.RestartBudget(path, clock=lambda: clock[0])
    sat._mic_thread = types.SimpleNamespace(is_alive=lambda: False)
    sat._health_last_sample = sample(1)
    sat._health_checks(100.0)
    sat._health_checks(130.0)
    assert sat.fatal_error, "an hour on, the budget has freed up"


def test_a_held_back_exit_does_not_block_the_link_recovery_behind_it(tmp_path):
    sat = health_sat(tmp_path)
    for i in range(3):
        sat._health_budget.record("exit", f"earlier {i}")
    sat._voice_input_started = False
    sat._ws_disconnected_since = 0.0
    recovered: list[int] = []
    sat._reassociate_wifi = lambda: recovered.append(1) or True
    sat._health_last_sample = sample(1, fds=900, gateway_ok=False)
    sat._health_checks(6 * 60.0)
    sat._health_last_sample = sample(2, fds=900, gateway_ok=False)
    sat._health_checks(7 * 60.0)
    assert sat.fatal_error is None
    assert recovered == [1]


def test_no_server_with_the_gateway_answering_touches_nothing(tmp_path):
    sat = health_sat(tmp_path)
    sat._voice_input_started = False      # a network-only scenario
    sat._ws_disconnected_since = 0.0
    recovered: list[int] = []
    sat._reassociate_wifi = lambda: recovered.append(1) or True
    for i, t in enumerate((20 * 60.0, 21 * 60.0, 22 * 60.0)):
        # NetworkManager may even say disconnected (an Ethernet path): the
        # gateway answering is the last word.
        sat._health_last_sample = sample(i + 1, gateway_ok=True, default_route=True,
                                         nm_state={"code": 30, "state": "disconnected"})
        sat._health_checks(t)
    assert recovered == [] and sat.fatal_error is None


def test_no_server_with_the_cores_host_refusing_touches_nothing(tmp_path, monkeypatch):
    """The gateway may drop probes, but a refusal from the core's own host
    proves the path: the core is down, the Pi is fine."""
    monkeypatch.setattr(client, "REBOOT_HELPER", tmp_path / "domovoi-reboot")
    (tmp_path / "domovoi-reboot").write_text("#!/bin/sh\n")
    ran: list[list[str]] = []
    monkeypatch.setattr(client.subprocess, "run", lambda argv, **kw: ran.append(argv))
    sat = health_sat(tmp_path)
    sat._voice_input_started = False
    sat._ws_disconnected_since = 0.0
    recovered: list[int] = []
    sat._reassociate_wifi = lambda: recovered.append(1) or True
    for i, t in enumerate((20 * 60.0, 21 * 60.0, 22 * 60.0)):
        sat._health_last_sample = sample(i + 1, gateway_ok=False, core_host_ok=True, core_ok=False)
        sat._health_checks(t)
    assert recovered == [] and ran == []


def test_a_process_that_never_connected_still_counts_its_outage(tmp_path):
    """`_ws_disconnected_since` is stamped only when a session ends. A unit
    that boots into a dead network never had one, and used to look
    'connected' to check 5 for ever."""
    sat = health_sat(tmp_path)
    sat._voice_input_started = False
    sat._ws_disconnected_since = None
    sat._health_process_started = 0.0
    recovered: list[int] = []
    sat._reassociate_wifi = lambda: recovered.append(1) or True
    facts = sat._health_process_facts(240.0)
    assert facts["ws"] == "down" and facts["ws_down_for_s"] == 240.0
    sat._health_last_sample = sample(1, gateway_ok=False)
    sat._health_checks(240.0)
    sat._health_last_sample = sample(2, gateway_ok=False)
    sat._health_checks(270.0)
    assert recovered == [], "four and a half minutes: not yet"
    sat._health_last_sample = sample(3, gateway_ok=False)
    sat._health_checks(5 * 60.0)
    sat._health_last_sample = sample(4, gateway_ok=False)
    sat._health_checks(6 * 60.0)
    assert recovered == [1], "five minutes since the process started, twice seen"
    # A session up again: nothing counts.
    sat.ws = object()
    sat._session_ready_seen = True
    assert sat._health_process_facts(400.0)["ws_down_for_s"] is None


def test_a_socket_open_before_ready_is_not_an_outage(tmp_path, monkeypatch):
    """Between `hello` (which clears `_ws_disconnected_since`) and `ready`
    the client syncs sounds and reads the link — seconds, sometimes more.
    That window used to read as 'no server since the process started' (days
    for a long-lived process), with whatever stale gateway reading the last
    sample held: a reboot during a successful reconnect."""
    monkeypatch.setattr(client, "REBOOT_HELPER", tmp_path / "domovoi-reboot")
    (tmp_path / "domovoi-reboot").write_text("#!/bin/sh\n")
    ran: list[list[str]] = []
    monkeypatch.setattr(client.subprocess, "run", lambda argv, **kw: ran.append(argv))
    sat = health_sat(tmp_path)
    sat._voice_input_started = False
    sat._health_process_started = 0.0
    sat._ws_disconnected_since = None        # cleared by the hello
    sat.ws = object()                        # the socket is open
    sat._session_ready_seen = False          # ready not processed yet
    recovered: list[int] = []
    sat._reassociate_wifi = lambda: recovered.append(1) or True
    assert sat._no_server_since() is None
    for i, t in enumerate((3 * 86400.0, 3 * 86400.0 + 60.0)):
        sat._health_last_sample = sample(i + 1, gateway_ok=False)   # stale
        sat._health_checks(t)
    assert recovered == [] and ran == []


def test_no_server_with_the_gateway_silent_recovers_the_link_once_per_cooldown(tmp_path):
    sat = health_sat(tmp_path)
    sat._voice_input_started = False      # a network-only scenario
    sat._ws_disconnected_since = 0.0
    recovered: list[float] = []
    sat._reassociate_wifi = lambda: recovered.append(1) or True
    seq = [0]

    def tick(t):
        seq[0] += 1
        sat._health_last_sample = sample(seq[0], gateway_ok=False,
                                         nm_state={"code": 30, "state": "disconnected"})
        sat._health_checks(t)

    tick(5 * 60.0)
    tick(6 * 60.0)
    assert len(recovered) == 1
    tick(7 * 60.0)
    tick(10 * 60.0)
    assert len(recovered) == 1, "cooldown: 5 minutes from the last recovery"
    tick(11 * 60.0)
    tick(12 * 60.0)
    assert len(recovered) == 2
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert [a["action"] for a in doc["actions"]] == ["wifi_recover", "wifi_recover"]


def test_the_self_check_honours_a_recovery_the_watcher_just_ran(tmp_path):
    """The Wi-Fi watcher and the self-check run the same `_reassociate_wifi`
    on different clocks. Whichever ran last, the other waits its cooldown."""
    sat = health_sat(tmp_path)
    sat._voice_input_started = False
    sat._ws_disconnected_since = 0.0
    recovered: list[int] = []
    sat._reassociate_wifi = lambda: recovered.append(1) or True
    sat._last_reassociate_at = 9 * 60.0      # the watcher, a minute ago
    for i, t in enumerate((10 * 60.0, 11 * 60.0, 12 * 60.0, 13 * 60.0)):
        sat._health_last_sample = sample(i + 1, gateway_ok=False)
        sat._health_checks(t)
    assert recovered == []
    for i, t in enumerate((14 * 60.0, 15 * 60.0)):
        sat._health_last_sample = sample(i + 10, gateway_ok=False)
        sat._health_checks(t)
    assert recovered == [1]


def test_reassociate_stamps_the_shared_recovery_clock(tmp_path):
    sat = health_sat(tmp_path)
    sat._nm_manages_wifi = lambda: True
    sat._reconnect_wifi_nm = lambda: True
    assert sat._last_reassociate_at is None
    sat._reassociate_wifi()
    assert sat._last_reassociate_at is not None


def test_a_reboot_needs_the_helper_else_it_is_only_said(tmp_path, monkeypatch, caplog):
    sat = health_sat(tmp_path)
    sat._voice_input_started = False      # a network-only scenario
    sat._ws_disconnected_since = 0.0
    sat._reassociate_wifi = lambda: False
    ran: list[list[str]] = []
    monkeypatch.setattr(client, "REBOOT_HELPER", tmp_path / "domovoi-reboot")
    caplog.set_level(logging.ERROR)
    sat._health_last_sample = sample(1, gateway_ok=False)
    sat._health_checks(16 * 60.0)
    sat._health_last_sample = sample(2, gateway_ok=False)
    sat._health_checks(17 * 60.0)
    assert ran == []
    assert any("not installed" in r.getMessage() for r in caplog.records)
    # Now the helper exists: the check calls it through sudo -n.
    (tmp_path / "domovoi-reboot").write_text("#!/bin/sh\n")

    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(client.subprocess, "run", lambda argv, **kw: ran.append(argv) or Result())
    for i, t in enumerate((33 * 60.0, 34 * 60.0)):
        sat._health_last_sample = sample(i + 3, gateway_ok=False)
        sat._health_checks(t)
    assert ran and ran[0][:2] == ["sudo", "-n"] and ran[0][2].endswith("domovoi-reboot")
    assert len(ran[0]) == 3, "argv-less"
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert doc["last_exit_reason"].startswith("reboot:")
    assert doc["restart_budget"]["recent"][-1]["action"] == "reboot"


def test_a_wedged_radio_with_no_route_is_rebooted(tmp_path, monkeypatch):
    """A radio that lost its association has no default route, so there is
    no gateway to probe (gateway_ok null) — it used to never be rebooted."""
    monkeypatch.setattr(client, "REBOOT_HELPER", tmp_path / "domovoi-reboot")
    (tmp_path / "domovoi-reboot").write_text("#!/bin/sh\n")

    class Result:
        returncode = 0
        stderr = ""

    ran: list[list[str]] = []
    monkeypatch.setattr(client.subprocess, "run", lambda argv, **kw: ran.append(argv) or Result())
    sat = health_sat(tmp_path)
    sat._voice_input_started = False
    sat._ws_disconnected_since = 0.0
    sat._reassociate_wifi = lambda: False
    for i, t in enumerate((16 * 60.0, 17 * 60.0)):
        sat._health_last_sample = sample(i + 1, gateway=None, gateway_ok=None, default_route=False,
                                         nm_state={"code": 30, "state": "disconnected"})
        sat._health_checks(t)
    assert ran and ran[0][2].endswith("domovoi-reboot")


def test_a_failed_reboot_helper_is_paced_and_does_not_claim_a_reboot(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "REBOOT_HELPER", tmp_path / "domovoi-reboot")
    (tmp_path / "domovoi-reboot").write_text("#!/bin/sh\n")

    class Refused:
        returncode = 1
        stderr = "sudo: a password is required"

    ran: list[list[str]] = []
    monkeypatch.setattr(client.subprocess, "run", lambda argv, **kw: ran.append(argv) or Refused())
    sat = health_sat(tmp_path)
    sat._voice_input_started = False
    sat._ws_disconnected_since = 0.0
    sat._reassociate_wifi = lambda: False
    t, seq = 16 * 60.0, 0
    while t < 30 * 60.0:
        seq += 1
        sat._health_last_sample = sample(seq, gateway_ok=False)
        sat._health_checks(t)
        t += 60.0
    assert len(ran) == 1, "not asked again every tick"
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert doc["last_exit_reason"] is None
    assert any(a["action"] == "reboot_failed" for a in doc["actions"])


def test_reboots_are_held_to_one_an_hour(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "REBOOT_HELPER", tmp_path / "domovoi-reboot")
    (tmp_path / "domovoi-reboot").write_text("#!/bin/sh\n")

    class Result:
        returncode = 0
        stderr = ""

    ran: list[list[str]] = []
    monkeypatch.setattr(client.subprocess, "run", lambda argv, **kw: ran.append(argv) or Result())
    sat = health_sat(tmp_path)
    sat._health_budget.record("reboot", "twenty minutes ago")
    sat._voice_input_started = False
    sat._ws_disconnected_since = 0.0
    sat._reassociate_wifi = lambda: False
    for i, t in enumerate((16 * 60.0, 17 * 60.0, 18 * 60.0)):
        sat._health_last_sample = sample(i + 1, gateway_ok=False)
        sat._health_checks(t)
    assert ran == []


# ─── the previous life's record and the spool, around a session ───────────


def test_connect_failures_are_recorded_in_offline_diag(tmp_path):
    sat = health_sat(tmp_path)
    sat._health_last_sample = {"gateway": "192.168.0.1", "gateway_ok": True, "core_ok": False}
    sat._note_connect_failure(ConnectionRefusedError(111, "Connection refused"))
    sat._last_server_error = "pairing_rejected"
    sat._note_session_ended_before_ready()
    doc = json.loads((tmp_path / "last-health.json").read_text())
    kinds = [e["failure"] for e in doc["offline_diag"]]
    assert kinds == ["tcp_refused", "closed_before_ready"]
    assert doc["offline_diag"][0]["gateway_ok"] is True
    assert doc["offline_diag"][1]["detail"] == "pairing_rejected"


def test_the_hello_carries_prev_health_until_a_ready_clears_it(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "_effective_wake_word", lambda cfg: "domovoi")
    for name in ("_read_synced_sha", "_effective_pairing_token", "_effective_approval_code"):
        monkeypatch.setattr(client, name, lambda: None)
    monkeypatch.setattr(client, "_verify_server_identity", lambda cfg, **kw: True)
    monkeypatch.setattr(client, "LOG_SPOOL", log_spool.LogSpool(tmp_path / "spool.txt"))
    monkeypatch.setattr(client, "LOG_SPOOL_HANDLER", log_spool.SpoolHandler(client.LOG_SPOOL))

    async def scenario():
        sat = health_sat(tmp_path, loop=asyncio.get_running_loop())
        sat._async_shutdown = asyncio.Event()
        sat._read_output_volume = lambda: None
        sat._emit_voice_status = lambda: None
        sat._emit_config_status = lambda: None

        async def no_sounds():
            return None

        sat._sync_sounds = no_sounds
        sat._prev_health = {"prev_boot_id": "b-old", "last_exit_reason": "RSS 310 MB"}
        sat._last_health.prev_path.write_text("{}")
        servers: list[FakeServer] = []

        def connect(url, **kw):
            server = FakeServer(sat)
            servers.append(server)
            return FakeConnect(server)

        monkeypatch.setattr(client.websockets, "connect", connect)

        await sat._run_session()
        hello = json.loads(servers[0].sent[0])
        assert hello["type"] == "hello"
        assert hello["prev_health"] == {"prev_boot_id": "b-old", "last_exit_reason": "RSS 310 MB"}
        assert sat._prev_health is not None, "no ready came: carried again next time"
        assert sat._reconnects == 1

        # A ready from an OLDER core, which lists neither feature: it never
        # recorded prev_health, so the record is kept for a core that will.
        sat.send_q = asyncio.Queue()
        sat._core_features = frozenset()
        sat._session_ready_seen = True
        sat._on_core_accepted_health()
        assert sat._prev_health is not None
        assert sat._last_health.prev_path.exists()
        assert client.LOG_SPOOL_HANDLER.mode == "off"
        # A ready from a core that lists both features: the receiver's
        # `ready` branch calls `_on_core_accepted_health` (asserted below
        # by source, since the whole branch also opens the microphone).
        sat._core_features = frozenset({"health", "log_push"})
        sat._on_core_accepted_health()
        assert sat._prev_health is None
        assert not sat._last_health.prev_path.exists()
        assert client.LOG_SPOOL_HANDLER.mode == "online"

        await sat._run_session()
        assert "prev_health" not in json.loads(servers[1].sent[0])

    asyncio.run(scenario())
    import inspect

    src = inspect.getsource(client.Satellite._handle_text_frame)
    ready = src[src.index('if t == "ready"'):src.index('elif t == "transcript"')]
    assert "self._session_ready_seen = True" in ready
    assert "self._on_core_accepted_health()" in ready


def test_ready_drains_the_spool_as_offline_log_push_frames(tmp_path, monkeypatch):
    spool = log_spool.LogSpool(tmp_path / "spool.txt")
    handler = log_spool.SpoolHandler(spool)
    monkeypatch.setattr(client, "LOG_SPOOL", spool)
    monkeypatch.setattr(client, "LOG_SPOOL_HANDLER", handler)
    handler.set_mode("offline")
    for i in range(3):
        spool.append(f"line {i} while disconnected")

    async def scenario():
        sat = health_sat(tmp_path, loop=asyncio.get_running_loop())
        sat.send_q = asyncio.Queue()
        sat._core_features = frozenset({"log_push"})
        sat._on_core_accepted_health()
        await settle()
        frames = [json.loads(sat.send_q.get_nowait()[1]) for _ in range(sat.send_q.qsize())]
        assert len(frames) == 1
        assert frames[0]["type"] == "log_push" and frames[0]["offline"] is True
        assert frames[0]["lines"].splitlines() == [f"line {i} while disconnected" for i in range(3)]
        assert spool.size() == 0, "cleared once sent"
        assert handler.mode == "online"

        # A core without log_push: nothing is sent, nothing accumulates.
        spool.append("another outage line")
        sat.send_q = asyncio.Queue()
        sat._core_features = frozenset()
        sat._on_core_accepted_health()
        await settle()
        assert sat.send_q.qsize() == 0
        assert handler.mode == "off"
        assert spool.size() > 0, "kept for a core that will take it"

    asyncio.run(scenario())


def test_live_lines_are_pushed_every_interval_bounded(tmp_path, monkeypatch):
    spool = log_spool.LogSpool(tmp_path / "spool.txt")
    handler = log_spool.SpoolHandler(spool, log_spool.PushBuffer(max_bytes=64))
    monkeypatch.setattr(client, "LOG_SPOOL_HANDLER", handler)

    async def scenario():
        sat = health_sat(tmp_path, loop=asyncio.get_running_loop())
        sat.cfg.health_log_push_interval_sec = 30.0
        sat.send_q = asyncio.Queue()
        sat.ws = object()
        sat._core_features = frozenset({"log_push"})
        sat._health_last_push = 0.0
        handler.set_mode("online")
        handler.push.append("a" * 30)
        handler.push.append("b" * 30)
        handler.push.append("c" * 30)          # over the push budget: dropped
        sat._push_live_logs(10.0)
        await settle()
        assert sat.send_q.qsize() == 0, "not due yet"
        sat._push_live_logs(31.0)
        await settle()
        frame = json.loads(sat.send_q.get_nowait()[1])
        assert frame["type"] == "log_push" and frame["offline"] is False
        assert frame["dropped"] == 1
        assert frame["lines"] == "a" * 30 + "\n" + "b" * 30 + "\n"

    asyncio.run(scenario())


def test_session_end_switches_the_spool_to_offline(tmp_path, monkeypatch):
    handler = log_spool.SpoolHandler(log_spool.LogSpool(tmp_path / "spool.txt"))
    monkeypatch.setattr(client, "LOG_SPOOL_HANDLER", handler)
    handler.set_mode("online")
    sat = make_sat()
    sat._on_session_ended()
    assert handler.mode == "offline"


# ─── config ────────────────────────────────────────────────────────────────


def test_health_config_defaults_and_overrides():
    cfg = client.Config.from_toml({"satellite": {"room_id": "den", "domovoi_url": "ws://x:6370"}})
    assert cfg.health_enabled is True
    assert cfg.health_sample_interval_sec == 60.0
    assert cfg.health_rss_limit_mb == 300.0
    assert cfg.health_fd_limit == 512
    assert cfg.health_no_server_escalate_min == 5.0
    assert cfg.health_reboot_after_min == 15.0
    cfg = client.Config.from_toml({
        "satellite": {"room_id": "den", "domovoi_url": "ws://x:6370"},
        "health": {"enabled": False, "rss_limit_mb": 200, "sample_interval_sec": 5,
                   "no_server_escalate_min": 2, "fd_limit": 256},
    })
    assert cfg.health_enabled is False
    assert cfg.health_rss_limit_mb == 200.0
    assert cfg.health_sample_interval_sec == 10.0, "floored: a tighter loop is a CPU cost"
    assert cfg.health_no_server_escalate_min == 2.0
    assert cfg.health_fd_limit == 256


def test_the_example_config_documents_the_health_section():
    import tomllib
    from pathlib import Path

    example = Path(client.__file__).resolve().parent / "config.toml.example"
    doc = tomllib.loads(example.read_text(encoding="utf-8"))
    assert doc["health"]["rss_limit_mb"] == 300
    assert doc["health"]["no_server_escalate_min"] == 5
    assert doc["health"]["enabled"] is True


def test_a_self_check_exit_has_a_hard_exit_backstop(tmp_path, monkeypatch):
    """A capture stream on a USB array that stopped answering can block in
    PortAudio's close; a process that never exits is never restarted."""
    started: list[tuple] = []

    class FakeTimer:
        def __init__(self, interval, fn, args=()):
            self.interval, self.fn, self.args, self.daemon = interval, fn, args, False

        def start(self):
            started.append((self.interval, self.fn, self.args, self.daemon))

    monkeypatch.setattr(client.threading, "Timer", FakeTimer)
    sat = health_sat(tmp_path)
    assert sat._health_exit("the mic thread is dead") is True
    [(interval, fn, args, daemon)] = started
    assert interval == client.HEALTH_HARD_EXIT_SEC and fn is client._hard_exit  # (the no-op stand-in)
    assert args == (1,) and daemon is True
    # A held-back exit arms nothing.
    started.clear()
    sat2 = health_sat(tmp_path / "b")
    for i in range(3):
        sat2._health_budget.record("exit", f"earlier {i}")
    assert sat2._health_exit("again") is False and started == []
