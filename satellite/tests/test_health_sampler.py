"""The health sampler, the last-health sidecar and the self-check table.

All of it off a Pi: the sampler reads fake ``/proc`` files from a temp
directory and fake tools from a dict, the sidecar writes into ``tmp_path``,
and ``decide`` is a pure function driven by a table. Nothing here touches
sounddevice or a database.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from satellite import health
from satellite.health import CheckInput, Decision, decide


# ─── fake /proc ────────────────────────────────────────────────────────────


def make_proc(tmp_path: Path, *, rss_kb=48112, threads=9, fds=23) -> tuple[Path, Path]:
    proc = tmp_path / "proc"
    (proc / "self" / "fd").mkdir(parents=True)
    for i in range(fds):
        (proc / "self" / "fd" / str(i)).write_text("")
    (proc / "self" / "status").write_text(
        "Name:\tpython3\nVmPeak:\t  120000 kB\n"
        f"VmHWM:\t   61440 kB\nVmRSS:\t   {rss_kb} kB\nThreads:\t{threads}\n"
    )
    # comm with a space and a parenthesis, which is why fields are taken
    # after the LAST ')'.
    (proc / "self" / "stat").write_text(
        "1234 (python3 (sat)) S 1 1234 1234 0 -1 4194560 100 0 0 0 "
        "150 50 0 0 20 0 9 0 5000 49000000 12028 18446744073709551615\n"
    )
    (proc / "stat").write_text("cpu  1000 0 500 8000 200 0 10 0 0 0\ncpu0 1 2 3 4 5 6 7\n")
    (proc / "meminfo").write_text(
        "MemTotal:         426000 kB\nMemFree:           40000 kB\n"
        "MemAvailable:     210000 kB\nSwapTotal:        102396 kB\nSwapFree:          90000 kB\n"
    )
    (proc / "loadavg").write_text("0.42 0.31 0.25 1/123 4567\n")
    (proc / "uptime").write_text("345678.12 1234.56\n")
    (proc / "sys" / "kernel" / "random").mkdir(parents=True)
    (proc / "sys" / "kernel" / "random" / "boot_id").write_text(
        "9d2f1b6e-1111-2222-3333-444455556666\n"
    )
    sysfs = tmp_path / "sys"
    (sysfs / "class" / "thermal" / "thermal_zone0").mkdir(parents=True)
    (sysfs / "class" / "thermal" / "thermal_zone0" / "temp").write_text("52345\n")
    return proc, sysfs


def fake_tools(answers: dict[str, str | None]):
    """A ``run`` whose answers are keyed by the tool name."""
    calls: list[list[str]] = []

    def run(argv, timeout=5.0):
        calls.append(list(argv))
        return answers.get(argv[0])

    run.calls = calls  # type: ignore[attr-defined]
    return run


TOOLS = {
    "vcgencmd": "throttled=0x50005\n",
    "nmcli": "GENERAL.STATE:100 (connected)\n",
    "ip": "default via 192.168.0.1 dev wlan0 proto dhcp src 192.168.0.42 metric 600\n"
          "192.168.0.0/24 dev wlan0 proto kernel scope link src 192.168.0.42\n",
}


# ─── parsing ───────────────────────────────────────────────────────────────


def test_proc_status_meminfo_loadavg_uptime_boot_id_and_temp(tmp_path):
    proc, sysfs = make_proc(tmp_path)
    assert health.read_proc_status(proc / "self" / "status") == {
        "rss_kb": 48112, "hwm_kb": 61440, "threads": 9,
    }
    assert health.read_meminfo(proc / "meminfo") == {
        "mem_free_kb": 40000, "mem_avail_kb": 210000, "swap_free_kb": 90000,
    }
    assert health.read_loadavg(proc / "loadavg") == (0.42, 0.31, 0.25)
    assert health.read_uptime(proc / "uptime") == 345678.12
    assert health.read_boot_id(proc / "sys/kernel/random/boot_id") == "9d2f1b6e-1111-2222-3333-444455556666"
    assert health.read_soc_temp(sysfs / "class/thermal/thermal_zone0/temp") == 52.3
    assert health.count_fds(proc / "self" / "fd") == 23


def test_process_cpu_ticks_are_taken_after_the_last_paren(tmp_path):
    proc, _ = make_proc(tmp_path)
    assert health.read_process_cpu_ticks(proc / "self" / "stat") == 200   # utime 150 + stime 50
    assert health.read_system_cpu_ticks(proc / "stat") == (9710 - 8200, 9710)


def test_a_missing_file_yields_none_not_an_exception(tmp_path):
    missing = tmp_path / "nope"
    assert health.read_proc_status(missing) == {"rss_kb": None, "hwm_kb": None, "threads": None}
    assert health.read_meminfo(missing)["mem_avail_kb"] is None
    assert health.read_loadavg(missing) is None
    assert health.read_uptime(missing) is None
    assert health.read_boot_id(missing) is None
    assert health.read_soc_temp(missing) is None
    assert health.count_fds(missing) is None
    assert health.read_system_cpu_ticks(missing) is None
    assert health.read_process_cpu_ticks(missing) is None


def test_cpu_meter_needs_two_readings():
    meter = health.CpuMeter(ticks_per_sec=100)
    assert meter.update((1000, 2000), 100, 10.0) == (None, None)
    # 60 s later: 300 process ticks = 3 s of CPU = 5 %; system 50 % busy.
    assert meter.update((1600, 3200), 400, 70.0) == (5.0, 50.0)


@pytest.mark.parametrize(
    "text, flags, names",
    [
        ("throttled=0x0\n", 0, []),
        ("throttled=0x50005\n", 0x50005, [
            "under_voltage", "throttled", "under_voltage_occurred", "throttled_occurred",
        ]),
        ("throttled=0x80000\n", 0x80000, ["soft_temp_limit_occurred"]),
        ("", None, []),
        (None, None, []),
        ("garbage", None, []),
    ],
)
def test_throttled_parse_and_decode(text, flags, names):
    assert health.parse_throttled(text) == flags
    assert health.decode_throttled(flags) == names


def test_nm_state_and_default_gateway_parse():
    assert health.parse_nm_state("GENERAL.STATE:100 (connected)\n") == {"code": 100, "state": "connected"}
    assert health.parse_nm_state("GENERAL.STATE:30 (disconnected)\n") == {"code": 30, "state": "disconnected"}
    assert health.parse_nm_state("") is None
    assert health.parse_nm_state("Error: Device 'wlan0' not found.") is None
    assert health.parse_default_gateway(TOOLS["ip"]) == "192.168.0.1"
    assert health.parse_default_gateway("192.168.0.0/24 dev wlan0\n") is None
    assert health.parse_default_gateway(None) is None


def test_run_tool_never_raises(monkeypatch):
    assert health.run_tool(["definitely-not-a-tool-xyz"]) is None


# ─── probes ────────────────────────────────────────────────────────────────


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _connect_with(outcomes: dict[tuple[str, int], BaseException | None]):
    seen = []

    def connect(addr, timeout=None):
        seen.append(addr)
        outcome = outcomes.get(addr, OSError("no route"))
        if outcome is None:
            return _Conn()
        raise outcome

    connect.seen = seen  # type: ignore[attr-defined]
    return connect


def test_gateway_counts_a_refusal_as_answering():
    """A home router answers a SYN on 53 or 80 with either an accept or an
    RST; both prove the link. Only silence does not."""
    refused = _connect_with({("192.168.0.1", 53): ConnectionRefusedError()})
    assert health.host_answers("192.168.0.1", connect=refused) is True
    assert refused.seen == [("192.168.0.1", 53)]
    silent = _connect_with({})
    assert health.host_answers("192.168.0.1", connect=silent) is False
    assert silent.seen == [("192.168.0.1", 53), ("192.168.0.1", 80), ("192.168.0.1", 443)]


def test_a_gateway_that_drops_tcp_but_answers_ping_answers(tmp_path):
    """A router that silently drops SYNs to its closed ports is still there;
    "the gateway is silent" licenses a reboot, so it needs every probe."""
    proc, sysfs = make_proc(tmp_path)
    pings: list[str] = []
    sampler = health.Sampler(proc_root=str(proc), sys_root=str(sysfs), run=fake_tools(TOOLS),
                             connect=_connect_with({}), ping=lambda h: pings.append(h) or True)
    assert sampler.sample({})["gateway_ok"] is True and pings == ["192.168.0.1"]
    sampler.ping = lambda h: False
    assert sampler.sample({})["gateway_ok"] is False
    sampler.ping = lambda h: None            # this host cannot ping: TCP decides
    assert sampler.sample({})["gateway_ok"] is False


def test_icmp_echo_never_raises():
    # Whatever this box allows (no ping socket on Windows, perhaps none in a
    # container), the answer is a bool or None.
    assert health.icmp_echo("192.0.2.1", timeout=0.2) in (True, False, None)


def test_the_core_probe_tells_a_refusing_host_from_a_silent_one(tmp_path):
    proc, sysfs = make_proc(tmp_path)
    connect = _connect_with({("192.168.0.1", 53): None,
                             ("192.168.0.117", 6370): ConnectionRefusedError()})
    sampler = health.Sampler(proc_root=str(proc), sys_root=str(sysfs), run=fake_tools(TOOLS),
                             connect=connect, ping=None)
    doc = sampler.sample({}, core_host="192.168.0.117", core_port=6370, probe_core=True)
    assert doc["core_ok"] is False and doc["core_host_ok"] is True
    sampler.connect = _connect_with({("192.168.0.1", 53): None})
    doc = sampler.sample({}, core_host="192.168.0.117", core_port=6370, probe_core=True)
    assert doc["core_ok"] is False and doc["core_host_ok"] is False


def test_no_default_route_is_a_fact_and_a_failed_ip_is_not(tmp_path):
    proc, sysfs = make_proc(tmp_path)
    no_route = dict(TOOLS, ip="192.168.0.0/24 dev wlan0 proto kernel scope link\n")
    sampler = health.Sampler(proc_root=str(proc), sys_root=str(sysfs), run=fake_tools(no_route),
                             connect=_connect_with({}), ping=None)
    doc = sampler.sample({})
    assert doc["default_route"] is False and doc["gateway"] is None and doc["gateway_ok"] is None
    sampler.run = fake_tools(dict(TOOLS, ip=None))
    assert sampler.sample({})["default_route"] is None
    sampler.run = fake_tools(TOOLS)
    assert sampler.sample({})["default_route"] is True


def test_the_core_must_actually_accept():
    refused = _connect_with({("192.168.0.117", 6370): ConnectionRefusedError()})
    assert health.service_reachable("192.168.0.117", 6370, connect=refused) is False
    up = _connect_with({("192.168.0.117", 6370): None})
    assert health.service_reachable("192.168.0.117", 6370, connect=up) is True


@pytest.mark.parametrize(
    "exc, expected",
    [
        (socket.gaierror(-2, "Name or service not known"), "dns"),
        (ConnectionRefusedError(111, "Connection refused"), "tcp_refused"),
        (TimeoutError("timed out"), "tcp_timeout"),
        (OSError("[Errno 113] No route to host"), "os_error"),
        (RuntimeError("SSL: CERTIFICATE_VERIFY_FAILED"), "tls"),
        (RuntimeError("server rejected WebSocket connection: HTTP 403 (handshake)"), "handshake"),
        (ConnectionError("ws://x:6370/v1/stream/den did not prove it is this device's Domovoi server"),
         "identity_unverified"),
    ],
)
def test_connect_failures_are_classified(exc, expected):
    assert health.classify_connect_failure(exc) == expected


def test_a_close_code_is_reported_as_such():
    class Rcvd:
        code = 1008

    class ConnectionClosedError(Exception):
        rcvd = Rcvd()

    assert health.classify_connect_failure(ConnectionClosedError("closed")) == "close_1008"


# ─── the whole sample ──────────────────────────────────────────────────────


def test_a_sample_merges_host_probes_and_process_facts(tmp_path):
    proc, sysfs = make_proc(tmp_path)
    run = fake_tools(TOOLS)
    connect = _connect_with({("192.168.0.1", 53): ConnectionRefusedError(),
                             ("192.168.0.117", 6370): None})
    sampler = health.Sampler(proc_root=str(proc), sys_root=str(sysfs), run=run, connect=connect)
    doc = sampler.sample(
        {"mic_fps": 33.2, "wake_loop_age_s": 0.4, "ws": "down", "reconnects": 3},
        core_host="192.168.0.117", core_port=6370, probe_core=True,
    )
    assert doc["seq"] == 1
    assert doc["rss_kb"] == 48112 and doc["hwm_kb"] == 61440 and doc["threads"] == 9
    assert doc["fds"] == 23
    assert doc["cpu_pct"] is None            # first reading
    assert doc["soc_temp_c"] == 52.3
    assert doc["throttled"] == 0x50005
    assert "under_voltage" in doc["throttled_flags"]
    assert doc["boot_id"].startswith("9d2f1b6e")
    assert doc["nm_state"] == {"code": 100, "state": "connected"}
    assert doc["gateway"] == "192.168.0.1"
    assert doc["gateway_ok"] is True
    assert doc["core_ok"] is True
    assert doc["mic_fps"] == 33.2 and doc["reconnects"] == 3
    assert len(json.dumps(doc)) <= health.SAMPLE_MAX_BYTES


def test_the_core_is_not_probed_while_the_socket_is_up(tmp_path):
    proc, sysfs = make_proc(tmp_path)
    connect = _connect_with({("192.168.0.1", 53): None})
    sampler = health.Sampler(proc_root=str(proc), sys_root=str(sysfs),
                             run=fake_tools(TOOLS), connect=connect)
    doc = sampler.sample({}, core_host="192.168.0.117", core_port=6370, probe_core=False)
    assert doc["core_ok"] is None
    assert ("192.168.0.117", 6370) not in connect.seen


def test_missing_tools_are_left_alone_after_three_misses_then_asked_again(tmp_path, monkeypatch):
    proc, sysfs = make_proc(tmp_path)
    run = fake_tools({"ip": TOOLS["ip"]})      # no vcgencmd, no nmcli
    sampler = health.Sampler(proc_root=str(proc), sys_root=str(sysfs), run=run,
                             connect=_connect_with({("192.168.0.1", 53): None}), ping=None)
    clock = [1000.0]
    monkeypatch.setattr(health.time, "monotonic", lambda: clock[0])
    first = sampler.sample({})
    assert first["throttled"] is None and first["throttled_flags"] == []
    assert first["nm_state"] is None
    for _ in range(5):
        clock[0] += 60.0
        sampler.sample({})
    tools_asked = [c[0] for c in run.calls]
    assert tools_asked.count("vcgencmd") == 3, "three misses, then left alone"
    assert tools_asked.count("nmcli") == 3
    assert tools_asked.count("ip") == 6
    clock[0] += health.Sampler.TOOL_RETRY_SEC
    sampler.sample({})
    assert [c[0] for c in run.calls].count("nmcli") == 4, "asked again after the rest"


def test_one_failed_nmcli_does_not_silence_it_for_the_process(tmp_path):
    """NetworkManager busy with a wedged radio times nmcli out once; the
    next sample must still ask."""
    proc, sysfs = make_proc(tmp_path)
    answers = dict(TOOLS)
    sampler = health.Sampler(proc_root=str(proc), sys_root=str(sysfs),
                             run=lambda argv, timeout=5.0: answers.get(argv[0]),
                             connect=_connect_with({("192.168.0.1", 53): None}), ping=None)
    answers["nmcli"] = None
    assert sampler.sample({})["nm_state"] is None
    answers["nmcli"] = "GENERAL.STATE:30 (disconnected)\n"
    assert sampler.sample({})["nm_state"] == {"code": 30, "state": "disconnected"}


def test_a_sample_is_bounded_to_4kb():
    doc = {"seq": 1, "ts": 1.0, "rss_kb": 1, "wifi": {"ssid": "x" * 5000}, "mic_fps": 33}
    bounded = health.bound_sample(doc)
    assert len(json.dumps(bounded, separators=(",", ":"))) <= health.SAMPLE_MAX_BYTES
    assert "wifi" not in bounded and bounded["mic_fps"] == 33


# ─── the sidecar ───────────────────────────────────────────────────────────


def test_last_health_is_written_atomically(tmp_path):
    lh = health.LastHealth(tmp_path / "last-health.json")
    assert lh.write({"boot_id": "b1", "rss_kb": 5}) is True
    assert lh.writes == 1
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert doc["boot_id"] == "b1" and doc["sample"]["rss_kb"] == 5
    assert doc["offline_diag"] == [] and doc["last_exit_reason"] is None
    assert not list(tmp_path.glob("*.tmp*")), "no temp file left behind"


def test_offline_diag_is_capped_at_50_newest_kept(tmp_path):
    lh = health.LastHealth(tmp_path / "last-health.json")
    for i in range(70):
        lh.note_offline({"n": i})
    assert len(lh.offline_diag) == health.OFFLINE_DIAG_MAX
    assert lh.offline_diag[0]["n"] == 20 and lh.offline_diag[-1]["n"] == 69


def test_take_previous_moves_the_file_aside_and_adds_prev_boot_id(tmp_path):
    path = tmp_path / "last-health.json"
    path.write_text(json.dumps({"boot_id": "old-boot", "last_exit_reason": "RSS 310 MB"}))
    lh = health.LastHealth(path)
    prev = lh.take_previous()
    assert prev["prev_boot_id"] == "old-boot" and prev["last_exit_reason"] == "RSS 310 MB"
    assert not path.exists(), "this process starts its own file fresh"
    assert lh.prev_path.exists()
    # Carried until the core acknowledges, then gone.
    lh.clear_previous()
    assert not lh.prev_path.exists()
    assert health.LastHealth(path).take_previous() is None


def test_take_previous_prefers_the_newer_file_and_drops_the_other(tmp_path):
    path = tmp_path / "last-health.json"
    lh = health.LastHealth(path)
    lh.prev_path.write_text(json.dumps({"boot_id": "older"}))
    import os, time as _t
    os.utime(lh.prev_path, (1, 1))
    path.write_text(json.dumps({"boot_id": "newer"}))
    prev = lh.take_previous()
    assert prev["boot_id"] == "newer"
    assert not path.exists() and lh.prev_path.exists()
    assert json.loads(lh.prev_path.read_text())["boot_id"] == "newer"
    _ = _t


def test_an_oversized_previous_file_is_slimmed_not_dropped(tmp_path):
    path = tmp_path / "last-health.json"
    big = {"boot_id": "b", "last_exit_reason": "x", "sample": {"pad": "y" * 20000},
           "offline_diag": [{"n": i} for i in range(50)]}
    path.write_text(json.dumps(big))
    prev = health.LastHealth(path).take_previous()
    assert prev["truncated"] is True
    assert "sample" not in prev
    assert [e["n"] for e in prev["offline_diag"]] == list(range(40, 50))
    assert len(json.dumps(prev)) <= health.PREV_HEALTH_MAX_BYTES


def test_a_corrupt_previous_file_is_ignored(tmp_path):
    path = tmp_path / "last-health.json"
    path.write_text("{not json")
    assert health.LastHealth(path).take_previous() is None


def test_write_into_an_unwritable_dir_returns_false(tmp_path):
    target = tmp_path / "file-not-dir"
    target.write_text("")
    lh = health.LastHealth(target / "last-health.json")
    assert lh.write({}) is False


# ─── the self-check table ──────────────────────────────────────────────────


def _ci(**kw) -> CheckInput:
    base = dict(now=1000.0, mic_expected=True, mic_thread_alive=True, idle=True,
                wake_heartbeat_age=0.5, rss_mb=120.0, fds=40)
    base.update(kw)
    return CheckInput(**base)


def actions(ci: CheckInput) -> list[str]:
    return [d.action for d in decide(ci)]


@pytest.mark.parametrize(
    "label, ci, expected",
    [
        ("healthy idle unit", _ci(), []),
        ("mic silent 29 s: wait", _ci(mic_silent_for=29.0), []),
        ("mic silent 30 s: reopen", _ci(mic_silent_for=30.0), ["reopen_mic"]),
        ("still silent after the reopen: exit", _ci(mic_silent_for=31.0, mic_reopened_at=960.0), ["exit"]),
        ("reopened over 5 min ago (exit held back): reopen again",
         _ci(mic_silent_for=400.0, mic_reopened_at=690.0), ["reopen_mic"]),
        ("mic silent but nothing expected: nothing", _ci(mic_expected=False, mic_silent_for=500.0), []),
        ("mic thread dead: exit", _ci(mic_thread_alive=False), ["exit"]),
        ("mic thread dead but voice never started: nothing", _ci(mic_expected=False, mic_thread_alive=False), []),
        ("wake loop stale 60 s while idle: exit", _ci(wake_heartbeat_age=60.0), ["exit"]),
        ("wake loop stale during a turn: fine", _ci(wake_heartbeat_age=300.0, idle=False), []),
        ("heartbeat as old as the chat that just ended: fine",
         _ci(wake_heartbeat_age=600.0, idle_for=3.0), []),
        ("idle for 60 s and still no heartbeat: exit",
         _ci(wake_heartbeat_age=600.0, idle_for=60.0), ["exit"]),
        ("wake loop not armed yet: fine", _ci(wake_heartbeat_age=None), []),
        ("RSS over 300 MB idle: exit", _ci(rss_mb=301.0), ["exit"]),
        ("RSS over 300 MB mid-turn: wait", _ci(rss_mb=301.0, idle=False), []),
        ("RSS at the limit: fine", _ci(rss_mb=300.0), []),
        ("fds over 512: exit", _ci(fds=513), ["exit"]),
        ("no server 4 min: nothing yet", _ci(no_server_for=240.0, gateway_ok=False), []),
        ("no server 5 min, gateway answers: never touch the link",
         _ci(no_server_for=300.0, gateway_ok=True, nm_connected=True), []),
        ("NM says disconnected but the gateway answers (Ethernet): nothing",
         _ci(no_server_for=900.0, gateway_ok=True, nm_connected=False, reboot_helper=True), []),
        ("gateway silent but the core's host refuses: the core is down, nothing",
         _ci(no_server_for=900.0, gateway_ok=False, core_host_ok=True, reboot_helper=True), []),
        ("no server 5 min, gateway silent: recover the link",
         _ci(no_server_for=300.0, gateway_ok=False), ["wifi_recover"]),
        ("no server 5 min, NM says disconnected: recover the link",
         _ci(no_server_for=300.0, nm_connected=False, gateway_ok=None), ["wifi_recover"]),
        ("recovery 4 min ago: cooldown", _ci(no_server_for=600.0, gateway_ok=False, last_wifi_recovery=800.0), []),
        ("recovery 5 min ago: again", _ci(no_server_for=600.0, gateway_ok=False, last_wifi_recovery=700.0), ["wifi_recover"]),
        ("15 min, gateway silent, helper present: reboot first, then recover",
         _ci(no_server_for=900.0, gateway_ok=False, reboot_helper=True), ["reboot", "wifi_recover"]),
        ("15 min, gateway silent, no helper: say a reboot is needed",
         _ci(no_server_for=900.0, gateway_ok=False), ["wifi_recover", "reboot_needed"]),
        ("15 min, no default route, NM disconnected (wedged radio): reboot",
         _ci(no_server_for=900.0, gateway_ok=None, default_route=False, nm_connected=False,
             reboot_helper=True), ["reboot", "wifi_recover"]),
        ("15 min, no default route, no NetworkManager (legacy unit): reboot",
         _ci(no_server_for=900.0, gateway_ok=None, default_route=False, reboot_helper=True),
         ["reboot", "wifi_recover"]),
        ("15 min, no default route but NM says connected: not silent, nothing",
         _ci(no_server_for=900.0, gateway_ok=None, default_route=False, nm_connected=True,
             reboot_helper=True), []),
        ("15 min, gateway unknown (ip not asked): no reboot",
         _ci(no_server_for=900.0, gateway_ok=None, nm_connected=False), ["wifi_recover"]),
        ("15 min, gateway answers: core problem, do nothing",
         _ci(no_server_for=900.0, gateway_ok=True, nm_connected=True, reboot_helper=True), []),
        ("reboot note made 10 min ago: not repeated",
         _ci(no_server_for=1200.0, gateway_ok=False, last_reboot_note=400.0, last_wifi_recovery=900.0), []),
        ("a failed reboot 10 min ago: not asked again yet",
         _ci(no_server_for=1200.0, gateway_ok=False, last_reboot_note=400.0, last_wifi_recovery=900.0,
             reboot_helper=True), []),
        ("every check that fires is listed, the ending ones first",
         _ci(fds=600, no_server_for=900.0, gateway_ok=False, reboot_helper=True),
         ["exit", "reboot", "wifi_recover"]),
    ],
)
def test_self_check_table(label, ci, expected):
    assert actions(ci) == expected, label


def test_every_decision_carries_a_reason():
    for ci in (_ci(mic_thread_alive=False), _ci(mic_silent_for=30.0),
               _ci(no_server_for=900.0, gateway_ok=False, reboot_helper=True)):
        for d in decide(ci):
            assert isinstance(d, Decision) and d.reason.strip()


def test_decisions_name_their_check_and_basis():
    by_check = {d.check: d for d in decide(_ci(
        mic_thread_alive=False, rss_mb=999.0, fds=999, no_server_for=900.0,
        gateway_ok=False, reboot_helper=True,
    ))}
    assert by_check["mic_thread"].basis == "tick"
    assert by_check["rss"].basis == "sample" and by_check["fds"].basis == "sample"
    assert by_check["no_server_link"].basis == "sample"
    assert by_check["no_server_reboot"].action == "reboot"


def test_the_rss_limit_is_the_configured_one():
    assert actions(_ci(rss_mb=250.0, rss_limit_mb=200.0)) == ["exit"]
    assert actions(_ci(rss_mb=250.0, rss_limit_mb=400.0)) == []


# ─── persistence and the restart budget ───────────────────────────────────


def _d(check, action="exit", basis="tick"):
    return Decision(action, f"{check} fired", check=check, basis=basis)


def test_persistence_needs_two_ticks_in_a_row():
    p = health.Persistence(2)
    assert p.filter([_d("wake_loop")]) == []
    assert p.filter([]) == [], "a tick without it resets the count"
    assert p.filter([_d("wake_loop")]) == []
    assert [d.check for d in p.filter([_d("wake_loop")])] == ["wake_loop"]
    assert p.streak("wake_loop", "exit") == 2


def test_persistence_counts_samples_not_ticks_for_sample_facts():
    p = health.Persistence(2)
    rss = _d("rss", basis="sample")
    assert p.filter([rss], sample_seq=7) == []
    assert p.filter([rss], sample_seq=7) == [], "the same sample read twice is one observation"
    assert p.filter([rss], sample_seq=8) == [rss]


def test_persistence_keys_on_the_action_too():
    """The silent-mic check moves from reopen to exit: the exit needs its
    own two observations after the reopen."""
    p = health.Persistence(2)
    p.filter([_d("mic_silent", "reopen_mic")])
    assert p.filter([_d("mic_silent", "reopen_mic")])
    p.forget("mic_silent")
    assert p.filter([_d("mic_silent", "exit")]) == []
    assert p.filter([_d("mic_silent", "exit")]) == [_d("mic_silent", "exit")]


def test_restart_budget_three_an_hour_one_of_them_a_reboot(tmp_path):
    clock = [10_000.0]
    path = tmp_path / "self-restarts.json"
    b = health.RestartBudget(path, clock=lambda: clock[0])
    assert b.allows("reboot")
    b.record("reboot", "gateway silent")
    assert not b.allows("reboot"), "one reboot an hour"
    assert b.allows("exit")
    b.record("exit", "rss")
    clock[0] += 60
    b.record("exit", "fds")
    assert not b.allows("exit")
    # Across lives: a new process reads the same file.
    b2 = health.RestartBudget(path, clock=lambda: clock[0])
    assert not b2.allows("exit") and len(b2.recent()) == 3
    b2.note_suppressed("exit", "rss again")
    summary = b2.summary()
    assert summary["spent"] is True and summary["suppressed"] == 1
    assert summary["last_suppressed"]["reason"] == "rss again"
    clock[0] += 3600
    assert health.RestartBudget(path, clock=lambda: clock[0]).allows("reboot")


def test_restart_budget_survives_a_corrupt_file_and_a_clock_that_jumped(tmp_path):
    path = tmp_path / "self-restarts.json"
    path.write_text("{not json")
    assert health.RestartBudget(path, clock=lambda: 5_000.0).allows("exit")
    path.write_text(json.dumps({"restarts": [
        {"ts": 5_000.0 + 10 * 3600, "action": "exit", "reason": "from a clock in the future"},
        {"ts": "yesterday", "action": "exit"},
        {"ts": 4_900.0, "action": "exit", "reason": "real"},
    ]}))
    b = health.RestartBudget(path, clock=lambda: 5_000.0)
    assert [e["reason"] for e in b.recent()] == ["real"]


def test_the_budget_is_in_the_last_health_record(tmp_path):
    lh = health.LastHealth(tmp_path / "last-health.json")
    lh.restart_budget = health.RestartBudget(tmp_path / "r.json").summary()
    lh.write(None)
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert doc["restart_budget"]["limit"] == 3 and doc["restart_budget"]["window_s"] == 3600.0
