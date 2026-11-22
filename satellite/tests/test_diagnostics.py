"""Read-only diagnostics probes (satellite/diagnostics.py), each with fakes:
no real subprocess, socket, /proc or audio stack is touched."""

from __future__ import annotations

import socket
from collections import namedtuple
from pathlib import Path

from satellite import diagnostics as d

TOKEN = "f" * 64  # a pairing token's shape (secrets.token_hex(32))


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ctx(tmp_path: Path, **kw) -> d.Context:
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True, exist_ok=True)
    (proc / "meminfo").write_text("MemFree: 100000 kB\nMemAvailable: 250000 kB\nSwapFree: 0 kB\n")
    (proc / "loadavg").write_text("0.42 0.39 0.35 1/123 4567\n")
    (proc / "uptime").write_text("86400.0 1000.0\n")
    (proc / "net" / "wireless").write_text(
        "Inter-| sta-|   Quality        |\n face | status | link level noise\n"
        " wlan0: 0000   54.  -56.  -256        0      0      0      0      0        0\n")
    therm = tmp_path / "sys" / "class" / "thermal" / "thermal_zone0"
    therm.mkdir(parents=True, exist_ok=True)
    (therm / "temp").write_text("52100\n")
    tools = {
        ("iwgetid", "-r"): "HomeNetwork5G\n",
        ("ip", "route"): "default via 192.168.1.1 dev wlan0 proto dhcp\n",
    }
    tools.update(kw.pop("tools", {}))

    def run(argv, timeout=5.0):
        if argv[0] == "systemctl":
            return tools.get(("systemctl",))
        return tools.get(tuple(argv))

    Usage = namedtuple("Usage", "total used free")
    base = dict(
        server_url="ws://192.168.1.20:6370",
        log_tail=lambda n: "",
        synced_sha="44f0fff",
        run=run,
        connect=lambda addr, timeout=1.0: _Conn(),
        local_address=lambda host, port: "192.168.1.41",
        resolve=lambda host, port, proto=0: [(2, 1, 6, "", ("192.168.1.20", port))],
        disk_usage=lambda p: Usage(32 * 2**30, 8 * 2**30, 24 * 2**30),
        proc_root=str(proc),
        sys_root=str(tmp_path / "sys"),
        started_at=1000.0,
        now=lambda: 4600.0,
        known_secrets=(TOKEN,),
    )
    base.update(kw)
    return d.Context(**base)


def test_the_probe_list_is_the_core_s():
    from domovoi import satellite_diagnostics as core

    assert list(d.PROBES) == list(core.PROBES)


def test_recent_logs_returns_the_newest_lines_with_secrets_taken_out(tmp_path):
    lines = [f"line {i}" for i in range(100)]
    lines.append(f"2026-11-22 INFO hello pairing_token={TOKEN}")
    lines.append("INFO auth header: Bearer abcdefghijklmnop1234")
    lines.append('config {"api_key": "sk-live-123456", "password": "hunter2"}')
    ctx = _ctx(tmp_path, log_tail=lambda n: "\n".join(lines))
    r = d.run_probe("recent_logs", ctx)
    assert r["ok"] is True and r["probe"] == "recent_logs" and r["at"] == 4600.0
    text = r["text"]
    assert "line 99" in text and "line 10\n" not in text  # the last LOG_LINES only
    assert TOKEN not in text and "f" * 32 not in text
    assert "abcdefghijklmnop1234" not in text
    assert "sk-live-123456" not in text and "hunter2" not in text
    assert "[redacted]" in text


def test_a_known_secret_is_replaced_even_where_no_pattern_matches(tmp_path):
    ctx = _ctx(tmp_path, log_tail=lambda n: "the value was Xk29-plain", known_secrets=("Xk29-plain",))
    assert "Xk29-plain" not in d.run_probe("recent_logs", ctx)["text"]


def test_output_is_capped_and_control_characters_go(tmp_path):
    noisy = ("\x1b[31mred\x1b[0m \x07bell\x00 " + "word " * 5000)
    ctx = _ctx(tmp_path, log_tail=lambda n: noisy)
    d.LOG_LINES, saved = 1, d.LOG_LINES
    try:
        text = d.run_probe("recent_logs", ctx)["text"]
    finally:
        d.LOG_LINES = saved
    assert len(text) <= d.MAX_TEXT_CHARS
    assert text.endswith("[cut short]")
    assert "\x1b" not in text and "\x07" not in text and "\x00" not in text


