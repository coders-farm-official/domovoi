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


def test_a_dead_mic_thread_exits_with_the_reason_on_disk(tmp_path, caplog):
    sat = health_sat(tmp_path)
    sat._mic_thread = types.SimpleNamespace(is_alive=lambda: False)
    sat._health_last_sample = {"rss_kb": 50_000, "fds": 30}
    caplog.set_level(logging.ERROR)
    sat._health_checks(500.0)
    assert sat.fatal_error and "mic thread is dead" in sat.fatal_error
    assert sat.shutdown_event.is_set()
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert "mic thread is dead" in doc["last_exit_reason"]
    assert doc["actions"][-1]["action"] == "exit"
    assert any("self-check" in r.getMessage() for r in caplog.records)


def test_rss_over_the_limit_while_idle_exits_but_not_mid_turn(tmp_path):
    sat = health_sat(tmp_path)
    sat._health_last_sample = {"rss_kb": 320 * 1024, "fds": 30}
    sat._turn_open = True
    sat._health_checks(500.0)
    assert sat.fatal_error is None
    sat._turn_open = False
    sat._health_checks(530.0)
    assert sat.fatal_error and "RSS 320 MB" in sat.fatal_error


def test_silent_mic_is_reopened_once_then_exits(tmp_path, caplog):
    sat = health_sat(tmp_path)
    sat._health_last_sample = {"rss_kb": 50_000, "fds": 30}
    opened: list[str] = []
    sat._stop_mic = lambda: opened.append("stop")
    sat._start_mic = lambda: opened.append("start")
    caplog.set_level(logging.WARNING)
    sat._mic_frames_total = 10
    sat._health_checks(100.0)            # frames moving: nothing
    assert opened == []
    # Frames stop. Tick at 130: silent since the previous tick (100) = 30 s.
    sat._health_checks(130.0)
    assert opened == ["stop", "start"], "reopened exactly once"
    assert sat.fatal_error is None
    # Still nothing 30 s after the reopen: exit for systemd.
    sat._health_checks(160.0)
    assert sat.fatal_error and "still no mic frames" in sat.fatal_error
    assert opened == ["stop", "start"], "never reopened a second time"


def test_frames_flowing_again_forgive_the_reopen(tmp_path):
    sat = health_sat(tmp_path)
    sat._health_last_sample = {"rss_kb": 50_000, "fds": 30}
    sat._stop_mic = lambda: None
    sat._start_mic = lambda: None
    sat._mic_frames_total = 10
    sat._health_checks(100.0)
    sat._health_checks(130.0)            # silent 30 s -> reopened
    assert sat._health_mic_reopened_at == 130.0
    sat._mic_frames_total = 500          # the reopen worked
    sat._health_checks(160.0)
    assert sat._health_mic_reopened_at is None and sat._health_mic_silent_since is None
    assert sat.fatal_error is None


def test_no_server_with_the_gateway_answering_touches_nothing(tmp_path):
    sat = health_sat(tmp_path)
    sat._voice_input_started = False      # a network-only scenario
    sat._ws_disconnected_since = 0.0
    sat._health_last_sample = {"rss_kb": 50_000, "fds": 30, "gateway_ok": True,
                               "nm_state": {"code": 100, "state": "connected"}}
    recovered: list[int] = []
    sat._reassociate_wifi = lambda: recovered.append(1) or True
    sat._health_checks(20 * 60.0)
    assert recovered == [] and sat.fatal_error is None


def test_a_process_that_never_connected_still_counts_its_outage(tmp_path):
    """`_ws_disconnected_since` is stamped only when a session ends. A unit
    that boots into a dead network never had one, and used to look
    'connected' to check 5 for ever."""
    sat = health_sat(tmp_path)
    sat._voice_input_started = False
    sat._ws_disconnected_since = None
    sat._health_process_started = 0.0
    sat._health_last_sample = {"rss_kb": 50_000, "fds": 30, "gateway_ok": False}
    recovered: list[int] = []
    sat._reassociate_wifi = lambda: recovered.append(1) or True
    facts = sat._health_process_facts(240.0)
    assert facts["ws"] == "down" and facts["ws_down_for_s"] == 240.0
    sat._health_checks(240.0)
    assert recovered == [], "four minutes: not yet"
    sat._health_checks(5 * 60.0)
    assert recovered == [1], "five minutes since the process started"
    # A session up again: nothing counts.
    sat.ws = object()
    sat._session_ready_seen = True
    assert sat._health_process_facts(400.0)["ws_down_for_s"] is None


def test_no_server_with_the_gateway_silent_recovers_the_link_once_per_cooldown(tmp_path):
    sat = health_sat(tmp_path)
    sat._voice_input_started = False      # a network-only scenario
    sat._ws_disconnected_since = 0.0
    sat._health_last_sample = {"rss_kb": 50_000, "fds": 30, "gateway_ok": False,
                               "nm_state": {"code": 30, "state": "disconnected"}}
    recovered: list[float] = []
    sat._reassociate_wifi = lambda: recovered.append(1) or True
    sat._health_checks(5 * 60.0)
    sat._health_checks(6 * 60.0)
    assert len(recovered) == 1
    sat._health_checks(10 * 60.0)
    assert len(recovered) == 2
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert [a["action"] for a in doc["actions"]] == ["wifi_recover", "wifi_recover"]


def test_a_reboot_needs_the_helper_else_it_is_only_said(tmp_path, monkeypatch, caplog):
    sat = health_sat(tmp_path)
    sat._voice_input_started = False      # a network-only scenario
    sat._ws_disconnected_since = 0.0
    sat._health_last_sample = {"rss_kb": 50_000, "fds": 30, "gateway_ok": False}
    sat._reassociate_wifi = lambda: False
    ran: list[list[str]] = []
    monkeypatch.setattr(client, "REBOOT_HELPER", tmp_path / "domovoi-reboot")
    caplog.set_level(logging.ERROR)
    sat._health_checks(16 * 60.0)
    assert ran == []
    assert any("not installed" in r.getMessage() for r in caplog.records)
    # Now the helper exists: the check calls it through sudo -n.
    (tmp_path / "domovoi-reboot").write_text("#!/bin/sh\n")

    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(client.subprocess, "run", lambda argv, **kw: ran.append(argv) or Result())
    sat._health_last_wifi_recovery = 16 * 60.0
    sat._health_checks(32 * 60.0)
    assert ran and ran[0][:2] == ["sudo", "-n"] and ran[0][2].endswith("domovoi-reboot")
    doc = json.loads((tmp_path / "last-health.json").read_text())
    assert doc["last_exit_reason"].startswith("reboot:")


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

        # A ready from a core that lists both features: the receiver's
        # `ready` branch calls `_on_core_accepted_health` (asserted below
        # by source, since the whole branch also opens the microphone).
        sat.send_q = asyncio.Queue()
        sat._core_features = frozenset({"health", "log_push"})
        sat._session_ready_seen = True
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