def test_network_hashes_the_ssid_and_reports_signal_gateway_dns_latency(tmp_path):
    r = d.run_probe("network", _ctx(tmp_path))
    assert r["ok"]
    text = r["text"]
    assert "HomeNetwork5G" not in text
    assert d.ssid_hash("HomeNetwork5G") in text
    assert "signal -56 dBm" in text
    assert "address: 192.168.1.41" in text
    assert "gateway: 192.168.1.1 (answers)" in text
    assert "dns: 192.168.1.20 -> 192.168.1.20" in text
    assert "latency to the server:" in text and "port 6370" in text


def test_network_says_when_the_server_is_unreachable(tmp_path):
    def refuse(addr, timeout=1.0):
        raise socket.timeout()

    r = d.run_probe("network", _ctx(tmp_path, connect=refuse))
    assert "gateway: 192.168.1.1 (silent)" in r["text"]
    assert "no connection to port 6370" in r["text"]


def test_system_reads_disk_memory_load_temperature_uptime(tmp_path):
    text = d.run_probe("system", _ctx(tmp_path))["text"]
    assert "disk free: 24.0 GB of 32.0 GB (75% free)" in text
    assert "memory available: 244 MB" in text
    assert "load: 0.42 0.39 0.35" in text
    assert "cpu temperature: 52.1" in text
    assert "system uptime: 1d 00:00:00" in text
    assert "satellite running for: 01:00:00" in text


def test_audio_lists_devices_and_says_when_there_is_no_stack(tmp_path):
    devs = [{"name": "seeed-2mic-voicecard", "max_input_channels": 2, "max_output_channels": 2},
            {"name": "HDMI", "max_input_channels": 0, "max_output_channels": 8}]
    text = d.run_probe("audio", _ctx(tmp_path, audio_devices=lambda: devs))["text"]
    assert "0: seeed-2mic-voicecard (in, out; 2 in / 2 out)" in text
    assert "1: HDMI (out; 0 in / 8 out)" in text
    assert d.run_probe("audio", _ctx(tmp_path))["text"] == "no audio stack on this device"


def test_versions(tmp_path):
    text = d.run_probe("versions", _ctx(tmp_path, board="linux_pc"))["text"]
    assert "satellite code: 44f0fff" in text and "board: linux_pc" in text
    assert "python: " in text and "os: " in text


def test_service_reads_systemd_restarts_and_last_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(d.sys, "platform", "linux")
    show = ("NRestarts=3\nResult=exit-code\nExecMainStatus=1\n"
            "ExecMainExitTimestamp=Sat 2026-11-21 10:00:00 UTC\nActiveState=active\nSubState=running\n"
            "ActiveEnterTimestamp=Sat 2026-11-21 10:00:05 UTC\n")
    text = d.run_probe("service", _ctx(tmp_path, tools={("systemctl",): show}))["text"]
    assert "restarts by systemd: 3" in text
    assert "last exit: exit-code, status 1" in text
    assert "state: active (running)" in text
    text = d.run_probe("service", _ctx(tmp_path))["text"]
    assert "restarts: unknown" in text


def test_an_unknown_probe_is_refused_and_nothing_runs(tmp_path):
    ran = []
    ctx = _ctx(tmp_path, log_tail=lambda n: ran.append(n) or "x",
               run=lambda *a, **k: ran.append(a) or None)
    for name in ("rm -rf /", "shell", "recent_logs; reboot", "../etc/passwd", None, 7, "x" * 200):
        r = d.run_probe(name, ctx)
        assert r["ok"] is False and r["text"] == "unknown probe: refused"
        assert r["probe"] in ("?", "shell")
    assert ran == []


def test_a_failing_probe_answers_without_a_traceback(tmp_path):
    def boom():
        raise RuntimeError("PortAudio said: secret=abc123def")

    r = d.run_probe("audio", _ctx(tmp_path, audio_devices=boom))
    assert r == {"probe": "audio", "ok": False, "text": "the probe failed (RuntimeError)", "at": 4600.0}


def test_the_client_answers_diag_request_and_declares_the_ability():
    src = (Path(__file__).resolve().parents[1] / "client.py").read_text(encoding="utf-8")
    assert 'elif t == "diag_request":' in src
    assert '"diagnostics": True,' in src
    assert '"type": "diag_result"' in src
